"""Which INPUT DOCUMENTS a `generate_scenarios_start` call carried — the baseline the re-draw
gate compares against.

WHY THIS EXISTS. `previous_scenario_set_id` keeps FLOW IDS stable and inherits no inputs, so
every regeneration has to re-send the whole authoring input set itself. A call composed from
working memory rather than from the markers on disk drops whatever was re-typed from context —
most often an inline-only document with no stash lane — and the batch is authored against a world
nobody declared. So the record of what the build has already sent is kept on DISK, where a
compacted or resumed session is bound by it too.

LOGICAL keys, not wire keys. `prd_markdown` and `prd_upload_id` are two lanes for one document,
and switching lanes mid-build is normal (that is what stashing is FOR) — comparing raw argument
names would call it a drop. Emptiness counts as absence: `scenario_state_schema_yaml=None` is
a document NOT sent, and a key present with a null value must not read as sent.

CONTROL keys are deliberately absent from the map below. `count`, `redraws`, `interest`,
`steering_demands`, `previous_scenario_set_id`, `adopt_from_scenario_set_id` and the rest shape
one call, not the world it authors against — and they legitimately differ per call. The skills'
own rules require it: the final delivery batch MUST omit `redraws`, so a naive "same arguments as
call 1" check would refuse the one call the build exists to make.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

#: Marker holding the input-document keys this build has successfully sent, one per line.
GEN_INPUT_KEYS_MARKER = ".gen_input_keys"

#: Marker naming the backend origin that checks a dropped input document itself: the origin whose
#: `generate_scenarios_start` Job envelope carried `detail.input_document_union` (the backend refuses a
#: regeneration that drops a document its predecessor chain was built from, for every client). Written by
#: `regen_spent` only, read by `regen_gate` only, which stands its own input-drop check down for a call that
#: names a predecessor while the marker names the CURRENT backend. A switch to another backend re-arms the
#: local check until that backend's own envelope says it checks.
SERVER_UNION_MARKER = ".server_input_union"

#: logical input document -> the argument names that can carry it, richest lane first.
#:
#: Scoped to the documents the BACKEND materializes into the set's `inputs/` directory (plus the
#: by-id lanes and the grounding handles), because those are exactly the inputs that shape what
#: gets authored. That boundary is checkable against the backend rather than being a taste call.
#: DELIBERATELY EXCLUDED, though they are input documents: `user_style_markdown` and
#: `world_state_markdown`. The backend BACKFILLS both from the
#: central trace store when a call omits them, so omitting them is not a drop — and the driver has
#: no local path to re-send them from, which would make the deny text a guessing loop.
INPUT_DOC_KEYS: dict[str, tuple[str, ...]] = {
    "variables": ("variables_yaml", "variables_upload_id"),
    "prd": ("prd_markdown", "prd_upload_id"),
    "plurai_config": ("plurai_config_yaml", "plurai_config_upload_id"),
    "state_schema": ("scenario_state_schema_yaml",),
    "inputs_schema": ("inputs_schema_yaml",),
    "outputs_schema": ("outputs_schema_yaml",),
    "data_profile": ("data_profile_markdown", "data_profile_upload_id"),
    "scan_contract": ("scan_contract_upload_id",),
    "world_reference_rows": ("world_reference_rows",),
    "knowledge": ("knowledge_bases", "corpus_ids", "corpus_id"),
    "doc_examples": ("doc_examples_set_id",),
    "scenario_bundle": ("scenario_bundle_id",),
    "document_fields": ("document_fields",),
}

#: What each logical key is called where the driver can go and get it again. The deny text is
#: read by a model that is about to resubmit, so naming the SOURCE is the difference between one
#: corrected call and a guessing loop.
INPUT_DOC_SOURCES: dict[str, str] = {
    "variables": '$WORKDIR/scenario_variables.yaml (or the stashed id in $WORKDIR/.variables_upload_id)',
    "prd": '$WORKDIR/simulation_prd.md (or the stashed id in $WORKDIR/.prd_upload_id)',
    "plurai_config": '$WORKDIR/plurai.config.yaml (or the stashed id in $WORKDIR/.plurai_config_upload_id)',
    "state_schema": '$WORKDIR/scenario_state_schema.yaml — inline only, there is no upload lane; '
                    "re-read it from the env job (job_status(id=<env_id>) -> detail.scenario_state_schema_yaml) if it is not on disk",
    "inputs_schema": '$WORKDIR/inputs_schema.yaml (written by the extract-scenario-variables skill; inline only)',
    "outputs_schema": '$WORKDIR/outputs_schema.yaml (written by the extract-scenario-variables skill; inline only)',
    "data_profile": '$WORKDIR/artifacts/data_profile.md (or the stashed id in $WORKDIR/.data_profile_upload_id)',
    "scan_contract": '$WORKDIR/.scan_contract_upload_id',
    "world_reference_rows": '$WORKDIR/.world_reference_rows.json (the JSON list, verbatim)',
    # ALL THREE marker shapes, in the order the shared reader reads them
    # (`scripts/corpus_coordinate.py`). Naming only two of them sent a driver to re-read a file a
    # per-tenant env does not have: `.corpus_ids` is what /manage-envs writes on a USE, and
    # `.corpus_id` exists only for an agent with exactly one base carrying exactly one id.
    "knowledge": '$WORKDIR/.knowledge_bases, else $WORKDIR/.corpus_ids (one id per line), else '
                 '$WORKDIR/.corpus_id — or run scripts/corpus_coordinate.py, which reads all three',
    "doc_examples": '$WORKDIR/.doc_examples_set_id',
    "scenario_bundle": '$WORKDIR/.scenario_bundle_id',
    "document_fields": '$WORKDIR/.document_fields',
}


# --- the map is checked against the BACKEND's own classification -------------------------------
#
# The map above is a hand-written mirror of a backend tool signature — the very thing this gate
# exists to prevent ("a regeneration cannot silently drop an input document") — so an argument
# added AFTER it was written is not an input document TO THE GATE: not recorded, not denied on,
# not warned about — a drop of exactly the argument nobody has looked at passes behind a green gate.
#
# The backend classifies every `generate_scenarios_start` argument itself and serves the
# classification, so the closed set below is READ rather than retyped. The GROUPING stays here —
# The backend has no opinion about which arguments are two lanes for one document, and that is what
# `input_doc_keys` needs — but the MEMBERSHIP is derivable, and `UNMAPPED_INPUT_DOCUMENTS` is the
# difference. `hooks/tests/test_gen_input_keys.py` asserts it is empty; a new backend input
# document is red until somebody gives it a lane and a source.
_LOCAL_INPUT_LANES: frozenset[str] = frozenset(lane for lanes in INPUT_DOC_KEYS.values() for lane in lanes)

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
try:
    from plurai_contracts import contract as _contract
except ImportError as _exc:  # a broken install, never a normal state — a hook must not take the session with it
    # LOUDLY, through the channel `report_guard.py` uses for its own unloadable data. The stub's
    # answer IS this file's own map, so `UNMAPPED_INPUT_DOCUMENTS` comes out `()` — byte-identical
    # to "the backend has no input document this map is missing", which is the one thing the
    # derive-and-assert below exists to say. `()`-because-blind must not read as `()`-because-clean:
    # CI reads the block through a REQUIRED contract and would go red, but the runtime gate is the
    # blind one, and it is the one that denies a customer's regeneration.
    try:
        from _hookfail import note as _note

        _note("_gen_inputs (plurai_contracts unreadable — the backend's input-document classification is NOT being read)", _exc)
    except Exception:  # noqa: BLE001 - the loud-failure helper must never BE the failure
        pass

    def _contract(name: str, default: object) -> object:  # type: ignore[misc]
        return default

    _READER_IMPORTED = False
else:
    _READER_IMPORTED = True

#: This file's own map, wearing the shape of the contract block. Passed as the `default` so the
#: fallback is the consumer's existing literal (rule 1 of `scripts/plurai_contracts.py`), and
#: kept as a NAME so the identity test below can tell the fallback from a real read.
_LOCAL_CLASSIFICATION: dict = {"input_documents": sorted(_LOCAL_INPUT_LANES)}
_CLASSIFICATION = _contract("generate_scenarios_arguments", default=_LOCAL_CLASSIFICATION) or _LOCAL_CLASSIFICATION

#: Whether the backend's own classification was actually read. FALSE both when the reader would
#: not import and when the vendored payload does not carry the block — in either case everything
#: below compares this map against itself, so an empty `UNMAPPED_INPUT_DOCUMENTS` is the answer to
#: a question nobody asked. Exported because that distinction is invisible in the tuples.
BACKEND_CLASSIFICATION_READ: bool = _READER_IMPORTED and _CLASSIFICATION is not _LOCAL_CLASSIFICATION

#: What the BACKEND says its input documents are. The default on absence is this file's own map,
#: unchanged — so a plugin against a backend older than the block behaves exactly as it did, and
#: the check below simply has nothing to say (which `BACKEND_CLASSIFICATION_READ` states).
BACKEND_INPUT_DOCUMENTS: frozenset[str] = frozenset(
    _CLASSIFICATION.get("input_documents", sorted(_LOCAL_INPUT_LANES))  # type: ignore[union-attr]
)

#: Input documents the backend has and this map has no lane for — the gate is blind to each one.
UNMAPPED_INPUT_DOCUMENTS: tuple[str, ...] = tuple(sorted(BACKEND_INPUT_DOCUMENTS - _LOCAL_INPUT_LANES))

#: Lanes this map still names that the backend no longer accepts. A deny on one of them would be
#: unsatisfiable — no call could ever carry it again.
RETIRED_INPUT_LANES: tuple[str, ...] = tuple(sorted(_LOCAL_INPUT_LANES - BACKEND_INPUT_DOCUMENTS))


def _carries(value: object) -> bool:
    """Whether an argument actually CARRIES its document.

    `None`, `''`, `[]` and `{}` are all "not sent": a call can name the parameter and send nothing for it.
    """
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, (str, list, tuple, dict, set)):
        return bool(value)
    return True


def current_backend_origin() -> str:
    """The origin of the backend this session talks to (``scheme://host:port``), or ``""`` when none resolves.

    The resolution every client side shares (``_login_store.resolve_backend``): the launch environment's pair,
    else the saved login. Never raises: a hook that cannot tell the backend keeps its own check.
    """
    try:
        import _login_store  # noqa: PLC0415 - hooks/ is on sys.path wherever a hook runs

        backend = _login_store.resolve_backend()
        if not backend.url:
            return ""
        found, _why = _login_store.backend_origin(backend.url)
        return _login_store.spell(found) if found else ""
    except Exception:  # noqa: BLE001 - an unreadable login means "unknown backend", which keeps the local check
        return ""


def server_checks_inputs(workdir: Path | None) -> bool:
    """Whether ``$WORKDIR/.server_input_union`` names the backend this session talks to now."""
    if workdir is None:
        return False
    origin = current_backend_origin()
    if not origin:
        return False
    try:
        return (workdir / SERVER_UNION_MARKER).read_text(encoding="utf-8", errors="replace").strip() == origin
    except OSError:
        return False


def names_predecessor(tool_input: object) -> bool:
    """Whether a call names ``previous_scenario_set_id``: the calls the backend checks for a dropped document."""
    return isinstance(tool_input, dict) and _carries(tool_input.get("previous_scenario_set_id"))


def input_doc_keys(tool_input: object) -> set[str]:
    """The LOGICAL input documents this call carries."""
    if not isinstance(tool_input, dict):
        return set()
    return {logical for logical, lanes in INPUT_DOC_KEYS.items() if any(_carries(tool_input.get(lane)) for lane in lanes)}


def parse_marker(text: str) -> set[str]:
    """Recorded keys from the marker's text — unknown names dropped.

    Dropping them is deliberate: a key this build recorded under an older plugin, whose name has
    since been retired, must not deny every call forever for want of an argument that no longer
    exists.
    """
    return {line.strip() for line in text.splitlines() if line.strip() in INPUT_DOC_KEYS}


def render_marker(keys: set[str]) -> str:
    return "".join(f"{k}\n" for k in sorted(keys))


def dropped_document_finding(key: str) -> Any:
    """The gate finding for one input document a call dropped: ``plugin.regen.input_doc_dropped``, advisory and
    holding. Its fingerprint covers the document alone, so one overrule retires that document for this
    workdir's build — the decision to stop sending it, recorded with its reason in the plugin's ledger."""
    import gate_ledger  # noqa: PLC0415 - scripts/ is on sys.path (above); imported where used so a broken install fails open

    return gate_ledger.make_finding(
        gate_ledger.REGEN_INPUT_DOC_DROPPED,
        subject=key,
        evidence={"document": key},
        observed={},
        remedy=f"re-send it from {INPUT_DOC_SOURCES.get(key, 'the workdir marker that holds it')}",
        draft_ref=None,
    )


def overruled_documents(workdir: Path, keys: Iterable[str]) -> set[str]:
    """The documents among ``keys`` whose drop the plugin has overruled in ``$WORKDIR/artifacts/.gate_overrules.jsonl``.

    Raises ``OSError`` or ``ValueError`` (``gate_ledger.LedgerError``) when the ledger cannot be read; the
    caller decides which way that fails.
    """
    import gate_ledger  # noqa: PLC0415

    lines = gate_ledger.read_ledger(gate_ledger.ledger_path(workdir / "artifacts"))
    return {key for key in keys if gate_ledger.is_overruled(dropped_document_finding(key), lines)}
