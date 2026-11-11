#!/usr/bin/env python3
"""The plugin's own gate findings, their catalogue, and the ledger of the plugin's overrules.

    gate_ledger.py overrule --art "$ART" --gate-id <id> --fingerprint <sha256> --reason "<what you verified>" [--finding-from <findings.json>]
    gate_ledger.py list     --art "$ART"
    gate_ledger.py export   --art "$ART" --scope sdk --out <path>
    gate_ledger.py summary  --art "$ART" --out <path>

A plugin gate (a verifier code, a smoke category, a run-gate reason, a backend-identity split, a corpus
shortfall, …) reports through ONE record, :class:`GateFinding` — the same fields as the backend's
``GateFinding``, in stdlib form, because the scripts that mint plugin findings
run under the tooling interpreter, which only has to import PyYAML. Who may lift a finding is never the
emitting site's choice: :func:`make_finding` resolves ``authority`` and ``holds`` from the catalogue below.

* ``security`` / ``invariant`` — no overrule lifts it. An overrule naming one is refused when it is
  written (``overrule_refused``) and ignored by :func:`apply` if it reaches the ledger another way.
* ``advisory`` — everything else. It HOLDS its effect (the check's exit code) until the plugin repairs the
  cause or overrules it, unless the id is in :data:`NON_HOLDING`, which only informs.

THE LEDGER is ``$ART/.gate_overrules.jsonl`` (:func:`ledger_path`): append-only, one JSON line per decision,

    {"overrule": {"gate_id", "fingerprint", "reason"}, "finding": <GateFinding> | null, "at", "plugin_version", "session"}

It sits beside ``verification.json`` and, unlike it, is never committed: the managed ``.gitignore`` block
ignores every ``artifacts/`` entry it does not name and every dotfile under ``plurai_sim/``. The plugin
writes it alone, with ``overrule``; the user is never asked about a finding or an overrule, and no
customer-facing report reads this file. Lines naming a gate outside the ``plugin.`` namespace are the
runner's and the backend's: ``export --scope sdk`` projects them as the runner's overrule list, and
``plugin.`` ids never leave the machine except as the scrubbed ``summary``.

An overrule binds to a fingerprint — sha256 over ``(gate_id, subject, evidence)`` — so it lapses the
moment the evidence it was judged on changes (:func:`apply` reports it ``stale``). ``observed`` carries
the volatile context (line numbers, stderr tails, values) and never moves the fingerprint.

Exit codes: 0 done · 2 refused (``overrule_refused`` for a security/invariant id, a reason under
:data:`OVERRULE_REASON_MIN_CHARS` characters, a gate id that is not dotted lower-case (``bad_gate_id``), an
undeclared ``plugin.`` id, a malformed fingerprint, a
``--finding-from`` that carries no such finding), or the ledger could not be read or written.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import re
import sys
import unicodedata
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _confine  # noqa: E402

# ---------------------------------------------------------------------------
# the contract (field names and canonical form follow the backend's gate contract)
# ---------------------------------------------------------------------------

SECURITY: Final[str] = 'security'
INVARIANT: Final[str] = 'invariant'
ADVISORY: Final[str] = 'advisory'
AUTHORITIES: Final[tuple[str, ...]] = (SECURITY, INVARIANT, ADVISORY)
HARD_AUTHORITIES: Final[frozenset[str]] = frozenset({SECURITY, INVARIANT})

#: Every plugin gate id starts with this; an id without it belongs to the runner or the backend.
PLUGIN_PREFIX: Final[str] = 'plugin.'

#: The shortest overrule reason accepted, counted in characters after ``strip()`` (the backend's floor).
OVERRULE_REASON_MIN_CHARS: Final[int] = 20

#: The canonical form :func:`fingerprint` hashes (the backend's ``FINGERPRINT_CANON_VERSION``).
FINGERPRINT_CANON_VERSION: Final[int] = 1

LEDGER_FILENAME: Final[str] = '.gate_overrules.jsonl'

_FINGERPRINT_RE = re.compile(r'[0-9a-f]{64}')
#: Every gate id, plugin or not: the SDK's ``--overrule`` loader refuses any other spelling, and it reads
#: this whole ledger, so one such line would refuse every later overrule along with it.
_GATE_ID_RE = re.compile(r'[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+')
_GATE_SEGMENT_RE = re.compile(r'[a-z][a-z0-9_]*')


class UnknownGateError(ValueError):
    """A ``plugin.`` id the catalogue does not declare, or an id outside the ``plugin.`` namespace."""


class EvidenceError(ValueError):
    """Evidence with no stable canonical form (a float, a non-string key, a value JSON cannot carry), or
    whose keys are not exactly the gate's declared evidence keys."""


class LedgerError(ValueError):
    """A ledger line that is not an overrule record. Raised, never skipped: a dropped line is a silent hold."""


@dataclass(frozen=True)
class HardGate:
    """A security or invariant gate: no overrule lifts it, and ``why`` says what would break or leak."""

    authority: str
    why: str

    def __post_init__(self) -> None:
        if self.authority not in HARD_AUTHORITIES:
            raise ValueError(f'a hard gate is security or invariant, not {self.authority!r}.')
        if not self.why.strip():
            raise ValueError('a hard gate must say why it is hard.')


