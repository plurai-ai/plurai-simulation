#!/usr/bin/env python3
"""Standalone verification of scenario_variables.yaml.

Self-contained: no backend dependency, no jsonschema dependency. Uses only the
standard library plus pyyaml (which any project producing YAML already has).

This script IS the plugin's contract. The extract-scenario-variables skill
must run it before declaring success. Its ERRORS (exit 2) are exactly what the
vendored schema (`references/scenario_variables_schema.json`) or the backend's
loader refuses:

1. Structure: `agent_purpose`, `agent_domain` and `world_grounding` present and
   typed, every list a list, every object item carrying its schema-required
   sub-keys as non-empty strings, enums on their closed sets.
2. Cross-field coherence: `world_grounding` must agree with `agent_tools[].reads`
   — the same rule the backend enforces at load (a hard validation error there
   too), caught here first so the skill can fix it locally.
3. Dispatched skill names: an `agent_tools` name must be a `tools:` key or a scan-topology skill
   name, the exact id the dispatch call carries (`dispatched_skill_names`).
4. Pasted prompts: a hint field must not equal, or repeat many of the lines of, a raw prompt file the
   agent loads, in any order and whatever the blank lines (`pasted_prompt_hints`).

Exit 0 on success, 2 on errors. Errors are human-readable and name the
offending field so the skill can re-prompt specifically.

Beside the errors is an ADVISORY tier (`advisories`, printed as `⚠`), for shapes the backend
loads that degrade what it can generate. Advisories never move the exit code — exit 0 can
still carry findings — so a lexical read of prose can report without refusing. FULL
POPULATION lives here: a list the backend defaults to empty, a defaulted string left blank, a
blank entry in a typed list — an agent with no such content (a pipeline with no user
archetypes) is told, never made to fabricate one. So does the evaluator-voice archetype.

Usage:
    python verify.py /path/to/scenario_variables.yaml
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    # Name the OFFENDING interpreter, not a bare `pip install`: this script is run with
    # whatever python the caller reached for, and the fix is almost always "use the customer
    # project's venv interpreter" rather than installing into the system interpreter. The
    # sibling generate-evals verifier says the same thing; the two must not disagree.
    print(
        f'ERROR: pyyaml is required to run verify.py, and {sys.executable} does not have it.\n'
        f"Re-run with an interpreter that does — the customer project's venv interpreter is "
        f'usually the easiest one — or install it there: {sys.executable} -m pip install pyyaml',
        file=sys.stderr,
    )
    sys.exit(3)


def _sibling(filename: str, name: str):
    """A sibling script of this file, loaded BY PATH: this file is also loaded by path
    (`scripts/record_target_rulings.py`), where the scripts directory is not on `sys.path`."""
    import importlib.util  # noqa: PLC0415 - only this loader needs it

    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().with_name(filename))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _language_evidence():
    """The sibling `language_evidence.py`."""
    return _sibling('language_evidence.py', '_scenario_variables_language_evidence')


# --- Schema description (mirrors the backend's ScenarioVariables Pydantic model) ---


def _fold_name():
    """The grounding key fold (`scripts/grounding_keys.py`), or None when this file runs copied out of the plugin."""
    try:
        scripts = str(Path(__file__).resolve().parents[3] / 'scripts')
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from grounding_keys import fold_name  # noqa: PLC0415 - repo script, optional at runtime
    except ImportError:
        return None
    return fold_name


class Advisory(str):
    """A finding the backend loads past. The `_check_*` helpers return it beside their errors;
    `verify()` drops it and `advisories()` prints it — the tier rides the value, so one walk
    serves both."""


# The schema's `required` strings (`minLength: 1`): absent, mistyped or empty does not load.
_TOP_LEVEL_REQUIRED_STRINGS = (
    'agent_purpose',
    'agent_domain',
)

# Strings the backend defaults to '': absent or blank loads, so it is advisory.
_TOP_LEVEL_POPULATED_STRINGS = (
    'interaction_style_hints',
    'success_criteria_hints',
)

# Lists the backend defaults to []: absent or empty loads, so it is advisory — every
# scenario-generation prompt that reads one then has nothing to draw from.
_TOP_LEVEL_POPULATED_LISTS = (
    'typical_user_profiles',
    'typical_complications',
    'typical_opening_scenarios',
)

# May be EMPTY (but must be lists, items validated when present): a tool-free agent
# (single-LLM conversational flow) legitimately has `agent_tools: []` — NEVER fabricate a
# pseudo-tool to satisfy a verifier; it enters the backend auditor contract as a fake
# capability surface. A stateless chat agent likewise has `entity_schemas: []`. The backend
# defaults both to [] and (in current backends) renders empty tools as
# '(no tools declared)' in every prompt that consumes them.
_TOP_LEVEL_ALLOWED_EMPTY_LISTS = (
    'agent_tools',
    'entity_schemas',
)

# For each list-of-object field, which sub-keys each item must have as a non-empty string.
_ITEM_SCHEMA: dict[str, tuple[str, ...]] = {
    'agent_tools': ('name', 'description', 'when_used'),
    'entity_schemas': ('name', 'description'),
    'typical_user_profiles': ('style', 'context', 'motivations'),
    'typical_complications': ('name', 'description'),
}

# world_grounding is a REQUIRED top-level enum (no default — mirrors the backend's
# ScenarioVariables field), not a free-form string, so it gets its own check
# rather than joining _TOP_LEVEL_REQUIRED_STRINGS.
_WORLD_GROUNDING_VALUES = ('seeded', 'live', 'hybrid')
# ToolSummary.reads — where a tool's records come from, the fact world_grounding derives from.
_TOOL_READS_VALUES = ('world_state', 'live')


# The two narrative archetype fields. `style` is a voice descriptor and is deliberately not
# scanned — a voice may name behaviour without fixing a scenario's surface.
_ARCHETYPE_PROSE_FIELDS = ('context', 'motivations')

#: Concrete-artifact nouns. A prop is a THING the person is holding, and no domain-free test
#: separates one from an abstraction, so the marker leans on this vocabulary instead.
_PROP_NOUNS = (
    r'footage|recording|clip|video|photo|image|screenshot|file|document|pdf|spreadsheet|csv|dataset|'
    r'invoice|receipt|contract|form|ticket|report|draft|deck|slide|transcript|attachment|lecture|email|letter'
)

#: The span allowed between a possession verb and the noun it possesses. It may NOT cross a
#: preposition or relativizer: "has access TO the dataset" and "has strong views ON which form"
#: are idiom, not possession, and without this guard both read as props.
_PROP_GAP = r'(?:(?!\b(?:on|to|about|with|for|of|in|from|which|that|whether|how|why|when)\b)[^.;,]){0,26}?'

#: Habitual/generic aspect anywhere in the field. Aspect, not vocabulary, is what separates a
#: standing disposition from a plot — "TYPICALLY has a document open" is a habit and "has a
#: document open" is a scene, and no noun list can tell them apart. Checked over the whole
#: field, so it also covers the adverb that lands after the verb.
_HABITUAL = re.compile(
    r'\b(?:typically|usually|often|routinely|generally|habitually|frequently|always|never|tends to|'
    r'likes to|prefers to|used to|every\s+(?:day|week|month|morning|afternoon|time|request|shift|'
    r'monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b',
    re.IGNORECASE,
)

#: Lexical markers of a PLOT, as (what it pins, pattern). A lexical read of prose is
#: RECALL-BIASED by construction: it cannot parse intent, so it catches the idioms below and
#: misses every plot phrased another way. Each pattern is narrowed until it stops firing on
#: ordinary DISPOSITIONAL prose — the shape this skill tells the author to write — because a
#: false positive costs a re-authoring round on a field that was already right. That, not the
#: misses, is what the tier is tuned against, and it is why it only ever ADVISES.
_PLOT_MARKERS: tuple[tuple[str, str], ...] = (
    (
        # `(?<!to )` keeps the infinitive out: "HAS TO fill in a form" is an obligation, not a
        # prop in hand. `_PROP_GAP` keeps the span from crossing a preposition, so "has access
        # TO the dataset" and "has strong views ON which form" stay idiom rather than possession.
        'a concrete prop',
        rf'(?<!to )\b(?:has|have|holds|holding|brought|carrying|carries|received|got)\b\s+'
        rf'{_PROP_GAP}\b(?:{_PROP_NOUNS})s?\b',
    ),
    (
        # No bare `named <word>` branch and no `one` in the count: they fired on "named
        # clearly", "a team named Platform Ops" and "escalates to one colleague". A count of
        # TWO or more people is what a dealt cast looks like, and it still catches the
        # archetype that motivated the rule ("share with two named colleagues").
        'a named or counted third party',
        # A count inside a RANGE is a standing team size, not a dealt cast: the lookbehinds
        # drop the second half of "two OR three teammates", and the filler may not be a
        # coordinator either.
        r'(?<!or )(?<!to )\b(?:two|three|four|five|a couple of|a pair of)\s+(?:(?!or\b|and\b|to\b)\w+\s+){0,2}?'
        r'(?:colleagues?|co-?workers?|teammates?|classmates?|friends?|managers?|supervisors?|directors?|'
        r'reviewers?|approvers?|stakeholders?)\b',
    ),
    (
        # A deadline is a SPECIFIC one the person is under, so the noun needs a determiner or a
        # time beside it. Bare "deadline" is how a standing disposition describes pressure, and
        # is this skill's own example complication name.
        'a deadline',
        r'\b(?:a|the|their|its)\s+deadline\b|\bdeadline\s+(?:today|tonight|tomorrow|is|was|of|for)\b'
        r'|\bdue\s+(?:today|tonight|tomorrow|by|this)\b'
        r'|\bby\s+(?:today|tonight|tomorrow|noon)\b'
        r'|\bby\s+(?:the\s+)?end\s+of\s+(?:the\s+)?(?:day|week|month|today|tomorrow)\b'
        r'|\bbefore\s+the\s+(?:next|meeting|call|class|session|standup)\b'
        r'|\bwithin\s+the\s+(?:hour|day)\b',
    ),
    (
        # The possessive is excluded: "reviews YESTERDAY'S numbers every morning" is a routine,
        # not the one past event a scenario would deal.
        'a specific past episode',
        r"\b(?:last night|last week|last month|yesterday|earlier today|this morning|this afternoon|"
        r"over the weekend|a moment ago|just now|an hour ago)\b(?!['’]s)",
    ),
)


def verify(path: Path) -> list[str]:
    """Return a list of human-readable errors. Empty list = valid."""
    if not path.is_file():
        return [f'scenario_variables.yaml not found at {path}']

    try:
        raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    except yaml.YAMLError as e:
        return [f'YAML parse error in {path}: {e}']

    if not isinstance(raw, dict):
        return [f'Expected top-level mapping in {path}, got {type(raw).__name__}']

    return [*(m for m in _structure(raw) if not isinstance(m, Advisory)), *dispatched_skill_names(raw, path.parent),
            *pasted_prompt_hints(raw, path.parent)]


def dispatched_skill_names(raw: dict, plurai_dir: Path) -> list[str]:
    """An `agent_tools` name that is neither a `plurai.config.yaml` `tools:` key nor a skill name in
    the scan topology (`artifacts/agent_topology.json` `skills`). The skill's dispatch call carries
    that exact name and coverage credits the call by exact match, so any other spelling is a
    surface no call can ever credit. Skipped when either file is absent or the topology lists no
    skill: there is then no dispatch id to hold the name to."""
    topology_path = plurai_dir / 'artifacts' / 'agent_topology.json'
    config_path = plurai_dir / 'plurai.config.yaml'
    if not topology_path.is_file() or not config_path.is_file():
        return []
    try:
        topology = json.loads(topology_path.read_text(encoding='utf-8'))
        config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, yaml.YAMLError):
        return []
    rows = topology.get('skills') if isinstance(topology, dict) else None
    skills = [n for n in (r.get('name') if isinstance(r, dict) else r for r in rows or []) if isinstance(n, str) and n]
    tools = raw.get('agent_tools')
    if not skills or not isinstance(tools, list):
        return []
    config_tools = config.get('tools') if isinstance(config, dict) else None
    known = {*skills, *(config_tools if isinstance(config_tools, dict) else ())}
    return [
        f"agent_tools[{i}].name {tool['name']!r} is neither a `tools:` key in plurai.config.yaml nor a skill in the scan "
        f'topology — list a dispatched skill under exactly its topology name, one of {skills}.'
        for i, tool in enumerate(tools)
        if isinstance(tool, dict) and isinstance(tool.get('name'), str) and tool['name'] not in known
    ]


#: A file the agent loads as a prompt: a text or template suffix, and `prompt` in its name or in a directory above it.
_PROMPT_SUFFIXES = frozenset({'.md', '.txt', '.prompt', '.j2', '.jinja', '.jinja2', '.tmpl'})
#: Trees that hold no prompt of the agent's own: dependencies, caches and build output (hidden dirs are skipped too).
_NOT_PROMPT_DIRS = frozenset({'node_modules', 'venv', 'site-packages', '__pycache__', 'dist', 'build'})
_PROMPT_FILE_MAX_BYTES = 200_000
#: A hint that repeats at least this many distinct lines of one prompt file is a paste and not a quotation.
_PASTED_LINES = 10
#: Or, once it has `_PASTED_SHARE_MIN_LINES` lines of its own, this share of them: a short dump of a short prompt.
_PASTED_SHARE = 0.6
_PASTED_SHARE_MIN_LINES = 6
_LINE_MARKER = re.compile(r'^(?:[#>*+\-]+|\d+[.)])\s+|^[#>*+\-]+$')
_EMPHASIS = re.compile(r'[*_`]')


def prompt_files(plurai_dir: Path) -> list[Path]:
    """The raw prompt files of the agent module (the workdir's parent), sorted; the workdir itself is the plugin's."""
    workdir = plurai_dir.resolve()
    root = workdir.parent
    found: list[Path] = []
    for here, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in _NOT_PROMPT_DIRS and (Path(here) / d).resolve() != workdir)
        in_prompt_dir = 'prompt' in Path(here).relative_to(root).as_posix().lower()
        for name in sorted(names):
            path = Path(here) / name
            if path.suffix.lower() in _PROMPT_SUFFIXES and ('prompt' in name.lower() or in_prompt_dir):
                found.append(path)
    return [p for p in found if p.stat().st_size <= _PROMPT_FILE_MAX_BYTES]


def _norm_line(line: str) -> str:
    """A line without its list, quote or heading marker, emphasis, case or spacing; '' when under three words remain."""
    text = ' '.join(_EMPHASIS.sub('', line).split())
    while (stripped := _LINE_MARKER.sub('', text, count=1)) != text:
        text = stripped
    text = text.casefold()
    return text if len(text.split()) >= 3 else ''


def _norm_lines(text: str) -> set[str]:
    """The distinct non-trivial lines of `text`; markers, blank lines and one-or-two-word lines carry no content."""
    return {line for raw in text.splitlines() if (line := _norm_line(raw))}


def pasted_prompt_hints(raw: dict, plurai_dir: Path) -> list[str]:
    """A hint field that equals a raw prompt file of the agent, or repeats a large share of its lines.

    The hints describe the USERS and what success looks like to them; a pasted prompt describes the agent, and every
    scenario-generation prompt that reads the field then takes the agent's own rules for the users' habits. A paste
    is counted on the normalised, distinct lines the field and one prompt file share, so blank lines, wrapping,
    markers, order and a few lines left out do not hide it: at least 10 shared lines, or at least 60% of a field
    of 6 or more lines. A bench with no prompt file holds nothing against the hint."""
    hints = {f: raw[f] for f in _TOP_LEVEL_POPULATED_STRINGS if isinstance(raw.get(f), str) and raw[f].strip()}
    if not hints:
        return []
    root = plurai_dir.resolve().parent
    errors: list[str] = []
    for field, text in hints.items():
        lines = _norm_lines(text)
        for path in prompt_files(plurai_dir):
            try:
                body = path.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            name = path.relative_to(root).as_posix()
            flat = ' '.join(body.split())
            shared = len(lines & _norm_lines(body))
            if len(flat) >= 20 and ' '.join(text.split()) == flat:
                errors.append(f'{field} equals the raw prompt file {name} — a hint is your own short read of how users behave and what '
                              'success looks like to them; write guidance for the scenario author in your own words instead of copying the prompt.')
            elif shared >= _PASTED_LINES or (len(lines) >= _PASTED_SHARE_MIN_LINES and shared >= _PASTED_SHARE * len(lines)):
                errors.append(f'{field} copies {shared} of its {len(lines)} lines from the raw prompt file {name} — a hint is your own '
                              'short read of how users behave and what success looks like to them; write guidance for the scenario author '
                              '(the users, their habits, what counts as success to them) instead of copying the prompt.')
    return errors


def _structure(raw: dict) -> list[str]:
    """Every structural finding, both tiers — an `Advisory` is one the backend loads past."""
    return [
        *_check_required_strings(raw),
        *_check_lists(raw),
        *_check_world_grounding(raw),
        *_check_world_grounding_coherence(raw),
        *_check_target_rulings(raw),
    ]


def advisories(path: Path) -> list[str]:
    """Shapes the backend ACCEPTS today that degrade what it can generate — reported, never refused.

    Same tier as the sibling `verify_state_schema.py`: this validator must never exceed the
    backend, and a lexical read of prose cannot be exact enough to refuse on. The structural
    `Advisory` findings (full population, a duplicate ruling id, a record tool with no `reads`),
    then the prose families — an evaluator-voice archetype (`evaluator_voice_archetypes`),
    plot-shaped user archetypes (`plot_shaped_archetypes`), an archetype with no `language` key at
    all (`undeclared_language_archetypes`, the required-nullable trait left implicit), and a
    language named in an archetype's prose without the typed field
    (`prose_language_archetypes`), which is the SPECIFIC case of the third and replaces it."""
    raw = _parsed(path)
    if raw is None:
        return []
    # The prose advisory SUBSUMES the undeclared one for the archetypes it names: both fire on an
    # archetype with no `language` key, and the two say different things to do about it — one
    # "write `language: null`", the other "write `language: \"Hebrew\"`". The specific advice is
    # the right one, so the general one steps aside where it applies rather than contradicting it.
    prose = _prose_language_hits(raw)
    return [*(m for m in _structure(raw) if isinstance(m, Advisory)), *evaluator_voice_archetypes(raw),
            *plot_shaped_archetypes(raw), *undeclared_language_archetypes(raw, covered_by=prose), *floor_field_findings(raw),
            *(message for _index, message in prose), *unknown_family_labels(raw, path.parent / 'simulation_prd.md'),
            *outcome_code_coverage(raw, path.parent / 'simulation_prd.md'), *undeclared_language_mix(raw, path.parent),
            *undeclared_control_messages(raw, path.parent), *harness_world_unseeded(path.parent),
            *carried_complications_dropped(raw, path.parent),
            # LAST: a caller that tails the output (`| tail -6`) still sees it.
            *missing_floor_entries(raw, path.parent)]


def carried_complications_dropped(raw: dict, workdir: Path) -> list[str]:
    """Name each complication of `artifacts/vocabulary_carry.json` (the previous build's classes that set up a confirmed
    problem, Step 4) that is neither kept by its exact name in `typical_complications` nor retired with a why in
    `artifacts/vocabulary_retired.json`. Silent without a carry file."""
    try:
        carried = json.loads((workdir / 'artifacts' / 'vocabulary_carry.json').read_text(encoding='utf-8')).get('carried')
    except (OSError, ValueError, AttributeError):
        return []
    kept = {str(c.get('name')) for c in raw.get('typical_complications') or [] if isinstance(c, dict)}
    try:
        retired_doc = json.loads((workdir / 'artifacts' / 'vocabulary_retired.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        retired_doc = {}
    listed = retired_doc.get('retired') if isinstance(retired_doc, dict) else None
    retired = {str(r.get('name')) for r in listed if isinstance(r, dict) and str(r.get('why') or '').strip()} if isinstance(listed, list) else set()
    notes = []
    for entry in carried if isinstance(carried, list) else []:
        name = str(entry.get('name') or '') if isinstance(entry, dict) else ''
        if not name or name in kept or name in retired:
            continue
        found = '; '.join(str(f.get('title')) for f in entry.get('found') or [] if isinstance(f, dict) and f.get('title'))
        notes.append(
            f'carried complication `{name}` set up a confirmed problem in the previous build ({found or "a confirmed finding"}) and is '
            'neither in `typical_complications` nor retired: keep it under that exact name (Step 4), or, only when the PRD rules its '
            'premise out, list it in `artifacts/vocabulary_retired.json` with its why.'
        )
    return notes


def _world_schema():
    """The sibling `world_schema.py`."""
    return _sibling('world_schema.py', '_scenario_variables_world_schema')


def harness_world_unseeded(workdir: Path) -> list[str]:
    """`harness_world_unseeded`: on a bench env-gen skips (Step 4b's test), each declared world type that is neither an
    entity block in `scenario_state_schema.yaml` nor declined by a cited `not_seedable` fact. Its scenarios would all
    open on the same store. Silent on a mocked bench (env-gen writes the blocks) and when no world type is declared."""
    try:
        config = yaml.safe_load((workdir / 'plurai.config.yaml').read_text(encoding='utf-8')) or {}
    except (OSError, yaml.YAMLError):
        return []
    ws = _world_schema()
    if not isinstance(config, dict) or not ws.env_gen_skipped(config):
        return []
    prd = workdir / 'simulation_prd.md'
    deliver, undecided = ws.expected_world_blocks(config, prd.read_text(encoding='utf-8') if prd.is_file() else '')
    try:
        schema = yaml.safe_load((workdir / ws.SCHEMA).read_text(encoding='utf-8')) or {}
    except (OSError, yaml.YAMLError):
        schema = {}
    blocks = schema.get('entity_types') if isinstance(schema, dict) else None
    missing = [t for t in [*deliver, *undecided] if not (isinstance(blocks, dict) and t in blocks)]
    if not missing:
        return []
    return [
        f'harness_world_unseeded: declared world type(s) {", ".join(missing)} have no entity block in {ws.SCHEMA} and no cited '
        '`seedability / not_seedable` decline, so every scenario opens on the same store. Run '
        '`scripts/world_schema.py <plurai_sim>` (Step 4b), or decline each type with a fact citing the line that makes it unseedable.'
    ]


def undeclared_control_messages(raw: dict, workdir: Path) -> list[str]:
    """Name each fixed message the agent inventory read a UI control post and the agent match whole
    (`artifacts/agent_inventory.json` `control_messages`) that `control_messages` omits. Silent without the file."""
    try:
        inventory = json.loads((workdir / 'artifacts' / 'agent_inventory.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    read = inventory.get('control_messages') if isinstance(inventory, dict) else None
    declared = raw.get('control_messages')
    declared = {str(m) for m in declared} if isinstance(declared, list) else set()
    out: list[str] = []
    for entry in read if isinstance(read, list) else []:
        text = str(entry.get('text') or '') if isinstance(entry, dict) else ''
        if text and text not in declared:
            declared.add(text)  # one advisory per message, at its first site
            out.append(
                f'`control_messages` omits `{text}` (sent by `{entry.get("sent_by")}`, matched whole at `{entry.get("matched_at")}`): '
                'declare it, or the simulated user paraphrases it and that branch is never reached.'
            )
    return out


def undeclared_language_mix(raw: dict, workdir: Path) -> list[str]:
    """`language` is unset while the agent's own files show it serves a language other than English.

    Unset is not "varied": every scenario is authored blind, and a blind set comes out in one
    language. Only under a `plurai_sim/` workdir — elsewhere there is no agent to read."""
    if raw.get('language') not in (None, '', [], {}) or 'plurai_sim' not in workdir.resolve().parts:
        return []
    evidence_module = _language_evidence()
    evidence, defaults = evidence_module.collect(workdir)
    proposal = evidence_module.propose(evidence, defaults)
    if proposal is None:
        return []
    served = '; '.join(f'{code}: {evidence[code][0]}' for code in evidence_module.served(evidence) if code != 'en')
    return [
        f'`language` is unset, but the agent serves more than English ({served}) — declare '
        f'`language: {evidence_module.render(proposal)}` (`language_evidence.py` lists the evidence), or every '
        'scenario is authored in one language.'
    ]


def floor_field_findings(raw: dict) -> list[str]:
    """Name a `presses_after_refusal` that is not true/false, and a `variant_carriers` entry the backend will drop:
    a key that is not one of the entry's `variants`, or a value that is not one of `agent_tools`. Advisory only — the
    backend loads past both and drops what it cannot use."""
    tools = {str(t.get('name')) for t in raw.get('agent_tools') or [] if isinstance(t, dict)}
    notes = []
    for i, comp in enumerate(raw.get('typical_complications') or []):
        if not isinstance(comp, dict):
            continue
        press = comp.get('presses_after_refusal')
        if press is not None and not isinstance(press, bool):
            notes.append(f'typical_complications[{i}] ({comp.get("name")}).presses_after_refusal must be true, false, or omitted.')
        carriers = comp.get('variant_carriers')
        if carriers is None:
            continue
        if not isinstance(carriers, dict):
            notes.append(f'typical_complications[{i}] ({comp.get("name")}).variant_carriers must map a variant to a tool name.')
            continue
        variants = {' '.join(str(v).casefold().split()) for v in comp.get('variants') or []}
        for variant, tool in carriers.items():
            if ' '.join(str(variant).casefold().split()) not in variants:
                notes.append(f'typical_complications[{i}] ({comp.get("name")}).variant_carriers key {variant!r} is not one of its variants; copy the variant text exactly.')
            if str(tool) not in tools:
                notes.append(f'typical_complications[{i}] ({comp.get("name")}).variant_carriers names {tool!r}, which is not in agent_tools.')
    return notes


#: A heading naming the PRD's Absences section, numbered or not (`### Absences`, `## 4. Absences — what it cannot do`,
#: `## 4.2 Absences`, `## §4 Absences`), and a list item under it. The backend types Absences rows from the whole PRD
#: whatever the heading looks like, so this reader must not miss a numbered one.
_HEADING = re.compile(r'^\s*(#{1,6})\s+(.*)$')
_ABSENCES_TITLE = re.compile(r'^(?:§?\s*\d+(?:\.\d+)*\.?\s+)?Absences\b', re.IGNORECASE)
_LIST_ITEM = re.compile(r'^\s*(?:[-*+]|\d+[.)])\s+\S')


def _prd_absence_rows(prd_path: Path) -> int:
    """How many list items the PRD's Absences section holds, its deeper subsections included (0 when there is no PRD or
    no such section)."""
    try:
        lines = prd_path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return 0
    rows, level = 0, None
    for line in lines:
        if heading := _HEADING.match(line):
            depth = len(heading.group(1))
            if level is None or depth <= level:
                level = depth if _ABSENCES_TITLE.match(heading.group(2).strip()) else None
        elif level is not None and _LIST_ITEM.match(line):
            rows += 1
    return rows


def missing_floor_entries(raw: dict, workdir: Path) -> list[str]:
    """The adversarial floor's two entries (SKILL "Adversarial floor"), each named when no complication carries its flag
    and its "applies when" holds on typed facts — the rule the backend's floor warning applies:
    `instruction_inside_read_content` when an agent tool that is not a `serves:` back-end layer returns records
    (non-empty `output_fields`); `pressure_after_refusal` when an archetype is `declined_by_design` or the PRD's
    Absences section lists a row. Advisory: the scan adds the entry (the backend only warns)."""
    comps = [c for c in raw.get('typical_complications') or [] if isinstance(c, dict)]
    try:
        config = yaml.safe_load((workdir / 'plurai.config.yaml').read_text(encoding='utf-8')) or {}
    except (OSError, yaml.YAMLError):
        config = {}
    tools_cfg = config.get('tools') if isinstance(config, dict) else None
    service = {str(n) for n, spec in tools_cfg.items() if isinstance(spec, dict) and spec.get('serves')} if isinstance(tools_cfg, dict) else set()
    readers = [str(t.get('name')) for t in raw.get('agent_tools') or []
               if isinstance(t, dict) and t.get('output_fields') and str(t.get('name')) not in service]
    notes = []
    if readers and not any(c.get('untrusted_instruction') is True for c in comps):
        notes.append(
            f'typical_complications has no `instruction_inside_read_content` entry (`untrusted_instruction: true`), but the agent '
            f'reads records from {", ".join(readers[:3])}{" and others" if len(readers) > 3 else ""}: add it as SKILL.md\'s '
            '"Adversarial floor" describes, or no scenario tests an instruction planted in what the agent reads.'
        )
    declined = [str(c.get('name')) for c in comps if c.get('declined_by_design') is True]
    absences = _prd_absence_rows(workdir / 'simulation_prd.md')
    if (declined or absences) and not any(c.get('presses_after_refusal') is True for c in comps):
        why = f'`{declined[0]}` is declined_by_design' if declined else f"the PRD's Absences section lists {absences} row(s)"
        notes.append(
            f'typical_complications has no `pressure_after_refusal` entry (`presses_after_refusal: true`), but {why}: add it as '
            'SKILL.md\'s "Adversarial floor" describes, or no scenario tests a user pressing after a correct refusal.'
        )
    return notes


def unknown_family_labels(raw: dict, prd_path: Path) -> list[str]:
    """Name each `families:` label the sibling PRD never mentions — a misspelled or split link.

    Presence only, never the family grammar: the backend owns what counts as a declared family
    and names a label it cannot match at generate-scenarios job start. A label absent from the
    whole PRD is wrong under any grammar, so this is the one case a local check can name."""
    try:
        prd = prd_path.read_text(encoding='utf-8').casefold()
    except OSError:
        return []
    notes = []
    for pool, key in (('typical_complications', 'name'), ('typical_user_profiles', 'style')):
        for i, entry in enumerate(raw.get(pool) or []):
            labels = entry.get('families') if isinstance(entry, dict) else None
            for label in labels if isinstance(labels, list) else []:
                if isinstance(label, str) and label.strip() and label.strip().casefold() not in prd:
                    notes.append(
                        f'{pool}[{i}] ({entry.get(key)}) lists family {label!r}, which simulation_prd.md never mentions — '
                        'spell each label exactly as the PRD declares it.'
                    )
    return notes


#: A markdown table's header rule (`|---|:--:|`), the line that makes the row above it a header.
_TABLE_RULE = re.compile(r'^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$')
#: An outcome code as a contract table spells it: an upper-case identifier (`NOT_PERMITTED`, `TIMED_OUT`).
_OUTCOME_CODE = re.compile(r'(?<![A-Za-z0-9_])[A-Z][A-Z0-9_]*[A-Z0-9](?![A-Za-z0-9_])')
#: A code cell: codes and nothing else but a closing parenthetical (`TIMED_OUT`, `A / B`, `A (+ metadata)`). A
#: cell of prose that merely contains a capitalised word ("API code 100004") is a description, not a code.
_CODE_CELL = re.compile(r'\s*[A-Z][A-Z0-9_]*[A-Z0-9](?:\s*[,/]\s*[A-Z][A-Z0-9_]*[A-Z0-9])*\s*(?:\(.*\))?\s*')


def _table_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith('|'):
        return None
    return [cell.strip() for cell in stripped.strip('|').split('|')]


def prd_outcome_codes(prd: str) -> list[str]:
    """The outcome codes the PRD's contract tables declare: every cell that is nothing but codes, in a
    column whose header names a code (`code`, `error_code`, `Error code`), in document order, once each. A
    PRD with no such table declares none, and the coverage check below says nothing."""
    codes: dict[str, None] = {}
    lines = prd.splitlines()
    columns: list[int] = []
    for i, line in enumerate(lines):
        cells = _table_cells(line)
        if cells is None:
            columns = []
            continue
        if i + 1 < len(lines) and _TABLE_RULE.match(lines[i + 1]):
            columns = [j for j, head in enumerate(cells) if 'code' in head.strip('`* ').casefold()]
            continue
        if _TABLE_RULE.match(line):
            continue
        for j in columns:
            cell = cells[j].replace('`', ' ') if j < len(cells) else ''
            if _CODE_CELL.fullmatch(cell):
                codes.update(dict.fromkeys(m for m in _OUTCOME_CODE.findall(cell.split('(')[0]) if len(m) > 2))
    return list(codes)


def _codes_named(text: object, codes: list[str]) -> list[str]:
    text = text if isinstance(text, str) else ''
    return [code for code in codes if re.search(rf'(?<![A-Za-z0-9_]){re.escape(code)}(?![A-Za-z0-9_])', text)]


def outcome_code_coverage(raw: dict, prd_path: Path) -> list[str]:
    """Each outcome code the PRD's contract declares is ONE test: the description (the archetype's base
    form, dealt first) or one `variants:` entry of exactly one archetype names it.

    Facts only: which codes the contract tables declare, which entries name them verbatim. Whether a
    code is reachable by a user's request or a world condition is a judgement, left to the author
    the unnamed-codes note is addressed to."""
    try:
        codes = prd_outcome_codes(prd_path.read_text(encoding='utf-8'))
    except OSError:
        return []
    if not codes:
        return []
    notes: list[str] = []
    named_by: dict[str, list[str]] = {}
    for i, comp in enumerate(raw.get('typical_complications') or []):
        if not isinstance(comp, dict):
            continue
        name = comp.get('name')
        variants = comp.get('variants') if isinstance(comp.get('variants'), list) else []
        for where, text in (('description', comp.get('description')), *((f'variants[{k}]', v) for k, v in enumerate(variants))):
            found = _codes_named(text, codes)
            if len(found) > 1:
                notes.append(
                    f'typical_complications[{i}] ({name}) {where} names {len(found)} outcomes ({", ".join(found)}) — each is dealt as '
                    'ONE test, so the others are never tested: keep one there and give each other its own `variants:` entry.'
                )
            for code in found:
                if name not in named_by.setdefault(code, []):
                    named_by[code].append(name)
    for code, names in named_by.items():
        if len(names) > 1:
            notes.append(f'outcome {code} is named by {len(names)} archetypes ({", ".join(map(str, names))}) — keep it on the one that enacts its trigger.')
    if unnamed := [code for code in codes if code not in named_by]:
        notes.append(
            f'simulation_prd.md declares outcome code(s) no complication names: {", ".join(unnamed)}. Each one a user\'s request '
            'or a world condition can reach is a test: name it verbatim in the description or one `variants:` entry of the '
            'archetype that enacts its trigger. A code no scenario can reach (an auth or transport failure) needs nothing.'
        )
    return notes


def _parsed(path: Path) -> dict | None:
    """The document, or None when it is unreadable, unparseable or not a mapping — each of
    which `verify()` already reports as a hard error. Mirrors the sibling verifier's helper of
    the same name."""
    if not path.is_file():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError):
        return None
    return raw if isinstance(raw, dict) else None


#: The ISO 639-1 language NAMES, as a closed data table (owned by `language_evidence.py`, beside each code).
#:
#: WHY A TABLE AND NOT AN IDIOM. The previous detector matched
#: ``(?:asks|speaks|writes|replies|talks|answers|communicates)\s+(?:in\s+)?([A-Z][a-z]{2,})`` and
#: handed the capture group to the author as the value to type. The verb list decides RECALL,
#: which was a documented trade-off; the CAPTURE GROUP decides correctness, and it was wrong three
#: ways, run in the tree: ``asks Support for a refund`` → ``Support``; ``answers Monday through
#: Friday`` → ``Monday``; ``speaks Plainly and expects the same`` → ``Plainly``. The advisory then
#: instructed the author to write ``language: "Support"``, and the backend seats the archetype on
#: a slot authored in that language or drops it with a typed conflict. It also mis-captured
#: ``Brazilian`` out of "Brazilian Portuguese" and ``Simplified`` out of "Simplified Chinese".
#:
#: Inverting it removes all four at once: a language is a MEMBER OF A CLOSED SET, so look for the
#: set's members instead of guessing a token's role from the verb in front of it. Adding verbs was
#: the patch to refuse — it widens recall and leaves every false positive standing.
_ISO_639_LANGUAGE_NAMES = frozenset(_language_evidence().NAME_TO_CODE)

#: ISO 639-1 members deliberately NOT looked for, with the reason. An exclusion is a decision on
#: the record; a silently trimmed table is the hand-picked include list this rule replaced.
_LANGUAGE_NAME_COLLISIONS = {
    'Latin': "names a SCRIPT in this repo's own vocabulary ('Latin script', 'non-Latin'), which is "
             'a fact about characters and not a claim about the archetype speaking Latin',
    'Tonga': "an ordinary place name; the language (ISO 'to') is vanishingly rare in agent prose",
}

#: Word tokens, script-aware and case-preserving: a language name is looked up by exact form, so
#: `[A-Za-z]+` would be enough here — but the table is the closed set, not the character class,
#: and a `\w+` split keeps a hyphenated or accented neighbour from swallowing the name.
_WORD = re.compile(r'[^\W\d_]+', re.UNICODE)


def _named_languages(text: str) -> list[str]:
    """Every table language named in one prose field, in order, without repeats."""
    seen: list[str] = []
    for token in _WORD.findall(text):
        if token in _ISO_639_LANGUAGE_NAMES and token not in _LANGUAGE_NAME_COLLISIONS and token not in seen:
            seen.append(token)
    return seen


def prose_language_archetypes(data: dict) -> list[str]:
    """Archetypes whose prose names a language the typed `language` field does not carry.

    Advisory, never an error: the deal reads ONLY the field, so a language that lives in `style`
    ("asks in Hebrew") is invisible to it and the slot's assigned language wins silently — the
    archetype's defining trait dropped without a word. Same tier as `plot_shaped_archetypes`.

    An archetype that DECLARES `language` (including an explicit `null`, which says "speaks the
    scenario's language") is not advised about: the trait is typed either way, and the prose is
    then free to describe it.
    """
    return [message for _index, message in _prose_language_hits(data)]


def _prose_language_hits(data: dict) -> list[tuple[int, str]]:
    """`(profile index, advisory)` for `prose_language_archetypes`.

    The index is what `advisories` needs to keep the general `undeclared_language_archetypes`
    advisory from contradicting this specific one on the same archetype; the message is all
    anything else wants. Read off the same walk so the two can never disagree about which
    archetypes were named.
    """
    profiles = data.get('typical_user_profiles')
    if not isinstance(profiles, list):
        return []
    out: list[tuple[int, str]] = []
    for i, profile in enumerate(profiles):
        if not isinstance(profile, dict) or 'language' in profile:
            continue
        for field in ('style', 'context', 'motivations'):
            named = _named_languages(str(profile.get(field) or ''))
            if named:
                out.append((
                    i,
                    f'typical_user_profiles[{i}].{field} names a language in prose ({", ".join(named)}) but the archetype '
                    f'declares no `language:` — the deal reads only the typed field, so the slot\'s assigned language '
                    f'overrides this silently. Add `language: "{named[0]}"` (or `language: null` if it is not the '
                    f"archetype's defining trait).",
                ))
                break
    return out


def undeclared_language_archetypes(data: dict, covered_by: list[tuple[int, str]] | None = None) -> list[str]:
    """Archetypes with no `language` KEY at all — the trait left implicit.

    `language` is required-nullable in `references/scenario_variables_schema.json`: an omitted key
    and a declared `null` are the same value to the backend's deal and DIFFERENT facts about the
    author, and the omitted one is how a language named only in prose came to be dropped without a
    word. Writing the key is the author saying they considered the trait.

    ADVISORY, not an error, and that is the tier the tree earns rather than the one the rule
    wants: the backend's `UserProfileArchetype.language` still carries `default=None`, so refusing
    here would exceed the backend — the rule this whole validator is built on. It becomes an error
    when the backend field loses its default.

    `covered_by` is `_prose_language_hits`' output, and archetypes it names are SKIPPED. Both
    rules fire on a missing key, and they prescribe different values for it — "write `language:
    null`" beside "add `language: \"Hebrew\"`" — so the general advisory stands down wherever the
    specific one already said what to write. Called without it (a direct caller testing this rule
    alone) nothing is skipped.
    """
    profiles = data.get('typical_user_profiles')
    if not isinstance(profiles, list):
        return []
    covered = {index for index, _message in covered_by or ()}
    return [
        f'typical_user_profiles[{i}] declares no `language:` key. It is required-nullable — write '
        f'`language: null` when the user speaks whatever language the scenario is authored in, or the '
        f'language name when the archetype is defined by it. An omitted key reads as null to the deal '
        f'and says nothing about whether anyone considered the trait.'
        for i, profile in enumerate(profiles)
        if isinstance(profile, dict) and 'language' not in profile and i not in covered
    ]


#: The assistant, as the thing an archetype's prose can make its OBJECT. Every scenario drawn from
#: such an archetype produces user turns that announce the probe to the agent — "Let's see how this
#: handles an out-of-order request" — and every judge downstream then grades a dialog with a critic
#: in it. The persona is a CALLER; it has a problem, not a hypothesis about the system.
#:
#: ONLY THE WORDS THAT NAME THE SOFTWARE AND NOTHING ELSE. `system`, `agent`, `model` and `flow`
#: were here and are ordinary nouns of the customer's own domain — an insurance `agent`, a `model`
#: number on a receipt, the `flow` of funds on a statement, `system` status — so beside a loose
#: verb they flagged caller prose that was exactly right ("wants to check the system status before
#: calling support", "needs to see the agent notes from last week") — an instruction to rewrite a
#: correct archetype. An undercount is the safe side here: the backend judges the same property
#: again at scenario authoring (its coherence auditor, `scenario.audit_coherence`), so a missed one is caught later.
_ASSISTANT_NOUN = r'(?:the|this)\s+(?:assistant|chatbot|bot)'

#: The same nouns PLUS the domain words, for the passive construction below only. "the system is
#: being tested" has no reading in which a caller is describing their own problem — the register
#: is in the construction, not in the verb's neighbourhood — so the words that are unsafe next to
#: a bare `check` are safe here.
_STUDIED_NOUN = r'(?:the|this)\s+(?:assistant|chatbot|bot|agent|model|system|flow)'

#: The verbs of somebody STUDYING the agent, paired with the noun above. SPELLED OUT, never a
#: `\w*` stem: `see\w*` also matches "seems", so "it seems the system dropped their reservation" —
#: a caller reporting what happened to them — was read as an evaluator announcing a probe.
_STUDY_VERB = (
    r'(?:see|sees|seeing|test|tests|tested|testing|probe|probes|probed|probing|'
    r'evaluate|evaluates|evaluated|evaluating|assess|assesses|assessed|assessing|'
    r'verify|verifies|verified|verifying|observe|observes|observed|observing|check|checks|checked|checking)'
)

#: The register of somebody STUDYING the agent, paired with the noun above. Deliberately not the
#: verdict vocabulary ("fails to", "should have"): a caller in character says "the system fails to
#: show my booking", and flagging that would send the author to rewrite an archetype that is exactly right.
_EVALUATOR_VOICE_MARKERS: tuple[tuple[str, str], ...] = (
    (
        'makes the assistant the object of a test',
        rf'\b{_STUDY_VERB}\s+(?:\w+\s+){{0,3}}?{_ASSISTANT_NOUN}\b'
        rf'|{_STUDIED_NOUN}\s+(?:\w+\s+){{0,2}}?\b(?:is being|gets)\s+(?:tested|probed|evaluated|assessed)\b',
    ),
    (
        'narrates the mechanics of the conversation instead of the user\'s own concern',
        r'\bcomments?\s+on\s+the\s+(?:mechanics|structure|flow|format|quality)\s+of\s+the\s+(?:conversation|dialog(?:ue)?|exchange|interaction)\b'
        r'|\bnarrat(?:es|ing)\s+(?:the\s+)?(?:test|evaluation|probe|experiment)\b'
        r'|\bannounc(?:es|ing)\s+(?:the\s+)?(?:test|probe|check)\b',
    ),
)

#: `style` IS scanned here, unlike the plot rule above. A voice descriptor may name behaviour
#: without fixing a scenario's surface — that is why the plot rule skips it — but "comments on the
#: mechanics of the conversation" is not a voice, it is an instruction to break character, and it
#: is the field that produced exactly that live (Nadia qual-2 s01, every turn).
_VOICE_SCANNED_FIELDS = ('style', 'context', 'motivations')


def evaluator_voice_archetypes(data: dict) -> list[str]:
    """Advise on an archetype whose prose makes the assistant its object of study.

    It produces an UNFAITHFUL transcript on every run of every scenario the archetype is dealt
    to — the simulator announces the probe to the agent, the run is filed
    ``measurement_clean: false``, and no eval fix repairs it. The backend loads the file (its scenario
    authoring judges the voice per scenario in its coherence auditor, ``scenario.audit_coherence``), so this is an advisory:
    the author hears it once, before any generation is paid for.

    LEXICAL, so narrow on purpose. It pairs an assistant NOUN with the observational register only
    — a persona complaining in character ("the system fails to show my booking") names the same
    noun beside a verdict word and is not flagged, which is the false-positive class the runtime
    detector's own narrowing was bought to avoid.
    """
    profiles = data.get('typical_user_profiles')
    if not isinstance(profiles, list):
        return []
    found: list[str] = []
    for i, profile in enumerate(profiles):
        if not isinstance(profile, dict):
            continue
        for field in _VOICE_SCANNED_FIELDS:
            text = profile.get(field)
            if not isinstance(text, str):
                continue
            for names, pattern in _EVALUATOR_VOICE_MARKERS:
                match = re.search(pattern, text, re.IGNORECASE)
                if match is None:
                    continue
                found.append(
                    f'typical_user_profiles[{i}].{field} writes the persona as an EVALUATOR, not a caller — '
                    f'{match.group(0).strip()!r} {names}. Every scenario dealt this archetype produces user turns that '
                    f'announce the probe to the agent, so the transcript is unfaithful and the run is not measurement-'
                    f'clean. Write what this person WANTS and how they behave when they do not get it; the suite is '
                    f'what tests the agent, never the persona.'
                )
                break
    return found


def plot_shaped_archetypes(data: dict) -> list[str]:
    """One advisory per plot marker found in a `typical_user_profiles` narrative field — a
    single `context` that pins both a prop and a deadline reports both.

    The archetype is the axis the backend holds CONSTANT while it varies setting / subject /
    cast / stakes across the scenarios that share it. Whatever the archetype fixes, no scenario
    can be dealt — so a prop, a cast, a deadline or a past episode written here is one fewer
    axis of difference between every scenario drawn from this profile (observed live)."""
    profiles = data.get('typical_user_profiles')
    if not isinstance(profiles, list):
        return []
    notes: list[str] = []
    for i, profile in enumerate(profiles):
        if not isinstance(profile, dict):
            continue
        for field in _ARCHETYPE_PROSE_FIELDS:
            text = profile.get(field)
            if not isinstance(text, str):
                continue
            for pins, phrase in _plot_markers(text):
                notes.append(
                    f'typical_user_profiles[{i}].{field} reads as a PLOT, not a disposition — {phrase!r} pins '
                    f'{pins}. Scenarios sharing this archetype are told apart by the surface each is dealt, so '
                    f'drop it and say how this person BEHAVES instead; the backend deals the surface per scenario.'
                )
    return notes


def _plot_markers(text: str) -> list[tuple[str, str]]:
    """(what it pins, the matched phrase) for each distinct plot marker in one field.

    A field written in habitual aspect is describing what this person always does, which is a
    disposition however concrete its nouns — so it reports nothing."""
    if _HABITUAL.search(text):
        return []
    found: list[tuple[str, str]] = []
    for pins, pattern in _PLOT_MARKERS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            found.append((pins, match.group(0).strip()))
    return found


def _check_required_strings(data: dict) -> list[str]:
    errors: list[str] = []
    for key in (*_TOP_LEVEL_REQUIRED_STRINGS, *_TOP_LEVEL_POPULATED_STRINGS):
        required = key in _TOP_LEVEL_REQUIRED_STRINGS
        value = data.get(key)
        if key not in data:
            errors.append(f"Missing required field '{key}'." if required else Advisory(f"Field '{key}' is missing — the backend loads it as ''."))
        elif value is None:
            # A key with no value is an explicit null, which the schema (`type: string`) and the backend refuse.
            errors.append(f"Missing required field '{key}'." if required else f"Field '{key}' is null — write a string, or omit the key.")
        elif not isinstance(value, str):
            errors.append(f"Field '{key}' must be a string, got {type(value).__name__}.")
        elif not value.strip():
            errors.append(_blank(f"Field '{key}' must be non-empty.", value, required=required))
    return errors


def _blank(message: str, value: str, *, required: bool) -> str:
    """A blank string: an error only where the schema's `minLength: 1` refuses it (an empty
    required string); whitespace, or a defaulted string, loads — an advisory."""
    return message if required and value == '' else Advisory(message)


def _check_lists(data: dict) -> list[str]:
    errors: list[str] = []
    for list_name in _TOP_LEVEL_POPULATED_LISTS:
        errors.extend(_check_list(data, list_name, required=True))
    for list_name in _TOP_LEVEL_ALLOWED_EMPTY_LISTS:
        errors.extend(_check_list(data, list_name, required=False))
    return errors


def _check_list(data: dict, list_name: str, *, required: bool) -> list[str]:
    """One list field: presence/emptiness per ``required``, then item validation.

    The backend defaults every one of these lists to ``[]``, so absence or emptiness never refuses: on
    a ``required`` (populated) list it is an advisory, on an allowed-empty one (tool-free /
    stateless agents) it is nothing. An explicit null (a key with no value) is NOT absence: the
    schema's `type: array` and the backend both refuse it. Items are validated whenever present."""
    value = data.get(list_name)
    if list_name in data and value is None:
        return [f"Field '{list_name}' is null — write a list (`[]` when the agent truly has none), or omit the key."]
    if value is None:
        if not required:
            return []
        return [Advisory(
            f"List '{list_name}' is missing — the backend loads it as empty; populate it from the PRD, or leave it "
            'empty only when the agent truly has none.'
        )]
    if not isinstance(value, list):
        return [f"Field '{list_name}' must be a list, got {type(value).__name__}."]
    if required and len(value) == 0:
        return [Advisory(
            f"List '{list_name}' has no entries — populate it from the PRD, or leave it empty only when the agent "
            'truly has none (never fabricate one).'
        )]
    if list_name == 'typical_opening_scenarios':
        return _check_string_list(list_name, value)
    return _check_object_list(list_name, value, _ITEM_SCHEMA[list_name])


