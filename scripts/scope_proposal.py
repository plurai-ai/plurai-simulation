#!/usr/bin/env python3
"""The scope a gate's question proposes to leave out, typed, so the operator's answer backs exactly those items.

A scope approval (the topology-approval ask) used to approve "the map" — and the scan then waived every input and
switched every capability off on that one answer. This writes `<workdir>/artifacts/scope_proposal.json` before the ask:
the inputs it proposes not to model, the switches it proposes to leave off and the tools those switches hold. The ask
hook (`hooks/ask_answered.py`) copies the items onto the recorded answer only when the answer approves, and
`bench_manifest.py validate` accepts an `operator_answer:<gate>` waiver or a capability override for a named item only.

A content part the agent takes (an image, a file) is never proposable: its kind is modelled (`input_scope_cut`). Only a
gate whose question carries a scope (`hooks/_run_driver.py` SCOPE_GATES) takes a proposal: a prerequisite such as `blocker`
never approves a cut. When that gate takes its default (a headless run driver, a live `/simulate` run) the default backs the
same items, so the proposal is written before the gate is sanctioned either way.

    scope_proposal.py write [--workdir <plurai_sim>] [--gate topology-approval] [--input NAME]... [--switch NAME]... [--tool NAME]...

Prints the sentence to fold into the question, in the customer's words. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_inventory  # noqa: E402

_HOOKS = str(Path(__file__).resolve().parent.parent / 'hooks')
if _HOOKS not in sys.path:
    sys.path.append(_HOOKS)
from _run_driver import SCOPE_GATES  # noqa: E402 - the one declaration of the gates that carry a scope

FILENAME = 'scope_proposal.json'
DEFAULT_GATE = 'topology-approval'
#: Content the user hands the agent: an `agent.io` kind the harness models, never one a scope approval drops.
CONTENT_KINDS = ('text', 'image', 'file_content_part', 'file_by_path', 'file_by_reference')


def _item_problems(inventory: Dict[str, Any], inputs: List[str], switches: List[str], tools: List[str]) -> List[Tuple[str, str, str]]:
    """``(key, name, why)`` for each proposed item the agent inventory refuses."""
    kinds: Dict[Any, set] = {}
    for e in inventory.get('inputs') or []:
        if isinstance(e, dict):
            kinds.setdefault(e.get('name'), set()).add(e.get('kind'))
    production = inventory.get('production_tools') if isinstance(inventory.get('production_tools'), dict) else {}
    known_switches = {s.get('switch') for s in production.get('switches') or [] if isinstance(s, dict)}
    problems = [('inputs', n, f'input `{n}` is no input the agent inventory found') for n in inputs if n not in kinds]
    # A name every reading of which is content: a name shared with another kind is admitted, and its content entry is
    # still refused where it is waived (`bench_manifest._waiver_problem`).
    problems += [('inputs', n, f'input `{n}` is a {"/".join(sorted(kinds[n]))} the agent takes: its kind is modelled, never scoped out')
                 for n in inputs if n in kinds and kinds[n] <= set(CONTENT_KINDS)]
    problems += [('switches', n, f'switch `{n}` is no switch the agent inventory found') for n in switches if n not in known_switches]
    problems += [('tools', n, f'tool `{n}` is no production tool') for n in tools if n not in set(production.get('names') or [])]
    return problems


def proposal_problems(inventory: Dict[str, Any], inputs: List[str], switches: List[str], tools: List[str]) -> List[str]:
    """Why the proposal cannot be asked: an item the reader never found, or an input whose kind must be modelled."""
    return [why for _, _, why in _item_problems(inventory, inputs, switches, tools)]


def admitted(inventory: Dict[str, Any], scope: Dict[str, Any]) -> Dict[str, List[str]]:
    """``scope``'s typed items less every one this script would have refused: what a scope record may back. The record's
    items come from `artifacts/scope_proposal.json`, which nothing stops being written by hand, so the validator re-checks
    them against the same inventory (`bench_manifest.run_driver_record`)."""
    lists = {key: [str(n) for n in scope.get(key) or []] if isinstance(scope.get(key), list) else [] for key in ('inputs', 'switches', 'tools')}
    refused = {(key, name) for key, name, _ in _item_problems(inventory, lists['inputs'], lists['switches'], lists['tools'])}
    return {key: [n for n in names if (key, n) not in refused] for key, names in lists.items()}


def sentence(inputs: List[str], tools: List[str]) -> str:
    """The list the question carries, worded for the customer."""
    parts = []
    if inputs:
        parts.append('inputs your agent accepts that this simulation will not send: ' + ', '.join(f'`{n}`' for n in inputs))
    if tools:
        parts.append(f'tools that stay switched off ({len(tools)}): ' + ', '.join(f'`{n}`' for n in tools))
    return ('Approving this map also confirms what the simulation leaves out — ' + '; '.join(parts) + '.') if parts else ''


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='Write the typed scope a gate question proposes to leave out.')
    sub = parser.add_subparsers(dest='cmd', required=True)
    write = sub.add_parser('write')
    write.add_argument('--workdir', help='the plurai_sim workdir (default: ./plurai_sim, else .)')
    write.add_argument('--gate', default=DEFAULT_GATE)
    write.add_argument('--input', action='append', default=[], dest='inputs')
    write.add_argument('--switch', action='append', default=[], dest='switches')
    write.add_argument('--tool', action='append', default=[], dest='tools')
    args = parser.parse_args(argv)
    if args.gate not in SCOPE_GATES:
        print(f'scope_proposal: refused — `{args.gate}` is no gate whose question carries a scope; only '
              f'{", ".join(sorted(SCOPE_GATES))} does (its approval, or its default when nobody is asked, backs the items named '
              'here). Never move a cut onto another gate: write the proposal for that gate before sanctioning it, or model the item.',
              file=sys.stderr)
        return 2
    workdir = Path(args.workdir).resolve() if args.workdir else (Path.cwd() / 'plurai_sim' if (Path.cwd() / 'plurai_sim').is_dir() else Path.cwd())
    inventory = agent_inventory.read_artifact(workdir) or {}
    if problems := proposal_problems(inventory, args.inputs, args.switches, args.tools):
        print('scope_proposal: refused — ' + '; '.join(problems), file=sys.stderr)
        return 2
    target = workdir / 'artifacts' / FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({'gate': args.gate, 'inputs': args.inputs, 'switches': args.switches, 'tools': args.tools},
                                 indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(sentence(args.inputs, args.tools))
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