@dataclass(frozen=True)
class GateFinding:
    """What one plugin gate saw about one subject. Build it with :func:`make_finding`, never by hand."""

    gate_id: str
    authority: str
    #: An advisory finding that holds its effect until the plugin overrules it. Always False when hard.
    holds: bool
    #: What it is about: a file, a check id, a tool, a corpus, a document.
    subject: str
    #: The stable facts the gate decided on — exactly the gate's declared evidence keys; fingerprinted.
    evidence: dict[str, Any]
    #: Volatile context (line numbers, values, output tails). Never fingerprinted.
    observed: dict[str, Any]
    #: What would make the gate pass.
    remedy: str
    #: The id of the work the gate held back, when it held some back.
    draft_ref: str | None
    fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GateFinding:
        """A finding as :meth:`as_dict` (and ``--findings-out``) wrote it; any other key set raises ValueError."""
        names = {f.name for f in dataclasses.fields(cls)}
        if set(data) != names:
            raise ValueError(f'not a gate finding: keys {sorted(data)} differ from {sorted(names)}.')
        return cls(**{name: data[name] for name in names})


@dataclass(frozen=True)
class Overrule:
    """The plugin lifting ONE finding: the gate id and the fingerprint of exactly the evidence it judged."""

    gate_id: str
    fingerprint: str
    #: What the plugin verified, citing the customer repo (file:line, a smoke trail, a diff). Internal.
    reason: str


@dataclass(frozen=True)
class LedgerLine:
    overrule: Overrule
    #: The finding as the plugin judged it, when the overrule was written from a findings file.
    finding: dict[str, Any] | None
    at: str
    plugin_version: str
    session: str

    def as_dict(self) -> dict[str, Any]:
        return {
            'overrule': dataclasses.asdict(self.overrule),
            'finding': self.finding,
            'at': self.at,
            'plugin_version': self.plugin_version,
            'session': self.session,
        }


@dataclass(frozen=True)
class Applied:
    """What the ledger does to one call's findings.

    * ``holding`` — what still holds the effect: every hard finding, and every holding advisory finding
      no ledger line lifts. Non-empty ⇒ the check's exit code says "something holds".
    * ``overruled`` — holding advisory findings a ledger line lifted.
    * ``stale`` — overrules whose gate ran in this call and match no current finding: the evidence they
      were written for has changed (or never held).
    * ``not_holding`` — overrules matching a current finding that holds nothing: there was no hold to lift.
    """

    holding: tuple[GateFinding, ...]
    overruled: tuple[GateFinding, ...]
    stale: tuple[Overrule, ...]
    not_holding: tuple[Overrule, ...]


# ---------------------------------------------------------------------------
# the catalogue
# ---------------------------------------------------------------------------

#: The evidence a verifier finding is fingerprinted on. The line number lives in ``observed``.
VERIFIER_EVIDENCE: Final[frozenset[str]] = frozenset({'code', 'file', 'message'})

#: A family's members are the closed id set its script declares (its ``Code`` / ``Category`` enum), so they
#: are not restated here one by one: ``<prefix><member>`` is declared for any snake_case member, and
#: ``scripts/tests/test_gate_ledger.py`` binds every catalogued member below to that script's enum.
FAMILIES: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        'plugin.verify_config.': VERIFIER_EVIDENCE,
        'plugin.verify_simulate.': VERIFIER_EVIDENCE,
        'plugin.smoke.': VERIFIER_EVIDENCE,
    }
)

#: A blocking harness smoke that ran and reached no verdict.
RUN_GATE_SMOKE_UNPROVEN: Final[str] = 'plugin.run_gate.smoke_unproven'
#: A declared route smoke the operator has not granted.
RUN_GATE_ROUTE_UNGRANTED: Final[str] = 'plugin.run_gate.route_ungranted'
#: A host-toolchain declaration that fails its own validation.
RUN_GATE_HOST_DECLARATION_INVALID: Final[str] = 'plugin.run_gate.host_declaration_invalid'
#: A declared host requirement this machine lacks, or has broken, or that fails under the sandbox.
RUN_GATE_HOST_GAP: Final[str] = 'plugin.run_gate.host_gap'
#: A declared host requirement no probe could confirm or that was not checked: nothing measured it missing.
RUN_GATE_HOST_UNVERIFIED: Final[str] = 'plugin.run_gate.host_unverified'
#: A tool the bench runs for real whose `kind: live` check, re-run before the run, did not read its service.
RUN_GATE_LIVE_CHECK_FAILED: Final[str] = 'plugin.run_gate.live_check_failed'
#: The backend answers for another organization or workspace than the one armed.
BACKEND_IDENTITY_TENANT_DIFFERS: Final[str] = 'plugin.backend_identity.tenant_differs'
#: The backend reads and writes another artifact store than the one armed.
BACKEND_IDENTITY_STORAGE_DIFFERS: Final[str] = 'plugin.backend_identity.storage_differs'
#: The backend is another build or source of the same tenant and store, or another host no tenant ties to it.
BACKEND_IDENTITY_DEPLOYMENT_DIFFERS: Final[str] = 'plugin.backend_identity.deployment_differs'
#: The built corpus landed fewer units than the declared scope.
CORPUS_SHORTFALL: Final[str] = 'plugin.corpus.shortfall'
#: Declared sources the corpus build did not ingest.
CORPUS_SOURCE_NOT_INGESTED: Final[str] = 'plugin.corpus.source_not_ingested'
#: The harness bytes moved since the recording the eval lane is pinned to.
LANE_HARNESS_MOVED: Final[str] = 'plugin.lane.harness_moved'
#: A regeneration that would not re-send an input document the previous generation was given.
REGEN_INPUT_DOC_DROPPED: Final[str] = 'plugin.regen.input_doc_dropped'
#: Names the run dispatched that the config's verb surface does not carry: not invented (the harness never refused
#: them by name), so they leave dead/unproven tools without blame and never reach the customer's report.
RUN_VERB_SURFACE_UNMATCHED: Final[str] = 'plugin.run.verb_surface_unmatched'
#: A target ruling that would rule out a subject a `potential` agent finding names (`record_target_rulings.py`).
RULINGS_POTENTIAL_SUBJECT: Final[str] = 'plugin.rulings.potential_subject'
#: A finding `lint_prd.py` leaves open on the PRD. The text is the evidence, so an acceptance lapses when the finding is reworded.
PRD_LINT_OPEN_FINDING: Final[str] = 'plugin.prd_lint.open_finding'

