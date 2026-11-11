#!/usr/bin/env python3
"""Read what the deployed mock answered against the agent's own response types. Advisory: it never blocks.

    mock_shape_probe.py [--workdir DIR]

Run it under the HARNESS interpreter (`$PLURAI_CHILD_PYTHON`, else `$PLURAI_PY`): it loads `simulate.py`, so the agent's
modules import exactly as in a run. `bench_manifest.py verify` keeps the mock's answers from the contract and witness
tests (`artifacts/mock_answers.json`, `mock_contract_check.py --answers-out`). For each mocked tool whose answer the
agent's own pydantic model, TypedDict or dataclass types (`derive_tool_facts.response_type`, the same reading of the
source as the scan), each kept answer goes through `pydantic.TypeAdapter(<that type>).validate_python`: what the agent's
client does with the answer. The only oracle is the agent's code; no answer is compared with another.

Writes `artifacts/mock_shape_probe.json` `{schema, env_id, tools: {tool: {status, reason?, type?, answers?, errors?: [{field,
message}], test?}}}`, `status` one of:

    ok          every kept answer fits
    mismatch    an answer does not fit; `errors` names each field, `test` the test that got it
    unverified  not checked, and `reason` says why: no test answered it, its answer is not typed by the agent's own class,
                its source was not found, the harness encodes its answer (`payload_codecs`), the type did not resolve or pydantic
                cannot read it, or the harness did not load. Never a pass.

`bench_manifest.py mock-gate` reads this for THIS world (`.env_id`): a mismatch is a WARN naming the tool and the field, which
the world's one `/edit-env` round may take. An edit keeps the env id, so the contract check that collects the answers again
removes this read with them (`mock_contract_check.py --answers-out`): answers for this world with no read of them make every
tool unverified (this script did not run since, or could not read the config).

Exit codes: 0 whatever it found · 2 usage.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
import typing
from pathlib import Path
from typing import Any

SCHEMA = 'plurai-mock-shape-probe/1'
RESULT = Path('artifacts') / 'mock_shape_probe.json'
ANSWERS = Path('artifacts') / 'mock_answers.json'
OK, MISMATCH, UNVERIFIED = 'ok', 'mismatch', 'unverified'
_ERRORS_SHOWN = 8
_DERIVE = Path(__file__).resolve().parents[1] / 'skills' / 'simulation-setup-python' / 'scripts' / 'derive_tool_facts.py'


def _source_roots(workdir: Path) -> list[Path]:
    """Where a tool's module may resolve: the folder above the bench, then each folder up to the checkout's top."""
    top = next((d for d in (workdir, *workdir.parents) if (d / '.git').exists()), workdir.parent)
    return [workdir.parent, *[d for d in workdir.parents if d != workdir.parent and (d == top or top in d.parents)], workdir]


def _derive_module() -> Any:
    spec = importlib.util.spec_from_file_location('_mock_shape_derive', _DERIVE)
    if spec is None or spec.loader is None:
        raise ImportError(f'no {_DERIVE.name}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _encodes_answer(entry: dict[str, Any]) -> bool:
    """The harness encodes a path of this tool's answer on its reply, so the mock rightly returns another shape."""
    return any(str(c.get('at') or '').startswith('returns') for c in entry.get('payload_codecs') or [] if isinstance(c, dict))


def _type_ref(derive: Any, roots: list[Path], name: str, entry: dict[str, Any]) -> dict[str, str] | None:
    descriptor = entry.get('descriptor') if isinstance(entry.get('descriptor'), dict) else {}
    for root in roots:
        ref = derive.response_type(root, name, descriptor)
        if ref is not None:
            return ref
    return None


def resolved_type(ref: dict[str, str]) -> Any:
    """The annotation, resolved in the agent's live module, as `typing.get_type_hints` resolves any annotation."""
    module = importlib.import_module(ref['module'])

    def holder() -> None: ...

    holder.__annotations__ = {'return': ref['annotation']}
    return typing.get_type_hints(holder, globalns=vars(module), include_extras=True)['return']


def check_answers(hint: Any, answers: list[dict[str, Any]]) -> dict[str, Any]:
    """One tool's verdict over its kept answers. Raises only when pydantic cannot read `hint`."""
    from pydantic import TypeAdapter, ValidationError  # noqa: PLC0415 - the agent's own pydantic, under the harness interpreter

    adapter = TypeAdapter(hint)
    errors: list[dict[str, str]] = []
    test = None
    for answer in answers:
        content = answer.get('content')
        if isinstance(content, str):  # a `json[...]` answer is the same value spelled as text
            try:
                content = json.loads(content)
            except ValueError:
                pass
        try:
            adapter.validate_python(content)
        except ValidationError as exc:
            test = test or answer.get('test')
            for error in exc.errors():
                row = {'field': '.'.join(str(p) for p in error.get('loc') or ()) or '(the whole answer)', 'message': str(error.get('msg'))}
                if row not in errors:
                    errors.append(row)
    if errors:
        return {'status': MISMATCH, 'errors': errors[:_ERRORS_SHOWN], 'test': test, 'answers': len(answers)}
    return {'status': OK, 'answers': len(answers)}