def _check_string_list(list_name: str, items: list) -> list[str]:
    errors: list[str] = []
    for i, item in enumerate(items):
        if not isinstance(item, str):
            errors.append(f"{list_name}[{i}] must be a non-empty string.")
        elif not item.strip():
            errors.append(Advisory(f"{list_name}[{i}] must be a non-empty string."))
    return errors


def _check_object_list(list_name: str, items: list, required_keys: tuple[str, ...]) -> list[str]:
    errors: list[str] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f'{list_name}[{i}] must be an object, got {type(item).__name__}.')
            continue
        for key in required_keys:
            value = item.get(key)
            if value is None:
                errors.append(f"{list_name}[{i}] missing required field '{key}'.")
            elif not isinstance(value, str):
                errors.append(f"{list_name}[{i}].{key} must be a string, got {type(value).__name__}.")
            elif not value.strip():
                errors.append(_blank(f"{list_name}[{i}].{key} must be non-empty.", value, required=True))
        if list_name == 'agent_tools':
            errors.extend(_check_tool_optionals(i, item))
        if list_name == 'typical_complications':
            errors.extend(_check_complication_optionals(i, item))
        if list_name == 'typical_user_profiles':
            errors.extend(_check_archetype_optionals(i, item))
    if list_name == 'typical_user_profiles':
        errors.extend(_check_profile_names(items))
    return errors