#: Every id outside a family, with the evidence keys its fingerprint covers.
GATES: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        RUN_GATE_SMOKE_UNPROVEN: frozenset({'check_id', 'signature', 'harness', 'sdk', 'cause'}),
        RUN_GATE_ROUTE_UNGRANTED: frozenset({'grant'}),
        RUN_GATE_HOST_DECLARATION_INVALID: frozenset({'declaration'}),
        RUN_GATE_HOST_GAP: frozenset({'requirement', 'status'}),
        RUN_GATE_HOST_UNVERIFIED: frozenset({'requirement', 'status'}),
        RUN_GATE_LIVE_CHECK_FAILED: frozenset({'check_id', 'cause'}),
        BACKEND_IDENTITY_TENANT_DIFFERS: frozenset({'differing', 'mcp_workspace_id', 'mcp_storage_root'}),
        BACKEND_IDENTITY_STORAGE_DIFFERS: frozenset({'differing', 'mcp_workspace_id', 'mcp_storage_root'}),
        BACKEND_IDENTITY_DEPLOYMENT_DIFFERS: frozenset({'differing', 'mcp_workspace_id', 'mcp_storage_root'}),
        CORPUS_SHORTFALL: frozenset({'declared_units', 'landed', 'not_ingested_reasons', 'unaccounted_paths', 'not_ingested_paths'}),
        CORPUS_SOURCE_NOT_INGESTED: frozenset({'declared_units', 'landed', 'not_ingested_reasons', 'unaccounted_paths', 'not_ingested_paths'}),
        LANE_HARNESS_MOVED: frozenset({'axis', 'pinned', 'current'}),
        REGEN_INPUT_DOC_DROPPED: frozenset({'document'}),
        RUN_VERB_SURFACE_UNMATCHED: frozenset({'verbs'}),
        RULINGS_POTENTIAL_SUBJECT: frozenset({'finding', 'subject', 'ruling'}),
        PRD_LINT_OPEN_FINDING: frozenset({'finding'}),
    }
)

_PARSES = 'the runner or the backend parses this shape and would crash on it, or drop it without a word'
_CONSENT = "consent is the operator's record; an overrule by the plugin must never stand in for it"