def run(workdir: Path) -> dict[str, Any]:  # noqa: PLR0912
    env_file = workdir / '.env_id'
    env_id = env_file.read_text(encoding='utf-8').strip() if env_file.is_file() else ''
    record: dict[str, Any] = {'schema': SCHEMA, 'env_id': env_id, 'tools': {}}
    try:
        import yaml  # noqa: PLC0415 - the harness interpreter's, as the deriver's

        config = yaml.safe_load((workdir / 'plurai.config.yaml').read_text(encoding='utf-8')) or {}
    except Exception as exc:  # noqa: BLE001 - nothing to read names no tool
        record['skipped'] = f'plurai.config.yaml could not be read: {type(exc).__name__}'
        return record
    tools = {n: e for n, e in (config.get('tools') or {}).items() if isinstance(e, dict) and e.get('mode') == 'mock'}
    try:
        stored = json.loads((workdir / ANSWERS).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        stored = {}
    held = stored.get('tools') if isinstance(stored, dict) and stored.get('env_id') == env_id else None
    answers = held if isinstance(held, dict) else {}

    def unverified(reason: str) -> dict[str, Any]:
        return {'status': UNVERIFIED, 'reason': reason}

    try:
        derive = _derive_module()
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - the deriver exits when pyyaml is missing; a lost reading decides nothing
        record['tools'] = {n: unverified(f'the source reader did not load: {type(exc).__name__}') for n in tools}
        return record
    roots = _source_roots(workdir)
    refs: dict[str, dict[str, str]] = {}
    for name, entry in tools.items():
        if _encodes_answer(entry):
            record['tools'][name] = unverified('the harness encodes its answer, so the mock rightly returns another shape')
        elif not answers.get(name):
            record['tools'][name] = unverified('no contract or witness test got an answer from it')
        else:
            try:
                ref = _type_ref(derive, roots, name, entry)
            except Exception as exc:  # noqa: BLE001 - an unreadable source decides this tool only
                record['tools'][name] = unverified(f'its source could not be read: {type(exc).__name__}')
                continue
            if ref is None:
                record['tools'][name] = unverified('no class of the agent\'s own code types its answer')
            else:
                refs[name] = ref
    if not refs:
        return record
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import real_reader_probe  # noqa: PLC0415 - loads the harness the one way

        real_reader_probe._harness(workdir)  # noqa: SLF001
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - a harness that cannot load decides no tool; the Init smoke names why
        why = f'the harness did not load: {type(exc).__name__}: {str(exc)[:200]}'
        record['tools'] |= {name: unverified(why) for name in refs}
        return record
    for name, ref in refs.items():
        try:
            verdict = check_answers(resolved_type(ref), answers[name])
        except Exception as exc:  # noqa: BLE001 - a type pydantic cannot read, an import that fails: this tool only
            verdict = unverified(f'`{ref["annotation"]}` could not be read as a type: {type(exc).__name__}: {str(exc)[:200]}')
        record['tools'][name] = {**verdict, 'type': ref['annotation']}
    return record


def verdicts(workdir: Path, env_id: str) -> dict[str, dict[str, Any]]:
    """Tool -> its verdict, for THIS world; no read on disk is no verdict at all. A read on disk is of the answers on disk:
    the contract check that collects them removes it."""
    try:
        data = json.loads((workdir / RESULT).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get('schema') != SCHEMA or data.get('env_id') != env_id or not isinstance(data.get('tools'), dict):
        return {}
    return {t: v for t, v in data['tools'].items() if isinstance(v, dict)}


def unread(workdir: Path, env_id: str) -> dict[str, str] | None:
    """The verdict for every mocked tool when `verdicts` has none and the mock answered THIS world: never silence."""
    try:
        data = json.loads((workdir / ANSWERS).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and env_id and data.get('env_id') == env_id:
        return {'status': UNVERIFIED, 'reason': 'mock_shape_probe.py did not run or could not read the config'}
    return None


def mismatch_reason(tool: str, verdict: dict[str, Any]) -> str:
    """The gate's WARN text: the tool and each field."""
    fields = '; '.join(f'{e.get("field")}: {e.get("message")}' for e in verdict.get('errors') or [])
    return f'mock_shape_mismatch: what the mock answers for {tool} does not fit the agent\'s `{verdict.get("type")}`: {fields}'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workdir', default='.', help='the plurai_sim directory (default: the current one)')
    args = parser.parse_args(argv)
    workdir = Path(args.workdir).resolve()
    if not (workdir / 'simulate.py').is_file():
        print(f'mock_shape_probe: not_applicable, no Python harness in {workdir}')
        return 0
    record = run(workdir)
    (workdir / RESULT).parent.mkdir(parents=True, exist_ok=True)
    (workdir / RESULT).write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    if record.get('skipped'):
        print(f'mock_shape_probe: {record["skipped"]}')
    counts = {OK: 0, MISMATCH: 0, UNVERIFIED: 0}
    for tool, verdict in sorted(record['tools'].items()):
        counts[verdict['status']] += 1
        if verdict['status'] == MISMATCH:
            print(f'MISMATCH   {mismatch_reason(tool, verdict)}')
        elif verdict['status'] == UNVERIFIED:
            print(f'UNVERIFIED {tool}: {verdict["reason"]}')
    print(f'mock_shape_probe: {counts[OK]} fit the agent\'s types, {counts[MISMATCH]} do not, {counts[UNVERIFIED]} unverified')
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
