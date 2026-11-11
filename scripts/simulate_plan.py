#!/usr/bin/env python3
"""Project state and run plan for `/simulate`: what exists, what to ask, which stages to run.

    simulate_plan.py state       <workdir>                                  # one JSON line (below)
    simulate_plan.py plan        <workdir> --choice rerun|finish|new|rescan|full [--traces yes|no]
    simulate_plan.py run-stage   <workdir> [--since ISO]                    # `present` or `run`
    simulate_plan.py run-summary <workdir>                                  # one line: the scored run
    simulate_plan.py require     traces|suite|run|fixes <workdir>           # exit 0, or exit 3 + one line
    simulate_plan.py fingerprint <workdir>                                  # last call of the scan; closes its attempt
    simulate_plan.py scan-open   <workdir> --mode reanalyze|focus|reuse|reverify [--component S|-] [--restart]
    simulate_plan.py scan-resume <workdir> [--restart]                      # exit 4 + the step to enter, or exit 0
    simulate_plan.py traces      <workdir> (--connect P --project N | --never-ask | --skip) [--anonymize [true|false]] [--env-file [default|PATH]] [--org-access grant:NAMES|decline|approve:NAMES]
    simulate_plan.py defaults    <workdir> [--since ISO]                    # one sentence per default the run took
    simulate_plan.py closing     <workdir> [--since ISO]                    # the run's whole closing message; never refuses
    simulate_plan.py routes                                                 # the feedback routes, JSON

Every subcommand that takes a workdir also takes `--flowdir D` (default `$FLOWDIR`).

WHAT IT READS. Only files the plugin already writes, plus `<workdir>/.simulate_state.json`, which
holds the two facts no other file holds (gitignored by the managed block's `**/plurai_sim/**/.*`):

    {"schema": "plurai-simulate-state/1",
     "scan":   {"at": ISO, "root": "<root, relative to the git top level>", "files": {"<path>": "<blob sha>"}, "truncated": bool},
     "traces": {"provider": "langsmith"|"langfuse"|null, "project": str|null, "ask": bool, "anonymize": bool|null,
                "env_file": ""|"<absolute path>"|null},
     "scan_open": {"at": ISO, "at_epoch": float, "mode": str, "component": str|null, "files": {...}, "truncated": bool}}

`scan_open` is the scan attempt in progress: `scan-open` writes it once the scan's path is decided and `fingerprint`
removes it, so it stands only while a scan has not landed. `scan-resume` reads it against `SCAN_STEPS`, the scan's
steps in order, each with a done-test over a file that step already writes, and names the first step not done. An
attempt that wrote no description yet (`DESCRIPTIONS`) is not resumed: `scan-resume` sets it aside, and so do `plan`
and a `require` that passes, since the step they start runs on the scan that landed. An attempt that is kept stays
as it is, start time included, so a `scan-open` after a resume keeps it. For planning only (`state`, `plan`), a
resumable attempt makes the project read as not scanned; `require` keeps its predicates and only warns.

`traces.anonymize` is the start call's personal-data answer (`traces --anonymize true|false`), whatever the
trace answers were; `traces --anonymize` alone writes it into `plurai.config.yaml` once the config exists.
`traces.env_file` is the start call's settings-location answer: `""` the defaults, a path the file the user named
(`traces --env-file default|PATH`), null not asked. `traces --env-file` alone writes a named file into the bench
manifest's `harness.env_file` (the file the harness loads and every presence check reads) once the manifest exists.
`traces.org_access` is the start call's answer on a hosted Agentforce agent: the items approved, by name, `[]` for none,
null not asked. `traces.org_approved` names what the user approved later, one item at a time. `apply_org_access` turns
them into `.host_effects_consent` once the map exists; a named action the map types a writer stays off.

The agent fingerprint is the git blob sha of every file under the directory that holds
`plurai_sim/` (tracked files from the index, modified and untracked ones hashed the way git hashes
a blob), `plurai_sim/` excluded. No content and no credentials are stored. Before the agent has a
workdir, `traces` writes its record to `$FLOWDIR/.simulate_state_pending.json`, and `fingerprint`
moves it into the workdir.

EXIT CODES: 0 ok · 2 no agent root resolved (`PLURAI_AGENT_RESOLVED=0`) · 3 a refusal, said in one
line (a missing requirement, a choice the project cannot take, a config key already declared, no
scored run) · 4 `scan-resume` found a scan attempt to resume · 64 usage.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 'plurai-simulate-state/1'
STATE_FILE = '.simulate_state.json'
PENDING_FILE = '.simulate_state_pending.json'
PLAN_FILE = '.plurai_simulate.json'
FLOW_MARKER = '.plurai_flow_active'
FLOW_ARM = Path(__file__).resolve().parent.parent / 'hooks' / 'flow_arm.py'

MAX_FILES = 20000
MAX_HASH_BYTES = 5 * 1024 * 1024
SKIP_DIRS = frozenset({'node_modules', '__pycache__', 'site-packages'})
CHANGED_SHOWN = 5

STAGES = ('scan', 'traces', 'suite', 'run', 'fixes')
#: The start choices. A `traces` answer of yes puts the traces stage right after the scan, on every choice:
#: Connect or Yes means the traces are analyzed in this run. `finish` re-enters a suite build that stopped
#: (the suite resumes from its own phase record).
PLANS = {
    'full': {'stages': ('scan', 'suite', 'run', 'fixes'), 'needs': ()},
    'rescan': {'stages': ('scan', 'suite', 'run', 'fixes'), 'needs': ('scanned',)},
    'finish': {'stages': ('suite', 'run', 'fixes'), 'needs': ('scanned', 'unfinished')},
    'new': {'stages': ('suite', 'run', 'fixes'), 'needs': ('scanned',)},
    'rerun': {'stages': ('run', 'fixes'), 'needs': ('scanned', 'suite')},
}
#: The start question's options, in their fixed order before `_options` reorders them.
START_OPTIONS = {
    'finish': ('Finish the suite', 'Picks the suite build up where it stopped, then runs it and plans fixes.'),
    'rerun': ('Rerun the current suite', 'Same scenarios, fresh run, updated fix plan.'),
    'new': ('New suite, then run', 'Regenerates scenarios from the current topology, then runs and plans fixes.'),
    'rescan': ('Rescan the agent', 'Rebuilds the topology (about 5 minutes), then runs end to end.'),
}
START_QUESTION = 'What should this run do?'
CHANGED_PREFIX = 'The agent changed since the last scan ({count} file{s}: {first}). '
RESCAN_CHANGED = ' (agent changed)'
#: The traces prompt, by whether a source is connected.
TRACES_PROMPTS = {
    'connect': ('Connect a trace source so the suite reflects real traffic?',
                (('connect', 'Connect', 'LangSmith or Langfuse; analyzed in this run.'),
                 ('skip', 'Skip for now', 'Asked again next run.'),
                 ('never', "Don't ask again", 'No trace source; never asked again.'))),
    'confirm': ('Ground this run in your {provider} traces (project {project})?',
                (('yes', 'Yes', 'Analyze the traces in this run (default).'),
                 ('no', 'No', 'Run without them this time.'))),
}
#: The personal-data posture, asked in the start call while neither the config declares `anonymize` nor an
#: earlier start answer is recorded. Each answer is the `--anonymize` value `traces` records. A `/simulate` run
#: then answers the `anonymization-posture` gate from it (`_run_driver.START_ANSWERS`). It names the knowledge base because
#: that answer is also what lets the run take the knowledge-base gates' recommendation unasked (`_run_driver.UNWINDOWED_GATES`).
ANONYMIZE_PROMPT = ('Remove personal data from your agent\'s documents (its knowledge base included) and traces on this machine before anything is uploaded?',
                    (('false', 'No, upload as-is', 'They are uploaded unchanged, under your agreement with Plurai.'),
                     ('true', 'Yes, remove it first', 'Personal data is removed locally first; this needs an extra local package.')))
#: Where the agent's settings are read from, said once, in the first start call of a project never scanned. `{file}`
#: and `{logins}` are this machine's: the env file the harness would load, the providers' default credential files.
ENV_PROMPT = ("Your agent's settings and keys are read from the environment Claude Code passes to the commands it runs{file}{logins}. "
              'Only what is missing there is asked about. Read settings from a different file instead?',
              (('default', 'Use these (Recommended)', 'Settings already set there are used as they are.'),
               ('other', 'A different file', 'Name its path; the simulation reads it instead of the default env file.')))
#: The org-access question, asked in the start call on a hosted Agentforce agent while nothing has answered it. Its
#: grant answer spells out the items, so what is recorded is what was shown. `{actions}`: the actions that need a grant.
ORG_ACCESS_PROMPT = ('Your agent runs in your Salesforce org, so simulating it happens there. During simulation runs, may the simulation: '
                     'deploy a copy of your agent into your org and activate it (your running agent is never touched; the copy can be '
                     'stopped and deleted){actions}; and upload each document a scenario hands your agent as a file in your org, deleted '
                     "when that conversation ends (Salesforce keeps a deleted file in your org's Recycle Bin for 15 days)? {others} "
                     'If the scan finds that an action it would run for real writes to your org, it stays off and the run says so.',
                     (('grant:{items}', 'Grant all (Recommended)', 'The copy is deployed and activated{ran}, and documents are uploaded and then deleted.'),
                      ('decline', 'Grant none', 'Nothing is deployed, run or uploaded in your org. With no copy to drive, no scenario can run against your agent, '
                                                'so this run goes no further than mapping it, and the next run asks again.')))
ORG_ACTIONS_CLAUSE = '; run these org actions for real, because they cannot be simulated and could change data or use credits in your org: {names}'
ORG_READERS_CLAUSE = 'Every other action stays simulated, except these, which only read: {names}.'
ORG_NO_READERS_CLAUSE = 'Every other action stays simulated.'
#: The start answer's items that are no org action (`host_effects.NON_TOOL_GRANTS`); the first brings the copy up.
ORG_BRING_UP = 'route_smoke'
ORG_ITEMS = (ORG_BRING_UP, 'file_uploads')
#: The stages that need the copy in the org, which a start answer without it does not allow.
ORG_COPY_STAGES = ('suite', 'run', 'fixes')
ORG_REFUSED_PLAN = 'You chose not to let the simulation use your org, so nothing can run against your agent. Run /simulate again to change that answer.'
ORG_REFUSED_CLOSING = 'Nothing was deployed or run in your org, as you chose, so no scenario ran against your agent. The next /simulate asks again.'
CONSENT_FILE = '.host_effects_consent'
PROVIDERS = {'langsmith': 'LangSmith', 'langfuse': 'Langfuse'}
#: The env vars each provider's export reads. Only their PRESENCE is ever reported.
PROVIDER_KEYS = {'langsmith': ('LANGSMITH_API_KEY',), 'langfuse': ('LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY')}

#: What a direct step call needs, as `facts` predicate names.
#: A run needs `suite_closed`: scenarios of a suite build that stopped before its hand-off are that build's own draft.
REQUIRES = {'traces': ('scanned',), 'suite': ('scanned',), 'run': ('scanned', 'suite_closed', 'scenarios'), 'fixes': ('diagnosable',)}
#: Each predicate's missing-thing sentence, in product words.
MISSING_SENTENCES = {
    'scanned': 'This agent has not been scanned yet',
    'scenarios': 'No test suite yet',
    'suite': 'No test suite yet',
    'unfinished': 'No unfinished suite',
    'suite_closed': 'The test suite build has not finished yet',
    'diagnosable': 'No simulation run yet',
}
MISSING_TAIL = '. Run /simulate to build it end to end.'
#: A predicate whose remedy is not a build from scratch says its own.
MISSING_TAILS = {'suite_closed': f'. Run /simulate and choose {START_OPTIONS["finish"][0]} to pick it up where it stopped.'}
#: What a direct step call says, without refusing, over a scan attempt that has not landed (`facts()['scan_open']`).
SCAN_OPEN_WARNING = 'WARNING: The last scan of this agent has not finished. Run /simulate or /simulation-scan-agent to pick it up where it stopped.'
#: The window name each step arms (`flow_arm.COMMANDS` values), for the driver check.
STEP_WINDOWS = {'traces': 'traces', 'suite': 'generate-suite', 'run': 'run', 'fixes': 'fixes'}

#: One product sentence per gate default a `/simulate` run took, or None when it changes nothing the
#: user would reverse — or when `COMPOSED_SENTENCES` composes it from the value taken and the project's files.
DEFAULT_SENTENCES = {
    'apply-brief': None,
    'component-pick': None,
    'corpus-reuse': 'The existing knowledge corpus was reused; say so to rebuild it.',
    'degraded-mode-consent': 'The suite quality check could not run with an independent reviewer, so it stopped; say so to retry.',
    'delete-confirm': None,
    'document-generation': None,
    'extraction-scope': 'The knowledge corpus was built from a focused part of a large source; say so to widen it.',
    'fix-selection': 'No fix was applied to your agent; say which fixes from the plan to apply.',
    'grounding-consent': None,
    'grounding-gate': None,
    'knowledge-audience': 'Knowledge articles published outside the agent\'s audience were left out; say so to include them.',
    'observability-opt-in': 'The final run was not recorded in your observability platform; say so to record the next one.',
    'rescan-diff-approval': 'The rescan kept the earlier wording wherever it would have dropped or reversed it; say so to take the new wording.',
    'saved-setup-gate': None,
    'simulate-start': 'This run reused what was already built and connected no trace source; say so to rebuild or to connect traces.',
    'topology-approval': None,
    'trace-scenario-selection': None,
    'which-kb': None,
    'which-scenario': None,
}
TOPOLOGY_SENTENCES = {
    'approved': 'The agent map was approved as drawn; say what it gets wrong.',
    'unprobed': 'Tool specs were written from the code without read-only checks; say so to verify them.',
    'mocked': 'Tools that act on your systems ran mocked ({names}); say which to run for real.',
    # Said off the scored run's `host_effects.denied` (`_topology_sentences`): a tool only ever `disallowed…`, or one held
    # to the run's sandbox.
    'switched-off': 'Tools that act on your systems were switched off ({names}); say which to run for real.',
    'sandboxed': "Tools that act on your systems ran for real in the run's sandbox ({names}); say which to run outside it.",
}
#: The grounding gate's sentence, by the default it took (`_run_driver` FACT_DISPATCH decides which).
GROUNDING_SENTENCES = {
    'build-from-recorded-sources': 'Built a knowledge base from your {documents}; say so to exclude any.',
    'proceed-without-corpus': 'No knowledge corpus was built; say so if your agent answers from documents or a database.',
}
#: `build-from-recorded-sources`' sentence when no corpus landed (`_no_corpus_built`).
CORPUS_NOT_BUILT = 'No knowledge base was built from your {documents}; say so to retry.'
#: `CORPUS_NOT_BUILT` when the extractor install failed (`resolve_plurai.py --install --record`) and the grounding job
#: delivered no corpus build (`_extractor_error`).
CORPUS_CANNOT_BUILD = ('No knowledge base was built from your {documents}: the document reader it needs could not be installed '
                       '({error}); say so to retry.')
DEFAULT_KIND = 'run_driver_default_applied'
#: The `declared_by` of a default the user did not choose: `/simulate`'s own (`simulate`), or a session's with no ask tool
#: and no run driver declared (`default`). A run driver declared headless in config or env chose its defaults itself.
DECIDED_FOR_YOU = frozenset({'simulate', 'default'})

#: The rewind of an edit route: not a fixed stage. The edit runs first, then `artifact_deps.py check`
#: names the rewind (`run`, `fixes`, or none) from what the edit made stale.
DEPS = 'deps'
#: Feedback → (what the user named, the invocation, the stage to rewind, or DEPS). One table: the skill
#: prints it, and `routes` hands it to the plan file.
FEEDBACK_ROUTES = (
    ('the map, the topology, or a tool mocked vs live', '/simulation-scan-agent <agent path> --force --restart', 'scan'),
    ('how the mocked world behaves (a tool\'s returns, seeded data)', '/edit-env', DEPS),
    ('scenarios or personas', '/simulation-generate-suite "<feedback>"', 'suite'),
    ('one eval\'s verdict or rubric', '/edit-evals', DEPS),
    ('a scenario\'s ground truth', '/edit-scenarios', DEPS),
    ('the run', '/simulation-run', 'run'),
    ('the fix plan', '/simulation-fixes', 'fixes'),
    ('stop or pause', 'flow-window.sh simulate end', None),
    ('anything else (a side question, "looks good")', 'answer it, then flow-window.sh simulate resume', None),
)

ANONYMIZE_KEY = 'anonymize'


class Refusal(Exception):
    """A one-line refusal: printed to stdout, exit 3."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # usage errors are 64, never argparse's 2 (2 = no workdir)
        self.print_usage(sys.stderr)
        print(f'{self.prog}: error: {message}', file=sys.stderr)
        raise SystemExit(64)