#: The only gates no overrule lifts. Every declared id not named here is advisory.
PLUGIN_HARD: Final[Mapping[str, HardGate]] = MappingProxyType(
    {
        # --- verify_config: the shapes the runner and the backend parse --------------------------------
        **{
            f'plugin.verify_config.{code}': HardGate(INVARIANT, _PARSES)
            for code in (
                'block_shape',
                'workspace_shape',
                'invalid_run_driver',
                'world_owner_value',
                'world_slots_value',
                'world_shared_ok_value',
                'shape_value',
                'shape_declared_not_at_root',
                'shape_absent_pipeline',
                'shape_contradicts_hooks',
                'render_actions_shape',
                'agent_io_shape',
                'agent_io_input_shape',
                'agent_io_may_open_shape',
                'agent_io_opens_shape',
                'agent_io_declared_not_at_root',
                'agent_io_channels_shape',
                'agent_io_channel_kind',
                'tool_requires_confirmation_invalid',
                'tool_invocation_invalid',
                'tool_exposed_in_invalid',
                'tool_state_updates_invalid',
                'descriptor_role_unknown',
                'descriptor_schema_invalid',
                'closed_class_detectors_shape',
                'world_id_field_unknown',
                'user_surfaces_invalid',
                'agent_host_user_surfaces_undeclared',
                'agent_host_render_actions_undeclared',
                'harness_single_instance_invalid',
            )
        },
        # --- verify_config: consent ---------------------------------------------------------------------
        'plugin.verify_config.live_write_ungranted': HardGate(SECURITY, _CONSENT),
        'plugin.verify_config.host_effect_ungranted': HardGate(SECURITY, _CONSENT),
        'plugin.verify_config.tool_unclassified': HardGate(
            SECURITY, 'an unclassified tool has no declared live/mock posture, so nothing says the run may reach it'
        ),
        # --- a store is real or simulated as a whole, and real only on the user's confirmation --------
        **{
            f'plugin.verify_config.{code}': HardGate(SECURITY, "a store the user did not confirm is simulated; " + _CONSENT)
            for code in ('world_confirmation_invalid', 'real_read_store_unconfirmed', 'live_read_unnamed')
        },
        'plugin.verify_config.index_over_simulated_records': HardGate(
            INVARIANT, 'a live index over records each scenario seeds returns rows the world does not hold, so the store must be one side whole'
        ),
        'plugin.verify_config.writer_reader_mode_split': HardGate(
            INVARIANT, 'a real read of a store whose writes are mocked reads pre-write state, so the store must be one side whole'
        ),
        **{
            f'plugin.{code}': HardGate(SECURITY, 'a simulated store or vendor reached through an unpinned endpoint is the real one')
            for code in ('verify_config.endpoints_undeclared', 'verify_simulate.endpoint_env_unpinned', 'verify_simulate.agent_env_unreachable')
        },
        'plugin.verify_simulate.agent_fake_unrouted': HardGate(
            INVARIANT, "a vendor double not served by the reference helper in seed_state is not this conversation's own vendor"
        ),
        'plugin.smoke.confirmed_store_seed_readback_gap': HardGate(
            INVARIANT, 'a confirmed live store not read back as seeded runs every scenario on the wrong world'
        ),
        # --- verify_config: the world the run reads --------------------------------------------------------
        'plugin.verify_config.guard_sees_mock': HardGate(
            INVARIANT, "a guard that reads what the mock never touches never runs as in production, so the run tests the agent without it"
        ),
        **{
            f'plugin.verify_config.{code}': HardGate(
                INVARIANT, 'a declared read-through the harness does not wire tests a real reader that never sees the mock\'s writes'
            )
            for code in ('read_through_invalid', 'read_through_unlinked', 'read_through_unbound')
        },
        # --- verify_simulate: a certain crash or a corrupt channel ----------------------------------------
        'plugin.verify_simulate.syntax_error': HardGate(INVARIANT, 'simulate.py does not parse; nothing runs'),
        'plugin.verify_simulate.missing_invoke_hook': HardGate(INVARIANT, 'the runner has no entry point to call'),
        'plugin.verify_simulate.seed_state_bad_signature': HardGate(INVARIANT, 'the runner calls seed_state with a fixed arity; another one raises'),
        **{
            f'plugin.verify_simulate.asyncio_run_in_{hook}': HardGate(INVARIANT, 'asyncio.run inside a hook the runner awaits raises on its running loop')
            for hook in ('invoke_agent', 'invoke_pipeline', 'seed_state')
        },
        'plugin.verify_simulate.spawned_stdout_on_ipc_channel': HardGate(INVARIANT, "child output on the IPC channel breaks the runner's framing"),
        'plugin.verify_simulate.entities_proxy_isinstance_dict_misuse': HardGate(
            INVARIANT, 'the world proxy is not a dict, so the branch reads an empty world'
        ),
        'plugin.verify_simulate.world_owner_conflict': HardGate(INVARIANT, 'two owners write the same world; the read is whichever wrote last'),
        # --- smoke: the child cannot start ------------------------------------------------------------------
        'plugin.smoke.syntax_error': HardGate(INVARIANT, 'simulate.py or a split helper does not parse; nothing runs'),
        'plugin.smoke.ipc_handshake': HardGate(INVARIANT, 'the child wrote non-JSON to stdout, so the runner cannot read a single turn'),
        'plugin.smoke.package_shadow': HardGate(INVARIANT, 'a directory named plurai shadows the SDK, so the runner imports the wrong package'),
        'plugin.smoke.sdk_api_gap': HardGate(INVARIANT, "the harness calls an SDK name the child's SDK does not have; the run raises at its first call"),
        # --- run gate --------------------------------------------------------------------------------------
        RUN_GATE_ROUTE_UNGRANTED: HardGate(SECURITY, "bringing the route up puts a copy of the agent into the customer's system; " + _CONSENT),
        RUN_GATE_HOST_DECLARATION_INVALID: HardGate(INVARIANT, 'an invalid declaration runs no probe, so no host fact is measured'),
        RUN_GATE_LIVE_CHECK_FAILED: HardGate(
            SECURITY, "a live tool's credential and service are the user's to fix; an overrule would run a tool that cannot work, silently"
        ),
        # --- backend identity ------------------------------------------------------------------------------
        BACKEND_IDENTITY_TENANT_DIFFERS: HardGate(SECURITY, "another organization or workspace would receive the customer's artifacts"),
        BACKEND_IDENTITY_STORAGE_DIFFERS: HardGate(INVARIANT, 'artifacts written to another store answer 404 by the ids this workdir holds'),
    }
)

#: Advisory ids that inform and hold nothing: an overrule on one changes no exit code. A verifier's
#: `warn` is no gate finding at all (only a `fail` is minted), so no verifier code is listed here.
NON_HOLDING: Final[frozenset[str]] = frozenset(
    {
        RUN_GATE_HOST_UNVERIFIED,
        RUN_VERB_SURFACE_UNMATCHED,
        # --- smoke: the agent's raise and a silent turn are the run's own signal, measured rather than refused ---
        'plugin.smoke.agent_raised',
        'plugin.smoke.turn_silent',
        'plugin.smoke.render_silent',
        'plugin.smoke.may_open_silent',
        # --- smoke: a read made through a C serializer or a forked child is not seen, so "unread" is a lead ---
        'plugin.smoke.request_key_unread',
        # --- smoke: a function the framework ran that a closed surface does not type is a warning; its remedy is the scan's ---
        'plugin.smoke.trail_tool_untyped',
        # --- smoke: what sibling agent processes met on one host warns; the runner re-runs a setup collision on its own ---
        'plugin.smoke.sibling_init_failed',
        'plugin.smoke.sibling_cross_talk',
        'plugin.smoke.exclusive_unneeded',
        # --- smoke: a turn's tool record warns (a void tool returns nothing; a paraphrased value trips the history probe) ---
        'plugin.smoke.trail_result_missing',
        'plugin.smoke.tool_history_dropped',
    }
)


