#!/usr/bin/env python3
"""Standalone verification of scenario_state_schema.yaml.

Self-contained: no backend dependency, no jsonschema dependency. Uses only the
standard library plus pyyaml — the sibling of `verify.py`, for the OTHER file the
plugin hands the backend.

This script IS the plugin's contract for that file. Run it before the schema is
stashed or passed to `generate_scenarios_start`: everything it checks otherwise
fails deep inside the backend's `building_graph` phase (a parser error with no
field name), or does not fail at all — it ships a scenario whose precondition is
silently empty. It validates:

1. That the YAML parses at all, and names the FLOW-STYLE TRAP when it does not:
   `{type: list[string]}` is invalid YAML (the unquoted `[` opens a flow
   sequence), while the same thing in block style, or quoted, is fine.
2. The TOP-LEVEL keys. There is no closed set there — every mapping that is not a
   known sibling is read as an ENTITY block — so a misspelled `agent_context_fields`
   validates as an entity while every spec inside it goes unchecked here and is
   discarded by the backend. A near-miss of a sibling name is an error.
3. The closed key set for an `agent_context_fields` spec — the backend reads exactly
   the keys in `_RECOGNISED` and silently discards the rest, so a JSON-Schema
   `properties:` looks authored and does nothing.
4. `ground_from` is a MAPPING keyed `entity_type`. A bare entity name is skipped
   silently server-side, and the field then grounds nothing.
5. Projection SCOPE: a grounded OBJECT field must carry `ground_from.field_map`,
   the only mechanism that limits which entity fields are merged over the authored
   dict. Unscoped, the real row overwrites the keys the narrative owns.
6. `precondition` arms only on a YAML BOOLEAN. Any other value is warned about and
   ignored server-side, so a quoted `"true"` is an inert marker.
7. Satisfiability: a field that demands content must be able to carry it. An
   object-resolving type with no declared sub-fields permits NO properties —
   populated it is rejected, empty it fails the demand.
8. `seeds:` coherence: the target must be a declared inline list of records whose
   item contract names every field the seed produces.
9. Entities, or the explicit escape hatch: a schema with no entity block is
   accepted only on `consumes_world_state: false` (the no-world declaration) —
   and the hatch still has to DECLARE something, so an entity-free schema with an
   empty `agent_context_fields` is refused (the backend cannot write the one artifact
   it would then require).
10. INJECTION: every field declares `inject` — where the wire delivers its value
   (`request` and/or `store`) — `owner` is `host` or `user`, a `generated_document`
   field is not explicitly owned by anyone else, and a schema that is BOTH
   `consumes_world_state: false` AND declares no entity block routes nothing to
   `store` (no `store` in `inject`, no `seeds:`): on a bench that declares no world that surface
   is empty by construction, so a value sent there never arrives. Both conjuncts,
   as the backend's loader defines it — a `false` schema that
   DECLARES entity types still has a store lane and is loaded, so refusing its rows
   here would trade this defect for a completeness failure on benches that work.

Every ERROR mirrors a backend behaviour: on exit 2 the skill re-authors the file
silently, so a check stricter than the backend would mutate a valid schema instead
of fixing a broken one. Two of the checks above report shapes the backend LOADS, so they
are advisories (`⚠`, exit 0) instead: a field with no `inject` (check 10's first
clause — the backend defaults it to `request` with a warning) and a grounded object with
no `field_map` (check 5 — the backend projects the row over the authored dict all the
same). Each names a one-key fix.

The one deliberate exception is the near-miss guard (check 2): the backend silently
DISCARDS a mis-keyed sibling block, and a schema whose specs vanish is broken even
though the backend accepts it. Re-authoring is the right outcome there — but it is
why that guard demands a genuine truncation (`agent_context` retains 0.67 of
`agent_context_fields`) rather than a similarity score, which would also fire on
ordinary words like `agent` and `tool`.

Exit 0 on success, 2 on errors. Errors are human-readable and name the offending
field so the skill can fix that field specifically.

Usage:
    python verify_state_schema.py /path/to/scenario_state_schema.yaml
"""

from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    # Name the OFFENDING interpreter, not a bare `pip install` — same posture as the
    # sibling verify.py; the two must not disagree.
    print(
        f'ERROR: pyyaml is required to run verify_state_schema.py, and {sys.executable} does not have it.\n'
        f"Re-run with an interpreter that does — the customer project's venv interpreter is "
        f'usually the easiest one — or install it there: {sys.executable} -m pip install pyyaml',
        file=sys.stderr,
    )
    sys.exit(3)


# --- Schema description (mirrors the backend's initial_state loader) ---

# The backend loader's closed key set for an `agent_context_fields` spec, read from the vendored `agent_context_spec_keys`
# contract block (scripts/contracts/contracts.json). The literal is ONLY the fallback for a payload
# that predates the block or a copy of this script run outside the plugin tree. It must equal the vendored block.
_RECOGNISED_FALLBACK = (
    'type',
    'description',
    'item_fields',
    'item_specs',
    'required',
    'precondition',
    'ground_from',
    'seeds',
    'sample_rows',
    'default_value',
    'generated_document',
    'inject',
    'owner',
    'enum_source',
    'enum_citation',
)


