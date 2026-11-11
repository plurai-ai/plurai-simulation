#!/usr/bin/env python3
"""What the final draw must carry so the qualification's coverage holes close INSIDE the loop.

    coverage_closure.py --diagnostics <job_status.json> --scenario-set-id <id> --topology <agent_topology.json>
                        --policies <policies_config.yaml>

Reads the finished ``suite_diagnostics`` job of the pass that judged the CURRENT (qualification) set, the
scan's map and the current policies mirror, and prints ONE JSON object for the final ``generate_scenarios_start``
call:

* ``steering_demands`` — typed demands (``flow_walked``, ``complication_dealt``), appended to the batch's own;
* ``interest`` — prose lines for what no typed demand can carry, appended to ``interest``;
* ``eval_asks`` — scope declarations for ONE non-additive ``/edit-evals`` session run BEFORE the draw, so
  the eval-set demands they add are seated by the same draw;
* ``basis`` — the rows each entry was derived from, for the loop log.

The holes it closes, each keyed on a typed record, never on a name:

* **An acting tool the qualification never called.** A map tool typed ``writes: world`` / ``live`` (it
  writes, deletes, sends, pays or grants) on the set's ``tool_coverage.uncovered``. The first
  :data:`MAX_TOOL_DEMANDS` of them, live writers first, are carried: each gets an ``interest`` line naming
  the step, and a flow that lists it is demanded (``walked_never_called`` / ``only_unwalked_flows``). A tool
  in no flow (``in_no_flow``) gets only the line, since no flow can be demanded for it. The rest are on
  ``basis`` with ``carried: false`` — priority slots, never the whole batch of seven.
* **A complication the plan withheld on every run** (``complication_enactment``: no run ``enacted`` it, some
  ``withheld`` it) is dealt again: ``complication_dealt``, at most :data:`MAX_TOOL_DEMANDS`.
* **A complication no eval grades** (every row's ``graded_by`` empty, and no policy in the CURRENT mirror
  declares it) is an ``eval_asks`` row: declare it on the judge that grades the situation it creates.
* **An eval that almost never applies** — a ``coverage.by_policy`` row (severity above ``low``) that applied
  to under half of its scenarios (the rest ``not_applicable`` / ``not_scored``), whose policy declares no
  premise (not ``premise_bound``; no ``target_complications`` / ``target_flows`` / ``current_elements`` guard):
  an ``eval_asks`` row to declare the premise it grades, so the draw seats it. A policy that declares one is
  already seated by the draw (``slot_plan.eval_set_demands``).

Never on the customer report: the closure is loop input, and its record is the loop log.

Exit codes: 0 written (an empty closure is a closure); 2 an input unreadable, not a ``done``
``suite_diagnostics`` job, or a job about another scenario set (nothing written).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA = 'coverage-closure-v1'
#: The causes the backend's coverage-gap computation writes on a ``tool_coverage.uncovered`` row.
CAUSE_IN_NO_FLOW, CAUSE_ONLY_UNWALKED, CAUSE_WALKED_NEVER_CALLED = 'in_no_flow', 'only_unwalked_flows', 'walked_never_called'
#: The map's ``writes`` values that make a tool ACT (``host_effects.typed_writes``): the simulated world, or a live system.
ACTING_WRITES = ('live', 'world')
#: How many never-called acting tools one final draw carries: priority slots, never the whole batch of seven.
MAX_TOOL_DEMANDS = 3
#: An eval that applied to under this share of the scenarios it was asked about almost never applies.
APPLIES_FLOOR = 0.5
#: The ``unmeasured`` tokens that mean the eval's premise never arose (a judge that ran and hedged or errored DID apply).
NOT_APPLIED_TOKENS = ('not_applicable', 'not_scored')
#: Severities whose rarely-applying eval is not worth a scope edit.
SKIPPED_SEVERITIES = ('low',)
#: The backend-owned check: derived from the config, never the author's to scope.
BACKEND_OWNED = ('calls_only_available_functions',)
#: A ``current_elements`` membership in a guard — the inferred arming channel ``slot_plan`` reads.
_GUARD_MEMBER = re.compile(r"['\"]([^'\"]+)['\"]\s+in\s+current_elements")


class Refused(Exception):
    """An input the closure cannot be computed from."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise Refused(f'{path} is unreadable ({type(exc).__name__}: {exc})') from exc


