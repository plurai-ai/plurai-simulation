#!/usr/bin/env python3
"""Stash every declared reference table and record the ``world_reference_rows`` a scenario call passes.

    world_reference_rows.py --workdir "$WORKDIR" --agent-root <root> --plurai-bin "$PLURAI_BIN"

A world type whose rows the agent only reads (a catalog, a code table) declares the file they are copied from:
``world_state.entity_types.<type>.reference_rows: <file>[#<list>]`` in ``plurai.config.yaml``. Each such file is stashed
here (``plurai stash-doc <file> reference_rows``; a ``.gz`` is decompressed first), and the entries
``{entity_type, input_doc_id, list?, path}`` are written (``path`` is the declared file without ``.gz``: its suffix
picks the backend's parser) to ``$WORKDIR/.world_reference_rows.json``. The scenario call
passes that list verbatim as ``world_reference_rows``; the backend then replaces each type's authored rows with the
real ones in every world.

Everything here is advisory. A file that cannot be read or stashed is a ``WARNING(world_reference_rows):`` line on
stderr and is left out, so its type's rows stay authored; the exit status is 0 whenever the arguments parse. The last
stdout line is ``WORLD_REFERENCE_ROWS=<json list>``, or ``WORLD_REFERENCE_ROWS=NONE`` when nothing was recorded, in
which case the marker is removed so an earlier build's ids are never passed for this one.

The one exception is a backend that refuses the SDK's stash (its version floor): the plugin's update text is printed
alone, nothing is recorded and the exit status is the plugin's refusal code (7), because every later call is refused too.
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills' / 'simulation-setup' / 'scripts'))

import _client_handshake  # noqa: E402 - sibling script: the one text a version refusal is shown as
from _stdio import utf8_child_text  # noqa: E402 - sibling script: UTF-8 children on Windows
from measured_candidates import reference_tables, yaml  # noqa: E402 - the one reader of the declaration

MARKER = '.world_reference_rows.json'
LABEL = 'reference_rows'
STASH_TIMEOUT_SECONDS = 300


def _warn(message: str) -> None:
    print(f'WARNING(world_reference_rows): {message}', file=sys.stderr)


def declared(config_path: Path) -> dict[tuple[str, str], str]:
    """``{(path, list): type}`` declared in the config, or ``{}`` when it cannot be read."""
    if yaml is None:
        _warn('PyYAML is not importable, so the config was not read')
        return {}
    try:
        return reference_tables(yaml.safe_load(config_path.read_text(encoding='utf-8', errors='replace')))
    except (OSError, yaml.YAMLError) as exc:
        _warn(f'could not read {config_path} ({type(exc).__name__}: {exc})')
        return {}


def _stash(plurai_bin: str, path: Path, scratch: Path) -> str | None:
    """The stash id of ``path``'s text, or None after a warning."""
    if path.name.endswith('.gz'):
        plain = scratch / path.name.removesuffix('.gz')
        try:
            with gzip.open(path, 'rb') as src, plain.open('wb') as dst:
                shutil.copyfileobj(src, dst)
        except (OSError, EOFError, zlib.error) as exc:
            _warn(f'could not decompress {path} ({exc})')
            return None
        path = plain
    out = scratch / f'{path.name}.id'
    try:
        done = subprocess.run(  # noqa: S603 - the resolved runner, given by the caller
            [plurai_bin, 'stash-doc', str(path), LABEL, '--out', str(out)],
            capture_output=True, text=True, **utf8_child_text(), timeout=STASH_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _warn(f'stash-doc did not run on {path.name} ({type(exc).__name__}: {exc})')
        return None
    _client_handshake.raise_if_refused(done)
    if done.stderr.strip():
        print(done.stderr.rstrip(), file=sys.stderr)
    doc_id = out.read_text(encoding='utf-8').strip() if done.returncode == 0 and out.is_file() else ''
    if not doc_id:
        _warn(f'stash-doc exited {done.returncode} on {path.name} and returned no id')
        return None
    return doc_id


def record(workdir: Path, agent_root: Path, plurai_bin: str) -> list[dict[str, str]]:
    """Stash each declared table and return the entries recorded in the marker (``[]`` removes it)."""
    marker = workdir / MARKER
    marker.unlink(missing_ok=True)  # first, so no failure below can leave an earlier build's ids to pass
    tables = declared(workdir / 'plurai.config.yaml')
    entries: list[dict[str, str]] = []
    if tables and not plurai_bin:
        _warn(f'no Python runner to stash with, so {len(tables)} reference table(s) were not sent; those types keep authored rows')
        tables = {}
    with tempfile.TemporaryDirectory(prefix='world_reference_rows_') as tmp:
        for i, ((rel, list_name), entity_type) in enumerate(tables.items()):
            path = (agent_root / rel).resolve()
            if not path.is_file():
                _warn(f'{entity_type}: reference_rows names {rel}, which is not a file under {agent_root}; its rows stay authored')
                continue
            scratch = Path(tmp) / str(i)
            scratch.mkdir()
            doc_id = _stash(plurai_bin, path, scratch)
            if doc_id is None:
                _warn(f'{entity_type}: {rel} was not stashed; its rows stay authored')
                continue
            entries.append({'entity_type': entity_type, 'input_doc_id': doc_id, **({'list': list_name} if list_name else {}), 'path': rel.removesuffix('.gz')})
    if entries:
        marker.write_text(json.dumps(entries, indent=2) + '\n', encoding='utf-8')
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--workdir', type=Path, required=True, help='the plurai_sim workdir holding plurai.config.yaml')
    parser.add_argument('--agent-root', type=Path, required=True, help="the agent's root; reference_rows paths are relative to it")
    parser.add_argument('--plurai-bin', default='', help='the resolved `plurai` runner (empty: nothing is stashed)')
    args = parser.parse_args(argv)

    def run() -> int:
        entries = record(args.workdir.resolve(), args.agent_root.resolve(), args.plurai_bin)
        print(f'WORLD_REFERENCE_ROWS={json.dumps(entries, separators=(",", ":")) if entries else "NONE"}')
        return 0

    return _client_handshake.refusal_main(run)


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