def _recognised_keys() -> tuple:
    try:
        scripts = str(Path(__file__).resolve().parents[3] / 'scripts')
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from plurai_contracts import contract  # noqa: PLC0415 - repo script, optional at runtime

        return tuple(contract('agent_context_spec_keys', default=list(_RECOGNISED_FALLBACK)))
    except (ImportError, IndexError):  # a copy outside the plugin tree: no scripts/ to read the contract from
        return _RECOGNISED_FALLBACK


_RECOGNISED = _recognised_keys()

# The two things the Tier-1 wire can do with a field's value, and the only members `inject`
# takes. `request` = the turn-1 payload spread (also `seed_state` param 1, also the mock's
# `get_simulation_context().agent_context`); `store` = rows mirrored into the world the mock
# and `seed_state` param 2 receive. A prior conversation is the per-scenario `resume_seed.yaml`
# LANE, not a field channel, and is deliberately absent.
_INJECT_CHANNELS = ('request', 'store')

# Whose value it is: host-supplied session context, or something the simulated USER hands over
# (a document). Default `host`.
_OWNERS = ('host', 'user')

# Spellings that look authored and are read NOWHERE. Named so the error can say what to
# write instead, rather than only that the key is unknown.
_TRAPS = {
    'properties': 'item_specs',
    'additional_properties': 'item_specs',
}

# Top-level keys that are NOT entity blocks.
_SIBLING_KEYS = ('agent_context_fields', 'tool_verbs', 'tool_contracts', 'consumes_world_state')

# How much of a sibling key a near-miss must retain to count as a TRUNCATION rather than a
# different word: `agent_context` retains 12/18 of `agent_context_fields` (0.67); `agent` retains
# 5/18 (0.28) and `tool` 4/9 of `tool_verbs` — telling either of those authors "did you mean …?"
# would be wrong.
# Mirrors the backend loader's truncation threshold (same value and rule). This script is standalone and cannot import the backend.
_TRUNCATION_RETAINED = 0.6

# Container keys the loader unwraps before reading entities.
_ENTITY_WRAPPER_KEYS = ('entities', 'entity_types')

# The keys an ENTITY block declares. A top-level mapping carrying one of these is an entity
# whatever its name — which is what keeps the near-miss check off a real entity.
_ENTITY_KEYS = ('required_fields', 'field_specs', 'fields', 'invariants')

# Type tokens the loader maps to a NON-object JSON type (the backend loader's JSON type aliases).
# Everything else that is a bare alpha token is a class-name cross-reference, i.e. an object.
_SCALAR_ALIASES = ('str', 'string', 'int', 'integer', 'float', 'number', 'decimal', 'money', 'bool', 'boolean', 'list', 'array', 'any')

# Parametric heads that resolve to an object (`dict[str, Any]`, `mapping[K, V]`).
_OBJECT_HEADS = ('dict', 'object', 'mapping')

# Adjective prefixes the loader strips when resolving a class-name token to an entity.
_CLASS_NAME_PREFIXES = ('enriched', 'mock', 'fake', 'simulated')