def diagnostics_detail(envelope: Any, scenario_set_id: str) -> dict[str, Any]:
    """The ``detail`` of a ``done`` ``suite_diagnostics`` job about ``scenario_set_id``; refused otherwise."""
    if not isinstance(envelope, dict) or not isinstance(envelope.get('detail'), dict):
        raise Refused('the diagnostics file is not a job_status envelope with a `detail`')
    if envelope.get('status') not in (None, 'done'):
        raise Refused(f'the diagnostics job is `{envelope.get("status")}`, not `done`')
    detail = envelope['detail']
    theirs, ours = str(detail.get('scenario_set_id') or '').strip(), str(scenario_set_id or '').strip()
    if not theirs or not ours or theirs != ours:
        raise Refused(f'the diagnostics job is about set {theirs or "<none>"!r}, not the current set {ours or "<none>"!r}')
    return detail


def acting_tools(topology: Any) -> dict[str, str]:
    """``{tool: writes}`` for every map tool typed to act (:data:`ACTING_WRITES`)."""
    tools = topology.get('tools') if isinstance(topology, dict) else None
    rows = [t for t in tools if isinstance(t, dict)] if isinstance(tools, list) else []
    return {str(t['name']): str(t['writes']) for t in rows if t.get('name') and t.get('writes') in ACTING_WRITES}


def tool_closure(tool_coverage: Any, acting: dict[str, str]) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """``(steering_demands, interest, basis)`` for the acting tools the set's run never called."""
    block = tool_coverage if isinstance(tool_coverage, dict) else {}
    ranks = block.get('start_ranks') if isinstance(block.get('start_ranks'), dict) else {}
    rows = [r for r in block.get('uncovered') or [] if isinstance(r, dict) and str(r.get('tool') or '') in acting]
    # Live writers first (a real side effect), then by name: the cap keeps the riskiest.
    rows.sort(key=lambda r: (acting[str(r['tool'])] != 'live', str(r['tool'])))
    demands: list[dict[str, Any]] = []
    interest: list[str] = []
    basis: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        tool, cause = str(row['tool']), str(row.get('cause') or '')
        carried = index < MAX_TOOL_DEMANDS
        flows = sorted((str(f) for f in row.get('flows') or [] if str(f).strip()), key=lambda f: (ranks.get(f, len(ranks) + 1), f))
        flow = flows[0] if carried and flows and cause in (CAUSE_WALKED_NEVER_CALLED, CAUSE_ONLY_UNWALKED) else None
        if flow is not None and all(d['subject'] != flow for d in demands):
            demands.append({'kind': 'flow_walked', 'subject': flow, 'slots': 1})
        if carried:
            where = f' on flow `{flow}`' if flow else ''
            interest.append(f'one scenario{where} whose user asks for the step `{tool}` performs, so the agent must decide whether and how to take it')
        basis.append({'tool': tool, 'writes': acting[tool], 'cause': cause, 'flow': flow, 'carried': carried})
    return demands, interest, basis


def declared_complications(policies: Any) -> set[str]:
    """Every complication a policy in the mirror declares: ``target_complications``, or a ``current_elements`` guard."""
    out: set[str] = set()
    for body in policies.values() if isinstance(policies, dict) else ():
        if isinstance(body, dict):
            out.update(str(c).strip() for c in body.get('target_complications') or [] if str(c).strip())
            out.update(_GUARD_MEMBER.findall(str(body.get('applicability_guard') or '')))
    return out


def _declares_premise(body: Any) -> bool:
    return isinstance(body, dict) and bool(
        body.get('target_complications') or body.get('target_flows') or _GUARD_MEMBER.search(str(body.get('applicability_guard') or ''))
    )