# ── small readers ─────────────────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse_time(value: object) -> float | None:
    """Epoch seconds from an ISO-8601 string (a trailing Z, or naive = UTC) or a number; None when unreadable."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _read_line(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8').strip()
    except (OSError, UnicodeDecodeError):
        return ''


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def _flowdir(explicit: str | None) -> Path | None:
    value = explicit or os.environ.get('FLOWDIR')
    return Path(value) if value else None


def _agent_root_refused() -> bool:
    """`resolve-workdir.sh` refused to name an agent root; unset reads as resolved (its own `:-1`)."""
    return (os.environ.get('PLURAI_AGENT_RESOLVED') or '1') != '1'


def _art(workdir: Path) -> Path:
    return workdir / 'artifacts'


def _agent_root(workdir: Path) -> Path:
    """The directory that holds `plurai_sim/` (a named env lives below it), as `resolve-workdir.sh` defines it."""
    for candidate in (workdir, *workdir.parents):
        if candidate.name == 'plurai_sim':
            return candidate.parent
    return workdir.parent


# ── the agent fingerprint ─────────────────────────────────────────────────────────────────────


def _blob_sha(path: Path) -> str | None:
    """The git blob sha of the file's bytes (what `git hash-object` prints), or `size:<n>` past the cap."""
    try:
        size = path.stat().st_size
        if size > MAX_HASH_BYTES:
            return f'size:{size}'
        data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()  # noqa: S324 - git's own object id, not a security hash


def _excluded(rel: str) -> bool:
    return 'plurai_sim' in rel.split('/')


def _skipped(rel: str, root: Path) -> bool:
    """An untracked file under a dot-dir, a dependency dir or a virtualenv (a dir holding `pyvenv.cfg`)."""
    parts = rel.split('/')[:-1]
    return any(p.startswith('.') or p in SKIP_DIRS or (root.joinpath(*parts[: i + 1]) / 'pyvenv.cfg').is_file() for i, p in enumerate(parts))


def _git(root: Path, *args: str) -> list[str] | None:
    try:
        done = subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return [p for p in done.stdout.decode('utf-8', errors='surrogateescape').split('\0') if p]


def _git_files(root: Path) -> dict[str, str] | None:
    """Root-relative path → blob sha from git, or None when the root is not in a work tree."""
    indexed = _git(root, 'ls-files', '-s', '-z')
    if indexed is None:
        return None
    files: dict[str, str] = {}
    for entry in indexed:
        meta, _, rel = entry.partition('\t')
        fields = meta.split()
        if len(fields) >= 2 and rel and not _excluded(rel):
            files[rel] = fields[1]
    for rel in _git(root, 'ls-files', '-d', '-z') or []:
        files.pop(rel, None)
    for rel in _git(root, 'ls-files', '-m', '-z') or []:
        if rel in files and (sha := _blob_sha(root / rel)) is not None:
            files[rel] = sha
    for rel in _git(root, 'ls-files', '-o', '--exclude-standard', '-z') or []:
        if not _excluded(rel) and not _skipped(rel, root) and (sha := _blob_sha(root / rel)) is not None:
            files[rel] = sha
    return files


def _walk_files(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith('.') and d not in SKIP_DIRS and d != 'plurai_sim' and not (here / d / 'pyvenv.cfg').is_file()
        )
        for name in sorted(filenames):
            rel = (here / name).relative_to(root).as_posix()
            if (sha := _blob_sha(here / name)) is not None:
                files[rel] = sha
        if len(files) > MAX_FILES:
            break
    return files


def fingerprint_now(root: Path) -> dict:
    files = _git_files(root)
    if files is None:
        files = _walk_files(root)
    keys = sorted(files)
    truncated = len(keys) > MAX_FILES
    return {'files': {k: files[k] for k in keys[:MAX_FILES]}, 'truncated': truncated}


def _root_label(root: Path) -> str:
    top = _git(root, 'rev-parse', '--show-toplevel')
    if top:
        try:
            rel = root.resolve().relative_to(Path(top[0].strip()).resolve()).as_posix()
        except ValueError:
            return '.'
        return rel or '.'
    return '.'