def _check_profile_names(profiles: list) -> list[str]:
    """An archetype's optional `name` is its stable label: trace analysis classifies production conversations under it.
    A blank name, or two that fold to one label, is an error; a profile with none is classified by its style."""
    fold = _fold_name()
    out: list[str] = []
    seen: dict[str, int] = {}
    unchecked = False
    for i, item in enumerate(profiles):
        if not isinstance(item, dict):
            continue
        if 'name' not in item:
            continue
        name = item['name']
        if not isinstance(name, str) or not name.strip():
            out.append(f'typical_user_profiles[{i}].name must be a non-empty string, got {name!r}.')
            continue
        if fold is None:
            unchecked = True
            continue
        folded = fold(name)
        if not folded:
            out.append(f'typical_user_profiles[{i}].name {name!r} is only separators. Give it a name made of letters or digits.')
        elif folded in seen:
            out.append(
                f'typical_user_profiles[{i}].name {name!r} folds to the same label as typical_user_profiles[{seen[folded]}] '
                f'({folded!r}): case and the separators - _ and space do not tell names apart. Rename one.'
            )
        else:
            seen[folded] = i
    if unchecked:
        out.append(Advisory(
            'typical_user_profiles names were not checked for collisions: this verifier runs outside the plugin and cannot fold them. '
            'Run the verifier from the plugin\'s own `skills/extract-scenario-variables/scripts/` to check them.'
        ))
    return out


