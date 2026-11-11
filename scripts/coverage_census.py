"""Coverage census: per kind of contract unit, how many end with each disposition. Report-only.

A unit is something the mock must get right, listed without a model from what `plurai.config.yaml` declares:

* ``tools``: every tool the mock world serves, the mocked ones (`mode` other than `real`) and the world-served real ones
  (`mode: real` whose reads or writes go through the generated world: `ToolDescriptor`'s effective `read_strategy` /
  `write_strategy` is `mocked`, explicit or implied by a non-null `reads_from` / `writes_to`). One the C10 record does not carry
  sits outside the mocked-tools check (C10 reads `mode != real`, plus the real tools served by harness-authored storage), so
  it is also counted as ``outside_gate``.
* ``rules``: every `descriptor.business_rules` and `descriptor.error_cases` entry of those tools.
* ``skipped_code``: every `skipped_code` line of those tools.
* ``world_types``: every `world_state.entity_types` name.
* ``row_sources``: every row source of a `seam: tool_body` read tool (derive_tool_facts.py `row_sources`: a library call
  whose result the tool iterates or returns), read off the agent's source under the bench's folder or its parent.

Each unit ends with exactly ONE disposition, taken in this order:

* ``tested``: a `test_*` body of `test_mock_contract.py` names it — the verifier's own reading
  (`verify_config._test_functions`: the body's string constants, docstring aside, compared lower-cased). A tool or a
  world type is named by its name; a rule only by one of its codes (`verify_config._RULE_CODE`), in a test that also
  names the rule's tool.
* ``witnessed``: env-gen's witness manifest (`mock_witness/mock_witness.json`) lists it: a test exercising the tool,
  a test over the world type, or a test exercising the rule's tool whose invariant spells one of the rule's codes.
* ``declined``: a typed decline lifts it. Today only a mocked tool's C10 waiver (`because`, `cite`, `owner`) is one.
* ``cited``: a row source answered for its exact line, by a `business_rules` `(source: …)` or a typed `skipped_code`
  `{cite, returns: every_row, evidence}` in the called library (derive_tool_facts.py `row_source_answer`, the reading
  verify_config's `row_source_uncited` makes); a range covering it is no answer.
* ``unaccounted``: none of the above.

So a unit nothing links to a test is `unaccounted` even when some test exercises it in practice: the census counts the
links a script can see, which is what a floor would key on. It changes no gate, verdict, rung or repair; its one
customer-facing output is the clause :func:`clause` adds to the run-integrity line, where an `unaccounted` unit reads
"with no test of its own" (never "open", which a customer reads as an unresolved problem).

Stdlib only: it runs under the tooling interpreter, like `bench_manifest.py`, its one caller.
"""

from __future__ import annotations

import ast
import functools
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mock_contract_check import entry_tools, witness_manifest

SCHEMA = 'plurai-coverage-census/1'
#: The kinds, in the order the clause says them, with the word for one unit.
KINDS = (('tools', 'tool'), ('rules', 'rule'), ('skipped_code', 'skipped code line'), ('world_types', 'world type'), ('row_sources', 'row source'))
DISPOSITIONS = ('tested', 'witnessed', 'declined', 'cited', 'unaccounted')
_DERIVE = Path(__file__).resolve().parents[1] / 'skills' / 'simulation-setup-python' / 'scripts' / 'derive_tool_facts.py'
#: The C10 status a typed waiver leaves on a mocked tool (`bench_manifest.mock_tools`).
WAIVED = 'waived'