def _family(gate_id: str) -> str | None:
    for prefix in FAMILIES:
        if gate_id.startswith(prefix) and _GATE_SEGMENT_RE.fullmatch(gate_id[len(prefix) :]):
            return prefix
    return None


def is_declared(gate_id: str) -> bool:
    return gate_id in GATES or _family(gate_id) is not None


def evidence_keys(gate_id: str) -> frozenset[str]:
    """The evidence keys ``gate_id``'s fingerprint covers. Raises :class:`UnknownGateError` for an undeclared id."""
    if not gate_id.startswith(PLUGIN_PREFIX):
        raise UnknownGateError(f'{gate_id!r} is not a plugin gate id: every plugin id starts with {PLUGIN_PREFIX!r}.')
    if gate_id in GATES:
        return GATES[gate_id]
    family = _family(gate_id)
    if family is None:
        raise UnknownGateError(f'gate id {gate_id!r} is not declared in gate_ledger.GATES and names no member of a declared family.')
    return FAMILIES[family]


def authority_of(gate_id: str) -> str:
    evidence_keys(gate_id)  # refuses an undeclared id
    hard = PLUGIN_HARD.get(gate_id)
    return hard.authority if hard else ADVISORY


def holds_of(gate_id: str) -> bool:
    return authority_of(gate_id) == ADVISORY and gate_id not in NON_HOLDING


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------


def _canonical(value: Any, path: str) -> Any:
    """``value`` with every string NFC-normalised, recursively. Refuses what has no stable canonical form."""
    if isinstance(value, str):
        return unicodedata.normalize('NFC', value)
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return value
    if isinstance(value, float):
        raise EvidenceError(f'evidence at {path} is a float ({value!r}); encode it as integers or a string so the fingerprint is stable.')
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise EvidenceError(f'evidence at {path} has a non-string key {key!r}.')
            normal = unicodedata.normalize('NFC', key)
            if normal in out:
                raise EvidenceError(f'evidence at {path} has two keys that normalise to {normal!r}.')
            out[normal] = _canonical(item, f'{path}.{normal}')
        return out
    # Tuple form, not `list | tuple`: assert-backend-identity.sh mints findings under whatever `python3`
    # the shell names, and a stock system interpreter can predate 3.10.
    if isinstance(value, (list, tuple)):
        return [_canonical(item, f'{path}[{i}]') for i, item in enumerate(value)]
    raise EvidenceError(f'evidence at {path} is a {type(value).__name__}, which JSON cannot carry.')


def fingerprint(gate_id: str, subject: str, evidence: Mapping[str, Any]) -> str:
    """sha256 over the canonical JSON of ``(gate_id, subject, evidence)`` — the backend's canonical form.

    Every string NFC-normalised (keys and values, recursively); floats and non-string keys refused;
    tuples read as lists; sorted keys, ``(',', ':')`` separators, ``ensure_ascii=False``, UTF-8. Lists keep
    their order: a producer sorts an id list itself.
    """
    payload = {
        'v': FINGERPRINT_CANON_VERSION,
        'gate_id': _canonical(gate_id, 'gate_id'),
        'subject': _canonical(subject, 'subject'),
        'evidence': _canonical(evidence, 'evidence'),
    }
    text = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _json_safe(value: Any) -> Any:
    """``observed`` is never fingerprinted: a float is kept, and anything JSON cannot carry becomes text."""
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def make_finding(
    gate_id: str,
    /,
    *,
    subject: str,
    evidence: Mapping[str, Any],
    observed: Mapping[str, Any],
    remedy: str,
    draft_ref: str | None,
) -> GateFinding:
    """THE way to build a :class:`GateFinding`.

    ``gate_id`` is positional-only, so every call site spells it where the catalogue scan reads it. It must be
    declared (:class:`UnknownGateError` otherwise) and ``evidence`` must carry exactly
    its declared keys (:class:`EvidenceError` otherwise). ``authority`` and ``holds`` come from the catalogue
    and cannot be passed. Every argument is required; ``draft_ref`` is an explicit ``None`` when the gate
    held no work back.
    """
    keys = evidence_keys(gate_id)
    if set(evidence) != keys:
        raise EvidenceError(f'{gate_id}: evidence keys {sorted(evidence)} differ from the declared {sorted(keys)}.')
    return GateFinding(
        gate_id=gate_id,
        authority=authority_of(gate_id),
        holds=holds_of(gate_id),
        subject=subject,
        evidence=_canonical(evidence, 'evidence'),
        observed=_json_safe(observed),
        remedy=remedy,
        draft_ref=draft_ref,
        fingerprint=fingerprint(gate_id, subject, evidence),
    )


def finding_line(finding: GateFinding) -> str:
    """The one stdout line a finding prints as. Machine form goes to ``--findings-out``, never a stdout trailer."""
    holding = ' holding' if finding.holds else ''
    return f'finding: {finding.gate_id} {finding.authority}{holding} {finding.fingerprint} — {finding.remedy}'