def _parsed(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    except yaml.YAMLError:
        return None
    return raw if isinstance(raw, dict) else None


def verify(path: Path) -> list[str]:
    """Return a list of human-readable errors. Empty list = valid."""
    if not path.is_file():
        return [f'scenario_state_schema.yaml not found at {path}']

    try:
        raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    except yaml.YAMLError as e:
        return [_parse_error(path, e)]

    if not isinstance(raw, dict):
        return [f'Expected top-level mapping in {path}, got {type(raw).__name__}']

    entities = _entity_blocks(raw)
    errors: list[str] = []
    errors.extend(_check_top_level_keys(raw))
    errors.extend(_check_entity_blocks(raw, entities))
    errors.extend(_check_agent_context_fields(raw, entities))
    errors.extend(_check_entities(raw, entities))
    return errors


def advisories(path: Path) -> list[str]:
    """Shapes the backend ACCEPTS and should not be given — reported, never refused.

    This validator's rule is that it must never exceed the backend: refusing a file the backend
    accepts re-authors something that works. A shape the backend loads past (a field with no
    `inject`, an unscoped grounded object) or whose semantics are about to change is still worth
    saying out loud, so it gets its own tier — printed, exit code untouched.
    """
    raw = _parsed(path)
    if raw is None:
        return []  # unreadable / unparseable is verify()'s finding, not a second one
    entities = _entity_blocks(raw)
    notes = list(_check_declared_empty_agent_context(raw, entities))
    fields = raw.get('agent_context_fields')
    for name, spec in (fields.items() if isinstance(fields, dict) else ()):
        if isinstance(spec, dict) and spec:
            notes.extend(_check_inject_declared(name, spec))
            notes.extend(_check_ground_from_scope(name, spec))
    return notes


def _parse_error(path: Path, exc: yaml.YAMLError) -> str:
    """A parse error, with the flow-style trap named when its signature is present.

    `{type: list[string]}` is the one malformation this file attracts: block style and a
    quoted scalar both accept `list[string]`, so it looks like valid YAML until someone
    collapses the mapping onto one line. pyyaml reports it as an unexpected '[' while
    parsing a flow mapping — the diagnostic is accurate and unrecognisable."""
    text = str(exc)
    hint = ''
    if 'flow mapping' in text and "but got '['" in text:
        hint = (
            '\n  This is the FLOW-STYLE TRAP: inside `{...}` an unquoted `[` opens a flow sequence, '
            'so `{type: list[string]}` is invalid YAML.\n'
            '  Write the spec in block style, or quote the type: `{type: "list[string]"}`.'
        )
    return f'YAML parse error in {path}: {text}{hint}'


def _entity_blocks(data: dict) -> dict[str, dict]:
    """The entity blocks, from either layout the loader accepts: one block per entity at the
    top level, or all of them under an `entities:` / `entity_types:` wrapper."""
    for wrapper in _ENTITY_WRAPPER_KEYS:
        block = data.get(wrapper)
        if isinstance(block, dict) and block:
            return {name: body for name, body in block.items() if isinstance(body, dict)}
    skip = set(_SIBLING_KEYS) | set(_ENTITY_WRAPPER_KEYS)
    return {name: body for name, body in data.items() if name not in skip and isinstance(body, dict)}


def _normalised(key: object) -> str:
    return re.sub(r'[^a-z0-9]', '', str(key).lower())


# Sibling keys by normalised spelling, so the scan below does not re-normalise the list per call.
_SIBLING_BY_NORM = {_normalised(sibling): sibling for sibling in _SIBLING_KEYS}


def _near_sibling(key: object) -> str | None:
    """The sibling key `key` was probably meant to be, or None."""
    norm = _normalised(key)
    if not norm:
        return None
    if norm in _SIBLING_BY_NORM:
        return _SIBLING_BY_NORM[norm]
    # A TRUNCATION is the commonest hand-authoring miss and ratio alone does not catch it:
    # `agent_context` vs `agent_context_fields` scores 0.80, just under the cutoff. This only
    # sharpens the HINT — what REFUSES a mis-keyed block is _check_entity_blocks, which mirrors
    # the backend's own rule (any fieldless entity) and so holds whatever the block is called.
    for norm_sibling, sibling in _SIBLING_BY_NORM.items():
        if not (norm_sibling.startswith(norm) or norm.startswith(norm_sibling)):
            continue
        if min(len(norm_sibling), len(norm)) / max(len(norm_sibling), len(norm)) >= _TRUNCATION_RETAINED:
            return sibling
    close = difflib.get_close_matches(norm, list(_SIBLING_BY_NORM), n=1, cutoff=0.85)
    return _SIBLING_BY_NORM[close[0]] if close else None


def _check_top_level_keys(data: dict) -> list[str]:
    """A misspelled SIBLING is read as an ENTITY block: it satisfies the entity check, every
    spec inside it goes unchecked, and the backend discards the block. The top level has no closed
    key set (entity names are free), so the guard is a near-miss — and a block carrying an
    entity's own keys is an entity whatever it is called.

    A near-miss must RETAIN most of the sibling it truncates (``_TRUNCATION_RETAINED``):
    `agent_context` is a typo of `agent_context_fields`, `agent` is an ordinary word, and
    refusing the latter would re-author a file the backend accepts.

    Reports the mis-keyed block itself. `_check_entity_blocks` carries the RULE for the case
    where such a block is the only entity, and stays quiet about the exact keys named HERE so
    one defect is reported once."""
    return [
        f"Top-level key '{key}' is read by NOTHING — did you mean `{sibling}`? As written the backend takes it for an "
        f'ENTITY block, so every spec inside it is discarded and this validator checks none of them.'
        for key, sibling in _misfiled_sibling_keys(data).items()
    ]


def _misfiled_sibling_keys(data: dict) -> dict[str, str]:
    """Top-level keys that read as a truncated SIBLING -> the sibling they truncate.

    The set `_check_top_level_keys` reports, and therefore the exact set `_check_entity_blocks`
    may treat as already named. Deduping on name SHAPE instead let a mis-keyed block that
    happens to carry an entity key (`agent_context:` with only `invariants:`) fall through
    both checks — accepted here, refused by the backend, which is the round trip this mirror
    exists to prevent."""
    misfiled: dict[str, str] = {}
    for key, body in data.items():
        if key in _SIBLING_KEYS or key in _ENTITY_WRAPPER_KEYS:
            continue
        if isinstance(body, dict) and any(k in body for k in _ENTITY_KEYS):
            continue
        if sibling := _near_sibling(key):
            misfiled[key] = sibling
    return misfiled


def _entity_field_count(body: dict) -> int:
    """How many fields the backend's `_parse_entity` would read off this block."""
    specs = body.get('field_specs') or body.get('fields') or {}
    if isinstance(specs, dict):
        return len(specs)
    if isinstance(specs, list):
        return sum(1 for spec in specs if isinstance(spec, dict) and (spec.get('name') or spec.get('field')))
    return 0


def _check_entity_blocks(data: dict, entities: dict[str, dict]) -> list[str]:
    """the backend refuses a schema whose entities are ALL fieldless (`state_schema_defects`).

    A fieldless entity builds an empty-able `<name>: array of {}` nothing can populate, while
    its presence makes `initial_world_state.yaml` REQUIRED — so when there is nothing else to
    seed, no scenario can complete. The near-miss check above is a HINT keyed on the name; this
    is the rule, and it holds when the mis-keyed block is called something the hint cannot
    recognise.

    Scoped to ALL-fieldless exactly as the backend is: a fieldless entity BESIDE a healthy one is a
    documentation-only declaration (`invariants` with no `field_specs`) that the extractor
    satisfies with `[]`, and the world file its siblings require is written anyway. Refusing
    that here would re-author a file the backend accepts — the one thing this validator must never
    do."""
    fieldless = [name for name, body in entities.items() if not _entity_field_count(body)]
    if not fieldless or len(fieldless) != len(entities):
        return []
    # Quiet only about the keys _check_top_level_keys ACTUALLY named — not about every key that
    # merely looks like a near-miss, which let an entity-shaped one fall through both checks.
    if all(name in _misfiled_sibling_keys(data) for name in fieldless):
        return []
    hint = f' Did you mean the sibling key `{sibling}`?' if (sibling := _near_sibling(fieldless[0])) else ''
    names = ', '.join(repr(n) for n in fieldless)
    return [
        f'Every entity in this schema declares no fields ({names}), so the backend refuses it: each builds an '
        f'empty-able `<name>: array of {{}}` nothing can populate, while their presence makes '
        f'initial_world_state.yaml REQUIRED and no scenario can complete.{hint} Give an entity a '
        f'`field_specs` block, or fix the key.'
    ]


def _check_agent_context_fields(data: dict, entities: dict[str, dict]) -> list[str]:
    fields = data.get('agent_context_fields')
    if fields is None:
        return []
    if not isinstance(fields, dict):
        return [f"'agent_context_fields' must be a mapping of field name -> spec, got {type(fields).__name__}."]

    entity_names = tuple(entities)
    errors: list[str] = []
    for name, spec in fields.items():
        if not spec:
            errors.append(f'agent_context_fields.{name} declares no spec — give it at least a `type`.')
            continue
        if not isinstance(spec, dict):
            errors.append(f'agent_context_fields.{name} must be a mapping, got {type(spec).__name__}.')
            continue
        errors.extend(_check_spec_keys(name, spec))
        errors.extend(_check_type(name, spec))
        errors.extend(_check_precondition(name, spec))
        errors.extend(_check_ground_from(name, spec))
        errors.extend(_check_satisfiable(name, spec, entity_names))
        errors.extend(_check_seeds(name, spec, entities))
        # Exactly the backend loader's rule for a schema with no store lane:
        #     consumes_world_state is False AND not entities
        # A live schema that DECLARES entity types is NOT store-less — the loader's docstring says
        # refusing its rows "would trade one defect for a completeness failure on benches that work
        # today". One conjunct here refused those schemas while the backend loaded them.
        errors.extend(_check_injection(name, spec, live=data.get('consumes_world_state') is False and not entities))
    return errors


def _declared_channels(spec: dict) -> tuple[str, ...]:
    """`inject` as a tuple, whatever shape it was written in."""
    raw = spec.get('inject')
    if isinstance(raw, list):
        return tuple(str(v) for v in raw)
    return (str(raw),) if raw is not None else ()


def _check_inject_declared(name: str, spec: dict) -> list[str]:
    """ADVISORY: a field with no `inject`. The backend DEFAULTS it to `request` and emits one WARNING
    per schema load, so the schema loads; routing that is inferred is still routing nobody
    declared, and the fix is one key whose value is the one the backend would have guessed."""
    if spec.get('inject') is not None:
        return []
    return [
        f'agent_context_fields.{name} declares no `inject`. Every field must say which channel carries its '
        f'value: `request` (the turn-1 payload by default, or a `file` content part on the turn the '
        f'scenario hands it over — Step 6.5; also handed to `seed_state` as param 1) or `store` '
        f'(rows mirrored into the world the mock and `seed_state` param 2 serve); a list declares both. The backend '
        f'defaults a missing `inject` to `request` with a warning, so an INSTALLED schema still runs — but a '
        f'schema authored now states it. Write `inject: request` unless the field also seeds world rows. On an '
        f'existing schema that predates the key: when Step 4b of extract-scenario-variables authored the file, '
        f're-run it, which authors the key; when env-gen emitted it (Step 4b is skipped there, and the schema is '
        f'written VERBATIM from the env job), add the key IN PLACE — no re-run authors it, and paying for another '
        f'env-gen job re-materialises the same bytes and the same red.'
    ]


def _check_injection(name: str, spec: dict, *, live: bool) -> list[str]:
    """`inject` and `owner` — the declared ROUTING of the field's value. A MISSING `inject` is
    `_check_inject_declared`'s advisory; a declared one is checked here.

    `store` is the world the mock serves and `seed_state` receives as param 2. On
    `consumes_world_state: false` that surface is EMPTY BY CONSTRUCTION (every tool reads a real
    backend, so seeded rows would be invisible), and `seeds:` routes there implicitly — so a
    field declaring either on a live schema declares a delivery the run cannot make, and the
    value silently never arrives. Refused at the declaration rather than diagnosed from a dead
    turn 1.
    """
    errors: list[str] = []
    raw = spec.get('inject')
    if raw is not None and (not isinstance(raw, (str, list)) or (isinstance(raw, list) and not raw)):
        errors.append(
            f'agent_context_fields.{name}.inject must be a channel name or a non-empty list of them '
            f'({", ".join(_INJECT_CHANNELS)}), got {type(raw).__name__}.'
        )
    elif raw is not None:
        unknown = sorted({c for c in _declared_channels(spec) if c not in _INJECT_CHANNELS})
        if unknown:
            errors.append(
                f'agent_context_fields.{name}.inject names {", ".join(unknown)}, which the wire cannot do with a '
                f'field value. The channels are exactly {", ".join(_INJECT_CHANNELS)} (a prior conversation is the '
                f'per-scenario `resume_seed.yaml` lane, not a field channel).'
            )

    # Exactly the backend loader's rule for who owns a field:
    #     owner = spec.get('owner', 'user' if spec.get('generated_document') is not None else 'host')
    # An OMITTED owner beside `generated_document` is IMPLIED `user` and loads — the loader's own
    # comment says defaulting it to `host` "would refuse them at load — a flag day". Reading a
    # bare `spec.get('owner')` here refused, at exit 2, a schema backend accepts.
    owner = spec.get('owner', 'user' if spec.get('generated_document') is not None else 'host')
    if owner is not None and owner not in _OWNERS:
        errors.append(
            f'agent_context_fields.{name}.owner must be one of {", ".join(_OWNERS)} (default `host`), got '
            f'{owner!r}. `host` = session context the product supplies; `user` = something the simulated user '
            f'hands over, which the user simulator is then told it is carrying.'
        )
    if spec.get('generated_document') is not None and owner != 'user':
        errors.append(
            # NAMES THE OWNER IT SAW. "but not `owner: user`" reads as covering the OMITTED case,
            # which this branch cannot reach at all (an omitted owner is implied `user` five lines
            # up, exactly as the backend implies it) — so the one schema shape it does refuse was
            # described by a sentence that also described a shape it accepts.
            f'agent_context_fields.{name} declares `generated_document` and `owner: {owner}`. A generated document '
            f'is authored per scenario and HANDED OVER by the simulated user — it is not host session context. '
            f'Under any other owner the user simulator is never told it is carrying the document, so it cannot '
            f'answer a question about what the document says. Set `owner: user`, or drop the key (an omitted '
            f'owner beside `generated_document` is implied `user` and loads).'
        )

    if live:
        seeds_implies_store = spec.get('seeds') is not None
        if seeds_implies_store or 'store' in _declared_channels(spec):
            wrote, remedy = (
                ('`seeds:`, which routes to `store` implicitly', 'Drop the `seeds:` block')
                if seeds_implies_store
                else ('`store` in `inject`',
                      'Route the field to `request` alone — the turn-1 payload by default, or a `file` '
                      'content part on the turn the scenario hands it over (Step 6.5)')
            )
            errors.append(
                f'agent_context_fields.{name} declares {wrote}, but the schema declares `consumes_world_state: '
                f'false`. That is the no-world declaration (no declared world type), so the seeded store '
                f'is EMPTY by construction and nothing routed to it is ever observable. {remedy}, or drop '
                f'`consumes_world_state: false` and declare the entity the rows belong to.'
            )
    return errors


def _check_spec_keys(name: str, spec: dict) -> list[str]:
    errors: list[str] = []
    for key in spec:
        if key in _RECOGNISED:
            continue
        replacement = _TRAPS.get(str(key))
        if replacement:
            errors.append(f'agent_context_fields.{name}.{key} is read NOWHERE by the backend (it is JSON-Schema vocabulary) — use `{replacement}`.')
        else:
            errors.append(f'agent_context_fields.{name}.{key} is not a recognised key and is silently discarded (recognised: {", ".join(_RECOGNISED)}).')
    return errors


def _check_type(name: str, spec: dict) -> list[str]:
    """`type` is MANDATORY and must be a non-empty STRING. Omitted, the field resolves to
    `string` and any `item_specs` beside it is dropped; a list here is the flow trap that
    PARSED — `{type: [string]}` yields a list, which the loader cannot turn into a type."""
    if 'type' not in spec:
        return [f'agent_context_fields.{name} declares no `type` — without one the field resolves to `string` and its sub-field contract is dropped.']
    value = spec['type']
    if isinstance(value, str) and value.strip():
        return []
    return [f'agent_context_fields.{name}.type must be a non-empty type expression string, got {value!r}.']


def _check_precondition(name: str, spec: dict) -> list[str]:
    """`precondition` arms on a YAML BOOLEAN and nothing else. The backend warns and treats any
    other value — including the quoted `"true"` this file's type expressions need — as NOT a
    precondition, so the marker is inert and the field it was meant to guarantee ships empty."""
    if 'precondition' not in spec:
        return []
    value = spec['precondition']
    if isinstance(value, bool):
        return []
    return [
        f'agent_context_fields.{name}.precondition must be an UNQUOTED YAML boolean (`true` / `false`), got {value!r} — '
        f'the backend treats every other value as NOT a precondition, so the marker is inert and the field can ship empty.'
    ]


def _check_ground_from(name: str, spec: dict) -> list[str]:
    if 'ground_from' not in spec:
        return []
    gf = spec['ground_from']
    if not isinstance(gf, dict):
        return [
            f'agent_context_fields.{name}.ground_from must be a MAPPING keyed `entity_type` '
            f'(e.g. `{{entity_type: product}}`), got {type(gf).__name__} — a bare entity name is skipped silently and grounds nothing.'
        ]
    errors: list[str] = []
    entity_type = gf.get('entity_type')
    if not (isinstance(entity_type, str) and entity_type.strip()):
        errors.append(f'agent_context_fields.{name}.ground_from declares no `entity_type` — the backend skips the declaration and the field grounds nothing.')
    for key in ('field_map', 'coerce'):
        if key in gf and not isinstance(gf[key], dict):
            errors.append(f'agent_context_fields.{name}.ground_from.{key} must be a mapping, got {type(gf[key]).__name__}.')
    return errors


def _check_ground_from_scope(name: str, spec: dict) -> list[str]:
    """ADVISORY: a grounded OBJECT field is scoped by `ground_from.field_map` — and by nothing else.
    The backend loads an unscoped one and projects all the same, so this is said, never refused.

    The backend projects the real row onto the field and MERGES it over the authored dict. It picks
    the projected keys from `field_map` FIRST; only without one does it fall back to the field's
    declared sub-keys, which it reads via `item_specs` in PREFERENCE to `item_fields`. So beside
    an `item_specs` block the `item_fields` list is never read.

    Precisely (the backend `agent_context_grounding.entity_supplied_keys` / `_project_entity`): the
    keys actually projected are the declared ones the ENTITY TYPE supplies — a declared key no
    candidate record carries is left authored, and a key one sibling carries but the chosen record
    lacks is written `null`. So the exposure is narrower than "every declared key is overwritten",
    and still real: any narrative-owned key sharing a name with an entity field is replaced without
    a word. `field_map` is the only thing that bounds it. Scalars are exempt — one value, nothing
    to scope."""
    if 'ground_from' not in spec or not isinstance(spec['ground_from'], dict):
        return []
    if not _is_object_expr(spec.get('type')):
        return []
    if spec['ground_from'].get('field_map'):
        return []
    if spec.get('item_fields') and not spec.get('item_specs'):
        return []  # no item_specs to outrank it, so item_fields does scope the projection
    outranked = ' — `item_specs` outranks `item_fields`, so an item_fields list beside it is never read' if spec.get('item_specs') else ''
    return [
        f'agent_context_fields.{name} is `{spec.get("type")}` with `ground_from` but no `field_map` to SCOPE the projection{outranked}. '
        f'Keys the corpus rows SUPPLY are merged over the authored dict, and a key some sibling row carries but the chosen '
        f'one lacks is written null — so any narrative-owned key that happens to share a name with an entity field is '
        f'silently replaced. (Keys no entity of that type supplies are left authored.) Add `field_map` naming the identity keys only, e.g. '
        f'`ground_from: {{entity_type: {spec["ground_from"].get("entity_type", "<entity>")}, field_map: {{sku: sku, title: title}}}}`.'
    ]


def _check_satisfiable(name: str, spec: dict, entity_names: tuple[str, ...]) -> list[str]:
    """The BLOCKER this validator exists for: a field that demands content but declares no
    sub-fields resolves to an object permitting none — unsatisfiable in both directions.

    Scoped exactly as the backend scopes it. An OBJECT-resolving type is refused at LOAD under
    either demand. A LIST-of-objects is refused only under `precondition: true`, at the
    precondition gate: its items permit no properties either, so every row can only be `{}`,
    which reads as empty content. `required` on a list is a mere presence check that `[]`
    satisfies — flagging it would block a schema backend accepts."""
    if spec.get('item_specs') or spec.get('item_fields'):
        return []
    type_expr = spec.get('type')
    demand = 'precondition: true' if spec.get('precondition') is True else ('required' if spec.get('required') else None)
    if demand is None:
        return []
    fix = 'add `item_specs` (typed, preferred) or `item_fields`'
    if _resolves_to_empty_object(type_expr, entity_names):
        return [
            f'agent_context_fields.{name} is `{type_expr}` with `{demand}` but declares no sub-fields — {fix}. '
            f'As written no value satisfies it: populated it is rejected as an unexpected property, empty it fails the demand.'
        ]
    if demand.startswith('precondition') and _is_list_of_objects(type_expr):
        return [
            f'agent_context_fields.{name} is `{type_expr}` with `{demand}` but declares no sub-fields — {fix}. '
            f'Its items permit NO properties, so every row can only be `{{}}` and the precondition gate reads the field as empty.'
        ]
    return []


def _check_seeds(name: str, spec: dict, entities: dict[str, dict]) -> list[str]:
    """`seeds:` mirrors the field's rows onto a HEAD entity's inline list. The target must BE
    such a list and its item contract must name every field the seed produces — the backend runs
    this same check before the graph build and RAISES on it, so a mismatch costs the whole job.

    Undeterminable shapes are skipped, never guessed, exactly as the backend skips them."""
    seeds = spec.get('seeds')
    if seeds is None:
        return []
    if not isinstance(seeds, dict):
        return [f'agent_context_fields.{name}.seeds must be a mapping declaring `target: <entity>.<field>`, got {type(seeds).__name__}.']
    target = seeds.get('target')
    if not (isinstance(target, str) and '.' in target):
        return [
            f'agent_context_fields.{name}.seeds declares target {target!r} — it must read `<entity>.<field>` '
            f'(e.g. `target: product.notes`); the backend skips any other form and the rows are never mirrored.'
        ]
    entity_name, _, target_field = target.partition('.')
    entity = entities.get(entity_name)
    if not isinstance(entity, dict):
        return []  # entity declared elsewhere / not at all — the backend skips rather than contradict
    field_specs = entity.get('field_specs') or entity.get('fields')
    if not isinstance(field_specs, dict):
        return []  # list-form or absent field specs — undeterminable
    target_spec = field_specs.get(target_field)
    if not isinstance(target_spec, dict):
        return [
            f'agent_context_fields.{name}.seeds targets {target}, but the `{entity_name}` entity declares no `{target_field}` field. '
            f'Seeding writes a list of row-dicts there and the validator rejects every one as an additional property. '
            f'Declare `{target_field}` on `{entity_name}` as `type: "list[dict]"` with the seeded row fields in `item_fields` / `item_specs`.'
        ]
    if not _is_list_of_objects(target_spec.get('type')):
        return [
            f'agent_context_fields.{name}.seeds targets {target}, but `{target_field}` is `{target_spec.get("type")}` — not a list of records. '
            f'Seeding writes a list of row-dicts there and the validator rejects it. Declare it `type: "list[dict]"` with an item contract; '
            f'do NOT flatten the row fields onto the entity.'
        ]
    field_map = seeds.get('field_map')
    if isinstance(field_map, dict) and field_map:
        produced = {str(v) for v in field_map.values()}
    elif _is_list_of_objects(spec.get('type')):
        produced = set(_declared_sub_keys(spec))  # identity seed — the rows carry the source's own keys
    else:
        return []  # identity seed off a non-list source: undeterminable, as it is server-side
    if not produced:
        return []  # source contract open — the backend cannot assert a mismatch either
    generate_id = seeds.get('generate_id')
    if isinstance(generate_id, str) and generate_id:
        produced.add(generate_id)
    allowed = set(_declared_sub_keys(target_spec))
    if not allowed:
        return [
            f'agent_context_fields.{name}.seeds seeds {target}, but {target} declares no item fields, so its inline records accept NOTHING — '
            f'seeding writes {sorted(produced)} and every one is rejected as an additional property. Give `{target_field}` an `item_fields` '
            f'(or `item_specs`) contract listing them.'
        ]
    offenders = produced - allowed
    if offenders:
        return [
            f'agent_context_fields.{name}.seeds produces field(s) {sorted(offenders)} that {target} items do not declare '
            f'(allowed: {sorted(allowed)}). Rename the field_map target(s) to the declared field names, or declare them on `{target_field}`.'
        ]
    return []


def _declared_sub_keys(spec: dict) -> list[str]:
    """The sub-field names a spec declares, with the backend's precedence: `item_specs` first, and
    `item_fields` read ONLY when there is no item_specs mapping."""
    item_specs = spec.get('item_specs')
    if isinstance(item_specs, dict):
        return [str(k) for k in item_specs]
    item_fields = spec.get('item_fields')
    return [str(f) for f in item_fields] if isinstance(item_fields, list) else []


def _is_object_expr(raw_type: object) -> bool:
    """Does this type expression resolve to a JSON OBJECT? Mirrors the loader's
    `_type_expr_to_schema`, so the match survives case, whitespace and parametrisation
    (`Dict`, `dict[str, Any]`, `mapping[K, V]`, `dict | null`) — a literal comparison skipped
    the very fields these checks exist for."""
    if not isinstance(raw_type, str) or not raw_type.strip():
        return False
    expr = raw_type.strip()
    parts = [p.strip() for p in expr.split('|')]
    non_null = [p for p in parts if p.lower() not in ('null', 'none')]
    if len(non_null) != len(parts):
        return bool(non_null) and _is_object_expr(non_null[0])
    if '[' in expr and expr.endswith(']'):
        return expr.partition('[')[0].strip().lower() in _OBJECT_HEADS
    token = expr.lower()
    if token in ('dict', 'object'):
        return True
    if token in _SCALAR_ALIASES or token.startswith('enum'):
        return False
    return expr[:1].isalpha()  # a class-name token: an entity cross-reference, or an open object


def _is_list_of_objects(raw_type: object) -> bool:
    """`list[dict]` / `array[<Entity>]` — a list whose ITEMS are objects."""
    if not isinstance(raw_type, str):
        return False
    expr = raw_type.strip()
    if '[' not in expr or not expr.endswith(']'):
        return False
    head, _, tail = expr.partition('[')
    return head.strip().lower() in ('list', 'array') and _is_object_expr(tail[:-1])


def _resolves_to_empty_object(raw_type: object, entity_names: tuple[str, ...]) -> bool:
    """Object-typed AND carrying no properties of its own — the loader's `_open_object_schema`,
    the one shape it refuses outright beside a demand for content. A class-name token that
    cross-references a declared entity inherits that entity's contract and is satisfiable."""
    if not _is_object_expr(raw_type):
        return False
    expr = str(raw_type).strip()
    non_null = [p.strip() for p in expr.split('|') if p.strip().lower() not in ('null', 'none')]
    expr = non_null[0] if non_null else expr
    if ('[' in expr and expr.endswith(']')) or expr.lower() in ('dict', 'object'):
        return True
    return not _resolves_to_entity(expr, entity_names)


def _class_token(token: object) -> str:
    """Lowercase + alpha-only + adjective prefix stripped, as the loader normalises a
    class-name token before matching it to an entity (`EnrichedAnnotation` -> `annotation`)."""
    bare = ''.join(c for c in str(token) if c.isalpha()).lower()
    for prefix in _CLASS_NAME_PREFIXES:
        if bare.startswith(prefix) and len(bare) > len(prefix):
            return bare[len(prefix) :]
    return bare


def _resolves_to_entity(token: str, entity_names: tuple[str, ...]) -> bool:
    target = _class_token(token)
    if not target:
        return False
    return any(norm == target or norm == target.rstrip('s') or target == norm.rstrip('s') for norm in (_class_token(e) for e in entity_names))


def _check_entities(data: dict, entities: dict[str, dict]) -> list[str]:
    """An entity-free schema is accepted ONLY on an explicit `consumes_world_state: false`
    — otherwise a schema that merely FORGOT its entities is refused at load, loudly.

    The escape hatch is not a blank cheque: the backend (``state_schema_defects``) refuses a
    definition that declares NOTHING — no entities and no agent_context_fields — because its
    composite schema gets no properties, so the `agent_context.yaml` the completeness gate
    requires can never be written. This validator certified exactly that shape on a real bench
    and the job died after authoring all three scenarios."""
    if entities:
        return []
    if data.get('consumes_world_state') is not False:
        return [
            'The schema declares no entity blocks. The backend accepts that only with an explicit '
            '`consumes_world_state: false` (the no-world declaration: the config declares no world type, so seeded rows '
            'would be invisible; a declared world gets its blocks from `world_schema.py`). Declare at least one entity, or set that key.'
        ]
    fields = data.get('agent_context_fields')
    if not (isinstance(fields, dict) and fields):
        return [
            'The schema declares NOTHING — no entity blocks and no `agent_context_fields`. The backend refuses this: the '
            'composite schema gets no properties at all, so `agent_context.yaml` (which it requires for every '
            'entity-free schema) can never be written and every scenario fails after authoring. For a genuinely '
            'stateless agent, write NO scenario_state_schema.yaml at all; otherwise give `agent_context_fields` at '
            'least one field.'
        ]
    return []


def _check_declared_empty_agent_context(data: dict, entities: dict[str, dict]) -> list[str]:
    """`agent_context_fields: {}` written out BESIDE real entity blocks.

    An explicitly empty map is not the same statement as omitting the key: it declares an
    agent-context surface carrying no properties. That is the same root as the declares-NOTHING
    refusal — a composite schema whose agent_context half can be required and cannot be written —
    and the entity blocks here are what keep it out of that hard error today. Deleting the key
    costs nothing (the entities seed on their own) and removes the ambiguity."""
    fields = data.get('agent_context_fields')
    if not isinstance(fields, dict) or fields or not entities:
        return []
    return [
        '`agent_context_fields` is declared and EMPTY. That is not the same as omitting it: it declares an '
        'agent-context surface with no properties, and on the current backend such a surface can be REQUIRED '
        'and unwritable — the same root as the declares-nothing refusal. Delete the key (the entity blocks seed '
        'on their own), or give it at least one field. Reported, not refused: the backend accepts this shape today, '
        'and this validator must never re-author a file the backend takes.'
    ]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] in ('-h', '--help'):
        print(
            'Usage: python verify_state_schema.py /path/to/scenario_state_schema.yaml\n'
            '\n'
            'Validates that the YAML parses and that every agent_context_fields spec uses keys '
            'the backend actually reads, grounds through a ground_from MAPPING, and is satisfiable. '
            'Exit 0 on success, 2 on failure.',
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
    print(f'scenario_state_schema.yaml at {path} is valid.')
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