def _check_archetype_optionals(i: int, archetype: dict) -> list[str]:
    """Validate the OPTIONAL UserProfileArchetype typed traits when present — `language` and
    `cannot_supply` (the backend `UserProfileArchetype`). Both exist because a trait living only in
    free-text prose is invisible to the batch deal: a language named in `style` is overridden by
    the slot silently, and an input the archetype would never provide is paired with a flow that
    requires it by luck of the draw.

    THE TWO TRAITS DIFFER ON `null`, and the difference is the backend's, not ours: `language` is
    `str | None` there, so an explicit `language: null` is accepted and means "no typed language".
    `cannot_supply` is `list[str]` with `default_factory=list` — no `| None` — so `cannot_supply:
    null` is REJECTED by Pydantic at load, and a file this verifier passed would then fail the
    backend. Absent is the normal case for both; an explicit null is legal for one and an
    authoring error for the other. It is the only asymmetry between the two.

    `cannot_supply` is type-checked rather than content-checked on purpose. Whether a declared
    token is the RIGHT wording is a question only the flow's `preconditions` can answer, and those
    live in the backend's dimension graph — which this verifier cannot see. What it can catch is
    the shape: a bare string instead of a list (`cannot_supply: "a framework list"` iterates as
    characters), or blank entries, both silent authoring bugs that would make the token match
    nothing at all."""
    errors: list[str] = []
    language = archetype.get('language')
    if language is not None and not (isinstance(language, str) and language.strip()):
        message = f'typical_user_profiles[{i}].language must be a non-empty string (a language name or tag) or omitted, got {language!r}.'
        errors.append(Advisory(message) if isinstance(language, str) else message)
    cannot = archetype.get('cannot_supply')
    if 'cannot_supply' in archetype and cannot is None:
        # Not the same as absent, and not a shape quibble: the backend types this field `list[str]`
        # with no `| None`, so an explicit null fails validation THERE while passing here — this
        # verifier would green-light a file the backend refuses to load. Say which fix is meant.
        errors.append(
            f'typical_user_profiles[{i}].cannot_supply is explicitly null, which the backend rejects '
            '(it is typed `list[str]`, unlike `language` which is `str | None`). OMIT the key '
            'entirely for an archetype with no such trait, or give it a list of input names.'
        )
    elif cannot is not None:
        if not isinstance(cannot, list):
            errors.append(
                f'typical_user_profiles[{i}].cannot_supply must be a LIST of strings (or omitted), got {cannot!r}. '
                'A bare string iterates as characters and would match no flow precondition.'
            )
        elif [entry for entry in cannot if not (isinstance(entry, str) and entry.strip())]:
            message = (
                f'typical_user_profiles[{i}].cannot_supply has a blank or non-string entry ({cannot!r}). Each entry names '
                "one input in the words the PRD uses for it — the backend's flow extractor reads these as evidence."
            )
            errors.append(message if [entry for entry in cannot if not isinstance(entry, str)] else Advisory(message))
    errors.extend(_check_family_labels(f'typical_user_profiles[{i}]', archetype))
    return errors