def write_findings(path: Path, findings: Iterable[GateFinding]) -> None:
    """``--findings-out``: every finding the call minted, raw (before the ledger), as one JSON list."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.tmp')
    tmp.write_text(json.dumps([f.as_dict() for f in findings], ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    tmp.replace(path)


def read_findings(path: Path) -> list[GateFinding]:
    """A findings list (a ``--findings-out`` file), or the ``findings`` of a gate record (the runner's
    ``gate_authority.json``)."""
    data = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(data, dict) and isinstance(data.get('findings'), list):
        data = data['findings']
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError(f'{path} is not a findings list.')
    return [GateFinding.from_dict(item) for item in data]


# ---------------------------------------------------------------------------
# the ledger
# ---------------------------------------------------------------------------


def ledger_path(art: Path) -> Path:
    """``$ART/.gate_overrules.jsonl`` — ``art`` is the workdir's ``artifacts/`` directory."""
    return art / LEDGER_FILENAME


def _is_hard(gate_id: str) -> bool:
    return gate_id in PLUGIN_HARD


#: The backend's gate catalogue as this plugin vendored it (`scripts/dev/vendor_client_contracts.py`).
_VENDORED_CONTRACTS = Path(__file__).resolve().parent / 'contracts' / 'contracts.json'


def vendored_authority(gate_id: str) -> str | None:
    """``gate_id``'s authority in the vendored backend catalogue, or ``None`` when it declares no such id (a
    runner id, or an unreadable copy)."""
    try:
        gates = json.loads(_VENDORED_CONTRACTS.read_text(encoding='utf-8'))['blocks']['gate_authority']['value']['gates']
        authority = gates[gate_id]['authority']
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return authority if isinstance(authority, str) else None


def _line_from(row: Any, where: str) -> LedgerLine:
    if not isinstance(row, dict) or not isinstance(row.get('overrule'), dict):
        raise LedgerError(f'{where} is not an overrule record.')
    raw = row['overrule']
    fields = ('gate_id', 'fingerprint', 'reason')
    if set(raw) != set(fields) or not all(isinstance(raw[f], str) for f in fields):
        raise LedgerError(f'{where}: its overrule carries {sorted(raw)}, not exactly {list(fields)} as strings.')
    finding = row.get('finding')
    if finding is not None and not isinstance(finding, dict):
        raise LedgerError(f'{where}: its finding is neither an object nor null.')
    return LedgerLine(
        overrule=Overrule(**{f: raw[f] for f in fields}),
        finding=finding,
        at=str(row.get('at') or ''),
        plugin_version=str(row.get('plugin_version') or ''),
        session=str(row.get('session') or ''),
    )


def read_ledger(path: Path) -> list[LedgerLine]:
    """Every line, in order; a missing ledger is ``[]``. A malformed line raises :class:`LedgerError`.

    Lines end at ``\\n`` only: ``str.splitlines`` also breaks at U+2028, U+2029, NEL and the other Unicode
    separators, which a ledger written before :func:`append_overrule` escaped them carries raw in a reason.
    """
    if not path.exists():
        return []
    out: list[LedgerLine] = []
    for n, text in enumerate(path.read_text(encoding='utf-8').split('\n'), 1):
        if not text.strip():
            continue
        try:
            row = json.loads(text)
        except ValueError as exc:
            raise LedgerError(f'{path}:{n} is not JSON ({exc}).') from exc
        out.append(_line_from(row, f'{path}:{n}'))
    return out


class OverruleRefused(ValueError):
    """An overrule the ledger will not record. ``label`` is the typed refusal (``overrule_refused`` for a hard id)."""

    def __init__(self, label: str, message: str) -> None:
        super().__init__(message)
        self.label = label


def check_overrule(overrule: Overrule, *, finding: Mapping[str, Any] | None) -> None:
    """Raise :class:`OverruleRefused` unless ``overrule`` may enter the ledger.

    A ``plugin.`` id must be declared and must not be hard. An id outside the namespace is the runner's or
    the backend's: it is refused when the finding passed with it, or the vendored backend catalogue, says
    it is ``security`` or ``invariant``; otherwise it is recorded for ``export`` and judged by its own
    catalogue there.
    """
    if not _GATE_ID_RE.fullmatch(overrule.gate_id):
        raise OverruleRefused('bad_gate_id', f'{overrule.gate_id!r} is not a dotted lower-case gate id as a finding prints it.')
    if overrule.gate_id.startswith(PLUGIN_PREFIX):
        if not is_declared(overrule.gate_id):
            raise OverruleRefused('unknown_gate', f'{overrule.gate_id} is not a declared plugin gate id.')
        if _is_hard(overrule.gate_id):
            hard = PLUGIN_HARD[overrule.gate_id]
            raise OverruleRefused('overrule_refused', f'{overrule.gate_id} is {hard.authority}: {hard.why}. No overrule lifts it; repair the cause.')
    else:
        stated = finding.get('authority') if finding is not None else None
        authority = stated if stated in HARD_AUTHORITIES else vendored_authority(overrule.gate_id)
        if authority in HARD_AUTHORITIES:
            raise OverruleRefused('overrule_refused', f'{overrule.gate_id} is {authority}. No overrule lifts it; repair the cause.')
    if not _FINGERPRINT_RE.fullmatch(overrule.fingerprint):
        raise OverruleRefused('bad_fingerprint', f'{overrule.fingerprint!r} is not a sha256 hex fingerprint as a finding line prints it.')
    if len(overrule.reason.strip()) < OVERRULE_REASON_MIN_CHARS:
        raise OverruleRefused(
            'reason_too_short',
            f'an overrule reason must state what was verified in at least {OVERRULE_REASON_MIN_CHARS} characters; got {len(overrule.reason.strip())}.',
        )
    if finding is not None and (finding.get('gate_id'), finding.get('fingerprint')) != (overrule.gate_id, overrule.fingerprint):
        raise OverruleRefused('finding_mismatch', 'the finding recorded with the overrule is not the finding it names.')


