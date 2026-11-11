"""Rewire the retrieved Agent Script bundle onto the plurai mock service.

The agent reasons and acts on Salesforce's servers, so nothing outside the org can intercept an
action call. The one escape channel is an outbound callout, so every mockable action is repointed
at a generated Apex class whose body calls the mock path the Plurai backend serves. The result is a *rewired copy*
— a second agent with its own developer name — and the customer's own agent is never edited.

Two rules shape everything below.

The tool surface stays put. An action's inputs, outputs, descriptions, labels and flags are copied
line for line, quoting and all, because the file quotes some names and not others and
re-serializing would silently rewrite the schema the model sees. The one addition is the
`plurai_session_id` input: the mock service keys its world state by session, an `@InvocableMethod`
receives only declared invocable variables, and the credential URL is env-scoped rather than
session-scoped, so there is no other channel for it.

No secret is generated. The External Credential describes only the shape of the credential, and the
token is pushed to its principal at deploy time through the Connect REST API.

This module is stdlib-only and takes its project root from `--project-dir` (default: the
working directory), because the setup skill copies it into the customer's `plurai_sim/` and
the lifecycle script runs it from the project root long after the plugin is gone.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import http.client
import importlib.util
import json
import os
import re
import shlex
import shutil
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple
from urllib.parse import urlsplit

# --- naming ------------------------------------------------------------------------------------

COPY_SUFFIX = '_Plurai'
COPY_LABEL_SUFFIX = ' (Plurai Mock)'
SESSION_VARIABLE = 'plurai_session_id'
APEX_PREFIX = 'PluraiMock_'
CALLOUT_CLASS = 'PluraiMockCallout'
NAMED_CREDENTIAL = 'Plurai_Mock'
EXTERNAL_CREDENTIAL = 'Plurai_Mock_Auth'
PRINCIPAL = 'Plurai_Mock_Principal'
# The credential parameter the callout's merge field reads. The value is never in this repo.
TOKEN_PARAMETER = 'Token'  # noqa: S105
PERMISSION_SET = 'Plurai_Mock'
API_VERSION = '67.0'

# A class name over 40 characters is rejected by the platform, so the generator refuses rather
# than deploying a name the org will truncate.
APEX_NAME_LIMIT = 40

# --- Salesforce DX layout ----------------------------------------------------------------------

DX_MANIFEST = 'sfdx-project.json'
# Directories that never hold the customer's DX project. A vendored or cached manifest under one of
# them would read as a second candidate and turn an ordinary checkout into a refusal.
DX_SCAN_SKIP = frozenset({'node_modules', '.sfdx', '.sf', 'plurai_sim'})

# --- Agent Script layout -----------------------------------------------------------------------
#
# The DSL is indentation-based, four spaces per step, spaces only. Two blocks are spelled
# `actions:` and they are told apart by indent alone: indent 4 holds the action *definitions*
# (schema and target), indent 8 under `reasoning:` holds the *references* a subagent may call.

DEFINITIONS_INDENT = 4
ACTION_NAME_INDENT = 8
ACTION_PROPERTY_INDENT = 12
IO_ENTRY_INDENT = 16
IO_PROPERTY_INDENT = 20
REFERENCES_INDENT = 8
REFERENCE_INDENT = 12
BINDING_INDENT = 16
VARIABLE_INDENT = 4

_NAME = r'([A-Za-z_][A-Za-z0-9_]*)'
#: A trailing `# comment`, which Agent Script allows after any value.
_COMMENT = r'\s*(?:#.*)?$'
_ACTION_NAME_RE = re.compile(rf'^ {{{ACTION_NAME_INDENT}}}{_NAME}:{_COMMENT}')
_IO_ENTRY_RE = re.compile(rf'^ {{{IO_ENTRY_INDENT}}}"?{_NAME}"?:\s*(.+?)\s*$')
_REFERENCE_RE = re.compile(rf'^ {{{REFERENCE_INDENT}}}{_NAME}:\s*@actions\.{_NAME}{_COMMENT}')
_TARGET_RE = re.compile(rf'^ {{{ACTION_PROPERTY_INDENT}}}target:\s*"([A-Za-z]+)://([^"]+)"{_COMMENT}')
#: Any `@actions.<name>` in the text: what the copy can call, whatever line or syntax carries it.
_ACTION_CALL_RE = re.compile(r'@actions\.([A-Za-z_][A-Za-z0-9_]*)')
#: A reasoning alias, `<alias>: @<namespace>.<name>`, at any indent: `@actions.<alias>` then names it.
_ALIAS_RE = re.compile(rf'^\s+{_NAME}:\s*@([A-Za-z_]+)\.{_NAME}{_COMMENT}')
#: A top-level block that holds its own reasoning and action list (`subagent <name>:`, `start_agent <name>:`).
_SCOPE_RE = re.compile(rf'^[a-z_]+ {_NAME}:{_COMMENT}')
#: An `actions:` block header, a trailing comment allowed.
_ACTIONS_HEADER_RE = re.compile(rf'actions:{_COMMENT}')
#: Any `target:` line naming a `<scheme>://` target, at any indent and in any quoting: each must be
#: one a parsed definition owns. The scheme keeps a variable or prose spelled `target:` out of it.
_ANY_TARGET_RE = re.compile(r'^\s*["\']?target["\']?\s*:\s*["\']?[A-Za-z]+://')
_COMPLEX_TYPE_RE = re.compile(rf'^ {{{IO_PROPERTY_INDENT}}}complex_data_type_name:')

# An action target we can repoint. Every one of these runs an implementation that can be replaced
# by an invocable Apex class; the rest — platform actions, prompt templates, retrievers — execute
# against org services with no seam an outside endpoint can occupy.
REWIRABLE_SCHEMES = frozenset({'apex', 'apexRest', 'api', 'externalService', 'flow'})

class SelfReport(NamedTuple):
    """How the harness observes a real action with no callout seam."""

    lane: str
    """The `observation_lane` the report rides on."""
    evidence: str
    """What in the Agent API reply the report is read from."""
    caveat: str
    """What that evidence leaves unseen, so what a count on the lane means."""
    enabled_by: str
    """The `knowledge:` key that must be `True` for the reply to carry that evidence at all."""
    granularity: str
    """The unit one piece of that evidence resolves to, declared as the entry's `granularity`."""


# The real actions the Agentforce harness (`references/simulate_py_agentforce.py.txt`) observes on
# its own, keyed by target alone. The org's Knowledge search has no callout seam, but with citations
# enabled the Agent API reply carries the articles it cited, and the harness writes one trail entry
# per cited message under the tool name `plurai.config.yaml` gives the entry declaring this lane —
# whatever the bundle calls the action. A search that cites nothing leaves no evidence, so the lane
# is a LOWER BOUND on the calls (`tool_trail_cited`): zero observed is unknown, never "not called".
# A reply's citations do not say which action produced them, so the lane is only self-reported for
# an action that is the ONLY callable one with its target.
SELF_REPORTED_TARGETS: dict[str, SelfReport] = {
    'standardInvocableAction://streamKnowledgeSearch': SelfReport(
        'tool_trail_cited',
        "the reply's citedReferences",
        'a search that cites nothing is not seen, so a count is a lower bound',
        'citations_enabled',
        # A citation's `recordId` is one `Knowledge__kav` version: a whole article, with no passage,
        # chunk or offset anywhere in the reply.
        'article',
    ),
}

class RecordedAct(NamedTuple):
    """An Agent API reply type the harness records as an act on `agent_actions`, never as a tool call."""

    reply_type: str
    """The reply message `type` that carries the act."""
    verb: str
    """The reserved verb the harness files it under (the template's `_ACT_OF_TYPE` value)."""
    utility: str
    """The Agent Script `@utils.<name>` a reasoning alias binds to emit it."""


# What the harness (`references/simulate_py_agentforce.py.txt`, `_ACT_OF_TYPE`) records on
# `agent_actions`. A stdlib copy, because this module runs from the customer's `plurai_sim/` long after
# the plugin is gone; `tests/test_recorded_acts_parity.py` fails when the two part. `--plan` prints it,
# so a scan never has to guess whether a hand-off leaves a record.
RECORDED_ACTS: tuple[RecordedAct, ...] = (RecordedAct('Escalate', 'handoff', 'escalate'),)

# Agent Script scalars with an exact invocable-variable counterpart. Anything else (`object`,
# `list[object]`) names a Flow-side composite that Apex cannot declare, so its value travels as
# JSON text; `--skip-complex-types` leaves those actions real instead.
_APEX_SCALARS = {'string': 'String', 'boolean': 'Boolean', 'number': 'Decimal', 'integer': 'Integer'}

# What a rewired parameter declares once its implementation is the mock. The Apex stand-in holds
# every non-scalar as a String carrying JSON, and the platform validates a published agent against
# its target's real signature, so the declaration has to say the same: an `object` of text.
MOCK_COMPLEX_TYPE = 'object'
MOCK_COMPLEX_TYPE_NAME = 'lightning__textType'

# The identifiers Apex refuses for a field. The set covers the keywords and the system type names
# alike: the compiler answers `Identifier name is reserved: date` exactly as it does for `class`,
# so an invocable variable cannot carry one of these names. The rewrite does NOT leave such an action
# real (that would hand a write to the customer's live org, or deny it): the COPY renames the
# parameter (`apex_field_names`) in its declaration and in every `with`/`@outputs.` use, and the
# generated Apex sends the bundle's original name to the mock, so the mock's contract is unchanged.
APEX_RESERVED = frozenset(
    """abstract activate and any array as asc autonomous begin bigdecimal blob boolean break bulk by byte case cast catch char class collect commit const
    continue convertcurrency currency date datetime decimal default delete desc do double else end enum exception exit export extends false final
    finally float for from future global goto
    group having if implements import in inner insert instanceof int integer interface into join last like limit list long loop map merge new not null nulls
    number object of on or outer override package parallel pragma private protected public retrieve return returning rollback savepoint search select
    set short sobject sort stat static string super switch synchronized system testmethod then this throw time transaction trigger true try type undelete update upsert
    using virtual void webservice when where while""".split()  # noqa: SIM905 — one prose block beats ninety quoted strings
)


class RewireError(Exception):
    """The bundle or the target environment cannot produce a deployable rewired copy."""


# --- consent: an org action runs only when it is known to only read, or on a grant ---------------

MANIFEST = 'bench_manifest.json'
CONFIG = 'plurai.config.yaml'
CONSENT_FILE = '.host_effects_consent'
#: The scan's map, whose `writes: live` tools are the other input the host-effects list is derived from.
MAP = 'artifacts/agent_topology.json'
#: The map's `writes` / `effects` / `runs` vocabularies (stdlib copies of `scripts/host_effects`; test_host_effects.py fails when they part).
WRITES = ('live', 'world', 'none')
EFFECTS = ('shell', 'files', 'network', 'schedule', 'none')
RUNS = ('in_process', 'provider_hosted', 'subprocess')
#: The remedy for a map that does not classify a live tool (stdlib copy of `scripts/host_effects.TYPING_REMEDY`).
TYPING_REMEDY = ('re-run /simulation-scan-agent: its Step 2a types `writes`, `effects` and `runs` on every live tool, and on a '
                 '`rag` entry that is a tool the config runs for real (or lists that tool under `tools`)')

# The left-real targets known to only read: an action every definition of which carries one of these
# is callable without a grant. Keyed by the FULL target, never by scheme or action name: the platform's
# `standardInvocableAction` catalog also sends email and changes records, and a name is the author's
# word. Every entry is a safety claim, so one joins only with evidence of what the target does. The
# scan's typing never adds an entry here: a model reading a description is exactly what this table
# exists not to trust. Separate from `SELF_REPORTED_TARGETS`, which says how a call is SEEN.
READ_ONLY_TARGETS = frozenset({'standardInvocableAction://streamKnowledgeSearch'})
#: Why a left-real action is not callable without a grant: the scan typed it a live writer, or nothing
#: shows it only reads. The second never claims a write it cannot know.
DENIED_TYPED = 'the scan found it writes your org'
DENIED_UNPROVEN = 'runs in your Salesforce org, outside the simulation, and is not known to only read — it could send email or change records there'


def _granted(workdir: Path) -> frozenset[str]:
    """What `.host_effects_consent` grants: `granted: a, b` names them; anything else grants nothing."""
    try:
        text = (workdir / CONSENT_FILE).read_text(encoding='utf-8').strip()
    except (OSError, UnicodeDecodeError):
        return frozenset()
    head, sep, names = text.partition(':')
    return frozenset(n.strip() for n in names.split(',') if n.strip()) if sep and head.strip() == 'granted' else frozenset()


def map_writes_sha256(topology: object) -> str:
    """The sha256 of the map's typed facts, `(name, status, writes, effects, runs)` per tool that types
    any: the other input the host-effects list is derived from. ONE function for both sides, like the one below."""
    tools = topology.get('tools') if isinstance(topology, dict) else None
    facts = sorted([str(t.get('name')), str(t.get('status')), str(t.get('writes')), json.dumps(t.get('effects')), str(t.get('runs'))]
                   for t in (tools if isinstance(tools, list) else []) if isinstance(t, dict) and {'writes', 'effects', 'runs'} & set(t))
    return hashlib.sha256(json.dumps(facts).encode('utf-8')).hexdigest()


def _tools_block_lines(config_text: str) -> list[str]:
    """The config's top-level `tools:` block, its key line first, up to the next column-0 key."""
    block: list[str] = []
    for line in config_text.splitlines():
        if not block:
            if re.match(r'tools\s*:', line):
                block.append(line.rstrip())
            continue
        if line.strip() and not line[0].isspace() and not line.startswith('#'):
            break
        block.append(line.rstrip())
    while block and (not block[-1] or block[-1].startswith('#')):
        block.pop()
    return block


_TOOL_KEY_RE = re.compile(r"""^\s*["']?([A-Za-z_][\w.-]*)["']?\s*:(.*)$""")
#: The keys `up` checks on a tool ENTRY (beside `mode:`, never inside `descriptor:`).
ENTRY_KEYS = ('observation_lane', 'granularity')


def _top_level_flow(inner: str) -> str:
    """A flow mapping's body with every nested `{...}` / `[...]` blanked to spaces, same length.

    Positions survive, so a match on the result indexes the original; a key inside a nested mapping
    (`descriptor: {observation_lane: ...}`) is not the entry's own.
    """
    out, depth = [], 0
    for ch in inner:
        if ch in '{[':
            depth += 1
        out.append(ch if depth == 0 else ' ')
        if ch in '}]' and depth:
            depth -= 1
    return ''.join(out)


def declared_entry_values(config_text: str, key: str) -> dict[str, str]:
    """`tools.<name>.<key>` for every tool entry that declares it, read from the text.

    Stdlib only, like the hash above: a tool's line sits one indent under `tools:`, and its value is
    the `<key>:` at the tool's own first indent below it, or inside a flow mapping on the tool's
    line. The same key nested deeper (inside `descriptor:`) is not the entry's. A list value, flow
    (`[a, b]`) or block (`- a` lines below the key), reads back in the one spelling `scope_list` writes.
    """
    value_re = re.compile(rf"""\b{key}\s*:\s*(?:(\[[^\[\]{{}}]*\])|["']?([A-Za-z_]+))""")

    def _value(match: re.Match[str]) -> str:
        items = match.group(1)
        return match.group(2) if items is None else scope_list(item.strip().strip('"\'') for item in items[1:-1].split(',') if item.strip())

    lanes: dict[str, str] = {}
    tool_indent: int | None = None
    tool: str | None = None
    child_indent: int | None = None
    block = _tools_block_lines(config_text)[1:]
    for n, line in enumerate(block):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        indent = len(line) - len(line.lstrip())
        if tool_indent is None:
            tool_indent = indent
        if indent == tool_indent:
            match = _TOOL_KEY_RE.match(line)
            tool, child_indent = (match.group(1), None) if match else (None, None)
            flow = match.group(2).split('#', 1)[0].strip() if match else ''
            if flow.startswith('{') and (key_at := re.search(rf'\b{key}\s*:', _top_level_flow(flow[1:-1]))):
                if inline := value_re.match(flow[1:-1], key_at.start()):
                    lanes[tool] = _value(inline)
            continue
        if tool is None or indent < tool_indent:
            continue
        if child_indent is None:
            child_indent = indent
        if indent == child_indent and (lane := value_re.match(line.strip())):
            lanes[tool] = _value(lane)
        elif indent == child_indent and re.match(rf'{key}\s*:\s*(#.*)?$', line.strip()):
            value_lines = [block[j].strip() for j in _block_value_lines(block, n, len(block), indent)]
            items = [text[1:].split(' #', 1)[0].strip().strip('"\'') for text in value_lines if text.startswith('- ')]
            if items and len(items) == len(value_lines) and all(items):
                lanes[tool] = scope_list(items)
    return lanes


