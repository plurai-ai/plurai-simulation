#!/usr/bin/env python3
"""Say what kind of Salesforce agent a project holds, from its metadata layout alone.

    agent_kind.py [--project-dir <agent root>] [--flow-dir "$FLOWDIR" [--art "$ART"]]

The hosted route rewires an Agent Script bundle (`aiAuthoringBundles/<name>/<name>.agent`) and
nothing else. An agent built in Agent Builder is defined instead by Bot / BotVersion and planner
metadata (GenAiPlannerBundle, or the older GenAiPlanner), and no Agent Script bundle exists for it
in the org, so asking for one to be retrieved is a request that can never be met. This script tells
the cases apart before the route starts:

    agent_script    an Agent Script bundle is present (it always wins: a publish leaves Bot and
                    planner metadata beside the bundle, and that is still an Agent Script agent)
    builder         no bundle, and the project defines an agent with Bot or planner metadata
    no_agent        a DX project with neither (actions alone, GenAiPlugin / GenAiFunction, define
                    no agent)
    not_salesforce  no `sfdx-project.json` at the root or one directory below it

Only metadata types and the DX file layout decide; no agent, topic or action name is read. Both the
source format (`<name>.bot-meta.xml`) and the metadata-API format (`<name>.bot`) count.

Prints one JSON object. With `--flow-dir`, a `builder` verdict also files ONE typed
`run_driver_input_required` row (gate `unsupported-agent-kind`) into the flow dir and, when its
workdir exists, `--art`, so a headless scan ends on a stated reason rather than an idle phase.

Exit 0 — proceed with the existing route rules for the printed kind.
Exit 3 — `builder`: stop the setup and show `message` to the customer.
Exit 2 — the project directory does not exist.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DX_MANIFEST = 'sfdx-project.json'
# Directories that never hold the customer's metadata: tool state, dependencies, and the
# simulation's own workdir.
SKIP_DIRS = frozenset({'node_modules', 'plurai_sim', '__pycache__', 'venv'})
# The metadata root the rewire generates its copy into (`rewire_agentforce.Paths.under`).
GENERATED_ROOT = 'generated'

AGENT_SCRIPT = 'agent_script'
BUILDER = 'builder'
NO_AGENT = 'no_agent'
NOT_SALESFORCE = 'not_salesforce'

GATE = 'unsupported-agent-kind'
REASON = 'builder_agent'
EXIT_REFUSE = 3

# (metadata type, the folder it lives under, its file suffix). The folder must be an ancestor of
# the file; the suffix matches with or without the source format's `-meta.xml`.
_LAYOUT: tuple[tuple[str, str, str], ...] = (
    ('Bot', 'bots', 'bot'),
    ('BotVersion', 'bots', 'botVersion'),
    ('GenAiPlannerBundle', 'genAiPlannerBundles', 'genAiPlannerBundle'),
    ('GenAiPlanner', 'genAiPlanners', 'genAiPlanner'),
    ('GenAiPlugin', 'genAiPlugins', 'genAiPlugin'),
    ('GenAiFunction', 'genAiFunctions', 'genAiFunction'),
)
#: The types that define an agent. GenAiPlugin and GenAiFunction are its topics and actions, and
#: exist in a project that holds no agent definition at all.
DEFINING_TYPES = ('Bot', 'BotVersion', 'GenAiPlannerBundle', 'GenAiPlanner')
_SUFFIX = {kind: re.compile(rf'\.{re.escape(suffix)}(-meta\.xml)?$') for kind, _, suffix in _LAYOUT}
_FOLDER = {kind: folder for kind, folder, _ in _LAYOUT}

MESSAGE = (
    'This agent was built in Agent Builder (the project holds its {types} metadata), not written in Agent Script. '
    "Plurai's simulation supports Agent Script agents only. "
    "To simulate it, upgrade the agent to the new Agentforce Builder (Setup → Agentforce Agents → the agent's menu → Upgrade to New Builder), "
    "retrieve the upgraded agent's Agent Script bundle into this project, and run the scan again."
)
NEEDS = (
    'an Agent Script version of the agent: upgrade it to the new Agentforce Builder (Setup → Agentforce Agents → Upgrade to New Builder), '
    "retrieve the upgraded agent's Agent Script bundle into the project, and scan again"
)


@dataclass(frozen=True)
class Verdict:
    kind: str
    bundles: tuple[str, ...] = ()
    metadata: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def supported(self) -> bool:
        return self.kind != BUILDER

    @property
    def types(self) -> tuple[str, ...]:
        return tuple(kind for kind, _, _ in _LAYOUT if self.metadata.get(kind))

    def message(self) -> str | None:
        return MESSAGE.format(types=', '.join(self.types)) if self.kind == BUILDER else None

    def as_json(self) -> dict[str, object]:
        out: dict[str, object] = {'kind': self.kind, 'supported': self.supported}
        if self.kind == BUILDER:
            out['reason'] = REASON
            out['message'] = self.message()
        out['agent_script_bundles'] = list(self.bundles)
        out['metadata_types'] = {kind: len(self.metadata[kind]) for kind in self.types}
        return out


def dx_roots(project: Path) -> list[Path]:
    """The directories holding `sfdx-project.json`: the project root, else those one directory below it."""
    if (project / DX_MANIFEST).is_file():
        return [project]  # its walk already covers every directory below it
    candidates = []
    if project.is_dir():
        candidates += [child for child in sorted(project.iterdir()) if child.is_dir() and not _skipped(child.name)]
    return [candidate for candidate in candidates if (candidate / DX_MANIFEST).is_file()]


def _skipped(name: str) -> bool:
    return name.startswith('.') or name in SKIP_DIRS


def _walk(root: Path):
    for here, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not _skipped(d))
        yield Path(here), sorted(files)


def _metadata_type(relative_parts: tuple[str, ...]) -> str | None:
    name, folders = relative_parts[-1], relative_parts[:-1]
    for kind, pattern in _SUFFIX.items():
        if pattern.search(name) and _FOLDER[kind] in folders:
            return kind
    return None


def classify(project: Path) -> Verdict:
    """The project's agent kind (see the module docstring), read from file layout alone."""
    roots = dx_roots(project)
    if not roots:
        return Verdict(NOT_SALESFORCE)
    bundles: list[str] = []
    metadata: dict[str, list[str]] = {}
    for root in roots:
        for here, files in _walk(root):
            for name in files:
                path = here / name
                relative = path.relative_to(project).as_posix()
                parts = path.relative_to(root).parts
                # An Agent Script bundle: aiAuthoringBundles/<bundle>/<file>.agent. The rewired copy
                # `rewire_agentforce.py` writes under `<package dir>/generated/` is ours, not the customer's.
                if name.endswith('.agent') and len(parts) >= 3 and parts[-3] == 'aiAuthoringBundles':
                    if len(parts) < 4 or parts[-4] != GENERATED_ROOT:
                        bundles.append(relative)
                    continue
                kind = _metadata_type(parts)
                if kind is not None:
                    metadata.setdefault(kind, []).append(relative)
    frozen = {kind: tuple(paths) for kind, paths in metadata.items()}
    if bundles:
        kind = AGENT_SCRIPT
    elif any(frozen.get(defining) for defining in DEFINING_TYPES):
        kind = BUILDER
    else:
        kind = NO_AGENT
    return Verdict(kind, tuple(bundles), frozen)