def append_overrule(path: Path, overrule: Overrule, *, finding: Mapping[str, Any] | None, at: str, plugin_version: str, session: str) -> LedgerLine:
    """Check ``overrule`` and append it as one line. Raises :class:`OverruleRefused` and writes nothing when refused.

    ASCII-escaped, so a reason or a copied finding holding a Unicode line separator stays on one physical line.
    """
    check_overrule(overrule, finding=finding)
    line = LedgerLine(overrule=overrule, finding=dict(finding) if finding is not None else None, at=at, plugin_version=plugin_version, session=session)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(line.as_dict(), ensure_ascii=True, sort_keys=True) + '\n')
    return line


def _in_scope(gate_id: str, evaluated: Collection[str]) -> bool:
    return gate_id in evaluated or any(scope.endswith('.') and gate_id.startswith(scope) for scope in evaluated)


def apply(findings: Sequence[GateFinding], lines: Sequence[LedgerLine], *, evaluated: Collection[str]) -> Applied:
    """What still holds once the ledger is read. Pure: never changes a finding or a line.

    ``evaluated`` names every gate that RAN in this call — gate ids, or a family prefix ending in ``.``
    (``'plugin.verify_config.'``) for a script that runs its whole closed set. It is what separates
    ``stale`` (the gate ran and nothing carries this fingerprint) from an overrule of a gate this call never
    looked at, which the ledger keeps for the call that does.

    A hard finding always holds, and a ledger line naming a hard id is ignored however it got there.
    """
    lifts = {(line.overrule.gate_id, line.overrule.fingerprint): line.overrule for line in lines if not _is_hard(line.overrule.gate_id)}
    holding: list[GateFinding] = []
    overruled: list[GateFinding] = []
    not_holding: list[Overrule] = []
    for finding in findings:
        if finding.authority in HARD_AUTHORITIES:
            holding.append(finding)
        elif not finding.holds:
            if (lift := lifts.get((finding.gate_id, finding.fingerprint))) is not None:
                not_holding.append(lift)
        elif (finding.gate_id, finding.fingerprint) in lifts:
            overruled.append(finding)
        else:
            holding.append(finding)
    current = {(f.gate_id, f.fingerprint) for f in findings}
    stale = tuple(o for key, o in lifts.items() if _in_scope(o.gate_id, evaluated) and key not in current)
    return Applied(holding=tuple(holding), overruled=tuple(overruled), stale=stale, not_holding=tuple(not_holding))


def is_overruled(finding: GateFinding, lines: Sequence[LedgerLine]) -> bool:
    """Whether a ledger line lifts this finding's hold. False for a hard or non-holding finding."""
    return bool(apply([finding], lines, evaluated=(finding.gate_id,)).overruled)


def settle(findings: Sequence[GateFinding], *, art: Path, evaluated: Collection[str], findings_out: Path | None) -> tuple[Applied, list[str]]:
    """What a script that mints findings does with them before it picks its exit code.

    Writes ``findings_out`` (every finding, raw, before the ledger) when one is given, reads the ledger in
    ``art``, and returns the :class:`Applied` — a non-empty ``holding`` is exit 1, "something holds" — with
    the human lines to print: one ``finding:`` line per finding, then one per overrule the ledger lifted,
    found stale, or found holding nothing. Raises :class:`LedgerError` or ``OSError`` when the ledger cannot
    be read: a ledger skipped is a hold dropped without a word.
    """
    if findings_out is not None:
        write_findings(findings_out, findings)
    path = ledger_path(art)
    applied = apply(findings, read_ledger(path), evaluated=evaluated)
    lines = [finding_line(f) for f in findings]
    lines += [f'overruled: {f.gate_id} {f.fingerprint} — lifted by {path}' for f in applied.overruled]
    lines += [f'overrule stale: {o.gate_id} {o.fingerprint} — the evidence it was judged on changed; judge the current finding again' for o in applied.stale]
    lines += [f'overrule not holding: {o.gate_id} {o.fingerprint} — that finding holds nothing, so the overrule changes nothing' for o in applied.not_holding]
    return applied, lines


def upload_summary(lines: Sequence[LedgerLine]) -> list[dict[str, Any]]:
    """The plugin's own overrules as the run upload may carry them: ``gate_id``, ``authority``, ``fingerprint``,
    ``decided_at`` (the latest line) and ``count`` (lines naming that finding) — never the reason or the
    evidence, which can quote customer content. Runner/backend ids are audited where they are applied; hard
    and undeclared ids lifted nothing and are left out.
    """
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for line in lines:
        gate_id, fp = line.overrule.gate_id, line.overrule.fingerprint
        if not gate_id.startswith(PLUGIN_PREFIX) or not is_declared(gate_id) or _is_hard(gate_id):
            continue
        row = rows.setdefault((gate_id, fp), {'gate_id': gate_id, 'authority': authority_of(gate_id), 'fingerprint': fp, 'decided_at': line.at, 'count': 0})
        row['count'] += 1
        row['decided_at'] = max(row['decided_at'], line.at)
    return [rows[key] for key in sorted(rows)]