def _check_complication_optionals(i: int, comp: dict) -> list[str]:
    """Validate the OPTIONAL ComplicationArchetype effect flags when present. Each is a tri-state
    bool-or-null on the backend side (a typed pre-filter: empties_knowledge_base / curtails_conversation
    / negates_anchor / declined_by_design / untrusted_instruction). A non-bool (e.g. the string "true") would be a silent authoring bug, so catch it
    here where it is cheap to fix; absent/null is the normal case (most complications set none).

    The language licence is a PAIR: `language` (which language the complication licenses) and
    `language_mode` ('throughout' | 'switches_to' — how it enters the conversation). Newly
    authored files are held to the full contract — one without the other is an error here, even
    though the backend tolerates a missing mode on LEGACY files via a prose-shape shim (whose
    false positives are exactly why the typed field exists)."""
    errors: list[str] = []
    for flag in ('empties_knowledge_base', 'curtails_conversation', 'negates_anchor', 'declined_by_design', 'untrusted_instruction'):
        value = comp.get(flag)
        if value is not None and not isinstance(value, bool):
            errors.append(f"typical_complications[{i}].{flag} must be true, false, or omitted, got {type(value).__name__}.")
    language = comp.get('language')
    mode = comp.get('language_mode')
    if language is not None and not (isinstance(language, str) and language.strip()):
        message = f'typical_complications[{i}].language must be a non-empty string (ISO-639 code or language name) or omitted.'
        errors.append(Advisory(message) if isinstance(language, str) else message)
    if mode is not None and mode not in ('throughout', 'switches_to'):
        errors.append(f"typical_complications[{i}].language_mode must be 'throughout' or 'switches_to', got {mode!r}.")
    if language is not None and mode is None:
        errors.append(
            f"typical_complications[{i}] declares language without language_mode — add language_mode: 'throughout' "
            f"(speaks it from turn one) or 'switches_to' (moves into it mid-conversation)."
        )
    if mode is not None and language is None:
        errors.append(f'typical_complications[{i}] declares language_mode without language — the mode qualifies the licence and cannot stand alone.')
    errors.extend(_check_family_labels(f'typical_complications[{i}]', comp))
    return errors