def changed_files(old: dict[str, str], new: dict[str, str]) -> list[str]:
    return sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))


# ── state files ───────────────────────────────────────────────────────────────────────────────


def _empty_state() -> dict:
    return {'schema': SCHEMA, 'scan': None, 'traces': {'provider': None, 'project': None, 'ask': True, 'anonymize': None, 'env_file': None, 'org_access': None, 'org_approved': None}}


def _load_state(workdir: Path, flowdir: Path | None) -> dict:
    """The workdir's record, with a pending traces record (written before the workdir existed) laid over it."""
    state = _empty_state()
    stored = _read_json(workdir / STATE_FILE)
    if isinstance(stored, dict):
        if isinstance(stored.get('scan'), dict):
            state['scan'] = stored['scan']
        if isinstance(stored.get('traces'), dict):
            state['traces'].update({k: stored['traces'].get(k, v) for k, v in state['traces'].items()})
    pending = _read_json(flowdir / PENDING_FILE) if flowdir is not None else None
    if isinstance(pending, dict) and isinstance(pending.get('traces'), dict):
        state['traces'].update({k: pending['traces'][k] for k in state['traces'] if k in pending['traces']})
    return state


def traces_record(workdir: Path, flowdir: Path | None = None) -> dict:
    """The recorded trace connection (`provider`, `project`, `ask`, `anonymize`, `env_file`); a key not recorded is None."""
    return _load_state(workdir, flowdir)['traces']


def _save_traces(workdir: Path, flowdir: Path | None, traces: dict) -> str:
    """Write the traces record into the workdir's state, else (no workdir yet) into the pending file."""
    if workdir.is_dir():
        stored = _read_json(workdir / STATE_FILE)
        stored = stored if isinstance(stored, dict) else _empty_state()
        stored['schema'] = SCHEMA
        stored['traces'] = traces
        _write_json(workdir / STATE_FILE, stored)
        if flowdir is not None:  # the record above already holds it, and left there it would be laid back over this answer
            (flowdir / PENDING_FILE).unlink(missing_ok=True)
        return 'workdir'
    if flowdir is None:
        raise Refusal('The agent has no simulation folder yet and no flow folder is set; nothing was recorded.')
    _write_json(flowdir / PENDING_FILE, {'schema': SCHEMA, 'traces': traces})
    return 'pending'


def _config_has_key(config: Path, key: str) -> bool:
    try:
        lines = config.read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    return any(line.split(':', 1)[0] == key for line in lines if ':' in line and not line.startswith((' ', '\t', '#')))


def _consent_state(path: Path) -> str | None:
    """`granted` / `declined` / `unasked` from a three-state consent record, None when absent."""
    head = _read_line(path).partition(':')[0].strip()
    return head if head in ('granted', 'declined', 'unasked') else None


def _granted_names(path: Path) -> set[str]:
    head, sep, names = _read_line(path).partition(':')
    return {n.strip() for n in names.split(',') if n.strip()} if sep and head.strip() == 'granted' else set()


# ── the scan attempt ──────────────────────────────────────────────────────────────────────────

SCAN_MODES = ('reanalyze', 'focus', 'reuse', 'reverify')
#: The modes that analyze, put the map to the approval gate and generate; `reuse` and `reverify` do none of the three.
GENERATING_MODES = frozenset({'reanalyze', 'focus'})
#: Seconds of mtime slack: a coarse filesystem clock can stamp a write made just after the open below the open time.
FRESH_SLACK = 2.0
#: The files only generation writes, after the approval. The config is not one: `traces --anonymize` appends to it
#: whatever the scan is doing.
DESCRIPTIONS = ('simulate.py', 'simulation_prd.md')
#: Everything generation writes, in the order `scan-resume` lists the ones this attempt wrote (`wrote:`).
GENERATED = ('plurai.config.yaml', 'simulate.py', 'test_mock_contract.py', 'simulation_prd.md', 'scenario_variables.yaml')
#: The `bench_manifest.py verify --mode` each recorded mode runs in Step 3c.
VERIFY_MODES = {'reanalyze': 'reverify', 'focus': 'reverify', 'reuse': 'reuse', 'reverify': 'reverify'}
LINT_PRD = Path(__file__).resolve().parent.parent / 'skills' / 'simulation-setup' / 'scripts' / 'lint_prd.py'
#: A tool the config mocks: Step 3 then writes `test_mock_contract.py`.
MOCK_MODE = re.compile(r'(?m)^[ \t]+mode:[ \t]*["\']?mock["\']?[ \t]*(?:#.*)?$')
RESUME_EXIT = 4


def _attempt(workdir: Path) -> dict | None:
    """The open scan attempt, or None (none recorded, or a record with no readable open time)."""
    stored = _read_json(workdir / STATE_FILE)
    attempt = stored.get('scan_open') if isinstance(stored, dict) else None
    if not isinstance(attempt, dict) or _parse_time(attempt.get('at_epoch')) is None:
        return None
    return attempt


def _opened(attempt: dict) -> float:
    """The attempt's open time (`_attempt` returns only attempts where it parses)."""
    return _parse_time(attempt.get('at_epoch')) or 0.0


def _fresh(path: Path, attempt: dict) -> bool:
    """Written by this attempt: the file's mtime is at or after the attempt opened, less `FRESH_SLACK`."""
    try:
        return path.is_file() and path.stat().st_mtime >= _opened(attempt) - FRESH_SLACK
    except OSError:
        return False


def _wrote_description(workdir: Path, attempt: dict) -> bool:
    return any(_fresh(workdir / name, attempt) for name in DESCRIPTIONS)


def _resumable(workdir: Path, attempt: dict) -> bool:
    """The attempt wrote a description: a resume keeps something it would redo. One cut before that starts over."""
    return _wrote_description(workdir, attempt)


def _set_aside_if_empty(workdir: Path) -> None:
    """A step that runs on the landed scan sets aside an open attempt with nothing to resume: it was abandoned."""
    attempt = _attempt(workdir)
    if attempt is not None and not _resumable(workdir, attempt):
        _drop_attempt(workdir)


def _analyzed(workdir: Path, attempt: dict) -> tuple[bool, str | None]:
    """Step 2a writes the map's spec (with `agent_inventory.json`) once the analysis is done."""
    return _fresh(_art(workdir) / 'agent_topology.json', attempt), None


def _approved(workdir: Path, attempt: dict) -> tuple[bool, str | None]:
    """Generation runs only once the map is approved, so a file it wrote in this attempt is the approval's proof.
    An attempt with none is never resumed (`scan_resume`), so a resumed one never asks the approval again."""
    return _wrote_description(workdir, attempt), None


def _prd_clean(workdir: Path) -> tuple[bool, str | None]:
    """`lint_prd.py` exits 0 on the PRD, run as Step 3 runs it, under this interpreter."""
    if not (workdir / 'simulation_prd.md').is_file():
        return False, None
    command = [sys.executable, str(LINT_PRD), str(workdir / 'simulation_prd.md'), '--config', str(workdir / 'plurai.config.yaml')]
    try:
        done = subprocess.run(command, capture_output=True, check=False, timeout=300, cwd=str(workdir))
    except (OSError, subprocess.SubprocessError):
        return False, 'WARNING: the PRD check could not run here; the generate step re-checks the PRD.'
    if done.returncode == 3:
        return False, f'WARNING: the PRD check could not read its inputs under {Path(sys.executable).name}; the generate step re-checks the PRD.'
    return done.returncode == 0, None


def _generated(workdir: Path, attempt: dict) -> tuple[bool, str | None]:
    """Generation's files are this attempt's (the contract tests too, when a tool is mocked), and the PRD is lint-clean
    (a fresh draft is not a finished PRD). `scenario_variables.yaml` is the last file generation writes."""
    if not all(_fresh(workdir / name, attempt) for name in ('plurai.config.yaml', 'simulate.py', 'scenario_variables.yaml')):
        return False, None
    try:
        mocked = bool(MOCK_MODE.search((workdir / 'plurai.config.yaml').read_text(encoding='utf-8', errors='replace')))
    except OSError:
        return False, None
    if mocked and not _fresh(workdir / 'test_mock_contract.py', attempt):
        return False, None
    return _prd_clean(workdir)


#: The scan's steps, in order. `enter` is where the skill picks the step up (by mode family when it differs), `done`
#: its test over a file the step already writes (None: always run, its checks are idempotent), `generating` whether
#: the step runs only in `GENERATING_MODES`.
SCAN_STEPS = (
    {'step': 'analyze', 'generating': True, 'done': _analyzed,
     'enter': 'Step 1b, in the recorded mode'},
    {'step': 'approve', 'generating': True, 'done': _approved,
     'enter': 'Step 2c, branch B: present the map already on disk and ask its approval again; analyze nothing'},
    {'step': 'generate', 'generating': True, 'done': _generated,
     'enter': 'Step 3: keep every file `wrote` lists, re-check each with its own check, and regenerate only what fails'},
    {'step': 'finish', 'generating': False, 'done': None,
     'enter': {'generating': 'Step 3, at "Then record the native tools", then on through Step 5',
               'saved': 'Step 3b, then on through Step 5'}},
)