def export_overrules(lines: Sequence[LedgerLine], *, scope: str) -> list[dict[str, str]]:
    """The overrule list another component reads. ``scope='sdk'``: every runner/backend id, the latest reason
    per finding — ``plugin.`` ids are never part of it."""
    if scope != 'sdk':
        raise ValueError(f'unknown export scope {scope!r}.')
    latest: dict[tuple[str, str], Overrule] = {}
    for line in lines:
        if not line.overrule.gate_id.startswith(PLUGIN_PREFIX):
            latest[(line.overrule.gate_id, line.overrule.fingerprint)] = line.overrule
    return [dataclasses.asdict(o) for o in latest.values()]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _plugin_version() -> str:
    try:
        data = json.loads((Path(__file__).resolve().parent.parent / '.claude-plugin' / 'plugin.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 'unknown'
    return str(data.get('version') or 'unknown') if isinstance(data, dict) else 'unknown'


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def session_id() -> str:
    """The session id of the harness running this process, for the ledger line's ``session``.

    ``hooks/_workdir.py`` owns the variable order (Copilot, Codex and Claude Code each export their own),
    so a script run under any harness stamps the id that harness's hooks key their markers under. It is the empty string
    when no harness exported one.
    """
    hooks = str(Path(__file__).resolve().parent.parent / 'hooks')
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    from _workdir import session_id as resolved  # noqa: PLC0415 - hooks/ is only on sys.path here

    return resolved()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    tmp.replace(path)


def _cmd_overrule(args: argparse.Namespace) -> int:
    finding: dict[str, Any] | None = None
    if args.finding_from is not None:
        try:
            matches = [f for f in read_findings(args.finding_from) if (f.gate_id, f.fingerprint) == (args.gate_id, args.fingerprint)]
        except (OSError, ValueError) as exc:
            print(f'gate_ledger: {args.finding_from} could not be read ({type(exc).__name__}: {exc})', file=sys.stderr)
            return 2
        if not matches:
            print(f'gate_ledger: finding_absent — {args.finding_from} carries no {args.gate_id} finding with fingerprint {args.fingerprint}', file=sys.stderr)
            return 2
        finding = matches[0].as_dict()
    overrule = Overrule(gate_id=args.gate_id, fingerprint=args.fingerprint, reason=args.reason)
    path = ledger_path(args.art)
    try:
        append_overrule(
            path, overrule, finding=finding, at=_now(), plugin_version=_plugin_version(), session=session_id()
        )
    except OverruleRefused as exc:
        print(f'gate_ledger: {exc.label} — {exc}', file=sys.stderr)
        return 2
    except OSError as exc:
        print(f'gate_ledger: {path} could not be written ({type(exc).__name__}: {exc})', file=sys.stderr)
        return 2
    print(f'gate_ledger: overruled {args.gate_id} {args.fingerprint}')
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    for line in read_ledger(ledger_path(args.art)):
        o = line.overrule
        ignored = ' (ignored: hard)' if _is_hard(o.gate_id) else ''
        print(f'{line.at} {o.gate_id} {o.fingerprint}{ignored} — {o.reason}')
    return 0


def _refused_out(args: argparse.Namespace) -> bool:
    """Say so and return True when ``--out`` names a path the confinement does not allow."""
    if why := _confine.refusal(args.out, allow_outside=args.allow_outside_workdir):
        print(f'gate_ledger: {why}', file=sys.stderr)
        return True
    return False


def _cmd_export(args: argparse.Namespace) -> int:
    if _refused_out(args):
        return 2
    _write_json(args.out, export_overrules(read_ledger(ledger_path(args.art)), scope=args.scope))
    return 0


def _cmd_summary(args: argparse.Namespace) -> int:
    if _refused_out(args):
        return 2
    _write_json(args.out, upload_summary(read_ledger(ledger_path(args.art))))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    verbs = parser.add_subparsers(dest='verb', required=True)

    p = verbs.add_parser('overrule', help='record one overrule (the plugin decides alone; never ask the user)')
    p.add_argument('--art', type=Path, required=True, help="the workdir's artifacts/ directory ($ART)")
    p.add_argument('--gate-id', required=True)
    p.add_argument('--fingerprint', required=True, help='as the `finding:` line printed it')
    p.add_argument(
        '--reason', required=True, help=f'what you verified, citing the repo (file:line, a smoke trail, a diff); >= {OVERRULE_REASON_MIN_CHARS} characters'
    )
    p.add_argument('--finding-from', type=Path, help='a --findings-out file carrying the finding; recorded with the overrule')
    p.set_defaults(run=_cmd_overrule)

    p = verbs.add_parser('list', help='every recorded overrule (internal; never shown to the user)')
    p.add_argument('--art', type=Path, required=True)
    p.set_defaults(run=_cmd_list)

    p = verbs.add_parser('export', help='the overrule list for the runner (runner/backend ids only)')
    p.add_argument('--art', type=Path, required=True)
    p.add_argument('--scope', required=True, choices=('sdk',))
    p.add_argument('--out', type=Path, required=True)
    _confine.add_flag(p)
    p.set_defaults(run=_cmd_export)

    p = verbs.add_parser('summary', help='the scrubbed audit summary for the run upload (no reason, no evidence)')
    p.add_argument('--art', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    _confine.add_flag(p)
    p.set_defaults(run=_cmd_summary)

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        return args.run(args)
    except (OSError, LedgerError) as exc:
        print(f'gate_ledger: the ledger could not be read or written ({type(exc).__name__}: {exc})', file=sys.stderr)
        return 2


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