def _check_family_labels(where: str, entry: dict) -> list[str]:
    """`families:` — the typed link to the PRD's declared families — must be a list of labels when present."""
    families = entry.get('families')
    if families is not None and not (isinstance(families, list) and all(isinstance(f, str) and f.strip() for f in families)):
        message = (
            f'{where}.families must be a LIST of the family labels the PRD declares (or omitted), got {families!r}. '
            'A bare string iterates as characters and links no family.'
        )
        # A list of strings with a blank label loads; anything else is off the schema's type.
        return [Advisory(message) if isinstance(families, list) and all(isinstance(f, str) for f in families) else message]
    return []


def _check_tool_optionals(i: int, tool: dict) -> list[str]:
    """Validate the OPTIONAL ToolSummary fields when present. `mode` is a strict enum on the
    the backend side — an off-enum value ('live', 'observe', a typo) would be REJECTED there, and a
    silently-missing `real` degrades to mock-treatment, so catch both here where they are cheap
    to fix. `output_fields` must be a list of non-empty strings (the tool's retrievable surface)."""
    errors: list[str] = []
    mode = tool.get('mode')
    if mode is not None and mode not in ('mock', 'real'):
        errors.append(f"agent_tools[{i}].mode must be 'mock' or 'real', got {mode!r} (map 'observe' to 'real').")
    reads = tool.get('reads')
    if reads is not None and reads not in _TOOL_READS_VALUES:
        errors.append(f'agent_tools[{i}].reads must be one of {_TOOL_READS_VALUES}, got {reads!r}.')
    elif reads == 'live' and mode != 'real':
        errors.append(f"agent_tools[{i}] is mode 'mock' but reads 'live' — a mocked tool's records come from the world state.")
    elif reads is None and isinstance(tool.get('output_fields'), list) and tool['output_fields']:
        # the backend loads it and falls back to `mode` (`tool_reads_live`), so it is advisory.
        errors.append(Advisory(
            f"agent_tools[{i}] returns records but declares no 'reads' — copy it from the tool's effective read strategy: "
            "'world_state' for mode mock, a real tool a mock's `serves:` names (or an agent tool on `observation_lane: tool_trail` "
            "with no reads_from: the mocked tools below it serve its records), or a legacy read_strategy mocked/stub; 'live' for "
            "read_strategy real or observe."
        ))
    fields = tool.get('output_fields')
    if fields is not None:
        if not isinstance(fields, list):
            errors.append(f'agent_tools[{i}].output_fields must be a list, got {type(fields).__name__}.')
        else:
            for j, f in enumerate(fields):
                if not isinstance(f, str):
                    errors.append(f'agent_tools[{i}].output_fields[{j}] must be a non-empty string.')
                elif not f.strip():
                    errors.append(Advisory(f'agent_tools[{i}].output_fields[{j}] must be a non-empty string.'))
    return errors


