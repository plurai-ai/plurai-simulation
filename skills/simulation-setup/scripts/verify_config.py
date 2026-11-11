"""Static analyzer for a generated ``plurai.config.yaml``.

The plugin's ``/simulation-scan-agent`` skill emits ``plurai.config.yaml`` as the
machine-readable spec the backend's rich mock generator consumes. The
backend reads ``descriptor.pre_conditions`` / ``post_conditions`` /
``error_cases`` per tool and generates a mock that maintains those
invariants — that's the contract.

This verifier catches the cases where the descriptor is too vague for the
backend to do anything useful:

  - Write-tools (``writes_to`` non-null) with no ``post_conditions``.
    The backend has no idea what state changes to model. Backend ships a
    stub that maintains no coherence; multi-turn simulations look broken.

  - Tool-name drift between ``plurai.config.yaml`` and ``simulate.py``.
    A handler registered for ``"lookup_user"`` while the config calls it
    ``"get_user"`` → the mock service never routes calls.

  - Declared mock-needing tools with NO tool wrapping in ``simulate.py``
    (``tools_not_wrapped`` — moved here from verify_simulate.py check 9,
    gated on the config so tool-less agents don't false-positive).

  - Cross-document drift between ``plurai.config.yaml`` and (when present)
    ``scenario_variables.yaml`` for entity types.

  - A ``shape:`` declaration that contradicts the harness (``shape: pipeline``
    over a simulate.py that binds only ``invoke_agent``, or the reverse). The
    declaration is what the runner dispatches on, so the contradiction is not
    cosmetic: the bench is driven through a hook it does not export.

  - NO ``shape:`` over a harness that binds BOTH hooks (``shape_absent_both_hooks``,
    warn). The default route is taken, the other hook is never called, and every
    policy scoped to it scores against an empty result — zero verdicts on a green
    run. A warn, not a refusal: the bench can serve the route it is driven on.

  - An ``agent.io:`` declaration that names a part kind nothing can send, or
    omits ``text``, which every turn carries. The setup smoke turns this
    declaration into probe turns against the real child, so a malformed one
    leaves a channel nobody confirmed. (Riding a ``shape: pipeline`` bench is a
    WARN: the block is inert on that route, which is worth saying and is not
    wrong — a harness may export both hooks.)

``Finding`` schema mirrors ``verify_simulate.py`` so the dispatching skill
can render both in one pass.

Usage::

    python verify_config.py path/to/plurai.config.yaml [--simulate path/to/simulate.py] [--findings-out PATH]

Every ``fail`` is a gate finding (``scripts/gate_ledger.py``), printed as a ``finding:`` line. Exits 1
while something HOLDS — a security or invariant finding, or an advisory one neither repaired nor
overruled in ``<workdir>/artifacts/.gate_overrules.jsonl`` — and 0 otherwise; 2 when it reached no verdict.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import enum
import functools
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:  # below 3.11: the same str-valued enum, interpolating as its value (`f'{member}'`), as enum.StrEnum does

    class StrEnum(str, enum.Enum):
        def __str__(self) -> str:
            return str(self.value)

        def __format__(self, spec: str) -> str:
            return format(str(self.value), spec)

try:
    import yaml
except ImportError:
    print(
        f'ERROR: pyyaml is required to run verify_config.py but is not importable '
        f'from {sys.executable}. Install it into that interpreter: '
        f'{sys.executable} -m pip install pyyaml',
        file=sys.stderr,
    )
    sys.exit(3)

# Pydantic schema canonically lives in plurai-mockenv (extracted from plurai-sdk).
# The SDK re-exports the NAMES (`from plurai import ToolDescriptor`) but the
# `plurai.descriptor_schema` MODULE was deleted before v0.3.0, so the fallback below
# resolves only on pre-0.3 installs — it is old-install tolerance, not a live path.
# Prefer plurai_mockenv — the engine package generated envs install. The verifier
# runs against customer-generated configs; the
# schema check below short-circuits with a warn-severity
# ``descriptor_schema_check_skipped`` finding when both imports fail — silent
# no-op would be worse than the bug this check exists to catch. The cross-field
# invariant ("writes_to ⇒ post_conditions non-empty") still fires from
# _check_write_tools_have_post_conditions regardless of import state. Catches
# Exception (not just ImportError) so a botched install with a SyntaxError or
# partial pydantic shadowing the v2 API also surfaces visibly rather than
# crashing the verifier.
try:
    from plurai_mockenv.descriptor_schema import ToolDescriptor
    from pydantic import ValidationError
except Exception:  # pragma: no cover — core absent; try the legacy SDK shim
    try:
        from plurai.descriptor_schema import ToolDescriptor
        from pydantic import ValidationError
    except Exception:  # pragma: no cover — exercised only with broken install
        ToolDescriptor = None  # type: ignore[assignment]
        ValidationError = None  # type: ignore[assignment]  # never read when ToolDescriptor is None


#: The agent's INPUT declaration. A ROOT key — every declaration any reader dispatches on is a
#: root key, and the repo already pays for a finding (`shape_declared_not_at_root`) teaching that
#: one level down is read by nobody. Spelled with the dot because that is the name the vocabulary
#: was specified under; YAML carries it as an ordinary key.
_AGENT_IO_KEY = 'agent.io'

#: Everything `agent.io` may declare — and nothing else. An unknown subkey is REFUSED BY NAME
#: rather than warned about, for the reason `render_actions`' shape is: a declaration nobody
#: reads is worse than no declaration, because the author believes a channel is covered.
_AGENT_IO_KNOWN_KEYS = ('input', 'may_open', 'opens', 'choice_text', 'choice_text_source', 'channels')

#: `agent.io.channels` (C1): the inputs the agent takes OUTSIDE the message content, each `{name, kind, field, source,
#: handover?}`; a scenario plants into a channel by its name. The kinds are READ from the backend's `input_channel_kinds`
#: block where it is reachable (`_channel_kinds`), this tuple where it is not — the SDK's `plurai.agent_io.CHANNEL_KINDS`.
_CHANNEL_KINDS_FALLBACK = ('file_by_reference', 'structured_context', 'between_turn_action', 'host_injected_message')
_CHANNEL_REQUIRED = ('name', 'kind', 'field', 'source')
_CHANNEL_KEYS = (*_CHANNEL_REQUIRED, 'handover')
_CITATION = re.compile(r'^\S+:\d+(?:-\d+)?$')

#: The closed vocabulary of an `agent.io.choice_text` value: what the harness's RESUME code does with
#: the words the user types beside an interrupt choice — hands them to the agent, or drops them (an
#: approval that resumes with no new input). The SDK's spelling (`plurai.agent_io.CHOICE_TEXT_VALUES`).
_CHOICE_TEXT_VALUES = ('delivered', 'dropped')

#: The MESSAGE CONTENT PART kinds a bench may declare it accepts — OpenAI's part `type` values,
#: which is the spelling the SDK's reader carries (`plurai.agent_io.INPUT_PART_KINDS`) and the
#: spelling that goes on the turn. ONE vocabulary across the two: a kind the SDK cannot put on a
#: turn is one this gate must not accept, and `image` spelled here against `image_url` there would
#: make both readers agree the declaration is broken while neither says which word is wrong.
#: A kind outside this tuple resolves to nothing and is refused BY NAME rather than falling
#: through to text — which would certify a file-reading agent on a text turn.
_INPUT_PART_KINDS = ('text', 'image_url', 'file')

#: The part kinds that hand the agent a DOCUMENT. Each needs a `.document_fields` entry beside the
#: config — the one trigger that has scenario generation author a document to hand over.
_DOCUMENT_PART_KINDS = ('image_url', 'file')

#: The part kind a scenario's handed-over document rides on. A scenario set generated from a
#: `.document_fields` entry hands the document as a `file` content part (an `image_url` part is
#: the harness's own extra, declared when the agent reads images).
_HANDOVER_PART_KIND = 'file'


class Code(StrEnum):
    """Every finding code THIS script can emit — the closed set, declared.

    RESTATED, NOT IMPORTED. Each verifier declares its own closed set: ``verify_simulate.py`` and
    ``verify_smoke.py`` each carry their own; the overlapping members are pinned equal by a test,
    not by an import. Out of scope on purpose: ``skills/generate-evals/scripts/verify_policies.py``
    is a different shape (policy lints, not a ``Finding`` stream) and pulling it in here would
    couple two skills that never ship together.

    WHY A DECLARED SET RATHER THAN A RUNTIME SWEEP. Two codes below
    (``DESCRIPTOR_SCHEMA_INVALID``, ``DESCRIPTOR_RETURNS_STATE_FIELD_NOT_IN_MOCK_BODY``) are
    unreachable without the descriptor schema importable — ``_check_descriptor_schema`` short-
    circuits to ``DESCRIPTOR_SCHEMA_CHECK_SKIPPED`` — and CI's pip line is ``pytest pyyaml``. A
    "run everything and collect what comes out" sweep is structurally blind to them in the very
    lane that gates this file, so the emitted set has to be SAID.

    ``StrEnum`` (the shim above it below 3.11), never a bare ``class Code(str, enum.Enum)``: ``Finding.render()`` interpolates the
    code, and a ``str``-mixin enum formats as ``Code.X`` rather than ``x`` — every operator line
    and every downstream grep silently rewritten while the ``==`` comparisons still pass.

    A code here is a CONTRACT with 23 non-producer readers (skill bodies, references, tests, the
    hooks) plus the backend and the SDK. Adding one is one member + one documentation row. Renaming
    one is not a rename at the call sites: add the new spelling and keep the old as an alias, so
    the readers that have not moved still resolve.

    Every emission site's code slot reads this enum (never a literal), every member has a
    symptom→cause row, and every documented code is a member.
    """

    AGENT_HOST_USER_SURFACES_UNDECLARED = 'agent_host_user_surfaces_undeclared'  # the agent-host route (Agentforce) declares no `world_state.user_surfaces`; its Agent API shows the user only the agent's replies
    AGENT_HOST_RENDER_ACTIONS_UNDECLARED = 'agent_host_render_actions_undeclared'  # the agent-host route (Agentforce) declares no `render_actions`; the persona's view of its typed actions is then never filtered
    AGENT_IO_DECLARED_NOT_AT_ROOT = 'agent_io_declared_not_at_root'  # `io:` nested under a parent key; only the ROOT `agent.io` is read
    AGENT_IO_INERT_ON_PIPELINE_SHAPE = 'agent_io_inert_on_pipeline_shape'  # warn: `shape: pipeline` has no turn to carry parts on
    AGENT_IO_CHANNELS_SHAPE = 'agent_io_channels_shape'  # `channels:` is not a list of `{name, kind, field, source, handover?}` with unique names and a `<path>:<line>` source
    AGENT_IO_CHANNEL_KIND = 'agent_io_channel_kind'  # a channel `kind` outside `_CHANNEL_KINDS` — no scenario can plant it
    AGENT_IO_CHOICE_TEXT_SHAPE = 'agent_io_choice_text_shape'  # `choice_text:` is not a non-empty `{<choice>: delivered|dropped}`
    AGENT_IO_CHOICE_TEXT_SOURCE = 'agent_io_choice_text_source'  # `choice_text` without a `choice_text_source: <path>:<line>`, or a source with no fact
    AGENT_IO_INPUT_KIND = 'agent_io_input_kind'  # a part kind outside `_INPUT_PART_KINDS` — nothing can send it
    AGENT_IO_INPUT_SHAPE = 'agent_io_input_shape'  # `agent.io.input` is not a list of non-blank, unique part names
    AGENT_IO_INPUT_TEXT = 'agent_io_input_text'  # warn: `input:` omits `text`, the channel every scenario uses
    AGENT_IO_MAY_OPEN_SHAPE = 'agent_io_may_open_shape'  # `may_open:` is not a YAML boolean
    AGENT_IO_OPENS_SHAPE = 'agent_io_opens_shape'  # `opens:` is not `always`, or contradicts `may_open: false`
    AGENT_IO_SHAPE = 'agent_io_shape'  # `agent.io:` is not a mapping
    AGENT_IO_UNKNOWN_KEY = 'agent_io_unknown_key'  # a sub-key outside `_AGENT_IO_KNOWN_KEYS`
    BLOCK_SHAPE = 'block_shape'  # a top-level block that must be a mapping is not one
    CLOSED_CLASS_DETECTORS_SHAPE = 'closed_class_detectors_shape'  # not `{<entity>.<field>: regex}`, or the regex does not compile
    CLOSED_CLASS_DETECTORS_SUBJECT = 'closed_class_detectors_subject'  # warn: subject is not `<entity>.<field>`, so it joins no PRD fact
    DELIVERY_SURFACE_NO_WRITER = 'delivery_surface_no_writer'  # a delivery surface's rows never reach the world the run reads
    DELIVERY_SURFACE_UNDETERMINED = 'delivery_surface_undetermined'  # warn: whether world_snapshot() returns a delivery surface's rows cannot be read statically
    DESCRIPTOR_ARGS_BARE_STRING = 'descriptor_args_bare_string'  # warn: `descriptor.args` is prose, not the structured dict
    DESCRIPTOR_RETURNS_BARE_STRING = 'descriptor_returns_bare_string'  # warn: `descriptor.returns` is prose, not the structured dict
    DESCRIPTOR_RETURNS_STATE_FIELD_NOT_IN_MOCK_BODY = 'descriptor_returns_state_field_not_in_mock_body'  # warn: a declared state-bearing return field the mock body never sets
    DESCRIPTOR_RETURNS_TYPE_UNTYPED = 'descriptor_returns_type_untyped'  # a mocked tool's `returns.type` is outside the vocabulary or has no provenance
    DESCRIPTOR_ROLE_UNKNOWN = 'descriptor_role_unknown'  # `descriptor.role` is outside the vocabulary the backend dispatches on
    DESCRIPTOR_SCHEMA_CHECK_SKIPPED = 'descriptor_schema_check_skipped'  # warn: no importable descriptor schema, so the block was not validated
    DESCRIPTOR_SCHEMA_INVALID = 'descriptor_schema_invalid'  # a `descriptor:` block the backend's `ToolDescriptor` refuses
    FILE_INPUT_WITHOUT_DOCUMENT_FIELD = 'file_input_without_document_field'  # `input:` names `file`/`image_url` and no `.document_fields` entry exists
    ESSENTIAL_STORAGE_NOT_MONKEYPATCHED = 'essential_storage_not_monkeypatched'  # the stub is declared but simulate.py never patches it
    ENDPOINTS_UNDECLARED = 'endpoints_undeclared'  # `egress` says `none_discovered` while tools name a store: no endpoint is pinned, so nothing fails closed
    ESSENTIAL_STORAGE_STUB_MISSING = 'essential_storage_stub_missing'  # a `mode: real` reading tool's storage is in no `stubs.modules`
    FIELD_PRESENCE_ON_NULLABLE_ARG = 'field_presence_on_nullable_arg'  # a `must_be_present` rule on an arg declared `nullable: true`
    GUARD_FIELD_UNTESTED = 'guard_field_untested'  # a field a declared result guard reads off a mocked tool is asserted by no test in test_mock_contract.py
    GUARD_SEES_MOCK = 'guard_sees_mock'  # a declared guard reads a store or a result field the mock never reaches: the agent's guard runs blind
    GUARDS_INVALID = 'guards_invalid'  # `guards:` is not a list of `{name, cite, watches, reads: store|result, via|fields}` over declared tools
    HARNESS_SINGLE_INSTANCE_INVALID = 'harness_single_instance_invalid'  # `harness.single_instance` is not a list of non-blank resource strings; the runner refuses it at start
    HANDOVER_KIND_NOT_DECLARED = 'handover_kind_not_declared'  # a `.document_fields` entry hands over documents and `agent.io.input` omits `file`
    HARNESS_UNKNOWABLE = 'harness_unknowable'  # warn: no harness source was read, so every config<->harness check stood down
    HELPER_NAMING_CONVENTION_DRIFT = 'helper_naming_convention_drift'  # warn: a near-miss helper filename the discovery walk ignores
    HELPER_UNPARSEABLE = 'helper_unparseable'  # warn: a split helper does not parse; AST checks against it are skipped
    HELPER_UNREADABLE = 'helper_unreadable'  # warn: a split helper could not be read at all
    IDENTITY_SURFACE_NOT_MOCKED = 'identity_surface_not_mocked'  # the declared identity surface is not a mocked tool
    INPUT_FILE_CHANNEL_UNDECLARED = 'input_file_channel_undeclared'  # the agent reads files the user names, and neither `file` nor `.document_fields` is declared
    INLINE_ACTION_WITHOUT_HOOK = 'inline_action_without_hook'  # an action on rows inside a list field, and simulate.py exports no `apply_user_action`
    INVALID_RUN_DRIVER = 'invalid_run_driver'  # `workspace.run_driver` is outside the closed set the hooks dispatch on
    KNOWLEDGE_BLOCK_INVALID = 'knowledge_block_invalid'  # warn: the `knowledge:` RECORD is malformed; nothing reads it either way
    NATIVE_TOOLS_WRONG_FRAMEWORK = 'native_tools_wrong_framework'  # `agent.native_tools` names tools on a framework `record_native_tools.py` never records them for
    MAP_STATUS_MODE_MISMATCH = 'map_status_mode_mismatch'  # warn: a map tool's `status` disagrees with its config `mode` (`mock` is `mocked`)
    LIVE_WRITE_UNGRANTED = 'live_write_ungranted'  # a tool writes the live system and `.host_effects_consent` does not grant it (warn at `agent_host`, else fail)
    HOST_EFFECT_UNGRANTED = 'host_effect_ungranted'  # a live tool that acts (shell, files, network, schedule) is ungranted and nothing on its runtime confines it
    TOOL_UNCLASSIFIED = 'tool_unclassified'  # the bench runs a tool for real that the map does not type (`writes`, `effects`, `runs`)
    REAL_READ_STORE_UNCONFIRMED = 'real_read_store_unconfirmed'  # a `mode: real` tool reads a record store `world_state.confirmation` does not name
    LIVE_READ_UNNAMED = 'live_read_unnamed'  # a corpus tool reads live through a simulated store's endpoint and that store's question does not name it
    INDEX_OVER_SIMULATED_RECORDS = 'index_over_simulated_records'  # a live corpus tool's hits are records of a simulated type, or the map's `grounding.records_of` does not type them
    READ_THROUGH_INVALID = 'read_through_invalid'  # `read_through:` on a tool that is not `mode: real`, beside `read_strategy: mocked`, or `via` is not a list of dotted accessors
    READ_THROUGH_UNBOUND = 'read_through_unbound'  # a `read_through.via` accessor the harness never rebinds: the real reader never sees the mock's writes
    READ_THROUGH_SWALLOWED = 'read_through_swallowed'  # warn: an overlay bound to a `read_through.via` accessor catches every exception and raises nothing
    READ_THROUGH_UNLINKED = 'read_through_unlinked'  # a `read_through` reader no `tool_graph` edge from a mock-world writer targets, so nothing says what the overlay serves
    RENDER_ACTIONS_SHAPE = 'render_actions_shape'  # `render_actions:` is malformed, so both scan shells refuse it
    RENDER_ACTIONS_UNDECLARED = 'render_actions_undeclared'  # warn: no top-level `render_actions`, so the callable surface is open and an undeclared call is never charged
    ROW_SOURCE_UNCITED = 'row_source_uncited'  # a `seam: tool_body` read tool's row source (derive_tool_facts `row_sources`) has no business rule `(source: <that line>)` and no `skipped_code` `{cite, returns: every_row, evidence}` in the called library
    RULE_SCOPE_UNCITED = 'rule_scope_uncited'  # a mocked tool's rule names some values of a closed set its request carries, with no cited span stating that scope
    RULE_TEST_VARIANT_MISSING = 'rule_test_variant_missing'  # a rule code's tests in test_mock_contract.py skip a request variant the rule governs
    SHAPE_ABSENT_BOTH_HOOKS = 'shape_absent_both_hooks'  # warn: simulate.py binds BOTH shape hooks and no `shape:` picks the route
    SHAPE_ABSENT_PIPELINE = 'shape_absent_pipeline'  # simulate.py binds only `invoke_pipeline` and no `shape:` says so
    SHAPE_CONTRADICTS_HOOKS = 'shape_contradicts_hooks'  # the declared `shape:` names a hook simulate.py does not bind
    SHAPE_DECLARED_NOT_AT_ROOT = 'shape_declared_not_at_root'  # `shape:` one level down; the runner reads only the root key
    SHAPE_VALUE = 'shape_value'  # `shape:` is outside `dialog` / `pipeline`
    SIMULATE_UNPARSEABLE = 'simulate_unparseable'  # warn: simulate.py does not parse; AST checks are skipped
    SIMULATE_UNREADABLE = 'simulate_unreadable'  # warn: simulate.py could not be read at all
    STORAGE_ROLE_UNDECLARED = 'storage_role_undeclared'  # warn: a stub shares a package root with a `mode: real` tool and declares no role
    STRUCTURED_CONTEXT_UNTYPED = 'structured_context_untyped'  # warn: an `agent_context` field a `structured_context` channel names is a bare `dict` with no `item_specs`
    TOOL_IN_CONFIG_MISSING_FROM_SIMULATE = 'tool_in_config_missing_from_simulate'  # a declared tool has no `@plurai.mock` wrapper
    TOOL_IN_SIMULATE_MISSING_FROM_CONFIG = 'tool_in_simulate_missing_from_config'  # a `@plurai.mock` wrapper names a tool the config omits
    TOOL_REQUIRES_CONFIRMATION_INVALID = 'tool_requires_confirmation_invalid'  # `requires_user_confirmation` is not a boolean on a tool entry, or sits inside `descriptor:`
    TOOL_INVOCATION_INVALID = 'tool_invocation_invalid'  # `invocation` is not `fixed`/`agent` on a tool entry, or sits inside `descriptor:`
    TOOL_ERROR_CHANNEL_INVALID = 'tool_error_channel_invalid'  # `error_channel` / `error_channel_source` is outside its vocabulary, or sits inside `descriptor:`
    ERROR_CHANNEL_UNTYPED = 'error_channel_untyped'  # warn, one per config: mocked tools declare no `error_channel`; a failed mocked call reaches the agent as a value
    TOOL_EXPOSED_IN_INVALID = 'tool_exposed_in_invalid'  # `exposed_in` is not a list of scope names on a tool entry, or sits inside `descriptor:`
    TOOL_SEAM_INVALID = 'tool_seam_invalid'  # a `mode: mock` tool declares no C12 `seam`, one outside the vocabulary, `tool_body` without `seam_reason` + `skipped_code`, or a `serves:` link to no other tool or without a `serves_cite`
    PAYLOAD_CODEC_INVALID = 'payload_codec_invalid'  # `tools.<name>.payload_codecs` is off a transport/client mock, inside `descriptor:`, or names a path or codec chain the harness cannot apply
    PAYLOAD_CODEC_UNSUPPORTED_SURFACE = 'payload_codec_unsupported_surface'  # `payload_codecs` on a `code` action-channel tool, whose interception applies no codecs
    SEAM_BELOW_INSTALLED_TOOL = 'seam_below_installed_tool'  # a `seam: transport` mock with no `payload_codecs` serves a tool whose path runs through an installed tool class
    TOOL_STATE_UPDATES_INVALID = 'tool_state_updates_invalid'  # `tools.<name>.state_updates` is malformed, on a non-mock tool, or reads an undeclared return field
    SUBAGENT_CALL_UNDECLARED = 'subagent_call_undeclared'  # a map `subagents[].call` names no `tools` key, so the delegation call is off the declared surface
    SUBAGENT_TOOLS_UNSTAGED = 'subagent_tools_unstaged'  # a tool the map says a sub-agent calls is no `tools` key, or its `exposed_in` omits that sub-agent's `call`
    SUBAGENT_CALL_UNSTATED = 'subagent_call_unstated'  # warn: a map `subagents[]` entry has no `call` key, or the config carries the unread `agent.sub_agents`
    AGENT_TOOL_UNDECLARED = 'agent_tool_undeclared'  # a map tool is no `tools` key, no render action and no `agent.native_tools` entry: off the declared surface and coverage
    STRATEGY_WITHOUT_MOCK = 'strategy_without_mock'  # warn, one per config: tools resolve to `write_strategy: mocked` and no tool on the bench is mock-backed
    TOOLS_NOT_WRAPPED = 'tools_not_wrapped'  # mock-needing tools are declared and simulate.py wraps none of them
    UNKNOWN_ROOT_KEY = 'unknown_root_key'  # a root key no reader knows — the declaration never happens
    UNKNOWN_WORKSPACE_KEY = 'unknown_workspace_key'  # a `workspace:` sub-key no reader knows
    USER_SURFACES_INVALID = 'user_surfaces_invalid'  # `world_state.user_surfaces` does not validate against the declared world
    WORKSPACE_SHAPE = 'workspace_shape'  # `workspace:` is neither the legacy scalar nor a mapping
    WORLD_CHECK_REFUSED = 'world_check_refused'  # a `world_state.entity_types.<type>.checks` entry is malformed, cites the PRD or no data file, reads an undeclared field, or has a literal / computed rule a real row of that file breaks
    WORLD_CHECK_SET_STALE = 'world_check_set_stale'  # warn: a declared `in` set is not its store's list as the file holds it now, or a real row's value falls outside it
    WORLD_REFERENCE_ROWS_INVALID = 'world_reference_rows_invalid'  # warn: a type's `reference_rows:` names no readable list under the agent root, or one the sweep cannot use as a reference set
    WORLD_CHECK_UNSWEPT = 'world_check_unswept'  # warn: the sweep proposes an `in` set from a recorded reference store and no declared check cites that store
    WORLD_CHECK_STORES_UNCLASSIFIED = 'world_check_stores_unclassified'  # warn: no reference store is recorded, and an id list above the sweep's cap holds every value of a swept field
    WORLD_CONFIRMATION_INVALID = 'world_confirmation_invalid'  # a `world_state.confirmation` entry confirms nothing: bad answer, no target, or a connection string labelled harness-created
    WORLD_FIXTURE_KEY_NOT_ID = 'world_fixture_key_not_id'  # a seeded-world literal in test_mock_contract.py keys a row by other than its own id, or repeats a key
    WORLD_FIELD_REQUIRED_UNDECLARED = 'world_field_required_undeclared'  # a declared `world_state` field has no boolean `required:`
    WORLD_ID_FIELD_UNDECIDED = 'world_id_field_undecided'  # a type a `references:` field names has no `id` / `<type>_id` field and no `id_field:`
    WORLD_ID_FIELD_UNKNOWN = 'world_id_field_unknown'  # `id_field:` names a field its type does not declare
    WORLD_OWNER_VALUE = 'world_owner_value'  # `world_state:` is unwalkable, or the owner word is outside mock/agent/external
    WORLD_SLOTS_VALUE = 'world_slots_value'  # `world_state.slots` is not an int >= 1, or sits beside an owner other than external/agent
    WORLD_REAL_WRITER_UNOWNED = 'world_real_writer_unowned'  # warn: a tool writes a real service while the world is the mock's
    WORLD_SHARED_OK_VALUE = 'world_shared_ok_value'  # `world_state.shared_world_ok` is not a boolean
    WORLD_OWNED_TYPES_INVALID = 'world_owned_types_invalid'  # `world_state.owned_types` is not a list of declared entity types
    WORLD_SERVED_WITHOUT_MOCK = 'world_served_without_mock'  # advisory: a `mode: real` tool reaches a faked store (effective `read_strategy: mocked` or explicit `write_strategy: mocked`) that no seam mock `serves:`
    WORLD_RESET_UNCONFIRMED = 'world_reset_unconfirmed'  # warn: simulate.py exports `reset` on an agent/external world no store confirmation names
    WORLD_SNAPSHOT_MERGES_MOCK = 'world_snapshot_merges_mock'  # `world_snapshot()` merges the mock's world by hand instead of declaring `owned_types`
    WORLD_ROWS_UNTYPED = 'world_rows_untyped'  # a `world_state` field holding rows (`list[dict]`) declares no typed `item_specs`
    WRITE_TOOL_MISSING_POST_CONDITIONS = 'write_tool_missing_post_conditions'  # `writes_to` is set and `post_conditions` is empty
    WRITE_TOOL_READER_UNDECLARED = 'write_tool_reader_undeclared'  # a mocked write tool names no reader on `tool_graph`, and no `target: null`
    WRITER_READER_MODE_SPLIT = 'writer_reader_mode_split'  # hard: a write lands in the mock world and a real read (a `read_through` included) reads its store
    WRITER_SEAM_STORE_UNREAD = 'writer_seam_store_unread'  # advisory: a `client` / `entity` seam writes a store no tool reads through the mock world


#: The read/parse failure a discovered source hit, by (role, stage). DECLARED as a table rather
#: than spelled inline as `'helper_x' if role == 'helper' else 'simulate_x'`: a conditional
#: expression in the code slot is a second place the vocabulary lives, and the lint cannot see
#: through one.
_READ_FAILURE: dict[tuple[str, str], Code] = {
    ('simulate', 'read'): Code.SIMULATE_UNREADABLE,
    ('helper', 'read'): Code.HELPER_UNREADABLE,
    ('simulate', 'parse'): Code.SIMULATE_UNPARSEABLE,
    ('helper', 'parse'): Code.HELPER_UNPARSEABLE,
}

#: The `descriptor:` fields that may be written as bare prose, each with the code that says so.
#: ONE table rather than a `('args', 'returns')` tuple to iterate plus an f-string to spell the
#: code from it: the field list and the vocabulary are the same fact, and two of them drift.
_BARE_STRING_CODE: dict[str, Code] = {
    'args': Code.DESCRIPTOR_ARGS_BARE_STRING,
    'returns': Code.DESCRIPTOR_RETURNS_BARE_STRING,
}


@dataclass(frozen=True)
class Finding:
    """One contract violation. Mirrors the shape used by ``verify_simulate.py``.

    ``filename`` is the file the line number refers to. ``None`` means
    "simulate.py" (default; back-compat). Set when the finding originates
    from a discovered helper (e.g. ``_storage.py`` / ``_mocks.py``), so
    the customer-facing line number isn't off-by-N from the simulate.py
    they're reading.
    """

    #: EXACTLY `fail` or `warn` — the two words `main()` buckets. Anything else is dropped on
    #: the floor by the CLI the skills instruct (`test_every_finding_severity_is_one_main_buckets`
    #: parses this file and refuses a third word).
    severity: str
    #: A member of `Code` — never a literal. The annotation is the rule; every call site is checked against it.
    code: Code
    message: str
    skill_step: str
    line: int | None = None
    filename: str | None = None

    def render(self) -> str:
        if self.line is not None and self.filename:
            loc = f' ({self.filename}:{self.line})'
        elif self.line is not None:
            loc = f' (line {self.line})'
        else:
            loc = ''
        marker = '✗' if self.severity == 'fail' else '!'
        return f'  {marker} [{self.code}] {self.message}{loc} — see SKILL.md {self.skill_step}'


# ---------------------------------------------------------------------------
# Multi-file discovery — shared between verify_config / verify_simulate
# ---------------------------------------------------------------------------

# Helper modules the customer may split simulate.py into (Step 8.4). Order
# matches the natural side-effect dependency: _storage.py is typically
# imported by _mocks.py (for _SHARED_STORAGE), so we list it first.
_SPLIT_HELPER_NAMES: tuple[str, ...] = ('_storage.py', '_mocks.py')

# Near-miss filenames that suggest a customer split their file but didn't
# follow the exact-name convention. Discovery scans for these too — when
# found alongside an absent canonical name, the verifier emits a hint.
_HELPER_NEAR_MISS: dict[str, str] = {
    'storage.py': '_storage.py',
    'mocks.py': '_mocks.py',
    'storage_helpers.py': '_storage.py',
    'mock_helpers.py': '_mocks.py',
}


@dataclass(frozen=True)
class _ParsedSource:
    """One simulate.py-or-helper file that's been read + parsed (or failed)."""

    filename: str   # e.g. "simulate.py" or "_mocks.py" — for findings + display
    text: str
    tree: ast.Module | None  # None when read or parse failed
    role: str        # 'simulate' | 'helper'


def _discover_sources(simulate_path: Path) -> tuple[list[_ParsedSource], list[Finding]]:
    """Read + parse simulate.py and any adjacent split helpers.

    Returns ``(sources, findings)``:

    - ``sources`` is the list of successfully-read+parsed files. simulate.py
      is always first when readable; helpers follow in canonical order.
    - ``findings`` carries one entry per read/parse failure plus one for
      each near-miss filename that suggests the customer mis-named a
      helper. These are warn-severity by design — they tell the customer
      WHICH file is broken / WHICH file the verifier ignored, so they don't
      chase a phantom problem in simulate.py.
    """
    findings: list[Finding] = []
    sources: list[_ParsedSource] = []

    def _read_and_parse(path: Path, role: str) -> _ParsedSource | None:
        try:
            text = path.read_text(encoding='utf-8')
        except OSError as exc:
            findings.append(Finding(
                severity='warn',
                code=_READ_FAILURE[(role, 'read')],
                message=(
                    f'could not read {path.name}: {type(exc).__name__}: {exc}. '
                    f'AST-dependent checks against this file will be skipped.'
                ),
                skill_step='Step 8',
                filename=path.name,
            ))
            return None
        try:
            tree = ast.parse(text, filename=path.name)
        except SyntaxError as exc:
            findings.append(Finding(
                severity='warn',
                code=_READ_FAILURE[(role, 'parse')],
                message=(
                    f'{path.name} does not parse: {exc.msg}. AST-dependent '
                    f'checks against this file will be skipped — fix the '
                    f'syntax error first.'
                ),
                skill_step='Step 8',
                line=exc.lineno,
                filename=path.name,
            ))
            return _ParsedSource(filename=path.name, text=text, tree=None, role=role)
        return _ParsedSource(filename=path.name, text=text, tree=tree, role=role)

    sim_source = _read_and_parse(simulate_path, role='simulate')
    if sim_source is not None:
        sources.append(sim_source)

    # Canonical-name helpers: ingest those that exist.
    for helper_name in _SPLIT_HELPER_NAMES:
        helper_path = simulate_path.parent / helper_name
        if helper_path.is_file():
            parsed = _read_and_parse(helper_path, role='helper')
            if parsed is not None:
                sources.append(parsed)

    # Near-miss helpers: warn but DON'T ingest. If the customer named a
    # helper `mocks.py` (no underscore), the verifier shouldn't merge it
    # silently — emit a hint so they rename to the canonical form.
    for near_miss, canonical in _HELPER_NEAR_MISS.items():
        near_miss_path = simulate_path.parent / near_miss
        canonical_path = simulate_path.parent / canonical
        if near_miss_path.is_file() and not canonical_path.is_file():
            findings.append(Finding(
                severity='warn',
                code=Code.HELPER_NAMING_CONVENTION_DRIFT,
                message=(
                    f'found {near_miss!r} adjacent to simulate.py but the '
                    f'verifier auto-discovers only {canonical!r}. Rename '
                    f'or symlink to pick up the AST-dependent checks against '
                    f'this helper.'
                ),
                skill_step='Step 8.4',
                filename=near_miss,
            ))

    return sources, findings


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file with a useful error if it's malformed."""
    text = path.read_text(encoding='utf-8')
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f'{path} is not valid YAML: {exc}') from exc
    if not isinstance(data, dict):
        raise ValueError(f'{path}: top-level YAML must be a mapping, got {type(data).__name__}')
    return data


# Top-level keys the backend + SDK actually read from plurai.config.yaml. Derived from the
# annotated example (references/example_plurai_config.yaml — workspace/project/anonymize/
# agent/agent_context/tools/world_state/tool_graph/stubs) plus the SDK's own top-level
# reads: plurai/_mocking/code_surface.py (`agent`, `tools`) and plurai/ingest/posture.py
# (`anonymize`). tests/test_verify_config.py pins this set to the example file, so example
# drift fails loudly here instead of silently widening/narrowing the check.
#
# `knowledge` is the ONE key here that nothing in the pipeline READS — see
# _check_knowledge_block. It is sanctioned because prompt-inlined knowledge is first-class
# to Step 9b and /build-rag-corpus Step 1, and with no home in the config operators were
# recording it in YAML comments, where no skill can find it and _check_root_keys would have
# warned on any key they invented for it.
#
# `egress` IS read — by the SDK's egress preflight (plurai/egress.py), which refuses a scored
# run when a declared production host is reachable. It is written by /simulation-scan-agent
# Step 3d (scripts/record_egress.py), not by hand.
#
# `render_actions` is the agent's RENDER / ACTION surface: the client-side verbs it emits that
# are not tool calls (UI renders, navigation, a hand-off) and so never appear under `tools:` —
# a render-action agent declares `tools: {}` by design. Read by lint_prd.py (every declared verb
# must be documented; the PRD's `RENDER_ACTIONS` fact must close the surface), validated here,
# and READ BY THE BACKEND: the backend loads
# the same key and raises `RenderActionsConfigError` on a non-list, a blank entry or a duplicate,
# while the SDK's `plurai/scan_contract_cli.py` EXITS 65 (refused) on the same shapes. So this check is a
# pre-flight for a real consumer, not a plugin-local convention, and the three must agree on what
# malformed means — the reason the duplicate check above compares stripped names.
#
# THIS COMMENT IS THE ONE STATEMENT OF THAT FACT, and the references cite it rather than
# restating it: a fact stated in four places goes stale in three of them. No loaded reference may
# say the backend ignores a key that has a named backend consumer.
#
# `shape` is the bench's own statement of WHICH HOOK the runner must drive — `dialog`
# (`invoke_agent`, a conversation) or `pipeline` (`invoke_pipeline`, one structured call). It is
# here rather than in a `.shape` sidecar because it is repo-invariant: the same on every machine
# and in every clone, unlike `.run_mode` / `.turn_timeout`, which describe one workspace. It is
# read by the runner (the declaration dispatches; the hooks are a HINT that never chooses a
# route) and cross-checked below against the hooks the harness actually binds. ABSENT is legal for
# a harness binding `invoke_agent` — that bench is driven as a dialog exactly as it always was.
# Absent over an `invoke_pipeline`-only harness is REFUSED: the turn loop has no hook to drive.
#
# `knowledge_out_of_audience` is the customer's answer on a hosted agent's Knowledge articles that
# are published only outside the audience the agent answers: `include` or `exclude`. Written by the
# Agentforce setup when it asks, read by its knowledge export; absent means not asked yet. It is
# not in the example on purpose: a scan copies the example, and a copied value would stand in for
# an answer nobody gave.
#
# `approve_harness_edits` is a retired declaration (hooks/_run_driver.py RETIRED_DECLARATION_KEYS): a config
# that carries it reads clean, and it changes nothing, because a harness repair is applied without a question.
# Never written by a scan, and left out of the example.
#
# `harness.entry_wrapper` names the operator-written runner `simulate.py` imports to reach the agent
# (a path relative to `plurai_sim/`). The scan writes it and the operator approves it with the map;
# `scripts/harness_fingerprint.py` reads it to hash the file and `--entry-wrapper` checks it.
#
# `workspace` is the one root key with a VALIDATED SUB-KEY SURFACE (_KNOWN_WORKSPACE_KEYS below).
# Historically a bare scalar (`workspace: acme`), it may also be a mapping carrying
# `run_driver: interactive | headless` — WHO is running the command, read by the flow-fidelity hooks
# (`hooks/_run_driver.py`) to decide whether a gate may ask a question at all. Absent ⇒ interactive
# ⇒ byte-identical to every run before the key existed.
#
# `agent.io` is the INPUT half of the same idea `render_actions` is the output half of: what the
# agent can be HANDED, declared instead of discovered. `input:` lists the MESSAGE CONTENT PART
# kinds the agent accepts (`text`, `image_url`, `file` — OpenAI's part `type` values, the SDK's own
# vocabulary) and `may_open:` says whether it can be driven
# with no user message at all (an agent that opens the conversation). There is no `output:` here
# on purpose — `render_actions` already declares the emitted surface, in this file and in both
# consumers, and a second declaration of it could only disagree with the first. The hook
# SIGNATURE carries none of this (every dialog harness in the tree is
# `invoke_agent(payload: dict, config: dict | None = None) -> dict`), so the scan AGENT writes it
# from reading the agent, exactly as it writes `render_actions:`; the AST gives one negative hint
# (`payload['messages'][-1]` cannot serve an empty opener), and the SETUP SMOKE confirms every
# declared part through the real child. Scoped to message content: an `agent_context` field the
# agent reads (a `page_context` precondition) is declared there and must not be declared here
# twice, where the two can disagree.
_KNOWN_ROOT_KEYS = frozenset({
    'workspace',
    'project',
    'anonymize',
    'knowledge_out_of_audience',
    'approve_harness_edits',
    'harness',
    'agent',
    'agent_context',
    'egress',
    'knowledge',
    'render_actions',
    'closed_class_detectors',
    'shape',
    'tools',
    'world_state',
    'tool_graph',
    'guards',
    'stubs',
    'scanned_at',
    'change_times',  # written by an older scan; still accepted
    _AGENT_IO_KEY,
    'agent.env',  # a list of variable names the SDK lets through to the agent child; a root key, like `agent.io`
})


#: Root blocks this file WALKS INTO — the key, the shape it must have, and what the key is for.
#: Every reader below goes through :func:`_mapping_block` / :func:`_sequence_block`, so a block
#: written as the wrong shape produces ONE finding naming the key and the type found, and the
#: remaining checks still run. A `tools:` written as a LIST would otherwise reach `.items()` and kill the
#: verifier with an `AttributeError` — rc=1, the same code `main()` returns for "contract
#: violation(s)", so a wrapper branching on the exit code could not tell a crash from a verdict
#: and the checks after it would never run. One rule covers every walked block, rather than N guards.
_WALKED_BLOCKS: dict[str, tuple[type, str]] = {
    'tools': (dict, 'a MAPPING of tool name → its declaration'),
    'stubs': (dict, 'a MAPPING carrying `modules:` / `post_import_patches:`'),
}

#: Sub-blocks walked one level down, keyed by ``<root>.<field>``.
_WALKED_SUBBLOCKS: dict[str, tuple[type, str]] = {
    'stubs.modules': (list, 'a LIST of module entries'),
    'stubs.post_import_patches': (list, 'a LIST of patch entries'),
    'agent.native_tools': (list, 'a LIST of the tool names the agent runs in its own runtime'),
}


def _mapping_block(config: dict[str, Any], key: str) -> dict[str, Any]:
    """``config[key]`` when it is a mapping, ``{}`` otherwise — never a raised attribute.

    The wrong-shape case is REPORTED by :func:`_check_block_shapes`, not swallowed here: this
    returns the empty block so the checks that read it degrade to "nothing declared" rather than
    taking the whole run down with them.
    """
    block = config.get(key)
    return block if isinstance(block, dict) else {}


def _sequence_block(config: dict[str, Any], key: str, field: str) -> list[Any]:
    """``config[key][field]`` when both rungs are the declared shape, ``[]`` otherwise."""
    value = _mapping_block(config, key).get(field)
    return value if isinstance(value, list) else []


def _descriptor_block(tool_entry: Any) -> dict[str, Any]:
    """A tool's ``descriptor:`` when it is a mapping, ``{}`` otherwise — never a raised attribute.

    :func:`_mapping_block`'s rule one rung down, and in ONE place rather than a guard per call
    site: a ``descriptor:`` written as a list or a scalar declares nothing any reader can walk, so
    every check below reads the empty block instead of reaching ``.get`` on it. The wrong shape is
    REPORTED by :func:`_check_descriptor_schema`, which validates the RAW value against
    ``ToolDescriptor`` — not swallowed here.

    A guard per call site is how the two surviving crash sites happened: ``descriptor.get('returns')``
    and ``descriptor.get('read_strategy')`` run only when a ``simulate.py`` was given, so a sweep
    driving ``verify(config, None)`` alone never reaches them and no guard was written there.
    """
    descriptor = tool_entry.get('descriptor') if isinstance(tool_entry, dict) else None
    return descriptor if isinstance(descriptor, dict) else {}


def _check_block_shapes(config: dict[str, Any]) -> list[Finding]:
    """A root block written as the wrong SHAPE is a finding, never a traceback.

    Runs FIRST, because every later check reads these blocks. A ``tools:`` written as a list (the
    shape a YAML author reaches for when the entries look like items) is not a tool declaration
    anything can read — the backend and the SDK both walk it as a mapping of name → declaration —
    so it is named with the type found, and the checks downstream see an empty block instead of
    a half-walked one.
    """
    findings: list[Finding] = []
    for key, (shape, what) in _WALKED_BLOCKS.items():
        value = config.get(key)
        if value is None or isinstance(value, shape):
            continue
        findings.append(Finding(
            severity='fail',
            code=Code.BLOCK_SHAPE,
            message=(
                f'`{key}:` must be {what} (got {type(value).__name__}). Every reader — this '
                f'verifier, the backend mock generator and the SDK — walks it as that shape, so '
                f'nothing at all is read off the form written here.'
            ),
            skill_step='Step 8',
        ))
    for dotted, (shape, what) in _WALKED_SUBBLOCKS.items():
        key, field = dotted.split('.', 1)
        value = _mapping_block(config, key).get(field)
        if value is None or isinstance(value, shape):
            continue
        findings.append(Finding(
            severity='fail',
            code=Code.BLOCK_SHAPE,
            message=(
                f'`{key}.{field}:` must be {what} (got {type(value).__name__}). It is walked as '
                f'that shape by every reader, so nothing is read off the form written here.'
            ),
            skill_step='Step 8',
        ))
    return findings


def _check_root_keys(config: dict[str, Any]) -> list[Finding]:
    """WARN on unknown TOP-LEVEL keys — the backend and SDK silently ignore them.

    A misspelled root key (``anonymise:``, ``tool:``, ``agent_contxt:``) never takes effect, and
    the first symptom is downstream behavior that looks like a different bug. It is a warning,
    not a gate: nothing downstream breaks on the key, and a customer's own vocabulary at the
    root (``business_rules:``) is not a defect. The message names the edit.
    :func:`test_every_config_this_repo_SHIPS_clears_the_root_key_gate` keeps this repo's own
    configs clean of them.

    The suggestion is an EDIT DISTANCE over the allowlist, never a hand-kept typo table:
    a table only ever knows the misspellings someone already made, and the allowlist is the
    one thing guaranteed to stay current with the vocabulary. A key with no near match gets
    no guess — ``business_rules:`` and ``pipeline:`` are somebody's real vocabulary rather
    than a misspelling of ours, and inventing a suggestion for them would send the author
    to the wrong edit.
    """
    known = ', '.join(sorted(_KNOWN_ROOT_KEYS))
    findings: list[Finding] = []
    for key in config:
        if key in _KNOWN_ROOT_KEYS:
            continue
        near = difflib.get_close_matches(str(key), sorted(_KNOWN_ROOT_KEYS), n=1, cutoff=0.6)
        suggestion = f' Did you mean {near[0]!r}?' if near else ''
        findings.append(
            Finding(
                severity='warn',
                code=Code.UNKNOWN_ROOT_KEY,
                message=(
                    f'top-level key {key!r} is not one the backend or SDK reads, so whatever it was '
                    f'meant to configure never takes effect.{suggestion} Fix it in one edit: rename it '
                    f'to the key you meant, move it under the parent that owns it, or delete it if it '
                    f'is a note for humans (a YAML comment carries that without being read as config). '
                    f'Known top-level keys: {known}.'
                ),
                skill_step='Step 8',
            )
        )
    return findings


#: The declarable run driver shapes, in lockstep with ``hooks/_run_driver.py`` MODES (a test asserts
#: the two are equal). ``interactive`` is the DEFAULT and the absent-key meaning: a config that
#: declares nothing here behaves exactly as every config did before the key existed.
_RUN_DRIVER_MODES = ('interactive', 'headless')

#: Sub-keys `workspace:` may carry when it is written as a MAPPING. The legacy scalar form
#: (`workspace: acme`) stays legal and is what almost every config has — `name` is simply where
#: that value goes once the block needs a second key.
#:
#: WHY THIS EXISTS AT ALL. Only TOP-LEVEL keys were validated, so `workspace.run_driverr: headless`
#: was silently ignored and the run silently stayed interactive — which for a CI run driver is
#: exactly the stall the key exists to remove, arriving with no diagnostic at all. A declaration
#: with no typo protection is a declaration that fails closed in the one mode nobody is watching.
_KNOWN_WORKSPACE_KEYS = frozenset({'name', 'run_driver'})


def _check_workspace_block(config: dict[str, Any]) -> list[Finding]:
    """Validate `workspace:` when it is a mapping — its sub-keys, and `run_driver`'s value.

    `workspace: acme` (a scalar) is the historical form and declares no run driver; it is passed
    over silently. Anything that is neither a scalar nor a mapping is the author meaning
    something this file cannot read, so it is named — which for years the code did NOT do: the
    guard returned `[]` for a list, so a `workspace:` written as a LIST carrying
    `run_driver: headless` was certified green while `hooks/_run_driver.resolve` read it as
    `interactive`, the stall this block's own message names.
    """
    if 'workspace' not in config:
        return []
    block = config['workspace']
    if block is None or isinstance(block, (str, int, float, bool)):
        return []
    if not isinstance(block, dict):
        return [Finding(
            severity='fail',
            code=Code.WORKSPACE_SHAPE,
            message=(
                f'`workspace:` must be a name (the historical scalar form) or a MAPPING of '
                f'{", ".join(f"`{k}:`" for k in sorted(_KNOWN_WORKSPACE_KEYS))} — got '
                f'{type(block).__name__}. Every reader walks it as a mapping, so a sub-key '
                f'written in any other shape declares nothing: `run_driver: headless` under a LIST '
                f'resolves to `interactive`, which headless automation experiences as a hang.'
            ),
            skill_step='Step 8',
        )]
    known = ', '.join(sorted(_KNOWN_WORKSPACE_KEYS))
    findings = [
        Finding(
            severity='warn',
            code=Code.UNKNOWN_WORKSPACE_KEY,
            message=(
                f'`workspace.{key}` is not a sub-key anything reads — misspelled? '
                f'Unknown sub-keys are silently ignored, so whatever this was meant to '
                f'configure never takes effect (a typo\'d `run_driver` leaves the run '
                f'INTERACTIVE, which headless automation experiences as a hang). '
                f'Known `workspace` sub-keys: {known}.'
            ),
            skill_step='Step 8',
        )
        for key in block
        if key not in _KNOWN_WORKSPACE_KEYS
    ]
    if 'run_driver' in block:
        run_driver = block['run_driver']
        if not isinstance(run_driver, str) or run_driver.strip() not in _RUN_DRIVER_MODES:
            findings.append(Finding(
                # `fail`, not `error`: main() buckets EXACTLY `fail` and `warn` and mints a gate
                # finding from each `fail`, so a third severity word is a finding that is never printed
                # and never counted — the CLI prints the path header and exits 0. A declaration the
                # verifier silently accepts is the stall this key exists to remove, arriving with
                # the one diagnostic that was supposed to catch it saying nothing.
                severity='fail',
                code=Code.INVALID_RUN_DRIVER,
                message=(
                    f'`workspace.run_driver` is {run_driver!r}, which is not a run driver shape. '
                    f'Declare one of: {", ".join(_RUN_DRIVER_MODES)}. Omit the key for '
                    f'`interactive` (the default). A value nothing recognises is read as no '
                    f'declaration at all, so a headless pipeline would stall at the first gate.'
                ),
                skill_step='Step 8',
            ))
    return findings


#: The hook a declared shape REQUIRES the harness to bind, and nothing else. The names are the
#: RUNNER's: the child process resolves them with ``getattr`` on the imported simulate module, so
#: what matters is the NAME BOUND AT MODULE SCOPE, not that a ``def`` produced it.
_SHAPE_HOOKS: dict[str, str] = {'dialog': 'invoke_agent', 'pipeline': 'invoke_pipeline'}

#: What an UNDECLARED bench is dispatched as. The runner defaults to the conversation; the hooks
#: are a hint that never chooses a route. Named because two checks below reason about it.
_UNDECLARED_SHAPE = 'dialog'

#: WHO OWNS THE WORLD this suite scores — ``world_state.owner``, the second declaration the runner
#: reads out of plurai.config.yaml beside ``shape:``. Exactly two things are reset per scenario:
#: the mock session the backend opens, and the child process the runner spawns. A world kept anywhere
#: else is carried forward, so scenario 2 opens on scenario 1's writes and each verdict is partly
#: about the run order — invisibly, because the run is GREEN.
#:
#:   ``mock``      the deployed mock owns every record this agent reads and writes. THE DEFAULT,
#:                 and what an undeclared bench means: the backend opens a session per scenario and
#:                 seeds it, so there is nothing for the harness to reset.
#:   ``agent``     the agent's own process owns it (its checkpointer's database, a file, an index).
#:   ``external``  a real CRM, a shared database, a server-side conversation store owns it.
#:
#: ``agent`` and ``external`` differ in WHERE the store is, not in what must be done about it —
#: both are worlds the mock does not own, so both require the harness to export ``reset()`` (the
#: Python sub-skill's Step 9e verifier refuses the pair; the runner refuses the run at start).
#:
#: DRIFT: this tuple is the SDK's ``plurai/orchestrator/orchestrator.py`` ``WORLD_OWNERS``, and an
#: unknown value there is a WorkspaceConfigError that exits the run — so a value this file accepts
#: and the SDK does not is a green verify followed by a refused run. Nothing in this repo can read
#: the SDK, so the shipped `references/example_plurai_config.yaml` documents the same three words for the
#: author and a change to one is a change to the other.
_WORLD_OWNERS = ('mock', 'agent', 'external')

#: What an undeclared ``world_state.owner`` means. Named so the message below can say it, and so
#: nothing here has to spell 'mock' twice.
_UNDECLARED_WORLD_OWNER = 'mock'


#: How much of the undeclared set the finding quotes: per type, and how many types.
_REQUIRED_UNDECLARED_FIELDS_SHOWN = 6
_REQUIRED_UNDECLARED_TYPES_SHOWN = 10


def _check_world_field_required(config: dict[str, Any]) -> list[Finding]:
    """Every declared ``world_state.entity_types.<type>.fields.<field>`` carries a boolean ``required``.

    The backend writes that flag onto the world schema it generates. A field declared without one
    has its requiredness re-authored from prose, so a field the agent or harness reads without a
    default can ship optional and a scenario seeding the row without it fails there.

    WARNED, with the exact line to write, and never refused: the backend still builds the world, and
    reads the field's requiredness from prose.
    """
    world_state = config.get('world_state')
    entity_types = world_state.get('entity_types') if isinstance(world_state, dict) else None
    if not isinstance(entity_types, dict):
        return []
    missing: dict[str, list[str]] = {}
    example: str | None = None
    for entity, body in entity_types.items():
        fields = body.get('fields') if isinstance(body, dict) else None
        if isinstance(fields, list):
            names = [str(name) for name in fields]
            if names and example is None:
                example = f'turn `world_state.entity_types.{entity}.fields` into a mapping and write `{names[0]}: {{required: true}}`'
        elif isinstance(fields, dict):
            names = [str(name) for name, spec in fields.items() if not (isinstance(spec, dict) and isinstance(spec.get('required'), bool))]
            if names and example is None:
                spec = fields.get(names[0]) if isinstance(fields.get(names[0]), dict) else {}
                typed = f'type: {spec["type"]}, ' if isinstance(spec.get('type'), str) else ''
                example = f'under `world_state.entity_types.{entity}.fields` write `{names[0]}: {{{typed}required: true}}`'
        else:
            names = []
        if names:
            missing[str(entity)] = names
    if not missing:
        return []
    groups = [
        f'{entity}: {", ".join(names[:_REQUIRED_UNDECLARED_FIELDS_SHOWN])}'
        + (f' (+{len(names) - _REQUIRED_UNDECLARED_FIELDS_SHOWN} more)' if len(names) > _REQUIRED_UNDECLARED_FIELDS_SHOWN else '')
        for entity, names in list(missing.items())[:_REQUIRED_UNDECLARED_TYPES_SHOWN]
    ]
    if len(missing) > _REQUIRED_UNDECLARED_TYPES_SHOWN:
        groups.append(f'+{len(missing) - _REQUIRED_UNDECLARED_TYPES_SHOWN} more type(s)')
    total = sum(len(names) for names in missing.values())
    message = (
        f'{total} `world_state` field(s) on {len(missing)} type(s) declare no boolean `required:` — '
        f'{"; ".join(groups)}. Write `required: true` on every field the agent or harness reads '
        f'without a default (its ids, what the harness storage reads with a fail-closed guard or a bare `row["f"]` '
        f'reads, the record model\'s non-defaulted fields) and `required: false` on the rest: the '
        f'backend carries it onto the generated world schema, and an undeclared flag is re-authored from prose.'
        + (f' For example, {example} (`required: false` where the record model defaults it).' if example else '')
    )
    return [Finding(severity='warn', code=Code.WORLD_FIELD_REQUIRED_UNDECLARED, message=message, skill_step='Step 8')]


def _check_world_checks(config: dict[str, Any], bases: list[Path]) -> list[Finding]:
    """Every ``world_state.entity_types.<type>.checks`` entry is re-measured on the data file its ``source`` cites.

    The backend refuses a seeded world that breaks a check, so a check is a fact about the agent's real rows, never a
    statement from memory: a check any real row of its cited file breaks, whose ``in`` set is not that file's, that
    cites the PRD or no data file, or that reads a field its type does not declare, is refused
    (``measured_candidates.world_check_outcome``). Advisory: the repair is to copy the sweep's proposal or drop the
    check, and the plugin may overrule it. A MEMBERSHIP miss (an ``in`` set that is no longer its store's list, or a
    real row's value outside it) is a warning only (``world_check_set_stale``): a store changes after a set is copied,
    and the remedy is to refresh the set, never to stop.
    """
    world_state = config.get('world_state')
    bodies = world_state.get('entity_types') if isinstance(world_state, dict) else None
    if not isinstance(bodies, dict) or not any(isinstance(b, dict) and 'checks' in b for b in bodies.values()):
        return []
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import measured_candidates  # noqa: PLC0415 - the sweep that proposes the checks re-measures them, one reading
    except ImportError:  # this file copied out of the plugin: the sweep is not beside it
        return []

    findings: list[Finding] = []
    for name, body in bodies.items():
        checks = body.get('checks') if isinstance(body, dict) else None
        if checks is None:
            continue
        fields = body.get('fields')
        declared = {str(f): (v if isinstance(v, dict) else {}) for f, v in fields.items()} if isinstance(fields, dict) else (
            dict.fromkeys(map(str, fields), {}) if isinstance(fields, list) else None
        )
        for n, check in enumerate(checks if isinstance(checks, list) else [checks], start=1):
            problems = [] if isinstance(checks, list) else ['`checks:` is not a list']
            if not problems and declared is not None and measured_candidates.check_defect(check) is None:
                problems = _world_check_fields(check, name, declared, measured_candidates)
            stale: list[str] = []
            if not problems:
                problems, stale = measured_candidates.world_check_outcome(check, bases)
            if stale:
                findings.append(Finding(
                    severity='warn',
                    code=Code.WORLD_CHECK_SET_STALE,
                    message=(
                        f'`world_state.entity_types.{name}.checks[{n}]`: refresh its reference set from the store (re-run '
                        '`measured_candidates.py <agent root> --profile <workdir>/artifacts/data_profile.md --workdir <workdir>`, copy '
                        'the check again, then ask me to rebuild or re-pin the mock environment), or drop it. The set no longer matches '
                        f'its store: {"; ".join(stale)}.'
                    ),
                    skill_step='Step 8',
                ))
            if problems:
                findings.append(Finding(
                    severity='fail',
                    code=Code.WORLD_CHECK_REFUSED,
                    message=(
                        f'`world_state.entity_types.{name}.checks[{n}]` is refused: copy it verbatim from `measured_candidates.py <agent '
                        'root> --profile plurai_sim/artifacts/data_profile.md --data <seed or fixture file>`, or drop it. A world check is '
                        f'a fact every real row holds, and this one is not: {"; ".join(problems)}.'
                    ),
                    skill_step='Step 8',
                ))
    return findings


def _check_reference_store_sweep(config: dict[str, Any], config_path: Path) -> list[Finding]:
    """The verifier runs the reference half of the world-check sweep itself, so a skipped sweep step is visible.

    A reference store is content the agent only reads: one it reads live (a root in ``.scan_corpus_roots`` or
    ``artifacts/corpus_roots.json``), or a world type the run simulates whose rows are copied from one list of the agent's
    data (its declared ``reference_rows:``, a catalog or code table). The world's other rows only ever hold its ids, and
    the scenario author composes them from a field's pattern unless an ``in`` check carries the set. For each store no
    declared check cites, this warns with the ready-to-copy YAML; for a list above the sweep's default cap that would be
    such a set and that no store or table holds, it warns naming the list; a ``reference_rows:`` that names no readable
    list is warned too. Warnings only, each leading with its remedy: whether a list is read-only content or rows the
    world seeds is the agent's call.
    """
    workdir = config_path.resolve().parent
    # The agent root sits above `plurai_sim/`, in the named-env layout (`plurai_sim/envs/<name>/`) too.
    root = next((d.parent for d in (workdir, *workdir.parents) if d.name == 'plurai_sim'), workdir.parent)
    try:
        profile = (workdir / 'artifacts' / 'data_profile.md').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return []
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import measured_candidates as mc  # noqa: PLC0415 - one sweep: the scan's proposals and this re-run read the same code
    except ImportError:  # this file copied out of the plugin: the sweep is not beside it
        return []
    if mc.yaml is None:
        return []
    stores = mc.reference_roots(root, workdir)
    tables = mc.reference_tables(config)
    paths = list(dict.fromkeys([*mc.input_paths(profile, root), *(rel for rel, _ in tables)]))
    world_state = config.get('world_state')
    bodies = world_state.get('entity_types') if isinstance(world_state, dict) else None
    bodies = {str(k): v for k, v in bodies.items() if isinstance(v, dict)} if isinstance(bodies, dict) else {}
    cited: set[tuple[str, str]] = set()
    # The fields each declared type can carry an `in` set on: declared, and no `references:` (the verifier refuses an `in`
    # set there, since the world seeds what it references).
    carryable: dict[str, set[str]] = {}
    for kind, body in bodies.items():
        for check in body.get('checks') if isinstance(body.get('checks'), list) else []:
            if isinstance(check, dict) and isinstance(check.get('source'), str) and (member := mc.members_list(check['source'])):
                cited.add(member)
        declared = body.get('fields')
        specs = declared if isinstance(declared, dict) else dict.fromkeys(declared, None) if isinstance(declared, list) else {}
        carryable[kind] = {str(f) for f, spec in specs.items() if not (isinstance(spec, dict) and spec.get('references'))}

    def by_list(found: list[Any]) -> dict[tuple[str, str], list[Any]]:
        """The proposals per members list no declared check cites, of a check some declared type other than the
        table's own can carry (each path's first field carryable): one no type can carry guards no world row and would be
        refused."""
        out: dict[tuple[str, str], list[Any]] = {}
        for c in found:
            member = mc.members_list(str(c.check['source']))
            heads = {str(p).split('.', 1)[0].removesuffix('[]') for p in c.check['then']}
            holders = set().union(*(fields for kind, fields in carryable.items() if kind != tables.get(member)))
            if member and member not in cited and (not bodies or heads <= holders):
                out.setdefault(member, []).append(c)
        return out

    findings: list[Finding] = []
    for kind, body in bodies.items():
        raw = body.get(mc.REFERENCE_ROWS_KEY)
        if raw is None:
            continue
        why = None
        table = next(((rel, name) for (rel, name), owner in tables.items() if owner == kind), None)
        rows = mc.data_lists(root.resolve(), table[0]).get(table[1]) if table else None
        if table is None:
            why = 'it is not a path string'
        elif not rows:
            why = 'no such list is readable there'
        elif not (ids := mc._id_fields(rows, mc.MAX_REFERENCE_STORE_MEMBERS)):
            why = (f'its {len(rows)} rows are over the {mc.MAX_REFERENCE_STORE_MEMBERS}-row ceiling' if len(rows) > mc.MAX_REFERENCE_STORE_MEMBERS
                   else 'no field there is a distinct id on every row (a non-numeric string)')
        elif isinstance(body.get('id_field'), str) and body['id_field'] not in ids:
            why = f'its `{body["id_field"]}` is not a distinct id on every row'
        if why:
            findings.append(Finding(
                severity='warn',
                code=Code.WORLD_REFERENCE_ROWS_INVALID,
                message=(
                    f'`world_state.entity_types.{kind}.{mc.REFERENCE_ROWS_KEY}: {raw}`: name the data file (and `#<list>`) under the '
                    f'agent root whose rows `{kind}` copies, or drop the key; no reference set is proposed from it: {why}.'
                ),
                skill_step='Step 8',
            ))
    unswept = by_list(mc.reference_store_checks(root, paths, stores or [], tables=tables)) if stores or tables else {}
    # A list the default cap refuses that no recorded store or declared table holds: whether the agent only reads it (a
    # store, a table) or the world seeds it is the agent's to say, so it is named and never proposed.
    unclassified = [
        m for m in by_list(mc.reference_store_checks(root, paths, [root], above=mc.MAX_REFERENCE_MEMBERS))
        if m not in unswept and m not in tables and not mc._in_store(root, m[0], stores or [])
    ]
    sweep = '"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/measured_candidates.py" "${WORKDIR%%/plurai_sim*}" --profile "$ART/data_profile.md" --workdir "$WORKDIR"'
    repin = 'then ask me to rebuild or re-pin the mock environment'
    for member, checks in unswept.items():
        where = mc._where(*member)
        owner = tables.get(member)
        what = f'the `{mc.REFERENCE_ROWS_KEY}` table of `{owner}`' if owner else 'a reference store the agent reads live (a recorded corpus root)'
        findings.append(Finding(
            severity='warn',
            code=Code.WORLD_CHECK_UNSWEPT,
            message=(
                f'`{where}`: copy the {len(checks)} check(s) below (or from `{sweep}`) under '
                f'`world_state.entity_types.<the type those rows are>.checks`, {repin}.\n`{where}` is {what}; every value of a field '
                f'on {len(checks)} list(s) of the agent\'s real rows is one of its ids, and no declared world check cites it, so the '
                'scenario author composes ids it does not hold. Skip a list no world type holds.'
                f'\n{mc.render_checks(checks)}\n# end of the checks to copy'
            ),
            skill_step='Step 8',
        ))
    if unclassified:
        where = ('no reference store is recorded (no `.scan_corpus_roots` or `artifacts/corpus_roots.json` in the workdir)' if stores is None
                 else 'no recorded reference store (`.scan_corpus_roots`, `artifacts/corpus_roots.json`) holds them')
        findings.append(Finding(
            severity='warn',
            code=Code.WORLD_CHECK_STORES_UNCLASSIFIED,
            message=(
                f'{", ".join(f"`{mc._where(*m)}`" for m in unclassified)}: if one holds the rows of a world type the agent only reads '
                f'(a catalog, a code table), declare `{mc.REFERENCE_ROWS_KEY}: <that file>[#<list>]` on that type; if the world seeds '
                f'them, leave it. Each is an id list longer than the sweep\'s default cap ({mc.MAX_REFERENCE_MEMBERS}) holding every '
                f'value of a field the world declares, and {where} and no type declares it, so no `in` set was proposed. For content '
                f'read live (not a world type), run `{sweep} --reference-roots <that file>`. After copying a proposed check, {repin}.'
            ),
            skill_step='Step 8',
        ))
    return findings


def _world_check_fields(check: dict[str, Any], name: str, declared: dict[str, dict[str, Any]], mc: Any) -> list[str]:
    """What a well-formed check reads that type ``name`` does not declare: a field, a row-list item key (when the row
    list types its items), a name or item key an ``expr`` reads, or an ``in`` set on a ``references:`` field.

    The backend reads an undeclared key as null (every item then breaks a literal) and an expr's missing key as an
    error, so such a check reds every seeded world instead of the impossible ones."""
    items = {f: set(spec['item_specs']) for f, spec in declared.items() if isinstance(spec.get('item_specs'), dict)}
    unknown: list[str] = []
    for side in ('when', 'then'):
        for path, rule in (check.get(side) or {}).items():
            head, _, rest = str(path).partition('.')
            field = head.removesuffix('[]')
            if field not in declared:
                unknown.append(f'`{field}`')
            elif rest and field in items and rest.split('.', 1)[0].removesuffix('[]') not in items[field]:
                unknown.append(f'`{field}[].{rest}`')
            if isinstance(rule, dict) and 'in' in rule and declared.get(field, {}).get('references'):
                unknown.append(f'an `in` set on `{field}`, a `references:` field (the world seeds what it references)')
            if isinstance(rule, dict) and 'expr' in rule:
                tree = mc.expr_tree(str(rule['expr']))
                bound = {t.id for c in ast.walk(tree) if isinstance(c, ast.comprehension) for t in ast.walk(c.target) if isinstance(t, ast.Name)}
                names = {c.id for c in ast.walk(tree) if isinstance(c, ast.Name)} - bound - set(mc._EXPR_FUNCTIONS)
                unknown += [f'`{n}` (in `{rule["expr"]}`)' for n in sorted(names - set(declared))]
                keys = {c.slice.value for c in ast.walk(tree) if isinstance(c, ast.Subscript) and isinstance(c.slice, ast.Constant) and isinstance(c.slice.value, str)}
                known = set().union(*items.values()) if items else None
                unknown += [f'`{k}` (in `{rule["expr"]}`)' for k in sorted(keys - known)] if known is not None else []
    return [f'it reads {", ".join(dict.fromkeys(unknown))}, which `{name}` does not declare'] if unknown else []


def _check_world_id_fields(config: dict[str, Any]) -> list[Finding]:
    """Every type a ``references:`` field names DECIDES which of its fields holds a row's own id.

    The backend keys each seeded row by its own id — the type's declared ``id_field``, else its ``id``
    or ``<type>_id`` field — and by position (``<type>_1``) when it has none. A foreign key holding the
    row's real id then resolves to nothing, and one holding the positional key never meets the id the
    agent and the mock join on. A type whose own id is spelled otherwise (``Id``, ``pnr``,
    ``bookingId``) must say so; ``id_field: null`` says it has none. Only the declaration decides: the
    field is not inferred from its name.

    ``world_id_field_unknown`` is refused: it names a field the backend cannot key by.
    ``world_id_field_undecided`` is WARNED, naming the line that decides it (``id_field: null`` keeps
    the positional keying): the backend still keys the rows, by position.
    """
    types = _declared_world_fields(config)
    world_state = config.get('world_state')
    bodies = world_state.get('entity_types') if isinstance(world_state, dict) else None
    if not isinstance(bodies, dict):
        return []
    findings: list[Finding] = []
    for name, body in bodies.items():
        if not isinstance(body, dict) or 'id_field' not in body or body['id_field'] is None:
            continue
        declared = body['id_field']
        if not isinstance(declared, str) or declared not in types.get(str(name), {}):
            findings.append(Finding(
                severity='fail',
                code=Code.WORLD_ID_FIELD_UNKNOWN,
                message=(
                    f'`world_state.entity_types.{name}.id_field: {declared!r}` names no field `{name}` declares. '
                    f'Name the declared field that holds a `{name}` row\'s own id, or write `id_field: null` when it has none.'
                ),
                skill_step='Step 8',
            ))
    referrers: dict[str, list[str]] = {}
    for owner, fields in types.items():
        for field, spec in fields.items():
            target = spec.get('references')
            if isinstance(target, str) and target.strip():
                entity, _, target_field = target.strip().partition('.')
                if target_field in ('', 'id'):
                    referrers.setdefault(entity, []).append(f'{owner}.{field}')
    for target, via in referrers.items():
        body = bodies.get(target)
        if not isinstance(body, dict) or 'id_field' in body or {'id', f'{target}_id'} & set(types.get(target, {})):
            continue
        message = (
            f'`{target}` is referenced by {", ".join(f"`{v}`" for v in via)} but declares neither an `id` / `{target}_id` field '
            f'nor `id_field:`, so its seeded rows are keyed by position and a reference holding a row\'s real id resolves to '
            f'nothing. Write `id_field: <the declared field that holds a {target} row\'s own id>` on '
            f'`world_state.entity_types.{target}`, or `id_field: null` when it has none.'
        )
        fields = ', '.join(types.get(target, {})) or 'none declared'
        message += (
            f' To keep the positional keying, write `id_field: null` under `world_state.entity_types.{target}`; to key by a '
            f'field, write `id_field: <field>` naming one of its fields ({fields}).'
        )
        findings.append(Finding(severity='warn', code=Code.WORLD_ID_FIELD_UNDECIDED, message=message, skill_step='Step 8'))
    return findings


def _union_members(declared: str, sep: str = '|') -> list[str]:
    """The top-level members of ``declared`` split on ``sep``, brackets respected (``list[a | b] | null`` has two)."""
    members: list[str] = []
    depth = start = 0
    for i, char in enumerate(declared):
        depth += (char == '[') - (char == ']')
        if char == sep and depth == 0:
            members.append(declared[start:i].strip())
            start = i + 1
    return [*members, declared[start:].strip()]


#: The type grammar the backend's world schema compiles, RESTATED from the backend's
#: `declared_world.schema_type_spelling`.
_TYPE_LIST_HEADS = frozenset({'list', 'array', 'sequence'})
_TYPE_MAP_HEADS = frozenset({'dict', 'object', 'mapping'})
_TYPE_NULLS = frozenset({'null', 'none', 'nonetype'})
_TYPE_CLOSED_SET_HEADS = frozenset({'enum', 'literal'})
_SIZED_NUMERIC_RE = re.compile(r'^(u?int|float)\d+$')
#: Value types a list can hold that are not records, though the world schema has no spelling for them.
_SCALAR_VALUE_NAMES = frozenset({'datetime', 'date', 'time', 'timedelta', 'uuid', 'bytes', 'any'})
_SCHEMA_TYPE_SPELLINGS = 'str, int, float, bool, list[<type>], dict, enum[...], Literal[...], and Optional[<type>] or `<type> | null`'


def _type_parts(member: str) -> tuple[str, str | None]:
    """``(lower-cased head, bracket contents or None)`` of one type member."""
    head, bracket, tail = member.partition('[')
    return head.strip().lower(), (tail[:-1] if bracket and member.endswith(']') else None)


def _type_members(declared: str) -> list[str]:
    """The top-level members of a type, with ``Optional[X]`` and ``Union[...]`` flattened into them."""
    out: list[str] = []
    for member in _union_members(declared):
        head, inner = _type_parts(member)
        if inner is not None and head == 'optional':
            out += [*_type_members(inner), 'null']
        elif inner is not None and head == 'union':
            out += [m for part in _union_members(inner, ',') for m in _type_members(part)]
        else:
            out.append(member)
    return out


def _is_scalar_atom(atom: str) -> bool:
    low = atom.lower()
    return low in _FIELD_VALUE_TYPES or low in _TYPE_NULLS or bool(_SIZED_NUMERIC_RE.match(low))


def _schema_compiles(declared: str) -> bool:
    """Whether the backend's world schema can compile ``declared`` (a model or ``datetime`` name it cannot)."""
    for member in _type_members(declared):
        head, inner = _type_parts(member)
        if inner is not None and head in _TYPE_LIST_HEADS:
            if not _schema_compiles(inner):
                return False
            continue
        named = member.lower() in _TYPE_LIST_HEADS | _TYPE_MAP_HEADS or _is_scalar_atom(member)
        if not (named or (inner is not None and head in _TYPE_MAP_HEADS | _TYPE_CLOSED_SET_HEADS)):
            return False
    return True


def _is_record(item: str) -> bool:
    """Whether a list item type is a record: a mapping, a model name, or a list of those."""
    for member in _type_members(item):
        head, inner = _type_parts(member)
        if inner is not None and head in _TYPE_LIST_HEADS:
            if _is_record(inner):
                return True
        elif (inner is not None and head in _TYPE_MAP_HEADS) or member.lower() in _TYPE_MAP_HEADS:
            return True
        elif not (_is_scalar_atom(member) or member.lower() in _TYPE_LIST_HEADS | _SCALAR_VALUE_NAMES or (inner is not None and head in _TYPE_CLOSED_SET_HEADS)):
            return True
    return False


def _holds_rows(declared: Any) -> bool:
    """A declared list whose item is a record (``list[dict]``, ``Optional[list[Row]]``, ``list[list[dict]]``), not a scalar list."""
    if not isinstance(declared, str):
        return False
    return any(inner is not None and head in _TYPE_LIST_HEADS and _is_record(inner) for head, inner in map(_type_parts, _type_members(declared)))


def _check_world_rows_typed(config: dict[str, Any]) -> list[Finding]:
    """Every `world_state` field that holds rows (`list[dict]`) declares them under `item_specs:`, each typed.

    The backend carries a row field's declared type onto the generated world schema. A row field named
    only in prose is typed there from its NAME, so every scenario can seed a row the agent's own record
    model rejects, and every one dies at Init. WARNED, naming the fix: the backend still builds the
    world, typing the row field from its name.
    """
    world_state = config.get('world_state')
    bodies = world_state.get('entity_types') if isinstance(world_state, dict) else None
    if not isinstance(bodies, dict):
        return []
    untyped: list[str] = []
    for entity, fields in _declared_world_fields(config).items():
        for field, spec in fields.items():
            if not _holds_rows(spec.get('type')):
                continue
            rows = spec.get('item_specs')
            if not isinstance(rows, dict) or not rows:
                untyped.append(f'`{entity}.{field}` (no item_specs)')
                continue
            typed = {str(sub): sub_spec.get('type') for sub, sub_spec in rows.items() if isinstance(sub_spec, dict)}
            bare = [str(sub) for sub in rows if not (isinstance(typed.get(str(sub)), str) and typed[str(sub)])]
            if bare:
                untyped.append(f'`{entity}.{field}` (no type on: {", ".join(bare)})')
            foreign = [f'{sub}: {kind}' for sub, kind in typed.items() if isinstance(kind, str) and kind and not _schema_compiles(kind)]
            if foreign:
                untyped.append(f'`{entity}.{field}` (types the world schema cannot compile: {", ".join(foreign)})')
    if not untyped:
        return []
    message = (
        f'{len(untyped)} `world_state` row field(s) declare no typed row contract: {"; ".join(untyped)}. Under each, write '
        f'`item_specs: {{<row field>: {{type: <type>, required: <bool>}}}}` for every field a row carries, typed from the '
        f'record model the harness\'s read path builds from each row, spelled {_SCHEMA_TYPE_SPELLINGS} (an ISO-8601 '
        f'timestamp is a str). The backend writes those types onto the world schema; a row field left in prose, or '
        f'typed in a spelling it cannot compile, is typed from its name, and a seeded row the agent\'s model rejects '
        f'fails every scenario at Init.'
    )
    return [Finding(severity='warn', code=Code.WORLD_ROWS_UNTYPED, message=message, skill_step='Step 8')]


def _check_structured_context_typed(config: dict[str, Any]) -> list[Finding]:
    """An `agent_context` field a `structured_context` channel names types its value under `item_specs:`.

    The backend copies the field's declared type onto the scenario schema. A bare `dict` compiles to a free-key map, so a
    scenario can seed a request object the production parser rejects. WARNED, naming the fix.
    """
    declared = config.get(_AGENT_IO_KEY)
    channels = declared.get('channels') if isinstance(declared, dict) else None
    context = config.get('agent_context')
    fields = context.get('fields') if isinstance(context, dict) else None
    if not isinstance(channels, list) or not isinstance(fields, dict):
        return []
    bare: list[str] = []
    for entry in channels:
        field = entry.get('field') if isinstance(entry, dict) and entry.get('kind') == 'structured_context' else None
        spec = fields.get(field) if isinstance(field, str) else None
        if not isinstance(spec, dict) or not isinstance(spec.get('type'), str) or spec.get('item_specs'):
            continue
        members = [m for m in _type_members(spec['type']) if m.lower() not in _TYPE_NULLS]
        if members and all(m.lower() in _TYPE_MAP_HEADS for m in members):
            bare.append(f'`{field}`')
    if not bare:
        return []
    message = (
        f'{len(bare)} `agent_context` field(s) a `structured_context` channel names are a bare `dict` with no `item_specs`: '
        f'{", ".join(bare)}. Under each, write `item_specs: {{<key>: {{type: <type>, required: <bool>}}}}` for every key its value '
        f'holds, nesting `item_specs` under an object key, read from the schema the request is parsed with on its way in.'
    )
    return [Finding(severity='warn', code=Code.STRUCTURED_CONTEXT_UNTYPED, message=message, skill_step='Step 8')]


def _check_world_state_owner(config: dict[str, Any]) -> list[Finding]:
    """``world_state.owner`` must be one of the three words the runner reads, or absent.

    ABSENT IS LEGAL and means :data:`_UNDECLARED_WORLD_OWNER` — every bench written before the key
    existed, and every bench whose world really is the mock's. So this check has exactly one
    subject: a declaration the runner cannot read. That subject has two shapes, because the
    runner WALKS to the value: a ``world_state:`` that is not a mapping (``world_state: external``)
    and a word outside the set. Both raise there; both fail here, under one code, because one
    remedy fixes either.

    A misspelling (``externl``, ``harness``, ``self``) is the expensive case and the reason this
    FAILS rather than warns. It is not ignored downstream: the SDK refuses the whole run at start
    with a ``WorkspaceConfigError`` (``plurai-run-simulation`` exits 65) rather than defaulting,
    precisely because a typo silently read as ``mock`` would reproduce the leak the declaration
    exists to end. This check is a $0 preflight for that refusal — same verdict, before generation
    has been paid for.

    Read the way the runner reads it (``str(raw).strip().lower()``), so ``External`` and
    ``  mock `` pass here exactly as they pass there. A check stricter than the consumer would
    refuse a config that runs.
    """
    world_state = config.get('world_state')
    if world_state is None:
        return []
    if not isinstance(world_state, dict):
        # `world_state: external` — the shorthand an author writes when the key reads like a
        # scalar. The runner WALKS into this block (`('world_state', 'owner')`) and raises
        # `WorkspaceConfigError` on an intermediate that exists and is not a mapping, so a
        # verifier that returned [] here handed back a green tick for a run that exits 65 before
        # a thread is opened. Same code as a word the runner cannot read, because it is the same
        # sentence one rung up and it has the same one remedy.
        return [Finding(
            severity='fail',
            code=Code.WORLD_OWNER_VALUE,
            message=(
                f'`world_state:` must be a MAPPING, not a {type(world_state).__name__} '
                f'({world_state!r}). The owner is declared one level in — `world_state: '
                f'{{owner: external}}` — because the block also carries `entity_types:`. The '
                f'runner walks `world_state.owner` and refuses a config it cannot walk into '
                f'(exit 65, before a thread is opened) rather than reading the bench as '
                f'{_UNDECLARED_WORLD_OWNER!r}.'
            ),
            skill_step='Step 8',
        )]
    if 'owner' not in world_state:
        return []
    declared = world_state['owner']
    if isinstance(declared, str) and declared.strip().lower() in _WORLD_OWNERS:
        return []
    legal = ', '.join(repr(owner) for owner in _WORLD_OWNERS)
    return [Finding(
        severity='fail',
        code=Code.WORLD_OWNER_VALUE,
        message=(
            f'`world_state.owner` must be one of {legal} (got {declared!r}). It names WHO OWNS the '
            f'state this suite scores: {_UNDECLARED_WORLD_OWNER!r} (the default, and what omitting '
            f'the key means) is the deployed mock, which the backend re-seeds per scenario; '
            f"'agent' is the agent's own store and 'external' a real system behind it — both of "
            f'which the run cannot reset, so the harness must export `reset()`. A value the runner '
            f'cannot read is not defaulted: it refuses the run at start (exit 65), before a thread '
            f'is opened.'
        ),
        skill_step='Step 8',
    )]


#: The owners ``world_state.slots`` may sit beside. DRIFT: the SDK's ``orchestrator._declared_world_slots``
#: refuses ``slots`` beside any other owner (undeclared included) — a WorkspaceConfigError, exit 65 at run
#: start. Known non-parity: this reads one config, the SDK collects across both rungs and refuses on disagreement.
_SLOTTED_WORLD_OWNERS = ('external', 'agent')


def _readable_world_owner(config: dict[str, Any]) -> str | None:
    """The owner word as the runner reads it (absent means mock), or ``None`` when ``world_owner_value`` refuses it."""
    world_state = config.get('world_state')
    if world_state is None:
        return _UNDECLARED_WORLD_OWNER
    if not isinstance(world_state, dict):
        return None
    if 'owner' not in world_state:
        return _UNDECLARED_WORLD_OWNER
    declared = world_state['owner']
    word = declared.strip().lower() if isinstance(declared, str) else None
    return word if word in _WORLD_OWNERS else None


def _check_world_state_slots(config: dict[str, Any]) -> list[Finding]:
    """``world_state.slots`` passes exactly what the runner accepts: an int >= 1 beside ``external`` or ``agent``.

    ``slots: null`` is absent. A bool, a string or a float is refused, never coerced. Silent when the owner
    itself is unreadable: ``world_owner_value`` already fails that, and one defect is one finding.
    """
    world_state = config.get('world_state')
    if not isinstance(world_state, dict) or world_state.get('slots') is None:
        return []
    owner = _readable_world_owner(config)
    if owner is None:
        return []
    slots = world_state['slots']
    if isinstance(slots, bool) or not isinstance(slots, int) or slots < 1:
        message = (
            f'`world_state.slots: {slots!r}` is not a slot count: write the whole number (1 or more) of identical store copies '
            f'the harness provides. The runner refuses it at start (exit 65) rather than coercing it.'
        )
    elif owner not in _SLOTTED_WORLD_OWNERS:
        message = (
            f'`world_state.slots: {slots}` applies only beside `world_state.owner: external` or `agent`, and this bench\'s owner is '
            f'{owner!r}. A store each child holds in its own process needs no `slots` — remove it. The runner refuses it at start (exit 65).'
        )
    else:
        return []
    return [Finding(severity='fail', code=Code.WORLD_SLOTS_VALUE, message=message, skill_step='Step 8')]


def _check_harness_single_instance(config: dict[str, Any]) -> list[Finding]:
    """``harness.single_instance`` is what the runner accepts: a list of non-blank resource cites (``["tcp port 8080"]``).

    The runner runs ONE agent process at a time when it is declared, and refuses any other shape at start (exit 65)
    rather than coercing it: a typo there decides whether two agent processes fight over one port.
    """
    harness = config.get('harness')
    raw = harness.get('single_instance') if isinstance(harness, dict) else None
    if raw is None or (isinstance(raw, list) and all(isinstance(item, str) and item.strip() for item in raw)):
        return []
    return [Finding(
        severity='fail',
        code=Code.HARNESS_SINGLE_INSTANCE_INVALID,
        message=(f'`harness.single_instance: {raw!r}` must be a list of the host resources one agent process holds, '
                 'e.g. ["tcp port 8080"]. The runner refuses it at start (exit 65).'),
        skill_step='Step 8',
    )]


def _check_world_real_writer_unowned(config: dict[str, Any]) -> list[Finding]:
    """Warn when a ``mode: real`` tool resolves to ``write_strategy: real`` while the world is the mock's.

    Static, so it covers every runtime (Python, Node, hosted, a black box). Warn-only: the harness-aware verdict is
    ``verify_simulate``'s ``world_live_writer_unreset``, which fires whatever the owner says. Same remedy order as it:
    mock the writers' systems, else reset the world per conversation, else carry-over on the user's explicit opt-in.
    """
    if _readable_world_owner(config) != _UNDECLARED_WORLD_OWNER:
        return []
    tools = _mapping_block(config, 'tools')
    writers = sorted(
        name for name, entry in tools.items() if isinstance(entry, dict) and entry.get('mode') == 'real' and _resolve_write_strategy(entry) == 'real'
    )
    if not writers:
        return []
    return [Finding(
        severity='warn',
        code=Code.WORLD_REAL_WRITER_UNOWNED,
        message=(
            f'tools {", ".join(writers)} write a real service while the world is the mock\'s — every parallel attempt shares that '
            f'store, nothing resets it, and the run scores the mock\'s world. Mock the writing systems (`mode: mock`, '
            f'`status: mocked` on the map); else export a `reset()` that returns that world to the same state before every '
            f'conversation; keep them live only on the user\'s explicit opt-in (`world_state.shared_world_ok: true`), and the run '
            f'then carries the named risk `shared_world`: conversations see each other\'s writes.'
        ),
        skill_step='Step 8',
    )]


def _check_world_shared_ok(config: dict[str, Any]) -> list[Finding]:
    """``world_state.shared_world_ok`` is a boolean, as the runner requires: it decides whether a run on a world nothing
    resets per conversation is held or proceeds with the ``shared_world`` risk, so ``"yes"`` or ``1`` is refused at
    start (exit 65), never coerced. ``null`` is absent."""
    world_state = config.get('world_state')
    value = world_state.get('shared_world_ok') if isinstance(world_state, dict) else None
    if value is None or isinstance(value, bool):
        return []
    return [Finding(
        severity='fail',
        code=Code.WORLD_SHARED_OK_VALUE,
        message=(f'`world_state.shared_world_ok: {value!r}` is not an opt-in: write `true` (the user chose to keep live writes '
                 f'with conversations seeing each other\'s writes) or remove it. The runner refuses it at start (exit 65).'),
        skill_step='Step 8',
    )]


#: `world_state.user_surfaces`: what a PERSON sees in the product (a `screen`) or receives from it
#: (a delivery). The backend validates the same declaration with the same keys, refusals and
#: bracketed refusal codes. TRI-STATE, like `render_actions`:
#: absent = not declared, `[]` = declared, and nothing reaches the user outside the chat.
_USER_SURFACES_KEY = 'user_surfaces'

#: kind -> whether a surface of that kind must name the row field holding the recipient's address
#: (the backend `KIND_TABLE[kind].recipient_required`).
_SURFACE_KIND_RECIPIENT_REQUIRED: dict[str, bool] = {
    'screen': False,
    'email': True,
    'text_message': True,
    'notification': True,
    'shared_with_me': True,
}

_SCREEN_KIND = 'screen'
_SURFACE_ORDERS = ('stored', 'newest_first')
_SURFACE_REQUIRED_KEYS = ('name', 'kind', 'title', 'rows', 'shown_fields', 'order')
_SURFACE_OPTIONAL_KEYS = ('row_key', 'recipient_field', 'numbered', 'hidden_when', 'actions', 'joins')
_ACTION_REQUIRED_KEYS = ('name', 'label', 'fields')
_ACTION_OPTIONAL_KEYS = ('when', 'values_from')
_JOIN_REQUIRED_KEYS = ('field', 'target', 'match', 'show')
_SURFACE_NAME_RE = re.compile(r'^[a-z][a-z0-9_]*$')
_SCALAR_TYPES = (str, int, float, bool, type(None))

#: `<type>` (entity rows) or `<type>[<selector>].<list_field>` (rows held inline in one entity's
#: list field), the `derived_from` / `held_by` grammar. A selector is a row index, a marker field,
#: or `<field>=<value>`.
_SURFACE_ROWS_RE = re.compile(r'^([A-Za-z_]\w*)(?:\[([^\]]+)\]\.([A-Za-z_]\w*))?$')
_SURFACE_SELECTOR_RE = re.compile(r'^([A-Za-z_]\w*)(?:=(.+))?$')
#: A join target: `<type>` (entity rows) or `<type>.<list_field>` (the items of a list field on those rows).
_JOIN_TARGET_RE = re.compile(r'^([A-Za-z_]\w*)(?:\.([A-Za-z_]\w*))?$')
_LIST_TYPE_RE = re.compile(r'^\s*list\[(.+)\]\s*$', re.IGNORECASE)

#: A declared field type's spellings -> the Python types a value of it may have. A type outside
#: this table (`list[...]`, a custom name) is not value-checked.
_FIELD_VALUE_TYPES: dict[str, tuple[type, ...]] = {
    'string': (str,),
    'str': (str,),
    'int': (int,),
    'integer': (int,),
    'float': (int, float),
    'number': (int, float),
    'decimal': (int, float),
    'money': (int, float),
    'bool': (bool,),
    'boolean': (bool,),
    'none': (type(None),),
    'null': (type(None),),
}


def _declared_world_fields(config: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """``{entity type: {field: its spec}}`` from ``world_state.entity_types`` (fields as a mapping or a list)."""
    world_state = config.get('world_state')
    types = world_state.get('entity_types') if isinstance(world_state, dict) else None
    if not isinstance(types, dict):
        return {}
    vocabulary: dict[str, dict[str, dict[str, Any]]] = {}
    for name, body in types.items():
        fields = body.get('fields') if isinstance(body, dict) else None
        if isinstance(fields, dict):
            vocabulary[str(name)] = {str(k): dict(v) if isinstance(v, dict) else {} for k, v in fields.items()}
        elif isinstance(fields, list):
            vocabulary[str(name)] = {str(f.get('name')): dict(f) for f in fields if isinstance(f, dict) and f.get('name')}
        else:
            vocabulary[str(name)] = {}
    return vocabulary


def _value_fits(value: Any, spec: dict[str, Any]) -> bool:
    """Whether ``value`` is a value of the declared field: in its ``enum``, and of its type."""
    enum_values = spec.get('enum')
    if isinstance(enum_values, list) and enum_values and value not in enum_values:
        return False
    declared = spec.get('type')
    if not isinstance(declared, str):
        return True
    allowed: list[type] = []
    for part in declared.split('|'):
        types = _FIELD_VALUE_TYPES.get(part.strip().lower())
        if types is None:
            return True
        allowed.extend(types)
    if isinstance(value, bool) and bool not in allowed:
        return False
    return isinstance(value, tuple(allowed))


def _is_scalar_list_type(declared: Any) -> bool:
    """A declared ``list[<scalar>]`` type: every item-union member is in the type table, not all null."""
    match = _LIST_TYPE_RE.match(declared) if isinstance(declared, str) else None
    if match is None:
        return False
    parts = [part.strip().lower() for part in match.group(1).split('|')]
    return all(part in _FIELD_VALUE_TYPES for part in parts) and any(_FIELD_VALUE_TYPES[part] != (type(None),) for part in parts)


def _item_fields(spec: dict[str, Any]) -> set[str] | None:
    """The row fields a list field declares typed (``item_specs`` / ``item_fields``), else ``None``."""
    specs = spec.get('item_specs')
    if isinstance(specs, dict) and specs:
        return {str(k) for k in specs}
    names = spec.get('item_fields')
    if isinstance(names, list) and names:
        return {str(n) for n in names}
    return None


#: The spellings the backend's model accepts for a bool field (pydantic's lax bool), so `numbered: 1`
#: loads on both sides.
_LAX_BOOL_WORDS = frozenset({'0', '1', 'f', 't', 'n', 'y', 'no', 'yes', 'off', 'on', 'false', 'true'})


def _lax_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return value in (0, 1)
    if isinstance(value, float):
        return value in (0.0, 1.0)
    return isinstance(value, str) and value.lower() in _LAX_BOOL_WORDS


def _str_keyed(value: Any) -> bool:
    """A mapping whose keys are all strings: what the backend's ``dict[str, ...]`` fields load."""
    return isinstance(value, dict) and all(isinstance(k, str) for k in value)


def _malformed(label: str, entry: Any) -> list[str]:
    """``[surface_malformed]`` refusals: one entry's keys and value shapes (the backend's model validation)."""
    def bad(loc: str, msg: str) -> str:
        return f"[surface_malformed] surface '{label}' field '{loc}': {msg}. Write {loc} in the documented shape."

    if not isinstance(entry, dict):
        return [bad('<entry>', f'a surface is a mapping, got {type(entry).__name__}')]
    errors = [f"[surface_malformed] surface '{label}' field '{k}': it is not a field of a surface. Remove {k}."
              for k in entry if k not in _SURFACE_REQUIRED_KEYS + _SURFACE_OPTIONAL_KEYS]
    errors += [f"[surface_malformed] surface '{label}' field '{k}': it is required. Add {k}." for k in _SURFACE_REQUIRED_KEYS if k not in entry]
    name, kind, order = entry.get('name'), entry.get('kind'), entry.get('order')
    if 'name' in entry and not (isinstance(name, str) and _SURFACE_NAME_RE.fullmatch(name)):
        errors.append(bad('name', 'lower_snake_case'))
    if 'kind' in entry and not (isinstance(kind, str) and kind in _SURFACE_KIND_RECIPIENT_REQUIRED):
        errors.append(bad('kind', f'one of {", ".join(_SURFACE_KIND_RECIPIENT_REQUIRED)}'))
    if 'order' in entry and not (isinstance(order, str) and order in _SURFACE_ORDERS):
        errors.append(bad('order', f'one of {", ".join(_SURFACE_ORDERS)}'))
    if 'title' in entry and not (isinstance(entry['title'], str) and entry['title']):
        errors.append(bad('title', 'a non-empty string'))
    if 'rows' in entry and not isinstance(entry['rows'], str):
        errors.append(bad('rows', 'a string'))
    for key in ('row_key', 'recipient_field'):
        if entry.get(key) is not None and not isinstance(entry[key], str):
            errors.append(bad(key, 'a field name'))
    shown = entry.get('shown_fields')
    if 'shown_fields' in entry and not (isinstance(shown, list) and shown and all(isinstance(f, str) for f in shown)):
        errors.append(bad('shown_fields', 'a list of at least one field name'))
    if 'numbered' in entry and not _lax_bool(entry['numbered']):
        errors.append(bad('numbered', 'true or false'))
    hidden = entry.get('hidden_when', {})
    if not (_str_keyed(hidden) and all(isinstance(v, _SCALAR_TYPES) for v in hidden.values())):
        errors.append(bad('hidden_when', 'a mapping of field to value'))
    actions = entry.get('actions', [])
    if not isinstance(actions, list):
        errors.append(bad('actions', 'a list'))
        actions = []
    for index, action in enumerate(actions):
        at = f'actions.{index}'
        if not isinstance(action, dict):
            errors.append(bad(at, 'an action is a mapping'))
            continue
        errors += [bad(f'{at}.{k}', 'not an action key') for k in action if k not in _ACTION_REQUIRED_KEYS + _ACTION_OPTIONAL_KEYS]
        errors += [bad(f'{at}.{k}', 'required') for k in _ACTION_REQUIRED_KEYS if k not in action]
        if 'name' in action and not (isinstance(action['name'], str) and _SURFACE_NAME_RE.fullmatch(action['name'])):
            errors.append(bad(f'{at}.name', 'lower_snake_case'))
        if 'label' in action and not (isinstance(action['label'], str) and action['label']):
            errors.append(bad(f'{at}.label', 'a non-empty string'))
        sets = action.get('fields')
        if 'fields' in action and not (
            _str_keyed(sets) and sets
            and all(v is None or (isinstance(v, list) and all(isinstance(x, _SCALAR_TYPES) for x in v)) for v in sets.values())
        ):
            errors.append(bad(f'{at}.fields', 'a mapping of at least one field to its allowed values, or null for any value'))
        when = action.get('when', {})
        if not (_str_keyed(when) and all(isinstance(v, _SCALAR_TYPES) for v in when.values())):
            errors.append(bad(f'{at}.when', 'a mapping of field to value'))
        values_from = action.get('values_from', {})
        if not (_str_keyed(values_from) and all(isinstance(v, str) for v in values_from.values())):
            errors.append(bad(f'{at}.values_from', 'a mapping of field to a path'))
    joins = entry.get('joins', [])
    if not isinstance(joins, list):
        errors.append(bad('joins', 'a list'))
        joins = []
    for index, join in enumerate(joins):
        at = f'joins.{index}'
        if not isinstance(join, dict):
            errors.append(bad(at, 'a join is a mapping'))
            continue
        errors += [bad(f'{at}.{k}', 'not a join key') for k in join if k not in _JOIN_REQUIRED_KEYS + ('within',)]
        errors += [bad(f'{at}.{k}', 'required') for k in _JOIN_REQUIRED_KEYS if k not in join]
        errors += [bad(f'{at}.{k}', 'a field name') for k in _JOIN_REQUIRED_KEYS if k in join and not isinstance(join[k], str)]
        within = join.get('within', {})
        if not (_str_keyed(within) and all(isinstance(v, str) for v in within.values())):
            errors.append(bad(f'{at}.within', 'a mapping of target field to row field'))
    return errors


def _rows_path(path: str) -> tuple[str, str | None, str | None] | None:
    """``(type, selector, list_field)`` for a path in the rows grammar, or ``None``."""
    match = _SURFACE_ROWS_RE.match(path.strip())
    if match is None:
        return None
    selector = match.group(2)
    return match.group(1), selector.strip() if selector else None, match.group(3)


def _join_target(target: str) -> tuple[str, str | None] | None:
    """``(type, list_field | None)`` for a join target, or ``None`` when it is not in the grammar."""
    match = _JOIN_TARGET_RE.match(target.strip())
    return (match.group(1), match.group(2)) if match else None


def _values_from_path(path: str) -> tuple[str, str | None, str | None] | None:
    """A ``values_from`` path, ``<type>[<selector>].<field>`` (one entity's list field), or ``None``."""
    parsed = _rows_path(path)
    return parsed if parsed is not None and parsed[2] is not None else None


def _structural(surface: dict[str, Any]) -> list[str]:
    """The refusals that need no declared world (the backend refuses them too)."""
    where = f"surface '{surface['name']}'"
    errors: list[str] = []
    rows = _rows_path(surface['rows'])
    if rows is None:
        errors.append(f"[surface_rows_malformed] {where} field 'rows': {surface['rows']!r} is not in the rows grammar. "
            "Write it as '<type>' or '<type>[<selector>].<list_field>'.")
    inline = rows is not None and rows[2] is not None
    if inline and not surface.get('row_key'):
        errors.append(f"[surface_row_key_missing] {where} field 'row_key': rows stored in a list field need the field that keeps a row's identity. Add row_key.")
    if rows is not None and not inline and surface.get('row_key') is not None:
        errors.append(f"[surface_row_key_misplaced] {where} field 'row_key': entity rows are keyed by their entity id. Remove row_key.")
    if _SURFACE_KIND_RECIPIENT_REQUIRED[surface['kind']] and not surface.get('recipient_field'):
        errors.append(f"[surface_recipient_missing] {where} field 'recipient_field': a {surface['kind']} surface is addressed to someone. "
            'Add recipient_field: the field that holds the recipient.')
    seen: set[str] = set()
    for action in surface.get('actions', []):
        slot = f"actions.{action['name']}"
        if action['name'] in seen:
            errors.append(f"[surface_action_duplicate] {where} field 'actions': action '{action['name']}' is declared twice. Give each action its own name.")
        seen.add(action['name'])
        errors += [
            f"[surface_action_values_empty] {where} field '{slot}.fields': '{field}' lists no values. List them, or write null for any value."
            for field, allowed in action['fields'].items()
            if allowed is not None and not allowed
        ]
        if inline:
            # A row inside a list field has no declared field types: the user edits what the row shows.
            errors += [
                f"[surface_action_field_not_shown] {where} field '{slot}.{part}': '{field}' is not in shown_fields, and an action on rows "
                'inside a list field may only set and test fields the row shows. Add it to shown_fields or drop it from the action.'
                for part, keys in (('fields', action['fields']), ('when', action.get('when', {})))
                for field in keys
                if field not in surface['shown_fields']
            ]
        for field, path in action.get('values_from', {}).items():
            if action['fields'].get(field, [None]) is not None:
                errors.append(
                    f"[surface_values_from_misplaced] {where} field '{slot}.values_from': '{field}' is not a field the action sets freely. "
                    f'Declare it as {field}: null under fields, or drop it from values_from.'
                )
            if _values_from_path(path) is None:
                errors.append(f"[surface_values_from_malformed] {where} field '{slot}.values_from': {path!r} names no list. Write it as '<type>[<selector>].<field>'.")
    joined: set[str] = set()
    for join in surface.get('joins', []):
        if join['field'] in joined:
            errors.append(f"[surface_join_duplicate] {where} field 'joins': '{join['field']}' is joined twice. Keep one join per field.")
        joined.add(join['field'])
        if join['field'] not in surface['shown_fields']:
            errors.append(f"[surface_join_field_not_shown] {where} field 'joins.{join['field']}': '{join['field']}' is not in shown_fields. "
                'Add it to shown_fields or remove the join.')
        if _join_target(join['target']) is None:
            errors.append(f"[surface_join_malformed] {where} field 'joins.{join['field']}.target': {join['target']!r} names no rows. "
                "Write it as '<type>' or '<type>.<list_field>'.")
    return errors


def _against_the_world(surface: dict[str, Any], vocabulary: dict[str, dict[str, dict[str, Any]]]) -> list[str]:
    """The refusals against ``world_state.entity_types`` (the backend refuses them too)."""
    where = f"surface '{surface['name']}'"
    rows = _rows_path(surface['rows'])
    if rows is None:
        return []
    row_type, selector, list_field = rows
    if row_type not in vocabulary:
        return [f"[surface_type_undeclared] {where} field 'rows': type '{row_type}' is not declared. "
                'Use a declared type or declare it under world_state.entity_types.']
    specs = vocabulary[row_type]
    errors: list[str] = []

    def need(field: str, slot: str, declared: Any, owner: str) -> bool:
        if field in declared:
            return True
        errors.append(f"[surface_field_undeclared] {where} field '{slot}': '{field}' is not a declared field of '{owner}'. "
            f'Use a declared field or declare it under world_state.entity_types.{owner.split(".", 1)[0]}.')
        return False

    def fits(field: str, value: Any, slot: str) -> None:
        if field in specs and not _value_fits(value, specs[field]):
            errors.append(f"[surface_value_mistyped] {where} field '{slot}': {json.dumps(value)} is not a declared value of '{field}'. "
                'Use a value of its declared type and enum.')

    def selector_ok(selector: str | None, owner_specs: dict[str, Any], owner: str, slot: str) -> None:
        if selector is None or selector.isdigit():
            return
        marker = _SURFACE_SELECTOR_RE.match(selector)
        if marker is None:
            errors.append(f"[surface_rows_malformed] {where} field '{slot}': selector [{selector}] names no row. Write an index, a marker field or <field>=<value>.")
        else:
            need(marker.group(1), slot, owner_specs, owner)

    def type_specs(type_name: str, slot: str) -> dict[str, dict[str, Any]] | None:
        if type_name not in vocabulary:
            errors.append(f"[surface_type_undeclared] {where} field '{slot}': type '{type_name}' is not declared. "
                'Use a declared type or declare it under world_state.entity_types.')
            return None
        return vocabulary[type_name]

    selector_ok(selector, specs, row_type, 'rows')
    # The fields a row of this surface carries, when they are declared (None: untyped inline rows).
    row_fields: Any = specs
    row_owner = row_type
    hidden_when = surface.get('hidden_when', {})
    if list_field is not None:
        row_fields, row_owner = None, f'{row_type}.{list_field}'
        if need(list_field, 'rows', specs, row_type):
            row_fields = _item_fields(specs[list_field])
            if row_fields is not None:
                slots = [('row_key', surface.get('row_key')), *(('shown_fields', f) for f in surface['shown_fields'])]
                slots += [('recipient_field', surface.get('recipient_field')), *(('hidden_when', f) for f in hidden_when)]
                for slot, field in slots:
                    if field:
                        need(field, slot, row_fields, row_owner)
    else:
        for field in surface['shown_fields']:
            need(field, 'shown_fields', specs, row_type)
        if surface.get('recipient_field'):
            need(surface['recipient_field'], 'recipient_field', specs, row_type)
        for field, value in hidden_when.items():
            if need(field, 'hidden_when', specs, row_type):
                fits(field, value, 'hidden_when')
    for action in surface.get('actions', []):
        slot = f"actions.{action['name']}"
        if row_fields is not None:
            for field, allowed in action['fields'].items():
                if need(field, f'{slot}.fields', row_fields, row_owner) and list_field is None:
                    for value in allowed or []:
                        fits(field, value, f'{slot}.fields')
            for field, value in action.get('when', {}).items():
                if need(field, f'{slot}.when', row_fields, row_owner) and list_field is None:
                    fits(field, value, f'{slot}.when')
        for path in action.get('values_from', {}).values():
            source = _values_from_path(path)
            if source is None or (source_specs := type_specs(source[0], f'{slot}.values_from')) is None:
                continue
            selector_ok(source[1], source_specs, source[0], f'{slot}.values_from')
            if need(source[2] or '', f'{slot}.values_from', source_specs, source[0]):
                declared = source_specs[source[2] or ''].get('type')
                if declared is not None and not _is_scalar_list_type(declared):
                    errors.append(
                        f"[surface_values_from_not_a_scalar_list] {where} field '{slot}.values_from': '{source[0]}.{source[2]}' "
                        f'is declared {declared!r}, not a list of scalars. Point values_from at a list[str] (or other list-of-scalars) field.'
                    )
    for join in surface.get('joins', []):
        slot = f"joins.{join['field']}"
        target = _join_target(join['target'])
        if target is None or (target_specs := type_specs(target[0], f'{slot}.target')) is None:
            continue
        target_type, target_list = target
        candidate_fields: Any = target_specs
        candidate_owner = target_type
        if target_list is not None:
            candidate_fields, candidate_owner = None, f'{target_type}.{target_list}'
            if need(target_list, f'{slot}.target', target_specs, target_type):
                list_spec = target_specs[target_list]
                if 'type' in list_spec and str(list_spec['type']).replace(' ', '').lower() not in ('list[dict]', 'list[object]', 'array'):
                    errors.append(
                        f"[surface_join_target_not_a_row_list] {where} field '{slot}.target': '{candidate_owner}' is declared "
                        f'{list_spec.get("type")!r}, not a list of rows. Point the join at a list[dict] field.'
                    )
                candidate_fields = _item_fields(list_spec)
        if candidate_fields is not None:
            need(join['match'], f'{slot}.match', candidate_fields, candidate_owner)
            need(join['show'], f'{slot}.show', candidate_fields, candidate_owner)
        for target_field, source_field in join.get('within', {}).items():
            need(target_field, f'{slot}.within', target_specs, target_type)
            if row_fields is not None:
                need(source_field, f'{slot}.within', row_fields, row_owner)
    return errors


def _load_user_surfaces(config: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, list[str]]:
    """``(surfaces, errors)``: the well-shaped declared surfaces (``None`` when the key is absent or unusable), and every refusal."""
    world_state = config.get('world_state')
    if not isinstance(world_state, dict) or _USER_SURFACES_KEY not in world_state:
        return None, []
    raw = world_state[_USER_SURFACES_KEY]
    if not isinstance(raw, list):
        return None, [
            f"[surfaces_malformed] field '{_USER_SURFACES_KEY}': must be a list of surfaces, got {type(raw).__name__}; write [] or omit the key."
        ]
    shape = config.get('shape')
    pipeline = [
        f'[surfaces_on_pipeline] `{_USER_SURFACES_KEY}` is declared, but this agent runs as a pipeline with no simulated user. Remove `{_USER_SURFACES_KEY}`.'
    ] if raw and isinstance(shape, str) and shape.strip().lower() == 'pipeline' else []
    errors: list[str] = []
    vocabulary = _declared_world_fields(config)
    surfaces: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, entry in enumerate(raw):
        label = entry['name'] if isinstance(entry, dict) and isinstance(entry.get('name'), str) else f'#{index}'
        malformed = _malformed(label, entry)
        if malformed:
            errors += malformed
            continue
        if entry['name'] in names:
            errors.append(f"[surface_name_duplicate] surface '{entry['name']}' field 'name': the name is used twice. Give each surface its own name.")
        names.add(entry['name'])
        errors += _structural(entry)
        surfaces.append(entry)
    if not errors:
        # As the backend: the pipeline rule and the world checks judge only a declaration that loaded clean.
        errors = pipeline + [e for surface in surfaces for e in _against_the_world(surface, vocabulary)]
    return surfaces, errors


def _check_user_surfaces(config: dict[str, Any]) -> list[Finding]:
    """``world_state.user_surfaces`` validates against the declared world."""
    _, errors = _load_user_surfaces(config)
    return [Finding(severity='fail', code=Code.USER_SURFACES_INVALID, message=e, skill_step='Step 8') for e in errors]


def _check_agent_host_declares_user_surfaces(config: dict[str, Any], *, route: str) -> list[Finding]:
    """The agent-host route (Agentforce) must DECLARE ``world_state.user_surfaces``, ``[]`` when nothing else reaches the user.

    Its Agent API is a text-only channel: the user sees the agent's replies and nothing else. An undeclared bench
    keeps the simulated user's undeclared view, which shows it the agent's backend calls, their args and their
    results (a case record, an authentication key), so the persona would act on what no customer was shown.
    """
    world_state = config.get('world_state')
    if route != 'agent_host' or (isinstance(world_state, dict) and _USER_SURFACES_KEY in world_state):
        return []
    return [Finding(
        severity='fail',
        code=Code.AGENT_HOST_USER_SURFACES_UNDECLARED,
        message=(f'this bench runs on the agent-host route, whose Agent API shows the user only the agent\'s replies, and `world_state.{_USER_SURFACES_KEY}` '
                 f'is not declared, so the simulated user would also read the agent\'s backend calls, their arguments and their results. '
                 f'Add `{_USER_SURFACES_KEY}: []` under `world_state:` (list a surface only for a view or delivery the user really gets outside the chat).'),
        skill_step='Step 8',
    )]


def _check_agent_host_declares_render_actions(config: dict[str, Any], *, route: str) -> list[Finding]:
    """The agent-host route (Agentforce) must DECLARE top-level ``render_actions``, ``[]`` when it renders nothing.

    The Agent API shows the user only the agent's text replies, so the render surface is empty by declaration. The backend
    filters the persona's view of the agent's typed actions only when the scenario carries ``render_actions``
    (``scan_contract.render_actions`` -> ``declared_render_verbs``); absent means unclassifiable and nothing is filtered.
    """
    if route != 'agent_host' or 'render_actions' in config:
        return []
    return [Finding(
        severity='fail',
        code=Code.AGENT_HOST_RENDER_ACTIONS_UNDECLARED,
        message=('this bench runs on the agent-host route, whose Agent API renders nothing to the user except the agent\'s text replies, and '
                 '`render_actions` is not declared, so the persona\'s view of the agent\'s typed actions is never filtered. '
                 'Add a top-level `render_actions: []` beside `tools:`.'),
        skill_step='Step 8',
    )]


def _check_render_actions_declared(config: dict[str, Any], *, route: str) -> list[Finding]:
    """Every other route declares top-level ``render_actions`` too, ``[]`` when the agent renders nothing (warn).

    The backend's callable surface is ``tools`` ∪ ``render_actions`` (the backend's surface-conformance check), and an absent
    key leaves it OPEN: every call off ``tools`` is exempt, so an undeclared call is never charged. Warn, not fail, so a
    map written before the rule keeps running; the agent-host route keeps its own refusal above.
    """
    if route == 'agent_host' or 'render_actions' in config:
        return []
    return [Finding(
        severity='warn',
        code=Code.RENDER_ACTIONS_UNDECLARED,
        message=('`render_actions` is not declared, so the callable surface is open and a call the config does not declare is '
                 'never charged. Add a top-level `render_actions: []` beside `tools:` (list the verbs when the agent renders some); '
                 'then declare every call the trail carries, framework-internal ones included, under `tools`.'),
        skill_step='Step 8',
    )]


#: The harness export that carries out a simulated user's act (SDK `child.USER_ACTION_HOOK`).
_USER_ACTION_HOOK = 'apply_user_action'


def _check_inline_actions_have_a_hook(config: dict[str, Any], simulate_source: str | None) -> list[Finding]:
    """An action on rows inside a list field runs only through the harness's ``apply_user_action``.

    Such a row lives in one entity's list, whose versioning only the product knows (a new revision
    per edit), so the SDK never writes it generically: without the hook every act is unexecutable.
    Skipped when no harness source was read.
    """
    if simulate_source is None:
        return []
    try:
        bound = _module_scope_bindings(ast.parse(simulate_source))
    except SyntaxError:
        return []
    if _USER_ACTION_HOOK in bound:
        return []
    surfaces, _ = _load_user_surfaces(config)
    return [
        Finding(
            severity='fail',
            code=Code.INLINE_ACTION_WITHOUT_HOOK,
            message=(
                f"User surface {surface['name']!r} declares actions on rows inside a list field (`{surface['rows']}`), and "
                f'simulate.py exports no `{_USER_ACTION_HOOK}`, so no act of the simulated user on it can be carried out. '
                f'Add `def {_USER_ACTION_HOOK}(act: dict) -> None` to simulate.py, writing the edit the way the product does.'
            ),
            skill_step='Step 8',
        )
        for surface in surfaces or []
        if surface.get('actions') and (rows := _rows_path(surface['rows'])) is not None and rows[2] is not None
    ]


def _resolve_write_strategy(tool_entry: dict[str, Any]) -> str | None:
    """``descriptor.write_strategy``, else ``mocked`` when ``writes_to`` is set: the read-side rule, mirrored."""
    explicit = _descriptor_block(tool_entry).get('write_strategy')
    if isinstance(explicit, str) and explicit:
        return explicit
    return 'mocked' if _tool_writes_to_is_non_null(tool_entry) else None


def _declares_a_write_of(tool_entry: dict[str, Any], entity_type: str) -> bool:
    """A ``descriptor.post_conditions`` entry changes or creates an ``entity_type`` row."""
    post = _descriptor_block(tool_entry).get('post_conditions')
    return isinstance(post, list) and any(isinstance(c, dict) and c.get('entity_type') == entity_type for c in post)


#: The harness export the SDK reads the world through whenever simulate.py binds it, whoever owns
#: the world (SDK `child._read_world`: the channel dispatches on the hook; the owner decides only
#: what the hook's absence means).
_WORLD_SNAPSHOT_HOOK = 'world_snapshot'

#: The row key the backend selects a surface's rows by (``user_world/projection.py``).
_ENTITY_TYPE_KEY = '_entity_type'

#: The SDK reads of the mock's world. A hook that merges one (``{**plurai.world_state_snapshot(),
#: **rows}``, the documented split world) hands the mock's rows over as well.
_MOCK_WORLD_READS = frozenset({'world_state_snapshot', 'get_full_state'})


def _harness_owns_the_world(config: dict[str, Any]) -> bool:
    """``world_state.owner`` is ``agent`` or ``external``: without ``world_snapshot()`` the run has no world to read."""
    world_state = config.get('world_state')
    owner = world_state.get('owner') if isinstance(world_state, dict) else None
    return isinstance(owner, str) and owner.strip().lower() in set(_WORLD_OWNERS) - {_UNDECLARED_WORLD_OWNER}


def _declared_owned_types(config: dict[str, Any]) -> frozenset[str] | None:
    """``world_state.owned_types`` when it is a list of type names; ``None`` when absent or malformed."""
    world_state = config.get('world_state')
    value = world_state.get('owned_types') if isinstance(world_state, dict) else None
    if not isinstance(value, list) or not all(isinstance(t, str) and t.strip() for t in value):
        return None
    return frozenset(t.strip() for t in value)


def _check_world_owned_types(config: dict[str, Any]) -> list[Finding]:
    """A split world declares the types the harness's store holds as a list of declared entity types. Warn-only."""
    world_state = config.get('world_state')
    declared_value = world_state.get('owned_types') if isinstance(world_state, dict) else None
    findings: list[Finding] = []
    owned = _declared_owned_types(config)
    if declared_value is not None:
        entity_types = world_state.get('entity_types') if isinstance(world_state.get('entity_types'), dict) else {}
        unknown = sorted(owned - set(entity_types)) if owned is not None and entity_types else []
        if owned is None or unknown:
            why = f'is {declared_value!r}, not a list of entity type names' if owned is None else f'names types `world_state.entity_types` does not declare: {", ".join(unknown)}'
            findings.append(Finding(severity='warn', code=Code.WORLD_OWNED_TYPES_INVALID, message=f'world_state.owned_types {why}', skill_step='Step 8'))
    return findings


def _check_world_reset_unconfirmed(config: dict[str, Any], simulate_only_source: str | None) -> list[Finding]:
    """``simulate.py`` exports ``reset`` on a world the agent or an external system owns, and no valid
    ``world_state.confirmation`` confirms a store. That reset clears the real store before every conversation, which
    runs live only on the user's confirmation. Warn-only: the run does not refuse it, so this is where it is said.
    """
    owner = _readable_world_owner(config)
    if owner not in ('agent', 'external') or simulate_only_source is None:
        return []
    try:
        if 'reset' not in _module_scope_bindings(ast.parse(simulate_only_source)):
            return []
    except SyntaxError:
        return []
    import writer_readers  # noqa: PLC0415 - the ONE reading of what a confirmation covers

    world_state = config.get('world_state')
    block = world_state.get('confirmation') if isinstance(world_state, dict) else None
    confirmed = isinstance(block, list) and not writer_readers.confirmation_errors(config) and any(
        isinstance(entry, dict) and entry.get('answer') in writer_readers.STORE_ANSWERS for entry in block)
    if confirmed:
        return []
    return [Finding(
        severity='warn',
        code=Code.WORLD_RESET_UNCONFIRMED,
        message=(
            f'`simulate.py` exports `reset()` on a world `world_state.owner: {owner}` says is real, and no `world_state.confirmation` '
            'confirms that store, so every conversation\'s reset clears it. Ask the user the store question and record '
            '`disposable_copy` (or `harness_created`) only on their Yes; otherwise simulate the store and drop `reset()` (a '
            'mock-owned world exports none). The run does not refuse it.'
        ),
        skill_step='Step 8',
    )]


def _check_world_snapshot_merge(config: dict[str, Any], simulate_source: str | None, simulate_only_source: str | None) -> list[Finding]:
    """``world_snapshot()`` never merges the mock's half of a split world.

    The SDK composes the two halves in one place (every hook row, plus the mock's rows of every
    other type). A hook that hand-merges ``plurai.world_state_snapshot()`` keeps the mock's seeded
    copy of every record the store re-keyed, beside the live one. Warn-only.
    """
    world_state = config.get('world_state')
    declared_value = world_state.get('owned_types') if isinstance(world_state, dict) else None
    findings: list[Finding] = []
    if simulate_source is None or simulate_only_source is None:
        return findings
    try:
        simulate_only, merged = ast.parse(simulate_only_source), ast.parse(simulate_source)
    except SyntaxError:
        return findings
    if _WORLD_SNAPSHOT_HOOK not in _module_scope_bindings(simulate_only):
        return findings
    reach = _snapshot_reach(simulate_only, merged)
    if reach is None or not reach.merges_mock:
        return findings
    remedy = (
        'Drop the merge: with `world_state.owned_types` declared, the run composes the hook\'s rows with the mock\'s rows of every other type.'
        if declared_value is not None else
        'Declare `world_state.owned_types: [<the entity types your store holds>]` and return only your store\'s rows; the run composes them '
        'with the mock\'s rows of every other type.'
    )
    findings.append(Finding(
        severity='warn',
        code=Code.WORLD_SNAPSHOT_MERGES_MOCK,
        message=(
            f'`{_WORLD_SNAPSHOT_HOOK}()` merges the mock\'s world into its own rows, so the mock\'s seeded copy of any record the '
            f'harness keeps under another key is scored beside the live one. {remedy}'
        ),
        skill_step='Step 8',
    ))
    return findings


class _SnapshotReach(NamedTuple):
    """What ``world_snapshot()`` returns, read from its body and every function it reaches."""

    types: frozenset[str]  # the ``_entity_type`` values it stamps, literally or through a module string constant
    undetermined: bool  # a stamp whose value this check cannot read
    merges_mock: bool  # it merges the mock's world into what it returns


def _snapshot_reach(simulate_only: ast.Module, merged: ast.Module) -> _SnapshotReach | None:
    """Read what the exported ``world_snapshot`` stamps, or ``None`` when its body is not in the harness sources.

    Starts at the function simulate.py binds to the hook (a ``def``, an alias, or a ``from … import``
    of a helper's function) and follows every function its body names. A stamp is
    ``{'_entity_type': x}``, ``row['_entity_type'] = x`` or a ``_entity_type=x`` keyword.
    """
    target = _WORLD_SNAPSHOT_HOOK
    for node in simulate_only.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and any(
            isinstance(t, ast.Name) and t.id == _WORLD_SNAPSHOT_HOOK for t in node.targets
        ):
            target = node.value.id
        elif isinstance(node, ast.ImportFrom):
            target = next((a.name for a in node.names if (a.asname or a.name) == _WORLD_SNAPSHOT_HOOK), target)
    defs: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for node in ast.walk(merged):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.setdefault(node.name, []).append(node)
    if target not in defs:
        return None
    constants = {
        t.id: node.value.value
        for node in merged.body if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        for t in node.targets if isinstance(t, ast.Name)
    }
    types: set[str] = set()
    undetermined = merges_mock = False
    seen, queue = {target}, list(defs[target])
    while queue:
        for node in ast.walk(queue.pop()):
            named = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
            if named in defs and named not in seen:
                seen.add(named)
                queue += defs[named]
            if isinstance(node, ast.Call):
                func = node.func
                called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
                merges_mock |= called in _MOCK_WORLD_READS
            stamps: list[ast.expr] = []
            if isinstance(node, ast.Dict):
                stamps = [v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant) and k.value == _ENTITY_TYPE_KEY]
            elif isinstance(node, ast.Call):
                stamps = [kw.value for kw in node.keywords if kw.arg == _ENTITY_TYPE_KEY]
            elif isinstance(node, ast.Assign):
                stamps = [
                    node.value for t in node.targets
                    if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and t.slice.value == _ENTITY_TYPE_KEY
                ]
            for value in stamps:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    types.add(value.value)
                elif isinstance(value, ast.Name) and value.id in constants:
                    types.add(constants[value.id])
                else:
                    undetermined = True
    return _SnapshotReach(frozenset(types), undetermined, merges_mock)


def _writer_findings(config: dict[str, Any], surface: dict[str, Any], row_type: str) -> list[Finding]:
    """G8 on the mock's world: a tool declares a write of the delivery's rows, and that write lands in the mock."""
    name, kind = surface.get('name'), surface.get('kind')
    tools = _mapping_block(config, 'tools')
    writers = {t: e for t, e in tools.items() if isinstance(e, dict) and _declares_a_write_of(e, row_type)}
    if not writers:
        return [Finding(
            severity='fail',
            code=Code.DELIVERY_SURFACE_NO_WRITER,
            message=(
                f'No tool writes `{row_type}`, the rows of the {kind} surface {name!r}, so the simulated user '
                f'never receives it. On the tool that sends it, add a post_condition that creates the row: '
                f'`{{entity_type: {row_type}, count_change: "+1", with_fields: {{...}}}}`.'
            ),
            skill_step='Step 8',
        )]
    blocked: list[str] = []
    for tool, entry in writers.items():
        mode = entry.get('mode')
        if mode is None or mode == 'mock':
            return []
        if mode == 'real':
            strategy = _resolve_write_strategy(entry)
            if strategy == 'mocked':
                return []
            if strategy == 'real':
                blocked.append(
                    f'Tool {tool!r} writes `{row_type}`, the rows of the {kind} surface {name!r}, but declares '
                    f'`write_strategy: real`: the real service performs its write, outside the simulated world, so the '
                    f'simulated user never receives it. Remove its `{row_type}` post_condition, and the {name!r} surface '
                    f'when no mocked tool writes its rows.'
                )
                continue
            why = f'its write strategy is {strategy!r}' if strategy else 'it declares no write strategy'
            blocked.append(_blocked_writer(tool, row_type, kind, name, why, f'Set `descriptor.write_strategy: mocked` on {tool!r}.'))
        else:
            blocked.append(_blocked_writer(tool, row_type, kind, name, f'it runs with `mode: {mode}`', f'Set `mode: mock` on {tool!r}.'))
    return [Finding(severity='fail', code=Code.DELIVERY_SURFACE_NO_WRITER, message=message, skill_step='Step 8') for message in blocked]


def _blocked_writer(tool: str, row_type: str, kind: Any, name: Any, why: str, remedy: str) -> str:
    """A writer whose write misses the simulated world, and the one setting that lands it there."""
    return (
        f'Tool {tool!r} writes `{row_type}`, the rows of the {kind} surface {name!r}, but {why}, so '
        f'the row never reaches the simulated world and the simulated user never receives it. {remedy}'
    )


def _snapshot_gap(surface: dict[str, Any], row_type: str, hooked: bool) -> Finding:
    """A delivery the world the run reads never holds, with its one remedy: return the rows from ``world_snapshot()``."""
    row = f"`{{'{_ENTITY_TYPE_KEY}': '{row_type}', ...}}`"
    why = (
        f'simulate.py exports `{_WORLD_SNAPSHOT_HOOK}()`, so the run reads the world from it, and it returns no `{row_type}` rows'
        if hooked else
        f'this harness owns the world (`world_state.owner`), so the run reads it only through `{_WORLD_SNAPSHOT_HOOK}()`, '
        f'and simulate.py exports none'
    )
    return Finding(
        severity='fail',
        code=Code.DELIVERY_SURFACE_NO_WRITER,
        message=(
            f"The {surface['kind']} surface {surface['name']!r} lists `{row_type}` rows. {why[0].upper()}{why[1:]}: the "
            f'simulated user never receives it. Return each `{row_type}` row from `{_WORLD_SNAPSHOT_HOOK}()` in simulate.py as {row}.'
        ),
        skill_step='Step 8',
    )


def _check_deliveries_reach_the_world(
    config: dict[str, Any], simulate_source: str | None, simulate_only_source: str | None,
) -> list[Finding]:
    """Every delivery surface's rows are in the world the run reads, so the simulated user receives it (G8).

    Dispatched as the SDK reads the world:

    - simulate.py exports ``world_snapshot`` (any owner): the hook returns rows of the surface's
      type, or merges the mock's world and a tool writes them there. A stamp this check cannot
      read leaves the surface undetermined (a warn), never confirmed.
    - no hook, the mock's world: a tool declares a write of the rows that lands in the mock.
    - no hook, a harness-owned world: nothing reaches the run; add the hook.

    Without a harness source only the config-readable row runs: the mock's world is checked for a
    writer, and ``harness_unknowable`` reports the rest.
    """
    deliveries = [
        (surface, rows[0])
        for surface in _load_user_surfaces(config)[0] or []
        if surface.get('kind') != _SCREEN_KIND and isinstance(surface.get('rows'), str)
        and (rows := _rows_path(surface['rows'])) is not None
    ]
    owned = _harness_owns_the_world(config)
    if simulate_source is None or simulate_only_source is None:
        return [] if owned else [f for surface, row_type in deliveries for f in _writer_findings(config, surface, row_type)]
    try:
        simulate_only, merged = ast.parse(simulate_only_source), ast.parse(simulate_source)
    except SyntaxError:
        return []
    if _WORLD_SNAPSHOT_HOOK not in _module_scope_bindings(simulate_only):
        if owned:
            return [_snapshot_gap(surface, row_type, hooked=False) for surface, row_type in deliveries]
        return [f for surface, row_type in deliveries for f in _writer_findings(config, surface, row_type)]
    reach = _snapshot_reach(simulate_only, merged)
    owned = _declared_owned_types(config)
    findings: list[Finding] = []
    for surface, row_type in deliveries:
        if reach is not None and row_type in reach.types:
            continue
        writer = _writer_findings(config, surface, row_type)
        # The mock's half reaches the run when the hook merges it (`world_snapshot_merges_mock` says to
        # stop) or when the SDK composes it: a type outside `world_state.owned_types` comes from the mock.
        mock_half = reach is not None and (reach.merges_mock or (owned is not None and row_type not in owned))
        if mock_half and not writer:
            continue
        if reach is None or reach.undetermined:
            findings.append(Finding(
                severity='warn',
                code=Code.DELIVERY_SURFACE_UNDETERMINED,
                message=(
                    f"The {surface['kind']} surface {surface['name']!r} lists `{row_type}` rows, and whether "
                    f'`{_WORLD_SNAPSHOT_HOOK}()` returns them cannot be read from the harness source (its `{_ENTITY_TYPE_KEY}` '
                    f'is computed, or its body is outside the harness files). If it does not, the run reports the surface as not captured.'
                ),
                skill_step='Step 8',
            ))
        elif mock_half:
            findings += writer
        else:
            findings.append(_snapshot_gap(surface, row_type, hooked=True))
    return findings


def _module_scope_bindings(tree: ast.Module) -> set[str]:
    """Every name simulate.py binds AT IMPORT TIME — the set ``getattr(module, name)`` can hit.

    Deliberately wider than "top-level ``def``": ``invoke_pipeline = _impl`` and
    ``from ._hooks import invoke_pipeline`` bind the hook just as well, and a check that saw only
    ``def`` would refuse a harness the runner drives perfectly well — a false refusal is worse
    than no check, because it is the reason an author stops reading the verifier. Statement
    bodies that RUN at import (``if`` / ``try`` / ``with`` / ``for``) are descended into; function
    and class bodies are not, because a name bound there is not an attribute of the module.

    A DELIBERATE COPY of ``skills/simulation-setup-python/scripts/verify_simulate.py``'s function
    of the same name: each verifier keeps its own copy rather than importing the other, and the two
    must answer the binding question identically over every harness shape. Their code is identical
    with docstrings dropped; this paragraph differs from its twin's, which is byte-pinned to the
    SDK's third copy (``plurai/module_bindings.py``).
    """
    bound: set[str] = set()

    def _walk(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
                continue  # its body is a different scope
            if isinstance(node, ast.Assign):
                # Flatten tuple/list targets first: `invoke_pipeline, _seed = pair()` binds the
                # hook exactly as a plain assignment does, and refusing it would be the false
                # refusal this function exists to avoid. (`match`/`case` capture patterns can bind
                # a module-scope name too — the one door knowingly left shut, exotic as it is; a
                # harness written that way is refused, and the message names the fix.)
                targets: list[ast.expr] = []
                for target in node.targets:
                    if isinstance(target, (ast.Tuple, ast.List)):
                        targets.extend(target.elts)
                    else:
                        targets.append(target)
                for target in targets:
                    if isinstance(target, ast.Name):
                        bound.add(target.id)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                if isinstance(node.target, ast.Name):
                    bound.add(node.target.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound.add(alias.asname or alias.name.split('.')[0])
            for field in ('body', 'orelse', 'finalbody'):
                nested = getattr(node, field, None)
                if isinstance(nested, list):
                    _walk([s for s in nested if isinstance(s, ast.stmt)])
            for handler in getattr(node, 'handlers', []) or []:
                _walk(handler.body)

    _walk(tree.body)
    return bound


def _reads_as_a_shape_word(value: str) -> bool:
    """Is this value one of the two shapes, or a misspelling of one?

    The question a NESTED `shape:` has to answer before it is claimed. `dialog` / `PIPELINE ` are
    the word itself; `pipelne` / `dailog` are the author reaching for it and missing. Anything
    further away is a different word that happens to be spelled `shape` — `rectangular`,
    `rounded`, `[3, 224, 224]` — and is not this check's business.
    """
    word = value.strip().lower()
    return bool(word in _SHAPE_HOOKS or difflib.get_close_matches(word, sorted(_SHAPE_HOOKS), n=1, cutoff=0.6))


def _check_shape_declaration(
    config: dict[str, Any], simulate_source: str | None,
) -> list[Finding]:
    """``shape:`` must be one of the two declarable shapes, and must not contradict the harness.

    Two failures, both of which shipped silently before the key existed:

    - a bench that IS one-shot carries its shape only in prose (a YAML comment), so the runner
      has to guess it from the hooks and every other reader guesses separately;
    - a config that SAYS one shape over a harness that binds the other. That contradiction is the
      expensive one: the declaration wins at dispatch (declared first, derived second), so the
      runner drives a hook the harness does not export and the bench dies at its first
      invocation — after generation has been paid for.

    ABSENT is a finding for two of the four HOOK CENSUSES, and the severities differ because the
    costs do. The hooks are a HINT that never chooses a route, so an undeclared bench is driven as
    :data:`_UNDECLARED_SHAPE`. A harness that cannot serve that route (``invoke_pipeline`` and no
    ``invoke_agent``) has no hook for the turn loop to call, so every scenario fails on turn 1
    after the set has been paid for — ``fail``. A harness binding BOTH hooks CAN serve it, so it
    is driven correctly and the other hook is simply never called: every policy scoped to that
    hook scores against an empty result, which is zero verdicts on a GREEN run — expensive, but
    not a refusal, so ``warn``. The remaining two censuses stay green: an ``invoke_agent`` bench
    validates exactly as it did (requiring the key of every bench would red essentially every
    bench in existence), and a harness binding neither hook belongs to ``verify_simulate``'s
    ``missing_invoke_hook``, not here. A ``shape`` nested one level down is that same absence
    wearing a declaration — only the ROOT key is read anywhere — so it is named, not passed over:
    ``fail`` when the value disagrees with what the bench is actually driven as, ``warn`` when it
    merely restates it (inert, but a bench the runner drives correctly must not be blocked).

    Read the way the runner reads it (``str(raw).strip().lower()``), the same normalisation
    ``_check_world_state_owner`` applies to the other key the SDK routes through
    ``orchestrator._declared_workspace_value``: ``Dialog`` and ``  PIPELINE `` are driven there,
    so a check that refused them would refuse a config that runs.

    The hook check is skipped when simulate.py was not given / did not parse: "I could not read
    the harness" is not "the harness contradicts you", and the parse failure is already reported
    by its own finding.
    """
    if 'shape' not in config:
        bound: set[str] | None = None
        if simulate_source is not None:
            try:
                bound = _module_scope_bindings(ast.parse(simulate_source))
            except SyntaxError:
                bound = None
        # A nested `shape:` is claimed when its value is a declared shape word OR A NEAR MISS of
        # one. Membership alone let `agent: {shape: pipelne}` through in silence — the likelier
        # mistake of the two, and the one where the author really did mean a dispatch — while a
        # mere `in` widened to anything would claim `shape: rectangular`, which is the ordinary
        # English word describing a payload or a tensor and is nobody's dispatch declaration.
        # Same near-miss rule, same `difflib` cutoff, as `_check_root_keys` one screen up.
        nested = sorted(
            key for key, value in config.items()
            if isinstance(value, dict) and isinstance(value.get('shape'), str)
            and _reads_as_a_shape_word(value['shape'])
        )
        if nested:
            # SEVERITY keyed on AGREEMENT, not on which key holds the stray declaration: an
            # undeclared bench is driven as `_UNDECLARED_SHAPE`, so a nested value naming that
            # same shape over a harness that binds its hook describes what already happens —
            # inert, worth saying, not worth blocking a bench the runner drives correctly. Any
            # other nested value (or a harness that cannot serve it) means the author believes
            # something the run will not do, which stays a fail.
            agrees = (config[nested[0]]['shape'].strip().lower() == _UNDECLARED_SHAPE
                      and bound is not None and _SHAPE_HOOKS[_UNDECLARED_SHAPE] in bound)
            stray = (
                f'plurai.config.yaml declares `shape:` under `{nested[0]}:`, but every reader '
                f'reads only the ROOT key, so this bench is dispatched as if nothing were '
                f'declared. Move it to the root of this file (or of the workspace-root '
                f'plurai.config.yaml), beside `workspace:`/`project:`.'
            )
            # TWO CALLS, each with a CONSTANT in the severity slot — not one call with a
            # conditional expression. The severity field is still a `str`, and the only thing
            # holding it to the two words `main()` buckets is an AST sweep over this file's
            # `Finding(severity=<Constant>)` slots; a conditional is precisely the shape that
            # sweep cannot see, which is the rule `:208-217` already states for the `code` slot.
            if agrees:
                return [Finding(
                    severity='warn',
                    code=Code.SHAPE_DECLARED_NOT_AT_ROOT,
                    message=stray + (
                        ' It happens to name the shape an undeclared bench is driven as anyway, so '
                        'nothing changes today — but nothing reads it either.'),
                    skill_step='Step 8',
                )]
            return [Finding(
                severity='fail',
                code=Code.SHAPE_DECLARED_NOT_AT_ROOT,
                message=stray,
                skill_step='Step 8',
            )]
        if bound is None:
            return []
        # THE HOOK CENSUS, not a pair of hand conjuncts: which of the declarable shapes this
        # harness could be DRIVEN as. One derived set and one table below, so the four states an
        # undeclared bench can be in are exhaustive by construction — two `if`s over two hook
        # names left the both-hooks state ruled on by nobody, which is exactly how it shipped
        # reported by no deterministic gate.
        serveable = {shape for shape, hook in _SHAPE_HOOKS.items() if hook in bound}
        if serveable == set(_SHAPE_HOOKS):
            other_shape = next(s for s in _SHAPE_HOOKS if s != _UNDECLARED_SHAPE)
            return [Finding(
                severity='warn',
                code=Code.SHAPE_ABSENT_BOTH_HOOKS,
                message=(
                    f'simulate.py binds BOTH {_SHAPE_HOOKS[_UNDECLARED_SHAPE]} and '
                    f'{_SHAPE_HOOKS[other_shape]} at module scope, and plurai.config.yaml declares '
                    f'no `shape:`. The declaration is what the runner dispatches on — the hooks are '
                    f'a hint that never chooses a route — so this bench is driven as '
                    f'`{_UNDECLARED_SHAPE}`, {_SHAPE_HOOKS[other_shape]} is never called, and every '
                    f'policy scoped to it is scored against an empty result: zero verdicts with a '
                    f'clean exit code, so the run is GREEN and nothing in it says the half you meant '
                    f'to test never ran. This is a WARN and not a refusal: this harness CAN serve '
                    f'the route it is driven on, so the default is defensible and blocking a bench '
                    f'the runner drives correctly would be a false red. Say which route you mean '
                    f'with ONE ROOT key — at the root of this file, or of the workspace-root '
                    f'plurai.config.yaml the runner also reads — beside `workspace:`/`project:`: '
                    f'`shape: {_UNDECLARED_SHAPE}` to keep the route this bench takes today, or '
                    f'`shape: {other_shape}` to be driven as the other one.'
                ),
                skill_step='Step 8',
            )]
        # A harness that binds something but NOT the hook the undeclared route needs. Keyed on
        # `_UNDECLARED_SHAPE`, not on the word `pipeline`: the subject is "cannot serve the route
        # it will be driven on", which is what makes it a refusal rather than a warning.
        if serveable and _UNDECLARED_SHAPE not in serveable:
            return [Finding(
                severity='fail',
                code=Code.SHAPE_ABSENT_PIPELINE,
                message=(
                    f'simulate.py binds {_SHAPE_HOOKS["pipeline"]} and no {_SHAPE_HOOKS["dialog"]}, '
                    f'and plurai.config.yaml declares no `shape:`. The declaration is what the '
                    f'runner dispatches on — the hooks are only a hint — so this bench is driven '
                    f'as a dialog, the turn loop has no hook to call, and every scenario fails on '
                    f'turn 1 after the set has been paid for. Add ONE ROOT key — at the root of '
                    f'this file, or of the workspace-root plurai.config.yaml the runner also '
                    f'reads — beside `workspace:`/`project:`: `shape: pipeline`. If '
                    f'this bench really is a dialog whose {_SHAPE_HOOKS["dialog"]} is bound out of '
                    f'module scope, declare `shape: dialog` instead.'
                ),
                skill_step='Step 8',
            )]
        return []
    declared = config['shape']
    if not isinstance(declared, str) or declared.strip().lower() not in _SHAPE_HOOKS:
        return [Finding(
            severity='fail',
            code=Code.SHAPE_VALUE,
            message=(
                f'`shape` must be one of {sorted(_SHAPE_HOOKS)} (got {declared!r}). It names the '
                f'hook the runner drives: `dialog` → invoke_agent, `pipeline` → invoke_pipeline. '
                f'A skill-agent bench is `dialog` — it is a dialog agent at the protocol level. '
                f'A value the runner cannot read is worse than no key: the hooks are a hint that '
                f'never chooses a route, so nothing downstream reads the declaration you wrote.'
            ),
            skill_step='Step 8',
        )]
    if simulate_source is None:
        return []
    try:
        tree = ast.parse(simulate_source)
    except SyntaxError:
        # Reported by verify_simulate / the discovery findings; not this check's business.
        return []
    shape = declared.strip().lower()
    required = _SHAPE_HOOKS[shape]
    bound = _module_scope_bindings(tree)
    if required in bound:
        # Both hooks bound is NOT a contradiction — it is exactly the ambiguity the declaration
        # exists to resolve (a harness may export both; the derivation cannot tell which the
        # bench is for, the declaration can).
        return []
    other_shape = next(s for s in _SHAPE_HOOKS if s != shape)
    other_hook = _SHAPE_HOOKS[other_shape]
    also = (
        f' It binds {other_hook} instead, which is the {other_shape} shape'
        if other_hook in bound else
        f' It binds neither {required} nor {other_hook}'
    )
    return [Finding(
        severity='fail',
        code=Code.SHAPE_CONTRADICTS_HOOKS,
        message=(
            f'plurai.config.yaml declares `shape: {shape}` but simulate.py binds no {required} '
            f'at module scope.{also}. The declaration is what the runner dispatches on, so this '
            f'bench would be driven through a hook it does not export and die on its first '
            f'invocation. Fix whichever is wrong: change the declaration, or export {required} '
            f'(a module-scope `def`, an assignment or an import all count — the runner resolves '
            f'the name with getattr).'
        ),
        skill_step='Step 8',
    )]


def _check_render_actions(config: dict[str, Any]) -> list[Finding]:
    """`render_actions:` is a list of distinct, non-blank verb names — or absent.

    Fail-severity, unlike the root-key warning: a malformed declaration is read by lint_prd.py
    as "undeclared", and undeclared is the exact state this key exists to end (a surface no
    completeness check can see). `[]` is a valid declaration — "none, by declaration" — and is
    distinct from omitting the key, which says nothing.
    """
    if 'render_actions' not in config:
        return []
    actions = config['render_actions']
    if not isinstance(actions, list):
        return [Finding(
            severity='fail',
            code=Code.RENDER_ACTIONS_SHAPE,
            message=(
                f'`render_actions` must be a list of action names (got {type(actions).__name__}). lint_prd.py reads a '
                'malformed declaration as UNDECLARED, so the render surface goes back to being invisible to every check.'
            ),
            skill_step='Step 8',
        )]
    bad = [repr(a) for a in actions if not isinstance(a, str) or not a.strip()]
    # STRIPPED before the duplicate check, because that is what the consumer compares. The backend's
    # `scan_contract.render_actions.parse_render_actions` strips each verb and then refuses a
    # repeat, so `['show_table', ' show_table ']` passed here and RAISED there — the check told
    # the author their config was clean and the scan rejected it.
    verbs = [a.strip() for a in actions if isinstance(a, str) and a.strip()]
    duplicates = sorted({v for v in verbs if verbs.count(v) > 1})
    findings: list[Finding] = []
    if bad:
        findings.append(Finding(
            severity='fail',
            code=Code.RENDER_ACTIONS_SHAPE,
            message=f'`render_actions` carries non-name entries {", ".join(bad)} — each entry is the verb name the agent emits.',
            skill_step='Step 8',
        ))
    if duplicates:
        findings.append(Finding(
            severity='fail',
            code=Code.RENDER_ACTIONS_SHAPE,
            message=(f'`render_actions` lists {", ".join(duplicates)} more than once — the surface is a set of verbs. '
                     'Names are compared STRIPPED, as the backend compares them, so two entries differing only in '
                     'surrounding whitespace are one verb.'),
            skill_step='Step 8',
        ))
    return findings


def _nested_io_parents(config: dict[str, Any]) -> list[str]:
    """The root keys that carry an `io:` mapping, where the declaration is read by nobody."""
    return sorted(key for key, value in config.items() if isinstance(value, dict) and isinstance(value.get('io'), dict))


def _check_agent_io(config: dict[str, Any]) -> list[Finding]:
    """`agent.io:` declares what the agent can be HANDED — or is absent.

    Known keys and no others: ``input:``, the MESSAGE CONTENT PART kinds the agent accepts
    (``text`` / ``image_url`` / ``file``), ``may_open:``, whether it can be driven with no user
    message at all, ``opens: always``, whether production's host opens EVERY conversation, and
    ``choice_text:`` + ``choice_text_source:``, what the resume code does with an interrupt answer's words. There is deliberately no ``output:`` — ``render_actions:`` is that
    declaration already, here and in both consumers, and a second one could only disagree.

    Fail-severity for the SHAPE, for `render_actions`' reason: the setup smoke turns this
    declaration into probe turns against the real child, so a malformed one does not degrade to
    "no declaration" — it degrades to a channel nobody confirmed while the author believes it was.
    An unknown subkey is named rather than ignored, which is the whole difference between this and
    the root-key warning one level up. What the CONSUMER accepts is never refused here, though:
    a declaration the SDK reads and drives (a bare `agent.io:`, an `input:` without `text`) is at
    most a warn, because a check stricter than the consumer refuses a config that runs.

    ``shape`` is READ here, never derived — this function is not given the harness source, so it
    structurally cannot re-decide a declaration the config already makes its own. The one
    cross-check it does make is a WARN, deliberately: ``agent.io`` describes a CONVERSATION's
    message channel and a bench declaring ``shape: pipeline`` is not driven through one, so the
    block is inert there — but inert is not wrong. A harness may export both hooks, and ``shape``
    names the route THIS run takes, so a bench run as a pipeline today can carry a truthful
    declaration of the dialog route it is also driven on. The author is told (inert is exactly what
    they cannot see, and the smoke plans no probe on that bench) and is not blocked.
    """
    def _fail(code: Code, message: str) -> Finding:
        return Finding(severity='fail', code=code, message=message, skill_step='Step 8')

    if _AGENT_IO_KEY not in config:
        # `agent:\n  io: {...}` — the nesting the ROOT key's own dotted spelling invites. Nothing
        # reads it: the SDK looks up the literal key `agent.io` at the document root, and so does
        # `verify_smoke._declared_io`, which reports "NOTHING WAS DECLARED" and plans no probe. So
        # the author believes a channel is covered and nothing probes it — the exact sentence this
        # function's docstring gives for an unknown SUB-key, one rung up.
        if nested := _nested_io_parents(config):
            return [_fail(
                Code.AGENT_IO_DECLARED_NOT_AT_ROOT,
                f'`{nested[0]}: {{io: …}}` declares nothing: only the ROOT key `{_AGENT_IO_KEY}:` is read, '
                f'spelled with a literal dot and placed beside `workspace:` / `project:`, not nested under '
                f'`{nested[0]}:`. Move the block to the root of this file; an ignored declaration would run '
                f'every scenario that hands the agent a file or an image against a harness that never declared '
                f'it, and the setup smoke plans no probe for it.',
            )]
        return []

    # A BARE `agent.io:` (YAML gives the key `None`) is the defaults, declared — which is what
    # `plurai.agent_io.parse_agent_io` does with it (`if raw is None: raw = {}`), so a bench
    # whose author commented the sub-keys out runs. Refusing it here would be a check stricter
    # than the consumer, reporting `got NoneType` for a key the runner reads as `{}`.
    declared = config[_AGENT_IO_KEY]
    if declared is None:
        declared = {}
    if not isinstance(declared, dict):
        return [_fail(
            Code.AGENT_IO_SHAPE,
            f'`{_AGENT_IO_KEY}` must be a mapping with {" / ".join(f"`{k}:`" for k in _AGENT_IO_KNOWN_KEYS)} '
            f'(got {type(declared).__name__}). It declares what this agent can be HANDED — the message '
            f'content parts it accepts, and whether it can open the conversation itself.',
        )]

    findings: list[Finding] = []
    unknown = [k for k in declared if k not in _AGENT_IO_KNOWN_KEYS]
    if unknown:
        findings.append(_fail(
            Code.AGENT_IO_UNKNOWN_KEY,
            f'`{_AGENT_IO_KEY}` carries {", ".join(repr(k) for k in unknown)}, which nothing reads. '
            f'It declares exactly {", ".join(f"`{k}`" for k in _AGENT_IO_KNOWN_KEYS)} and nothing else — '
            f'the agent\'s EMITTED surface is `render_actions:` (a root key of its own), and an '
            f'`agent_context` field the agent reads is declared under `agent_context:`. A key here that '
            f'no reader knows is a channel you believe is covered and nothing probes.',
        ))

    shape = config.get('shape')
    # `.strip().lower()`, the reading `_check_shape_declaration` two screens up documents as the
    # RUNNER's own — one key read two ways in one file meant `shape: Pipeline` (legal, driven as a
    # pipeline) carried an `agent.io` block with nothing saying it was inert.
    if isinstance(shape, str) and shape.strip().lower() == 'pipeline':
        findings.append(Finding(
            severity='warn',
            code=Code.AGENT_IO_INERT_ON_PIPELINE_SHAPE,
            message=(
                f'plurai.config.yaml declares `shape: pipeline` AND `{_AGENT_IO_KEY}`, which nothing reads on '
                f'that route: a pipeline bench is one `invoke_pipeline(inputs)` call, with no turn 0 to open '
                f'and no message content parts to carry — its inputs arrive as that call\'s kwargs and are '
                f'declared by the hop\'s own signature. WARN, not a refusal: a harness may export both hooks, '
                f'and `shape` names the route THIS run takes, so a bench driven as a pipeline today can carry a '
                f'truthful declaration of the dialog route it is also driven on. Worth saying because the '
                f'setup smoke plans no probe here, so a declaration on this bench is confirmed by nothing. '
                f'If this bench is only ever a pipeline, drop `{_AGENT_IO_KEY}`; if the `shape:` is what is '
                f'wrong, fix that — the declaration is what the runner dispatches on and is never derived '
                f'from the hooks, so nothing downstream reconciles the two for you.'
            ),
            skill_step='Step 8',
        ))

    if 'input' in declared:
        parts = declared['input']
        if not isinstance(parts, list):
            findings.append(_fail(
                Code.AGENT_IO_INPUT_SHAPE,
                f'`{_AGENT_IO_KEY}.input` must be a list of part kinds from {list(_INPUT_PART_KINDS)} '
                f'(got {type(parts).__name__}).',
            ))
        else:
            bad = [repr(k) for k in parts if not isinstance(k, str) or not k.strip()]
            kinds = [k.strip() for k in parts if isinstance(k, str) and k.strip()]
            unknown_kinds = sorted({k for k in kinds if k not in _INPUT_PART_KINDS})
            duplicates = sorted({k for k in kinds if kinds.count(k) > 1})
            if bad:
                findings.append(_fail(
                    Code.AGENT_IO_INPUT_SHAPE,
                    f'`{_AGENT_IO_KEY}.input` carries non-name entries {", ".join(bad)} — each entry is one '
                    f'part kind from {list(_INPUT_PART_KINDS)}.',
                ))
            if duplicates:
                findings.append(_fail(
                    Code.AGENT_IO_INPUT_SHAPE,
                    f'`{_AGENT_IO_KEY}.input` lists {", ".join(duplicates)} more than once — the input surface is a '
                    f'SET of part kinds. Names are compared stripped.',
                ))
            if unknown_kinds:
                # An advisory gate: the SDK's `agent.io` reader drops a kind outside its vocabulary with
                # a finding, so nothing would ever send it — the declaration is wrong, not a crash.
                findings.append(_fail(
                    Code.AGENT_IO_INPUT_KIND,
                    f'`{_AGENT_IO_KEY}.input` declares {", ".join(repr(k) for k in unknown_kinds)}, which is not a part '
                    f'kind anything can send. The vocabulary is {list(_INPUT_PART_KINDS)} — OpenAI\'s content-part '
                    f'`type` values, which is what goes on the turn: `text` is the words the simulated user types, '
                    f'`image_url` a picture, `file` a document handed to the turn. The setup '
                    f'smoke sends one probe turn per declared kind through the real harness, and it has no probe for '
                    f'a kind that is not in that list — so this declaration could never be confirmed.',
                ))
            elif not bad and 'text' not in kinds:
                # WARN, not a refusal — `agent.io.input` gates CONTENT PARTS, never the string
                # turn. `plurai.child._turn_messages` says so in terms: a `str` user message is
                # "NEVER refused, whatever `agent.io.input` says … including the agent-first
                # bench, whose user still SPEAKS after turn 0", and `AgentIO.input_parts`
                # documents `()` as a declaration of its own ("none, by declaration"). So
                # `input: []`, `input: []` + `may_open: true` and `input: [image_url]` all run;
                # failing them here would refuse the agent-first bench this key exists to declare.
                findings.append(Finding(
                    severity='warn',
                    code=Code.AGENT_IO_INPUT_TEXT,
                    message=(
                        f'`{_AGENT_IO_KEY}.input` is {kinds or "empty"} and does not include `text`. The runner '
                        f'still SPEAKS to this bench — a plain string turn is never refused on a declaration '
                        f'(`plurai.child._turn_messages`), and `input:` gates only the message CONTENT PARTS — so '
                        f'nothing here dies on a paid run. What it costs is a declaration that disagrees with what '
                        f'the bench is actually handed: the setup smoke plans no `text` probe, so the one channel '
                        f'every scenario uses is confirmed by nothing. Declare `text` beside whatever else this '
                        f'agent accepts, unless this really is the agent-first bench that is spoken to and handed '
                        f'no parts.'
                    ),
                    skill_step='Step 8',
                ))

    if 'may_open' in declared and not isinstance(declared['may_open'], bool):
        findings.append(_fail(
            Code.AGENT_IO_MAY_OPEN_SHAPE,
            f'`{_AGENT_IO_KEY}.may_open` must be a YAML boolean, `true` or `false` '
            f'(got {declared["may_open"]!r}). It says whether this agent can be driven with NO user message on '
            f'turn 0 — the agent greets first. A string (`"yes"`, `"true"`) is not a boolean and the reader that '
            f'dispatches the empty opener would not see it.',
        ))
    if 'opens' in declared:
        if declared['opens'] != 'always':
            findings.append(_fail(
                Code.AGENT_IO_OPENS_SHAPE,
                f'`{_AGENT_IO_KEY}.opens` must be `always` (got {declared["opens"]!r}) — declared only when production\'s '
                f'host opens EVERY conversation (a page render, a greeting), so turn 0 is the agent\'s and a scenario\'s '
                f'first_talk is turn 1. Omit it otherwise.',
            ))
        elif declared.get('may_open') is False:
            findings.append(_fail(
                Code.AGENT_IO_OPENS_SHAPE,
                f'`{_AGENT_IO_KEY}` declares `opens: always` beside `may_open: false` — a host that always opens is driven '
                f'with an empty message list on turn 0. Drop `may_open: false`; `opens: always` implies it.',
            ))
    findings.extend(_check_choice_text(declared, _fail))
    findings.extend(_check_channels(declared, _fail))
    return findings


def _channel_kinds() -> tuple[str, ...]:
    """The closed channel-kind vocabulary, from the backend's `input_channel_kinds` block where it is reachable."""
    try:
        scripts = str(Path(__file__).resolve().parents[3] / 'scripts')
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from plurai_contracts import contract  # noqa: PLC0415 - repo script, optional at runtime

        return tuple(contract('input_channel_kinds', default={'kinds': list(_CHANNEL_KINDS_FALLBACK)})['kinds'])
    except ImportError:
        return _CHANNEL_KINDS_FALLBACK


def _check_channels(declared: dict[str, Any], fail: Callable[[Code, str], Finding]) -> list[Finding]:
    """`agent.io.channels: [{name, kind, field, source, handover?}]` — or absent. The SDK's parse
    (`plurai.agent_io._parse_channels`), refused here by name: a scenario plants into a channel BY NAME, so a
    declaration that cannot testify must never reach a run as "no such channel"."""
    if 'channels' not in declared:
        return []
    channels = declared['channels']
    if not isinstance(channels, list):
        return [fail(Code.AGENT_IO_CHANNELS_SHAPE, f'`{_AGENT_IO_KEY}.channels` must be a list of {{name, kind, field, source, handover}} entries (got {type(channels).__name__}).')]
    findings: list[Finding] = []
    seen: set[str] = set()
    kinds = _channel_kinds()
    for index, entry in enumerate(channels):
        where = f'`{_AGENT_IO_KEY}.channels[{index}]`'
        if not isinstance(entry, dict):
            findings.append(fail(Code.AGENT_IO_CHANNELS_SHAPE, f'{where} must be a mapping {{name, kind, field, source, handover}} (got {type(entry).__name__}).'))
            continue
        if unknown := sorted(str(k) for k in entry if k not in _CHANNEL_KEYS):
            findings.append(fail(Code.AGENT_IO_CHANNELS_SHAPE, f'{where} carries {unknown}, which nothing reads; a channel is exactly {list(_CHANNEL_KEYS)}.'))
        if blank := [k for k in _CHANNEL_REQUIRED if not isinstance(entry.get(k), str) or not entry[k].strip()]:
            findings.append(fail(Code.AGENT_IO_CHANNELS_SHAPE, f'{where} needs non-blank {blank}: the name scenarios plant by, the kind, the request field the agent reads it from, and the `<path>:<line>` that reads it.'))
            continue
        if entry['kind'] not in kinds:
            findings.append(fail(Code.AGENT_IO_CHANNEL_KIND, f'{where} kind {entry["kind"]!r} is not one of {list(kinds)}; a message content part (text, image, file) goes in `input:` instead.'))
        if not _CITATION.match(entry['source'].strip()):
            findings.append(fail(Code.AGENT_IO_CHANNELS_SHAPE, f'{where} source {entry["source"]!r} is not a `<path>:<line>` of the agent code that reads it.'))
        if 'handover' in entry and not isinstance(entry['handover'], str):
            findings.append(fail(Code.AGENT_IO_CHANNELS_SHAPE, f'{where} handover must be a string, got {type(entry["handover"]).__name__}.'))
        if entry['name'] in seen:
            findings.append(fail(Code.AGENT_IO_CHANNELS_SHAPE, f'{where} repeats the channel name {entry["name"]!r}; scenarios address a channel by its name.'))
        seen.add(entry['name'])
    return findings


def _check_choice_text(declared: dict[str, Any], fail: Callable[[Code, str], Finding]) -> list[Finding]:
    """`agent.io.choice_text: {<choice>: delivered|dropped}` + `choice_text_source: <path>:<line>`, or neither.

    The SDK's parse (`plurai.agent_io._parse_choice_text`), refused here by name so it never reaches a run:
    a non-empty mapping, non-blank string keys, values from the closed `_CHOICE_TEXT_VALUES`, and a
    non-blank source beside it. A source with no fact is refused too. Absent is today's behaviour.
    """
    if 'choice_text' not in declared:
        if 'choice_text_source' in declared:
            return [fail(
                Code.AGENT_IO_CHOICE_TEXT_SOURCE,
                f'`{_AGENT_IO_KEY}.choice_text_source` is set without `choice_text:` — it cites the resume code a '
                f'`choice_text` fact was read off. Declare the fact beside it, or drop both.',
            )]
        return []
    facts = declared['choice_text']
    if not isinstance(facts, dict) or not facts:
        return [fail(
            Code.AGENT_IO_CHOICE_TEXT_SHAPE,
            f'`{_AGENT_IO_KEY}.choice_text` must be a non-empty mapping of interrupt choice → one of '
            f'{list(_CHOICE_TEXT_VALUES)}, got {facts!r}. Omit the key when the resume code was not read.',
        )]
    findings: list[Finding] = []
    for choice, verdict in facts.items():
        if not isinstance(choice, str) or not choice.strip():
            findings.append(fail(
                Code.AGENT_IO_CHOICE_TEXT_SHAPE,
                f'`{_AGENT_IO_KEY}.choice_text` keys must be the non-blank choice spellings the agent offers, got {choice!r}.',
            ))
        elif verdict not in _CHOICE_TEXT_VALUES:
            findings.append(fail(
                Code.AGENT_IO_CHOICE_TEXT_SHAPE,
                f'`{_AGENT_IO_KEY}.choice_text.{choice}` must be one of {list(_CHOICE_TEXT_VALUES)}, got {verdict!r}.',
            ))
    source = declared.get('choice_text_source')
    if not isinstance(source, str) or not source.strip():
        findings.append(fail(
            Code.AGENT_IO_CHOICE_TEXT_SOURCE,
            f'`{_AGENT_IO_KEY}.choice_text_source` is required beside `choice_text:` — `<path>:<line>` of the resume '
            f'code the fact was read off, got {source!r}.',
        ))
    return findings


def _check_file_input_has_document_field(config: dict[str, Any], workdir: Path) -> list[Finding]:
    """`agent.io.input` naming a document part kind needs a `.document_fields` entry in ``workdir``.

    The handover lane reads only declared document fields, so without the entry no scenario can hand
    the declared document over. The block's SHAPE is `_check_agent_io`'s; malformed reads as nothing.
    """
    declared = config.get(_AGENT_IO_KEY)
    parts = declared.get('input') if isinstance(declared, dict) else None
    kinds = [k for k in _DOCUMENT_PART_KINDS if isinstance(parts, list) and k in {p.strip() for p in parts if isinstance(p, str)}]
    if not kinds:
        return []
    if _document_fields_declared(workdir):
        return []
    return [Finding(
        severity='fail',
        code=Code.FILE_INPUT_WITHOUT_DOCUMENT_FIELD,
        message=(
            f'`{_AGENT_IO_KEY}.input` declares {", ".join(repr(k) for k in kinds)} — a document handed to the turn — '
            f'but `{workdir}` has no `.document_fields` entry, the one trigger that has scenario generation author '
            f'that document, so no scenario can hand it over. Write the entry (`[{{field, format, brief}}]`, the '
            f'document ask in `simulation-setup/SKILL.md`), or drop {" and ".join(f"`{k}`" for k in kinds)} from '
            f'`input:` if nothing hands this agent a document.'
        ),
        skill_step='Step 8',
    )]


def _document_fields_declared(workdir: Path) -> bool:
    """Whether ``workdir`` carries a non-empty `.document_fields` list; an unreadable file reads as nothing."""
    try:
        entries = json.loads((workdir / '.document_fields').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return isinstance(entries, list) and bool(entries)


def _check_handover_declares_document_kinds(config: dict[str, Any], workdir: Path) -> list[Finding]:
    """A `.document_fields` entry hands the agent documents, so `agent.io.input` must declare `file`.

    The mirror of `_check_file_input_has_document_field`: that one refuses a declaration with nothing
    to hand over, this one refuses a handover with no declaration. The SDK drives a harness that
    declares nothing with `input: [text]` and fails any scenario whose user line carries another part
    kind as `harness_undeclared_input`.

    Stands down on a pipeline bench (its document is an `invoke_pipeline` input, with no turn to carry
    a part) and when `io:` is nested under another key (`agent_io_declared_not_at_root` names that
    cause). The block's SHAPE is `_check_agent_io`'s; a malformed `input:` reads as declaring nothing.
    """
    shape = config.get('shape')
    if (isinstance(shape, str) and shape.strip().lower() == 'pipeline') or not _document_fields_declared(workdir):
        return []
    if _AGENT_IO_KEY not in config and _nested_io_parents(config):
        return []
    declared = config.get(_AGENT_IO_KEY)
    parts = declared.get('input') if isinstance(declared, dict) else None
    kinds = {p.strip() for p in parts if isinstance(p, str)} if isinstance(parts, list) else {'text'}
    if _HANDOVER_PART_KIND in kinds:
        return []
    declares = f'{sorted(kinds)}' if _AGENT_IO_KEY in config else 'undeclared, which the SDK drives as text only'
    return [Finding(
        severity='fail',
        code=Code.HANDOVER_KIND_NOT_DECLARED,
        message=(
            f'`{workdir / ".document_fields"}` has scenario generation hand the agent documents, as `{_HANDOVER_PART_KIND}` '
            f'content parts, but `{_AGENT_IO_KEY}.input` is {declares} '
            f'and does not name `{_HANDOVER_PART_KIND}`. The SDK fails every scenario that hands over a part kind '
            f'the harness does not declare, as `harness_undeclared_input`, so none of them would run. Declare '
            f'`{_AGENT_IO_KEY}: {{input: [text, {_HANDOVER_PART_KIND}]}}` at the root of this file and have `invoke_agent` '
            f'read the part (`references/document_input.py`), or remove the `.document_fields` entry if nothing hands '
            f'this agent a document.'
        ),
        skill_step='Step 8',
    )]


def _check_input_file_channel_declared(config: dict[str, Any], workdir: Path, topology: dict[str, Any] | None) -> list[Finding]:
    """An agent that reads files the user names is handed documents, by path: `file` + `.document_fields`.

    Evidence, typed where it exists: a live map tool that is its runtime's file-read built-in
    (`host_effects.FILE_READ_BUILTINS`), or an `## Agent facts` entry in `simulation_prd.md` whose quoted
    `evidence` has the agent take a user's file (`lint_prd.input_file_capabilities`). With neither half
    declared, scenario generation authors no document and the PRD steers the user away from the channel,
    so the suite never hands the agent a file. Either half alone is the two checks above's to name.
    Stands down on a pipeline bench, where a document is an `invoke_pipeline` input.
    """
    shape = config.get('shape')
    if isinstance(shape, str) and shape.strip().lower() == 'pipeline':
        return []
    declared = config.get(_AGENT_IO_KEY)
    parts = declared.get('input') if isinstance(declared, dict) else None
    if (isinstance(parts, list) and _HANDOVER_PART_KIND in {p.strip() for p in parts if isinstance(p, str)}) or _document_fields_declared(workdir):
        return []
    _plugin_scripts_on_path('scripts', 'skills/simulation-setup/scripts')
    from host_effects import FILE_READ_BUILTINS, runtime_of  # noqa: PLC0415 - the plugin's one host-effects table
    from lint_prd import input_file_capabilities  # noqa: PLC0415 - the PRD lint owns the capability matcher

    readers = set(FILE_READ_BUILTINS.get(runtime_of(topology, config), ()))
    tools = topology.get('tools') if isinstance(topology, dict) else None
    evidence = [
        f'live file-read tool `{t["name"]}` on the map'
        for t in (tools if isinstance(tools, list) else ())
        if isinstance(t, dict) and t.get('name') in readers and t.get('status') != 'mocked'
    ]
    try:
        prd = (workdir / 'simulation_prd.md').read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        prd = ''
    evidence += [f'agent fact `{subject}` quoting an input-file capability' for subject in input_file_capabilities(prd)]
    if not evidence:
        return []
    return [Finding(
        severity='fail',
        code=Code.INPUT_FILE_CHANNEL_UNDECLARED,
        message=(
            f'The agent reads files the user names ({"; ".join(evidence)}), but `{_AGENT_IO_KEY}.input` does not name '
            f'`{_HANDOVER_PART_KIND}` and `{workdir}` has no `.document_fields` entry, so no scenario hands it a file and '
            f'the suite never tests reading or editing one. Declare the by-path handover (Step 8): `{_HANDOVER_PART_KIND}` in '
            f'`{_AGENT_IO_KEY}.input`, a `.document_fields` entry, and an `invoke_agent` that writes each handed-over file '
            f"into the scenario's `inputs/` and names its path in the message. When this agent's reads never take a "
            f"user's file (cite the line that confines them), or the operator declined document generation, overrule this "
            f'finding in `artifacts/.gate_overrules.jsonl`.'
        ),
        skill_step='Step 8',
    )]


def _check_closed_class_detectors(config: dict[str, Any]) -> list[Finding]:
    """`closed_class_detectors:` is `{subject: regex}` — or absent.

    The customer's own detector for a closed set: "these are all the order codes, and THIS is what
    an order code looks like". It is VALIDATED by the backend, never trusted — the same six gates
    run against it, and one that fires on the customer's own prose is refused with the offending
    text quoted — so the only thing decidable here is the SHAPE, and the shape has to fail closed.

    Fail-severity, for the reason `render_actions` is: both scan shells refuse a malformed
    declaration with a usage error rather than degrading to "no override", because an override the
    customer wrote and the pipeline silently dropped is the worst of the three outcomes — they
    believe a check is running and none is. Catching it here means they hear it while editing the
    config instead of from a scan that will not start.
    """
    if 'closed_class_detectors' not in config:
        return []

    def _fail(message: str) -> Finding:
        return Finding(severity='fail', code=Code.CLOSED_CLASS_DETECTORS_SHAPE, message=message, skill_step='Step 8')

    declared = config['closed_class_detectors']
    if not isinstance(declared, dict):
        return [
            _fail(
                f'`closed_class_detectors` must be a mapping of `<entity>.<field>` to a regex (got {type(declared).__name__}). '
                'Both scan shells refuse a malformed declaration outright rather than reading it as "no override".'
            )
        ]
    findings: list[Finding] = []
    for subject, pattern in declared.items():
        name = str(subject).strip()
        if not name or not isinstance(pattern, str) or not pattern.strip():
            findings.append(
                _fail(
                    f'`closed_class_detectors.{subject}` needs a non-blank `<entity>.<field>` subject and a non-blank regex — '
                    'the subject is what joins this declaration to the `closed_set` fact in the PRD.'
                )
            )
            continue
        try:
            re.compile(pattern)
        except re.error as exc:
            findings.append(_fail(f'`closed_class_detectors.{name}` is not a valid regex ({exc}): {pattern!r}.'))
        if '.' not in name:
            findings.append(Finding(
                severity='warn',
                code=Code.CLOSED_CLASS_DETECTORS_SUBJECT,
                message=(
                    f'`closed_class_detectors.{name}` does not look like an `<entity>.<field>` subject. It is matched against a '
                    '`closed_set` fact\'s `subject:` in simulation_prd.md; a name that matches none is silently never applied.'
                ),
                skill_step='Step 8',
            ))
    return findings


def _check_knowledge_block(config: dict[str, Any]) -> list[Finding]:
    """Type-check the OPTIONAL ``knowledge:`` block when present.

    **Nothing in the pipeline reads this block.** The backend never receives it as
    configuration and the SDK never looks it up: setting ``prompt_inlined: true``
    configures NOTHING — it does not inline anything, does not build a corpus, does not
    change generation. It is a RECORD, written for humans and for the skills that read the
    config while deciding what to do: Step 9b (does this agent's knowledge form a closed,
    enumerable doc set that owes a VERBATIM SOURCE APPENDIX?) and ``/build-rag-corpus``
    Step 1 (is prompt-inlined knowledge in play, so a corpus is still warranted with no
    retrieval tool in sight?). Both treat prompt-inlined knowledge as first-class; a note in a YAML comment would
    be unreadable to a skill, which is why the declaration is a key.

    Shape: ``{prompt_inlined: bool, closed_doc_set: [paths], notes: str}``. Warn-severity —
    and NOT for ``unknown_root_key``'s reason, which is a refusal. The
    distinction is which half is wrong: the ROOT key is unreadable, so the declaration never
    happens at all, while ``knowledge`` IS read and only its inner shape is off — a string
    ``"true"`` or a doc set given as one comma-joined string is a record the skills will
    misread, which is worth flagging and cheap to fix, but it is not a setting that silently
    does nothing.
    """
    block = config.get('knowledge')
    if block is None:
        return []

    def _warn(message: str) -> Finding:
        """One record defect. Every one of them carries the same "nothing reads this" note, so
        an operator fixing the warning never concludes that fixing it CONFIGURES something."""
        return Finding(
            severity='warn',
            code=Code.KNOWLEDGE_BLOCK_INVALID,
            message=(
                f'{message} (`knowledge:` is a RECORD for humans + the skills — neither the '
                f'backend nor the SDK reads it, so nothing is configured by it either way.)'
            ),
            skill_step='Step 9b',
        )

    if not isinstance(block, dict):
        return [_warn(f'knowledge must be a mapping of {{prompt_inlined, closed_doc_set, notes}}, got {type(block).__name__}.')]

    findings: list[Finding] = []
    prompt_inlined = block.get('prompt_inlined')
    if prompt_inlined is not None and not isinstance(prompt_inlined, bool):
        findings.append(_warn(
            f'knowledge.prompt_inlined must be true or false, got '
            f'{type(prompt_inlined).__name__} ({prompt_inlined!r}) — a quoted "true" is a '
            f'string, and a skill reading this record would take it at face value.'
        ))

    docs = block.get('closed_doc_set')
    if docs is not None:
        if not isinstance(docs, list):
            findings.append(_warn(
                f'knowledge.closed_doc_set must be a list of document paths, got '
                f'{type(docs).__name__}. One comma-joined string reads as a single path.'
            ))
        else:
            findings.extend(
                _warn(f'knowledge.closed_doc_set[{i}] must be a non-empty string path, got {type(doc).__name__} ({doc!r}).')
                for i, doc in enumerate(docs)
                if not isinstance(doc, str) or not doc.strip()
            )

    notes = block.get('notes')
    if notes is not None and not isinstance(notes, str):
        findings.append(_warn(f'knowledge.notes must be a string, got {type(notes).__name__}.'))
    return findings


def _tool_writes_to_is_non_null(tool_entry: dict[str, Any]) -> bool:
    """A tool is a 'write-tool' when ``descriptor.writes_to`` is non-null/empty.

    Read-only tools (``writes_to: null``) don't need ``post_conditions`` —
    they don't change state. Write-tools do.
    """
    writes_to = _descriptor_block(tool_entry).get('writes_to')
    if writes_to is None:
        return False
    if isinstance(writes_to, str) and writes_to.strip().lower() in {'', 'none', 'null'}:
        return False
    return True


def _check_descriptor_schema(config: dict[str, Any]) -> list[Finding]:
    """Validate every ``tools.*.descriptor`` against ``ToolDescriptor``.

    The Pydantic schema is the single source of truth for descriptor
    grammar — the same types power the backend mock generator. Validating
    here catches typos (e.g., ``change_too`` for ``change``,
    ``set_too`` for ``set_to``) at generation time rather than letting
    them reach the backend, where the mismatch silently produces a
    state-incoherent mock.

    When ``plurai-sdk`` isn't importable (broken install, stale version
    without ``descriptor_schema``, missing pydantic), the function emits
    a warn-severity ``descriptor_schema_check_skipped`` finding rather
    than silently returning ``[]`` — a green checkmark for a check that
    never ran is exactly the failure class the schema exists to prevent.
    The cross-field invariant in ``_check_write_tools_have_post_conditions``
    still fires regardless, so basic write-tool coverage remains.
    """
    if ToolDescriptor is None:
        return [Finding(
            severity='warn',
            code=Code.DESCRIPTOR_SCHEMA_CHECK_SKIPPED,
            message=(
                'plurai-sdk is not importable in this venv; the descriptor '
                'shape check (ToolDescriptor.model_validate) did not run. '
                'Install/upgrade plurai-sdk so typos like change_too/set_too '
                'are caught here rather than at the backend.'
            ),
            skill_step='Step 8',
        )]
    findings: list[Finding] = []
    for tool_name, tool_cfg in _mapping_block(config, 'tools').items():
        if not isinstance(tool_cfg, dict):
            continue
        descriptor = tool_cfg.get('descriptor')
        if descriptor is None:
            continue
        try:
            ToolDescriptor.model_validate(descriptor)
        except ValidationError as exc:
            errs = exc.errors()
            shown = errs[:3]
            suffix = f' (and {len(errs) - 3} more)' if len(errs) > 3 else ''
            findings.append(Finding(
                severity='fail',
                code=Code.DESCRIPTOR_SCHEMA_INVALID,
                message=(
                    f'tool {tool_name!r} descriptor does not match the '
                    f'ToolDescriptor schema (`from plurai import ToolDescriptor`; '
                    f'canonically plurai_mockenv.descriptor_schema). '
                    f'Backend mock generator will reject this shape. '
                    f'Details: {shown!r}{suffix}'
                ),
                skill_step='Step 8',
            ))
    return findings


#: A mocked tool's `returns.type` vocabulary and `returns.type_source` values, as this repo spelled
#: them before the `descriptor_return_types` contract block. The fallback when the vendored payload
#: does not carry it (an older backend), or when this file runs copied out of the plugin.
_RETURN_TYPE_FALLBACK = {
    'types': ['str', 'json', 'json[list]', 'json[dict]', 'dict', 'list', 'bool', 'int', 'float', 'null', 'unknown'],
    'type_sources': ['plugin', 'code', 'annotation', 'trace', 'declared', 'stub_run', 'inferred', 'declared_unknown'],
}


def _return_type_vocabulary() -> tuple[frozenset[str], frozenset[str]]:
    """(types, type_sources), READ from the backend's `descriptor_return_types` block where it is reachable."""
    served: Any = _RETURN_TYPE_FALLBACK
    try:
        scripts = str(Path(__file__).resolve().parents[3] / 'scripts')
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from plurai_contracts import contract  # noqa: PLC0415 - repo script, optional at runtime

        served = contract('descriptor_return_types', default=_RETURN_TYPE_FALLBACK)
    except ImportError:
        pass
    return frozenset(served['types']), frozenset(served['type_sources'])


#: The one remedy for an untyped return, by what the tool declares. Only a tool whose declared
#: `original_module` is Python source has a script that reads its code.
_RETURN_TYPE_REMEDY = {
    'python': (
        'Derive it from the tool\'s code, never by guess: "$PLURAI_PY" '
        '"${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/derive_tool_facts.py" plurai_sim/plurai.config.yaml '
        '--root <the agent repo root> --write. Then regenerate test_mock_contract.py (its _RETURN_TYPES) and '
        'regenerate the mock env, both built from the old type.'
    ),
    'hand_typed': (
        'Type it from the best evidence, never by guess: a return annotation is `type_source: annotation` with '
        '`evidence: [<file>:<line>]`; the tool\'s own declaration on disk is `type_source: declared` with its file:line; '
        'your reading of the code is `type_source: inferred` with the lines you read; a tool nothing types is '
        '`type: unknown`, `type_source: declared_unknown`, with its `unknown_reason`. Then regenerate the mock env, built from the old type.'
    ),
}


def _python_source_bases(config_path: Path) -> list[Path]:
    """The roots a tool's module may resolve under: the config's folder and its parent (the two rungs the
    SDK reads a config from), and — inside a git checkout — every folder up to its toplevel, since the
    agent's repo root (derive_tool_facts.py's `--root`) is one of them. Never above the repo: a stray
    `tools.py` in a home directory is not the agent's module."""
    here = config_path.resolve().parent
    rungs = [here, here.parent]
    top = next((d for d in (here, *here.parents) if (d / '.git').exists()), None)
    if top is None:
        return rungs
    return [d for d in (here, *here.parents) if d == top or top in d.parents]


@functools.cache
def _derive_module() -> Any:
    """derive_tool_facts.py, loaded by path so the checks and the script share ONE reading (its module resolution, its
    row sources); None when this file runs copied out of the plugin."""
    import importlib.util  # noqa: PLC0415

    path = Path(__file__).resolve().parents[2] / 'simulation-setup-python' / 'scripts' / 'derive_tool_facts.py'
    spec = importlib.util.spec_from_file_location('_derive_tool_facts_resolver', path) if path.is_file() else None
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _derive_module_file() -> Callable[[Path, str], tuple[Path, Path] | None] | None:
    """derive_tool_facts.py's own `_module_file(root, dotted)`; None when this file runs copied out of the plugin."""
    module = _derive_module()
    return None if module is None else module._module_file


def _declares_python_source(descriptor: dict[str, Any], bases: list[Path]) -> bool:
    """derive_tool_facts.py resolves the tool's declared `original_module` to Python source under one of
    `bases`. Without the script there is no derive remedy to offer."""
    module = descriptor.get('original_module')
    module_file = _derive_module_file() if isinstance(module, str) and module else None
    if module_file is None:
        return False
    parent = module.rpartition('.')[0]

    def spelled_with_its_class(base: Path) -> bool:  # `<module>.<Class>` beside a method, which the script reads as `<module>`
        return bool(parent) and module_file(base, parent) is not None and _derive_module().located(base, '', descriptor)[3] is not None

    try:
        return any(module_file(base, module) is not None or spelled_with_its_class(base) for base in bases)
    except RuntimeError:  # a PLURAI_CHILD_PYTHON that cannot report its path; the smoke names that
        return False


def _split_outside_brackets(text: str, sep: str) -> list[str]:
    """`text` split on `sep` outside brackets: `Union[dict[str, int], None]` has two members."""
    parts, depth, cur = [], 0, ''
    for ch in text:
        if ch == sep and depth == 0:
            parts.append(cur)
            cur = ''
            continue
        depth += (ch == '[') - (ch == ']')
        cur += ch
    return [p.strip() for p in [*parts, cur] if p.strip()]


#: Python's spellings of `null`, as a return annotation writes them.
_NULL_SPELLINGS = frozenset({'None', 'NoneType'})
#: The container kinds whose subscript (`list[dict]`, `Dict[str, Any]`) only narrows the element: the
#: vocabulary types the container, and `returns.item_fields` / `fields` carry the element's shape.
_SUBSCRIPTED_CONTAINERS = frozenset({'list', 'dict'})


def _return_type_members(declared: Any, types: frozenset[str]) -> list[str]:
    """`returns.type` as its vocabulary members, NORMALIZED ON READ.

    The same reading the mock-contract template's `_content_check` applies (so the two never
    disagree): `Optional[T]` is `T | null`, `Union[a, b]` is `a | b`, `None` is `null`, and a
    subscripted `list[...]` / `dict[...]` is the container. `list[dict]` is the spelling the example
    config taught before the vocabulary existed and still teaches for real-mode tools; it means
    `list`. A member that normalizes to nothing in the vocabulary is kept as written, so the refusal
    names what the config says.
    """
    if not isinstance(declared, str):
        return []
    text = declared.strip()
    for wrapper, extra in (('Optional[', ['null']), ('Union[', [])):
        if text.startswith(wrapper) and text.endswith(']'):
            raw = _split_outside_brackets(text[len(wrapper):-1], ',') + extra
            break
    else:
        raw = _split_outside_brackets(text, '|')
    members = []
    for member in raw:
        base = member.split('[', 1)[0].strip()
        if member in _NULL_SPELLINGS:
            member = 'null'
        elif member not in types and base.lower() in _SUBSCRIPTED_CONTAINERS and base.lower() in types:
            member = base.lower()
        members.append(member)
    return members


def _predates_return_provenance(config: dict[str, Any]) -> bool:
    """No mocked tool in the config declares `returns.type_source`.

    Provenance is a whole-config contract: the scan writes it on every mocked tool at once
    (`derive_tool_facts.py --write`, or the hand-typed recipe). A config where NO tool carries it
    was written before the contract existed — legacy work that ran under the old reading — while a
    config where some tools carry it and one does not has a gap the scan left, and that is refused.
    """
    for entry in _mapping_block(config, 'tools').values():
        if isinstance(entry, dict) and entry.get('mode') == 'mock':
            returns = _descriptor_block(entry).get('returns')
            if isinstance(returns, dict) and 'type_source' in returns:
                return False
    return True


#: What a legacy config's finding adds: why it is a warning, and that nothing already built is voided.
_RETURN_TYPE_LEGACY = (
    'No mocked tool in this config declares `returns.type_source`, so it is read as a config that predates '
    'return-type provenance: read as before and not refused. If this scan just wrote it, type every tool. To type it now'
)
#: The union the scan wrote by GUESS before return types were derived from code (a name prefix gave
#: `list`, everything else this). It is the placeholder the provenance contract exists to end, so a
#: legacy config's message does not excuse it: the relief covers a real type written without its
#: provenance, never a guess.
_GUESSED_RETURN_TYPE = frozenset({'str', 'dict'})


#: Annotation containers the read above does not accept, and the vocabulary token each names. Used only to
#: name the token in the refusal, through the ONE normaliser (:func:`_return_type_members`).
_ALIAS_TOKENS = {'Sequence': 'list', 'Tuple': 'list', 'tuple': 'list', 'Iterable': 'list', 'Mapping': 'dict'}
_ANNOTATION_ALIASES = re.compile(r'\b(' + '|'.join(_ALIAS_TOKENS) + r')\b(?:\[[^\]]*\])?')


def _check_descriptor_return_types(config: dict[str, Any], source_bases: list[Path]) -> list[Finding]:
    """A mocked tool's `returns.type` is a vocabulary type (or a `|` union of them) with provenance.

    `type_source: code` / `annotation` carries `evidence`; `declared_unknown` is `type: unknown` plus an
    `unknown_reason`. A type written with none of that is a guess — the placeholder class a name-prefix
    or a `str | dict` default produces — and env-gen's contract loop then drives the mock toward it.
    The remedy is decided per tool (:data:`_RETURN_TYPE_REMEDY`, :func:`_declares_python_source`).

    The type is read through :func:`_return_type_members`, so an equivalent older spelling
    (`list[dict]`, `dict | None`, `Optional[dict]`) is that type, never a finding. Every case is a WARN:
    env-gen still builds the mock, toward the declared type. A config that predates provenance altogether
    (:func:`_predates_return_provenance`) is told so for the missing provenance ALONE, with the line that
    types it; a type outside the vocabulary, a missing type, and the `str | dict` guess
    (:data:`_GUESSED_RETURN_TYPE`) carry no such note on any config.
    """
    types, sources = _return_type_vocabulary()
    legacy = _predates_return_provenance(config)
    findings: list[Finding] = []
    for name, entry in _mapping_block(config, 'tools').items():
        if not isinstance(entry, dict) or entry.get('mode') != 'mock':
            continue
        descriptor = _descriptor_block(entry)
        returns = descriptor.get('returns')
        returns = returns if isinstance(returns, dict) else {}
        declared, source = returns.get('type'), returns.get('type_source')
        members = _return_type_members(declared, types)
        why = None
        relieved = False
        if not members or any(m not in types for m in members) or ('unknown' in members and len(members) > 1):
            why = f'returns.type {declared!r} is not a vocabulary type ({", ".join(sorted(types))}; `|` joins a union)'
            spelled = _return_type_members(_ANNOTATION_ALIASES.sub(lambda m: _ALIAS_TOKENS[m.group(1)], str(declared)), types)
            if spelled and all(m in types for m in spelled) and spelled != members:
                why += f' — write `{" | ".join(dict.fromkeys(spelled))}`'
        elif source not in sources:
            why = f'returns.type {declared!r} has no returns.type_source ({", ".join(sorted(sources))})'
            # The ONE reason a legacy config's message names: a vocabulary type, no guess, written before
            # provenance existed. Every other reason carries no legacy note on any config.
            relieved = legacy and source is None and frozenset(members) != _GUESSED_RETURN_TYPE
        elif (source == 'declared_unknown') != (members == ['unknown']):
            why = f'type_source {source!r} does not match returns.type {declared!r} (declared_unknown is exactly `type: unknown`)'
        elif source == 'declared_unknown' and not returns.get('unknown_reason'):
            why = 'returns.type unknown carries no returns.unknown_reason'
        elif source != 'declared_unknown' and not returns.get('evidence'):
            why = f'returns.type {declared!r} (type_source {source}) carries no returns.evidence'
        elif source == 'declared_unknown' and (returns.get('fields') or returns.get('item_fields')):
            # A shape was read, so it has a source: written as "nothing could type it", it reaches env-gen as no evidence at all.
            findings.append(Finding(
                severity='warn', code=Code.DESCRIPTOR_RETURNS_TYPE_UNTYPED, skill_step='Step 8',
                message=(f'tool {name!r}: returns is `type_source: declared_unknown` but carries '
                         f'{"`fields`" if returns.get("fields") else "`item_fields`"}. A shape read from the tool\'s own declaration is '
                         '`type_source: declared` with that file:line as `evidence`; one read from the code is `inferred` with the lines read. '
                         'declared_unknown carries no fields. When derive_tool_facts says it found no source, correct `original_module` or its `--root` first: '
                         'then the code types the tool, and no written source has to.'),
            ))
        if why:
            derives = _declares_python_source(descriptor, source_bases)
            remedy = _RETURN_TYPE_REMEDY['python' if derives else 'hand_typed']
            if relieved:
                line = '' if derives else f' — e.g. `returns: {{type: {" | ".join(members)}, type_source: annotation, evidence: ["<file>:<line>"]}}`'
                remedy = f'{_RETURN_TYPE_LEGACY}{line}: {remedy}'
            message = f'tool {name!r}: {why}. {remedy}'
            findings.append(Finding(severity='warn', code=Code.DESCRIPTOR_RETURNS_TYPE_UNTYPED, message=message, skill_step='Step 8'))
    return findings


def _check_field_presence_on_nullable_args(config: dict[str, Any]) -> list[Finding]:
    """A `{field: a, must_be_present: true}` rule on an arg declared `nullable: true`: the real tool
    accepts `None` there, so a mock honouring the rule refuses a call production accepts."""
    findings: list[Finding] = []
    for name, entry in _mapping_block(config, 'tools').items():
        if not isinstance(entry, dict):
            continue
        descriptor = _descriptor_block(entry)
        args = descriptor.get('args') if isinstance(descriptor.get('args'), dict) else {}
        pre = descriptor.get('pre_conditions') if isinstance(descriptor.get('pre_conditions'), list) else []
        for rule in pre:
            field = rule.get('field') if isinstance(rule, dict) and rule.get('must_be_present') else None
            spec = args.get(field) if isinstance(field, str) else None
            if isinstance(spec, dict) and spec.get('nullable') is True:
                findings.append(Finding(
                    severity='fail',
                    code=Code.FIELD_PRESENCE_ON_NULLABLE_ARG,
                    message=f'tool {name!r}: arg {field!r} is nullable, so `must_be_present` refuses a call the real tool accepts. Drop the rule.',
                    skill_step='Step 8',
                ))
    return findings


def _check_write_tools_have_post_conditions(config: dict[str, Any]) -> list[Finding]:
    """Every write-tool's descriptor MUST declare ``post_conditions``.

    Backend mock generator reads ``post_conditions`` to know what state
    changes the rich mock should produce. Vague or missing → useless mock.
    """
    findings: list[Finding] = []
    tools = _mapping_block(config, 'tools')
    for name, entry in tools.items():
        if not isinstance(entry, dict):
            continue
        if not _tool_writes_to_is_non_null(entry):
            continue
        if entry.get('mode') == 'real' and _resolve_write_strategy(entry) == 'real':
            # A live writer: no mock is built for it and it changes no simulated world.
            continue
        post = _descriptor_block(entry).get('post_conditions')
        if not post:
            findings.append(Finding(
                severity='fail',
                code=Code.WRITE_TOOL_MISSING_POST_CONDITIONS,
                message=(
                    f'tool {name!r}: writes_to is non-null but descriptor has '
                    f'no post_conditions. Backend mock generator needs structured '
                    f'state transitions to model the write — add at least one '
                    f'entry under post_conditions describing what changes '
                    f'(e.g., {{entity_type: X, count_change: "+1"}}).'
                ),
                skill_step='Step 8',
            ))
    return findings


#: A store is real or simulated as a WHOLE: a split with a real reader is hard, and a `read_through` overlay is
#: no cure for it. Remedy 1 is the default; remedy 2 only for a store `world_state.confirmation` names.
_SPLIT_NOTE = (
    'Fix it, in order: (1) mock the store as a whole: every tool that reads or writes it `mode: mock` (or served through '
    'the seam mock that `serves:` it), its rows seeded per scenario; (2) only for a store `world_state.confirmation` names '
    '(one the harness creates per conversation, or a disposable copy the user confirmed), run the writer for real against it '
    '(`mode: real`, `write_strategy` unset, its `writes_to` naming the confirmed target); (3) when the effect comes from a '
    'consumer the run never executes (a queue worker, a webhook), the mock\'s `post_conditions` write the outcome entities '
    'and the reader reads through them. A `read_through` overlay does not resolve it.'
)
#: The seam arm with no real reader: the agent's own code reads that store, so it may not see the write. A note to resolve.
_SEAM_UNREAD_NOTE = (
    'Declare who reads it back (`tool_graph`), or mock the agent\'s reads of that store too; a send-only sink (a message, a '
    'fax) that nothing reads back is resolved by an overrule stating so (`scripts/gate_ledger.py overrule`).'
)


def _split_lead(config: dict[str, Any], writer: str) -> str:
    """What opens a split on `writer` whose writes land in a confirmed store: run it for real, no grant."""
    _plugin_scripts_on_path('scripts')
    import writer_readers  # noqa: PLC0415 - the ONE reading of the local store

    if writer not in writer_readers.local_store_writers(config):
        return ''
    return f'{writer} writes only a confirmed store (`world_state.confirmation`), so its write is not a live write: run it for real (remedy 2). '


def _check_writer_readers(config: dict[str, Any]) -> list[Finding]:
    """Every mocked write tool names who reads back what it writes, and each reader sits on the writer's side of the mock.

    The declaration is `tool_graph` (`scripts/writer_readers.py`). A read on the other side of the mock than the write
    never sees it: every read-after-write in the run reads pre-write state. A store is real or simulated as a whole
   , so a split with a real reader is a hard `fail` (:data:`_SPLIT_NOTE`), and a reader that declares
    `read_through` counts as real here. A `client` / `entity` seam whose store no tool reads at all is an advisory `fail`
    (`writer_seam_store_unread`). A mocked writer that names no reader WARNS (a coverage note, never held).
    """
    _plugin_scripts_on_path('scripts')
    import writer_readers  # noqa: PLC0415 - the ONE reading of the relation, shared with bench_manifest.py

    edge_splits = writer_readers.mode_splits(config, through_is_real=True)
    findings = [
        Finding(
            severity='fail',
            code=Code.WRITER_READER_MODE_SPLIT,
            message=(
                f'tool_graph: {edge.writer} writes {landed} and {edge.reader} reads {looked}'
                f'{f" ({edge.entity})" if edge.entity else ""}, so a read after the write may not see it. '
                f'{_split_lead(config, edge.writer)}{_SPLIT_NOTE}'
            ),
            skill_step='Step 6',
        )
        for edge, landed, looked in edge_splits
    ]
    on_edges = {(edge.writer, edge.reader) for edge, _, _ in edge_splits}
    for writer, store, real in writer_readers.store_splits(config, through_is_real=True):
        unreported = [r for r in real if (writer, r) not in on_edges]
        if real and not unreported:
            continue
        if unreported:
            findings.append(Finding(severity='fail', code=Code.WRITER_READER_MODE_SPLIT, skill_step='Step 6', message=(
                f'{writer} writes `{store}` into the mock world and {", ".join(unreported)} read(s) that store for real, so a '
                f'read after the write may not see it. {_split_lead(config, writer)}{_SPLIT_NOTE}')))
            continue
        seam = writer_readers.seam(_mapping_block(config, 'tools')[writer])
        findings.append(Finding(severity='fail', code=Code.WRITER_SEAM_STORE_UNREAD, skill_step='Step 6', message=(
            f'{writer} writes `{store}` into the mock world and no mock-world tool reads it, though its `seam: {seam}` stands in '
            f'for a store the agent\'s own code reads through (a `target: null` edge does not change where the reads go). '
            f'{_SEAM_UNREAD_NOTE}')))
    if undeclared := writer_readers.undeclared_writers(config):
        only_writers = [f'{w} (→ {", ".join(targets)})' for w, targets in undeclared.items() if targets]
        findings.append(Finding(
            severity='warn',
            code=Code.WRITE_TOOL_READER_UNDECLARED,
            message=(
                f'{len(undeclared)} mocked write tool(s) name no reader on `tool_graph`: {", ".join(undeclared)}. A coverage note, '
                'never a stop: their read-your-writes pairs go unwitnessed. Where the reader is obvious, declare it in one step — '
                '`{source: <write tool>, target: <tool that reads what it writes>, shared_entity, relationship}`, the agent\'s real '
                'tools included, or `{source: <write tool>, target: null, relationship: <why nothing reads it back>}`; else proceed.'
                + (f' Its edges point only to other writers: {"; ".join(only_writers)}.' if only_writers else '')
            ),
            skill_step='Step 6',
        ))
    return findings


def _check_store_confirmation(config: dict[str, Any], workdir: Path, topology: dict[str, Any] | None) -> list[Finding]:
    """Writes AND reads of a store run live only on the user's confirmation that names it.

    `world_state.confirmation` must be well-formed (`world_confirmation_invalid`); a `mode: real` tool that reads a record
    store no confirmed `target` is named in fails (`real_read_store_unconfirmed`: mock it, its rows seeded per scenario),
    corpus tools aside, though a store question naming a corpus endpoint variable must name them (`live_read_unnamed`) and one
    over the records of a simulated type is part of that store (`index_over_simulated_records`); and
    a config whose tools name stores must have swept their endpoints (`endpoints_undeclared`), so the harness can pin each
    to the mock and an unmocked path fails closed. All are hard.
    """
    _plugin_scripts_on_path('scripts')
    import writer_readers  # noqa: PLC0415 - the ONE reading of what a confirmation covers

    findings = [
        Finding(severity='fail', code=Code.WORLD_CONFIRMATION_INVALID, skill_step='Step 2b', message=(
            f'world_state.confirmation {error}: it confirms nothing, so every store stays mocked. Each entry is '
            '`{answer: harness_created | disposable_copy | agent_bundled, target: <the store>}`, recorded from the scan\'s store '
            'question, or `{answer: agent_fake, target: "fake: <path>", ...}` for a computing vendor double the agent ships.'))
        for error in writer_readers.confirmation_errors(config)
    ]
    answers = writer_readers.recorded_answers(workdir)
    if unbacked := writer_readers.unbacked_disposable_copies(config, answers):
        findings.append(Finding(severity='fail', code=Code.WORLD_CONFIRMATION_INVALID, skill_step='Step 2b', message=(
            f'world_state.confirmation records `disposable_copy` for {", ".join(unbacked)}, but no recorded user answer backs it: a '
            'confirmation is written only from the user\'s "Yes — run it live" to that store\'s own question, never inferred (a map '
            'approval, a blocker answer or a partial Yes such as "run some live" confirms no store) and never from a Yes the '
            'question marked (Recommended): the simulate option is always the recommended one. Ask the store question for each, '
            'record the entry only on a Yes, and otherwise drop it and mock that store\'s tools.')))
    findings += [
        Finding(severity='fail', code=Code.WORLD_CONFIRMATION_INVALID, skill_step='Step 2b', message=(
            f'world_state.confirmation types `bundled: {target}` as the agent\'s own files, but the map lists it as a corpus '
            'source (`grounding.sources`): documents the agent reads are its data, never bundled. Drop the entry and mock '
            'its readers (or serve them through a seam), so each scenario seeds that folder.'))
        for target in writer_readers.bundled_data_targets(config, topology)
    ]
    corpus = writer_readers.corpus_tools(workdir, topology)
    if unconfirmed := sorted({*writer_readers.unconfirmed_real_readers(config, corpus),
                              *writer_readers.corpus_source_readers(config, topology, corpus)}):
        findings.append(Finding(severity='fail', code=Code.REAL_READ_STORE_UNCONFIRMED, skill_step='Step 2b', message=(
            f'{", ".join(unconfirmed)} read a store for real that no `world_state.confirmation` target is named in. A store '
            'is simulated as a whole unless the user confirmed it: set each `mode: mock` (or serve it through its store\'s '
            'seam mock) so it reads the per-scenario seeded world, or, on the user\'s confirmation of that store, record the '
            'entry and name its `target` in the tool\'s `reads_from`.')))
    findings += [
        Finding(severity='fail', code=Code.LIVE_READ_UNNAMED, skill_step='Step 2b', message=(
            f'{", ".join(tools)} (the live corpus reads the map types on `{var}`) read live through the endpoint of a store the user '
            f'chose to simulate, and that store\'s question, which names `{var}`, does not name them: it told the user the simulation never '
            'touches that store. Re-ask it naming each as a code span, read live and read-only, beside the simulated records. Keep '
            'them live: reference content stays real; an index over the records that store holds is not reference content.'))
        for var, tools in writer_readers.unnamed_live_reads(config, topology, corpus, answers).items()
    ]
    if over := writer_readers.index_over_simulated(config, topology, corpus, workdir):
        findings.append(Finding(severity='fail', code=Code.INDEX_OVER_SIMULATED_RECORDS, skill_step='Step 2a', message=(
            f'{"; ".join(f"{tool} ({why})" for tool, why in sorted(over.items()))}: a search over the records of a type the run '
            'simulates is part of that store, not reference content. Type each retrieval surface in the map\'s '
            '`grounding.records_of`: the `world_state.entity_types` type whose rows its hits are, or null when no world type '
            'holds what it returns (a playbook, regulations, runbooks). Map one over a simulated type `mocked` with its store '
            '(`reads_from` that store, `returns` declared), so it answers from each scenario\'s seeded rows, and drop it from '
            '`grounding.surfaces`, `records_of` and `.knowledge_bases`.')))
    egress = config.get('egress')
    discovery = egress.get('discovery') if isinstance(egress, dict) else None
    if isinstance(discovery, dict) and discovery.get('status') == 'none_discovered' and (
            named := [n for n in writer_readers.store_tools(config) if n not in corpus]):
        findings.append(Finding(severity='fail', code=Code.ENDPOINTS_UNDECLARED, skill_step='Step 3d', message=(
            f'egress says `none_discovered`, but {len(named)} tool(s) reach a store ({", ".join(named[:6])}'
            f'{", …" if len(named) > 6 else ""}). Declare every endpoint variable a store or vendor client reads, loopback '
            'included, and each code fallback default as a host row, so the harness pins them and an unmocked path fails fast.')))
    return findings


#: Route (a)'s real engine the harness runs, said once: the declared per-conversation local store (F6,
#: `writer_readers.local_store_writers` — `world_state.owner: agent`, its types in `owned_types`, not `shared_world_ok`). A
#: write into it is not a live write: it runs for real with no grant, whatever its `write_strategy` says.
_LOCAL_STORE_CLAUSE = (
    'A store the harness creates for each conversation, or a disposable copy the user confirmed, is route (a): declare it '
    '(`world_state.owner: agent`, the types it holds in `owned_types`, a `world_state.confirmation` entry whose `target` each '
    'writer\'s `writes_to` names, a harness `reset()`), and a write into it is not a live write: it runs for real with no grant.'
)

#: The remedy for a `mode: real` writer whose store the harness owns (`world_state.owner: agent` / `external`): its own code
#: writes that store and `world_snapshot()` observes it. Outside the declared per-conversation local store an explicit
#: `write_strategy: real` would make each write live (`host_effects.live_write_tools`), gated behind a consent grant, so the
#: remedy never asks for it.
_HARNESS_OWNED_WRITE_REMEDY = (
    'type each write `world` on the map and leave `write_strategy` unset: their own code writes the store this harness owns, '
    f'and `world_snapshot()` observes the result. {_LOCAL_STORE_CLAUSE} Outside that store an explicit `real` would make each '
    'a live write that runs only on a consent grant.'
)


def _check_strategy_without_mock(config: dict[str, Any]) -> list[Finding]:
    """Tools resolve to ``write_strategy: mocked`` on a bench where no mock is built.

    ``mocked`` names the adapter env-gen generates. When no tool is mock-backed (no ``mode: mock``,
    no effective ``read_strategy: mocked``: the test that makes a run mockless), env-gen never runs
    and that adapter never exists, so the label is a false claim to every reader. One advisory
    finding per config, naming the tools; the remedy depends on who owns the world the writes land in.
    """
    tools = {name: entry for name, entry in _mapping_block(config, 'tools').items() if isinstance(entry, dict)}
    served = _served_by_seam(config)
    if any(_identity_surface_is_mocked(entry, served=name in served) for name, entry in tools.items()):
        return []
    mocked = sorted(str(name) for name, entry in tools.items() if _resolve_write_strategy(entry) == 'mocked')
    if not mocked:
        return []
    if _harness_owns_the_world(config):
        remedy = f'Declare where they land: {_HARNESS_OWNED_WRITE_REMEDY}'
    else:
        remedy = (
            'Declare where they land: nothing is mocked, so their post_conditions describe writes no simulated world will observe. '
            'Put the store under a seam mock that names them in `serves:`, or declare `write_strategy: real` on a write that really '
            'goes to the live system (it then runs only on a `.host_effects_consent` grant).'
        )
    return [Finding(
        severity='warn',
        code=Code.STRATEGY_WITHOUT_MOCK,
        message=(
            f'{len(mocked)} tool(s) resolve to `write_strategy: mocked`, but no tool on this bench is mock-backed, so no '
            f'mock adapter is generated for them: {", ".join(mocked)}. {remedy}'
        ),
        skill_step='Step 8',
    )]


def _served_by_seam(config: dict[str, Any]) -> frozenset[str]:
    """The real tools a seam mock names in `serves:` — `writer_readers.served_by_seam`, the one reading."""
    _plugin_scripts_on_path('scripts')
    import writer_readers  # noqa: PLC0415 - the ONE reading of which side a tool's reads land on

    return writer_readers.served_by_seam(config)


def _check_world_served_without_mock(config: dict[str, Any]) -> list[Finding]:
    """``world_served_without_mock``: a ``mode: real`` tool reaches a faked store with no seam mock under it.

    Every store a tool reaches is served by exactly one of: the real thing (a granted real read, or a real engine the harness
    runs per conversation), the agent's own in-tree implementation when it is the run path's only one, or a ``mode: mock`` at
    the lowest call whose arguments are plain typed values, with ``serves:`` naming the tool. The retired route — a real tool over ``read_strategy`` /
    ``write_strategy: mocked``, served by a storage class the harness writes — is decided from the config alone
    (``writer_readers.world_served_without_mock``: an effective ``read_strategy: mocked``, or an explicit
    ``write_strategy: mocked``, on a tool no mock serves). The write side keys on the explicit value only, so a harness-owned
    world's default-inferred writes are not flagged.

    One ``fail`` per config, naming the tools, and ADVISORY: it is in no hard-gate catalogue, so it holds until it is repaired
    or the plugin overrules it with the reason (a pre-collapse config at suite time; a re-scan re-routes it). The runner never
    reads ``read_strategy``, so such a config keeps running unchanged.
    """
    _plugin_scripts_on_path('scripts')
    import writer_readers  # noqa: PLC0415 - the ONE reading of which real tools a seam mock serves

    names = writer_readers.world_served_without_mock(config)
    if not names:
        return []
    real_store = (
        'declare `read_strategy: real` for a store that is real — a granted real read, the agent\'s own in-tree implementation '
        '(only when it is the run path\'s only implementation of that store, used unmodified), or a real engine the harness runs '
        'per conversation'
    )
    writes = f' Where the harness owns the world, {_HARNESS_OWNED_WRITE_REMEDY}' if _harness_owns_the_world(config) else f' {_LOCAL_STORE_CLAUSE}'
    return [Finding(
        severity='fail',
        code=Code.WORLD_SERVED_WITHOUT_MOCK,
        message=(
            f'{len(names)} `mode: real` tool(s) reach a faked store that no seam mock serves: {", ".join(names)}. The harness never '
            'implements store behaviour: put the store under a `mode: mock` entry at the lowest call whose arguments are plain typed values (`client` / `entity` / '
            '`transport`), with `serves:` naming these tools and a `serves_cite`, and drop their `reads_from` / `writes_to` '
            f'(`observation_lane: tool_trail`; `seam: tool_body` only where no seam is reachable); or {real_store}.{writes} '
            'Advisory: a config written before this rule keeps running unchanged; when no re-scan is due, overrule it with that reason.'
        ),
        skill_step='Step 6',
    )]


#: A rule's closing citation, `(source: <path>:<LINE>[-<END>])` — the span that states the rule's scope.
_RULE_SOURCE = re.compile(r'\(source:\s*([^\s:()]+):(\d+)(?:-(\d+))?\)\s*\.?\s*$')
#: An error / outcome code as a service spells one: UPPER_SNAKE with at least one underscore.
_RULE_CODE = re.compile(r'\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b')
#: A business rule that leads with its code (`CODE: condition`), the shape Step 8 writes a computed failure in.
_LEADING_RULE_CODE = re.compile(r'\s*([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\s*:')
_LITERAL_MEMBERS = re.compile(r'^\s*Literal\[(.+)\]\s*$')
#: The missing-row refusal every tool has; no request variant narrows it, so it is no variant rule.
_NOT_A_VARIANT_RULE = frozenset({'NOT_FOUND'})


class _RuleScope(NamedTuple):
    """One rule (a business rule or an error case) of a mocked tool, read against one closed set its request carries."""

    tool: str
    label: str  # `business_rules[i]` / `error_cases[i]`
    text: str
    code: str | None  # the code the rule produces: a business rule's leading code, an error case's `returns.error.code`
    axis: str  # `<entity type>.<field>`
    members: tuple[str, ...]
    named: tuple[str, ...]  # the members the rule's text names
    issue: str | None  # why the cited span does not state the scope; None when it does (or the rule is unscoped)

    @property
    def scoped(self) -> bool:
        return 0 < len(self.named) < len(self.members)

    @property
    def governs(self) -> tuple[str, ...]:
        """The members the rule applies to: the named ones only behind an accepted citation, else every one."""
        return self.named if self.scoped and self.issue is None else self.members


def _listed(value: Any) -> list[Any]:
    """A descriptor list slot as a list; any other shape is the schema check's finding, read here as empty."""
    return value if isinstance(value, list) else []


#: Ordinals, quantifiers and determiners. A closed-set value spelled like one ("first", "all", "default") is named
#: in prose only when marked as a value (quoted, backticked or after `=`): bare, it is ordinary English ("match the
#: confirmation code first"). The class cannot be closed fully; the checks reading it stay advisory.
_FUNCTION_WORDS = frozenset({
    'first', 'last', 'next', 'previous', 'all', 'any', 'none', 'other', 'default', 'each', 'every', 'some',
    'both', 'either', 'neither', 'one', 'only', 'same', 'new', 'no', 'yes',
})


def _names_value(text: str, value: str, *, prose: bool = True) -> bool:
    """Whether `text` names `value` as a whole token: no word character or joining hyphen on either side, a
    hyphen that opens an arrow (`routed-car->0`) ending the token. In `prose` a function-word value counts
    only when marked as a value."""
    token = re.escape(value)
    if prose and value.lower() in _FUNCTION_WORDS:
        return re.search(rf'([\'"`]){token}\1|=\s*{token}(?![\w-])', text, re.IGNORECASE) is not None
    return re.search(rf'(?<![\w-]){token}(?!\w|-(?!>))', text, re.IGNORECASE) is not None


def _member_spellings(members: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """Each closed-set member with the spellings text names it by: itself and, when every member shares a
    prefix up to a separator (`routed-car`, `routed-bike`), its distinguishing tail (`car`)."""
    cut = max(os.path.commonprefix(list(members)).rfind(sep) for sep in '-_./:')
    return {m: (m, m[cut + 1:]) if 0 <= cut < len(m) - 1 else (m,) for m in members}


def _named_members(text: str, members: tuple[str, ...], *, prose: bool = True) -> tuple[str, ...]:
    """The members of a closed set `text` names, by any of their spellings."""
    return tuple(m for m, spellings in _member_spellings(members).items() if any(_names_value(text, s, prose=prose) for s in spellings))


def _closed_set_members(spec: dict[str, Any]) -> list[str]:
    """A world field's closed set: its `enum:`, or the members of a `Literal[...]` type."""
    if isinstance(spec.get('enum'), list):
        return [str(m) for m in spec['enum'] if m is not None]
    literal = _LITERAL_MEMBERS.match(str(spec.get('type') or ''))
    return [m.strip().strip('\'"') for m in literal.group(1).split(',') if m.strip()] if literal else []


def _request_axes(config: dict[str, Any], args: Any) -> dict[str, tuple[str, ...]]:
    """The closed sets a tool's request carries, as `{'<type>.<field>': members}`.

    A closed set is a world field with two or more declared values, on a type no tool's `post_conditions`
    names: a type a tool creates or changes carries lifecycle values (a status), not request variants. The
    request carries it when its `descriptor.args` names two or more of the values.
    """
    changed = {
        pc.get('entity_type')
        for entry in _mapping_block(config, 'tools').values()
        for pc in _listed(_descriptor_block(entry).get('post_conditions') if isinstance(entry, dict) else None)
        if isinstance(pc, dict)
    }
    arg_text = yaml.safe_dump(args, sort_keys=False) if isinstance(args, (dict, list)) else str(args or '')
    axes: dict[str, tuple[str, ...]] = {}
    for etype, fields in _declared_world_fields(config).items():
        if etype in changed:
            continue
        for field, spec in fields.items():
            members = tuple(dict.fromkeys(_closed_set_members(spec)))
            if len(members) >= 2 and len(_named_members(arg_text, members, prose=False)) >= 2:
                axes[f'{etype}.{field}'] = members
    return axes


def _cited_scope_issue(text: str, named: tuple[str, ...], members: tuple[str, ...], codes: list[str], bases: list[Path]) -> str | None:
    """None when the rule ends `(source: path:LINE[-END])` and that span names every value in `named` and one
    of the rule's codes; otherwise why it does not. A span that names the value but not the code is a test,
    an example or a remedy hint about that variant, not the statement that the rule governs only it."""
    cite = _RULE_SOURCE.search(text)
    if cite is None:
        return 'it cites no source for that scope'
    rel, lo, hi = cite.group(1), int(cite.group(2)), int(cite.group(3) or cite.group(2))
    path = next((base / rel for base in bases if (base / rel).is_file()), None)
    if path is None:
        return f'its source {rel} is no file under the agent repo'
    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError as exc:
        return f'its source {rel} could not be read ({type(exc).__name__})'
    if not 1 <= lo <= hi <= len(lines):
        return f'{rel}:{lo}-{hi} is outside the file ({len(lines)} lines)'
    span = '\n'.join(lines[lo - 1:hi])
    in_span = _named_members(span, members, prose=False)
    missing = [v for v in named if v not in in_span]
    if missing:
        return f'{rel}:{lo}-{hi} does not name {", ".join(missing)}'
    if codes and not any(re.search(rf'\b{re.escape(c)}\b', span) for c in codes):
        return f'{rel}:{lo}-{hi} names none of the rule\'s codes ({", ".join(dict.fromkeys(codes))})'
    return None


def _rule_scopes(config: dict[str, Any], bases: list[Path]) -> list[_RuleScope]:
    """Every (rule, request closed set) pair of every mocked tool — what both rule-scope checks read."""
    scopes: list[_RuleScope] = []
    for name, entry in _mapping_block(config, 'tools').items():
        if not isinstance(entry, dict) or entry.get('mode') != 'mock':
            continue
        descriptor = _descriptor_block(entry)
        axes = _request_axes(config, descriptor.get('args'))
        if not axes:
            continue
        rules: list[tuple[str, str, str | None, list[str]]] = []
        for i, rule in enumerate(_listed(descriptor.get('business_rules'))):
            if isinstance(rule, str):
                leading = _LEADING_RULE_CODE.match(rule)
                rules.append((f'business_rules[{i}]', rule, leading.group(1) if leading else None, _RULE_CODE.findall(_RULE_SOURCE.sub('', rule))))
        for i, case in enumerate(_listed(descriptor.get('error_cases'))):
            if isinstance(case, dict) and isinstance(case.get('condition'), str):
                returns = case.get('returns') if isinstance(case.get('returns'), dict) else {}
                error = returns.get('error') if isinstance(returns.get('error'), dict) else {}
                code = error.get('code') if isinstance(error.get('code'), str) else None
                condition = case['condition']
                rules.append((f'error_cases[{i}]', condition, code, _RULE_CODE.findall(_RULE_SOURCE.sub('', condition)) + ([code] if code else [])))
        for label, text, code, codes in rules:
            for axis, members in axes.items():
                named = _named_members(_RULE_SOURCE.sub('', text), members)
                issue = _cited_scope_issue(text, named, members, codes, bases) if 0 < len(named) < len(members) else None
                scopes.append(_RuleScope(str(name), label, text, code, axis, members, named, issue))
    return scopes


def _check_row_sources_cited(config: dict[str, Any], bases: list[Path]) -> list[Finding]:
    """Each row source of a `seam: tool_body` read tool (derive_tool_facts.py `row_sources`: a library call whose result the
    tool iterates or returns) is answered for that exact line: by a `business_rules` entry whose `(source: …)` names it,
    stating which stored values or state the rows keep, or by a typed `skipped_code` entry `{cite: <line>, returns:
    every_row, evidence: <path>:<line>}` whose evidence is a line inside the called function in the installed library,
    outside the agent's tree (derive_tool_facts.py `row_source_answer`).

    A library default may narrow the rows (caldav `todos()` never returns completed tasks); the mock is written from the
    config, so a narrowing nobody wrote down ships a listing that shows rows the real tool hides. A `tool_body` seam
    already lists the body it skips in `skipped_code`, so a range or a plain line answers nothing. Advisory: one repair
    round answers each line, and the plugin may overrule it; it never blocks.
    """
    module = _derive_module()
    if module is None:
        return []
    findings: list[Finding] = []
    for name, entry in _mapping_block(config, 'tools').items():
        if not module.row_source_tool(entry):
            continue
        try:
            units = module.first_row_sources(bases, str(name), _descriptor_block(entry))
            open_ = [(u, why) for u in units if (why := module.row_source_answer(u, entry, bases)) is not None]
        except (OSError, SyntaxError, ValueError, RuntimeError):  # an unreadable source, or an interpreter that cannot report its path
            continue
        if open_:
            findings.append(Finding(
                severity='warn',
                code=Code.ROW_SOURCE_UNCITED,
                message=(
                    f'tool {name!r}: row source(s) {"; ".join(f"{u} ({why})" for u, why in open_)} produce the rows it returns, and '
                    'nothing answers them. Open the called function in the agent\'s installed library and read what it returns by '
                    'default. If it hides rows (a status, archived, deleted, an owner, a page limit), add a business rule naming '
                    'the stored values or state it keeps, ending `(source: <file>:<line>)` on that exact line; if it returns every '
                    'row, add to `skipped_code` `{cite: <file>:<line>, returns: every_row, evidence: <library file>:<line>}`, the '
                    'evidence a line inside that function in the installed library. A body-wide `skipped_code` range answers no row '
                    'source. One repair round; the plugin may overrule it.'
                ),
                skill_step='Step 6',
            ))
    return findings


def _check_rule_scope_cited(scopes: list[_RuleScope]) -> list[Finding]:
    """A rule that names some but not all values of a closed set the request carries ends `(source: path:LINE)`.

    The qualifier narrows the rule to one request variant, and the mock is built to exactly that rule; a
    qualifier the author inferred from a test family, an example or a remedy hint about that variant ships a
    mock that lets the rule's failure pass on every other variant. Advisory: the scan's own repair cites the
    span or drops the qualifier, and the plugin may overrule it.
    """
    findings: list[Finding] = []
    for s in scopes:
        if s.scoped and s.issue is not None:
            findings.append(Finding(
                severity='fail',
                code=Code.RULE_SCOPE_UNCITED,
                message=(
                    f'tool {s.tool!r} {s.label} names only {", ".join(s.named)} of {s.axis} ({", ".join(s.members)}), '
                    f'and {s.issue}: {s.text[:160]!r}. Cite the span that states this scope — end the rule with '
                    f'`(source: <path>:<LINE>[-<END>])`, a span naming {", ".join(s.named)} and the rule\'s code — or drop '
                    f'the qualifier so the rule governs every {s.axis}. A test family, example or remedy hint that '
                    f'exercises one variant is not a scope.'
                ),
                skill_step='Step 8',
            ))
    return findings


def _test_functions(tree: ast.Module) -> list[tuple[set[str], list[str]]]:
    """Each `test_*` function as (its string constants, its body text constants), docstring excluded."""
    out: list[tuple[set[str], list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_'):
            body = node.body[1:] if ast.get_docstring(node) is not None else node.body
            strings = [c.value for stmt in body for c in ast.walk(stmt) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
            out.append(({v.lower() for v in strings}, strings))
    return out


def _check_rule_tests_cover_variants(scopes: list[_RuleScope], workdir: Path) -> list[Finding]:
    """Each rule code gets a rule test in `test_mock_contract.py` on every request variant the rule governs.

    A rule test is a `test_*` function whose body (docstring aside) spells the code; the variants it exercises
    are the closed-set values it spells. Every value is governed unless `rule_scope_uncited` accepted a cited
    scope. Env-gen runs this file as the mock's acceptance test, so a variant with no rule test is one where a
    mock that never raises the code passes. A code no test spells at all is outside this check.
    """
    path = workdir / 'test_mock_contract.py'
    try:
        tests = _test_functions(ast.parse(path.read_text(encoding='utf-8'))) if path.is_file() else []
    except (OSError, SyntaxError, ValueError):
        tests = []
    if not tests:
        return []
    governed: dict[tuple[str, str, str], set[str]] = {}
    members_of: dict[str, tuple[str, ...]] = {}
    for s in scopes:
        if s.code and s.code not in _NOT_A_VARIANT_RULE and s.label.startswith('business_rules'):
            governed.setdefault((s.tool, s.code, s.axis), set()).update(s.governs)
            members_of[s.axis] = s.members
    findings: list[Finding] = []
    for (tool, code, axis), need in governed.items():
        spelled = re.compile(rf'\b{re.escape(code)}\b')
        rule_tests = [lowered for lowered, strings in tests if any(spelled.search(v) for v in strings)]
        if not rule_tests:
            continue
        covered = [m for m in members_of[axis] if any(m.lower() in lowered for lowered in rule_tests)]
        missing = [m for m in members_of[axis] if m in need and m not in covered]
        if covered and missing:
            findings.append(Finding(
                severity='fail',
                code=Code.RULE_TEST_VARIANT_MISSING,
                message=(
                    f'tool {tool!r}: rule {code} governs {axis} {", ".join(missing)}, and test_mock_contract.py\'s rule tests '
                    f'for it exercise only {", ".join(covered) or "no value"}. Add one rule test per missing value, its world '
                    f'seeded so the rule fires — or, when a source states that the rule governs only some values, cite it on '
                    f'the rule (`rule_scope_uncited`).'
                ),
                skill_step='Step 9d',
            ))
    return findings


def _check_guard_fields_tested(config: dict[str, Any], workdir: Path) -> list[Finding]:
    """Structured-return fidelity: the mock contract asserts every field a declared guard reads off a mocked tool's result.

    A `guards[]` entry with `reads: result` names the fields the agent's own guard reads (a status, an artifact list,
    a message's metadata). Env-gen runs `test_mock_contract.py` as the mock's acceptance test, so a field no test there
    spells is one a mock may drop — the guard then reads nothing and the run tests the agent without it.
    """
    path = workdir / 'test_mock_contract.py'
    try:
        tests = _test_functions(ast.parse(path.read_text(encoding='utf-8'))) if path.is_file() else []
    except (OSError, SyntaxError, ValueError):
        tests = []
    if not tests:
        return []
    tools = _mapping_block(config, 'tools')
    findings: list[Finding] = []
    for guard in _guards(config):
        if guard.get('reads') != 'result' or not isinstance(guard.get('fields'), list):
            continue
        for tool in (t for t in guard.get('watches') or [] if isinstance(tools.get(t), dict) and tools[t].get('mode', 'mock') == 'mock'):
            spells_tool = [strings for strings, _ in tests if tool.lower() in strings]
            untested = [f for f in guard['fields'] if isinstance(f, str) and not any(f.rsplit('.', 1)[-1].lower() in strings for strings in spells_tool)]
            if untested:
                findings.append(Finding(
                    severity='fail',
                    code=Code.GUARD_FIELD_UNTESTED,
                    message=(
                        f'guard {guard.get("name")!r} reads {", ".join(untested)} off `{tool}`\'s result, and no test in '
                        f'test_mock_contract.py calling `{tool}` asserts it. Add one that calls `{tool}` and asserts each field '
                        'in its return.'
                    ),
                    skill_step='Step 9d',
                ))
    return findings


#: How many mis-keyed rows a `world_fixture_key_not_id` finding names.
_FIXTURE_KEYS_SHOWN = 5


def _production_key(row: dict[str, Any], entity_type: str, id_field: str | None) -> Any:
    """The row's own id as the backend reads it (its key is ``str`` of this); ``None`` is positional."""
    declared = row.get(id_field) if id_field else None
    return declared or row.get('id') or row.get(f'{entity_type}_id') or None


def _check_fixture_world_keys(config: dict[str, Any], workdir: Path) -> list[Finding]:
    """`test_mock_contract.py`'s seeded worlds key each row as the backend keys a scenario world: by its own id.

    A world literal is any dict whose values are all row literals carrying `_entity_type`. A handle key
    (`card_1` for `card_id: 101`) fails a handler that resolves `get_entity(<id>, '<type>')` as every real
    scenario needs, and passes one that does not; a repeated key collapses two rows into one.
    """
    path = workdir / 'test_mock_contract.py'
    try:
        tree = ast.parse(path.read_text(encoding='utf-8')) if path.is_file() else None
    except (OSError, SyntaxError, ValueError):
        tree = None
    if tree is None:
        return []
    world_state = config.get('world_state')
    bodies = world_state.get('entity_types') if isinstance(world_state, dict) else None
    id_fields = {str(n): b.get('id_field') for n, b in bodies.items() if isinstance(b, dict)} if isinstance(bodies, dict) else {}
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict) or not node.values or not all(
            isinstance(v, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == '_entity_type' for k in v.keys) for v in node.values
        ):
            continue
        issues: list[str] = []
        seen: set[str] = set()
        for key_node, value in zip(node.keys, node.values):
            if not isinstance(key_node, ast.Constant):
                continue
            key = str(key_node.value)
            if key in seen:
                issues.append(f'`{key}` repeats a key')
            seen.add(key)
            row: dict[str, Any] = {}
            for k, v in zip(value.keys, value.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    try:
                        row[k.value] = ast.literal_eval(v)
                    except (ValueError, SyntaxError, TypeError):
                        row[k.value] = ...
            entity_type = row.get('_entity_type')
            if not isinstance(entity_type, str):
                continue
            id_field = id_fields.get(entity_type) if isinstance(id_fields.get(entity_type), str) else None
            if ... in (row.get(id_field) if id_field else None, row.get('id'), row.get(f'{entity_type}_id')):
                continue
            own = _production_key(row, entity_type, id_field)
            if own is None:
                continue
            via = id_field if row.get(id_field) else 'id' if row.get('id') else f'{entity_type}_id'
            if key != str(own):
                issues.append(f'`{key}` (a `{entity_type}`, `{via}` {own!r}) → `{own}`')
        if issues:
            shown = '; '.join(issues[:_FIXTURE_KEYS_SHOWN]) + (f' (+{len(issues) - _FIXTURE_KEYS_SHOWN} more)' if len(issues) > _FIXTURE_KEYS_SHOWN else '')
            findings.append(Finding(
                severity='fail',
                code=Code.WORLD_FIXTURE_KEY_NOT_ID,
                message=(
                    f'test_mock_contract.py:{node.lineno}: {len(issues)} seeded row(s) are not keyed as the backend keys a scenario '
                    f'world — {shown}. Key each row by the string of its own id (its `id_field`, else `id`, else `<type>_id`) '
                    'and keep keys distinct: see mock-contract-tests.md, "the seeded world".'
                ),
                skill_step='Step 9d',
            ))
    return findings


def _plugin_scripts_on_path(*relative: str) -> None:
    for part in relative:
        path = str(Path(__file__).resolve().parents[3] / part)
        if path not in sys.path:
            sys.path.insert(0, path)


def _check_native_tools_framework(config: dict[str, Any]) -> list[Finding]:
    """`agent.native_tools` names tools only on a framework `record_native_tools.py` records them for.

    The same predicate as the recorder (`host_effects.recorded_native_tools`): only a runtime whose
    tools no plurai lane observes (`UNOBSERVED_RUNTIME_FRAMEWORKS`, the Claude Agent SDK) gets the key.
    Anywhere else, the names make the runner report the whole tool channel unread, so the mock event
    log is discarded and every tool-trail check abstains. An empty list declares nothing to any reader.
    A non-list is `block_shape`'s."""
    agent = config.get('agent')
    names = agent.get('native_tools') if isinstance(agent, dict) else None
    if not isinstance(names, list) or not names:
        return []
    _plugin_scripts_on_path('scripts')
    from host_effects import UNOBSERVED_RUNTIME_FRAMEWORKS, framework_id  # noqa: PLC0415 - the recorder's own predicate

    declared = str(agent.get('framework') or '').strip()
    if framework_id(declared) in UNOBSERVED_RUNTIME_FRAMEWORKS:
        return []
    return [Finding(
        severity='fail',
        code=Code.NATIVE_TOOLS_WRONG_FRAMEWORK,
        message=(
            f'`agent.native_tools` is script-managed: `record_native_tools.py` writes it only for a Claude Agent SDK agent, '
            f'and this agent\'s framework is {declared or "undeclared"!r}. Here it makes the runner report the whole tool '
            f'channel unread: the mock event log is discarded and every tool-trail check abstains. Delete the '
            f'`native_tools:` line (and its managed-marker comment above it) from the `agent:` block.'
        ),
        skill_step='Step 8',
    )]


def _declared_route(workdir: Path) -> str:
    """Where the map (`artifacts/agent_topology.json`) DECLARES the tools are intercepted: `agent_host`
    when it says so, else `harness`. An undeclared or unreadable map is the harness, so it fails closed."""
    _plugin_scripts_on_path('scripts')
    from host_effects import intercepted_at_agent_host  # noqa: PLC0415 - the plugin's one reader of the declaration

    try:
        topology = json.loads((workdir / 'artifacts' / 'agent_topology.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        topology = None
    return 'agent_host' if intercepted_at_agent_host(topology) else 'harness'


def _host_route_denials(workdir: Path) -> dict[str, str] | Finding | None:
    """What the agent-host route's bring-up will deny, from the rewrite's own plan of the bundle beside
    this workdir: `{action: 'typed' | 'unproven'}`, a warn when the rewrite refuses the bundle, or None when no
    bundle is there to plan (the config's typed writers are then all this check can see)."""
    _plugin_scripts_on_path('skills/simulation-setup-agentforce/scripts')
    import rewire_agentforce as rewire  # noqa: PLC0415 - the route's one denial rule, never a second copy of it

    agent_file = rewire.located_agent_file(workdir)
    try:
        text = agent_file.read_text(encoding='utf-8') if agent_file is not None else None
    except (OSError, UnicodeDecodeError):
        text = None
    if text is None:
        return None
    try:
        typed_live = rewire.declared_host_effects(workdir)
    except rewire.RewireError:
        typed_live = frozenset()  # the typing only adds denials; `up` refuses a stale list itself
    try:
        _, plan = rewire.rewire_bundle(text, granted=rewire._granted(workdir), typed_live=typed_live)
    except rewire.RewireError as error:
        return Finding(severity='warn', code=Code.LIVE_WRITE_UNGRANTED, skill_step='Step 8',
                       message=f'the route\'s bring-up refuses this bundle, so nothing runs until it is fixed: {error}')
    return {name: 'typed' if reason == rewire.DENIED_TYPED else 'unproven' for name, reason in plan.denied}


def _harness_live_write_findings(config: dict[str, Any], workdir: Path, allowed: frozenset[str], topology: dict[str, Any] | None) -> list[Finding]:
    """No confiner stands between a harness-bound live writer and the live system: each ungranted one fails."""
    from host_effects import live_write_tools, typed_writes  # noqa: PLC0415

    return [Finding(
        severity='fail',
        code=Code.LIVE_WRITE_UNGRANTED,
        message=(f'tool {name!r} writes the live system for real and `.host_effects_consent` does not grant it, so a '
                 'scored run would write it with nobody\'s consent. Grant it at the topology question, or set `mode: mock`.'),
        skill_step='Step 8',
    ) for name in live_write_tools(config, at_agent_host=False, typed_writes=typed_writes(topology)) if name not in allowed]


def _agent_host_live_write_findings(config: dict[str, Any], workdir: Path, allowed: frozenset[str], topology: dict[str, Any] | None) -> list[Finding]:
    """The route denies an ungranted org action where the agent runs, so each warns, naming the denial:
    the config's typed writers, and every action the rewrite's plan denies as not known to only read. A copy with no
    rewrite plan beside it denies nothing, so there each ungranted writer fails, as on the harness route."""
    from host_effects import live_write_tools, typed_writes  # noqa: PLC0415

    denials = _host_route_denials(workdir)
    if isinstance(denials, Finding):
        return [denials]
    written = {name for name in live_write_tools(config, at_agent_host=True, typed_writes=typed_writes(topology)) if name not in allowed}
    if denials is None:
        # No rewrite plan beside this workdir: nothing in the simulated copy denies an ungranted writer, so each fails.
        return [Finding(
            severity='fail',
            code=Code.LIVE_WRITE_UNGRANTED,
            message=(f'tool {name!r} writes the live system from the agent\'s host, `.host_effects_consent` does not grant it, and '
                     'nothing in this simulated copy denies it. Grant it at the topology question, or set `mode: mock` and rewire '
                     'the copy\'s action to the mock.'),
            skill_step='Step 8',
        ) for name in sorted(written)]
    typed = sorted(written | {name for name, why in denials.items() if why == 'typed'})
    findings = [Finding(
        severity='warn',
        code=Code.LIVE_WRITE_UNGRANTED,
        message=(f'tool {name!r} writes the live system and `.host_effects_consent` does not grant it; the route denies it at '
                 "the agent's host (`--plan` lists it under `denied`). Grant it at the topology question to let it run."),
        skill_step='Step 8',
    ) for name in typed]
    findings += [Finding(
        severity='warn',
        code=Code.LIVE_WRITE_UNGRANTED,
        message=(f'tool {name!r} runs in the live system at the agent\'s host, is not known to only read, and '
                 '`.host_effects_consent` does not grant it; the route denies it there (`--plan` lists it under `denied`), so '
                 'a scenario that needs it shows the agent unable to take that step. Grant it at the topology question to let it run.'),
        skill_step='Step 8',
    ) for name in sorted(set(denials) - set(typed))]
    return findings


#: How an ungranted live writer is kept from acting, per DECLARED interception route.
_LIVE_WRITE_ROUTES: dict[str, Callable[[dict[str, Any], Path, frozenset[str], dict[str, Any] | None], list[Finding]]] = {
    'harness': _harness_live_write_findings,
    'agent_host': _agent_host_live_write_findings,
}


def _read_topology(workdir: Path) -> dict[str, Any] | None:
    """The map beside the config (`artifacts/agent_topology.json`), or `None` when it is absent or unreadable."""
    try:
        topology = json.loads((workdir / 'artifacts' / 'agent_topology.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return topology if isinstance(topology, dict) else None


def _exposed_in(entry: object) -> list[str]:
    """A tool entry's ``exposed_in`` scope names; ``[]`` for a missing entry or a malformed value (``tool_exposed_in_invalid`` reports that)."""
    scopes = entry.get('exposed_in') if isinstance(entry, dict) else None
    return [str(scope).strip() for scope in scopes] if isinstance(scopes, list) else []


def _check_subagent_calls(config: dict[str, Any], topology: dict[str, Any] | None) -> list[Finding]:
    """A sub-agent the agent invokes as a CALL is on its callable surface, so the map's ``subagents[].call`` names a ``tools`` key.

    ``call`` is the exact name the delegation carries on the tool trail, or ``null`` when the sub-agent is reached without
    a call (a graph edge, a router node). A ``call`` that names no ``tools`` key FAILS: the backend's surface check reads
    only ``tools`` ∪ ``render_actions``, so every delegation would be charged as an undeclared call. An entry with no
    ``call`` key at all is a map written before the field existed and WARNS, so an existing workdir keeps running; a
    fresh scan cannot omit it (``render_topology.py`` refuses the map). ``agent.sub_agents`` is read by nothing and warns.

    The tools a calling sub-agent calls itself (``subagents[].tools``) reach the backend through the staged config: each
    is a ``tools`` key whose ``exposed_in`` names the sub-agent's ``call`` (the backend's tool-scope check). One
    that does not FAILS, since scoring then cannot tell a delegation whose inner calls went unrecorded from a miss.
    """
    tools = _mapping_block(config, 'tools')
    entries = topology.get('subagents') if isinstance(topology, dict) else None
    findings: list[Finding] = []
    for entry in entries if isinstance(entries, list) else ():
        if not isinstance(entry, dict):
            continue
        name = entry.get('name')
        if 'call' not in entry:
            findings.append(Finding(
                severity='warn',
                code=Code.SUBAGENT_CALL_UNSTATED,
                message=(f'sub-agent {name!r} on the map states no `call`. When the agent invokes it as a call, write the exact '
                         'name that call carries on the tool trail as `call` and declare it as a `tools` entry; write `call: null` '
                         'when it is reached without a call.'),
                skill_step='Step 8',
            ))
        elif entry['call'] is not None and str(entry['call']) not in tools:
            findings.append(Finding(
                severity='fail',
                code=Code.SUBAGENT_CALL_UNDECLARED,
                message=(f'sub-agent {name!r} is invoked as the call {entry["call"]!r}, which is not a `tools` key, so every '
                         f'delegation to it is off the declared callable surface. Declare `tools.{entry["call"]}` under that exact '
                         'trail name, or correct `call` on the map.'),
                skill_step='Step 8',
            ))
        call, owned = entry.get('call'), entry.get('tools')
        if call is not None and isinstance(owned, list):
            unstaged = sorted(str(t) for t in owned if str(call) not in _exposed_in(tools.get(str(t))))
            if unstaged:
                findings.append(Finding(
                    severity='fail',
                    code=Code.SUBAGENT_TOOLS_UNSTAGED,
                    message=(f'sub-agent {name!r} calls {", ".join(unstaged)} itself, and the config does not say so: each must be a '
                             f'`tools` entry whose `exposed_in` lists {str(call)!r}, the sub-agent\'s call (beside any other scope that '
                             'offers it). That is how scoring tells a delegation whose inner calls went unrecorded from a miss.'),
                    skill_step='Step 8',
                ))
    if 'sub_agents' in _mapping_block(config, 'agent'):
        findings.append(Finding(
            severity='warn',
            code=Code.SUBAGENT_CALL_UNSTATED,
            message=('`agent.sub_agents` is read by nothing. Declare each delegation call as a `tools` entry under the exact name '
                     'it carries on the tool trail, and name it as that sub-agent\'s `call` on the map.'),
            skill_step='Step 8',
        ))
    return findings


def _check_agent_tools_declared(config: dict[str, Any], topology: dict[str, Any] | None) -> list[Finding]:
    """Every tool on the map is a ``tools`` key, a ``render_actions`` verb or an ``agent.native_tools`` entry.

    The map (``artifacts/agent_topology.json``) is the scan's record of the agent's tools, the one its PRD tool list is
    written from. When the mock sits BELOW them (an HTTP, egress or DB adapter; a bridged agent), the config declares
    only the mocked endpoints, and every call the agent's model makes to its own tools is charged as undeclared by the
    backend's surface check (``tools`` ∪ ``render_actions``) and is invisible to coverage. FAILS, one finding naming
    them all; the remedy is the scan's declaration of each (``mode: real``, ``observation_lane: tool_trail``).
    ``agent.native_tools`` names a runtime's built-ins no plurai lane observes, recorded by ``record_native_tools.py``.
    The CLI built-ins that recorder writes (its own predicate, ``host_effects.recorded_native_tools``, narrowed to the
    rows the CLI runs itself: ``runs: subprocess`` and not MCP-served, the confiner's reach) and that are not recorded
    yet WARN once, pointing to it, so verify reads the same whichever of the two runs first; they never go under
    ``tools:``. The agent's own live tools still FAIL with the declare remedy.
    """
    rows = topology.get('tools') if isinstance(topology, dict) else None
    named = sorted({str(t['name']) for t in rows if isinstance(t, dict) and str(t.get('name') or '').strip()}) if isinstance(rows, list) else []
    agent = _mapping_block(config, 'agent')
    native = agent.get('native_tools') if isinstance(agent.get('native_tools'), list) else []
    render = config.get('render_actions') if isinstance(config.get('render_actions'), list) else []
    surface = {*map(str, _mapping_block(config, 'tools')), *map(str, render), *map(str, native)}
    missing = [name for name in named if name not in surface]
    if not missing:
        return []
    _plugin_scripts_on_path('scripts')
    from host_effects import outcome_for, recorded_native_tools  # noqa: PLC0415 - the recorder's and the confiner's own predicates

    # A built-in: what the recorder writes, and that the CLI runs itself (`runs: subprocess`, not MCP-served) - the confiner's reach.
    recorded = set(recorded_native_tools(topology or {}, config) or ())
    builtin = {str(t['name']) for t in rows or () if isinstance(t, dict) and str(t.get('name')) in recorded
               and outcome_for(t, runtime='claude_agent_sdk') == 'confine'}
    unrecorded = [name for name in missing if name in builtin]
    undeclared = [name for name in missing if name not in builtin]
    findings = [Finding(
        severity='warn',
        code=Code.AGENT_TOOL_UNDECLARED,
        message=(f'{len(unrecorded)} built-in(s) of this Claude Agent SDK agent are not recorded yet: {", ".join(unrecorded)}. '
                 '`record_native_tools.py` records them as `agent.native_tools` (the scan runs it after setup); never declare '
                 'them under `tools:`.'),
        skill_step='Step 8',
    )] if unrecorded else []
    if undeclared:
        findings.append(Finding(
            severity='fail',
            code=Code.AGENT_TOOL_UNDECLARED,
            message=(f'{len(undeclared)} tool(s) on the map are not on the declared surface: {", ".join(undeclared)}. Every call the '
                     'agent makes to one is charged as undeclared and no coverage sees it. Declare each as `tools.<name>` under the '
                     'name the trail carries; when the mock sits below it, `mode: real` with `observation_lane: tool_trail` and no '
                     '`reads_from` / `writes_to`.'),
            skill_step='Step 8',
        ))
    return findings


def _check_map_status_matches_mode(config: dict[str, Any], topology: dict[str, Any] | None) -> list[Finding]:
    """A map tool's ``status`` agrees with its config ``mode``: ``mock`` is ``mocked``, ``real`` / ``observe`` is ``live``.

    The host-effects question lists the map's ``live`` tools, so a tool the config mocks that the map still calls
    ``live`` is offered a grant that changes nothing. WARNS, naming each tool, so the next verify catches the drift.
    """
    tools = _mapping_block(config, 'tools')
    rows = [*(topology.get('rag') or []), *(topology.get('tools') or [])] if isinstance(topology, dict) else []
    expected = {'mock': 'mocked', 'real': 'live', 'observe': 'live'}
    drift = sorted(
        f"{row['name']} (map {row['status']}, config mode {entry['mode']})"
        for row in rows if isinstance(row, dict) and row.get('status') in ('live', 'mocked')
        and isinstance(entry := tools.get(str(row.get('name'))), dict) and expected.get(str(entry.get('mode'))) not in (None, row['status'])
    )
    if not drift:
        return []
    return [Finding(
        severity='warn',
        code=Code.MAP_STATUS_MODE_MISMATCH,
        message=(f'the map and the config disagree on what runs for real: {", ".join(drift)}. A tool whose external call goes to the '
                 'mock is `status: mocked` on the map; set the map status to match the config mode and re-render it.'),
        skill_step='Step 8',
    )]


def _check_host_effects_granted(config: dict[str, Any], workdir: Path, *, route: str, topology: dict[str, Any] | None) -> list[Finding]:
    """A live tool runs for real only when the map classifies it, and, when it acts, only on the
    operator's grant or under a confiner that stops it. ALWAYS run: it reads the config and the map,
    and needs no harness. `route` (`_declared_route`) and `topology` (`_read_topology`; `None` when
    there is no readable map) are required, so no caller can drop either.

    * A tool the bench runs for real that the map does not classify FAILS, whatever the route: an
      unknown tool never runs by default.
    * A live writer, dispatched on `route` (`_LIVE_WRITE_ROUTES`): at `agent_host` the route's
      bring-up denies an ungranted action where the agent runs, so each warns, naming the denial (only where a rewrite
      plan stands beside the workdir; a copy without one has no confiner and is treated as the harness route); on
      the `harness` route nothing else stands between the writer and the live system, so each fails.
    * Any other tool that needs a grant is stopped by its runtime's confiner
      (`host_effects.CONFINERS`) when that confiner reaches it; when none does, nothing stands
      between it and the host, so an ungranted one that is not `mode: mock` FAILS."""
    _plugin_scripts_on_path('scripts')
    from host_effects import (  # noqa: PLC0415 - the plugin's one host-effects rule
        TYPING_REMEDY,
        UnclassifiedTools,
        consent_required,
        granted,
        live_write_tools,
        outcome_for,
        runtime_of,
        typed_writes,
        unclassified_tools,
    )

    allowed = granted(workdir)
    findings: list[Finding] = []
    if unclassified := unclassified_tools(topology, config):
        where = 'artifacts/agent_topology.json does not classify' if isinstance(topology, dict) else 'no readable artifacts/agent_topology.json classifies'
        findings.append(Finding(
            severity='fail',
            code=Code.TOOL_UNCLASSIFIED,
            message=f'{where} {unclassified}, which the bench runs for real, so nobody knows what they do on this machine and none may run by default; {TYPING_REMEDY}.',
            skill_step='Step 8',
        ))
    findings += _LIVE_WRITE_ROUTES[route](config, workdir, allowed, topology)
    if not isinstance(topology, dict):
        return findings
    runtime = runtime_of(topology, config)
    if runtime == 'agent_host' and route == 'agent_host' and _host_route_denials(workdir) is None:
        runtime = ''  # a copy with no rewrite plan has no confiner: an ungranted live tool is mocked or refused
    try:
        required = consent_required(topology, runtime=runtime)
    except UnclassifiedTools:
        return findings  # the refusal above stands for the map's consent set
    writers = set(live_write_tools(config, at_agent_host=route == 'agent_host', typed_writes=typed_writes(topology)))
    tools = config.get('tools') if isinstance(config.get('tools'), dict) else {}
    mocked = {str(n) for n, e in tools.items() if isinstance(e, dict) and e.get('mode') == 'mock'}
    by_name = {str(t['name']): t for t in [*(topology.get('rag') or []), *(topology.get('tools') or [])] if isinstance(t, dict) and t.get('name')}
    for name in required:
        if name in allowed or name in writers or name in mocked or outcome_for(by_name[name], runtime=runtime) != 'mock_or_refuse':
            continue
        tool = by_name[name]
        findings.append(Finding(
            severity='fail',
            code=Code.HOST_EFFECT_UNGRANTED,
            message=(f'tool {name!r} runs for real (effects {tool["effects"]}, runs {tool["runs"]}) and `.host_effects_consent` does not '
                     f'grant it, and nothing on this runtime ({runtime or "undeclared"}) confines it, so a scored run would let it act with '
                     "nobody's consent. Grant it at the topology question, or mock it (`status: mocked` on the map, `mode: mock` in the config)."),
            skill_step='Step 8',
        ))
    return findings


def _extract_simulate_tool_names(simulate_source: str) -> set[str]:
    """Pull every ``@plurai.mock(...)`` decorator's tool name from the AST.

    Handles both call shapes the SDK accepts:
      * ``@plurai.mock("lookup_user", ...)`` — positional first arg
      * ``@plurai.mock(name="lookup_user", ...)`` — keyword arg

    Returns an empty set when the source doesn't parse (caller has already
    surfaced syntax errors via ``verify_simulate``).
    """
    try:
        tree = ast.parse(simulate_source)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            tool_name = _decorator_tool_name(deco)
            if tool_name is not None:
                names.add(tool_name)
    return names


def _decorator_tool_name(deco: ast.expr) -> str | None:
    """Return the tool-name argument of a ``@plurai.mock(...)`` decorator.

    Recognizes positional + ``name=`` keyword shapes. Returns ``None`` for
    decorators that aren't ``plurai.mock`` or whose name argument isn't a
    string literal.
    """
    if not isinstance(deco, ast.Call):
        return None
    func = deco.func
    if not (isinstance(func, ast.Attribute) and func.attr == 'mock'):
        return None
    # Positional first argument
    if deco.args and isinstance(deco.args[0], ast.Constant) and isinstance(deco.args[0].value, str):
        return deco.args[0].value
    # Keyword ``name="..."``
    for kw in deco.keywords:
        if kw.arg == 'name' and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _check_tool_name_consistency(
    config: dict[str, Any], simulate_source: str | None,
) -> list[Finding]:
    """Tool names declared in plurai.config.yaml must match @plurai.mock decorators.

    Drift is silent — the mock service never routes calls to the mismatched
    handler, and the test loop just sees agents calling tools that 404 in
    the mock service. AST-detectable; fail-severity.
    """
    if simulate_source is None:
        return []
    # mode: real tools are executed against the customer's REAL implementation, so they have no
    # @plurai.mock handler BY DESIGN — the sibling _check_tools_wrapped already exempts them and
    # says so ("an all-real surface legitimately has no wrap call"). Keying on .keys() alone made
    # an honest all-real agent unpassable, and the documented escape was to write `tools: {}` —
    # i.e. to ERASE the tool surface to satisfy a checker, losing the descriptors every downstream
    # skill reads.
    config_tools = {
        str(name) for name, tool_cfg in _mapping_block(config, 'tools').items()
        if not (isinstance(tool_cfg, dict) and tool_cfg.get('mode') == 'real')
    }
    simulate_tools = _extract_simulate_tool_names(simulate_source)

    findings: list[Finding] = []
    missing_in_simulate = config_tools - simulate_tools
    if missing_in_simulate:
        findings.append(Finding(
            severity='fail',
            code=Code.TOOL_IN_CONFIG_MISSING_FROM_SIMULATE,
            message=(
                f'plurai.config.yaml declares tools {sorted(missing_in_simulate)!r} '
                f'with no @plurai.mock("…") handler in simulate.py. Agents '
                f'calling these tools will hit a schema-stub fallback at best '
                f'and break the simulation at worst.'
            ),
            skill_step='Step 8',
        ))
    missing_in_config = simulate_tools - config_tools
    if missing_in_config:
        findings.append(Finding(
            severity='fail',
            code=Code.TOOL_IN_SIMULATE_MISSING_FROM_CONFIG,
            message=(
                f'simulate.py declares @plurai.mock handlers for '
                f'{sorted(missing_in_config)!r} not present in '
                f'plurai.config.yaml.tools. The backend has no spec to '
                f'generate rich mocks for these.'
            ),
            skill_step='Step 8',
        ))
    return findings


def _has_tool_wrapping(tree: ast.Module) -> bool:
    """True iff the module contains a recognized tool-interception pattern.

    Copied from ``verify_simulate.py`` (whose check 9 moved here — see
    :func:`_check_tools_wrapped`). Three accepted patterns (any one satisfies
    the contract; the third is :func:`_rebinds_a_mock`):

      1. **Canonical wrap calls** — ``apply_mocks(...)``, ``mock_wrap(...)``,
         or ``MockToolNode(...)``, as a plain ``Name`` call, an attribute
         call (``plurai.apply_mocks(...)``), or either form behind a
         ``from plurai import apply_mocks as wrap`` alias.

      2. **Coroutine override** — attribute assignment of the form
         ``<tool>.coroutine = <expr>`` where ``<tool>`` is NOT ``self``.
         Used by LangChain ``create_agent`` agents whose tools live inside
         a factory closure and can't be reached by module-level
         ``apply_mocks``. The ``self.coroutine`` exclusion prevents a
         customer class with its own ``coroutine`` attribute from silently
         satisfying the check without wrapping any tools.
    """
    canonical = {'apply_mocks', 'mock_wrap', 'MockToolNode'}
    # Resolve aliases: ``from plurai import apply_mocks as wrap`` → 'wrap'
    # is a wrap name in this module's local scope.
    aliased = set(canonical)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == 'plurai':
            for alias in node.names:
                if alias.name in canonical and alias.asname:
                    aliased.add(alias.asname)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr if isinstance(func, ast.Attribute)
            else func.id if isinstance(func, ast.Name)
            else None
        )
        if name in aliased:
            return True

    # Coroutine override. Excludes ``self.coroutine = …`` (see docstring).
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not (isinstance(target, ast.Attribute) and target.attr == 'coroutine'):
                continue
            if isinstance(target.value, ast.Name) and target.value.id == 'self':
                continue
            return True
    return _rebinds_a_mock(tree)


#: The SDK's mock-resolution entry: a function that calls it answers a tool call from the mock.
_MOCK_RESOLVERS = frozenset({'_resolve_mock_response'})


def _rebinds_a_mock(tree: ast.Module) -> bool:
    """3. **Rebinding** — ``setattr(x, y, v)`` or ``x.attr = v`` where ``v`` is a mock function (one that
    calls the SDK's ``_resolve_mock_response``, or a ``@plurai.mock`` handler), or a call to a local factory
    that returns one. This is how a harness intercepts a tool at the call its code reaches."""
    defs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def calls_resolver(fn: ast.AST) -> bool:
        return any(isinstance(c, ast.Call) and ast.unparse(c.func).rsplit('.', 1)[-1] in _MOCK_RESOLVERS for c in ast.walk(fn))

    def decorated(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        return any(ast.unparse(d.func if isinstance(d, ast.Call) else d).rsplit('.', 1)[-1] == 'mock' for d in fn.decorator_list)

    mocks = {fn.name for fn in defs if decorated(fn) or calls_resolver(fn)}
    factories = {fn.name for fn in defs if any(
        isinstance(r, ast.Return) and isinstance(r.value, ast.Name) and r.value.id in mocks and r.value.id != fn.name for r in ast.walk(fn))}

    def is_mock(value: ast.expr | None) -> bool:
        if isinstance(value, ast.Name):
            return value.id in mocks
        return isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in factories

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'setattr' and len(node.args) == 3 and is_mock(node.args[2]):
            return True
        if isinstance(node, ast.Assign) and is_mock(node.value) and any(isinstance(tg, ast.Attribute) for tg in node.targets):
            return True
    return False


#: Source suffixes of a non-Python program a harness can spawn to reach the agent. Data suffixes
#: (.json/.yaml/.md/.txt/.csv/…) are deliberately absent: reading a data file is not a bridge.
_BRIDGE_SUFFIXES = frozenset({'.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs', '.go', '.rb', '.java', '.kt', '.rs', '.php', '.cs', '.sh'})


def _non_python_bridge(tree: ast.Module, harness_dir: Path | None) -> str | None:
    """The first string constant in the harness that names an existing non-Python source file at or
    under ``harness_dir`` (e.g. ``HERE / 'bridge.ts'``), as a path relative to it — or None."""
    if harness_dir is None:
        return None
    root = harness_dir.resolve()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or not node.value or any(c.isspace() for c in node.value):
            continue
        if Path(node.value).suffix.lower() not in _BRIDGE_SUFFIXES:
            continue
        try:
            candidate = (root / node.value).resolve()
            if candidate.is_file() and candidate.is_relative_to(root):
                return candidate.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
    return None


def _check_tools_wrapped(
    config: dict[str, Any], simulate_source: str | None, *, harness_dir: Path | None,
) -> list[Finding]:
    """Declared mock-needing tools with no interception pattern in simulate.py.

    Moved here from ``verify_simulate.py`` check 9: only the config knows
    whether the agent actually HAS tools that need a mock, so the gate
    belongs with the config. The check fires only when ``tools`` is
    non-empty AND at least one tool is not ``mode: real`` — a tool-less
    agent (pure-LLM chatbot / summarizer / classifier) or an all-real
    surface legitimately has no wrap call, and used to get a false-positive
    warning from verify_simulate on every run.

    With mock-needing tools declared and NO wrapping detected, severity is
    ``fail`` (not the old warn): the agent will call the real tool
    implementations on turn 1 and crash against any external dependency —
    a real defect, not a judgment call.

    Except when the harness reaches the agent through a non-Python program it
    names (a sibling ``bridge.ts`` it spawns): the wrapping lives there, where
    this AST check cannot read, so the finding drops to ``warn`` and names the
    file. The smoke turn drives that bridge and is what shows the routing.
    """
    if simulate_source is None:
        return []
    tools = _mapping_block(config, 'tools')
    mock_needing = sorted(
        name for name, tool_cfg in tools.items()
        if not (isinstance(tool_cfg, dict) and tool_cfg.get('mode') == 'real')
    )
    if not mock_needing:
        return []
    try:
        tree = ast.parse(simulate_source)
    except SyntaxError:
        # Caller has already surfaced syntax errors via verify_simulate.
        return []
    if _has_tool_wrapping(tree):
        return []
    bridge = _non_python_bridge(tree, harness_dir)
    if bridge is not None:
        return [Finding(
            severity='warn',
            code=Code.TOOLS_NOT_WRAPPED,
            message=(
                f'simulate.py reaches the agent through {bridge}, which this check cannot read, so '
                f'it cannot confirm that the mock-needing tools {mock_needing!r} are wrapped. The '
                f'smoke turn is what shows the mocks are routed. An improvised bridge stays unverifiable: '
                f're-scan onto the bridge template (simulation-setup-bridge), whose simulate.py wraps every '
                f'mock and whose verify_bridge.py proves each route reaches it.'
            ),
            skill_step='Step 8',
        )]
    return [Finding(
        severity='fail',
        code=Code.TOOLS_NOT_WRAPPED,
        message=(
            f'plurai.config.yaml declares mock-needing tools '
            f'{mock_needing!r} but simulate.py wraps none of them — no '
            'apply_mocks(...), mock_wrap(...), or MockToolNode(...) call '
            'and no `<tool>.coroutine = ...` override found. The agent '
            'will call the real tool implementations on turn 1 and crash '
            'against any external dependency (DB, third-party APIs). Add '
            'a Phase 3 `plurai.apply_mocks(<tools_module>, {...})` block '
            'listing every tool the agent uses.'
        ),
        skill_step='Step 8',
    )]


def _check_tool_requires_confirmation(config: dict[str, Any]) -> list[Finding]:
    """``tools.<name>.requires_user_confirmation`` — a framework-declared confirmation gate, a boolean.

    A sibling of ``descriptor:`` for the reason ``state_updates`` is (``ToolDescriptor`` forbids
    unknown keys). The backend counts only a literal ``true`` on a declared tool entry, so a string, a
    number or a copy inside the descriptor declares nothing and is refused rather than ignored.
    """
    findings: list[Finding] = []
    for name, entry in _mapping_block(config, 'tools').items():
        if not isinstance(entry, dict):
            continue
        if 'requires_user_confirmation' in entry and not isinstance(entry['requires_user_confirmation'], bool):
            why = f'is {entry["requires_user_confirmation"]!r}, not a boolean; only `true` declares the gate'
        elif 'requires_user_confirmation' in _descriptor_block(entry):
            why = 'sits inside `descriptor:`, where nothing reads it; move it to the tool entry, beside `mode:`'
        else:
            continue
        findings.append(Finding(
            severity='fail',
            code=Code.TOOL_REQUIRES_CONFIRMATION_INVALID,
            message=f'tools.{name}.requires_user_confirmation {why}',
            skill_step='Step 8',
        ))
    return findings


#: The two values ``tools.<name>.invocation`` takes. ``fixed``: the agent's own control flow calls
#: the tool on EVERY run, whatever the model or the data decide. ``agent``: a model selects it, or
#: code calls it conditionally. Absent means ``agent``.
_TOOL_INVOCATIONS = ('fixed', 'agent')


def _check_tool_invocation(config: dict[str, Any]) -> list[Finding]:
    """``tools.<name>.invocation`` — who decides the call, ``fixed`` or ``agent``.

    A sibling of ``descriptor:`` for the reason ``requires_user_confirmation`` is (``ToolDescriptor``
    forbids unknown keys). The backend reads it to tell a check that only asserts a fixed step fired
    (true on every run) from one that asserts a model-selected call, so a value outside the two, or a
    copy inside the descriptor, is refused rather than read as ``agent``.
    """
    findings: list[Finding] = []
    for name, entry in _mapping_block(config, 'tools').items():
        if not isinstance(entry, dict):
            continue
        if 'invocation' in entry and entry['invocation'] not in _TOOL_INVOCATIONS:
            why = f'is {entry["invocation"]!r}; it is `fixed` (called on every run whatever the agent decides) or `agent`'
        elif 'invocation' in _descriptor_block(entry):
            why = 'sits inside `descriptor:`, where nothing reads it; move it to the tool entry, beside `mode:`'
        else:
            continue
        findings.append(Finding(
            severity='fail',
            code=Code.TOOL_INVOCATION_INVALID,
            message=f'tools.{name}.invocation {why}',
            skill_step='Step 8',
        ))
    return findings


#: How the REAL tool reports the failure the mock models (``tools.<name>.error_channel``), and the
#: evidence rung that says so (``error_channel_source``), in the evidence order.
_ERROR_CHANNELS = ('raises', 'returns')
_ERROR_CHANNEL_SOURCES = ('plugin', 'trace', 'declared', 'inferred')


def _check_tool_error_channel(config: dict[str, Any]) -> list[Finding]:
    """``tools.<name>.error_channel`` — how the real tool reports a failure, a sibling of ``descriptor:``.

    The SDK's ``mock_wrap`` reads it from the config beside the harness: ``raises`` raises
    ``plurai.MockToolError``, ``returns`` hands back the tool's own failure value. Warn-only: a
    value outside the vocabulary declares nothing (a failed mocked call is then handed to the agent
    as a value), and the mocked tools that declare nothing are named in ONE finding per config so
    the scan can type them.
    """
    findings: list[Finding] = []
    untyped: list[str] = []
    for name, entry in _mapping_block(config, 'tools').items():
        if not isinstance(entry, dict):
            continue
        why = None
        if 'error_channel' in entry and entry['error_channel'] not in _ERROR_CHANNELS:
            why = f'`error_channel: {entry["error_channel"]!r}` is not `raises` or `returns`'
        elif 'error_channel_source' in entry and entry['error_channel_source'] not in _ERROR_CHANNEL_SOURCES:
            why = f'`error_channel_source: {entry["error_channel_source"]!r}` is not `plugin`, `trace`, `declared` or `inferred`'
        elif {'error_channel', 'error_channel_source'} & set(_descriptor_block(entry)):
            why = '`error_channel` sits inside `descriptor:`, where nothing reads it; move it to the tool entry, beside `mode:`'
        if why is not None:
            findings.append(Finding(severity='warn', code=Code.TOOL_ERROR_CHANNEL_INVALID, message=f'tools.{name}: {why}', skill_step='Step 8'))
        elif entry.get('mode', 'mock') == 'mock' and 'error_channel' not in entry:
            untyped.append(str(name))
    if untyped:
        findings.append(Finding(
            severity='warn',
            code=Code.ERROR_CHANNEL_UNTYPED,
            message=(
                f'{len(untyped)} mocked tool(s) declare no `error_channel`, so a failed mocked call reaches the agent as a value '
                f'whether or not the real tool raises: {", ".join(sorted(untyped))}. Declare `error_channel: raises | returns` '
                'with its `error_channel_source` (trace > declared > inferred) on each tool entry.'
            ),
            skill_step='Step 8',
        ))
    return findings


#: C12: where a mock cuts in (the lowest call whose arguments are plain typed values, not an encoded wire payload), and why a mock may sit at the tool
#: body (``seam_reason``).
_SEAMS = ('transport', 'client', 'entity', 'tool_body')
_SEAM_REASONS = ('inline_external_call', 'hosted_body', 'unpatchable_write')
#: A ``skipped_code`` / ``guards[].cite`` entry: ``<path>:<line>`` or ``<path>:<start>-<end>``.
_CODE_CITE = re.compile(r'^[^\s:]+:\d+(-\d+)?$')


def _skipped_cite(item: Any) -> str:
    """A ``skipped_code`` entry's cite: a plain ``<path>:<line>``, or a typed row-source answer's ``cite``
    (``{cite, returns: every_row, evidence}``, see ``_check_row_sources_cited``); empty when it has none."""
    cite = item.get('cite') if isinstance(item, dict) else item
    return cite if isinstance(cite, str) else ''


def _check_tool_seams(config: dict[str, Any]) -> list[Finding]:
    """C12 ``tools.<name>.seam`` on every ``mode: mock`` tool, a sibling of ``descriptor:``.

    The seam is the lowest call whose arguments are plain typed values: the agent's own code and the library logic above it keep
    running. ``tool_body`` skips that code, so it names why
    (``seam_reason``) and the code it skips (``skipped_code``, each a manifest coverage hole).
    """
    findings: list[Finding] = []
    for name, entry in _mapping_block(config, 'tools').items():
        if not isinstance(entry, dict) or entry.get('mode', 'mock') != 'mock':
            continue
        seam, reason, skipped = entry.get('seam'), entry.get('seam_reason'), entry.get('skipped_code')
        why = None
        if seam not in _SEAMS:
            why = f'`seam: {seam!r}` is not one of {", ".join(_SEAMS)}' if 'seam' in entry else f'declares no `seam:` ({" | ".join(_SEAMS)})'
        elif seam == 'tool_body' and reason not in _SEAM_REASONS:
            why = f'`seam: tool_body` needs `seam_reason: {" | ".join(_SEAM_REASONS)}` (got {reason!r})'
        elif seam == 'tool_body' and not (isinstance(skipped, list) and skipped and all(_CODE_CITE.match(_skipped_cite(c)) for c in skipped)):
            why = f'`seam: tool_body` needs `skipped_code: [<path:line>, …]`, the agent code the mock skips (got {skipped!r})'
        if why is not None:
            findings.append(Finding(
                severity='fail',
                code=Code.TOOL_SEAM_INVALID,
                message=f'tools.{name}: {why}. Mock at the lowest call whose arguments are plain typed values, not an encoded wire payload.',
                skill_step='Step 6',
            ))
    findings.extend(_check_seam_serves(config))
    return findings


def _check_seam_serves(config: dict[str, Any]) -> list[Finding]:
    """``tools.<name>.serves`` / ``serves_cite``: the real tools a seam mock stands under, and the code that routes them there.

    Traces carry the real tool's name, never the seam's, so the backend records the link and opens each served tool's
    error-channel slot: that tool's trace facts reach the mock, and the seam's own args and channel stay under the seam. A name
    that is no tool here, or a link with no ``<path:line>`` cite, reaches no trace fact.
    """
    tools = _mapping_block(config, 'tools')
    findings: list[Finding] = []
    for name, entry in tools.items():
        if not isinstance(entry, dict) or 'serves' not in entry:
            continue
        served, cite = entry.get('serves'), entry.get('serves_cite')
        why = None
        if entry.get('mode', 'mock') != 'mock' or entry.get('seam') not in ('transport', 'client', 'entity'):
            why = '`serves:` belongs on a `mode: mock` entry at a `transport`, `client` or `entity` seam, the mock a real tool calls through'
        elif not (isinstance(served, list) and served and all(isinstance(t, str) and t.strip() for t in served)):
            why = f'`serves:` must be a list of real tool names (got {served!r})'
        elif unknown := [t for t in served if t.strip() not in tools or t.strip() == name]:
            why = f'`serves:` names {", ".join(repr(t) for t in unknown)}, no other tool of this config'
        elif mocked := [t for t in served if isinstance(tools[t.strip()], dict) and tools[t.strip()].get('mode', 'mock') == 'mock']:
            why = f'`serves:` names {", ".join(repr(t) for t in mocked)}, mocked itself: its own mock answers it, so its calls never reach this seam'
        elif entry.get('invocation') == 'fixed' and (
            unfixed := [t for t in served if not (isinstance(tools[t.strip()], dict) and tools[t.strip()].get('invocation') == 'fixed')]
        ):
            why = (
                f'`invocation: fixed` with `serves:` naming {", ".join(repr(t) for t in unfixed)}, not `invocation: fixed` itself: a fixed seam serves only '
                'fixed tools. A call the product makes on every turn outside every own tool declares `invocation: fixed` and no `serves`; '
                'an endpoint an own tool also reaches keeps `serves` and no `invocation`'
            )
        elif not (isinstance(cite, str) and _CODE_CITE.match(cite.strip())):
            why = f'`serves:` needs `serves_cite: <path:line>`, the agent code where the served tool makes this call (got {cite!r})'
        if why is not None:
            findings.append(Finding(
                severity='fail',
                code=Code.TOOL_SEAM_INVALID,
                message=f'tools.{name}: {why}. The served tool\'s trace facts reach this mock only through a valid link.',
                skill_step='Step 6',
            ))
    return findings



#: ``tools.<name>.payload_codecs``: the codecs the harness applies at a path (decode ``args.*`` before the mock, encode
#: ``returns.*`` after it), in list order on decode. ``mime`` turns RFC 5322 into ``{headers, body, html?, attachments?}``.
_PAYLOAD_CODECS = ('base64', 'base64url', 'json', 'mime', 'form_urlencoded')
#: A codec path: ``args.<arg>`` / ``returns`` (the whole reply), then dotted keys, each optionally ``[*]`` (every item of a list).
_PAYLOAD_CODEC_PATH = re.compile(r'^(?P<root>args|returns)(?P<rest>(?:\.[^.\[\]\s]+|\[\*\])*)$')
#: Codecs whose decoded value is a string: last on a return path, the harness cannot tell a decoded value from one the
#: mock already encoded, so it would encode twice.
_PAYLOAD_CODECS_AMBIGUOUS_LAST = ('base64', 'base64url')


def _payload_codec_problems(entry: dict[str, Any], codecs: Any) -> list[str]:
    """What is wrong with one ``payload_codecs`` value on a transport/client mock, one phrase per problem."""
    if not (isinstance(codecs, list) and codecs):
        return [f'must be a non-empty list of `{{at: args.<path> | returns.<path>, codec: [<codec>, …]}}` (got {codecs!r})']
    args = _descriptor_block(entry).get('args')
    declared = set(args) if isinstance(args, dict) else None
    problems: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(codecs):
        label = f'payload_codecs[{i}]'
        if not (isinstance(item, dict) and set(item) == {'at', 'codec'}):
            problems.append(f'{label} must be exactly `{{at, codec}}` (got {item!r})')
            continue
        at, chain = item['at'], item['codec']
        match = _PAYLOAD_CODEC_PATH.match(at) if isinstance(at, str) else None
        if match is None:
            problems.append(f'{label}.at {at!r} is not `args.<arg>[.<key>…]` / `returns[.<key>…]` (dotted keys, `[*]` for each list item)')
        elif at in seen:
            problems.append(f'{label}.at {at!r} is declared twice')
        else:
            seen.add(at)
            first = match['rest'].removeprefix('.').split('.')[0].split('[')[0]
            if match['root'] == 'args' and (not first or (declared is not None and first not in declared)):
                problems.append(f'{label}.at {at!r} names no `descriptor.args` key ({", ".join(sorted(declared or ())) or "none declared"})')
        if not (isinstance(chain, list) and chain and all(isinstance(c, str) for c in chain)):
            problems.append(f'{label}.codec must be a non-empty list of codecs (got {chain!r})')
            continue
        if unknown := [c for c in chain if c not in _PAYLOAD_CODECS]:
            problems.append(f'{label}.codec names {", ".join(repr(c) for c in unknown)}, not one of {", ".join(_PAYLOAD_CODECS)}')
        if 'mime' in chain[:-1]:
            problems.append(f'{label}.codec puts `mime` before {chain[-1]!r}: `mime` decodes to a structure, so it is last')
        if match is not None and match['root'] == 'returns' and chain[-1] in _PAYLOAD_CODECS_AMBIGUOUS_LAST:
            problems.append(
                f'{label}.codec ends a return path in bare `{chain[-1]}`: the harness cannot tell a decoded string from one the mock '
                'already encoded; end it in `json` / `mime` / `form_urlencoded`, or leave the return encoded'
            )
    return problems


def _check_payload_codecs(config: dict[str, Any]) -> list[Finding]:
    """``tools.<name>.payload_codecs`` — the fields a transport/client mock's wire carries encoded, a sibling of ``seam:``.

    The SDK decodes ``args.<path>`` before the mock and encodes ``returns.<path>`` after it, so the mock, the trail and the
    backend's answer check read plain values and ``descriptor.args`` / ``returns`` describe the DECODED shape. A
    declaration the harness cannot apply fails ``payload_codec_invalid``; one on a ``code`` action-channel tool, whose
    interception applies no codecs, fails ``payload_codec_unsupported_surface``.
    """
    agent = config.get('agent')
    code_channel = isinstance(agent, dict) and agent.get('action_channel') == 'code'
    findings: list[Finding] = []
    for name, entry in _mapping_block(config, 'tools').items():
        if not isinstance(entry, dict):
            continue
        problems: list[str] = []
        if 'payload_codecs' in _descriptor_block(entry):
            problems.append('`payload_codecs` sits inside `descriptor:`, which takes no extra keys; move it to the tool entry, beside `seam:`')
        if 'payload_codecs' in entry:
            if code_channel:
                findings.append(Finding(
                    severity='fail',
                    code=Code.PAYLOAD_CODEC_UNSUPPORTED_SURFACE,
                    message=(
                        f'tools.{name}: `payload_codecs` on a `code` action-channel tool: the code interception applies no codecs, so '
                        'the mock would read the encoded value. Drop it and describe the wire value as it is.'
                    ),
                    skill_step='Step 6',
                ))
                continue
            if entry.get('mode', 'mock') != 'mock' or entry.get('seam') not in ('transport', 'client'):
                problems.append('`payload_codecs` belongs on a `mode: mock` entry at a `transport` or `client` seam, where the harness calls the mock')
            else:
                problems += _payload_codec_problems(entry, entry['payload_codecs'])
        if problems:
            findings.append(Finding(
                severity='fail',
                code=Code.PAYLOAD_CODEC_INVALID,
                message=f'tools.{name}: {"; ".join(problems)}.',
                skill_step='Step 6',
            ))
    return findings


def _function_span(tree: Any, qualname: str) -> tuple[int, int] | None:
    """The first and last line of ``qualname`` (``func`` or ``Class.method``) in a parsed module, decorators
    included; None when the module does not define it."""
    body = getattr(tree, 'body', [])
    node = None
    for part in qualname.split('.'):
        node = next((n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == part), None)
        if node is None:
            return None
        body = node.body
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
    return first, node.end_lineno or node.lineno


#: Folders that hold dependencies or tooling, never the agent's own modules.
_DEPENDENCY_DIRS = frozenset({'node_modules', '.venv', 'venv', 'site-packages', 'dist-packages', '__pycache__'})


def _tree_module_names(root: Path) -> set[str]:
    """Every folder name and ``.py`` stem under ``root`` (dependency folders and dot-folders aside), unpruned otherwise:
    a module whose top package is one of them may be the agent's own, whatever the inventory's scope skipped."""
    names: set[str] = set()
    for _here, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _DEPENDENCY_DIRS and not d.startswith('.')]
        names.update(dirs)
        names.update(f[:-3] for f in files if f.endswith('.py'))
    return names


def _declares_args_codecs(entry: dict[str, Any]) -> bool:
    """Whether a tool entry's own ``payload_codecs`` decodes at least one ``args.*`` path: the declaration that the
    request's encoded fields were examined. A returns-only declaration says nothing about what the mock receives."""
    rows = entry.get('payload_codecs')
    return isinstance(rows, list) and any(isinstance(r, dict) and isinstance(r.get('at'), str) and r['at'].startswith('args.') for r in rows)


def _check_seam_below_installed_tool(config: dict[str, Any], workdir: Path) -> list[Finding]:
    """``seam_below_installed_tool``: a ``seam: transport`` mock with no ``payload_codecs`` serves a tool whose path runs
    through an installed tool class, so the mock receives the library's wire payload, not the tool's typed args.

    The seam rule (C12) is the lowest call whose arguments are plain typed values. Below an installed tool class the
    wire carries what the library built (a Gmail draft is base64url MIME inside a JSON body), so the mock and the
    backend's answer check read an encoded payload. A served tool runs through an installed tool class when
    (a) its ``original_module`` is installed code, or (b) its in-tree ``original_function`` body holds a
    ``production_tools.backends[]`` construction of one (``artifacts/agent_inventory.json``). Both sides are decided
    POSITIVELY, against the git root holding this workdir (never the inventory's stored ``root``, which names the
    scanning machine): in-tree is a file under an import root or a seed's directory; installed is a module whose top
    package is nowhere in the tree (dependency folders aside) AND is a package of a recorded backend class or of the
    agent's site-packages. A module that is neither is silent: the check never guesses. Silent with no inventory, no
    root, or a non-Python agent.
    """
    tools = _mapping_block(config, 'tools')
    seams = {
        str(name): [t.strip() for t in entry['serves'] if isinstance(t, str) and t.strip()]
        for name, entry in tools.items()
        if isinstance(entry, dict) and entry.get('mode', 'mock') == 'mock' and entry.get('seam') == 'transport'
        and not _declares_args_codecs(entry) and isinstance(entry.get('serves'), list)
    }
    if not any(seams.values()):
        return []
    _plugin_scripts_on_path('scripts')
    import agent_inventory  # noqa: PLC0415 - the ONE reading of the agent's tree and its installed tool classes

    inventory = agent_inventory.read_artifact(workdir)
    seeds = inventory.get('seeds') if isinstance(inventory, dict) else None
    if not (isinstance(seeds, list) and seeds and all(isinstance(p, str) and p.endswith(agent_inventory.PY_SUFFIXES) for p in seeds)):
        return []
    root, _ = agent_inventory.find_root(workdir)
    if root is None:
        return []
    production = inventory.get('production_tools')
    backends = [
        b for b in (production.get('backends') if isinstance(production, dict) else None) or []
        if isinstance(b, dict) and isinstance(b.get('class'), str) and isinstance(b.get('source'), str) and ':' in b['source']
    ]
    repo = agent_inventory.Repo(root, agent_inventory.source_files(root))
    seed_dirs = list(dict.fromkeys((root / seed).parent for seed in seeds))
    own_names = _tree_module_names(root)
    installed = agent_inventory.Installed(agent_inventory.site_packages(root, workdir))
    backend_packages = {b['class'].split('.')[0] for b in backends}

    def own_file(module: str) -> Path | None:
        path = repo.resolve_py(module, root / '__init__.py')
        if path is not None:
            return path
        parts = module.split('.')
        return next((c for d in seed_dirs for c in (d.joinpath(*parts[:-1], parts[-1] + '.py'), d.joinpath(*parts, '__init__.py'))
                     if c.is_file() and root in c.parents), None)

    def is_installed(module: str) -> bool:
        top = module.split('.')[0]
        if top in own_names:
            return False
        return top in backend_packages or installed._module(module) is not None or any(
            (d / top).is_dir() or (d / f'{top}.py').is_file() for d in installed.dirs)

    findings: list[Finding] = []
    for name, served in seams.items():
        through: list[tuple[str, str, bool]] = []  # (served tool, installed callable, is a class)
        for tool in served:
            descriptor = _descriptor_block(tools.get(tool))
            module, function = descriptor.get('original_module'), descriptor.get('original_function')
            if not (isinstance(module, str) and module.strip() and isinstance(function, str) and function.strip()):
                continue
            path = own_file(module.strip())
            if path is None:
                if is_installed(module.strip()):
                    head = function.strip().split('.')
                    through.append((tool, f'{module.strip()}.{head[0]}', len(head) > 1))
                continue
            span = _function_span(repo.tree(path), function.strip())
            if span is None:
                continue
            rel = repo.rel(path)
            for backend in backends:
                file, _, line = backend['source'].rpartition(':')
                if file == rel and line.isdigit() and span[0] <= int(line) <= span[1]:
                    through.append((tool, backend['class'], True))
        if not through:
            continue
        named = '; '.join(f'`{tool}` runs `{dotted}`' for tool, dotted, _ in through)
        remedies = ' / '.join(dict.fromkeys(
            f'`{dotted.rsplit(".", 1)[-1]}` at `seam: client` with `original_function: {dotted.rsplit(".", 1)[-1]}._run`' if is_class
            else f'`{dotted}` at `seam: client`'
            for _, dotted, is_class in through
        ))
        findings.append(Finding(
            severity='fail',
            code=Code.SEAM_BELOW_INSTALLED_TOOL,
            message=(
                f'tools.{name}: a `seam: transport` mock with no `args.*` `payload_codecs` row sits below installed tool code: {named}. '
                'The mock then receives the wire payload the library encodes, not the tool\'s typed arguments. '
                f'Mock {remedies} (and drop it from `serves:`), or declare `payload_codecs` on this entry for every field the wire carries encoded.'
            ),
            skill_step='Step 6',
        ))
    return findings


_GUARD_READS = ('store', 'result')


def _guards(config: dict[str, Any]) -> list[dict[str, Any]]:
    value = config.get('guards')
    return [g for g in value if isinstance(g, dict)] if isinstance(value, list) else []


def _check_guards(config: dict[str, Any]) -> list[Finding]:
    """``guards:`` — the agent's middleware and guards that read a tool's result or a store, as the scan declares them.

    ``{name, cite, watches: [<tool>], reads: store, via: <dotted reader>}`` or ``{…, reads: result, fields: [<path>]}``.
    ``guard_sees_mock`` reads it; a malformed entry declares nothing it could check.
    """
    if 'guards' not in config:
        return []
    tools = _mapping_block(config, 'tools')
    raw = config.get('guards')
    problems = [] if isinstance(raw, list) else [f'must be a LIST of guard declarations (got {type(raw).__name__})']
    for i, guard in enumerate(raw if isinstance(raw, list) else []):
        label = f'guards[{i}]'
        if not isinstance(guard, dict):
            problems.append(f'{label} is not a mapping')
            continue
        watches = guard.get('watches')
        if not (isinstance(guard.get('name'), str) and guard['name'].strip()):
            problems.append(f'{label} has no `name`')
        if not (isinstance(guard.get('cite'), str) and _CODE_CITE.match(guard['cite'])):
            problems.append(f'{label} `cite` is not `<path>:<line>` (got {guard.get("cite")!r})')
        if not (isinstance(watches, list) and watches and all(isinstance(t, str) and t in tools for t in watches)):
            problems.append(f'{label} `watches` must list declared tools (got {watches!r})')
        if guard.get('reads') not in _GUARD_READS:
            problems.append(f'{label} `reads` is not `store` or `result` (got {guard.get("reads")!r})')
        elif guard['reads'] == 'store' and not (isinstance(guard.get('via'), str) and guard['via'].strip()):
            problems.append(f'{label} reads a store, so `via` names the dotted reader it reads through')
        elif guard['reads'] == 'result' and not (isinstance(guard.get('fields'), list) and guard['fields'] and all(isinstance(f, str) and f for f in guard['fields'])):
            problems.append(f'{label} reads a result, so `fields` lists the result paths it reads')
    return [Finding(severity='fail', code=Code.GUARDS_INVALID, message=f'`guards:` {p}.', skill_step='Step 6') for p in problems]


def _harness_patches(name: str, source: str, bridge_text: str) -> bool:
    """The harness rebinds ``name``: an assignment to ``<x>.<name>``, a ``setattr`` / ``patch`` naming it, or a bridge
    map / replace file that names it."""
    if re.search(rf'\b{re.escape(name)}\b', bridge_text):
        return True
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else []
        if any(isinstance(t, ast.Attribute) and t.attr == name for t in targets):
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and (node.value == name or node.value.endswith(f'.{name}')):
            return True
    return False


def _bridge_text(harness_dir: Path | None) -> str:
    bridge = harness_dir / 'bridge' if harness_dir is not None else None
    if bridge is None or not bridge.is_dir():
        return ''
    files = [bridge / 'bridge.map.json', *sorted((bridge / 'replace').glob('*'))] if (bridge / 'replace').is_dir() else [bridge / 'bridge.map.json']
    return '\n'.join(p.read_text(encoding='utf-8', errors='replace') for p in files if p.is_file())


def _check_guards_see_mock(config: dict[str, Any], simulate_source: str | None, *, harness_dir: Path | None = None) -> list[Finding]:
    """``guard_sees_mock``: every declared guard over a mocked tool reads what the mock produced.

    A store guard watching a write that lands in the mock world reads through ``via``: the harness rebinds that reader,
    or the write's seam (``transport`` / ``client``) also serves the store's reads to a mocked tool. A result guard reads
    ``fields`` off the tool's return: a mocked tool declares each in ``descriptor.returns.fields`` / ``.state_fields`` /
    ``state_updates`` and the harness places it. Otherwise the agent's guard runs on what the mock never touched — it
    never fires, or fires on every call — and the run tests an agent without it.
    """
    if simulate_source is None:
        return []
    _plugin_scripts_on_path('scripts')
    import writer_readers  # noqa: PLC0415 - the ONE reading of which side a tool's writes land on

    tools = {name: entry for name, entry in _mapping_block(config, 'tools').items() if isinstance(entry, dict)}
    bridge_text = _bridge_text(harness_dir)
    findings: list[Finding] = []

    def blind(guard: dict[str, Any], why: str) -> None:
        findings.append(Finding(
            severity='fail',
            code=Code.GUARD_SEES_MOCK,
            message=f'guard {guard["name"]} ({guard["cite"]}) {why}, so it never sees the mock and the run tests the agent without it.',
            skill_step='Step 6',
        ))

    for guard in _guards(config):
        watches = [t for t in guard.get('watches') or [] if isinstance(t, str) and t in tools]
        if guard.get('reads') == 'store' and isinstance(guard.get('via'), str):
            leaf = guard['via'].rsplit('.', 1)[-1]
            mocked = [t for t in watches if writer_readers.writes(tools[t]) and writer_readers.write_side(tools[t]) == writer_readers.MOCK_WORLD]
            # A transport / client seam that also serves the store's reads to a mocked reader is the reader `via` reaches.
            unserved = [t for t in mocked if not (writer_readers.seam(tools[t]) in ('transport', 'client') and writer_readers.MOCK_WORLD in {
                side for reader, side in writer_readers.store_readers(config, t).items() if reader != t})]
            if unserved and not _harness_patches(leaf, simulate_source, bridge_text):
                blind(guard, f'reads the store {", ".join(unserved)} write(s) through `{guard["via"]}`, which the harness never rebinds to the mock world')
        elif guard.get('reads') == 'result' and isinstance(guard.get('fields'), list):
            for tool in (t for t in watches if writer_readers.mode(tools[t]) == 'mock'):
                returns = _descriptor_block(tools[tool]).get('returns')
                declared = {k for block in ((returns or {}).get('fields'), (returns or {}).get('state_fields')) if isinstance(block, dict) for k in block} if isinstance(returns, dict) else set()
                declared |= set(tools[tool].get('state_updates') or {}) if isinstance(tools[tool].get('state_updates'), dict) else set()
                text = simulate_source + '\n' + bridge_text
                missing = [f for f in guard['fields'] if isinstance(f, str) and (
                    f.rsplit('.', 1)[-1] not in declared or not re.search(rf'\b{re.escape(f.rsplit(".", 1)[-1])}\b', text))]
                if missing:
                    blind(guard, f'reads {", ".join(missing)} off `{tool}`\'s result, which the mock does not return (declare each in '
                                 '`descriptor.returns.fields` / `state_fields` / `state_updates`) or the harness never places')
    return findings


#: A `read_through.via` entry: a dotted accessor whose last segment is the name the harness rebinds (a Python
#: `pkg.mod.Class.method`, or a TS module path ending in its export, `utils/busy.getBusy`).
_READ_THROUGH_VIA = re.compile(r'^\S+\.[A-Za-z_$][\w$]*$')


def _read_through_readers(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {name: entry for name, entry in _mapping_block(config, 'tools').items() if isinstance(entry, dict) and 'read_through' in entry}


def _check_read_through(config: dict[str, Any]) -> list[Finding]:
    """``tools.<name>.read_through: {via: [<dotted store accessor>, ...]}`` — a real reader of a store a mocked write lands in.

    A sibling of ``descriptor:`` (the strict descriptor refuses unknown keys). It declares that the harness rebinds each
    ``via`` accessor so the agent's own read sees the mock world over the real store; which writers and which world type
    it serves come from the ``tool_graph`` edges into the reader. ``read_through_invalid``: not ``mode: real``, beside
    ``read_strategy: mocked`` (a world-only reader overlays nothing), or ``via`` is not a non-empty list of dotted names.
    ``read_through_unlinked``: no edge from a writer whose write lands in the mock world targets the reader, so nothing
    says what the overlay serves.
    """
    _plugin_scripts_on_path('scripts')
    import writer_readers  # noqa: PLC0415 - the ONE reading of which side a tool's writes land on

    tools = {name: entry for name, entry in _mapping_block(config, 'tools').items() if isinstance(entry, dict)}
    linked = {e.reader for e in writer_readers.writer_edges(config) if e.reader and writer_readers.write_side(tools[e.writer]) == writer_readers.MOCK_WORLD}
    findings: list[Finding] = []
    for name, entry in _read_through_readers(config).items():
        block = entry['read_through']
        via = block.get('via') if isinstance(block, dict) else None
        why = (
            f'is on a `mode: {writer_readers.mode(entry)}` tool; only a real reader reads through' if writer_readers.mode(entry) != 'real'
            else 'sits beside `read_strategy: mocked`, a reader that reads the mock world alone' if _descriptor_block(entry).get('read_strategy') == 'mocked'
            else f'`via` is {via!r}; it is a non-empty list of the dotted store accessors the harness rebinds'
            if not (isinstance(via, list) and via and all(isinstance(v, str) and _READ_THROUGH_VIA.match(v.strip()) for v in via))
            else None
        )
        if why:
            findings.append(Finding(severity='fail', code=Code.READ_THROUGH_INVALID, message=f'tools.{name}.read_through {why}.', skill_step='Step 6'))
        elif name not in linked:
            findings.append(Finding(
                severity='fail',
                code=Code.READ_THROUGH_UNLINKED,
                message=(
                    f'tools.{name}.read_through: no `tool_graph` edge from a write tool whose writes land in the mock world targets '
                    f'{name}, so nothing says which writes the overlay serves. Add `{{source: <mocked writer>, target: {name}, '
                    'shared_entity: <type>}`, or drop `read_through`.'
                ),
                skill_step='Step 6',
            ))
    return findings


def _check_read_through_bound(config: dict[str, Any], simulate_source: str | None, *, harness_dir: Path | None = None) -> list[Finding]:
    """``read_through_unbound``: every ``read_through.via`` accessor of a real reader is rebound by the harness (the
    same rebinding test as a guard's ``via``). Otherwise the run tests a reader that never sees the mock's writes.
    ``read_through_swallowed`` (warn, never a stop): the overlay bound there swallows a failure (:func:`_overlay_swallows`),
    so a rebound accessor can still read the store without the mock's writes and say nothing."""
    if simulate_source is None:
        return []
    bridge_text = _bridge_text(harness_dir)
    findings: list[Finding] = []
    for name, entry in _read_through_readers(config).items():
        block = entry['read_through']
        via = block.get('via') if isinstance(block, dict) else None
        if entry.get('mode') != 'real' or not isinstance(via, list):
            continue
        unbound = [v for v in via if isinstance(v, str) and v.strip() and not _harness_patches(v.strip().rsplit('.', 1)[-1], simulate_source, bridge_text)]
        if unbound:
            findings.append(Finding(
                severity='fail',
                code=Code.READ_THROUGH_UNBOUND,
                message=(
                    f'tools.{name}.read_through names {", ".join(f"`{v}`" for v in unbound)}, which the harness never rebinds, so the '
                    'real read never sees the mock\'s writes. Rebind each accessor to read the mock world first (a point read serves '
                    'the world\'s entity, a query adds the world\'s matching rows to the real ones), or mock lower.'
                ),
                skill_step='Step 6',
            ))
    readers_of: dict[str, set[str]] = {}
    for name, entry in _read_through_readers(config).items():
        via = entry['read_through'].get('via') if isinstance(entry['read_through'], dict) else None
        for v in via if entry.get('mode') == 'real' and isinstance(via, list) else []:
            if isinstance(v, str) and v.strip():
                readers_of.setdefault(v.strip().rsplit('.', 1)[-1], set()).add(name)
    for (function, line), leaves in sorted(_overlay_swallows(simulate_source, set(readers_of)).items()):
        readers = sorted({r for leaf in leaves for r in readers_of[leaf]})
        findings.append(Finding(
            severity='warn',
            code=Code.READ_THROUGH_SWALLOWED,
            message=(
                f'simulate.py:{line} `{function}` (the read-through overlay of {", ".join(f"`{r}`" for r in readers)}) catches every '
                'exception and raises nothing, so a world read, placement or render that fails reads as "nothing in the world" and the '
                'real reader silently shows the store without the mock\'s writes. Test "outside a scenario" with '
                '`plurai.active_session_id() is None`, and let any other failure raise `plurai.HarnessFault` naming the accessor.'
            ),
            skill_step='Step 6',
        ))
    return findings


#: An `except` that catches these catches every failure; one whose body raises nothing swallows it.
_CATCH_ALL = frozenset({'Exception', 'BaseException'})


def _catches_all(handler: ast.ExceptHandler) -> bool:
    kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(k is None or (isinstance(k, ast.Name) and k.id in _CATCH_ALL) or (isinstance(k, ast.Attribute) and k.attr in _CATCH_ALL) for k in kinds)


def _overlay_swallows(source: str, leaves: set[str]) -> dict[tuple[str, int], set[str]]:
    """``(function, line) -> the via leaves it serves`` for every catch-all ``except`` that raises nothing, in the
    module-level functions the harness binds to a ``via`` leaf (``X.<leaf> = fn`` / ``setattr(X, '<leaf>', fn)``) and
    every module-level function they call, transitively. Python harness only: a bridge ``replace`` file is not read."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    funcs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    bound: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id in funcs:
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr in leaves:
                    bound.setdefault(node.value.id, set()).add(target.attr)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'setattr' and len(node.args) == 3
              and isinstance(node.args[1], ast.Constant) and node.args[1].value in leaves and isinstance(node.args[2], ast.Name)
              and node.args[2].id in funcs):
            bound.setdefault(node.args[2].id, set()).add(str(node.args[1].value))
    serves: dict[str, set[str]] = {}
    for root, root_leaves in bound.items():
        stack, seen = [root], set()
        while stack:
            fn = stack.pop()
            if fn in seen:
                continue
            seen.add(fn)
            serves.setdefault(fn, set()).update(root_leaves)
            stack += [n.func.id for n in ast.walk(funcs[fn]) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in funcs]
    return {
        (fn, handler.lineno): served
        for fn, served in serves.items()
        for handler in ast.walk(funcs[fn])
        if isinstance(handler, ast.ExceptHandler) and _catches_all(handler) and not any(isinstance(n, ast.Raise) for s in handler.body for n in ast.walk(s))
    }


def _check_tool_exposed_in(config: dict[str, Any]) -> list[Finding]:
    """``tools.<name>.exposed_in`` — the agent's scopes (subagent, topic, graph node) whose tool list holds the tool.

    A sibling of ``descriptor:`` for the reason ``invocation`` is. The backend refuses a value that is not a list of
    scope names at generation, so it is refused here first, and a copy inside the descriptor declares nothing.
    """
    findings: list[Finding] = []
    for name, entry in _mapping_block(config, 'tools').items():
        if not isinstance(entry, dict):
            continue
        value = entry.get('exposed_in')
        if 'exposed_in' in entry and not (isinstance(value, list) and all(isinstance(scope, str) and scope.strip() for scope in value)):
            why = f'is {value!r}; it is a list of the scope names whose tool list holds this tool'
        elif 'exposed_in' in _descriptor_block(entry):
            why = 'sits inside `descriptor:`, where nothing reads it; move it to the tool entry, beside `mode:`'
        else:
            continue
        findings.append(Finding(
            severity='fail',
            code=Code.TOOL_EXPOSED_IN_INVALID,
            message=f'tools.{name}.exposed_in {why}',
            skill_step='Step 8',
        ))
    return findings


#: The one-of forms a ``state_updates:`` entry takes: a literal the real body writes, the mock
#: result field that carries the value, or a declared drop with its reason.
_STATE_UPDATE_FORMS = frozenset({'value', 'from_result', 'omit'})


def _check_tool_state_updates(config: dict[str, Any]) -> list[Finding]:
    """``tools.<name>.state_updates`` — the state keys the REAL tool writes on success.

    A sibling of ``descriptor:`` (``ToolDescriptor`` forbids unknown keys). Each key maps to
    exactly one of ``{value: <literal>}``, ``{from_result: <returns field>}`` or
    ``{omit: <reason>}``. Only a ``mode: mock`` tool has a wrapper to land them; ``messages`` is
    the wrapper's own ToolMessage; a ``from_result`` must name a ``descriptor.returns.fields``
    entry or a ``descriptor.returns.state_fields`` one (a sibling of the model-visible return),
    or the mock never returns it.
    """
    findings: list[Finding] = []

    def bad(name: str, why: str) -> None:
        findings.append(Finding(
            severity='fail',
            code=Code.TOOL_STATE_UPDATES_INVALID,
            message=f'tools.{name}.state_updates: {why}',
            skill_step='Step 8',
        ))

    for name, entry in _mapping_block(config, 'tools').items():
        if not isinstance(entry, dict) or 'state_updates' not in entry:
            continue
        declared = entry['state_updates']
        if not isinstance(declared, dict):
            bad(name, f'must be a mapping of state key -> one of {sorted(_STATE_UPDATE_FORMS)} (got {type(declared).__name__})')
            continue
        if entry.get('mode') != 'mock':
            bad(name, f'is declared on a `mode: {entry.get("mode")}` tool; the real body runs and writes its own state, so only a `mode: mock` tool takes it')
        returns = _descriptor_block(entry).get('returns')
        blocks = [returns.get(k) for k in ('fields', 'state_fields')] if isinstance(returns, dict) else []
        declared_keys = [block for block in blocks if isinstance(block, dict)]
        for key, spec in declared.items():
            if key == 'messages':
                bad(name, '`messages` is the wrapper\'s own ToolMessage; declare only the other state keys')
            elif not isinstance(spec, dict) or len(spec) != 1 or not set(spec) <= _STATE_UPDATE_FORMS:
                bad(name, f'{key!r} must be exactly one of {sorted(_STATE_UPDATE_FORMS)} (got {spec!r})')
            elif any(form in spec and not (isinstance(spec[form], str) and spec[form].strip()) for form in ('from_result', 'omit')):
                bad(name, f'{key!r}: `from_result` names a field and `omit` states the reason; both are non-blank strings (got {spec!r})')
            elif 'from_result' in spec and declared_keys and not any(spec['from_result'] in block for block in declared_keys):
                bad(name, f'{key!r} reads from_result {spec["from_result"]!r}, which descriptor.returns.fields / .state_fields does not declare, so the mock never returns it')
    return findings


def _check_descriptor_bare_string_fields(config: dict[str, Any]) -> list[Finding]:
    """Flag ``descriptor.args`` / ``descriptor.returns`` given as a bare
    description string instead of the structured dict form.

    ``ToolDescriptor``'s field validator accepts this — it coerces a bare
    string to ``{'description': <str>}`` rather than hard-failing the whole
    ``generate_env_start`` upload on one loose field, so this is NOT a
    backend-rejection error. But the dict form (``{type, fields}`` for
    returns; ``{arg_name: {type, description}}`` for args) is what feeds
    the backend's richer mock generation (e.g. state-bearing return fields
    checked below); a bare string silently degrades that. Flag it as
    ``warn`` so authors choose the richer form deliberately, not by
    accident.
    """
    findings: list[Finding] = []
    for tool_name, tool_cfg in _mapping_block(config, 'tools').items():
        if not isinstance(tool_cfg, dict):
            continue
        descriptor = _descriptor_block(tool_cfg)
        for field_name, bare_string_code in _BARE_STRING_CODE.items():
            value = descriptor.get(field_name)
            if isinstance(value, str):
                findings.append(Finding(
                    severity='warn',
                    code=bare_string_code,
                    message=(
                        f'tool {tool_name!r}: descriptor.{field_name} is a bare '
                        f'string rather than the structured dict form. The '
                        f"backend accepts this (auto-wrapped as "
                        f"{{'description': ...}}) but loses the structured info "
                        f'used for richer mock generation — prefer the dict '
                        f'form. See references/example_plurai_config.yaml.'
                    ),
                    skill_step='Step 8',
                ))
    return findings


def _state_bearing_return_fields(entry: dict[str, Any]) -> frozenset[str]:
    """The return fields a tool's ``state_updates:`` lands: each landing key's own name and each
    ``from_result`` source. ``omit:`` entries land nothing."""
    declared = entry.get('state_updates')
    if not isinstance(declared, dict):
        return frozenset()
    out: set[str] = set()
    for key, spec in declared.items():
        if not isinstance(spec, dict) or 'omit' in spec:
            continue
        out.add(str(key))
        if isinstance(spec.get('from_result'), str):
            out.add(spec['from_result'])
    return frozenset(out)


def _check_descriptor_returns_fields_present_for_state_setters(
    config: dict[str, Any], simulate_source: str | None,
) -> list[Finding]:
    """When ``descriptor.returns.fields`` declares a state-bearing field, the mock body should
    mention it (so the backend has somewhere to read from when it adopts the same shape).

    State-bearing is the tool's own typed declaration, never a list of names: a return field is
    state-bearing when the entry's ``state_updates:`` lands it — a key of the same name, or the
    ``from_result`` source of one.

    This is severity ``warn`` — the dumb local mock is allowed to omit
    fields the rich backend mock will set.
    """
    if simulate_source is None:
        return []
    try:
        tree = ast.parse(simulate_source)
    except SyntaxError:
        return []

    findings: list[Finding] = []
    for name, entry in _mapping_block(config, 'tools').items():
        if not isinstance(entry, dict):
            continue
        returns = _descriptor_block(entry).get('returns') or {}
        if not isinstance(returns, dict):
            # ToolDescriptor.returns coerces a bare description string to
            # {'description': <str>} with no 'fields' — nothing to check here.
            continue
        fields = returns.get('fields') or {}
        if not isinstance(fields, dict):
            continue
        state_bearing = {f for f in fields if f in _state_bearing_return_fields(entry)}
        if not state_bearing:
            continue
        # Does the corresponding mock function mention them anywhere in its body?
        mock_fn = _find_mock_function(tree, name)
        if mock_fn is None:
            continue
        body_text = ast.unparse(mock_fn) if hasattr(ast, 'unparse') else ''
        missing = {f for f in state_bearing if f not in body_text}
        if missing:
            findings.append(Finding(
                severity='warn',
                code=Code.DESCRIPTOR_RETURNS_STATE_FIELD_NOT_IN_MOCK_BODY,
                message=(
                    f'tool {name!r}: descriptor.returns.fields declares '
                    f'{sorted(missing)!r} but the inline mock body never '
                    f'mentions them. The dumb local mock can omit these '
                    f'(backend rich mock sets them) — but if scenarios test '
                    f'this terminal/error path, the local simulation will '
                    f'never observe the field.'
                ),
                skill_step='Step 8',
                line=mock_fn.lineno,
            ))
    return findings


def _root_package(dotted: str) -> str:
    """Return the first dotted segment of a Python module path.

    ``our_backend.db.queries`` → ``our_backend``. ``mypackage`` → ``mypackage``.
    """
    return dotted.split('.', 1)[0]


def _resolve_read_strategy(tool_entry: dict[str, Any]) -> str | None:
    """Mirror ``ToolDescriptor.effective_read_strategy`` without depending
    on the Pydantic schema being importable. ``mocked`` is the default
    when ``reads_from`` is a non-empty string; explicit ``read_strategy``
    on the descriptor overrides.

    Verifier needs this to skip essentialness checks for tools whose
    reads explicitly stay real (RAG indexes, vector DBs, public reference
    data — customer opts out via ``read_strategy: real``).
    """
    descriptor = _descriptor_block(tool_entry)
    explicit = descriptor.get('read_strategy')
    if isinstance(explicit, str) and explicit:
        return explicit
    reads_from = descriptor.get('reads_from')
    if isinstance(reads_from, str) and reads_from.strip():
        return 'mocked'
    return None


#: The descriptor value that DECLARES a tool to be the agent's identity surface — the sign-in /
#: token / tenant-resolve / consent call a session is established through. A declaration, like
#: the strategies above and unlike a guess: nothing here keys on a tool NAME, on an
#: ``Authorization`` header, on a provider or on a language, so the same predicate holds for an
#: agent in any domain (and holds vacuously for the overwhelming majority, which declare nothing).
_IDENTITY_ROLE = 'identity'

#: The VOCABULARY ``descriptor.role`` is drawn from — one value today. Every check below is keyed
#: on this set rather than on a list of known misspellings, so adding a role here accepts it
#: everywhere and anything outside it is refused by construction, with no second place to update.
#:
#: (Not to be confused with ``_STORAGE_ROLE_KEYS`` / :func:`_declared_roles` further down, which
#: read a ``role:`` on a ``stubs.modules`` ENTRY — a different object, a different vocabulary, and
#: normalized rather than exact because nothing downstream compares those as identifiers.)
_DECLARED_DESCRIPTOR_ROLES = frozenset({_IDENTITY_ROLE})


def _declares_identity_role(tool_entry: dict[str, Any]) -> bool:
    """True when the tool's descriptor declares ``role: identity``.

    Compared the way the CONSUMER compares it — the backend dispatches on ``descriptor.role ==
    IDENTITY_ROLE``, exactly, with no case folding and no strip — because a pre-flight that arms
    on a spelling the consumer ignores tells the author their identity surface is handled when
    nothing downstream will serve it. (The mirror-image mistake is this file's own history:
    ``render_actions`` was compared RAW here while the backend compared it stripped, and a config
    this check called clean was rejected by the scan.)
    """
    return _descriptor_block(tool_entry).get('role') == _IDENTITY_ROLE


def _check_descriptor_role_vocabulary(config: dict[str, Any]) -> list[Finding]:
    """A ``descriptor.role`` outside the declared vocabulary is refused, by value.

    Without this, the exactness in :func:`_declares_identity_role` has a silent hole:
    ``role: Identity`` on a tool left on the live provider was LOUD before the
    marker existed (the schema called it an extra key) and would be SILENT after it — the backend
    compares exactly, so the capitalised spelling declares nothing to anyone, and the author is
    told their config is clean while the identity surface is served by nobody. A declaration that
    takes effect nowhere is the failure class ``unknown_root_key`` exists for, one level down.

    Keyed on :data:`_DECLARED_DESCRIPTOR_ROLES`, never on a list of typos: a value that normalizes
    onto a declared role is called out as the near-miss it is, and anything else is refused for the
    plainer reason that nothing dispatches on it. Fail rather than warn, because the base tree's
    behaviour for exactly this config was a non-zero exit, and a fix that quiets a loud config is
    not a fix.
    """
    findings: list[Finding] = []
    accepted = ', '.join(repr(r) for r in sorted(_DECLARED_DESCRIPTOR_ROLES))
    for name, entry in _mapping_block(config, 'tools').items():
        if not isinstance(entry, dict):
            continue
        descriptor = _descriptor_block(entry)
        if 'role' not in descriptor:
            continue
        role = descriptor.get('role')
        if role is None:
            continue
        # A role is a NAME. Testing membership first crashed on every unhashable value
        # (`role: [identity]` → `TypeError: unhashable type: 'list'`, taking the ten checks
        # after this one down with it); the SHAPE is decided before the vocabulary is
        # consulted, and a non-name gets the same code with the shape spelled out.
        if not isinstance(role, str):
            findings.append(Finding(
                severity='fail',
                code=Code.DESCRIPTOR_ROLE_UNKNOWN,
                message=(
                    f'tool {name!r}: descriptor.role is a {type(role).__name__} ({role!r}), not a role '
                    f'NAME. Every reader of this key compares it to a word, so a {type(role).__name__} '
                    f'dispatches nowhere. Accepted today: {accepted}.'
                ),
                skill_step='Step 8',
            ))
            continue
        if role in _DECLARED_DESCRIPTOR_ROLES:
            continue
        near = next(
            (r for r in sorted(_DECLARED_DESCRIPTOR_ROLES) if role.strip().lower() == r),
            None,
        )
        detail = (
            f'{role!r} is a case/whitespace variant of {near!r}, and every reader of this key '
            f'compares it EXACTLY — so it declares nothing at all. Write {near!r}.'
            if near is not None else
            f'nothing dispatches on {role!r}; the key would take effect nowhere.'
        )
        findings.append(Finding(
            severity='fail',
            code=Code.DESCRIPTOR_ROLE_UNKNOWN,
            message=(
                f'tool {name!r}: descriptor.role is {role!r}, which is not a declared role. '
                f'{detail} Accepted today: {accepted}.'
            ),
            skill_step='Step 8',
        ))
    return findings


def _identity_surface_is_mocked(tool_entry: dict[str, Any], *, served: bool) -> bool:
    """Is there a MOCK behind this identity surface?

    Three shapes serve one from the mocked world, and they are the ones the rest of this
    file already reasons about:

    * ``mode: mock`` — a ``@plurai.mock`` handler answers the call, and the token it hands back
      is whatever the seeded user entity says it is. An OMITTED ``mode`` is the same thing:
      "not ``mode: real``" is how every other check here reads mock-needing, and the marker must
      not invent a second meaning for that absence.
    * ``mode: real`` named in a seam mock's ``serves:`` (``served``) — the customer's own sign-in
      logic runs, and the call it makes reaches the seam mock, which answers from the seeded world.
    * ``mode: real`` with an effective ``read_strategy`` of ``mocked`` — the retired route a
      legacy config still carries (``world_served_without_mock`` flags it); its reads come from a
      harness-authored adapter. Resolved through :func:`_resolve_read_strategy`, never re-derived.

    Everything else is a real credential path: ``mode: observe`` RUNS the call and captures the
    result (the capture happens after the real badge was required), ``read_strategy: real`` is
    the live provider by declaration, ``stub`` is a ``MagicMock`` token no seeded user stands
    behind, and an unrecognized mode is a shape this file cannot see a mock in.
    """
    mode = tool_entry.get('mode')
    if mode is None or mode == 'mock':
        return True
    if mode == 'real':
        return served or _resolve_read_strategy(tool_entry) == 'mocked'
    return False


def _check_identity_surfaces_are_mocked(config: dict[str, Any]) -> list[Finding]:
    """A descriptor that declares ``role: identity`` must have a mock behind it.

    Identity is mocked like every other dependency: the session a scenario starts from is
    SEEDED STATE and the token the agent holds is derived from a seeded user entity, so a run
    never needs a real credential. An identity surface left on the live provider is the one
    dependency the simulation cannot satisfy — the sign-in fails for want of a credential
    nobody can supply, every scenario dies on its first turn, and the run reports an agent that
    refuses to work rather than a mock that was never built.

    Refused (not warned) and refused HERE, because the two alternatives are worse: at setup the
    repair is one word in ``plurai.config.yaml``, while the same fault found later has already
    cost a generated environment, a generated scenario set and a scored run.
    """
    findings: list[Finding] = []
    served = _served_by_seam(config)
    for name, entry in _mapping_block(config, 'tools').items():
        if not isinstance(entry, dict) or not _declares_identity_role(entry):
            continue
        if _identity_surface_is_mocked(entry, served=name in served):
            continue
        mode = entry.get('mode')
        strategy = _resolve_read_strategy(entry)
        findings.append(Finding(
            severity='fail',
            code=Code.IDENTITY_SURFACE_NOT_MOCKED,
            message=(
                f'tool {name!r}: the descriptor declares `role: identity`, but nothing in this '
                f'config mocks it (mode: {mode!r}, effective read_strategy: {strategy!r}). The '
                f'run would need a REAL credential to get past the sign-in, which is the one '
                f'dependency a simulation cannot supply: every scenario dies on turn 1 and the '
                f'failure reads as an agent defect. Identity is mocked like every other '
                f'dependency — the session is seeded state and the token comes from a seeded '
                f'user entity. Serve it from the mocked world: `mode: mock` with a '
                f'@plurai.mock handler, or keep `mode: real` and name it in the `serves:` of '
                f"the seam mock its sign-in call goes through, so the tool's own body runs "
                f'over the mock.'
            ),
            skill_step='Step 8',
        ))
    return findings


def _same_or_nested(a: str, b: str) -> bool:
    """True when two dotted module paths are the same module or one contains the
    other (``our_backend.db`` ↔ ``our_backend.db.queries``). Segment-aware, so
    ``our_backend.dbutils`` is NOT nested under ``our_backend.db``."""
    return a == b or a.startswith(b + '.') or b.startswith(a + '.')


#: A stub entry may DECLARE that the module is a data store. The verdict below needs a
#: positive storage signal, and a sibling-package storage module (``our_backend.db``
#: beside ``our_backend.agents.customer.tools``) is named nowhere else in the config —
#: this is how the customer says so. Accepted spellings, because the key is written by
#: hand: ``storage: true``, or ``role``/``kind``/``layer`` naming a store.
_STORAGE_ROLE_KEYS = ('role', 'kind', 'layer')
_STORAGE_ROLE_VALUES = frozenset({'storage', 'store', 'database', 'db', 'persistence'})


def _declared_roles(entry: dict[str, Any]) -> list[str]:
    """The entry's non-empty ``role``/``kind``/``layer`` values, lowercased."""
    values = (str(entry.get(key) or '').strip().lower() for key in _STORAGE_ROLE_KEYS)
    return [value for value in values if value]


def _declares_storage_role(entry: dict[str, Any]) -> bool:
    """True when the entry says the module IS a data store (``storage: true`` / ``role: storage``)."""
    return entry.get('storage') is True or any(role in _STORAGE_ROLE_VALUES for role in _declared_roles(entry))


def _declares_non_storage_role(entry: dict[str, Any]) -> bool:
    """True when the entry ANSWERS the role question with something that is not a store
    (``role: telemetry``, ``storage: false``). The customer has already said what this
    module is, so the undeclared-role warning below has nothing left to ask."""
    return entry.get('storage') is False or any(role not in _STORAGE_ROLE_VALUES for role in _declared_roles(entry))


#: Dotted module paths inside free-form prose (``reads_from`` / ``writes_to``). Matching the
#: stub as a bare SUBSTRING re-opens the false positive from the other side: stub
#: ``our_backend.db`` matches "our_backend.db_archive (nightly rollup)". Tokens are compared
#: with :func:`_same_or_nested`, which is segment-aware.
_DOTTED_TOKEN_RE = re.compile(r'[A-Za-z_][\w.]*\.[\w.]+')


def _dotted_tokens(text: str) -> set[str]:
    return {token.rstrip('.') for token in _DOTTED_TOKEN_RE.findall(text)}


def _post_import_patch_targets(config: dict[str, Any]) -> set[str]:
    """Dotted module paths named by ``stubs.post_import_patches``.

    A patch target IS a storage signal: the customer already declared that this module
    must be replaced after import. Entries are accepted as a bare dotted string or as a
    mapping carrying ``module`` / ``target`` / ``path`` (the shape varies by author; an
    unrecognized entry contributes nothing rather than raising)."""
    targets: set[str] = set()
    for entry in _sequence_block(config, 'stubs', 'post_import_patches'):
        if isinstance(entry, str) and entry:
            targets.add(entry.split(':', 1)[0])
        elif isinstance(entry, dict):
            for key in ('module', 'target', 'path'):
                value = entry.get(key)
                if isinstance(value, str) and value:
                    targets.add(value.split(':', 1)[0])
    return targets


def _classify_storage_candidates(config: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Split the stub modules a ``mode: real`` reading tool might store through into
    ``(essential, unclassified)``.

    A stub module is **essential** when it shares a root package with a ``mode: real``
    tool whose effective ``read_strategy`` is ``mocked`` AND something in the config
    positively says the module is that tool's STORAGE. Both halves are required.

    Shared root package NARROWS the candidates — it handles the realistic layout where
    tools and storage live in SIBLING packages under a common root
    (``our_backend.agents.customer.tools`` and ``our_backend.db``), which a strict-prefix
    check would have falsely demanded the customer restructure. It is not the verdict:
    on its own it flagged ``agent.clients.consumption`` (env-var-configured telemetry) as
    essential storage purely because the customer's top-level package is named ``agent``,
    and the check then FAILED a correct config for not monkey-patching it.

    The positive signals, any one of which is enough:

    * a reading tool's ``original_module`` is the stub, or nested with it either way;
    * a reading tool's ``reads_from`` / ``writes_to`` prose NAMES the stub's dotted path
      (the prose is free-form — "Postgres orders table" — so a bare last segment is not
      matched, and only whole dotted segments count: ``our_backend.db_archive`` in the
      prose is not the stub ``our_backend.db``);
    * the stub is a ``stubs.post_import_patches`` target;
    * the stub entry declares a storage role (``storage: true`` / ``role: storage``).

    **unclassified** is the middle rung: shares the root, no positive signal, and the entry
    never says what the module IS. Dropping those silently is the worse trade — the sibling
    layout the docstring above calls realistic fails every signal, so a genuinely
    un-monkey-patched store would pass in silence. The caller warns on them and asks for the
    ``role:`` declaration that decides it. An entry declaring a NON-storage role has already
    answered, so it is neither.

    Fail-loud is preserved for genuine storage: every stub a reading tool points at, or
    that is declared/patched as a store, still requires its monkey-patch. What is gone is
    the FAIL verdict drawn from a shared package name alone.
    """
    stub_entries: list[dict[str, Any]] = [
        entry
        for entry in _sequence_block(config, 'stubs', 'modules')
        if isinstance(entry, dict) and isinstance(entry.get('module'), str) and entry['module']
    ]
    if not stub_entries:
        return set(), set()

    reading_tool_roots: set[str] = set()
    # Module paths the config points at as a reading tool's store: the tool's own module, the
    # dotted paths its reads_from/writes_to prose names, and the post_import_patches targets
    # folded in below. One set, because all three are the SAME test — :func:`_same_or_nested`.
    storage_signals: set[str] = set()
    for tool_entry in _mapping_block(config, 'tools').values():
        if not isinstance(tool_entry, dict):
            continue
        if tool_entry.get('mode') != 'real':
            continue
        if _resolve_read_strategy(tool_entry) != 'mocked':
            # Customer explicitly opted this tool's read out of mocking
            # (read_strategy: real for RAG, vector DBs, real reference data).
            # The tool's storage module — if any — should stay real.
            continue
        descriptor = _descriptor_block(tool_entry)
        original_module = descriptor.get('original_module')
        if not isinstance(original_module, str) or not original_module:
            continue
        reading_tool_roots.add(_root_package(original_module))
        storage_signals.add(original_module)
        for key in ('reads_from', 'writes_to'):
            text = descriptor.get(key)
            if isinstance(text, str) and text:
                storage_signals |= _dotted_tokens(text)

    if not reading_tool_roots:
        return set(), set()

    storage_signals |= _post_import_patch_targets(config)
    essential: set[str] = set()
    unclassified: set[str] = set()
    for entry in stub_entries:
        module = entry['module']
        if _root_package(module) not in reading_tool_roots:
            continue
        if any(_same_or_nested(module, named) for named in storage_signals) or _declares_storage_role(entry):
            essential.add(module)
        elif not _declares_non_storage_role(entry):
            unclassified.add(module)
    return essential, unclassified


def _essential_storage_modules(config: dict[str, Any]) -> set[str]:
    """The FAIL half of :func:`_classify_storage_candidates` — modules that must be
    monkey-patched."""
    return _classify_storage_candidates(config)[0]


def _check_essential_storage_declared(config: dict[str, Any]) -> list[Finding]:
    """A ``mode: real`` reading tool requires at least one ``stubs.modules`` entry.

    The fail case this catches: customer declares one or more ``mode: real``
    tools with non-empty ``reads_from`` but their ``stubs:`` block is
    empty (or absent). The env-generator has nothing to anchor faithful
    adapter generation against, and the customer hasn't told the verifier
    which production modules need to be replaced.

    We deliberately DON'T try to per-tool prove that a specific stub is
    "the right one" — Python module layouts vary wildly across customer
    repos and the heuristic for "this tool talks to that storage" can't
    win against custom factories, late imports, or DI containers. The
    coarser "have ANY stubs at all when you have ANY reading mode: real
    tool" check catches the realistic mistake (forgot to declare stubs
    entirely) without false-positives on legitimate sibling layouts.

    Per-stub coverage is enforced by the companion check
    ``_check_essential_storage_monkeypatched``, which narrows by shared root
    package and then requires a positive storage signal before naming a stub
    essential (see ``_essential_storage_modules``).
    """
    stub_modules = [
        entry['module']
        for entry in _sequence_block(config, 'stubs', 'modules')
        if isinstance(entry, dict) and isinstance(entry.get('module'), str) and entry['module']
    ]
    if stub_modules:
        return []

    findings: list[Finding] = []
    for tool_name, tool_entry in _mapping_block(config, 'tools').items():
        if not isinstance(tool_entry, dict):
            continue
        if tool_entry.get('mode') != 'real':
            continue
        if _resolve_read_strategy(tool_entry) != 'mocked':
            # Tool's read explicitly stays real (RAG, vector DB, real
            # reference data) — no stub needed for this tool's storage.
            continue
        reads_from = _descriptor_block(tool_entry).get('reads_from')
        findings.append(Finding(
            severity='fail',
            code=Code.ESSENTIAL_STORAGE_STUB_MISSING,
            message=(
                f'tool {tool_name!r} is mode=real with reads_from={reads_from!r} '
                f'and read_strategy=mocked (default), but stubs.modules is '
                f'empty. Either add the production storage module to '
                f'stubs.modules in plurai.config.yaml, or set '
                f'descriptor.read_strategy=real if the tool should hit the '
                f'real read source (RAG indexes, vector DBs, public reference '
                f'data).{_LEGACY_ROUTE_NOTE}'
            ),
            skill_step='Step 8',
        ))
        # One finding per missing-stubs config is enough; don't repeat per tool.
        return findings
    return findings


_PARSE_FAILED = object()

#: The essential-storage checks key on a `mode: real` reader over an effective `read_strategy: mocked`: the retired route.
#: They stay for configs written before it was retired, and say so.
_LEGACY_ROUTE_NOTE = (
    ' (This applies only to a tool on the retired `read_strategy: mocked` route; putting its store under a seam mock that '
    'names it in `serves:` — `world_served_without_mock` — retires this check.)'
)


def _simulate_monkeypatches_module(simulate_source: str, module_dotted: str) -> object | bool:
    """Detect whether simulate.py installs a monkey-patch on ``module_dotted``.

    Returns ``True`` / ``False`` for parseable input; returns the sentinel
    ``_PARSE_FAILED`` when ``simulate.py`` doesn't parse at all (so callers
    can surface that as a distinct warn finding rather than reporting a
    misleading "no monkey-patch" verdict on unparseable input).

    Recognized assignment shapes (all derived from import-induced aliases):

      import {module_dotted} as _alias
      _alias.factory = ...

      import {module_dotted}
      {module_dotted}.factory = ...     # ast.Attribute chain matching the dotted path

      from {parent} import {tail}
      {tail}.factory = ...

      sys.modules['{module_dotted}'] = ...     # whole-module substitution

    NON-recognized (deliberate):

      <root_package>.<unrelated_submodule>.x = ...  -- the root alone is
        not enough; we don't seed the bare root package name into the
        accepted alias set. That seeding produced false-positive PASSes
        when ``simulate.py`` patched a SIBLING submodule of the stub root.

      setattr(<alias>, ...)  -- valid Python but uncommon for storage
        replacement; the verifier failure message names attribute-assign
        as the canonical pattern so customers can adapt.

    The check uses AST rather than substring matching so commented-out
    examples in docstrings don't cause false positives.
    """
    try:
        tree = ast.parse(simulate_source)
    except SyntaxError:
        return _PARSE_FAILED

    # Aliases come strictly from import statements that name module_dotted
    # (or an ancestor that brings module_dotted into scope as a sub-attribute).
    aliases: set[str] = set()
    # If a customer wrote ``import our_backend.db`` we want to accept
    # ``our_backend.db.foo = ...`` as a valid patch. That requires
    # recognizing the dotted attribute chain {our_backend → .db → .foo}.
    # Store dotted aliases here so we can compare against attribute chains
    # rooted at any segment of the dotted import.
    dotted_aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module_dotted:
                    # ``import X.Y.Z`` binds ``X`` (unless aliased).
                    aliases.add(alias.asname or alias.name.split('.', 1)[0])
                    dotted_aliases.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                bound = alias.asname or alias.name
                full = f'{node.module}.{alias.name}'
                if full == module_dotted:
                    aliases.add(bound)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            # Shape 1: <alias>.attr = ... (Attribute target rooted at a Name we accept).
            if isinstance(target, ast.Attribute):
                root = _root_name_of_attribute(target)
                if root and root in aliases:
                    return True
                # Shape 2: <module_dotted>.attr = ... where the LHS is a
                # multi-segment Attribute chain matching one of our dotted
                # aliases. Reconstruct the prefix and compare.
                prefix = _attribute_chain_prefix(target)
                if prefix and any(
                    prefix == da or prefix.startswith(da + '.') for da in dotted_aliases
                ):
                    return True
            # Shape 3: sys.modules['<module_dotted>'] = ... or any longer dotted descendant.
            if _is_sys_modules_subscript_for(target, module_dotted):
                return True
    return False


def _attribute_chain_prefix(node: ast.expr) -> str | None:
    """Reconstruct the dotted prefix of an Attribute chain rooted at a Name.

    For ``a.b.c.d``, returns ``"a.b.c"`` (everything except the leaf
    attribute being assigned to). Returns ``None`` for chains rooted at
    non-Name expressions.
    """
    cur: ast.expr = node
    if not isinstance(cur, ast.Attribute):
        return None
    parts: list[str] = []
    cur = cur.value  # drop the leaf attribute (the one being assigned).
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    return '.'.join(reversed(parts))


def _is_sys_modules_subscript_for(target: ast.expr, module_dotted: str) -> bool:
    """True when ``target`` is ``sys.modules['<module_dotted>']`` (or a longer
    dotted descendant). Customers replacing the whole module via
    ``sys.modules[...] = MagicMock()`` is a recognized pattern.
    """
    if not isinstance(target, ast.Subscript):
        return False
    val = target.value
    if not (
        isinstance(val, ast.Attribute)
        and val.attr == 'modules'
        and isinstance(val.value, ast.Name)
        and val.value.id == 'sys'
    ):
        return False
    slice_node = target.slice
    if not (isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str)):
        return False
    key = slice_node.value
    return key == module_dotted or key.startswith(module_dotted + '.')


def _root_name_of_attribute(node: ast.expr) -> str | None:
    """Walk an ``ast.Attribute`` chain to its root ``ast.Name`` and return
    that name. Returns ``None`` for non-Name roots (calls, subscripts, etc.)."""
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        cur = cur.value
    if isinstance(cur, ast.Name):
        return cur.id
    return None


def _check_essential_storage_monkeypatched(
    config: dict[str, Any], simulate_source: str | None,
) -> list[Finding]:
    """For every essential storage backing a ``mode: real`` tool, ``simulate.py``
    MUST monkey-patch the storage factory (or substitute the module via
    ``sys.modules[...] = ...``).

    Without the patch, the customer's real tool body runs against the
    production storage — either hitting prod (bad) or crashing on missing
    credentials (also bad). The env-generator produces the adapter; the
    plugin's simulate.py is responsible for installing it.

    Non-essential storages don't need a faithful adapter — they're handled
    by the existing ``sys.modules[...] = MagicMock()`` substitution at
    Phase 1 of simulate.py.

    An UNCLASSIFIED candidate (shares the reading tool's root package, nothing in the
    config says whether it is that tool's store) that is also unpatched gets a WARN, not
    silence: the sibling-package layout fails every positive signal, so a real store would
    otherwise pass unremarked. The warning names the module and the ``role:`` key that
    settles it.

    When ``simulate.py`` doesn't parse, emits a single
    ``simulate_unparseable`` warn finding and short-circuits the
    per-storage check — telling the customer the verifier couldn't read
    their file beats reporting fake "no monkey-patch" findings about
    storages it never had a chance to inspect.
    """
    if simulate_source is None:
        return []
    essential, unclassified = _classify_storage_candidates(config)
    graded = [(m, 'fail') for m in sorted(essential)] + [(m, 'warn') for m in sorted(unclassified)]
    findings: list[Finding] = []
    for module, severity in graded:
        result = _simulate_monkeypatches_module(simulate_source, module)
        if result is _PARSE_FAILED:
            return [Finding(
                severity='warn',
                code=Code.SIMULATE_UNPARSEABLE,
                message=(
                    'simulate.py does not parse; the essential-storage '
                    'monkey-patch check did not run. Fix the syntax error '
                    'first (verify_simulate emits the underlying parse '
                    'error), then re-run verify_config.'
                ),
                skill_step='Step 8',
            )]
        if result:
            continue
        if severity == 'warn':
            findings.append(Finding(
                severity='warn',
                code=Code.STORAGE_ROLE_UNDECLARED,
                message=(
                    f'stub module {module!r} shares a package root with a mode=real tool but '
                    f'nothing in the config says whether it is that tool\'s STORAGE, and '
                    f'simulate.py installs no monkey-patch for it. If it IS the store, declare '
                    f'``role: storage`` on its ``stubs.modules`` entry and install the '
                    f'env-generator\'s storage_adapter.py (see the Python sub-skill\'s '
                    f'"Phase 3b: Storage-layer monkey-patch"). If it is not, declare its actual '
                    f'role (e.g. ``role: telemetry``) to silence this.{_LEGACY_ROUTE_NOTE}'
                ),
                skill_step='Step 8',
            ))
        else:
            findings.append(Finding(
                severity='fail',
                code=Code.ESSENTIAL_STORAGE_NOT_MONKEYPATCHED,
                message=(
                    f'storage module {module!r} is essential (a mode=real '
                    f'tool reads from it) but simulate.py does not install '
                    f'any monkey-patch for it. The env-generator\'s '
                    f'storage_adapter.py must be installed in place of the '
                    f'production storage factory — see the Python sub-skill\'s '
                    f'"Phase 3b: Storage-layer monkey-patch" section. '
                    f'Recognized patterns: ``import {module} as _x; _x.factory = ...``, '
                    f'``import {module}; {module}.factory = ...``, '
                    f'``sys.modules[{module!r}] = ...``.{_LEGACY_ROUTE_NOTE}'
                ),
                skill_step='Phase 3b: Storage-layer monkey-patch',
            ))
    return findings


def _find_mock_function(tree: ast.Module, tool_name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Find ``def mock_<tool>(...)`` or any function decorated with
    ``@plurai.mock(<tool>)`` — handles both positional and ``name=`` kwarg
    decorator shapes via :func:`_decorator_tool_name`.
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if _decorator_tool_name(deco) == tool_name:
                return node
        # Naming convention fallback: ``def mock_<tool>``.
        if node.name == f'mock_{tool_name}':
            return node
    return None


#: The checks that cannot reach a verdict until the harness has been READ — every one of them
#: returns `[]` on a `None` source. DECLARED as one tuple because the finding that reports the
#: gap NAMES this set: spelling the names in the message instead would leave a sixth gated check
#: un-named by the very finding that exists to name it. `tests/test_verify_config.py` pins the
#: tuple equal to the set of checks `verify()` below hands a harness source to, by parsing
#: `verify()` — so the two cannot drift in either direction.
_HARNESS_GATED_CHECKS: tuple[Callable[..., list[Finding]], ...] = (
    _check_shape_declaration,
    _check_essential_storage_monkeypatched,
    _check_tool_name_consistency,
    _check_tools_wrapped,
    _check_descriptor_returns_fields_present_for_state_setters,
    _check_inline_actions_have_a_hook,
    _check_deliveries_reach_the_world,
    _check_world_snapshot_merge,
    _check_world_reset_unconfirmed,
    _check_guards_see_mock,
    _check_read_through_bound,
)

#: Every code a check in `_HARNESS_GATED_CHECKS` (or a helper it calls) can emit: the gates that did not
#: run when no harness source was read, so that run neither applies nor reports stale an overrule of
#: one. It names exactly the codes those functions emit.
_HARNESS_GATED_CODES: frozenset[Code] = frozenset({
    Code.DELIVERY_SURFACE_NO_WRITER,
    Code.DELIVERY_SURFACE_UNDETERMINED,
    Code.DESCRIPTOR_RETURNS_STATE_FIELD_NOT_IN_MOCK_BODY,
    Code.ESSENTIAL_STORAGE_NOT_MONKEYPATCHED,
    Code.GUARD_SEES_MOCK,
    Code.INLINE_ACTION_WITHOUT_HOOK,
    Code.READ_THROUGH_SWALLOWED,
    Code.READ_THROUGH_UNBOUND,
    Code.SHAPE_ABSENT_BOTH_HOOKS,
    Code.SHAPE_ABSENT_PIPELINE,
    Code.SHAPE_CONTRADICTS_HOOKS,
    Code.SHAPE_DECLARED_NOT_AT_ROOT,
    Code.SHAPE_VALUE,
    Code.SIMULATE_UNPARSEABLE,
    Code.STORAGE_ROLE_UNDECLARED,
    Code.TOOLS_NOT_WRAPPED,
    Code.TOOL_IN_CONFIG_MISSING_FROM_SIMULATE,
    Code.TOOL_IN_SIMULATE_MISSING_FROM_CONFIG,
    Code.WORLD_RESET_UNCONFIRMED,
    Code.WORLD_SNAPSHOT_MERGES_MOCK,
})


def verify(
    config_path: Path,
    simulate_path: Path | None = None,
) -> list[Finding]:
    """Run every check; return concatenated findings.

    When ``simulate_path`` is given and the customer split simulate.py into
    helpers (``_storage.py`` / ``_mocks.py`` per Step 8.4), the verifier
    auto-discovers those modules via ``_discover_sources``. Each file is
    parsed independently: helpers that fail to read/parse produce a
    distinct warn finding (``helper_unparseable`` / ``helper_unreadable``,
    naming the offending file) and are EXCLUDED from the concatenated
    source the AST checks see. A broken helper no longer cascades into
    a wall of false-positive tool-name fails.

    THE RETURNED LIST DESCRIBES WHAT WAS MEASURED, not only what was wrong.
    When no harness source came back from that discovery — no path, a path
    that is not a file, or nothing in it parsed — every check in
    :data:`_HARNESS_GATED_CHECKS` stands down, so the gap is REPORTED
    (``harness_unknowable``, warn) rather than passed over as a clean run.
    """
    config = _load_yaml(config_path)
    simulate_source: str | None = None
    simulate_only_source: str | None = None
    discovery_findings: list[Finding] = []
    if simulate_path is not None and simulate_path.is_file():
        sources, discovery_findings = _discover_sources(simulate_path)
        # Concat the successfully-parsed sources. The order (simulate.py
        # first, then helpers) is preserved by _discover_sources so any
        # check that walks the merged AST encounters simulate.py first.
        usable_texts = [s.text for s in sources if s.tree is not None]
        if usable_texts:
            simulate_source = '\n'.join(usable_texts)
        # …and simulate.py ALONE, for the one check that asks what the imported MODULE exports.
        # The runner imports simulate.py and getattrs the hook off it; a hook defined in a split
        # helper that simulate.py never imports is not an attribute of that module, and the
        # merged text would say it is.
        simulate_only_source = next(
            (s.text for s in sources if s.role == 'simulate' and s.tree is not None), None
        )

    findings: list[Finding] = list(discovery_findings)
    if simulate_source is None:
        # A VERDICT MUST DESCRIBE WHAT IT MEASURED. Every check in `_HARNESS_GATED_CHECKS`
        # returns [] without a harness source, so an empty list here is "nothing was checked",
        # not "nothing is wrong" — and `main()` publishes the contract-clean line on exactly
        # that emptiness. Same rule and same shape as DESCRIPTOR_SCHEMA_CHECK_SKIPPED.
        #
        # Keyed on the SOURCE and placed AFTER the discovery block, never on `simulate_path`:
        # `--simulate <a path that is not a file>` and `--simulate <a directory>` both reach
        # here, and those are the worse legs — the operator named a path and believes the
        # cross-document checks ran.
        stood_down = ', '.join(sorted(c.__name__.removeprefix('_check_') for c in _HARNESS_GATED_CHECKS))
        findings.append(Finding(
            severity='warn',
            code=Code.HARNESS_UNKNOWABLE,
            message=(
                f'No harness source was read, so the {len(_HARNESS_GATED_CHECKS)} checks that '
                f'compare this config against the harness did not run: {stood_down}. This '
                f'verdict was measured on the config alone — a bench the runner cannot '
                f'dispatch, or whose tool names do not match its harness, would not have been '
                f'reported here. Pass `--simulate <the harness this config describes>` to run '
                f'them. A bench whose harness this verifier cannot parse has no source for them '
                f'to read, and they stay dark on it by construction.'
            ),
            skill_step='Step 8',
        ))
    findings.extend(_check_block_shapes(config))
    findings.extend(_check_native_tools_framework(config))
    findings.extend(_check_root_keys(config))
    findings.extend(_check_workspace_block(config))
    findings.extend(_check_shape_declaration(config, simulate_only_source))
    findings.extend(_check_world_state_owner(config))
    findings.extend(_check_world_state_slots(config))
    findings.extend(_check_harness_single_instance(config))
    findings.extend(_check_world_real_writer_unowned(config))
    findings.extend(_check_world_shared_ok(config))
    findings.extend(_check_world_field_required(config))
    findings.extend(_check_world_id_fields(config))
    findings.extend(_check_world_rows_typed(config))
    findings.extend(_check_structured_context_typed(config))
    findings.extend(_check_world_checks(config, _python_source_bases(config_path)))
    findings.extend(_check_reference_store_sweep(config, config_path))
    findings.extend(_check_user_surfaces(config))
    findings.extend(_check_agent_host_declares_user_surfaces(config, route=_declared_route(config_path.parent)))
    findings.extend(_check_agent_host_declares_render_actions(config, route=_declared_route(config_path.parent)))
    findings.extend(_check_render_actions_declared(config, route=_declared_route(config_path.parent)))
    findings.extend(_check_render_actions(config))
    findings.extend(_check_agent_io(config))
    findings.extend(_check_file_input_has_document_field(config, config_path.parent))
    findings.extend(_check_handover_declares_document_kinds(config, config_path.parent))
    findings.extend(_check_closed_class_detectors(config))
    findings.extend(_check_knowledge_block(config))
    findings.extend(_check_descriptor_schema(config))
    findings.extend(_check_descriptor_bare_string_fields(config))
    findings.extend(_check_tool_state_updates(config))
    findings.extend(_check_tool_requires_confirmation(config))
    findings.extend(_check_tool_invocation(config))
    findings.extend(_check_tool_error_channel(config))
    findings.extend(_check_tool_seams(config))
    findings.extend(_check_payload_codecs(config))
    findings.extend(_check_guards(config))
    findings.extend(_check_tool_exposed_in(config))
    findings.extend(_check_descriptor_role_vocabulary(config))
    findings.extend(_check_identity_surfaces_are_mocked(config))
    findings.extend(_check_write_tools_have_post_conditions(config))
    findings.extend(_check_strategy_without_mock(config))
    findings.extend(_check_world_served_without_mock(config))
    findings.extend(_check_writer_readers(config))
    findings.extend(_check_read_through(config))
    workdir = config_path.parent
    findings.extend(_check_seam_below_installed_tool(config, workdir))
    findings.extend(_check_store_confirmation(config, workdir, _read_topology(workdir)))
    findings.extend(_check_host_effects_granted(config, workdir, route=_declared_route(workdir), topology=_read_topology(workdir)))
    findings.extend(_check_subagent_calls(config, _read_topology(workdir)))
    findings.extend(_check_agent_tools_declared(config, _read_topology(workdir)))
    findings.extend(_check_map_status_matches_mode(config, _read_topology(workdir)))
    findings.extend(_check_input_file_channel_declared(config, workdir, _read_topology(workdir)))
    findings.extend(_check_descriptor_return_types(config, _python_source_bases(config_path)))
    findings.extend(_check_row_sources_cited(config, _python_source_bases(config_path)))
    rule_scopes = _rule_scopes(config, _python_source_bases(config_path))
    findings.extend(_check_rule_scope_cited(rule_scopes))
    findings.extend(_check_rule_tests_cover_variants(rule_scopes, workdir))
    findings.extend(_check_guard_fields_tested(config, workdir))
    findings.extend(_check_fixture_world_keys(config, workdir))
    findings.extend(_check_field_presence_on_nullable_args(config))
    findings.extend(_check_essential_storage_declared(config))
    findings.extend(_check_essential_storage_monkeypatched(config, simulate_source))
    findings.extend(_check_tool_name_consistency(config, simulate_source))
    findings.extend(_check_tools_wrapped(config, simulate_source, harness_dir=simulate_path.parent if simulate_path is not None else None))
    findings.extend(_check_descriptor_returns_fields_present_for_state_setters(config, simulate_source))
    findings.extend(_check_inline_actions_have_a_hook(config, simulate_only_source))
    findings.extend(_check_deliveries_reach_the_world(config, simulate_source, simulate_only_source))
    findings.extend(_check_world_owned_types(config))
    findings.extend(_check_world_snapshot_merge(config, simulate_source, simulate_only_source))
    findings.extend(_check_world_reset_unconfirmed(config, simulate_only_source))
    findings.extend(_check_guards_see_mock(config, simulate_source, harness_dir=simulate_path.parent if simulate_path is not None else None))
    findings.extend(_check_read_through_bound(config, simulate_source, harness_dir=simulate_path.parent if simulate_path is not None else None))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Verify a plurai.config.yaml against the backend mock-generator contract.',
    )
    parser.add_argument('config', type=Path, help='Path to plurai.config.yaml')
    parser.add_argument(
        '--simulate',
        type=Path,
        default=None,
        help='Optional simulate.py path for cross-document checks.',
    )
    parser.add_argument(
        '--findings-out',
        type=Path,
        default=None,
        help='Write every gate finding this run minted, as JSON, to this path (never printed on stdout).',
    )
    args = parser.parse_args(argv)

    if not args.config.is_file():
        print(f'verify_config: not a file: {args.config}', file=sys.stderr)
        return 2

    try:
        findings = verify(args.config, args.simulate)
    except ValueError as exc:
        print(f'verify_config: {exc}', file=sys.stderr)
        return 2

    fails = [f for f in findings if f.severity == 'fail']
    warns = [f for f in findings if f.severity == 'warn']
    try:
        harness_read = not any(f.code == Code.HARNESS_UNKNOWABLE for f in findings)
        applied, lines = gate_findings(fails, args.config, args.simulate, harness_read=harness_read, findings_out=args.findings_out)
    except (OSError, ValueError) as exc:
        print(f'verify_config: the gate ledger could not be read or the findings written ({type(exc).__name__}: {exc})', file=sys.stderr)
        return 2

    if not findings:
        print(f'verify_config: ✓ {args.config} satisfies the backend contract.')
        return 0

    print(f'verify_config: {args.config}')
    for line in lines:
        print(f'  {line}')
    if fails:
        print(f'  {len(fails)} contract violation(s):')
        for f in fails:
            print(f.render())
    if warns:
        print(f'  {len(warns)} warning(s):')
        for f in warns:
            print(f.render())
    return 1 if applied.holding else 0


def gate_findings(
    fails: list[Finding], config_path: Path, simulate_path: Path | None, *, harness_read: bool, findings_out: Path | None,
) -> tuple[Any, list[str]]:
    """Each `fail` as a gate finding, settled against the plugin's ledger beside this workdir.

    A `fail` is a gate: the catalogue in `scripts/gate_ledger.py` decides whether it is security, invariant
    or advisory, and an advisory one HOLDS the exit code until it is repaired or the plugin overrules it in
    `<workdir>/artifacts/.gate_overrules.jsonl`. A `warn` is no gate and never holds. The fingerprint covers
    the code, the file and the message; the line number is `observed`, so an overrule survives an edit
    elsewhere in the file and lapses the moment the finding's own text changes.

    `harness_read` is False when no harness source was read: the checks in `_HARNESS_GATED_CHECKS` did not
    run, so their codes are left out of what this call evaluated.

    Returns the ledger's `Applied` (a non-empty `holding` is exit 1) and the `finding:` lines to print.
    """
    _plugin_scripts_on_path('scripts')
    import gate_ledger  # noqa: PLC0415 - the plugin's one finding contract, resolved from this plugin's root

    harness = simulate_path.name if simulate_path is not None else 'simulate.py'
    minted = [
        gate_ledger.make_finding(
            f'plugin.verify_config.{f.code}',
            subject=config_path.name,
            evidence={'code': str(f.code), 'file': f.filename or (harness if f.line is not None else config_path.name), 'message': f.message},
            observed={'line': f.line},
            remedy=f'the [{f.code}] fix in SKILL.md {f.skill_step}',
            draft_ref=None,
        )
        for f in fails
    ]
    evaluated = ('plugin.verify_config.',) if harness_read else tuple(f'plugin.verify_config.{c}' for c in Code if c not in _HARNESS_GATED_CODES)
    return gate_ledger.settle(minted, art=config_path.parent / 'artifacts', evaluated=evaluated, findings_out=findings_out)


if __name__ == '__main__':
    _plugin_scripts_on_path('scripts')
    from _stdio import utf8_stdio  # noqa: PLC0415 - sibling script: UTF-8 standard streams on Windows

    utf8_stdio()
    sys.exit(main())
