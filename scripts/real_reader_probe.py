#!/usr/bin/env python3
"""The real-reader probe: a mocked write, read back through the agent's REAL reader inside the harness.

    real_reader_probe.py [--workdir DIR]

Run it under the HARNESS interpreter (`$PLURAI_CHILD_PYTHON`, else `$PLURAI_PY`): it imports `simulate.py`, so every
patch and overlay the harness installs is in place, exactly as in a run.

The world's `mock_witness/mock_witness.json` lists, per pair whose reader is NOT mocked, a test with
`drive: real_reader` and a `probe` `{write_args, read_args, expect: [<value the write introduces>], expect_gone?:
[<value the write removes>], initial_state?, agent_context?}`. For each, on a fresh session of the deployed mock
(`.mock_service_url`): the reader is called through the harness's `probe_read(tool, args)` hook, the writer runs on the
mock, and the reader is called again. Every `expect` value must be visible after the write and not all before it; every
`expect_gone` value must be visible before the write and absent after it (a booked slot leaving an availability read, a
closed ticket leaving an open-ticket search: a write whose effect is a disappearance). A reader the harness serves from the mock
world passes; a real reader nothing overlays — the store the agent actually reads — fails, which is the
`writer_reader_mode_split` a world-state read cannot see (it proves only that the mock stored the write).

Writes `artifacts/real_reader_probe.json`
`{schema: plurai-real-reader-probe/1, env_id, pairs: [{test, write_tool, read_tool, status, reason}]}` with
`status ∈ {witnessed, failed, no_verdict, waived}`; `bench_manifest.py mock-gate` reads it. A pair with no `probe`, a
harness with no `probe_read`, or a call that raised is `no_verdict` — never a pass. A pair the world ships with
`waived: {because: no_reader, cite}` instead of a probe (its real reader shows none of the written fields) is
`waived` for THAT pair only: its writer is decided by its probed pairs, and a writer with none is never waived.

Exit codes: 0 every pair witnessed (or none to probe) · 1 a pair failed · 3 a pair reached no verdict · 2 usage.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import inspect
import json
import sys
import uuid
from pathlib import Path
from typing import Any

SCHEMA = 'plurai-real-reader-probe/1'
RESULT = Path('artifacts') / 'real_reader_probe.json'
WITNESS = Path('mock_witness') / 'mock_witness.json'
DRIVE = 'real_reader'
HOOK = 'probe_read'
WITNESSED, FAILED, NO_VERDICT, WAIVED = 'witnessed', 'failed', 'no_verdict', 'waived'
#: The one reason a real-reader pair may ship without a probe (C10 `waived.because`).
PAIR_WAIVER = 'no_reader'


def probe_pairs(workdir: Path) -> list[dict[str, Any]]:
    """The `drive: real_reader` tests of the world's witness manifest."""
    try:
        data = json.loads((workdir / WITNESS).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    tests = data.get('tests') if isinstance(data, dict) and data.get('schema') == 'plurai-mock-witness/1' else None
    return [t for t in tests or [] if isinstance(t, dict) and t.get('drive') == DRIVE]


def pair_waiver(pair: dict[str, Any]) -> dict[str, str] | None:
    """The pair's C10 waiver — `{because: no_reader, cite}` with a non-blank cite — or None."""
    waived = pair.get('waived')
    if isinstance(waived, dict) and waived.get('because') == PAIR_WAIVER and isinstance(waived.get('cite'), str) and waived['cite'].strip():
        return {'because': PAIR_WAIVER, 'cite': waived['cite'].strip()}
    return None


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)


def visible(expect: list[Any], value: Any) -> list[str]:
    """The `expect` values present in `value`'s text, as the agent would read it."""
    text = _text(value)
    return [str(e) for e in expect if str(e) in text]


def _call(hook: Any, tool: str, args: dict[str, Any]) -> Any:
    result = hook(tool, dict(args))
    return asyncio.run(result) if inspect.isawaitable(result) else result