def _check_world_grounding(data: dict) -> list[str]:
    """Top-level enum check for `world_grounding`, mirroring the tool-`mode` enum style
    above (`_check_tool_optionals`): REQUIRED (no default — absence is reported distinctly
    from an off-enum value, same as the backend's Pydantic-required-field treatment) and must be
    one of 'seeded' | 'live' | 'hybrid'."""
    errors: list[str] = []
    value = data.get('world_grounding')
    if value is None:
        errors.append(f"Missing required field 'world_grounding' (must be one of {_WORLD_GROUNDING_VALUES}).")
    elif value not in _WORLD_GROUNDING_VALUES:
        errors.append(f"Field 'world_grounding' must be one of {_WORLD_GROUNDING_VALUES}, got {value!r}.")
    return errors


_TARGET_RULING_KINDS = ('not_in_contract', 'unobservable')
_TARGET_RULING_KEYS = ('id', 'obligation', 'ruling', 'contract_quote', 'flow_id', 'source')


def _check_target_rulings(data: dict) -> list[str]:
    """`target_rulings` (optional): audited obligations a scenario target may not require.

    The backend DROPS a row it cannot enforce rather than failing the batch, so a malformed row here is
    a ruling that silently never reaches the author or the auditor. The vendored schema refuses a
    malformed row (`required`, `additionalProperties: false`, the `ruling` enum), so that is an
    error; a repeated id is not off the schema, and the backend keeps the first — an advisory."""
    rows = data.get('target_rulings')
    if rows is None:
        return []
    if not isinstance(rows, list):
        return [f"'target_rulings' must be a list, got {type(rows).__name__}."]
    errors: list[str] = []
    seen: set[str] = set()
    for i, row in enumerate(rows):
        where = f'target_rulings[{i}]'
        if not isinstance(row, dict):
            errors.append(f'{where} must be a mapping.')
            continue
        unknown = sorted(set(row) - set(_TARGET_RULING_KEYS))
        if unknown:
            errors.append(f'{where} has unknown key(s) {unknown}; allowed: {list(_TARGET_RULING_KEYS)}.')
        for key in ('id', 'obligation'):
            if not isinstance(row.get(key), str) or not row[key].strip():
                errors.append(f"{where}.{key} must be a non-empty string.")
        if row.get('ruling') not in _TARGET_RULING_KINDS:
            errors.append(f'{where}.ruling must be one of {_TARGET_RULING_KINDS}, got {row.get("ruling")!r}.')
        for key in ('contract_quote', 'flow_id', 'source'):
            if key in row and not isinstance(row[key], str):
                errors.append(f'{where}.{key} must be a string.')
        if isinstance(row.get('id'), str) and row['id'] in seen:
            errors.append(Advisory(f'{where}.id {row["id"]!r} repeats an earlier row; the backend keeps only the first.'))
        seen.add(str(row.get('id')))
    return errors