def _block_value_lines(lines: list[str], at: int, stop: int, indent: int) -> list[int]:
    """The lines below the key at `lines[at]` that carry its block value: `- item` lines at the key's
    indent or deeper, and anything indented deeper than the key. Blank and comment lines are skipped."""
    value: list[int] = []
    for j in range(at + 1, stop):
        text = lines[j].strip()
        if not text or text.startswith('#'):
            continue
        if _indent(lines[j]) > indent or (_indent(lines[j]) == indent and text.startswith('- ')):
            value.append(j)
        else:
            break
    return value


def scope_list(scopes: Iterable[str]) -> str:
    """The `exposed_in` value as the config spells it: the scope names, sorted, as a flow list."""
    return f'[{", ".join(sorted(scopes))}]'



def declared_lanes(config_text: str) -> dict[str, str]:
    """`tools.<name>.observation_lane` for every tool entry that declares one."""
    return declared_entry_values(config_text, 'observation_lane')


def check_trail_lanes(plan: RewirePlan, workdir: Path) -> None:
    """Refuse a config whose self-report entries are not the ones this plan prints.

    The harness files the Knowledge search's citations under the one config entry on that lane, so
    an entry missing it empties the trail with nothing raised, and one that claims it wrongly
    credits the citations to an action that did not run. The entry's `granularity` is the unit a
    citation resolves to, and the evals read it: missing, recall and precision read as
    passage-level scores the evidence never carried. Every entry the bundle scopes must also say where
    it is exposed (`exposed_in`): without it scenario generation cannot tell which flows run with the
    Knowledge search at hand, and types only the flows that name it.
    """
    config = workdir / CONFIG
    try:
        text = config.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as error:
        raise RewireError(f'{config} cannot be read, so the Knowledge-search lane cannot be checked; run the scan to completion first') from error
    # Every real action with an entry is checked, `none` included: an entry with no lane reads to eval
    # generation as ordinarily observable, so a call-presence judge on it fails on evidence no run has.
    real = {name for name, _ in (*plan.left_real, *plan.denied)} & set(_entry_names(text))
    self_lanes = {report.lane for report in SELF_REPORTED_TARGETS.values()}
    printed = {
        'observation_lane': (plan.observation_lane_by_name, lambda value: value in self_lanes, real),
        'granularity': (plan.granularity_by_name, lambda value: True, set()),
    }
    for key in ENTRY_KEYS:
        wanted, claimed, required = printed[key]
        declared = declared_entry_values(text, key)
        for name in sorted({name for name, _ in plan.self_reported} | required | {name for name, value in declared.items() if claimed(value)}):
            want, have = wanted.get(name), declared.get(name)
            if name not in plan.observation_lane_by_name:
                raise RewireError(f'{config}: tools.{name} declares {key}: {have}, but the bundle has no action {name}; remove that line')
            if want is None:
                raise RewireError(f'{config}: tools.{name} declares {key}: {have}, but the plan prints no {key} for it; remove that line')
            if have != want:
                raise RewireError(
                    f'{config}: tools.{name} declares {key}: {have or "(nothing)"}, but the plan prints '
                    f'{key}: {want} for it; write `{key}: {want}` on the tools.{name} entry, beside `mode:` '
                    f'(not inside `descriptor:`), exactly as `rewire_agentforce.py --plan` prints it — '
                    f'or run `rewire_agentforce.py --write-lanes`, which writes every lane the plan prints'
                )
    scoped = {name: scope_list(scopes) for name, scopes in plan.exposed_in}
    declared_scopes = declared_entry_values(text, 'exposed_in')
    for name in sorted((set(scoped) & set(_entry_names(text))) | set(declared_scopes)):
        want, have = scoped.get(name), declared_scopes.get(name)
        if have != want:
            raise RewireError(
                f'{config}: tools.{name} declares exposed_in: {have or "(nothing)"}, but the bundle exposes it in '
                f'{want or "no subagent"}; run `rewire_agentforce.py --write-lanes`, which writes each action\'s '
                f'exposed_in beside `mode:`' + ('' if want else ' (or remove that line)')
            )


#: The harness template's module constant naming the lane it files citations under, and the constant
#: an older template hardcoded the Knowledge tool's name in instead of reading it from the config.
HARNESS_LANE_CONSTANT = '_TRAIL_LANE'
STALE_HARNESS_CONSTANT = '_KNOWLEDGE_TOOL_NAME'


def check_harness(plan: RewirePlan, workdir: Path) -> None:
    """Refuse a `simulate.py` that would file the Knowledge search's citations on another footing than the plan.

    A harness that names the tool itself keeps writing trail entries under that name whatever the
    config says, so a migrated config does not stop them; one whose lane constant is not the plan's
    reads no config entry and reports nothing. Read with `ast`, from the module's own assignments.
    """
    harness = workdir / 'simulate.py'
    if not harness.is_file():
        return
    try:
        tree = ast.parse(harness.read_text(encoding='utf-8'), filename=str(harness))
    except (OSError, UnicodeDecodeError, SyntaxError) as error:
        raise RewireError(f'{harness} cannot be read as Python ({error}); regenerate it from the template (simulation-setup-agentforce Step A5)') from error
    constants: dict[str, object] = {}
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        for target in targets:
            if isinstance(target, ast.Name) and isinstance(getattr(node, 'value', None), ast.Constant):
                constants[target.id] = node.value.value
    regenerate = 'regenerate it from the template (simulation-setup-agentforce Step A5)'
    if STALE_HARNESS_CONSTANT in constants:
        raise RewireError(
            f'{harness} names the Knowledge tool itself ({STALE_HARNESS_CONSTANT} = {constants[STALE_HARNESS_CONSTANT]!r}) instead of '
            f'reading it from the config, so it reports calls under that name whatever the config declares; {regenerate}'
        )
    lanes = sorted({report.lane for _, report in plan.self_reported})
    if lanes and constants.get(HARNESS_LANE_CONSTANT) not in lanes:
        raise RewireError(
            f'{harness} files citations on {HARNESS_LANE_CONSTANT} = {constants.get(HARNESS_LANE_CONSTANT)!r}, but the plan self-reports '
            f'{", ".join(lanes)}; {regenerate}'
        )


_FLOW_VALUE_RE = r"""\s*:\s*(?:\[[^\[\]{}]*\]|["']?[A-Za-z_]+["']?)"""


def _own_flow_key(inner: str, key: str) -> re.Match[str] | None:
    """`key` and its value in a flow mapping's body, found at the mapping's own level (never nested)."""
    top = re.search(rf',?\s*\b{key}\s*:', _top_level_flow(inner))
    return re.compile(rf',?\s*\b{key}{_FLOW_VALUE_RE}').match(inner, top.start()) if top else None


def _splice_flow(rest: str, values: dict[str, str | None]) -> str:
    """One flow-style entry's `{...}` with each of its OWN keys set to its value, or removed for `None`."""
    inner = rest.strip()[1:-1]
    for key, value in values.items():
        present = _own_flow_key(inner, key)
        if value is None:
            if present:
                inner = (inner[: present.start()] + inner[present.end():]).strip()
                inner = inner[1:].lstrip() if inner.startswith(',') else inner
        elif present:
            lead = re.match(r',?\s*', inner[present.start():]).group(0)
            inner = inner[: present.start()] + lead + f'{key}: {value}' + inner[present.end():]
        else:
            inner = f'{inner.rstrip()}, {key}: {value}' if inner.strip() else f'{key}: {value}'
    return ' {' + inner.strip() + '}'


def splice_entry_values(config_text: str, values: dict[str, dict[str, str | None]]) -> str:
    """`config_text` with each named tool entry's keys set as TEXT; `None` removes the key.

    Nothing else moves: other keys, comments, quoting and order stay byte for byte. A block entry
    gets a missing key after its `mode:` line (or first); a flow entry `{...}` gets it at the end.
    """
    lines = config_text.splitlines(keepends=True)
    newline = '\r\n' if '\r\n' in config_text else '\n'   # an inserted line ends the way the file's do
    start = next((i for i, line in enumerate(lines) if re.match(r'tools\s*:', line)), None)
    if start is None or lines[start].split(':', 1)[1].split('#', 1)[0].strip():
        raise RewireError(f'{CONFIG} has no block-style top-level `tools:` mapping to write the lanes into')
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() and not lines[i][0].isspace() and not lines[i].startswith('#')), len(lines))
    body = [i for i in range(start + 1, end) if lines[i].strip() and not lines[i].lstrip().startswith('#')]
    tool_indent = _indent(lines[body[0]]) if body else 2
    entries: dict[str, tuple[int, int]] = {}
    heads = [i for i in body if _indent(lines[i]) == tool_indent]
    for n, head in enumerate(heads):
        match = _TOOL_KEY_RE.match(lines[head].rstrip('\r\n'))
        if match:
            entries[match.group(1)] = (head, heads[n + 1] if n + 1 < len(heads) else end)
    if missing := sorted(set(values) - set(entries)):
        raise RewireError(f'{CONFIG} has no tools entry for {", ".join(missing)}; the scan writes each action\'s entry and descriptor (simulation-setup-agentforce Step A2)')
    edits: dict[int, list[str]] = {}
    for name, keys in values.items():
        head, stop = entries[name]
        rest = _TOOL_KEY_RE.match(lines[head].rstrip('\r\n')).group(2)
        if rest.split('#', 1)[0].strip().startswith('{'):
            code, _, comment = rest.partition('#')
            edits[head] = [lines[head].split(':', 1)[0] + ':' + _splice_flow(code, keys) + (f'  #{comment.rstrip()}' if comment else '') + newline]
            continue
        if rest.split('#', 1)[0].strip():
            raise RewireError(f'{CONFIG}: tools.{name} is not a mapping this can write into; write its keys as a block or a `{{...}}` mapping')
        children = [i for i in range(head + 1, stop) if lines[i].strip() and not lines[i].lstrip().startswith('#')]
        child_indent = _indent(lines[children[0]]) if children else tool_indent + 2
        at = {key: i for i in children for key in keys if _indent(lines[i]) == child_indent and re.match(rf'{key}\s*:', lines[i].strip())}
        anchor = next((i for i in children if _indent(lines[i]) == child_indent and re.match(r'mode\s*:', lines[i].strip())), head)
        for key, value in keys.items():
            if key in at:
                comment = lines[at[key]].rstrip('\r\n').partition(' #')[2]
                edits[at[key]] = [] if value is None else [f'{" " * child_indent}{key}: {value}' + (f'  #{comment}' if comment else '') + newline]
                # A block value (`- item` lines below the key) goes with the key it belonged to.
                edits.update(dict.fromkeys(_block_value_lines(lines, at[key], stop, child_indent), []))
            elif value is not None:
                edits.setdefault(anchor, [lines[anchor] if lines[anchor].endswith('\n') else lines[anchor] + newline]).append(f'{" " * child_indent}{key}: {value}{newline}')
    return ''.join(''.join(edits[i]) if i in edits else line for i, line in enumerate(lines))


def write_lanes(plan: RewirePlan, workdir: Path) -> tuple[list[str], bool]:
    """Write the lanes (and granularities) the plan prints, and the subagents each action is exposed in,
    onto the workdir config's tool entries.

    A deterministic function of the bundle, so a workspace scanned before the lanes existed migrates
    in one command. Idempotent. The bench manifest's hash of the `tools:` block is re-stamped only
    when it was current before the write: only lane and scope keys moved, so the host-effect list derived from
    that block is unchanged; a manifest already stale stays stale and still names its own remedy.
    """
    config = workdir / CONFIG
    try:
        # Bytes, not text: text mode would translate a CRLF file's line endings on the way in and out.
        before = config.read_bytes().decode('utf-8')
    except (OSError, UnicodeDecodeError) as error:
        raise RewireError(f'{config} cannot be read; run the scan to completion first') from error
    present = set(_entry_names(before))
    granularity = plan.granularity_by_name
    exposed_in = dict(plan.exposed_in)
    values = {
        name: {
            'observation_lane': lane,
            'granularity': granularity.get(name),
            'exposed_in': scope_list(exposed_in[name]) if exposed_in.get(name) else None,
        }
        for name, lane in plan.observation_lane_by_name.items()
        if name in present or name in granularity
    }
    after = splice_entry_values(before, values)
    wrote = [f'tools.{name}: observation_lane: {keys["observation_lane"]}' + ''.join(f', {key}: {keys[key]}' for key in ('granularity', 'exposed_in') if keys[key])
             for name, keys in sorted(values.items())]
    if after == before:
        return wrote, False
    manifest = workdir / MANIFEST
    old_hash, new_hash = tools_block_sha256(before), tools_block_sha256(after)
    try:
        manifest_text = manifest.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        manifest_text = None
    config.write_bytes(after.encode('utf-8'))
    if manifest_text is not None and f'"{old_hash}"' in manifest_text and old_hash != new_hash:
        manifest.write_text(manifest_text.replace(f'"{old_hash}"', f'"{new_hash}"'), encoding='utf-8')
    check_trail_lanes(plan, workdir)
    return wrote, True


def _entry_names(config_text: str) -> list[str]:
    """Every tool entry name under the top-level `tools:` block."""
    block = _tools_block_lines(config_text)[1:]
    content = [line for line in block if line.strip() and not line.lstrip().startswith('#')]
    if not content:
        return []
    indent = _indent(content[0])
    return [m.group(1) for line in content if _indent(line) == indent and (m := _TOOL_KEY_RE.match(line))]


def unclassified_live_tools(topology: object) -> list[str]:
    """The map's live tools without a valid `writes` + `effects` + `runs` (stdlib copy of the rule in
    `scripts/host_effects.unclassified_tools`, map half): none of them may run by default."""
    tools = topology.get('tools') if isinstance(topology, dict) else None
    out = []
    for t in tools if isinstance(tools, list) else []:
        if not isinstance(t, dict) or not t.get('name') or t.get('status') == 'mocked':
            continue
        effects = t.get('effects')
        if not (t.get('writes') in WRITES and t.get('runs') in RUNS and isinstance(effects, list) and effects
                and all(e in EFFECTS for e in effects) and ('none' not in effects or effects == ['none'])):
            out.append(str(t['name']))
    return sorted(out)