def probe(pair: dict[str, Any], hook: Any, start_session: Any, execute: Any) -> dict[str, Any]:
    """One pair's verdict. `start_session(sid, initial_state, agent_context)` opens the mock session the harness reads;
    `execute(sid, tool, args)` runs the writer on the mock."""
    out = {k: pair.get(k) for k in ('test', 'write_tool', 'read_tool')}
    if waiver := pair_waiver(pair):
        return {**out, 'status': WAIVED, 'waiver': waiver, 'reason': waiver['cite']}
    spec = pair.get('probe')
    expect = spec.get('expect') if isinstance(spec, dict) and isinstance(spec.get('expect'), list) else []
    gone = spec.get('expect_gone') if isinstance(spec, dict) and isinstance(spec.get('expect_gone'), list) else []
    if not isinstance(spec, dict) or not (expect or gone):
        return {**out, 'status': NO_VERDICT, 'reason': 'the witness manifest carries no `probe` (write_args, read_args, expect / expect_gone) for this pair'}
    if hook is None:
        return {**out, 'status': NO_VERDICT, 'reason': f'simulate.py defines no `{HOOK}(tool, args)`, so the real reader cannot be called'}
    reader, read_args = str(pair['read_tool']), dict(spec.get('read_args') or {})
    try:
        sid = f'real-reader-probe-{uuid.uuid4().hex[:12]}'
        start_session(sid, spec.get('initial_state') or {}, spec.get('agent_context') or {})
        before = _call(hook, reader, read_args)
        execute(sid, str(pair['write_tool']), dict(spec.get('write_args') or {}))
        after = _call(hook, reader, read_args)
    except Exception as exc:  # noqa: BLE001 - any raise is a probe that reached no verdict, named
        return {**out, 'status': NO_VERDICT, 'reason': f'{type(exc).__name__}: {str(exc)[:300]}'}
    missing = [str(e) for e in expect if str(e) not in visible(expect, after)]
    if missing:
        return {**out, 'status': FAILED, 'reason': f'`{reader}` through the harness does not show what `{pair["write_tool"]}` wrote on the mock: '
                                                    f'{missing} absent from {_text(after)[:400]!r}'}
    if stale := visible(gone, after):
        return {**out, 'status': FAILED, 'reason': f'`{reader}` through the harness still shows what `{pair["write_tool"]}` removed on the mock: '
                                                    f'{stale} present in {_text(after)[:400]!r}'}
    if expect and len(visible(expect, before)) == len(expect):
        return {**out, 'status': NO_VERDICT, 'reason': 'every expected value was already visible before the write: expect a value the write introduces'}
    if unseen := [str(e) for e in gone if str(e) not in visible(gone, before)]:
        return {**out, 'status': NO_VERDICT, 'reason': f'{unseen} were not visible before the write, so their absence after it shows nothing: '
                                                        'expect_gone names a value the write removes'}
    return {**out, 'status': WITNESSED, 'reason': f'`{reader}` shows {expect} after the write' + (f' and no longer shows {gone}' if gone else '')}


def _harness(workdir: Path) -> Any:
    sys.path.insert(0, str(workdir))
    spec = importlib.util.spec_from_file_location('simulate', workdir / 'simulate.py')
    if spec is None or spec.loader is None:
        raise ImportError(f'no simulate.py in {workdir}')
    module = importlib.util.module_from_spec(spec)
    sys.modules['simulate'] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop('simulate', None)
        raise
    return module


def run(workdir: Path) -> tuple[int, dict[str, Any]]:
    pairs = probe_pairs(workdir)
    env_id = (workdir / '.env_id').read_text(encoding='utf-8').strip() if (workdir / '.env_id').is_file() else ''
    record: dict[str, Any] = {'schema': SCHEMA, 'env_id': env_id, 'pairs': []}
    if pairs:
        try:
            import plurai  # noqa: PLC0415 - the harness interpreter's SDK

            url = (workdir / '.mock_service_url').read_text(encoding='utf-8').strip()
            plurai.configure(url)
            client = plurai.MockServiceClient(base_url=url)
            hook = getattr(_harness(workdir), HOOK, None)
        except Exception as exc:  # noqa: BLE001 - a harness that cannot load decides no pair
            why = f'the harness could not be loaded against the mock: {type(exc).__name__}: {str(exc)[:300]}'
            record['pairs'] = [probe(p, None, None, None) if pair_waiver(p) else
                               {**{k: p.get(k) for k in ('test', 'write_tool', 'read_tool')}, 'status': NO_VERDICT, 'reason': why} for p in pairs]
        else:
            record['pairs'] = [probe(p, hook, plurai.start_session, lambda sid, tool, args: client.execute_mock(sid, tool, args, 0)) for p in pairs]
    statuses = {p['status'] for p in record['pairs']}
    return (1 if FAILED in statuses else 3 if NO_VERDICT in statuses else 0), record


def read_result(workdir: Path) -> dict[str, Any] | None:
    """The recorded probe, or None when absent or unreadable."""
    try:
        data = json.loads((workdir / RESULT).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get('schema') == SCHEMA else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workdir', default='.', help='the plurai_sim directory (default: the current one)')
    args = parser.parse_args(argv)
    workdir = Path(args.workdir).resolve()
    if not (workdir / 'simulate.py').is_file():
        print(f'real_reader_probe: no simulate.py in {workdir}', file=sys.stderr)
        return 2
    code, record = run(workdir)
    (workdir / RESULT).parent.mkdir(parents=True, exist_ok=True)
    (workdir / RESULT).write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    for pair in record['pairs']:
        print(f'{pair["status"].upper():<10} {pair["write_tool"]} -> {pair["read_tool"]}: {pair["reason"]}')
    print(f'real_reader_probe: {len(record["pairs"])} pair(s), exit {code}')
    return code


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