def _tool_reads_live(tool: dict) -> bool:
    """A tool's typed `reads` fact, falling back to `mode == 'real'` when absent — the backend's `tool_reads_live`."""
    reads = tool.get('reads')
    return reads == 'live' if reads is not None else tool.get('mode') == 'real'


def _check_world_grounding_coherence(data: dict) -> list[str]:
    """Cross-validate declared `world_grounding` against `agent_tools[].reads` — verify.py's
    FIRST cross-field rule. Mirrors the backend's world-grounding
    coherence rule: ground truth is derived from the RECORD-RETURNING tools (non-empty `output_fields` — a
    tool with none serves no records and says nothing about where the world's data lives):
    none real -> 'seeded'; every one real -> 'live'; a mix -> 'hybrid'. No record-returning
    tools at all -> nothing live to declare, so 'seeded' is the only coherent value.

    That covers the DEGENERATE case, which reads like a gap but isn't: an agent with no
    record-returning tools AND no seeds is still 'seeded' — the seeded world is the whole
    world, and here it is empty. There is no fourth enum value for "no world", and none is
    needed: with an empty seeded world, seed-existence claims are unfalsifiable in both
    directions, so the grounding auditor has nothing to check and stays quiet, whereas 'live'
    would falsely promise a backend that might hold the record.

    Skipped when `world_grounding` is missing/off-enum or `agent_tools` is present but not a
    list — `_check_world_grounding` / `_check_lists` already reported that; layering a
    confusing second error about an already-broken field helps no one. A MISSING `agent_tools`
    is treated as `[]` (legal: the backend defaults it) so this
    check still mirrors the backend — otherwise a file omitting the key but declaring 'live'
    would pass here and hard-fail at the backend load. Never derives or defaults
    `world_grounding` itself, same restraint as the backend original."""
    world_grounding = data.get('world_grounding')
    if world_grounding not in _WORLD_GROUNDING_VALUES:
        return []
    tools = data.get('agent_tools', [])
    if not isinstance(tools, list):
        return []
    record_tools = [t for t in tools if isinstance(t, dict) and isinstance(t.get('output_fields'), list) and t['output_fields']]
    real_tools = [t for t in record_tools if _tool_reads_live(t)]
    mock_tools = [t for t in record_tools if not _tool_reads_live(t)]

    if not real_tools:
        expected = 'seeded'
    elif not mock_tools:
        expected = 'live'
    else:
        expected = 'hybrid'

    if world_grounding == expected:
        return []

    if world_grounding == 'seeded' and real_tools:
        names = ', '.join(str(t.get('name', '<unnamed>')) for t in real_tools)
        return [
            f"world_grounding is declared 'seeded' but {len(real_tools)} record-returning tool(s) read "
            f"live ({names}) — the seed is not the whole world for these; declare 'live' or 'hybrid'."
        ]
    if world_grounding == 'live' and mock_tools:
        names = ', '.join(str(t.get('name', '<unnamed>')) for t in mock_tools)
        return [
            f"world_grounding is declared 'live' but {len(mock_tools)} record-returning tool(s) still read "
            f"the world state ({names}) — not every record-returning tool is live; declare 'hybrid' or 'seeded'."
        ]
    # Falls through for any other mismatch: 'hybrid' declared but the tools aren't actually
    # mixed, OR 'live' (or 'seeded') declared with no record-returning tools at all to
    # justify it. Name the ACTUAL declared value (never hardcode one) alongside expected.
    return [
        f'world_grounding is declared {world_grounding!r} but the record-returning tool reads do not match it '
        f"— expected '{expected}' from {len(real_tools)} live / {len(mock_tools)} world-state record-returning tool(s)."
    ]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] in ('-h', '--help'):
        print(
            'Usage: python verify.py /path/to/scenario_variables.yaml\n'
            '\n'
            'Validates that the backend loads the YAML (exit 2 names what it would refuse) and '
            'reports, as `⚠` advisories, what it loads but generates worse from — an empty '
            'list, a blank string, an evaluator-voiced archetype. Exit 0 on success, 2 on '
            'failure; `⚠` lines are printed on both paths and never change the exit code.',
            file=sys.stderr,
        )
        return 2

    path = Path(args[0])
    errors = verify(path)
    # Printed on BOTH paths: an advisory suppressed by an unrelated error is one nobody sees on
    # the retry that fixes the error either.
    for note in advisories(path):
        print(f'  ⚠ {note}', file=sys.stderr)
    if errors:
        print(f'Validation failed for {path}:', file=sys.stderr)
        for err in errors:
            print(f'  - {err}', file=sys.stderr)
        print(f'\n{len(errors)} error(s) found. Fix the named fields and re-run.', file=sys.stderr)
        return 2
    print(f'scenario_variables.yaml at {path} is valid.')
    return 0


if __name__ == '__main__':
    try:
        sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
        from _stdio import utf8_stdio
    except ImportError:  # copied out of the plugin: no scripts/ beside it, so the streams stay as Python opened them
        def utf8_stdio() -> None:
            pass
    utf8_stdio()
    sys.exit(main())
