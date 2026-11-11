#!/usr/bin/env python3
"""The bookkeeping `/analyze-traces` needs around delivering one trace part.

The skill exports the traces with `plurai ingest-traces`, hands the printed part entry to the backend's
`ground_workspace_start`, and reads the result back from the grounding mirror. Three small steps sit between
those calls, and this file holds them so no skill retypes them:

    trace_delivery.py names    --workdir D --out F     # write the names the agent descriptions declare, as a JSON list
    trace_delivery.py entry    --entry F               # the entry `ingest-traces` printed, checked, as the `added` argument
    trace_delivery.py evidence --workdir D             # the tools the mirror holds trace evidence for

`names` feeds `--declared-names`: under anonymized posture a field whose name a description declares keeps its
name through the scrub, so the traces can join the declared keys. The names come from the declared part the
scan built (`artifacts/declared_part.json`); a workdir without one is read from its descriptions directly.
Only tool and entity keys name trace fields, so profile, complication and agent-fact keys are left out.

`entry` refuses what the backend would refuse (a part with no posture, a fingerprint on a raw part, none on an
anonymized one), so the failure is stated before a tool call is spent. `evidence` reads the mirror and never
writes it.

Exit codes: 0 done; 2 nothing to build from (`names`); 3 a check failed, stated on stderr, nothing written.
The script is stdlib plus PyYAML, on the Python 3.9 floor.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))

import declared_part as dp  # noqa: E402 - sibling of the plugin's scripts/: the declared part and its refusal causes
import grounding_files  # noqa: E402 - the part-entry check and the atomic write
import grounding_keys as gk  # noqa: E402 - the key grammar the names are read with

MIRROR_NAME = 'grounding_ledger.yaml'
TRACE_KIND = 'trace_archive'
#: The kinds whose names occur in a trace as a tool, an argument or a field.
NAMING_KINDS = ('tool', 'entity')



def _fail(message: str) -> int:
    print(f'trace_delivery: {message}', file=sys.stderr)
    return 3


# --- names -------------------------------------------------------------------------------------------


def declared_names(keys: list) -> list:
    """The sorted, distinct names the tool and entity keys carry: the key's own name and every segment name."""
    names = set()
    for text in keys:
        try:
            key = gk.parse_key(text)
        except gk.KeyGrammarError:
            continue
        if key.kind not in NAMING_KINDS:
            continue
        names.add(key.name)
        names.update(part for segment in key.segments for part in segment[1:])
    return sorted(names)


def _part_keys(workdir: Path) -> Optional[list]:
    """The keys of the built declared part, else of one built from the descriptions; None when there is nothing."""
    path = workdir / 'artifacts' / grounding_files.DECLARED_PART_NAME
    if path.is_file():
        part = json.loads(path.read_text(encoding='utf-8'))
    else:
        texts = dp.read_workdir(workdir)
        if not texts:
            return None
        part = dp.build(**texts)
    return [entry['key'] for entry in part['entries']]


def _cmd_names(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    try:
        keys = _part_keys(workdir)
    except dp.DescriptionRefused as err:
        return _fail(f'a description is refused ({err.cause}): {err}')
    except (OSError, ValueError, KeyError, TypeError) as err:
        return _fail(f'{grounding_files.DECLARED_PART_NAME} cannot be read ({type(err).__name__}); build the declared part again')
    if keys is None:
        print(f'trace_delivery: none of the agent descriptions exists in {workdir}; nothing declares a name.', file=sys.stderr)
        return 2
    names = declared_names(keys)
    grounding_files.write_atomic(Path(args.out), json.dumps(names, ensure_ascii=False) + '\n')
    print(json.dumps({'names': len(names), 'path': args.out}, ensure_ascii=False))
    return 0


# --- the printed entry -------------------------------------------------------------------------------


def _check_entry(entry: Any) -> Optional[str]:
    """The cause a printed trace part entry is not deliverable, or None."""
    return grounding_files.check_part_entry(entry, TRACE_KIND)


def _cmd_entry(args: argparse.Namespace) -> int:
    try:
        entry = json.loads(Path(args.entry).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return _fail(f'{args.entry} is not the JSON object `plurai ingest-traces` prints; the delivery did not complete.')
    problem = _check_entry(entry)
    if problem is not None:
        return _fail(f'the part entry {problem}; nothing was delivered.')
    print(json.dumps([entry], separators=(',', ':'), ensure_ascii=False))
    return 0


# --- the mirror's trace evidence ---------------------------------------------------------------------


def trace_tools(document: Any) -> list:
    """The sorted tool names with at least one trace-tier evidence item on any of their keys."""
    entries = document.get('entries') if isinstance(document, dict) else None
    found = set()
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get('key'), str):
            continue
        evidence = entry.get('evidence')
        if not isinstance(evidence, list) or not any(isinstance(item, dict) and item.get('tier') == 'trace' for item in evidence):
            continue
        try:
            key = gk.parse_key(entry['key'])
        except gk.KeyGrammarError:
            continue
        if key.kind == 'tool':
            found.add(key.name)
    return sorted(found)


def _cmd_evidence(args: argparse.Namespace) -> int:
    import yaml

    path = Path(args.workdir) / MIRROR_NAME
    if not path.is_file():
        print(json.dumps({'mirror': False, 'trace_tools': 0, 'tools': []}))
        return 0
    try:
        document = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return _fail(f'{MIRROR_NAME} cannot be read; pull the mirror again.')
    tools = trace_tools(document)
    settled = document.get('settled') if isinstance(document, dict) else None
    print(json.dumps({'mirror': True, 'settled': settled is True, 'trace_tools': len(tools), 'tools': tools}, ensure_ascii=False))
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Bookkeeping for /analyze-traces' trace part delivery.")
    sub = parser.add_subparsers(dest='command', required=True)
    names = sub.add_parser('names', help='write the names the agent descriptions declare, as a JSON list')
    names.add_argument('--workdir', required=True)
    names.add_argument('--out', required=True, help='write the JSON list of names here')
    names.set_defaults(func=_cmd_names)
    entry = sub.add_parser('entry', help='check the entry `plurai ingest-traces` printed and print it as the `added` argument')
    entry.add_argument('--entry', required=True, help='the file the printed object was saved to')
    entry.set_defaults(func=_cmd_entry)
    evidence = sub.add_parser('evidence', help='the tools the grounding mirror holds trace evidence for')
    evidence.add_argument('--workdir', required=True)
    evidence.set_defaults(func=_cmd_evidence)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