#: `verify_config._RULE_SOURCE` / `_RULE_CODE`, pinned equal by test: a rule's codes, its trailing source cite aside.
_RULE_SOURCE = re.compile(r'\(source:\s*([^\s:()]+):(\d+)(?:-(\d+))?\)\s*\.?\s*$')
_RULE_CODE = re.compile(r'\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b')


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _listed(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _strategy_mocked(descriptor: Mapping[str, Any], strategy: str, store: str, nullish: frozenset[str]) -> bool:
    """`ToolDescriptor.effective_<side>_strategy == 'mocked'` (plurai_mockenv): explicit, or the default a non-null
    store implies — the reading `bench_manifest._reads_mocked` makes for the read side."""
    explicit = descriptor.get(strategy)
    if isinstance(explicit, str) and explicit:
        return explicit == 'mocked'
    value = descriptor.get(store)
    return isinstance(value, str) and bool(value.strip()) and value.strip().lower() not in nullish


def world_served(spec: Any) -> bool:
    """A `mode: real` tool whose reads or writes the generated world serves (either effective strategy is `mocked`)."""
    if not isinstance(spec, dict) or spec.get('mode') != 'real':
        return False
    descriptor = _mapping(spec.get('descriptor'))
    return (_strategy_mocked(descriptor, 'read_strategy', 'reads_from', frozenset())
            or _strategy_mocked(descriptor, 'write_strategy', 'writes_to', frozenset({'none', 'null'})))


def served_tools(config: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """(mocked, world-served real) tool names, in declaration order. An entry that is not a mapping is mocked, the SDK's default."""
    tools = _mapping(config.get('tools'))
    mocked = [str(name) for name, spec in tools.items() if not isinstance(spec, dict) or spec.get('mode', 'mock') != 'real']
    return mocked, [str(name) for name, spec in tools.items() if world_served(spec)]


def contract_tests(path: Path) -> list[tuple[set[str], list[str]]]:
    """Each `test_*` function of the contract file as (its string constants lower-cased, as written), docstring aside —
    `verify_config._test_functions`, pinned equal by test. No file, or one that does not parse: no tests."""
    try:
        tree = ast.parse(path.read_text(encoding='utf-8')) if path.is_file() else None
    except (OSError, SyntaxError, ValueError):
        tree = None
    out: list[tuple[set[str], list[str]]] = []
    for node in ast.walk(tree) if tree is not None else ():
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_'):
            body = node.body[1:] if ast.get_docstring(node) is not None else node.body
            strings = [c.value for stmt in body for c in ast.walk(stmt) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
            out.append(({v.lower() for v in strings}, strings))
    return out


def rule_codes(entry: Any) -> list[str]:
    """The codes a `business_rules` string or an `error_cases` entry spells (its `returns.error.code` included)."""
    if isinstance(entry, str):
        return _RULE_CODE.findall(_RULE_SOURCE.sub('', entry))
    if not isinstance(entry, dict):
        return []
    condition = entry.get('condition') if isinstance(entry.get('condition'), str) else ''
    code = _mapping(_mapping(entry.get('returns')).get('error')).get('code')
    return _RULE_CODE.findall(_RULE_SOURCE.sub('', condition)) + ([code] if isinstance(code, str) and code else [])


@functools.cache
def _derive() -> Any:
    """derive_tool_facts.py, loaded by path (verify_config's own reading of row sources); None without it or its pyyaml."""
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location('_derive_tool_facts_census', _DERIVE) if _DERIVE.is_file() else None
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except SystemExit:  # it exits when pyyaml is missing; so is the config then
        sys.modules.pop(spec.name, None)
        return None
    return module


def row_source_units(config: Mapping[str, Any], workdir: Path) -> dict[str, str]:
    """`{'<tool>:<file>:<line>': cited | unaccounted}` over the `seam: tool_body` read tools; empty when none is declared."""
    specs = _mapping(config.get('tools'))
    module = _derive() if specs else None
    if module is None:
        return {}
    out: dict[str, str] = {}
    for tool, spec in specs.items():
        if not module.row_source_tool(spec):
            continue
        try:
            bases = [workdir, workdir.parent]
            units = module.first_row_sources(bases, str(tool), _mapping(spec.get('descriptor')))
            out |= {f'{tool}:{unit}': 'unaccounted' if module.row_source_answer(unit, spec, bases) else 'cited' for unit in units}
        except (OSError, SyntaxError, ValueError, RuntimeError):
            continue
    return out


def _spells(texts: list[str], codes: list[str]) -> bool:
    return any(re.search(rf'\b{re.escape(code)}\b', text) for code in codes for text in texts)


def _kind(dispositions: dict[str, str], **extra: Any) -> dict[str, Any]:
    counts = {d: sum(1 for v in dispositions.values() if v == d) for d in DISPOSITIONS}
    return {'units': len(dispositions), **counts, **extra, 'dispositions': dispositions}


def census(config: Mapping[str, Any], contract: Path, witness_dir: Path, mock_tools: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The census record `verification.json` carries as `coverage_census`.

    `contract` is the bench's `test_mock_contract.py`, `witness_dir` the directory of env-gen's witness manifest (absent
    until the suite builds the mock), `mock_tools` the C10 record (`bench_manifest.mock_tools`), read for waivers and for the
    world-served tools it already carries (those are inside the gate).
    """
    config = config if isinstance(config, Mapping) else {}
    tests = contract_tests(contract)
    manifest = witness_manifest(witness_dir)
    entries = manifest or []
    witnessed_tools = {tool for entry in entries for tool in entry_tools(entry)}
    witnessed_types = {str(t) for entry in entries for t in _listed(entry.get('entity_types'))}
    mocked, served = served_tools(config)
    specs = _mapping(config.get('tools'))

    def tool_disposition(tool: str) -> str:
        if any(tool.lower() in lowered for lowered, _ in tests):
            return 'tested'
        if tool in witnessed_tools:
            return 'witnessed'
        return 'declined' if _mapping(mock_tools.get(tool)).get('status') == WAIVED else 'unaccounted'

    tools = {tool: tool_disposition(tool) for tool in [*mocked, *served]}

    rules: dict[str, str] = {}
    skipped: dict[str, str] = {}
    for tool in tools:
        spec = _mapping(specs.get(tool))
        descriptor = _mapping(spec.get('descriptor'))
        naming = [strings for lowered, strings in tests if tool.lower() in lowered]
        invariants = [str(e['invariant']) for e in entries if isinstance(e.get('invariant'), str) and tool in entry_tools(e)]
        for key in ('business_rules', 'error_cases'):
            for i, entry in enumerate(_listed(descriptor.get(key))):
                codes = rule_codes(entry)
                rules[f'{tool}.{key}[{i}]'] = ('tested' if codes and any(_spells(s, codes) for s in naming)
                                               else 'witnessed' if codes and _spells(invariants, codes) else 'unaccounted')
        for i, _line in enumerate(_listed(spec.get('skipped_code'))):
            skipped[f'{tool}.skipped_code[{i}]'] = 'unaccounted'

    world_types: dict[str, str] = {}
    for name in map(str, _mapping(_mapping(config.get('world_state')).get('entity_types'))):
        world_types[name] = ('tested' if any(name.lower() in lowered for lowered, _ in tests)
                             else 'witnessed' if name in witnessed_types else 'unaccounted')

    return {
        'schema': SCHEMA,
        'contract_tests': len(tests),
        'witness_manifest': manifest is not None,
        'kinds': {
            'tools': _kind(tools, outside_gate=len(outside := [t for t in served if t not in mock_tools]), outside_gate_tools=outside),
            'rules': _kind(rules),
            'skipped_code': _kind(skipped),
            'world_types': _kind(world_types),
            'row_sources': _kind(row_source_units(config, contract.parent)),
        },
    }


def _plural(word: str, n: int) -> str:
    return word if n == 1 else f'{word}s'


def clause(record: Mapping[str, Any] | None) -> str | None:
    """The run-integrity clause: counts only, one part per kind that has units; None when no kind has any.

    `144 tools: 20 tested, 100 witnessed, 24 with no test of their own (10 world-served, outside the mocked-tools
    check)`. The last count is the `unaccounted` units; the unit names stay in `verification.json`.
    """
    kinds = _mapping(_mapping(record).get('kinds'))
    parts: list[str] = []
    for kind, word in KINDS:
        counts = _mapping(kinds.get(kind))
        units = counts.get('units')
        if not isinstance(units, int) or units <= 0:
            continue
        said = [f'{counts[d]} {d}' for d in ('tested', 'witnessed', 'declined', 'cited') if counts.get(d)]
        if counts.get('unaccounted'):
            n = counts['unaccounted']
            said.append(f'{n} with no test of {"its" if n == 1 else "their"} own')
        part = f'{units} {_plural(word, units)}: {", ".join(said)}'
        if kind == 'tools' and counts.get('outside_gate'):
            part += f' ({counts["outside_gate"]} world-served, outside the mocked-tools check)'
        parts.append(part)
    return 'mocked-tool tests: ' + '; '.join(parts) if parts else None