def tools_block_sha256(config_text: str) -> str:
    """The sha256 of the config's top-level `tools:` block as text: the one input the live-write list
    is derived from. The ONE function for both sides (`bench_manifest.py init` records it, `up` checks
    it), stdlib-only because this module runs from the customer's tree under a bare `python3`.

    A block ends at the next column-0 key; a column-0 comment ends it only when no indented line
    follows, so a comment inside the block cannot hide the tools after it. Line endings do not count.
    A shape that would put a live-write input outside that text (a byte-order mark, a quoted or a
    second `tools` key, an alias to an anchor defined outside the block) is refused, never half-hashed.
    """
    unreadable = f'{CONFIG} cannot be checked as written; write the `tools:` block as one unquoted top-level key, with no byte-order mark and no alias to an anchor outside it'
    keys = [line for line in config_text.splitlines() if re.match(r"""["']?tools["']?\s*:""", line)]
    if config_text.startswith('\ufeff') or len(keys) > 1 or any(not key.startswith('tools') for key in keys):
        raise RewireError(unreadable)
    text = '\n'.join(_tools_block_lines(config_text))
    # An alias is a whole node (`key: *a`, `- *a`, `<<: *a`); `*` inside prose is not one.
    aliases = set(re.findall(r'(?:^|[\s,\[{])\*([\w.-]+)[ \t]*(?=$|[,\]}#\n])', text, re.MULTILINE))
    if aliases - set(re.findall(r'(?:^|[\s,\[{])&([\w.-]+)', text, re.MULTILINE)):
        raise RewireError(unreadable)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def declared_host_effects(workdir: Path) -> frozenset[str]:
    """The scan's host effects (`surfaces.host_effects` in the workdir's bench manifest), before any grant.

    They only ADD denials (`rewire_bundle`'s `typed_live`): a left-real action the scan left off this
    list is still denied unless its target is known to only read, or the operator granted it."""
    manifest = workdir / MANIFEST
    try:
        declared = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise RewireError(f'{manifest} is missing or unreadable, so nobody knows which actions write the live org; run the scan to completion first') from error
    surfaces = (declared.get('surfaces') or {}) if isinstance(declared, dict) else {}
    effects = surfaces.get('host_effects')
    if not isinstance(effects, list):
        raise RewireError(f'{manifest} lists no surfaces.host_effects; run the scan to completion first')
    # The list is only as current as the `tools:` block it was derived from: a stale one would deny nothing.
    try:
        current = tools_block_sha256((workdir / CONFIG).read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError) as error:
        raise RewireError(f'{workdir} has no plurai.config.yaml it can read, so nobody knows which actions write the live org; run the scan to completion first') from error
    if surfaces.get('host_effects_tools_sha256') != current:
        raise RewireError(f'{manifest} lists host effects derived from a different `tools:` block than {workdir / CONFIG} holds; refresh it with `bench_manifest.py init`')
    try:
        topology = json.loads((workdir / MAP).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise RewireError(f'{workdir / MAP} is missing or unreadable, so nobody knows which actions the map types `writes: live`; run the scan to completion first') from error
    tools = topology.get('tools') if isinstance(topology, dict) else None
    if untyped := sorted(str(t.get('name')) for t in (tools if isinstance(tools, list) else []) if isinstance(t, dict) and 'writes' in t and t['writes'] not in WRITES):
        raise RewireError(f'{workdir / MAP} types `writes` outside {", ".join(WRITES)} on {untyped}, so nobody knows whether they write the live org; fix the map and refresh with `bench_manifest.py init`')
    if unclassified := unclassified_live_tools(topology):
        raise RewireError(f'{workdir / MAP} does not classify {unclassified}, so nobody knows what they do outside the simulation; {TYPING_REMEDY}')
    if surfaces.get('host_effects_map_sha256') != map_writes_sha256(topology):
        raise RewireError(f'{manifest} lists host effects derived from different typed facts than {workdir / MAP} holds; refresh it with `bench_manifest.py init`')
    return frozenset(str(name) for name in effects)


# --- the parsed bundle -------------------------------------------------------------------------


def apex_field_names(entries: Iterable[str]) -> dict[str, str]:
    """The Apex field (and, in the copy, the Agent Script parameter) name for each declared name.

    A name Apex reserves (case-insensitively) becomes `<name>Value`, then `<name>Value2`, ... until it
    collides with neither another name in the same list nor a reserved word. Every other name is kept.
    """
    names = list(entries)
    taken = {n.lower() for n in names} | {SESSION_VARIABLE.lower()}
    out: dict[str, str] = {}
    for name in names:
        if name.lower() not in APEX_RESERVED:
            out[name] = name
            continue
        candidate, n = f'{name}Value', 1
        while candidate.lower() in taken or candidate.lower() in APEX_RESERVED:
            n += 1
            candidate = f'{name}Value{n}'
        taken.add(candidate.lower())
        out[name] = candidate
    return out


@dataclass(frozen=True)
class IoEntry:
    """One input or output of an action definition, with the line span it occupies."""

    name: str
    declared_type: str
    start: int
    end: int
    complex: bool

    @property
    def apex_type(self) -> str:
        return _APEX_SCALARS.get(self.declared_type, 'String')

    @property
    def json_text(self) -> bool:
        """True when Apex has no counterpart for the declared type, so the value travels as JSON text."""
        return self.declared_type not in _APEX_SCALARS


@dataclass
class ActionDefinition:
    """An action definition: its schema, its target, and the lines the rewrite touches."""

    name: str
    scheme: str | None = None
    target: str | None = None
    """The whole `target:` value, `<scheme>://<path>`; None when the definition has no parsable one."""
    target_line: int | None = None
    source_line: int | None = None
    inputs: list[IoEntry] = field(default_factory=list)
    outputs: list[IoEntry] = field(default_factory=list)
    inputs_end: int | None = None
    inputs_missing_before: int | None = None
    confirmation_line: int | None = None
    """The line holding `require_user_confirmation:`, None when the definition omits it."""
    requires_confirmation: bool = False
    """`require_user_confirmation: True`: the platform asks the user to confirm before each call."""

    @property
    def rewirable(self) -> bool:
        return self.scheme in REWIRABLE_SCHEMES

    @property
    def complex_typed(self) -> bool:
        return any(entry.complex for entry in (*self.inputs, *self.outputs))

    @property
    def apex_class(self) -> str:
        return APEX_PREFIX + self.name

    @property
    def input_fields(self) -> dict[str, str]:
        """Declared input name -> the name the copy and its Apex class use (differs only for an Apex-reserved name)."""
        return apex_field_names(e.name for e in self.inputs)

    @property
    def output_fields(self) -> dict[str, str]:
        return apex_field_names(e.name for e in self.outputs)

    @property
    def renamed(self) -> tuple[tuple[str, str, str], ...]:
        """(`in`|`out`, declared name, the copy's name) for each parameter the copy has to rename for Apex."""
        return tuple((d, old, new) for d, fields in (('in', self.input_fields), ('out', self.output_fields)) for old, new in fields.items() if old != new)


@dataclass
class Bundle:
    """The parsed `.agent` file: line spans the rewrite edits, keyed by what they mean."""

    lines: list[str]
    developer_name: str
    developer_name_line: int
    label_line: int | None
    variables_end: int
    definitions: list[ActionDefinition]
    """Every action definition in file order. One name has one definition per subagent that calls it."""
    references: list[tuple[str, int]]
    """(action name, the line that opens the reference) — one per call site, an action may have several."""
    agent_user: str = ''
    """`access.default_agent_user`: the org user the agent's own actions run as, empty when the bundle omits it."""
    exposed_in: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Action name -> the top-level blocks whose reasoning references it, in file order: where the model can call it."""

    def by_name(self) -> dict[str, list[ActionDefinition]]:
        grouped: dict[str, list[ActionDefinition]] = {}
        for definition in self.definitions:
            grouped.setdefault(definition.name, []).append(definition)
        return grouped


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(' '))


def _block_end(lines: list[str], start: int, indent: int) -> int:
    """The line after the last line of the block opened at `start`, whose body is indented past `indent`.

    Trailing blank lines fall outside the block, so an insertion at this point lands under the last
    real line rather than after the gap that separates the block from the next one.
    """
    end = start + 1
    for i in range(start + 1, len(lines)):
        if not lines[i].strip():
            continue
        if _indent(lines[i]) <= indent:
            break
        end = i + 1
    return end


def _quoted_value(line: str) -> str:
    return line.split(':', 1)[1].strip().strip('"')


def _parse_io_block(lines: list[str], start: int) -> tuple[list[IoEntry], int]:
    """Read one `inputs:`/`outputs:` block; return its entries and the line after the block."""
    end = _block_end(lines, start, ACTION_PROPERTY_INDENT)
    entries: list[IoEntry] = []
    for i in range(start + 1, end):
        match = _IO_ENTRY_RE.match(lines[i])
        if match is None:
            continue
        entry_end = _block_end(lines, i, IO_ENTRY_INDENT)
        declared = match.group(2).split('=')[0].strip()
        complex_typed = any(_COMPLEX_TYPE_RE.match(lines[j]) for j in range(i + 1, entry_end))
        entries.append(IoEntry(name=match.group(1), declared_type=declared, start=i, end=entry_end, complex=complex_typed))
    return entries, end


def _parse_action(lines: list[str], name: str, start: int, end: int) -> ActionDefinition:
    action = ActionDefinition(name=name)
    first_property: int | None = None
    i = start + 1
    while i < end:
        line = lines[i]
        if _indent(line) != ACTION_PROPERTY_INDENT or not line.strip():
            i += 1
            continue
        if first_property is None:
            first_property = i
        key = line.strip().split(':', 1)[0]
        if key == 'inputs':
            action.inputs, i = _parse_io_block(lines, i)
            action.inputs_end = i
            continue
        if key == 'outputs':
            action.outputs, i = _parse_io_block(lines, i)
            continue
        if key == 'target':
            action.target_line = i
            match = _TARGET_RE.match(line)
            action.scheme = match.group(1) if match else None
            action.target = f'{match.group(1)}://{match.group(2)}' if match else None
        elif key == 'source':
            action.source_line = i
        elif key == 'require_user_confirmation':
            action.confirmation_line = i
            action.requires_confirmation = line.split(':', 1)[1].strip() == 'True'
        i += 1
    # An action with no `inputs:` block still needs one, opened where its first property sits.
    action.inputs_missing_before = None if action.inputs_end is not None else first_property
    return action


def _header_values(lines: list[str]) -> tuple[str, int, int | None, str]:
    """The header entries the rewrite reads: the developer name and the line holding it, the line
    holding the label, and the agent user. All four sit at the same indent under different sections."""
    developer_name = ''
    developer_name_line = -1
    label_line: int | None = None
    agent_user = ''
    for i, line in enumerate(lines):
        if _indent(line) != VARIABLE_INDENT:
            continue
        key = line.strip().split(':', 1)[0]
        if key == 'developer_name':
            developer_name, developer_name_line = _quoted_value(line), i
        elif key == 'agent_label':
            label_line = i
        elif key == 'default_agent_user':
            agent_user = _quoted_value(line)
    return developer_name, developer_name_line, label_line, agent_user


def parse_bundle(text: str) -> Bundle:
    """Read the Agent Script file into the line spans the rewrite needs. Nothing is re-serialized."""
    lines = text.splitlines()
    developer_name, developer_name_line, label_line, agent_user = _header_values(lines)
    variables_end = -1
    definitions: list[ActionDefinition] = []
    references: list[tuple[str, int]] = []
    exposed_in: dict[str, list[str]] = {}
    scope: str | None = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        indent = _indent(line)
        if indent == 0 and stripped and not stripped.startswith('#'):
            scope = scope_match.group(1) if (scope_match := _SCOPE_RE.match(line)) else None
        if indent == 0 and stripped == 'variables:':
            variables_end = _block_end(lines, i, 0)
        elif indent == DEFINITIONS_INDENT and _ACTIONS_HEADER_RE.match(stripped):
            block_end = _block_end(lines, i, DEFINITIONS_INDENT)
            for j in range(i + 1, block_end):
                name_match = _ACTION_NAME_RE.match(lines[j])
                if name_match:
                    # Every subagent that calls an action defines it again in full, so one name has
                    # several definitions and the rewrite has to reach all of them.
                    definitions.append(_parse_action(lines, name_match.group(1), j, _block_end(lines, j, ACTION_NAME_INDENT)))
        elif indent == REFERENCES_INDENT and _ACTIONS_HEADER_RE.match(stripped):
            block_end = _block_end(lines, i, REFERENCES_INDENT)
            for j in range(i + 1, block_end):
                match = _REFERENCE_RE.match(lines[j])
                if match:
                    references.append((match.group(2), j))
                    if scope is not None and scope not in exposed_in.setdefault(match.group(2), []):
                        exposed_in[match.group(2)].append(scope)

    if developer_name_line < 0:
        raise RewireError('the bundle has no config.developer_name; it is not an Agent Script file')
    if variables_end < 0:
        raise RewireError('the bundle has no top-level variables: block to carry the mock session id')
    return Bundle(
        lines=lines,
        developer_name=developer_name,
        developer_name_line=developer_name_line,
        label_line=label_line,
        variables_end=variables_end,
        definitions=definitions,
        references=references,
        agent_user=agent_user,
        exposed_in={name: tuple(scopes) for name, scopes in exposed_in.items()},
    )


#: The `knowledge:` keys that name the knowledge SOURCE an action grounds on. The block's other keys
#: (`citations_url`, `citations_enabled`) shape citations and bind no source, so an empty one of
#: them is not a finding.
_KNOWLEDGE_SOURCE_KEYS = ('rag_feature_config_id',)
_KNOWLEDGE_BINDING_RE = re.compile(r'=\s*@knowledge\.(\w+)\s*$')


def knowledge_values(bundle: Bundle) -> dict[str, tuple[str, int]]:
    """Each key the top-level `knowledge:` block sets: its raw value and its 1-based line."""
    lines = bundle.lines
    values: dict[str, tuple[str, int]] = {}
    for i, line in enumerate(lines):
        if _indent(line) == 0 and line.strip() == 'knowledge:':
            for j in range(i + 1, _block_end(lines, i, 0)):
                if _indent(lines[j]) == VARIABLE_INDENT and ':' in lines[j]:
                    key, raw = lines[j].strip().split(':', 1)
                    values[key] = (raw.strip(), j + 1)
            break
    return values


def knowledge_source_lines(bundle: Bundle) -> tuple[str, ...]:
    """The bundle's knowledge-source binding, read deterministically off the file: each source key the
    top-level `knowledge:` block sets, its value, and every action input bound to it (1-based lines).

    Printed by `--plan`, which the scan copies: an EMPTY source bound into an action is the
    `tool_surface` / `source_unbound` fact (`simulation-setup/references/agent_facts.md`), cited at the
    binding line, with the `empty_means` the platform documents for an empty value. `()` when the bundle has no `knowledge:` block or binds none of its source keys.
    """
    lines = bundle.lines
    values = knowledge_values(bundle)
    bound: dict[str, dict[str, list[int]]] = {}
    for action in bundle.definitions:
        for entry in action.inputs:
            match = _KNOWLEDGE_BINDING_RE.search(lines[entry.start])
            if match:
                bound.setdefault(match.group(1), {}).setdefault(f'{action.name}.{entry.name}', []).append(entry.start + 1)
    report = []
    for key in _KNOWLEDGE_SOURCE_KEYS:
        if key not in values or key not in bound:
            continue
        raw, line_no = values[key]
        state = 'is empty' if raw.strip('"\'') == '' else f'= {raw}'
        targets = '; '.join(f'{target} (lines {", ".join(map(str, at))})' for target, at in bound[key].items())
        report.append(f'knowledge source: {key} {state} (line {line_no}); bound into {targets}')
    return tuple(report)


#: Where `export_agentforce_knowledge.py` writes the org's published articles, under the workdir, and
#: its index BESIDE that directory rather than in it: the directory is a corpus root, and every file in
#: it is hashed by the scan contract and ingested as a document.
KNOWLEDGE_SNAPSHOT_DIR = 'knowledge_snapshot'
KNOWLEDGE_SNAPSHOT_INDEX = 'knowledge_snapshot.INDEX.json'


def knowledge_snapshot_lines(workdir: Path) -> tuple[str, ...]:
    """What the exported knowledge snapshot holds, read off its index: the resolved source and the article count."""
    index_path = workdir / KNOWLEDGE_SNAPSHOT_INDEX
    try:
        index = json.loads(index_path.read_text(encoding='utf-8'))
        articles, source = index['articles'], index['source']
        folder = f'{workdir.name}/{KNOWLEDGE_SNAPSHOT_DIR}'
    except FileNotFoundError:
        return ('knowledge snapshot: not exported — the org\'s published articles are not in the corpus (export_agentforce_knowledge.py)',)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return (f'knowledge snapshot: {index_path.name} is unreadable ({type(error).__name__}); re-run export_agentforce_knowledge.py',)
    return (f'knowledge snapshot: {len(articles)} article(s) from {source.get("described", "an unrecorded source")} -> {folder}',)


# --- the rewrite -------------------------------------------------------------------------------


@dataclass(frozen=True)
class RewirePlan:
    """What the rewrite did, in the words the recipes and the tests report."""

    copy_developer_name: str
    rewired: tuple[ActionDefinition, ...]
    left_real: tuple[tuple[str, str], ...]
    retyped: tuple[tuple[str, str, str, str], ...]
    """(action, io, `in`|`out`, its real `declared/complex_data_type_name`) for each io the copy declares as text instead."""
    renamed: tuple[tuple[str, str, str, str], ...] = ()
    """(action, `in`|`out`, declared name, the copy's name) for each parameter renamed because Apex reserves its name."""
    denied: tuple[tuple[str, str], ...] = ()
    """(action, `DENIED_TYPED` or `DENIED_UNPROVEN`) for each action left real that the copy cannot call."""
    granted_live: tuple[tuple[str, str], ...] = ()
    """(action, its target) for each `left_real` action that runs for real in the org only because the operator granted it."""
    real_targets: tuple[tuple[str, str], ...] = ()
    """(action, its target) for each action the copy leaves real or denies: what runs in the org under that name."""
    knowledge_sources: tuple[str, ...] = ()
    """One line per knowledge SOURCE binding the bundle's `knowledge:` block sets (see `knowledge_source_lines`)."""
    confirmation_gated: tuple[tuple[str, tuple[int, ...]], ...] = ()
    """(action, the 1-based lines declaring it) for each name EVERY definition of which says `require_user_confirmation: True`."""
    confirmation_disagreements: tuple[tuple[str, tuple[int, ...]], ...] = ()
    """(action, the 1-based lines saying True) for each name only SOME definitions of which say it: not a declaration of the name."""
    self_reported: tuple[tuple[str, SelfReport], ...] = ()
    """(action, how) for each `left_real` action the harness observes on its own (see `SELF_REPORTED_TARGETS`)."""
    unobserved: tuple[tuple[str, str], ...] = ()
    """(action, why) for each `left_real` action with a self-reported target the harness cannot observe."""
    exposed_in: tuple[tuple[str, tuple[str, ...]], ...] = ()
    """(action, the subagents whose reasoning references it) for every referenced action, as the source bundle declares it."""
    act_aliases: tuple[tuple[str, tuple[str, ...]], ...] = ()
    """(reply type, the bundle's reasoning aliases bound to its `@utils.<name>`) for each of `RECORDED_ACTS`."""

    @property
    def apex_classes(self) -> tuple[str, ...]:
        return (CALLOUT_CLASS, *(action.apex_class for action in self.rewired))

    @property
    def observation_lane_by_name(self) -> dict[str, str]:
        """the `plurai.config.yaml` `observation_lane` value per action name.

        The SAME split `_classify` already computed for `mode:` (rewired -> mock, everything else ->
        real), read the other way: a `left_real`/`denied` action has NO interception layer — apex,
        apexRest, api, externalService and flow are the only rewirable schemes, and every reason that
        keeps an action real (no callout seam, a name collision, a reserved word, disagreeing
        definitions) is equally structural — no run will EVER populate `tool_events` for it, whatever
        the specific reason. A rewired action calls out to the mock service and IS intercepted, so it
        gets the ordinary lane. One dict, so a caller can never see `mode` and `observation_lane`
        disagree about the same action the way the two used to before this field existed.

        The one exception is a `left_real` action whose calls the harness itself reports
        (`self_reported`): it stays `mode: real`, but its lane is the one the harness writes. A
        denied action is never called, so it stays `none` whatever its target.
        """
        lane: dict[str, str] = {action.name: 'tool_events' for action in self.rewired}
        lane.update({name: 'none' for name, _ in self.left_real})
        lane.update({name: report.lane for name, report in self.self_reported})
        lane.update({name: 'none' for name, _ in self.denied})
        return lane

    @property
    def granularity_by_name(self) -> dict[str, str]:
        """The `plurai.config.yaml` `granularity` per action whose calls the harness self-reports.

        Read off the same typed fact as the lane (the target's `SelfReport`), never off the name.
        """
        return {name: report.granularity for name, report in self.self_reported}


def _session_input_lines() -> list[str]:
    pad = ' ' * IO_ENTRY_INDENT
    sub = ' ' * IO_PROPERTY_INDENT
    return [
        f'{pad}{SESSION_VARIABLE}: string',
        f'{sub}description: "The plurai mock session id for this run. It comes from the agent variable of the '
        f'same name; never ask the user for it and never repeat it back."',
        f'{sub}label: "Plurai Session Id"',
        f'{sub}is_required: True',
        f'{sub}is_user_input: False',
    ]


def _session_variable_lines() -> list[str]:
    pad = ' ' * VARIABLE_INDENT
    sub = ' ' * (VARIABLE_INDENT * 2)
    return [
        f'{pad}{SESSION_VARIABLE}: mutable string',
        f'{sub}description: "The plurai mock session id for this run. Set through the Agent API at session '
        f'start; it selects the mock world state the rewired actions read and write."',
        f'{sub}visibility: "External"',
    ]


def _classify(action: ActionDefinition, *, skip_complex_types: bool) -> str | None:
    """The reason this action stays real, or None when it is rewired."""
    if not action.rewirable:
        return f'{action.scheme or "unknown"}:// runs inside the org and has no callout seam'
    if skip_complex_types and action.complex_typed:
        return 'its schema declares a composite type Apex cannot carry (--skip-complex-types)'
    if len(action.apex_class) > APEX_NAME_LIMIT:
        return f'the Apex class name {action.apex_class} exceeds {APEX_NAME_LIMIT} characters'
    return None


def _signature(action: ActionDefinition) -> tuple[str, tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    return (
        action.scheme or '',
        tuple((entry.name, entry.declared_type) for entry in action.inputs),
        tuple((entry.name, entry.declared_type) for entry in action.outputs),
    )


def _disagreement(copies: list[ActionDefinition]) -> str | None:
    """One Apex class serves every copy of a name, so the copies have to declare the same thing."""
    if len({_signature(copy) for copy in copies}) == 1:
        return None
    return f'its {len(copies)} definitions declare different inputs, outputs or targets, so one Apex class cannot serve them'


def _confirmation_gates(bundle: Bundle) -> tuple[tuple[tuple[str, tuple[int, ...]], ...], tuple[tuple[str, tuple[int, ...]], ...]]:
    """The names the platform confirms before each call, and the names whose definitions disagree about it.

    Read from the declaration only: a name is gated when every definition of it says True, whatever its fate here.
    """
    gated: list[tuple[str, tuple[int, ...]]] = []
    disagree: list[tuple[str, tuple[int, ...]]] = []
    for name, copies in sorted(bundle.by_name().items()):
        says = tuple(copy.confirmation_line + 1 for copy in copies if copy.requires_confirmation and copy.confirmation_line is not None)
        if says and len(says) == len(copies):
            gated.append((name, says))
        elif says:
            disagree.append((name, says))
    return tuple(gated), tuple(disagree)


_RECORD_TYPE_NAME = 'lightning__recordInfoType'
_DESCRIPTION_RE = re.compile(rf'^ {{{IO_PROPERTY_INDENT}}}description:')
RECORD_INPUT_NOTE = (
    "In this copy the record travels as text: pass the record's Id (the Id field of the record an earlier action returned), "
    'never an email address, a name or another identifier.'
)


def _with_record_note(line: str) -> str:
    """One description line with the record contract appended — the original text first, inside its quotes."""
    body = line.rstrip()
    if body.endswith('"') and body.count('"') >= 2:
        return f'{body[:-1]} {RECORD_INPUT_NOTE}"'
    return f'{" " * IO_PROPERTY_INDENT}description: "{_quoted_value(body)} {RECORD_INPUT_NOTE}"'


def _text_typed(lines: list[str], entry: IoEntry, *, is_input: bool = False) -> dict[int, str]:
    """The line replacements that restate one complex parameter as the JSON text the mock returns.

    A record INPUT loses its type. The platform checks no provenance on a record input: its planner
    fills the slot from the conversation (a record built from an email, or an Id no action returned)
    and the action runs. So the copy adds no provenance check of its own, since that would refuse calls
    production executes. Its description asks for the Id of a returned record. That is guidance, not
    enforcement: an ungrounded fill reaches the mock as an Id, where production may build the record
    from other fields.
    """
    head, _, rest = lines[entry.start].partition(':')
    name, _, default = rest.partition('=')
    tail = f' = {default.strip()}' if default else ''
    edits = {entry.start: f'{head}: {MOCK_COMPLEX_TYPE}{tail}'}
    for i in range(entry.start + 1, entry.end):
        if _COMPLEX_TYPE_RE.match(lines[i]):
            edits[i] = f'{" " * IO_PROPERTY_INDENT}complex_data_type_name: "{MOCK_COMPLEX_TYPE_NAME}"'
    if is_input and _complex_type_name(lines, entry) == _RECORD_TYPE_NAME:
        description = next((i for i in range(entry.start + 1, entry.end) if _DESCRIPTION_RE.match(lines[i])), None)
        if description is None:
            edits[entry.start] += f'\n{" " * IO_PROPERTY_INDENT}description: "{RECORD_INPUT_NOTE}"'
        else:
            edits[description] = _with_record_note(lines[description])
    return edits


def _rename_key(text: str, old: str, new: str) -> str:
    """Rename the parameter a declaration line opens (`"date": object` -> `"dateValue": object`); later lines of the text are kept."""
    return re.sub(rf'^(\s*"?){re.escape(old)}("?:)', rf'\g<1>{new}\g<2>', text, count=1)


def _complex_type_name(lines: list[str], entry: IoEntry) -> str:
    return next(_quoted_value(lines[i]) for i in range(entry.start + 1, entry.end) if _COMPLEX_TYPE_RE.match(lines[i]))


def _split_by_fate(bundle: Bundle, *, skip_complex_types: bool) -> tuple[list[ActionDefinition], list[tuple[str, str]]]:
    """Sort the bundle's action names into the ones that are rewired and the ones that stay real."""
    rewired: list[ActionDefinition] = []
    left_real: list[tuple[str, str]] = []
    for name, copies in sorted(bundle.by_name().items()):
        reason = _classify(copies[0], skip_complex_types=skip_complex_types) or _disagreement(copies)
        if reason is None:
            rewired.append(copies[0])
        else:
            left_real.append((name, reason))
    return rewired, left_real


def _self_report(copies: list[ActionDefinition]) -> SelfReport | None:
    """How the harness reports this real action's calls, or None when it reports none.

    Every definition must carry the self-reported target: a name some of whose definitions run
    something else cannot be credited with every reply that carries the target's evidence.
    """
    report = SELF_REPORTED_TARGETS.get(copies[0].target or '')
    if report is None or any(copy.target != copies[0].target for copy in copies):
        return None
    return report


def _read_only(copies: list[ActionDefinition]) -> bool:
    """Whether EVERY definition of a name targets a known read-only kind (`READ_ONLY_TARGETS`)."""
    return all(copy.target in READ_ONLY_TARGETS for copy in copies)


def _target_of(copies: list[ActionDefinition]) -> str:
    return ', '.join(sorted({copy.target or 'an unparsable target' for copy in copies}))


def _denial_remedies(name: str, why_real: str, copies: list[ActionDefinition]) -> str:
    """The ways out of a denied action the copy still mentions: drop the mention, point it at the
    mock when the rewrite can, or grant it to run for real."""
    remove = 'Remove that mention from the bundle, since the copy cannot call it'
    if all(copy.rewirable for copy in copies):
        rewire = f'or rewire it to the mock: it stays real only because {why_real}; change that in the bundle and the copy calls the mock instead'
    else:
        rewire = 'it cannot be rewired to the mock, because its target has no callout seam'
    return f'{remove}; {rewire}; or, to let it run for real in the org, add it to plurai_sim/{CONSENT_FILE} (granted: {name}).'


def _refuse_uncallable_calls(out: list[str], *, callable_names: set[str]) -> None:
    """The closing invariant: every `@actions.<X>` left in the copy names a rewired action, a callable
    left-real one, or a reasoning alias of one. An action the parser did not see (any syntax it misses)
    is none of these, so the copy is refused rather than left calling it for real."""
    aliases = {
        match.group(1)
        for line in out
        if (match := _ALIAS_RE.match(line)) and (match.group(2) != 'actions' or match.group(3) in callable_names)
    }
    for number, line in enumerate(out, 1):
        for called in _ACTION_CALL_RE.findall(line):
            if called not in callable_names | aliases:
                raise RewireError(
                    f'the copy calls @actions.{called} at line {number}, which is no action this rewrite parsed, so it would run for real in '
                    'the org unchecked; write its definition as `<name>:` on its own line under the subagent\'s `actions:` block '
                    '(a trailing `# comment` is fine), or remove the call'
                )


def _self_reports(
    left_real: list[tuple[str, str]], bundle: Bundle
) -> tuple[tuple[tuple[str, SelfReport], ...], tuple[tuple[str, str], ...]]:
    """The callable real actions the harness self-reports, and why the others with such a target are not.

    The evidence has to exist: a reply carries it only when the bundle's `knowledge:` block enables
    it. And it has to be attributable: it names no action, so when several callable actions share a
    self-reported target none of them is credited.
    """
    by_name, knowledge = bundle.by_name(), knowledge_values(bundle)
    candidates = [(name, by_name[name][0].target, report) for name, _ in left_real if (report := _self_report(by_name[name])) is not None]
    unobserved: list[tuple[str, str]] = []
    enabled = []
    for name, target, report in candidates:
        raw, line_no = knowledge.get(report.enabled_by, ('', 0))
        if raw == 'True':
            enabled.append((name, target, report))
        else:
            said = ('is not set' if not line_no else f'is {raw} (line {line_no})' if raw == 'False'
                    else f'is `{raw}` (line {line_no}), not the Agent Script literal `True`')
            unobserved.append((name, f'knowledge {report.enabled_by} {said}, so a reply carries no evidence of its calls'))
    sharing = {target: tuple(name for name, other, _ in enabled if other == target) for _, target, _ in enabled}
    reported = tuple((name, report) for name, target, report in enabled if len(sharing[target]) == 1)
    for name, target, _ in enabled:
        if len(sharing[target]) > 1:
            others = ', '.join(other for other in sharing[target] if other != name)
            unobserved.append((name, f'its target is shared with {others}, so a reply cannot say which one ran'))
    return reported, tuple(sorted(unobserved))


def rewire_bundle(text: str, *, granted: frozenset[str], typed_live: frozenset[str], skip_complex_types: bool = False) -> tuple[str, RewirePlan]:
    """Return the rewired copy's Agent Script and the plan describing it. The input text is untouched.

    An action left real is callable in the copy only when every definition of it targets a known
    read-only kind (`READ_ONLY_TARGETS`) or `granted` names it; any other has its references removed.
    `typed_live` (the scan's host effects) only ADDS denials, so a writer the scan mis-typed as a
    reader, or left untyped, is still denied. Both are required: a caller that forgets one must fail
    loudly, not deny nothing.
    """
    bundle = parse_bundle(text)
    by_name = bundle.by_name()
    owned = {definition.target_line for definition in bundle.definitions}
    for number, line in enumerate(bundle.lines):
        if _ANY_TARGET_RE.match(line) and number not in owned:
            raise RewireError(f'line {number + 1} sets a target no parsed action definition owns, so the copy would keep calling it for real '
                              'under a name the rewrite classified from another definition; write the definition as `<name>:` on its own line under the subagent\'s `actions:` block')
    rewired, left_real = _split_by_fate(bundle, skip_complex_types=skip_complex_types)
    rewired_names = {action.name for action in rewired}
    # A name that matches an action only by case would grant or deny nothing, silently.
    spelled = {name.lower(): name for name in rewired_names | {name for name, _ in left_real}}
    for which, names in (('grant', granted), ('deny', typed_live)):
        for name in sorted(names):
            if name not in spelled.values() and name.lower() in spelled:
                raise RewireError(f'{name} names the bundle action {spelled[name.lower()]} only by case, so it would {which} nothing; '
                                  'spell it as the bundle does in the map, the config and plurai_sim/.host_effects_consent, then refresh with `bench_manifest.py init`')
    # What runs in the org needs a grant unless it is known to only read; the scan's typing only adds.
    needs_grant = {name for name, _ in left_real if name in typed_live or not _read_only(by_name[name])}
    denied = tuple((name, DENIED_TYPED if name in typed_live else DENIED_UNPROVEN) for name, _ in left_real if name in needs_grant - granted)
    denied_names = {name for name, _ in denied}
    stays_real = dict(left_real)
    real_targets = tuple((name, _target_of(by_name[name])) for name, _ in left_real)
    granted_live = tuple((name, target) for name, target in real_targets if name in needs_grant & granted)
    left_real = [(name, reason) for name, reason in left_real if name not in denied_names]

    replacements: dict[int, str] = {}
    deletions: set[int] = set()
    insertions: dict[int, list[str]] = {}

    def insert_before(line_no: int, added: list[str]) -> None:
        insertions.setdefault(line_no, []).extend(added)

    copy_name = bundle.developer_name + COPY_SUFFIX
    replacements[bundle.developer_name_line] = f'{" " * VARIABLE_INDENT}developer_name: "{copy_name}"'
    if bundle.label_line is not None:
        label = _quoted_value(bundle.lines[bundle.label_line])
        replacements[bundle.label_line] = f'{" " * VARIABLE_INDENT}agent_label: "{label}{COPY_LABEL_SUFFIX}"'
    insert_before(bundle.variables_end, _session_variable_lines())

    # Every copy of a rewired definition moves. Leaving one behind would keep a live Flow running
    # under a name the agent believes is mocked.
    for definition in bundle.definitions:
        if definition.name not in rewired_names:
            continue
        if definition.target_line is not None:
            replacements[definition.target_line] = f'{" " * ACTION_PROPERTY_INDENT}target: "apex://{definition.apex_class}"'
        # `source:` names the packaged action this definition was derived from. The rewired copy
        # implements the action itself, so the reference goes; it is not part of the tool surface.
        if definition.source_line is not None:
            deletions.add(definition.source_line)
        for is_input, entries in ((True, definition.inputs), (False, definition.outputs)):
            for entry in entries:
                if entry.complex:
                    replacements.update(_text_typed(bundle.lines, entry, is_input=is_input))
        if definition.inputs_end is not None:
            insert_before(definition.inputs_end, _session_input_lines())
        elif definition.inputs_missing_before is not None:
            insert_before(definition.inputs_missing_before, [f'{" " * ACTION_PROPERTY_INDENT}inputs:', *_session_input_lines()])

    # The binding goes first among the reference's sub-lines, ahead of any `set` or `if`, which is
    # where every other `with` in the file sits.
    binding = f'{" " * BINDING_INDENT}with {SESSION_VARIABLE} = @variables.{SESSION_VARIABLE}'
    for name, reference_line in bundle.references:
        if name in rewired_names:
            insert_before(reference_line + 1, [binding])
        elif name in denied_names:  # the reference and its `with` lines; an emptied `actions:` goes too
            deletions.update(range(reference_line, _block_end(bundle.lines, reference_line, REFERENCE_INDENT)))
    # A parameter Apex reserves is renamed in the copy: its declaration, each call site's `with` binding
    # and each `@outputs.` read. The class sends the declared name to the mock, so nothing downstream moves.
    by_rewired = {action.name: action for action in rewired}
    for definition in bundle.definitions:
        if definition.name not in rewired_names:
            continue
        for entries, fields in ((definition.inputs, definition.input_fields), (definition.outputs, definition.output_fields)):
            for entry in entries:
                if fields[entry.name] != entry.name:
                    replacements[entry.start] = _rename_key(replacements.get(entry.start, bundle.lines[entry.start]), entry.name, fields[entry.name])
    for name, reference_line in bundle.references:
        if name not in rewired_names or not by_rewired[name].renamed:
            continue
        action = by_rewired[name]
        for j in range(reference_line + 1, _block_end(bundle.lines, reference_line, REFERENCE_INDENT)):
            edited = replacements.get(j, bundle.lines[j])
            for old, new in action.input_fields.items():
                if old != new:
                    edited = re.sub(rf'^(\s*with\s+){re.escape(old)}(\s*=)', rf'\g<1>{new}\g<2>', edited)
            for old, new in action.output_fields.items():
                if old != new:
                    edited = re.sub(rf'@outputs\.{re.escape(old)}\b', f'@outputs.{new}', edited)
            if edited != bundle.lines[j]:
                replacements[j] = edited
    for i, line in enumerate(bundle.lines):
        if _indent(line) == REFERENCES_INDENT and _ACTIONS_HEADER_RE.match(line.strip()):
            body = [j for j in range(i + 1, _block_end(bundle.lines, i, REFERENCES_INDENT)) if bundle.lines[j].strip()]
            if body and deletions.issuperset(body):
                deletions.add(i)

    out: list[str] = []
    for i, line in enumerate(bundle.lines):
        out.extend(insertions.get(i, []))
        if i not in deletions:
            out.extend(replacements.get(i, line).split('\n'))
    out.extend(insertions.get(len(bundle.lines), []))

    for number, line in enumerate(out, 1):
        for name, reason in denied:
            if re.search(rf'@actions\.{name}\b', line):
                raise RewireError(f'{name} {reason}, so the copy must not call it, and it still does at line {number}. '
                                  f'{_denial_remedies(name, stays_real[name], by_name[name])}')
    _refuse_uncallable_calls(out, callable_names=rewired_names | {name for name, _ in left_real})

    # What `_text_typed` restated, for the report: the planner fills these as text, not as their real type.
    mock_type = f'{MOCK_COMPLEX_TYPE}/{MOCK_COMPLEX_TYPE_NAME}'
    retyped = tuple(
        (action.name, entry.name, direction, real)
        for action in rewired
        for direction, entries in (('in', action.inputs), ('out', action.outputs))
        for entry in entries
        if entry.complex and (real := f'{entry.declared_type}/{_complex_type_name(bundle.lines, entry)}') != mock_type
    )
    renamed = tuple((action.name, direction, old, new) for action in rewired for direction, old, new in action.renamed)
    gated, disagree = _confirmation_gates(bundle)
    self_reported, unobserved = _self_reports(left_real, bundle)
    plan = RewirePlan(
        copy_developer_name=copy_name,
        rewired=tuple(rewired),
        left_real=tuple(left_real),
        retyped=retyped,
        renamed=renamed,
        denied=denied,
        granted_live=granted_live,
        real_targets=real_targets,
        knowledge_sources=knowledge_source_lines(bundle),
        confirmation_gated=gated,
        confirmation_disagreements=disagree,
        self_reported=self_reported,
        unobserved=unobserved,
        exposed_in=tuple(sorted(bundle.exposed_in.items())),
        act_aliases=_act_aliases(bundle.lines),
    )
    return '\n'.join(out) + '\n', plan


def _act_aliases(lines: list[str]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Per recorded act, the reasoning aliases (`<alias>: @utils.<name>`) that emit it, in file order, once each."""
    bound = [(m.group(1), m.group(3)) for line in lines if (m := _ALIAS_RE.match(line)) and m.group(2) == 'utils']
    return tuple((act.reply_type, tuple(dict.fromkeys(alias for alias, utility in bound if utility == act.utility))) for act in RECORDED_ACTS)


# --- generated Apex ----------------------------------------------------------------------------


def _apex_literal(value: str) -> str:
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"


def callout_class_source() -> str:
    """The one class that knows the endpoint, so a new env changes metadata and not code."""
    handler_error_key = _handler_error_tag_key(mock_envelope_rows()[0])
    return f"""// The single seam between the org and the plurai mock service. The named credential carries the
// env root — Salesforce keeps a path that a credential URL already holds — so this endpoint is
// static and a new env changes only the credential's URL.
public with sharing class {CALLOUT_CLASS} {{
    public class PluraiMockException extends Exception {{}}

    private static final String ENDPOINT = 'callout:{NAMED_CREDENTIAL}/mock/execute';

    // Salesforce caps a callout at 120 s and the server gives up before that, so the cap here is
    // the platform's own rather than a shorter guess that would mask a slow cold start.
    private static final Integer TIMEOUT_MS = 120000;

    public static Map<String, Object> execute(String sessionId, String tool, Map<String, Object> args) {{
        if (String.isBlank(sessionId)) {{
            throw new PluraiMockException('{SESSION_VARIABLE} is empty: the Agent API session did not carry the plurai mock session id');
        }}
        Map<String, Object> payload = new Map<String, Object>{{
            'session_id' => sessionId,
            'tool' => tool,
            'args' => args
        }};

        HttpRequest req = new HttpRequest();
        req.setEndpoint(ENDPOINT);
        req.setMethod('POST');
        req.setHeader('Content-Type', 'application/json');
        req.setTimeout(TIMEOUT_MS);
        req.setBody(JSON.serialize(payload));

        HttpResponse res = new Http().send(req);
        String body = res.getBody() == null ? '' : res.getBody();
        if (res.getStatusCode() != 200) {{
            throw new PluraiMockException('mock service returned ' + res.getStatusCode() + ' for ' + tool + ': ' + body);
        }}

        // The mock service answers 200 and names a failure inside the body, so the status alone is
        // not the verdict.
        Object parsed = JSON.deserializeUntyped(body);
        if (!(parsed instanceof Map<String, Object>)) {{
            throw new PluraiMockException('mock service returned a non-object body for ' + tool + ': ' + body);
        }}
        Map<String, Object> envelope = (Map<String, Object>) parsed;
        Object response = envelope.get('response');
        if (!(response instanceof Map<String, Object>)) {{
            throw new PluraiMockException('mock service returned no response object for ' + tool + ': ' + body);
        }}
        return agentView(tool, (Map<String, Object>) response);
    }}

    // What the action hands the agent from one mocked call: the response's `content`, whatever its
    // status, because a failure the real action reports through its outputs carries them there. A
    // failure with no content fails the action with the mock's message, as a faulted Flow does.
    //
    // A handler that raised carries no `status` at all (see the `{handler_error_key}` tag below),
    // so it is checked before the status-keyed branches, mirroring the backend's own `classify()` order.
    public static Map<String, Object> agentView(String tool, Map<String, Object> response) {{
        Object statusValue = response.get('status');
        String status = statusValue instanceof String ? (String) statusValue : null;
        Object content = response.get('content');
        if (('success'.equals(status) || 'error'.equals(status)) && content instanceof Map<String, Object>) {{
            return (Map<String, Object>) content;
        }}
        if (response.get('{handler_error_key}') == true) {{
            throw new PluraiMockException(tool + ' failed: ' + String.valueOf(response.get('error')));
        }}
        if ('error'.equals(status)) {{
            Object reason = response.get('message') != null ? response.get('message') : response.get('error');
            throw new PluraiMockException(tool + ' failed: ' + String.valueOf(reason));
        }}
        // Any other answer (no `content` map: a drifted status, text content, an un-enveloped
        // return) is the mock's answer, not a broken service: the action fails, and the agent
        // handles the failure, as the `typed_outputs` view of each such `mock_envelope` row says.
        throw new PluraiMockException(tool + ' failed: ' + JSON.serialize(response));
    }}

    // A composite Agent Script type has no invocable-variable counterpart, so its value crosses the
    // Apex boundary as text. Only a JSON object or array is a composite written as text; anything else
    // (a record Id, a date) is the value itself, and the parser can read a prefix of it as a number.
    public static Object fromText(String value) {{
        if (String.isBlank(value)) {{
            return value;
        }}
        try {{
            Object parsed = JSON.deserializeUntyped(value);
            if (parsed instanceof Map<String, Object> || parsed instanceof List<Object>) {{
                return parsed;
            }}
            return value;
        }} catch (Exception e) {{
            return value;
        }}
    }}

    public static String asText(Object value) {{
        if (value == null) {{
            return null;
        }}
        return value instanceof String ? (String) value : JSON.serialize(value);
    }}

    public static Boolean asBoolean(Object value) {{
        if (value == null) {{
            return null;
        }}
        return value instanceof Boolean ? (Boolean) value : Boolean.valueOf(String.valueOf(value));
    }}

    public static Decimal asDecimal(Object value) {{
        if (value == null) {{
            return null;
        }}
        return value instanceof Decimal ? (Decimal) value : Decimal.valueOf(String.valueOf(value));
    }}

    public static Integer asInteger(Object value) {{
        Decimal parsed = asDecimal(value);
        return parsed == null ? null : parsed.intValue();
    }}
}}
"""


_COERCERS = {'String': 'asText', 'Boolean': 'asBoolean', 'Decimal': 'asDecimal', 'Integer': 'asInteger'}


def apex_class_source(action: ActionDefinition) -> str:
    """One stand-in class per rewired action: the same variable names (bar an Apex-reserved one, renamed), answered by the mock service."""
    in_fields, out_fields = action.input_fields, action.output_fields
    request_fields = [
        f'        @InvocableVariable(required=true)\n        public String {SESSION_VARIABLE};',
        *(f'        @InvocableVariable\n        public {entry.apex_type} {in_fields[entry.name]};' for entry in action.inputs),
    ]
    response_fields = [f'        @InvocableVariable\n        public {entry.apex_type} {out_fields[entry.name]};' for entry in action.outputs]
    # The field may carry a different name than the declaration (Apex reserves `date`); the mock is always
    # sent and answers with the DECLARED name, so its contract is the one the scan wrote.
    args = [
        f"            args.put('{entry.name}', {CALLOUT_CLASS}.fromText(req.{in_fields[entry.name]}));"
        if entry.json_text
        else f"            args.put('{entry.name}', req.{in_fields[entry.name]});"
        for entry in action.inputs
    ]
    assignments = [
        f'            out.{out_fields[entry.name]} = {CALLOUT_CLASS}.{_COERCERS[entry.apex_type]}(result.get({_apex_literal(entry.name)}));' for entry in action.outputs
    ]

    # `callout=true` gives the invocable method its own transaction: Apex refuses an HTTP callout that follows uncommitted DML in the same one. Every
    # wrapper calls out by construction, so the attribute is unconditional. Without it the class still compiles, deploys and passes the health probe,
    # then throws `System.CalloutException: You have uncommitted work pending` only on a conversation whose planner does DML before reaching this action.
    return f"""// Stand-in for the {action.name} action. The inputs and outputs are the ones the agent already
// declares; the implementation is the plurai mock service instead of the org.
public with sharing class {action.apex_class} {{
    public class Request {{
{chr(10).join(request_fields)}
    }}

    public class Response {{
{chr(10).join(response_fields) if response_fields else '        @InvocableVariable' + chr(10) + '        public String outcome;'}
    }}

    @InvocableMethod(label='Plurai Mock {action.name}' callout=true)
    public static List<Response> invoke(List<Request> requests) {{
        List<Response> responses = new List<Response>();
        for (Request req : requests) {{
            Map<String, Object> args = new Map<String, Object>();
{chr(10).join(args) if args else '            // this action declares no inputs'}
            Map<String, Object> result = {CALLOUT_CLASS}.execute(req.{SESSION_VARIABLE}, '{action.name}', args);
            Response out = new Response();
{chr(10).join(assignments) if assignments else '            out.outcome = ' + CALLOUT_CLASS + ".asText(result.get('outcome'));"}
            responses.add(out);
        }}
        return responses;
    }}
}}
"""


def apex_class_meta() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ApexClass xmlns="http://soap.sforce.com/2006/04/metadata">
    <apiVersion>{API_VERSION}</apiVersion>
    <status>Active</status>
</ApexClass>
"""


#: the backend's whole `mock_envelope` table as this script reads it: each row's envelope and its
#: `typed_outputs` view. The workdir copy `up` runs cannot reach the vendored contract and self-tests
#: these; the plugin's tests hold them equal to the vendored block.
_BUILT_IN_ENVELOPE_ROWS: tuple[dict[str, Any], ...] = (
    {
        'id': 'success_content_map',
        'envelope': {'content': {'k': 'PLURAI_SELF_TEST_SUCCESS'}, 'message': 'PLURAI_SELF_TEST_SIBLING', 'status': 'success'},
        'views': {'typed_outputs': {'kind': 'returns', 'value': {'k': 'PLURAI_SELF_TEST_SUCCESS'}}},
    },
    {
        'id': 'success_content_text',
        'envelope': {'content': 'PLURAI_SELF_TEST_TEXT', 'status': 'success'},
        'views': {'typed_outputs': {'kind': 'fails', 'marker': 'PLURAI_SELF_TEST_TEXT'}},
    },
    {
        'id': 'error_message',
        'envelope': {'message': 'PLURAI_SELF_TEST_FAULT', 'status': 'error'},
        'views': {'typed_outputs': {'kind': 'fails', 'marker': 'PLURAI_SELF_TEST_FAULT'}},
    },
    {
        'id': 'engine_refusal',
        'envelope': {'_plurai_unregistered_tool': True, 'error': 'PLURAI_SELF_TEST_REFUSED', 'status': 'error', 'tool': 'plurai_self_test'},
        'views': {'typed_outputs': {'kind': 'fails', 'marker': 'PLURAI_SELF_TEST_REFUSED'}},
    },
    {
        'id': 'handler_error',
        'envelope': {'_plurai_handler_error': True, 'error': 'PLURAI_SELF_TEST_HANDLER_ERROR'},
        'views': {'typed_outputs': {'kind': 'fails', 'marker': 'PLURAI_SELF_TEST_HANDLER_ERROR'}},
    },
    {
        'id': 'error_with_content',
        'envelope': {'content': {'k': 'PLURAI_SELF_TEST_OUTCOME'}, 'message': 'PLURAI_SELF_TEST_UNREAD', 'status': 'error'},
        'views': {'typed_outputs': {'kind': 'returns', 'value': {'k': 'PLURAI_SELF_TEST_OUTCOME'}}},
    },
    {
        'id': 'contentless_success',
        'envelope': {'message': 'PLURAI_SELF_TEST_BARE', 'status': 'success'},
        'views': {'typed_outputs': {'kind': 'fails', 'marker': 'PLURAI_SELF_TEST_BARE'}},
    },
    {
        'id': 'drifted_status_with_content',
        'envelope': {'content': {'k': 'PLURAI_SELF_TEST_DRIFTED_OUTCOME'}, 'message': 'PLURAI_SELF_TEST_DRIFTED_STATUS', 'status': 'failed'},
        'views': {'typed_outputs': {'kind': 'fails', 'marker': 'PLURAI_SELF_TEST_DRIFTED_STATUS'}},
    },
)


def _handler_error_tag_key(rows: list[dict[str, Any]]) -> str:
    """The one key the `handler_error` row's example envelope marks `true` with — read off the
    contract rather than typed a second time, so `agentView` special-cases the exact tag the backend's
    mock engine writes, whichever name it is today."""
    row = next((r for r in rows if r['id'] == 'handler_error'), None)
    if row is None:
        raise RewireError("mock_envelope carries no 'handler_error' row — agentView cannot special-case a handler's own exception")
    keys = [key for key, value in row['envelope'].items() if value is True]
    if len(keys) != 1:
        raise RewireError(f"mock_envelope's 'handler_error' row does not name exactly one boolean tag key: {row['envelope']!r}")
    return keys[0]


#: The vendored contract, relative to a plugin root.
_VENDORED_CONTRACT = Path('scripts') / 'contracts' / 'contracts.json'
#: One read per process: `up` builds the callout class and the health script from the same rows.
_ENVELOPE_ROWS_READ: list[tuple[tuple[dict[str, Any], ...], str]] = []


def _vendored_contract() -> Path | None:
    """The vendored contract this file can reach: in the plugin tree it sits in, else under the plugin
    root the environment names. A workdir copy run with neither reaches none, by design."""
    here = Path(__file__).resolve()
    roots = [here.parents[3]] if len(here.parents) > 3 and here.parents[2].name == 'skills' else []
    if os.environ.get('CLAUDE_PLUGIN_ROOT'):
        roots.append(Path(os.environ['CLAUDE_PLUGIN_ROOT']))
    return next((root / _VENDORED_CONTRACT for root in roots if (root / _VENDORED_CONTRACT).is_file()), None)


def _read_envelope_rows() -> tuple[tuple[dict[str, Any], ...], str]:
    vendored = _vendored_contract()
    if vendored is None:
        # A workdir copy: the built-in table is held equal to the vendored block by the plugin's
        # tests, and the health script names its source, so this is the designed path, not a warning.
        return _BUILT_IN_ENVELOPE_ROWS, 'built-in'
    try:
        rows = json.loads(vendored.read_text(encoding='utf-8'))['blocks']['mock_envelope']['value']
        return tuple({'id': row['id'], 'envelope': row['envelope'], 'views': {'typed_outputs': row['views']['typed_outputs']}} for row in rows), 'vendored'
    except (OSError, ValueError, KeyError, TypeError) as error:
        reason = error.strerror if isinstance(error, OSError) else f'{type(error).__name__}: {error}'
        print(
            f'rewire: the vendored contract at {vendored} is unreadable ({reason}); '
            f'the mock projection self-test runs the {len(_BUILT_IN_ENVELOPE_ROWS)} mock_envelope rows built into this script',
            file=sys.stderr,
        )
        return _BUILT_IN_ENVELOPE_ROWS, 'built-in'


def mock_envelope_rows() -> tuple[list[dict[str, Any]], str]:
    """The envelope shapes the health script feeds the DEPLOYED projection, with what a typed-output
    action must receive from each, and which table they came from: the backend's `mock_envelope`
    contract as the plugin vendors it, else the one built into this script. Read as data rather than
    through `scripts/plurai_contracts.py`, because this file stays stdlib-only."""
    if not _ENVELOPE_ROWS_READ:
        _ENVELOPE_ROWS_READ.append(_read_envelope_rows())
    rows, source = _ENVELOPE_ROWS_READ[0]
    return [dict(row) for row in rows], source


#: A record Id the parser alone would read as the number it starts with; `fromText` must hand it back as text.
FROM_TEXT_SELF_TEST = '500XX00000AbCdE'
_SELF_TEST_REMEDY = "the deployed mock callout does not hand the mock's content to the actions - run up again from this plugin version"


def _self_test_block(row: dict[str, Any], handler_error_key: str) -> str:
    envelope, view = row['envelope'], row['views']['typed_outputs']
    call = f"{CALLOUT_CLASS}.agentView('plurai_self_test', (Map<String, Object>) JSON.deserializeUntyped({_apex_literal(json.dumps(envelope, separators=(',', ':')))}))"
    refusal = f'throw new {CALLOUT_CLASS}.PluraiMockException({_apex_literal("PLURAI_MOCK_PROJECTION failed: " + _SELF_TEST_REMEDY)});'
    header = f'// mock_envelope row: {row["id"]}'
    if view['kind'] == 'returns' and isinstance(view['value'], dict) and view['value']:
        checks = ' || '.join(f'!{_apex_literal(str(marker))}.equals(view.get({_apex_literal(str(key))}))' for key, marker in view['value'].items())
        return f"""{header}
view = {call};
if ({checks}) {{
    {refusal}
}}"""
    if view['kind'] == 'fails':
        # A row `agentView` fails through its dedicated `tool + ' failed: ' + reason` branch (an
        # `error` status, or the handler-error tag) has one exact message, so this checks equality
        # rather than containment: a `contains` check on the marker alone would pass just as well
        # against the generic fallback's JSON dump (`tool + ' failed: ' + <response>`), which also embeds it, so it
        # could never tell a real branch from that fallback. A row with neither carries no
        # dedicated message this file can predict, so it keeps the substring check.
        if envelope.get('status') == 'error' or handler_error_key in envelope:
            check = f"!fault.equals({_apex_literal('plurai_self_test failed: ' + str(view['marker']))})"
        else:
            check = f"!fault.contains({_apex_literal(str(view['marker']))})"
        return f"""{header}
fault = null;
try {{
    {call};
}} catch ({CALLOUT_CLASS}.PluraiMockException e) {{
    fault = e.getMessage();
}}
if (fault == null || {check}) {{
    {refusal}
}}"""
    raise RewireError(f'mock_envelope row {row["id"]!r}: a typed-output action cannot be checked against the view {view!r}')


def health_script(env_id: str) -> str:
    """Anonymous Apex for `agentforce_mock_path.sh up`: it throws on anything but 200 so the CLI's exit code is the verdict."""
    rows, source = mock_envelope_rows()
    handler_error_key = _handler_error_tag_key(rows)
    self_test = '\n\n'.join(_self_test_block(row, handler_error_key) for row in rows)
    return f"""// Proves the org reaches the deployed mock env through the Plurai backend with the token it holds.
// The named credential's URL is the env root, so this appends only /health.
//
// The block throws on anything but 200: `sf apex run` exits non-zero on a failed block, which is
// what makes `agentforce_mock_path.sh up` stop rather than deploy an agent that cannot call out.

String endpoint = 'callout:{NAMED_CREDENTIAL}/health';

HttpRequest req = new HttpRequest();
req.setEndpoint(endpoint);
req.setMethod('GET');
req.setTimeout(120000);

System.debug('PLURAI_MOCK_HEALTH env_id={env_id}');
System.debug('PLURAI_MOCK_HEALTH endpoint=' + endpoint);

HttpResponse res = new Http().send(req);
String body = res.getBody() == null ? '' : res.getBody();
System.debug('PLURAI_MOCK_HEALTH status=' + res.getStatusCode() + ' ' + res.getStatus());
System.debug('PLURAI_MOCK_HEALTH body=' + body.substring(0, Math.min(300, body.length())));

if (res.getStatusCode() != 200) {{
    throw new CalloutException('PLURAI_MOCK_HEALTH failed: ' + res.getStatusCode() + ' ' + body);
}}
System.debug('PLURAI_MOCK_HEALTH verdict=OK - the org reached the mock env through the Plurai backend');

// The deployed projection every stand-in reads through, run in the org before any agent turn.
System.debug('PLURAI_MOCK_PROJECTION rows={len(rows)} source={source}');
Map<String, Object> view;
String fault;

{self_test}

// A record Id is text, never the number its leading digits spell, and a JSON object is still a map.
if (!{_apex_literal(FROM_TEXT_SELF_TEST)}.equals({CALLOUT_CLASS}.fromText({_apex_literal(FROM_TEXT_SELF_TEST)})) || !({CALLOUT_CLASS}.fromText('{{"k":1}}') instanceof Map<String, Object>)) {{
    throw new {CALLOUT_CLASS}.PluraiMockException('PLURAI_MOCK_PROJECTION failed: the deployed mock callout does not pass a record Id to the mock as text - run up again from this plugin version');
}}
System.debug('PLURAI_MOCK_PROJECTION verdict=OK - the actions receive the mock content, and a contentless error fails the action');
"""


# --- generated credential metadata -------------------------------------------------------------


def named_credential_xml(endpoint: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<NamedCredential xmlns="http://soap.sforce.com/2006/04/metadata">
    <label>Plurai Mock</label>
    <description>Reaches the plurai mock env through the Plurai backend's mock proxy. The URL carries the
        env root, and the generated Apex appends /mock/execute.</description>
    <namedCredentialType>SecuredEndpoint</namedCredentialType>
    <calloutStatus>Enabled</calloutStatus>
    <generateAuthorizationHeader>false</generateAuthorizationHeader>
    <allowMergeFieldsInBody>false</allowMergeFieldsInBody>
    <allowMergeFieldsInHeader>true</allowMergeFieldsInHeader>
    <namedCredentialParameters>
        <description>The env root under the server's mock proxy prefix.</description>
        <parameterName>DefaultEndpoint</parameterName>
        <parameterType>Url</parameterType>
        <parameterValue>{endpoint}</parameterValue>
    </namedCredentialParameters>
    <namedCredentialParameters>
        <description>The named principal that holds the org token.</description>
        <parameterName>DefaultAuth</parameterName>
        <parameterType>Authentication</parameterType>
        <externalCredential>{EXTERNAL_CREDENTIAL}</externalCredential>
    </namedCredentialParameters>
    <namedCredentialParameters>
        <description>The org bearer, resolved from the principal at callout time.</description>
        <parameterName>Authorization</parameterName>
        <parameterType>HttpHeader</parameterType>
        <parameterValue>Bearer {{!$Credential.{EXTERNAL_CREDENTIAL}.{TOKEN_PARAMETER}}}</parameterValue>
        <sequenceNumber>1</sequenceNumber>
    </namedCredentialParameters>
</NamedCredential>
"""


def external_credential_xml() -> str:
    """The credential shape only, and no parameter that could hold a value.

    The principal is declared as an `externalCredentialParameters` entry — a `<principals>` element
    is rejected at parse time on this API version. The `{TOKEN_PARAMETER}` parameter the callout's
    merge field reads is *not* declared here: `agentforce_mock_path.sh up` creates it on the principal through
    Connect REST API, which is what keeps the token out of every file in the repo.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ExternalCredential xmlns="http://soap.sforce.com/2006/04/metadata">
    <label>Plurai Mock Auth</label>
    <description>Holds the org token for the mock path. The token is pushed to the
        principal at deploy time and is never written to a file.</description>
    <authenticationProtocol>Custom</authenticationProtocol>
    <externalCredentialParameters>
        <parameterName>{PRINCIPAL}</parameterName>
        <parameterType>NamedPrincipal</parameterType>
        <sequenceNumber>1</sequenceNumber>
    </externalCredentialParameters>
</ExternalCredential>
"""


def permission_set_xml(apex_classes: tuple[str, ...]) -> str:
    """The one grant the rewired agent's user needs to reach the mock service.

    The principal grant alone is not enough. A named-credential callout resolves its token
    out of `UserExternalCredential`, and an agent user's profile carries no read on that
    object, so the callout throws `You don't have read permissions on the User External
    Credential object` before any request leaves the org — an agent that answers "technical
    issue" and a mock service whose event log stays empty.
    """
    grant = '    <classAccesses>\n        <apexClass>{name}</apexClass>\n        <enabled>true</enabled>\n    </classAccesses>'
    grants = '\n'.join(grant.format(name=name) for name in apex_classes)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<PermissionSet xmlns="http://soap.sforce.com/2006/04/metadata">
    <label>Plurai Mock</label>
    <description>Grants the rewired copy's Apex, the external credential principal it calls out
        through, and the object the runtime reads that principal's token from. A callout is
        refused without all three.</description>
    <hasActivationRequired>false</hasActivationRequired>
{grants}
    <externalCredentialPrincipalAccesses>
        <enabled>true</enabled>
        <externalCredentialPrincipal>{EXTERNAL_CREDENTIAL}-{PRINCIPAL}</externalCredentialPrincipal>
    </externalCredentialPrincipalAccesses>
    <objectPermissions>
        <object>UserExternalCredential</object>
        <allowCreate>false</allowCreate>
        <allowDelete>false</allowDelete>
        <allowEdit>false</allowEdit>
        <allowRead>true</allowRead>
        <modifyAllRecords>false</modifyAllRecords>
        <viewAllRecords>false</viewAllRecords>
    </objectPermissions>
</PermissionSet>
"""


def _members(type_name: str, names: tuple[str, ...]) -> str:
    body = '\n'.join(f'        <members>{name}</members>' for name in names)
    return f'    <types>\n{body}\n        <name>{type_name}</name>\n    </types>'


def empty_package_xml() -> str:
    """The package half of a destructive deploy: everything to remove is listed in the other file."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
    <version>{API_VERSION}</version>
</Package>
"""


def destructive_changes_xml(plan: RewirePlan) -> str:
    blocks = [
        _members('AiAuthoringBundle', (plan.copy_developer_name,)),
        _members('ApexClass', plan.apex_classes),
        _members('PermissionSet', (PERMISSION_SET,)),
        _members('NamedCredential', (NAMED_CREDENTIAL,)),
        _members('ExternalCredential', (EXTERNAL_CREDENTIAL,)),
    ]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
{chr(10).join(blocks)}
    <version>{API_VERSION}</version>
</Package>
"""


#: The platform types the `Agent:<name>` pseudo-type retrieves: the records `publish` mints for an
#: agent (a Bot carries its BotVersions). Listed in the order a delete has to take them, each before
#: the records it references.
MINTED_TYPES = ('Bot', 'GenAiPlannerBundle', 'GenAiPlugin', 'GenAiFunction')
_MD = '{http://soap.sforce.com/2006/04/metadata}'


def minted_manifest(retrieved: Path) -> tuple[str, list[str]]:
    """The pre-destructive manifest for what `publish` minted, read from a retrieve of `Agent:<copy>`.

    `retrieved` is the `--target-metadata-dir` of that retrieve. Returns the manifest (empty when
    the retrieve listed nothing to delete) and one line per retrieved member it leaves in place. A
    namespaced member (`ns__Name`) belongs to the platform or a package rather than to the copy, and
    no deploy can delete it, so listing it would fail the whole delete.
    """
    found = sorted(retrieved.rglob('package.xml'))
    if not found:
        return '', []
    if len(found) > 1:
        raise RewireError(f'expected one package.xml under {retrieved}, found {len(found)}')
    try:
        root = ET.fromstring(found[0].read_text(encoding='utf-8'))
    except ET.ParseError as error:
        raise RewireError(f'{found[0]} is not readable XML: {error}') from error
    members: dict[str, list[str]] = {}
    kept: list[str] = []
    for block in root.iter(f'{_MD}types'):
        type_name = (block.findtext(f'{_MD}name') or '').strip()
        for member in (m.text.strip() for m in block.iter(f'{_MD}members') if m.text and m.text.strip()):
            if type_name not in MINTED_TYPES:
                kept.append(f'{type_name}: {member} (not a type publish mints)')
            elif '__' in member:
                kept.append(f'{type_name}: {member} (namespaced, so not the copy\'s)')
            else:
                members.setdefault(type_name, []).append(member)
    if not members:
        return '', kept
    blocks = [_members(type_name, tuple(members[type_name])) for type_name in MINTED_TYPES if type_name in members]
    return (
        f"""<?xml version="1.0" encoding="UTF-8"?>
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
{chr(10).join(blocks)}
    <version>{API_VERSION}</version>
</Package>
""",
        kept,
    )


def bundle_meta_xml(developer_name: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<AiAuthoringBundle xmlns="http://soap.sforce.com/2006/04/metadata">
    <bundleType>AGENT</bundleType>
    <target>{developer_name}.v1</target>
</AiAuthoringBundle>
"""


# --- the environment the copy points at --------------------------------------------------------


def mock_endpoint(base_url: str, env_id: str) -> str:
    """The named credential's URL: the server base, path and all, plus the env root.

    Salesforce keeps a path a credential URL already carries, which is what lets the generated
    Apex stay static across envs.
    """
    parts = urlsplit(base_url)
    if not parts.scheme or not parts.netloc:
        raise RewireError(f'{base_url} is not an absolute URL; the callout base must carry a scheme and a host')
    loopback = parts.hostname in {'localhost', '127.0.0.1', '::1'}
    if parts.scheme != 'https' and not loopback:
        raise RewireError(f'{base_url} is not https; Salesforce will not call out to it')
    if not env_id:
        raise RewireError("no mock env id; run the plugin's deploy_env and write the id to .env_id")
    return f'{base_url.rstrip("/")}/mock/{env_id}'


# --- writing the tree --------------------------------------------------------------------------


@dataclass(frozen=True)
class DxProject:
    """The Salesforce DX project: the directory holding `sfdx-project.json` and the package
    directory that manifest declares.

    A standard checkout puts the manifest at the repo root; a project that is one part of a larger
    repo nests it a directory down. Both are searched, exactly one may answer, and the package
    directory is read rather than assumed — `force-app` is a convention, not a rule.
    """

    root: Path
    package_dir: Path

    @classmethod
    def locate(cls, agent_root: Path) -> DxProject:
        found = _dx_manifests(agent_root)
        if len(found) != 1:
            listed = ', '.join(str(path) for path in found) if found else 'none'
            raise RewireError(f'expected exactly one {DX_MANIFEST} at {agent_root} or one directory below it, found {len(found)}: {listed}')
        manifest = found[0]
        return cls(root=manifest.parent, package_dir=_package_dir(manifest))


def _dx_manifests(agent_root: Path) -> list[Path]:
    candidates = [agent_root / DX_MANIFEST]
    if agent_root.is_dir():
        candidates += [child / DX_MANIFEST for child in sorted(agent_root.iterdir()) if child.is_dir() and child.name not in DX_SCAN_SKIP]
    return [candidate for candidate in candidates if candidate.is_file()]


def _package_dir(manifest: Path) -> Path:
    """The package directory `sf` deploys from, as the manifest declares it."""
    try:
        declared = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise RewireError(f'{manifest} is not readable JSON: {error}') from error
    entries = declared.get('packageDirectories') if isinstance(declared, dict) else None
    if not isinstance(entries, list) or not entries:
        raise RewireError(f'{manifest} declares no packageDirectories; the generated metadata has nowhere to land')
    marked = [entry for entry in entries if isinstance(entry, dict) and entry.get('default')]
    if len(marked) == 1:
        chosen = marked[0]
    elif len(entries) == 1 and isinstance(entries[0], dict):
        chosen = entries[0]
    else:
        raise RewireError(f'{manifest} declares {len(entries)} packageDirectories and {len(marked)} of them default; mark exactly one default')
    declared_path = chosen.get('path')
    if not isinstance(declared_path, str) or not declared_path.strip():
        raise RewireError(f'{manifest} declares a packageDirectories entry with no path')
    relative = PurePosixPath(declared_path.strip().replace('\\', '/'))
    if relative.is_absolute() or '..' in relative.parts:
        raise RewireError(f'{manifest} declares the package directory as {declared_path}; it has to be a relative path inside the project')
    return manifest.parent.joinpath(*relative.parts)


@dataclass(frozen=True)
class Paths:
    """Where the generated tree lands. Metadata sits in the package directory so `sf` resolves it;
    the manifests and the health script sit outside it so they are never mistaken for components."""

    metadata_root: Path
    support_root: Path

    @classmethod
    def under(cls, dx: DxProject) -> Paths:
        return cls(metadata_root=dx.package_dir / 'generated', support_root=dx.root / 'generated')


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def sole_agent_file(bundle_dir: Path) -> Path:
    """The bundle's one Agent Script file. A directory with none or several is not a bundle to rewire."""
    sources = sorted(bundle_dir.glob('*.agent'))
    if len(sources) != 1:
        raise RewireError(f'expected exactly one .agent file in {bundle_dir}, found {len(sources)}')
    return sources[0]


def generate(bundle_dir: Path, paths: Paths, *, base_url: str, env_id: str, workdir: Path, skip_complex_types: bool = False) -> RewirePlan:
    """Emit the rewired copy, its Apex, and its credential metadata. The source bundle is read only.

    A left-real action is not callable in the copy unless every definition of it targets a known
    read-only kind, or the workdir's `.host_effects_consent` grants it; the scan's host effects deny
    even a read-only one until granted. A config whose self-report entries are not the
    plan's (`check_trail_lanes`), or a harness that files citations on another footing
    (`check_harness`), is refused before anything is written.
    """
    source = sole_agent_file(bundle_dir)
    endpoint = mock_endpoint(base_url, env_id)
    typed_live = declared_host_effects(workdir)

    copy_text, plan = rewire_bundle(source.read_text(encoding='utf-8'), granted=_granted(workdir), typed_live=typed_live, skip_complex_types=skip_complex_types)
    check_trail_lanes(plan, workdir)
    check_harness(plan, workdir)

    # A stale tree would deploy classes for actions this env does not rewire.
    for root in (paths.metadata_root, paths.support_root):
        if root.exists():
            shutil.rmtree(root)

    copy_dir = paths.metadata_root / 'aiAuthoringBundles' / plan.copy_developer_name
    _write(copy_dir / f'{plan.copy_developer_name}.agent', copy_text)
    _write(copy_dir / f'{plan.copy_developer_name}.bundle-meta.xml', bundle_meta_xml(plan.copy_developer_name))

    classes = paths.metadata_root / 'classes'
    _write(classes / f'{CALLOUT_CLASS}.cls', callout_class_source())
    _write(classes / f'{CALLOUT_CLASS}.cls-meta.xml', apex_class_meta())
    for action in plan.rewired:
        _write(classes / f'{action.apex_class}.cls', apex_class_source(action))
        _write(classes / f'{action.apex_class}.cls-meta.xml', apex_class_meta())

    _write(paths.metadata_root / 'namedCredentials' / f'{NAMED_CREDENTIAL}.namedCredential-meta.xml', named_credential_xml(endpoint))
    _write(paths.metadata_root / 'externalCredentials' / f'{EXTERNAL_CREDENTIAL}.externalCredential-meta.xml', external_credential_xml())
    _write(paths.metadata_root / 'permissionsets' / f'{PERMISSION_SET}.permissionset-meta.xml', permission_set_xml(plan.apex_classes))

    _write(paths.support_root / 'manifest' / 'package.xml', empty_package_xml())
    _write(paths.support_root / 'manifest' / 'destructiveChanges.xml', destructive_changes_xml(plan))
    _write(paths.support_root / 'apex' / 'plurai_mock_health.apex', health_script(env_id))
    return plan


# --- what the lifecycle recipes read ------------------------------------------------------------


def plan_report(plan: RewirePlan) -> str:
    """The mock|real split, as both the scan and the generation print it.

    One string for the two modes on purpose: a scan that worded the split differently from the
    generation would show the customer a table the deploy does not match.
    """
    lines = [
        f'rewired copy: {plan.copy_developer_name}',
        f'rewired actions ({len(plan.rewired)}): {", ".join(action.name for action in plan.rewired)}',
    ]
    if plan.rewired:
        # stated once here, not per name — every rewired action is
        # intercepted by the mock service, the ordinary observation_lane, never a special case.
        lines.append('  observation_lane: tool_events (intercepted by the mock service)')
    lines.append(f'left real ({len(plan.left_real)}):')
    # observation_lane appended PER ITEM, after the existing reason text: whatever the specific
    # reason an action stays real, none of them puts a run through the one interception point
    # (the Apex callout) that populates tool_events. It is `none` unless the harness reports the
    # action's calls itself (`self_reported`), and then it names that lane. An action with a
    # self-reported target the harness cannot observe (`unobserved`) says why it is `none`.
    self_reported, unobserved = dict(plan.self_reported), dict(plan.unobserved)
    lines.extend(
        f'  {name}: {reason} (observation_lane: {report.lane}, granularity: {report.granularity}, self-reported by the harness from {report.evidence}; {report.caveat})'
        if (report := self_reported.get(name)) is not None
        else f'  {name}: {reason} (observation_lane: none, {why})'
        if (why := unobserved.get(name))
        else f'  {name}: {reason} (observation_lane: none)'
        for name, reason in plan.left_real
    )
    if plan.retyped:
        lines.append(f'retyped ({len(plan.retyped)}):')
        lines.extend(f'  {action}.{io} ({direction}): {real} -> {MOCK_COMPLEX_TYPE}/{MOCK_COMPLEX_TYPE_NAME}' for action, io, direction, real in plan.retyped)
    if plan.renamed:
        lines.append(f'renamed ({len(plan.renamed)}): Apex reserves the name, so the copy declares the parameter under another one; the mock still receives and answers with the declared name')
        lines.extend(f'  {action}.{old} ({direction}): -> {new}' for action, direction, old, new in plan.renamed)
    lines.extend(plan.knowledge_sources)
    targets = dict(plan.real_targets)
    if plan.denied:
        lines.append(f'denied ({len(plan.denied)}):')
        lines.extend(
            f'  {name} ({targets.get(name, "an unparsable target")}): {reason}. The simulated copy cannot call it, so a scenario that '
            f'needs it will show the agent unable to take that step. To let simulations call it for real, add it to '
            f'plurai_sim/{CONSENT_FILE} (granted: {name}). (observation_lane: none)'
            for name, reason in plan.denied
        )
    if plan.granted_live:
        # The record of every org action the operator vouched for: it runs for real, outside the mock.
        lines.append(f'granted live ({len(plan.granted_live)}):')
        lines.extend(
            f'  {name} ({target}): runs for real in your org, on your grant in plurai_sim/{CONSENT_FILE}. (observation_lane: {plan.observation_lane_by_name[name]})'
            for name, target in plan.granted_live
        )
    if plan.confirmation_gated:
        lines.append(f'confirmation-gated ({len(plan.confirmation_gated)}): require_user_confirmation: True on every definition -> tools.<name>.requires_user_confirmation: true')
        lines.extend(f'  {name}: line {", ".join(map(str, at))}' for name, at in plan.confirmation_gated)
    if plan.confirmation_disagreements:
        lines.append(f'confirmation disagreement ({len(plan.confirmation_disagreements)}): only some definitions say require_user_confirmation: True')
        lines.extend(f'  {name}: True at line {", ".join(map(str, at))}' for name, at in plan.confirmation_disagreements)
    # Printed whatever the bundle binds: the harness records these acts for every agent, and a scan
    # that is not told so guesses that a hand-off leaves no record.
    aliases = dict(plan.act_aliases)
    lines.append(f'recorded acts ({len(RECORDED_ACTS)}): the harness records each reply of this type on agent_actions under the verb, '
                 'with its targets; an act, not a tool call (no tools: entry, no observation_lane)')
    lines.extend(
        f'  {act.reply_type} -> {act.verb} (@utils.{act.utility}; '
        + (f'bound in this bundle as: {", ".join(bound)})' if (bound := aliases.get(act.reply_type)) else 'no alias in this bundle binds it)')
        for act in RECORDED_ACTS
    )
    return '\n'.join(lines)


def _with_snapshot(plan: RewirePlan, workdir: Path) -> RewirePlan:
    """The plan with the knowledge snapshot's line beside its `knowledge source:` line, when the bundle binds one."""
    return replace(plan, knowledge_sources=plan.knowledge_sources + knowledge_snapshot_lines(workdir)) if plan.knowledge_sources else plan


def name_assignments(bundle_dir: Path) -> str:
    """The generator's names as `key=value` shell assignments, ready for `eval`.

    The recipes deploy and delete by name, so a name they spell themselves is a name that can
    drift from the one this module emits. Values are shell-quoted; the agent user comes from the
    bundle, which is the only place that knows it, and is empty when the bundle declares none —
    bring-up needs it for the on-behalf-of grant and refuses, while teardown never reads it.
    """
    bundle = parse_bundle(sole_agent_file(bundle_dir).read_text(encoding='utf-8'))
    values = {
        'suffix': COPY_SUFFIX,
        'ec': EXTERNAL_CREDENTIAL,
        'principal': PRINCIPAL,
        'param': TOKEN_PARAMETER,
        'permset': PERMISSION_SET,
        'apiver': API_VERSION,
        'agent_user': bundle.agent_user or '',
        'copy': bundle.developer_name + COPY_SUFFIX,
    }
    return '\n'.join(f'{key}={shlex.quote(value)}' for key, value in values.items())


_TEXT_VARIABLE_RE = re.compile(r'(?:mutable\s+)?string(?:\s*=.*)?')


def external_text_variable(text: str, name: str) -> bool:
    """Whether the bundle's top-level `variables:` block declares `name` a string marked
    `visibility: "External"`: the only kind the Agent API sets from a message. Any other is dropped
    without an error, so a file id sent on it never reaches the agent."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if _indent(line) == 0 and line.strip() == 'variables:'), None)
    if start is None:
        return False
    end = _block_end(lines, start, 0)
    for i in range(start + 1, end):
        key, sep, spec = lines[i].strip().partition(':')
        if _indent(lines[i]) != VARIABLE_INDENT or not sep or key != name:
            continue
        if not _TEXT_VARIABLE_RE.fullmatch(spec.strip()):
            return False
        body = lines[i + 1:_block_end(lines, i, VARIABLE_INDENT)]
        return any(_indent(line) == 2 * VARIABLE_INDENT and line.strip().partition(':')[0] == 'visibility' and _quoted_value(line) == 'External'
                   for line in body)
    return False


def file_input_agent_user(project: Path, copy_name: str, variable: str) -> str:
    """For the harness's file turn: the user the rewired copy `copy_name` runs as, which the uploaded file
    is shared with, once its bundle is checked to declare `variable` an External string."""
    bundle_dir = Paths.under(DxProject.locate(project)).metadata_root / 'aiAuthoringBundles' / copy_name
    if not bundle_dir.is_dir():
        raise RewireError(f'{bundle_dir} is missing: bring the mock path up, which generates the rewired copy')
    text = sole_agent_file(bundle_dir).read_text(encoding='utf-8')
    if not external_text_variable(text, variable):
        raise RewireError(
            f'{copy_name} declares no variable {variable!r} as a string with visibility: "External", so the '
            'Agent API would drop the file id sent on it. _FILE_VARIABLE must name the External Text variable '
            "the reader action's file input is bound from; mark it External in the agent's bundle if it is not."
        )
    agent_user = parse_bundle(text).agent_user
    if not agent_user:
        raise RewireError(f'{copy_name} declares no access.default_agent_user to share the uploaded file with')
    return agent_user


def path_assignments(agent_root: Path) -> str:
    """The resolved layout as `key=value` shell assignments, ready for `eval`.

    `sf` runs from the DX root and the generated metadata lands under the package directory, both
    of which move with the checkout's layout. The lifecycle reads them from here so the layout is
    resolved once, in the module that already owns it.
    """
    dx = DxProject.locate(agent_root)
    values = {
        'AGENT_ROOT': str(agent_root),
        'DX_ROOT': str(dx.root),
        'PACKAGE_DIR': str(dx.package_dir),
        'WORKDIR': str(_resolve_workdir(agent_root)),
    }
    return '\n'.join(f'{key}={shlex.quote(value)}' for key, value in values.items())


def credential_body(token: str) -> str:
    """The Connect REST body that puts the org token on the principal.

    The caller reads the token from the environment and pipes this body on stdin, so the value is
    never a command-line argument and never a file.
    """
    if not token:
        raise RewireError('PLURAI_TOKEN is empty; the principal would be created without a credential')
    return json.dumps(
        {
            'externalCredential': EXTERNAL_CREDENTIAL,
            'principalName': PRINCIPAL,
            'principalType': 'NamedPrincipal',
            'authenticationProtocol': 'Custom',
            'credentials': {TOKEN_PARAMETER: {'value': token, 'encrypted': True}},
        }
    )


# --- the liveness probe ------------------------------------------------------------------------

#: A cold sandbox can take most of a minute to answer its first request, and the lifecycle retries
#: on top of this, so one attempt waits long. It bounds each connect and each read.
PROBE_TIMEOUT_SECONDS = 70.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Report a redirect as its own status: the lifecycle decides on the code the URL answered."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _origin(url: str) -> tuple[str, str, int] | None:
    """`(scheme, host, port)` of an absolute http(s) URL, else None.

    A spelling two parsers read differently (backslash, userinfo, `%` in the authority, whitespace or
    control characters) is None, as in `hooks/session_pin.py`.
    """
    text = (url or '').strip()
    if '\\' in text or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in text):
        return None
    try:
        parts = urlsplit(text)
        scheme, host, port = parts.scheme.lower(), (parts.hostname or '').lower(), parts.port
    except ValueError:
        return None
    if '@' in parts.netloc or '%' in parts.netloc:
        return None
    if scheme not in ('http', 'https') or not host:
        return None
    return scheme, host, (443 if scheme == 'https' else 80) if port is None else port


#: Beside a copy of this file: the one backend origin the plugin's guard accepted when the copy was made
#: (`scripts/record_backend_origin.py`), the record the copied `resolve-plurai-urls.sh` reads too. A bare
#: lowercase `https://host[:port]`, or `http://127.0.0.1:port` / `http://localhost:port` for a local backend, and
#: nothing else; any other line is no record.
_BACKEND_RECORD = '.plurai_backend_origin'
_RECORDED_ORIGIN = re.compile(r'https://[a-z0-9-]+(?:\.[a-z0-9-]+)*(?::[0-9]+)?|http://(?:127\.0\.0\.1|localhost):[0-9]+')


def _recorded_origin() -> tuple[str, str, int] | None:
    try:
        lines = (Path(__file__).resolve().parent / _BACKEND_RECORD).read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    return _origin(lines[0]) if lines and _RECORDED_ORIGIN.fullmatch(lines[0]) else None


def _plugin_url_guard():
    """`hooks/url_guard.py` of the plugin this file ships in, or None for a copy outside it."""
    root = Path(__file__).resolve().parents[3]
    hooks = root / 'hooks'
    # The plugin's own tree has its manifest beside `hooks/`; three directories above a copy in a project
    # is whatever the project has there, and it is not run.
    if not (root / '.claude-plugin' / 'plugin.json').is_file():
        return None
    if not (hooks / 'url_guard.py').is_file() or not (hooks / 'session_pin.py').is_file():
        return None
    # Loaded by path, with the hooks directory on the import path for its own sibling import only while
    # it runs: this file is stdlib-only, and a copy of it must not depend on the plugin being there.
    spec = importlib.util.spec_from_file_location('plurai_url_guard', hooks / 'url_guard.py')
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(hooks))
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 - a guard that cannot load answers "no", through the narrower rule
        sys.modules.pop(spec.name, None)
        return None
    finally:
        sys.path.remove(str(hooks))
    return module


def _is_backend_url(url: str) -> bool:
    """Whether the bearer may be sent to `url`.

    Inside the plugin the answer is `hooks/url_guard.py`'s: loopback, or an origin the session pin
    recorded when the session started. The environment of the command that runs this is not read, so
    a command prefix cannot name its own destination. A copy in the customer's workdir has no session
    pin to read, so it holds the narrower rule that needs none: exactly the origin recorded beside it
    (`_recorded_origin`). Loopback as such is no answer: a remote backend's token must never reach a local listener. `test_rewire_agentforce_probe.py` holds the two together.
    """
    guard = _plugin_url_guard()
    if guard is not None:
        return bool(guard.is_backend_url(url))
    target = _origin(url)
    if target is None:
        return False
    return target == _recorded_origin()


def _not_the_backend() -> str:
    """The sentence that says why the bearer is withheld: the plugin's (with the reason an unusable session pin adds), or, in a copy that has no guard, the plain one."""
    guard = _plugin_url_guard()
    if guard is not None and hasattr(guard, 'refused_message'):
        return str(guard.refused_message('this', ''))
    return 'this is not the backend this session is bound to'


#: The client name the backend keeps the plugin's floor under, and the value sent when no version is known: it sorts
#: below every floor, so the backend answers with the update message rather than a malformed-header refusal.
_CLIENT_NAME = 'plurai-simulation'
_UNKNOWN_CLIENT_VERSION = '0.0.0'
#: Beside a copy of this file: the `X-Plurai-Client` value of the plugin that made the copy (`scripts/record_backend_origin.py`).
#: A copy has no plugin manifest to read, and this file is stdlib-only, so the value travels as a record. The pattern is
#: the version grammar of `scripts/_client_handshake.py`.
_CLIENT_RECORD = '.plurai_client'
_CLIENT_STAMP = re.compile(
    rf'{_CLIENT_NAME}/\d{{1,9}}\.\d{{1,9}}\.\d{{1,9}}(?:[-.]?(?:dev|a|b|rc)\.?\d{{1,9}}|\.post\d{{1,9}})?', re.ASCII
)


def _plugin_handshake(root: Path) -> Any:
    """`scripts/_client_handshake.py` of the plugin this file ships in, loaded by path; `None` in a copy or when it cannot load."""
    handshake = root / 'scripts' / '_client_handshake.py'
    if not ((root / '.claude-plugin' / 'plugin.json').is_file() and handshake.is_file()):
        return None
    spec = importlib.util.spec_from_file_location('plurai_client_handshake', handshake)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up while the class is built
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 - a handshake that cannot load falls through to the copy's own rule
        sys.modules.pop(spec.name, None)
        return None
    return module


def _client_header() -> str:
    """The `X-Plurai-Client` value every request to the backend carries.

    Inside the plugin it is the manifest's version, from `scripts/_client_handshake.py`. In a copy it is the record
    beside the file, or the lowest version when there is none or it does not read as one.
    """
    root = Path(__file__).resolve().parents[3]
    module = _plugin_handshake(root)
    if module is not None:
        return str(module.header_value(root))
    try:
        lines = (Path(__file__).resolve().parent / _CLIENT_RECORD).read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []
    if lines and _CLIENT_STAMP.fullmatch(lines[0]):
        return lines[0]
    return f'{_CLIENT_NAME}/{_UNKNOWN_CLIENT_VERSION}'


def _user_agent() -> str:
    """`plurai-simulation-plugin/<version>` from the plugin this file ships in, by the same rule as `_client_header`;
    `.../unknown` for a copy outside it. The hosted endpoint rejects urllib's default agent."""
    root = Path(__file__).resolve().parents[3]
    module = _plugin_handshake(root)
    if module is not None:
        return str(module.user_agent(root))
    return 'plurai-simulation-plugin/unknown'


def probe(url: str, token: str, timeout: float = PROBE_TIMEOUT_SECONDS) -> str:
    """The HTTP status of `GET url` as three digits, or `000` when nothing answered.

    The bearer is sent only when `token` is non-empty and `url` is the configured backend: the URL
    is an argument, and the token would otherwise follow it to any host a command line names. The
    caller passes the token from the environment, so the org token is never a command-line argument
    and never in the process table. Redirects are not followed. The reason for a `000` goes to
    stderr, because a TLS or DNS failure and a closed port print the same code.
    """
    request = urllib.request.Request(url, method='GET', headers={'User-Agent': _user_agent()})
    request.add_unredirected_header('X-Plurai-Client', _client_header())
    if token and not _is_backend_url(url):
        print(f'rewire: probe {url}: bearer withheld, {_not_the_backend()}', file=sys.stderr)
        token = ''
    if token:
        request.add_header('Authorization', f'Bearer {token}')
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            return f'{response.status:03d}'
    except urllib.error.HTTPError as error:
        return f'{error.code:03d}'
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as error:
        print(f'rewire: probe {url}: no answer ({error})', file=sys.stderr)
        return '000'


# --- CLI ---------------------------------------------------------------------------------------


def _resolve_workdir(project: Path) -> Path:
    """The plugin's simulation workdir under `project`.

    `plurai_sim/` is the bare layout; with `envs/` and `.active-env` both present the active env's
    directory is the workdir instead. This mirrors the resolver the plugin's own skills source,
    reduced to the part that applies once the project root is already known — the lifecycle script
    runs long after the plugin is gone and cannot source it.
    """
    workdir = project / 'plurai_sim'
    active = workdir / '.active-env'
    envs = workdir / 'envs'
    if envs.is_dir() and active.is_file():
        name = active.read_text(encoding='utf-8').strip()
        if name:
            return envs / name
    return workdir


def _resolve_agent_root(start: Path) -> Path:
    """The Agentforce project root: the nearest directory at or above `start` holding `plurai_sim/`.

    The lifecycle script lives inside the workdir and hands over its own directory, so the root is
    found through the marker the plugin writes rather than through the DX layout, which differs
    between checkouts. A directory that is already the root answers itself.
    """
    for candidate in (start, *start.parents):
        if (candidate / 'plurai_sim').is_dir():
            return candidate
    return start


def located_agent_file(workdir: Path) -> Path | None:
    """The retrieved bundle's one Agent Script file beside ``workdir``, or None when there is no bundle to rewrite.

    The one locate the plugin's readers share: a bundle here means the agent-host route is this rewrite, which denies an
    ungranted action in the copy and proves the copy's callout itself (``up``'s health callout); none means some other
    simulated copy, which has neither.
    """
    try:
        return sole_agent_file(_default_bundle_dir(DxProject.locate(_resolve_agent_root(workdir.resolve()))))
    except (RewireError, OSError):
        return None


def _default_env_id(project: Path) -> str:
    """The mock env id the plugin's env generation wrote to `.env_id` under the workdir.

    The workdir is the only place read: under the named-env layout each env has its own file, and a
    second location would let a stale root copy deploy the copy against an env nobody selected.
    """
    candidate = _resolve_workdir(project) / '.env_id'
    return candidate.read_text(encoding='utf-8').strip() if candidate.is_file() else ''


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--project-dir', type=Path, default=None, help='the Agentforce project root (default: the working directory)')
    parser.add_argument('--bundle-dir', type=Path, default=None, help='the retrieved AiAuthoringBundle directory')
    parser.add_argument('--base-url', default='', help='the Plurai backend REST base the callout reaches; required whenever the tree is generated')
    parser.add_argument('--env-id', default=None, help='mock env id (default: the .env_id file under the project dir)')
    parser.add_argument('--skip-complex-types', action='store_true', help='leave an action real when its schema declares a type Apex cannot carry')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--names', action='store_true', help='print the generator names as shell assignments and exit')
    mode.add_argument('--print-env-id', action='store_true', help='print the resolved mock env id and exit')
    mode.add_argument('--paths', action='store_true', help='print the resolved project layout as shell assignments and exit')
    mode.add_argument('--credential-body', action='store_true', help='print the credential request body from PLURAI_TOKEN and exit')
    mode.add_argument('--granted', metavar='NAME', default=None, help="exit 0 iff the workdir's .host_effects_consent grants NAME, else 1")
    mode.add_argument('--plan', action='store_true', help='print the mock|real split for the retrieved bundle and exit, writing nothing')
    mode.add_argument(
        '--probe', metavar='URL', default=None, help='print the HTTP status of GET URL (000 when nothing answers) with the PLURAI_TOKEN bearer and exit'
    )
    mode.add_argument(
        '--minted-manifest', metavar='DIR', type=Path, default=None,
        help='print the pre-destructive manifest for the records a retrieve of Agent:<copy> into DIR lists (nothing when it lists none) and exit',
    )
    mode.add_argument('--write-lanes', action='store_true', help="write the observation_lane (and granularity) --plan prints, and each action's exposed_in subagents, onto each tool entry of the workdir's config, and exit")
    return parser.parse_args(argv)


def _default_bundle_dir(dx: DxProject) -> Path:
    root = dx.package_dir / 'main' / 'default' / 'aiAuthoringBundles'
    # A retrieved bundle is named after its agent, so a dot-prefixed directory here belongs to an
    # editor or an agent tool that writes state wherever it runs. `glob('*')` matches it, and
    # counting it refuses an ordinary checkout by sending the caller after a second agent that
    # does not exist.
    bundles = sorted(p for p in root.glob('*') if p.is_dir() and not p.name.startswith('.'))
    if len(bundles) != 1:
        raise RewireError(f'expected exactly one retrieved bundle under {root}, found {len(bundles)}; pass --bundle-dir')
    return bundles[0]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    # The setup skill copies this module into the project's `plurai_sim/`, so `__file__` sits below
    # the tree it reads and writes. The project root is an argument instead, and the working
    # directory is the default because that is where the lifecycle script runs it. Either way it is
    # normalized upward, so the lifecycle can hand over the workdir it sits in.
    project = _resolve_agent_root((args.project_dir or Path.cwd()).resolve())
    try:
        if args.probe is not None:
            # Always exit 0 with a code on stdout: the lifecycle runs under `set -e` and decides
            # on the code, so a failed probe must print `000` rather than end the run.
            print(probe(args.probe, os.environ.get('PLURAI_TOKEN', '').strip()))
            return 0
        if args.granted is not None:
            return 0 if args.granted in _granted(_resolve_workdir(project)) else 1
        if args.minted_manifest is not None:
            # `clean` writes stdout to the manifest it deploys, so what it leaves goes to stderr.
            manifest, kept = minted_manifest(args.minted_manifest)
            print(manifest, end='')
            for line in kept:
                print(f'kept in the org: {line}', file=sys.stderr)
            return 0
        if args.credential_body:
            print(credential_body(os.environ.get('PLURAI_TOKEN', '')))
            return 0
        if args.paths:
            # The lifecycle deploys from the DX root and cleans the tree under the package
            # directory. Both come from here so a checkout that nests its DX project needs no
            # second convention.
            print(path_assignments(project))
            return 0
        if args.print_env_id:
            # The lifecycle script needs the id for its health probe and asks for it here rather
            # than reading `.env_id` itself, so the workdir layout is resolved in one place.
            env_id = args.env_id if args.env_id is not None else _default_env_id(project)
            if not env_id:
                raise RewireError("no mock env id; run the plugin's env generation and write the id to .env_id")
            print(env_id)
            return 0
        if args.names:
            print(name_assignments(args.bundle_dir or _default_bundle_dir(DxProject.locate(project))))
            return 0
        if args.plan:
            # The split depends on the bundle alone, so the scan reports it before the mock env
            # exists — no base url, no env id, and no tree. `up` generates.
            bundle_dir = args.bundle_dir or _default_bundle_dir(DxProject.locate(project))
            # The denials the bundle and the consent file decide print always; the ones the scan's typing
            # adds print once its manifest is current, and before that the plan says why they are not known.
            workdir = _resolve_workdir(project)
            try:
                typed_live, unknown = declared_host_effects(workdir), ''
            except RewireError as error:
                typed_live, unknown = frozenset(), f"\ndenied by the scan's typing: not known yet — {error}"
            _, plan = rewire_bundle(
                sole_agent_file(bundle_dir).read_text(encoding='utf-8'), granted=_granted(workdir), typed_live=typed_live, skip_complex_types=args.skip_complex_types
            )
            print(plan_report(_with_snapshot(plan, _resolve_workdir(project))) + unknown)
            return 0
        if args.write_lanes:
            # The lanes depend on the denial as well as the bundle, so the scan's manifest must be current.
            workdir = _resolve_workdir(project)
            bundle_dir = args.bundle_dir or _default_bundle_dir(DxProject.locate(project))
            _, plan = rewire_bundle(sole_agent_file(bundle_dir).read_text(encoding='utf-8'), granted=_granted(workdir),
                                    typed_live=declared_host_effects(workdir), skip_complex_types=args.skip_complex_types)
            wrote, changed = write_lanes(plan, workdir)
            head = f'wrote to {workdir / CONFIG}:' if changed else f'{workdir / CONFIG} already declares every lane the plan prints:'
            print('\n'.join([head, *(f'  {line}' for line in wrote)]))
            return 0
        if not args.base_url:
            # No environment fallback: the base is the Plurai backend REST URL the plugin's resolver
            # hands over, and a guess here builds a callout that leaves the org and reaches nothing.
            raise RewireError('no --base-url; pass the Plurai backend REST base the callout reaches')
        dx = DxProject.locate(project)
        plan = generate(
            args.bundle_dir or _default_bundle_dir(dx),
            Paths.under(dx),
            base_url=args.base_url,
            env_id=args.env_id if args.env_id is not None else _default_env_id(project),
            workdir=_resolve_workdir(project),
            skip_complex_types=args.skip_complex_types,
        )
    except RewireError as error:
        print(f'rewire: {error}', file=sys.stderr)
        return 1

    print(plan_report(_with_snapshot(plan, _resolve_workdir(project))))
    return 0


#: Native Windows; a test sets ``rewire_agentforce.WINDOWS``.
WINDOWS = os.name == 'nt'


def utf8_stdio() -> None:
    """``hooks/_utf8_stdio.py:utf8_stdio``, repeated because this file runs from its workdir copy: on Windows only,
    a standard stream that is not UTF-8 (a cp1252 pipe) becomes UTF-8 with ``errors='replace'``."""
    if not WINDOWS:
        return
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            if stream.encoding.lower().replace('_', '-') not in ('utf-8', 'utf8'):
                stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError, OSError):
            continue


def utf8_child_text() -> dict[str, str]:
    """``scripts/_stdio.py:utf8_child_text``, repeated for the same reason: ``subprocess`` arguments that decode a
    child writing UTF-8 (the sf CLI) as UTF-8 on Windows, and nothing elsewhere."""
    return {'encoding': 'utf-8', 'errors': 'replace'} if WINDOWS else {}


if __name__ == '__main__':
    utf8_stdio()
    raise SystemExit(main())