def _age(seconds: float) -> str:
    minutes = max(0, int(seconds // 60))
    if minutes < 60:
        return f'{minutes}m'
    if minutes < 48 * 60:
        return f'{minutes // 60}h {minutes % 60}m'
    days = minutes // (24 * 60)
    return f"{days} day{'' if days == 1 else 's'}"


def resume_step(workdir: Path, attempt: dict) -> tuple[dict, list[str], list[str]]:
    """The first step of `SCAN_STEPS` not done, the steps before it, and the warnings the tests raised."""
    generating = attempt.get('mode') in GENERATING_MODES
    done_steps: list[str] = []
    warnings: list[str] = []
    for spec in SCAN_STEPS:
        if spec['generating'] and not generating:
            continue
        if spec['done'] is None:
            return spec, done_steps, warnings
        done, note = spec['done'](workdir, attempt)
        if note:
            warnings.append(note)
        if not done:
            return spec, done_steps, warnings
        done_steps.append(spec['step'])
    return SCAN_STEPS[-1], done_steps, warnings


def _drop_attempt(workdir: Path) -> None:
    stored = _read_json(workdir / STATE_FILE)
    if isinstance(stored, dict) and 'scan_open' in stored:
        del stored['scan_open']
        _write_json(workdir / STATE_FILE, stored)


def resumable_attempt(workdir: Path) -> dict | None:
    """The open attempt `scan-resume` would resume, or None. The scan's arm reads it (`open_epoch.py`): a scan that
    resumes keeps its operator record."""
    attempt = _attempt(workdir)
    return attempt if attempt is not None and _resumable(workdir, attempt) else None


def _retire_scan_record(workdir: Path) -> None:
    """A set-aside attempt's operator record goes with it, as a new scan's arm retires the last one's. Never raises."""
    try:
        sys.path.append(str(Path(__file__).resolve().parent.parent / 'hooks'))
        import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path only here

        _run_driver.retire_durable(_art(workdir), 'scan-agent', None)
        _run_driver.retire_scope(_art(workdir), 'scan-agent')
    except Exception:  # noqa: BLE001 - an earlier attempt's rows predate the next attempt, which reads none of them
        pass


def scan_open(workdir: Path, mode: str, component: str | None, restart: bool) -> str:
    """Record the attempt once the scan's path is decided. An attempt already open is kept as it is, unless `restart`."""
    if not workdir.is_dir():
        raise Refusal('The agent has no simulation folder yet; nothing was recorded.')
    current = _attempt(workdir)
    if current is not None and not restart:
        line = f"simulate_plan: a scan attempt is already open (mode {current.get('mode')}, opened {current.get('at')}); kept."
        if current.get('mode') != mode:
            line += f" WARNING: the open attempt keeps mode {current.get('mode')}, not {mode}; only a request to start over restarts it."
        return line
    now = fingerprint_now(_agent_root(workdir))
    stored = _read_json(workdir / STATE_FILE)
    stored = stored if isinstance(stored, dict) else _empty_state()
    stored['schema'] = SCHEMA
    stored['scan_open'] = {'at': _now(), 'at_epoch': datetime.now(timezone.utc).timestamp(), 'mode': mode, 'component': component,
                           'files': now['files'], 'truncated': now['truncated']}
    _write_json(workdir / STATE_FILE, stored)
    return f"simulate_plan: scan attempt opened (mode {mode}){'; the earlier attempt was set aside' if current is not None else ''}."


def scan_resume(workdir: Path, restart: bool) -> tuple[int, list[str]]:
    """`resume_step: none` (exit 0), or the step an open attempt resumes at (exit `RESUME_EXIT`)."""
    attempt = _attempt(workdir)
    if attempt is None:
        return 0, ['resume_step: none']
    if restart:
        _drop_attempt(workdir)
        _retire_scan_record(workdir)  # the arm kept it for a resume this invocation does not take
        return 0, ['resume_step: none', 'reason: the open scan attempt was set aside, as asked']
    if not _resumable(workdir, attempt):
        _drop_attempt(workdir)
        return 0, ['resume_step: none', 'reason: the open scan attempt wrote no description of the agent yet; it was set aside']
    spec, done_steps, warnings = resume_step(workdir, attempt)
    enter = spec['enter']
    if isinstance(enter, dict):
        enter = enter['generating' if attempt.get('mode') in GENERATING_MODES else 'saved']
    mode = attempt.get('mode')
    lines = [f"resume_step: {spec['step']}", f'mode: {mode}', f"verify_mode: {VERIFY_MODES.get(mode, 'reverify')}"]
    if attempt.get('component'):
        lines.append(f"component: {attempt['component']}")
    wrote = [name for name in GENERATED if _fresh(workdir / name, attempt)]
    lines += [f"opened: {attempt.get('at')} ({_age(datetime.now(timezone.utc).timestamp() - _opened(attempt))} ago)",
              f"done: {', '.join(done_steps) or 'nothing yet'}", f"wrote: {', '.join(wrote) or 'nothing yet'}", f'enter: {enter}']
    if isinstance(attempt.get('files'), dict):
        changed = changed_files(attempt['files'], fingerprint_now(_agent_root(workdir))['files'])
        if changed:
            lines.append(f"WARNING: {len(changed)} agent file{'' if len(changed) == 1 else 's'} changed since this scan began "
                         f"({', '.join(changed[:CHANGED_SHOWN])})")
    return RESUME_EXIT, lines + warnings


# ── project facts ─────────────────────────────────────────────────────────────────────────────


def _run_dir_from_pointer(workdir: Path) -> Path | None:
    pointer = _read_line(workdir / '.last_run')
    if not pointer:
        return None
    path = Path(pointer)
    for candidate in (path, workdir / path, _art(workdir) / 'runs' / path.name):
        if (candidate / 'run.json').is_file():
            return candidate
    return None


def _scored_run_dir(workdir: Path) -> Path | None:
    """`runs/index.json`'s scored run, else the `.last_run` pointer."""
    index = _read_json(_art(workdir) / 'runs' / 'index.json')
    name = index.get('scored_run') if isinstance(index, dict) else None
    if isinstance(name, str) and name and (_art(workdir) / 'runs' / name / 'run.json').is_file():
        return _art(workdir) / 'runs' / name
    return _run_dir_from_pointer(workdir)


def _has_confirmed_finding(path: Path) -> bool:
    """`agent_findings.md` holds a CONFIRMED section: its `status:` says confirmed, or it has none (older files).
    A file of `potential` sections only is the scan-only path, with nothing to diagnose."""
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return False
    for section in re.split(r'(?m)^## ', text)[1:]:
        status = re.search(r'(?im)^\W*status\W*:\W*(\w+)', section)
        if status is None or status.group(1).lower() == 'confirmed':
            return True
    return False


def _bench_manifest():  # noqa: ANN202 - a module
    """`scripts/bench_manifest.py`, the phase record's one reader and writer (stdlib only)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bench_manifest  # noqa: PLC0415

    return bench_manifest


def _phases(workdir: Path) -> list:
    record = _read_json(_art(workdir) / 'suite_state.json')
    phases = record.get('phases') if isinstance(record, dict) else None
    return phases if isinstance(phases, list) else []


def _handed_off(workdir: Path) -> bool:
    """The CURRENT build handed off, by the phase record (`bench_manifest.handed_off`, the one finished rule). The
    record decides, not the anchor file: an anchor from an earlier build stays on disk while a later build runs, and
    a session that died between `report landed` and the anchor write handed off all the same. A record with no
    hand-off phase (written before the suite recorded one) leaves the anchor as the only evidence — and only while
    the record holds no reset: a reset means a build began after whichever build the anchor belongs to, the rule
    `handed_off` applies to any record after a `report landed`. An anchor that records `handed_off: false` was written
    over a build that had not handed off (`lane_pin.py anchor`), so it is no evidence; a legacy anchor has no such key."""
    bm = _bench_manifest()
    phases = [p for p in _phases(workdir) if isinstance(p, dict)]
    if any((p.get('phase'), p.get('status')) == (bm.HANDOFF_PHASE, 'landed') for p in phases):
        return bm.handed_off(phases)
    if any(p.get('phase') == bm.RESET_PHASE for p in phases):
        return False
    path = _art(workdir) / 'suite_anchor.json'
    record = _read_json(path)
    return path.is_file() and not (isinstance(record, dict) and record.get('handed_off') is False)


def facts(workdir: Path) -> dict:
    """The predicates `REQUIRES` and `PLANS` read, from files only.

    A scan counts once it wrote `verification.json` (its last proof), not on the harness alone. A suite counts once
    its current build handed off (`_handed_off`): set ids alone are also what a build that stopped mid-qualification
    leaves. A suite whose phase record leaves a build open (`bench_manifest.open_build`) is `unfinished`; a record
    that ends on a reset or a hand-off leaves nothing to finish. `suite_closed` is its negation, for `REQUIRES`.
    `scan_open` is a scan attempt that has not landed and has something to resume (`_resumable`); only planning
    reads it (`_planned_scanned`), since a cut scan can leave `verification.json` behind."""
    exists = workdir.is_dir()
    scanned = (exists and (workdir / 'plurai.config.yaml').is_file() and (workdir / 'simulate.py').is_file()
               and (_art(workdir) / 'verification.json').is_file())
    attempt = _attempt(workdir) if exists else None
    scenarios = bool(_read_line(workdir / '.scenario_set_id'))
    evals = bool(_read_line(workdir / '.eval_set_id'))
    finished = _handed_off(workdir)
    unfinished = not finished and _bench_manifest().open_build(_phases(workdir)) is not None
    run_dir = _run_dir_from_pointer(workdir)
    return {
        'scanned': scanned,
        'scan_open': attempt is not None and _resumable(workdir, attempt),
        'scenarios': scenarios,
        'suite': scenarios and evals and finished,
        'unfinished': unfinished,
        'suite_closed': not unfinished,
        'run': run_dir is not None,
        'run_dir': run_dir,
        'diagnosable': run_dir is not None or _has_confirmed_finding(_art(workdir) / 'agent_findings.md'),
    }


def _planned_scanned(f: dict) -> bool:
    """Scanned, for planning: a scan that landed and no resumable attempt since."""
    return f['scanned'] and not f['scan_open']


def _options(f: dict, changed: bool) -> list[dict]:
    first = ('rerun',) if f['suite'] else ('finish',) if f['unfinished'] else ()
    order = (*first, 'new', 'rescan')
    options = []
    for choice in order:
        label, description = START_OPTIONS[choice]
        if choice == 'rescan' and changed:
            label += RESCAN_CHANGED
        options.append({'choice': choice, 'label': label, 'description': description})
    return options


def _prompt(question: str, options: tuple) -> dict:
    return {'question': question, 'options': [{'answer': a, 'label': label, 'description': d} for a, label, d in options]}


def state(workdir: Path, flowdir: Path | None) -> dict:
    f = facts(workdir)
    scanned = _planned_scanned(f)
    saved = _load_state(workdir, flowdir)
    root = _agent_root(workdir)
    agent_changed = None
    changed: list[str] = []
    scan = saved.get('scan')
    if scanned and isinstance(scan, dict) and isinstance(scan.get('files'), dict):
        changed = changed_files(scan['files'], fingerprint_now(root)['files'])
        agent_changed = bool(changed)
    traces = saved['traces']
    connected = traces.get('provider') in PROVIDERS and bool(traces.get('project'))
    if connected:
        traces_prompt = _prompt(TRACES_PROMPTS['confirm'][0].format(provider=PROVIDERS[traces['provider']], project=traces['project']),
                                TRACES_PROMPTS['confirm'][1])
    elif traces.get('ask', True) is not False:
        traces_prompt = _prompt(*TRACES_PROMPTS['connect'])
    else:
        traces_prompt = None
    anonymize_declared = _config_has_key(workdir / 'plurai.config.yaml', ANONYMIZE_KEY)
    anonymize_prompt = None if anonymize_declared or isinstance(traces.get('anonymize'), bool) else _prompt(*ANONYMIZE_PROMPT)
    env_prompt = None if scanned or isinstance(traces.get('env_file'), str) else _env_prompt(workdir)
    org_access_prompt = _org_access_prompt(workdir, traces)
    if scanned:
        question = 'start'
        prefix = ''
        if agent_changed:
            prefix = CHANGED_PREFIX.format(count=len(changed), s='' if len(changed) == 1 else 's', first=', '.join(changed[:CHANGED_SHOWN]))
        start = {'question': prefix + START_QUESTION, 'options': _options(f, bool(agent_changed))}
    else:
        question = 'traces_only' if traces_prompt or anonymize_prompt or env_prompt or org_access_prompt else 'none'
        start = None
    run_dir = f['run_dir']
    return {
        'workdir_exists': workdir.is_dir(),
        'agent_root': str(root),
        'scanned': scanned,
        'scan_open': f['scan_open'],
        'verified': (_art(workdir) / 'verification.json').is_file(),
        'agent_changed': agent_changed,
        'changed_files': {'count': len(changed), 'first': changed[:CHANGED_SHOWN]},
        'suite': {'exists': f['suite'], 'unfinished': f['unfinished'], 'scenarios': f['scenarios'], 'anchor': _anchor(workdir)},
        'run': {'exists': f['run'], 'name': run_dir.name if run_dir is not None else None},
        'traces': {
            'provider': traces.get('provider'),
            'project': traces.get('project'),
            'ask': traces.get('ask', True) is not False,
            'connected': connected,
            'credentials_in_env': {p: all(os.environ.get(k) for k in keys) for p, keys in PROVIDER_KEYS.items()},
        },
        'anonymize_declared': anonymize_declared,
        'anonymize_prompt': anonymize_prompt,
        'env_file': traces.get('env_file'),
        'env_prompt': env_prompt,
        'org_access_prompt': org_access_prompt,
        'question': question,
        'start_question': start,
        'options': start['options'] if start else [],
        'traces_prompt': traces_prompt,
    }


def _agentforce_script(name: str):  # noqa: ANN202 - a module
    """A script of the hosted route (stdlib only), imported only when this project may be one."""
    path = str(Path(__file__).resolve().parent.parent / 'skills' / 'simulation-setup-agentforce' / 'scripts')
    if path not in sys.path:
        sys.path.insert(0, path)
    return importlib.import_module(name)


def _org_actions(workdir: Path) -> tuple[list[str], list[str]] | None:
    """For a hosted Agentforce agent, from its bundle alone and writing nothing: the actions left real that need a grant,
    and those known to only read. None for any other agent. A bundle that cannot be read names no action, so grants none."""
    try:
        if _agentforce_script('agent_kind').classify(_agent_root(workdir)).kind != 'agent_script':
            return None
    except Exception:  # noqa: BLE001 - the start call never fails on this: no question, and the scan asks as it does without one
        return None
    try:
        rewire = _agentforce_script('rewire_agentforce')
        source = rewire.sole_agent_file(rewire._default_bundle_dir(rewire.DxProject.locate(_agent_root(workdir))))  # noqa: SLF001 - the workdir may not exist yet
        bundle = rewire.parse_bundle(source.read_text(encoding='utf-8'))
        by_name = bundle.by_name()
        real = [name for name, _ in rewire._split_by_fate(bundle, skip_complex_types=False)[1]]  # noqa: SLF001 - the rewrite's own split; its public entry refuses an ungranted action
        return sorted(n for n in real if not rewire._read_only(by_name[n])), sorted(n for n in real if rewire._read_only(by_name[n]))  # noqa: SLF001
    except Exception:  # noqa: BLE001 - see the docstring
        return [], []


def _org_refused(traces: dict) -> bool:
    """The start answer on record leaves the copy out, so nothing can run against the agent."""
    answer = traces.get('org_access')
    return isinstance(answer, list) and ORG_BRING_UP not in {*answer, *(traces.get('org_approved') or [])}


def _org_copy_refused(workdir: Path, flowdir: Path | None = None) -> bool:
    """A refusal on record that no later grant lifted."""
    return _org_refused(_load_state(workdir, flowdir)['traces']) and ORG_BRING_UP not in _granted_names(workdir / CONSENT_FILE)


def _org_access_prompt(workdir: Path, traces: dict) -> dict | None:
    """ORG_ACCESS_PROMPT for a hosted Agentforce agent, while neither a grant nor a refusal made elsewhere answers it. The
    user's own "none" at an earlier start is asked again: it rides the start call, and is the one way back."""
    answer = traces.get('org_access')
    if isinstance(answer, list) and not _org_refused(traces):
        return None
    if not isinstance(answer, list) and _consent_state(workdir / CONSENT_FILE) not in (None, 'unasked'):
        return None
    actions = _org_actions(workdir)
    if actions is None:
        return None
    needs, readers = actions
    question = ORG_ACCESS_PROMPT[0].format(actions=ORG_ACTIONS_CLAUSE.format(names=', '.join(needs)) if needs else '',
                                           others=ORG_READERS_CLAUSE.format(names=', '.join(readers)) if readers else ORG_NO_READERS_CLAUSE)
    (grant, grant_label, grant_said), refuse = ORG_ACCESS_PROMPT[1]
    options = ((grant.format(items=','.join([*ORG_ITEMS, *needs])), grant_label, grant_said.format(ran=', these actions run for real' if needs else '')), refuse)
    return _prompt(question, options)


def _record_org_access(workdir: Path, record: dict, value: str) -> None:
    """Record a `--org-access` answer. Every name must be one the question could have shown now."""
    if value == 'decline':
        record['org_access'] = []
        return
    kind, sep, names = value.partition(':')
    items = {n.strip() for n in names.split(',') if n.strip()}
    actions = _org_actions(workdir)
    if kind not in ('grant', 'approve') or not sep or not items or actions is None:
        raise Refusal('--org-access takes grant:<names>, approve:<names> or decline, on a hosted Agentforce agent; nothing was recorded.')
    known = {*ORG_ITEMS, *actions[0]}
    if items - known:
        raise Refusal(f'--org-access: {", ".join(sorted(items - known))} is not something this question can approve; nothing was recorded.')
    if kind == 'grant':
        record['org_access'] = sorted(items)
    else:
        record['org_approved'] = sorted(items | set(record.get('org_approved') or []))


def _writes_on_map(tool: object) -> bool:
    """The map says this writes to the org. Only that takes back an action the user named: a missing type, or an effect
    that is no write (`effects: [network]`), does not."""
    return isinstance(tool, dict) and tool.get('writes') == 'live'


def apply_org_access(workdir: Path, flowdir: Path | None, *, typed_live: frozenset[str] = frozenset(), only_add: bool = False) -> str | None:
    """Turn the org-access answers into `.host_effects_consent`, or None when there are none or no map to read yet.

    Grants only what they name. An action the start answer names that the map (or `typed_live`, the bench's writers) says
    writes is left out, unless the user approved that one by name; so is anything never named. A later approval is added
    to the file as it stands: `only_add` also keeps what the file already grants. Returns what the model is told, naming
    what stayed off."""
    record = _load_state(workdir, flowdir)['traces']
    answer = record.get('org_access')
    approved = set(record.get('org_approved') or [])
    topology = _read_json(_art(workdir) / 'agent_topology.json')
    if not (isinstance(answer, list) or approved) or not isinstance(topology, dict):
        return None
    answer = answer if isinstance(answer, list) else []
    consent = workdir / CONSENT_FILE
    if _org_refused(record) and not approved:
        if _consent_state(consent) in (None, 'unasked'):
            consent.write_text('declined\n', encoding='utf-8')
        return 'Nothing is granted in the org: no copy is deployed, and this run goes no further than mapping the agent.'
    tools = {str(t.get('name')): t for t in topology.get('tools') or [] if isinstance(t, dict)}
    named = set(answer) - set(ORG_ITEMS)
    kept = {n for n in named if not _writes_on_map(tools.get(n)) and n not in typed_live}
    taken_off = set() if only_add else named - kept - approved
    granted = (_granted_names(consent) - taken_off) | (set(answer) & set(ORG_ITEMS)) | kept | approved
    consent.write_text(f'granted: {", ".join(sorted(granted))}\n', encoding='utf-8')
    actions = _org_actions(workdir)
    required = {name for name, t in tools.items() if t.get('consent') == 'required'}
    off = sorted(((named | required | set(actions[0] if actions else ())) - granted))
    return f'Granted: {", ".join(sorted(granted))}.' + (f' Left out, and off: {", ".join(off)}.' if off else '')


def _env_prompt(workdir: Path) -> dict:
    """ENV_PROMPT with this machine's locations: names and paths only, no file opened but to find it."""
    bm = _bench_manifest()
    doc = _read_json(workdir / bm.MANIFEST_FILENAME)
    env_file = bm.env_file_path(doc if isinstance(doc, dict) else {}, workdir)
    logins = sorted({f'~/{rel}' for rels in bm._PROVIDER_FILES.values() for rel in rels if (Path.home() / rel).exists()})  # noqa: SLF001
    question = ENV_PROMPT[0].format(file=f', then {env_file}' if env_file else ', then a .env file in your agent\'s folder once there is one',
                                    logins=f'; model-provider logins also from {", ".join(logins)}' if logins else '')
    return _prompt(question, ENV_PROMPT[1])


def _record_env_file(workdir: Path, value: str) -> tuple[str, str | None]:
    """The `env_file` record for an `--env-file` answer, and a warning: `default` is `""`; a path is made absolute (`~`
    expanded, a relative one from the agent root). A named file that is not there yet is recorded all the same."""
    if value == 'default':
        return '', None
    path = Path(value).expanduser()
    path = path if path.is_absolute() else _agent_root(workdir) / path
    if not _bench_manifest()._platform.is_local_drive_path(path):  # noqa: SLF001 - a network path is never touched
        raise Refusal(f'{path} is not on a local drive; name a file on this machine. Nothing was recorded.')
    warning = None if path.is_file() else f'WARNING: {path} does not exist yet; it is read once it does.'
    return str(path.resolve()), warning


def _apply_env_file(workdir: Path, recorded: object) -> str:
    """Write a named env file into the bench manifest's `harness.env_file`, the one place the harness and every
    presence check read it from: relative to the agent module when the file is under it, else absolute."""
    if not isinstance(recorded, str):
        return 'no settings-location answer recorded'
    if not recorded:
        return 'settings are read from the default locations'
    bm = _bench_manifest()
    target = workdir / bm.MANIFEST_FILENAME
    doc = _read_json(target)
    if not isinstance(doc, dict) or not isinstance(doc.get('harness'), dict):
        return f'the settings file {recorded} is recorded until the scan writes the bench manifest'
    path, base = Path(recorded), bm.agent_dir(workdir).resolve()
    doc['harness']['env_file'] = path.relative_to(base).as_posix() if path.is_relative_to(base) else str(path)
    bm._write_json(target, doc)  # noqa: SLF001 - the manifest's own atomic writer
    return f'harness.env_file: {doc["harness"]["env_file"]} written to {bm.MANIFEST_FILENAME}'


def _anchor(workdir: Path) -> str | None:
    record = _read_json(_art(workdir) / 'suite_anchor.json')
    value = record.get('anchor') if isinstance(record, dict) else None
    return value if isinstance(value, str) else None


# ── subcommands ───────────────────────────────────────────────────────────────────────────────


def plan(workdir: Path, choice: str, traces: str, flowdir: Path | None = None) -> str:
    _set_aside_if_empty(workdir)
    f = facts(workdir)
    f = {**f, 'scanned': _planned_scanned(f)}  # a scan cut after it rewrote the agent's description is finished first
    if choice == 'full' and f['scanned']:
        choice = 'rescan'
    if choice != 'full' and not f['scanned']:
        choice = 'full'  # nothing to rerun or rebuild on: the first run is always end to end
    spec = PLANS[choice]
    missing = [need for need in spec['needs'] if not f[need]]
    if missing:
        # A stopped build is finished, never rebuilt: `new` would set its paid work aside.
        instead = 'finish' if f['unfinished'] and choice != 'finish' else 'new'
        raise Refusal(f'{MISSING_SENTENCES[missing[0]]} to {choice}; choose {START_OPTIONS[instead][0]}.')
    refused = _org_copy_refused(workdir, flowdir)
    if refused and all(stage in ORG_COPY_STAGES for stage in spec['stages']):
        raise Refusal(ORG_REFUSED_PLAN)
    if choice == 'new':
        # A new suite: set any unfinished build aside, or the suite resumes it (`reset_suite` writes nothing over a
        # finished one, which the suite's resume already reads as over). On stderr — stdout is the stage line.
        reset = _bench_manifest().reset_suite(workdir, 'user chose a new suite')
        if reset:
            print(reset, file=sys.stderr)
    stages = [stage for stage in spec['stages'] if not (refused and stage in ORG_COPY_STAGES)]
    if traces == 'yes':
        stages.insert(1 if stages[0] == 'scan' else 0, 'traces')
    return ','.join(stages)


def _plan_file(flowdir: Path | None) -> dict:
    data = _read_json(flowdir / PLAN_FILE) if flowdir is not None else None
    return data if isinstance(data, dict) else {}


def _since(explicit: str | None, flowdir: Path | None, stage: str | None) -> float | None:
    if explicit:
        return _parse_time(explicit)
    plan_file = _plan_file(flowdir)
    since = plan_file.get('since')
    if stage and isinstance(since, dict) and stage in since:
        return _parse_time(since[stage])
    return _parse_time(plan_file.get('started_at'))


def run_stage(workdir: Path, since: float | None) -> str:
    """`present` iff the newest FINAL run is of the current scenario set, written in this flow — audited or not: an
    unaudited final is owed its audit (`deliverables`), never a second run.

    The index is rebuilt from the run folders on disk (`runs_index.build_index`, nothing written): a
    stage that never ran `runs_index.py write` leaves `runs/index.json` behind its own final run."""
    if not (_art(workdir) / 'runs').is_dir() or since is None:
        return 'run'
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import runs_index  # noqa: PLC0415 - sibling script, stdlib only

    index = runs_index.build_index(workdir, {})
    entry = ([r for r in index['runs'] if r['stage'] == 'final'] or [None])[-1]
    current = _read_line(workdir / '.scenario_set_id')
    written = _parse_time(entry.get('written_at')) if entry else None
    if (entry and entry.get('stage') == 'final' and current and entry.get('scenario_set_id') == current
            and written is not None and written >= since):
        return 'present'
    return 'run'


def run_summary(workdir: Path) -> str:
    run_dir = _scored_run_dir(workdir)
    spec = _read_json(run_dir / 'run.json') if run_dir is not None else None
    totals = spec.get('totals') if isinstance(spec, dict) else None
    if not isinstance(totals, dict):
        raise Refusal('No scored run to summarize.')
    trials = spec.get('trials') if isinstance(spec.get('trials'), dict) else {}
    n = trials.get('n') if isinstance(trials.get('n'), int) else 1
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import run_numbers  # noqa: PLC0415 - sibling script: THE pass line every surface pastes

    try:
        counts = [run_numbers.pass_line(spec)]
        over = ''
    except run_numbers.C7Missing:  # an older run.json: its own counts, pooled over every trial, as before
        counts = [f"{totals.get('passed', 0)} passed", f"{totals.get('failed', 0)} failed"]
        over = f" of {totals['conversations']} conversations" if totals.get('conversations') else ''
        if over and 0 < (hops := int(totals.get('single_hop_replays') or 0)) <= totals['conversations']:
            # A single-hop regression row replayed one recorded turn: a replay, never a conversation.
            over = f" of {totals['conversations'] - hops} conversations and {hops} replays of a recorded turn"
    # A `not_valid` row is counted in `errored` too (`build_run_json`), so it is said once, as not valid.
    voided = totals.get('not_valid') if isinstance(totals.get('not_valid'), int) else 0
    for key, word in (('errored', 'errored'), ('incomplete', 'incomplete'), ('not_evaluated', 'not evaluated'), ('not_valid', 'not valid'),
                      ('missing_scenarios', 'never ran')):
        value = max(0, totals[key] - voided) if key == 'errored' and isinstance(totals.get(key), int) else totals.get(key)
        if value:
            counts.append(f'{value} {word}')
    line = f"Final run: {totals.get('scenarios', 0)} scenarios, {n} trial{'' if n == 1 else 's'} each — {', '.join(counts)}{over}."
    verdict = _read_json(run_dir / 'release_verdict.json')
    if isinstance(verdict, dict):
        # The Agent line rides beside the rung: the closing is the one place a `/simulate` run states the agent's verdict.
        line += ''.join(f' {v.rstrip(".")}.' for v in (verdict.get('rung'), verdict.get('agent_line'), verdict.get('customer_banner'))
                        if isinstance(v, str) and v.strip())
    return line


def _driven(step: str, bases: list[Path]) -> bool:
    """Does the open window name a command that drives `step` (`flow_arm.FLOW_DRIVERS`)? Fails open to True."""
    try:
        spec = importlib.util.spec_from_file_location('_simulate_flow_arm', FLOW_ARM)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[spec.name] = module  # type: ignore[union-attr]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        drivers = module.FLOW_DRIVERS.get(STEP_WINDOWS[step], ())
    except Exception:  # noqa: BLE001 - a requirement check never stops a driven run
        print('simulate_plan: the driver table could not be read; the requirement check is skipped', file=sys.stderr)
        return True
    for base in bases:
        if _read_line(base / FLOW_MARKER) in drivers:
            return True
    return False


def require(step: str, workdir: Path, flowdir: Path | None) -> str | None:
    """None when `step` may run, else the one line that says what is missing."""
    bases = [b for b in (flowdir, workdir) if b is not None]
    if _driven(step, list(dict.fromkeys(bases))):
        return None
    f = facts(workdir)
    for need in REQUIRES[step]:
        if not f[need]:
            return MISSING_SENTENCES[need] + MISSING_TAILS.get(need, MISSING_TAIL)
    return None


def require_warning(step: str, workdir: Path, flowdir: Path | None) -> str | None:
    """The line a step that may run prints over a scan attempt that has not landed; never a refusal. An attempt that
    wrote nothing is set aside instead: this step runs on the scan that landed."""
    bases = [b for b in (flowdir, workdir) if b is not None]
    if 'scanned' not in REQUIRES[step] or _driven(step, list(dict.fromkeys(bases))):
        return None
    _set_aside_if_empty(workdir)
    return SCAN_OPEN_WARNING if facts(workdir)['scan_open'] else None


def _bench_writers(workdir: Path) -> frozenset[str]:
    """The writers on the bench's `host_effects`. That list also holds what the map types only an effect, which is no
    write to the org. Without a config to tell them apart, all of it counts."""
    listed = _agentforce_script('rewire_agentforce').declared_host_effects(workdir)
    bm = _bench_manifest()
    config = bm._load_yaml(workdir / 'plurai.config.yaml')  # noqa: SLF001
    topology = _read_json(_art(workdir) / 'agent_topology.json')
    if not isinstance(config, dict) or not isinstance(topology, dict):
        return listed
    declared = bm.live_write_tools(config, at_agent_host=bm.intercepted_at_agent_host(topology), typed_writes=bm.typed_writes(topology))
    return listed & {*declared, *bm.typed_live_writers(topology)}


def _narrow_org_access(workdir: Path) -> str:
    """The scan's last word on the start answer: its bench lists the writers the map alone did not. What that takes off
    the grant is said here. Nothing to say when there is no answer, or the list is not current."""
    if not _load_state(workdir, None)['traces'].get('org_access'):
        return ''
    before = _granted_names(workdir / CONSENT_FILE)
    try:
        apply_org_access(workdir, None, typed_live=_bench_writers(workdir))
    except Exception:  # noqa: BLE001 - the fingerprint already landed, and the grant stands as the map left it
        return ''
    removed = before - _granted_names(workdir / CONSENT_FILE)
    return f' Left out of the org approval, and off: {", ".join(sorted(removed))}.' if removed else ''


def fingerprint(workdir: Path, flowdir: Path | None) -> str:
    root = _agent_root(workdir)
    now = fingerprint_now(root)
    stored = _read_json(workdir / STATE_FILE)
    stored = stored if isinstance(stored, dict) else _empty_state()
    stored['schema'] = SCHEMA
    stored['scan'] = {'at': _now(), 'root': _root_label(root), 'files': now['files'], 'truncated': now['truncated']}
    stored.pop('scan_open', None)  # the scan landed: its attempt is closed by the same write
    traces = _load_state(workdir, flowdir)['traces']
    stored['traces'] = traces
    _write_json(workdir / STATE_FILE, stored)
    if flowdir is not None:
        try:
            (flowdir / PENDING_FILE).unlink()
        except OSError:
            pass
    line = f"simulate_plan: agent fingerprint recorded ({len(now['files'])} files{', capped' if now['truncated'] else ''})."
    line += _narrow_org_access(workdir)
    # A landed scan makes an unfinished suite build stale: the next suite run rebuilds on this scan.
    try:
        reset = _bench_manifest().reset_suite(workdir, 'the agent was rescanned')
    except Exception:  # noqa: BLE001 - the fingerprint already landed; the suite's resume reports its own record
        reset = None
    return f'{line} {reset}' if reset else line


def traces_cmd(workdir: Path, flowdir: Path | None, args: argparse.Namespace) -> str:
    record = _load_state(workdir, flowdir)['traces']
    said = []
    if args.connect:
        record.update(provider=args.connect, project=args.project, ask=True)
        said.append(f'connected {PROVIDERS[args.connect]} project {args.project}')
    elif args.never_ask:
        record['ask'] = False
        said.append('the traces prompt will not be asked again')
    elif args.skip:
        said.append('skipped for this run')
    if args.anonymize not in (None, 'recorded'):
        record['anonymize'] = args.anonymize == 'true'
    warning = None
    if args.env_file not in (None, 'recorded'):
        record['env_file'], warning = _record_env_file(workdir, args.env_file)
    if args.org_access is not None:
        _record_org_access(workdir, record, args.org_access)
    if args.connect or args.never_ask or args.anonymize not in (None, 'recorded') or args.env_file not in (None, 'recorded') or args.org_access is not None:
        _save_traces(workdir, flowdir, record)  # before the config write, which may refuse
    if args.org_access is not None:
        approving = args.org_access.startswith('approve:')
        said.append((apply_org_access(workdir, flowdir, only_add=approving) or 'the org access answer is recorded until the scan maps the agent').rstrip('.'))
    if args.env_file is not None:
        said.append(_apply_env_file(workdir, record.get('env_file')))
    if args.anonymize is not None:
        said.append(_apply_anonymize(workdir, record.get('anonymize')))
    return 'simulate_plan: ' + '; '.join(said) + '.' + (f'\n{warning}' if warning else '')


def _apply_anonymize(workdir: Path, answer: object) -> str:
    """Append the user's own start answer as `anonymize:`; never over a key that is already there."""
    config = workdir / 'plurai.config.yaml'
    if _config_has_key(config, ANONYMIZE_KEY):
        raise Refusal('plurai.config.yaml already declares anonymize; it was left as it is.')
    if not isinstance(answer, bool):
        return 'no anonymize answer recorded'
    if not config.is_file():
        return 'the anonymize answer is recorded until the scan writes plurai.config.yaml'
    text = config.read_text(encoding='utf-8')
    config.write_text(text + ('' if text.endswith('\n') or not text else '\n') + f'{ANONYMIZE_KEY}: {"true" if answer else "false"}\n', encoding='utf-8')
    return f'anonymize: {"true" if answer else "false"} written to plurai.config.yaml'


def _topology_sentences(workdir: Path, _value: object) -> list[str]:
    out = [TOPOLOGY_SENTENCES['approved']]
    topology = _read_json(_art(workdir) / 'agent_topology.json')
    topology = topology if isinstance(topology, dict) else {}
    # Tool probes need no consent (they run with no network); only an explicit earlier "No" leaves them unrun.
    if topology.get('probes') and _consent_state(workdir / '.probe_consent') == 'declined':
        out.append(TOPOLOGY_SENTENCES['unprobed'])
    granted = _granted_names(workdir / '.host_effects_consent')
    held = sorted(
        str(t.get('name')) for t in topology.get('tools') or []
        if isinstance(t, dict) and t.get('consent') == 'required' and t.get('name') and str(t.get('name')) not in granted
    )
    denied = _denied(workdir)
    reasons = {n: denied[n] for n in held if isinstance(denied.get(n), list) and denied[n]}
    off = [n for n in held if n in reasons and all(str(r).startswith('disallowed') for r in reasons[n])]
    for key, names in (('switched-off', off), ('sandboxed', [n for n in reasons if n not in off]),
                       ('mocked', [n for n in held if n not in reasons])):
        if names:
            out.append(TOPOLOGY_SENTENCES[key].format(names=', '.join(names)))
    return out


def _denied(workdir: Path) -> dict:
    """The scored run's `run_summary.json` `host_effects.denied` ({tool: [reason, …]}); `{}` with none readable."""
    run_dir = _scored_run_dir(workdir)
    summary = _read_json(run_dir / 'run_summary.json') if run_dir is not None else None
    effects = summary.get('host_effects') if isinstance(summary, dict) else None
    denied = effects.get('denied') if isinstance(effects, dict) else None
    return denied if isinstance(denied, dict) else {}


def _document_count(workdir: Path) -> int | None:
    """The documents the corpus was built over: `.corpus_scope`'s newest `declared_units`, else the map's sources."""
    try:
        lines = (workdir / '.corpus_scope').read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        lines = []
    for line in reversed(lines):
        key, sep, value = line.partition(':')
        if sep and key.strip() == 'declared_units' and value.strip().isdigit():
            return int(value.strip())
    topology = _read_json(_art(workdir) / 'agent_topology.json')
    grounding = topology.get('grounding') if isinstance(topology, dict) else None
    sources = grounding.get('sources') if isinstance(grounding, dict) else None
    return len(sources) if isinstance(sources, list) and sources else None


def _grounding_sentences(workdir: Path, value: object) -> list[str]:
    sentence = GROUNDING_SENTENCES.get(value) if isinstance(value, str) else None
    if sentence is None:
        return []
    error = None
    if value == 'build-from-recorded-sources' and _no_corpus_built(workdir):
        error = _extractor_error(workdir)
        sentence = CORPUS_NOT_BUILT if error is None else CORPUS_CANNOT_BUILD
    count = _document_count(workdir)
    documents = "agent's documents" if count is None else 'document' if count == 1 else f'{count} documents'
    return [sentence.format(documents=documents, error=error)]


def _extractor_error(workdir: Path) -> str | None:
    """The failed extractor install's `error` (`resolve_plurai.EXTRACTOR_MISSING`), only while the grounding job delivered
    no corpus build at all (`.grounding_status.json` `detail.corpus_builds == []`): a build that ran and failed on the
    backend is never blamed on the install. ``None`` otherwise."""
    marker = _read_json(_art(workdir) / '.extractor_missing')
    error = marker.get('error') if isinstance(marker, dict) else None
    if not isinstance(error, str) or not error.strip():
        return None
    status = _read_json(_art(workdir) / '.grounding_status.json')
    detail = status.get('detail') if isinstance(status, dict) else None
    return error.strip() if isinstance(detail, dict) and detail.get('corpus_builds') == [] else None


def _no_corpus_built(workdir: Path) -> bool:
    """No corpus marker shape names a corpus id and none is unreadable (`corpus_coordinate.read_coordinate`: no marker,
    or only a `.knowledge_bases` base declared before it was built); False when it cannot be asked."""
    try:
        if (here := str(Path(__file__).resolve().parent)) not in sys.path:
            sys.path.insert(0, here)
        import corpus_coordinate  # noqa: PLC0415 - sibling script: all three corpus marker shapes are read there

        coordinate = corpus_coordinate.read_coordinate(workdir)
    except Exception:  # noqa: BLE001 - a closing sentence never stops the closing
        return False
    return not coordinate.unreadable and not coordinate.ids


#: The trace brief's sentence, by the default it took (`_run_driver._apply_brief_default` decides which).
APPLY_BRIEF_SENTENCES = {
    'env-only': 'The production-trace brief was applied to the existing mock world; say so if it should not be.',
    'none': 'The production-trace brief was saved for the next suite build; this run kept the mock world as it was. Say so to apply it now.',
}


def _apply_brief_sentences(workdir: Path, value: object) -> list[str]:
    sentence = APPLY_BRIEF_SENTENCES.get(value) if isinstance(value, str) else None
    return [sentence] if sentence else []


#: Gates whose closing sentences depend on the value taken and on the project's files.
COMPOSED_SENTENCES = {'apply-brief': _apply_brief_sentences, 'topology-approval': _topology_sentences, 'grounding-gate': _grounding_sentences}


#: The run's-end sentence for a default that backed a scope cut (`hooks/_run_driver.scope_of_default` files the items on its
#: row): the switches in full (few, and what a customer can flip), the tools by count and the first three, the inputs (often
#: scores of request fields and endpoints) by count only — the row and run.json keep every name.
SCOPE_SENTENCE = 'The simulation left out what the agent map proposed — {parts}; say which to include.'
#: (row key, label, how many names the sentence spells out; None = all, 0 = the count alone, its label taking `{s}`).
SCOPE_PARTS = (
    ('switches', 'settings it held at the simulation\'s value', None),
    ('tools', 'tools that stayed switched off', 3),
    ('inputs', 'input{s} your agent accepts that the simulation did not send', 0),
)


def _scope_sentences(row: dict) -> list[str]:
    """The cut a defaulted row backed, by kind; nothing when the row carries none."""
    parts = []
    for key, label, shown in SCOPE_PARTS:
        value = row.get(key)
        names = [n for n in value if isinstance(n, str) and n] if isinstance(value, list) else []
        if names and shown == 0:
            parts.append(f'{len(names)} ' + label.format(s='' if len(names) == 1 else 's'))
        elif names:
            more = len(names) - shown if shown is not None and len(names) > shown else 0
            parts.append(f'{label} ({len(names)}): ' + ', '.join(f'`{n}`' for n in names[:len(names) - more])
                         + (f' and {more} more' if more else ''))
    return [SCOPE_SENTENCE.format(parts='; '.join(parts))] if parts else []


def defaults(workdir: Path, since: float | None) -> list[str]:
    """One sentence per gate default this `/simulate` run took, latest row per gate, in gate order of first use — and,
    for a default that backed a scope cut, the cut by name (`DECIDED_FOR_YOU` says whose rows count)."""
    try:
        lines = (_art(workdir) / '.run_driver_findings.jsonl').read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return []
    taken: dict[str, object] = {}
    rows: dict[str, dict] = {}
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not (isinstance(row, dict) and row.get('kind') == DEFAULT_KIND and row.get('declared_by') in DECIDED_FOR_YOU):
            continue
        at = _parse_time(row.get('at'))
        if since is not None and (at is None or at < since):
            continue
        gate = row.get('gate')
        if isinstance(gate, str):
            taken[gate] = row.get('default')
            rows[gate] = row
    out: list[str] = []
    for gate, value in taken.items():
        if gate in COMPOSED_SENTENCES:
            out.extend(COMPOSED_SENTENCES[gate](workdir, value))
        elif DEFAULT_SENTENCES.get(gate):
            out.append(DEFAULT_SENTENCES[gate])  # type: ignore[arg-type]
        out.extend(_scope_sentences(rows[gate]))
    return out


#: The closing message's lead, and the line before its defaults (`closing`).
CLOSING_LEAD = 'The simulation is finished.'
CLOSING_DEFAULTS_LEAD = 'Decided for you, without asking:'
#: The line before the harness repairs this run applied (`_closing_harness_fixes`).
CLOSING_HARNESS_LEAD = 'Fixed in your simulation harness, without asking:'


def _logged_this_run(workdir: Path, kind: str, since: float | None) -> list[str]:
    """The findings of this run's `kind` loop-log entries (`append_loop_log`, the log's one parser). Stamps are
    minute-precision, so an entry in the minute the run started counts as this run's."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import append_loop_log  # noqa: PLC0415 - sibling script, stdlib only

    found = []
    for entry in append_loop_log.parse_entries(_art(workdir) / 'loop_log.md'):
        if entry.kind != kind or not entry.finding.strip():
            continue
        when = entry.when
        if since is not None and (when is None or when.timestamp() < since - 60):
            continue
        found.append(entry.finding.strip())
    return found


def _closing_harness_fixes(workdir: Path, since: float | None) -> list[str]:
    """Each harness repair this run applied, in the words its `harness-fix` loop-log entry gives."""
    fixes = [f'- {finding}' for finding in _logged_this_run(workdir, 'harness-fix', since)]
    return [CLOSING_HARNESS_LEAD, *fixes] if fixes else []
#: The fix plan's line. Never opens with the fixes hand-off's own anchor word: this is a summary of it, not the hand-off.
CLOSING_PLAN_LINK = 'Open the fix plan →'


def _plan_written_this_run(workdir: Path, since: float | None) -> Path | None:
    """The fix plan, when this run wrote it: a `fixes.md` from before `since` is an earlier run's."""
    plan = _art(workdir) / 'fixes.md'
    return None if not plan.is_file() or (since is not None and plan.stat().st_mtime < since) else plan


def _closing_issues(workdir: Path, since: float | None) -> list[str]:
    """The fix plan's line: the count the fixes anchor says (`fix_verification.issue_counts`, the page's tiers plus the
    plan's own issues) and its link (`report_link`), when this run wrote the plan (`_plan_written_this_run`). Stdlib
    only: this runs under a bare `python3`."""
    plan = _plan_written_this_run(workdir, since)
    if plan is None:
        return []
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import fix_verification  # noqa: PLC0415 - sibling script, stdlib only
    import report_link  # noqa: PLC0415 - sibling script; puts hooks/ on sys.path for `_workdir`

    count, _ = fix_verification.issue_counts(plan.read_text(encoding='utf-8', errors='replace'), workdir)
    link = f'**[{CLOSING_PLAN_LINK}]({report_link.target(plan)})**'
    # Nothing counted: the plan is linked alone, never miscounted as none.
    return [f'{count} — {link}' if count else link]


def _closing_page(workdir: Path, since: float | None) -> list[str]:
    """The overview page's line (`publish_report.closing_link`, which owns its label and its record)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import publish_report  # noqa: PLC0415 - sibling script; imported only here, it loads the page machinery

    return publish_report.closing_link(workdir, since)


def _closing_defaults(workdir: Path, since: float | None) -> list[str]:
    said = defaults(workdir, since)
    return [CLOSING_DEFAULTS_LEAD, *(f'- {sentence}' for sentence in said)] if said else []


def _closing_next(workdir: Path, since: float | None) -> list[str]:
    """The one command to type next: score the fixes this run applied, else choose from its fix plan, else score the
    agent. Left out when the org refusal has already named `/simulate`, and when there is no suite to run. Whether a
    fix was applied is read from the `fix-applied` entries the fixes stage logs, never from the plan: its `Regression:`
    lines read the same before and after a choice."""
    if _org_copy_refused(workdir):
        return []
    if _plan_written_this_run(workdir, since) is not None:
        if _logged_this_run(workdir, 'fix-applied', since):
            stage, purpose = 'run', 'to score the fixes you applied'
        else:
            stage, purpose = 'fixes', 'to choose which fixes from the plan to apply'
    elif _scored_run_dir(workdir) is not None:
        stage, purpose = 'run', 'to score your agent again after you change it'
    elif facts(workdir)['suite']:
        stage, purpose = 'run', 'to score your agent'
    else:
        return []
    return [f"Next: `{next(invocation for _, invocation, rewind in FEEDBACK_ROUTES if rewind == stage)}` {purpose}."]


def _respelled(text: str) -> str:
    """``text`` with each of the plugin's commands spelled the way this harness types it; as it is when that fails."""
    try:
        sys.path.insert(0, str(FLOW_ARM.parent))
        import _workdir  # noqa: PLC0415 - which harness owns the shell
        import session_glossary  # noqa: PLC0415 - the command spelling's one home

        return session_glossary.respell_commands(text, _workdir.harness())
    except Exception:  # noqa: BLE001 - the closing stands; only its spelling is lost
        return text


def closing(workdir: Path, since: float | None) -> str:
    """The whole closing message of a `/simulate` run: the outcome (`run_summary`), the fix plan's issues and link, the
    overview page, the harness repairs it applied, the defaults the run took, then the next command. A piece whose source
    is absent or unreadable is left out; the lead always stands, so a closing never fails."""
    blocks = []
    try:
        outcome = run_summary(workdir)
    except Exception:  # noqa: BLE001 - no scored run (a Refusal) or an unreadable one: the lead stands alone
        outcome = ''
    blocks.append(f'{CLOSING_LEAD} {outcome}'.strip())
    if _org_copy_refused(workdir):
        blocks.append(ORG_REFUSED_CLOSING)
    for piece in (_closing_issues, _closing_page, _closing_harness_fixes, _closing_defaults, _closing_next):
        try:
            lines = piece(workdir, since)
        except Exception:  # noqa: BLE001 - an unreadable source leaves its piece out
            lines = []
        if lines:
            blocks.append('\n'.join(lines))
    return _respelled('\n\n'.join(blocks))


# ── CLI ───────────────────────────────────────────────────────────────────────────────────────


def _parser() -> argparse.ArgumentParser:
    ap = _Parser(prog='simulate_plan.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', parser_class=_Parser)
    sub.required = True

    def with_workdir(name: str, **kw: object) -> argparse.ArgumentParser:
        p = sub.add_parser(name, **kw)
        p.add_argument('workdir', type=Path)
        p.add_argument('--flowdir', default=None)
        return p

    with_workdir('state')
    p = with_workdir('plan')
    p.add_argument('--choice', required=True, choices=sorted(PLANS))
    p.add_argument('--traces', default='no', choices=('yes', 'no'))
    with_workdir('run-stage').add_argument('--since', default=None)
    with_workdir('run-summary')
    p = sub.add_parser('require')
    p.add_argument('step', choices=sorted(REQUIRES))
    p.add_argument('workdir', type=Path)
    p.add_argument('--flowdir', default=None)
    with_workdir('fingerprint')
    p = with_workdir('scan-open')
    p.add_argument('--mode', required=True, choices=SCAN_MODES)
    p.add_argument('--component', default=None)
    p.add_argument('--restart', action='store_true')
    with_workdir('scan-resume').add_argument('--restart', action='store_true')
    p = with_workdir('traces')
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--connect', choices=sorted(PROVIDERS))
    mode.add_argument('--never-ask', action='store_true')
    mode.add_argument('--skip', action='store_true')
    p.add_argument('--project', default=None)
    p.add_argument('--anonymize', nargs='?', const='recorded', choices=('true', 'false', 'recorded'))
    p.add_argument('--env-file', nargs='?', const='recorded', help='default, or the path of the file to read settings from')
    p.add_argument('--org-access', default=None, help='grant:<names>, decline, or approve:<names>: the org-access answer')
    with_workdir('defaults').add_argument('--since', default=None)
    with_workdir('closing').add_argument('--since', default=None)
    sub.add_parser('routes')
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _parser()
    args = ap.parse_args(argv)
    if args.cmd == 'routes':
        print(json.dumps([[label, invocation] for label, invocation, _ in FEEDBACK_ROUTES], ensure_ascii=False))
        return 0
    if args.cmd == 'traces':
        if args.connect and not args.project:
            ap.error('traces --connect needs --project')
        if not (args.connect or args.never_ask or args.skip or args.anonymize is not None or args.env_file is not None or args.org_access is not None):
            ap.error('traces needs --connect, --never-ask, --skip, --anonymize, --env-file or --org-access')
    if _agent_root_refused():
        print('simulate_plan: no agent root resolved (PLURAI_AGENT_RESOLVED=0) — see resolve-workdir.sh\'s ERROR above; '
              '`cd` into the agent directory and re-run.', file=sys.stderr)
        return 2
    workdir = args.workdir.resolve()
    flowdir = _flowdir(args.flowdir)
    try:
        if args.cmd == 'state':
            print(json.dumps(state(workdir, flowdir), ensure_ascii=False, sort_keys=True))
        elif args.cmd == 'plan':
            print(plan(workdir, args.choice, args.traces, flowdir))
        elif args.cmd == 'run-stage':
            print(run_stage(workdir, _since(args.since, flowdir, 'run')))
        elif args.cmd == 'run-summary':
            print(run_summary(workdir))
        elif args.cmd == 'require':
            line = require(args.step, workdir, flowdir)
            if line is not None:
                print(line)
                return 3
            warning = require_warning(args.step, workdir, flowdir)
            if warning is not None:
                print(warning)
        elif args.cmd == 'fingerprint':
            print(fingerprint(workdir, flowdir))
        elif args.cmd == 'scan-open':
            component = args.component
            if component == '-':  # the user's own words arrive on stdin (a quoted heredoc), never inside shell quotes
                component = sys.stdin.buffer.read().decode('utf-8', errors='replace')
            print(scan_open(workdir, args.mode, (component or '').strip() or None, args.restart))
        elif args.cmd == 'scan-resume':
            code, lines = scan_resume(workdir, args.restart)
            print('\n'.join(lines))
            return code
        elif args.cmd == 'traces':
            print(traces_cmd(workdir, flowdir, args))
        elif args.cmd == 'defaults':
            for sentence in defaults(workdir, _since(args.since, flowdir, None)):
                print(sentence)
        elif args.cmd == 'closing':
            print(closing(workdir, _since(args.since, flowdir, None)))
    except Refusal as refusal:
        print(str(refusal))
        return 3
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