def complication_closure(rows: Any, policies: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """``(steering_demands, eval_asks, basis)`` from the run's ``complication_enactment`` rows."""
    outcomes: dict[str, dict[str, int]] = {}
    graded: dict[str, bool] = {}
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, dict) or not str(row.get('complication') or '').strip():
            continue
        name = str(row['complication']).strip()
        counts = outcomes.setdefault(name, {})
        for outcome, n in (row.get('outcomes') or {}).items():
            counts[str(outcome)] = counts.get(str(outcome), 0) + int(n or 0)
        graded[name] = graded.get(name, False) or bool(row.get('graded_by'))
    declared = declared_complications(policies)
    demands: list[dict[str, Any]] = []
    asks: list[dict[str, Any]] = []
    basis: list[dict[str, Any]] = []
    for name in sorted(outcomes):
        counts = outcomes[name]
        withheld = not counts.get('enacted') and bool(counts.get('withheld'))
        ungraded = not graded[name] and name not in declared
        if withheld and len(demands) < MAX_TOOL_DEMANDS:
            demands.append({'kind': 'complication_dealt', 'subject': name, 'slots': 1})
        if ungraded:
            asks.append({
                'kind': 'declare_grader',
                'subject': name,
                'ask': f'no eval grades `{name}`: declare it in the `target_complications` of the judge whose rubric grades the '
                       'situation it creates, extending that rubric to name it when none does',
            })
        if withheld or ungraded:
            basis.append({'complication': name, 'outcomes': counts, 'withheld': withheld, 'ungraded': ungraded})
    return demands, asks, basis


def applicability_asks(coverage: Any, policies: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(eval_asks, basis)`` for each eval that applied to under :data:`APPLIES_FLOOR` of its scenarios and declares no premise."""
    rows = coverage.get('by_policy') if isinstance(coverage, dict) else None
    bodies = policies if isinstance(policies, dict) else {}
    asks: list[dict[str, Any]] = []
    basis: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, dict):
            continue
        name = str(row.get('policy') or '')
        unmeasured = row.get('unmeasured') if isinstance(row.get('unmeasured'), dict) else {}
        asked = int(row.get('scenarios') or 0) + int(row.get('abstained') or 0)
        applied = asked - sum(int(unmeasured.get(token) or 0) for token in NOT_APPLIED_TOKENS)
        if (not name or name in BACKEND_OWNED or row.get('severity') in SKIPPED_SEVERITIES or row.get('premise_bound') or name not in bodies
                or not asked or applied >= APPLIES_FLOOR * asked or _declares_premise(bodies[name])):
            continue
        asks.append({
            'kind': 'declare_premise',
            'subject': name,
            'ask': f'`{name}` applied to {applied} of {asked} scenario(s) and declares no premise: declare in its `target_complications` '
                   '(or `target_flows`) the complication or flow that sets up what it grades, so the draw seats it',
        })
        basis.append({'policy': name, 'applied': applied, 'asked': asked})
    return asks, basis


def closure(detail: dict[str, Any], topology: Any, policies: Any) -> dict[str, Any]:
    demands, interest, tool_basis = tool_closure(detail.get('tool_coverage'), acting_tools(topology))
    dealt, grader_asks, complication_basis = complication_closure(detail.get('complication_enactment'), policies)
    premise_asks, eval_basis = applicability_asks(detail.get('coverage'), policies)
    return {
        'schema': SCHEMA,
        'scenario_set_id': detail.get('scenario_set_id'),
        'steering_demands': [*demands, *dealt],
        'interest': interest,
        'eval_asks': [*grader_asks, *premise_asks],
        'basis': {'tools': tool_basis, 'complications': complication_basis, 'evals': eval_basis},
    }


def read_policies(path: Path) -> dict[str, Any]:
    """The policies mirror (``{name: body}``); refused when unreadable."""
    import yaml  # noqa: PLC0415 - the one PyYAML read; the caller runs under $PLURAI_PY

    try:
        loaded = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError) as exc:
        raise Refused(f'{path} is unreadable ({type(exc).__name__})') from exc
    if not isinstance(loaded, dict):
        raise Refused(f'{path} is not a mapping of policies')
    return loaded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n', 1)[0])
    parser.add_argument('--diagnostics', required=True, type=Path, help="the qualification suite_diagnostics job's job_status JSON")
    parser.add_argument('--scenario-set-id', required=True, help='the CURRENT .scenario_set_id the diagnostics must be about')
    parser.add_argument('--topology', required=True, type=Path, help='$ART/agent_topology.json')
    parser.add_argument('--policies', required=True, type=Path, help='$WORKDIR/policies_config.yaml, the CURRENT mirror')
    args = parser.parse_args(argv)
    try:
        detail = diagnostics_detail(_read_json(args.diagnostics), args.scenario_set_id)
        topology = _read_json(args.topology)
        policies = read_policies(args.policies)
    except Refused as exc:
        print(f'coverage_closure: {exc} — no closure computed', file=sys.stderr)
        return 2
    print(json.dumps(closure(detail, topology, policies), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