def record_refusal(verdict: Verdict, *, flow_dir: Path, art: Path | None) -> tuple[bool, dict[str, str]]:
    """File the typed stop through the operator-findings writer.

    Returns whether the session copy (the one a headless driver reads) landed, and ``{base: why it
    did not land}`` for every base that missed.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'hooks'))
    import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path only here

    command = _run_driver.flow_command([flow_dir])
    _, unrecorded = _run_driver.record_durably(
        _run_driver.INPUT_REQUIRED,
        session_base=flow_dir,
        art=art,
        gate=GATE,
        command=command,
        needs=NEEDS,
        reason=REASON,
        metadata_types=list(verdict.types),
    )
    return _run_driver.SESSION_BASE not in unrecorded, unrecorded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        '--project-dir',
        type=Path,
        default=None,
        help='the agent root (default: the working directory)',
    )
    parser.add_argument(
        '--flow-dir',
        type=Path,
        default=None,
        help='$FLOWDIR: file the typed stop here on a builder verdict',
    )
    parser.add_argument(
        '--art',
        default='',
        help='$ART: the durable copy of that stop, written only when its workdir exists',
    )
    args = parser.parse_args(argv)
    project = (args.project_dir or Path.cwd()).resolve()
    if not project.is_dir():
        print(f'agent_kind: {project} is not a directory', file=sys.stderr)
        return 2
    verdict = classify(project)
    out = verdict.as_json()
    if verdict.kind == BUILDER and args.flow_dir is not None:
        # An empty `$ART` (no workdir resolved) is no base at all, never the working directory.
        art = Path(args.art) if args.art.strip() else None
        recorded, unrecorded = record_refusal(verdict, flow_dir=args.flow_dir, art=art)
        out['recorded'] = recorded
        if unrecorded:
            out['not_recorded'] = unrecorded
    print(json.dumps(out, ensure_ascii=False))
    return EXIT_REFUSE if verdict.kind == BUILDER else 0


if __name__ == '__main__':
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
