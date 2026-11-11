#!/usr/bin/env python3
"""A scenario re-draw must re-send the inputs the build already sent, and (inside a flow) must
have been logged first. PreToolUse on the backend's `generate_scenarios_start`, run by `run.py` on
every harness. The mock gate is never a reason to deny a draw: it warns (`bench_manifest.py mock-gate`).

TWO independent checks, in that order, because they refuse different mistakes.

--- 1. THE INPUT-DOCUMENT SUPERSET ---

`previous_scenario_set_id` keeps FLOW IDS stable and inherits NO inputs, so a regeneration that
does not re-send the authoring documents regenerates against a world nobody declared. A call
composed from working memory instead of from the markers on disk drops exactly the input that has
no upload-id lane, because it is the one re-typed from context every time. `hooks/regen_spent.py`
writes what each STARTED generation actually sent into `$WORKDIR/.gen_input_keys`, and a later
call that carries less is a gate finding per dropped document — `plugin.regen.input_doc_dropped`,
advisory and HOLDING: the call is denied, naming the document and where to get it, until it is
re-sent or the plugin overrules the finding in `$WORKDIR/artifacts/.gate_overrules.jsonl`
(`scripts/gate_ledger.py overrule`). An overrule is how a document is retired on purpose; its
fingerprint covers the document alone, so it retires that document for the build.

THE BACKEND NOW CHECKS IT. A backend that refuses a dropped input document itself (along the whole
predecessor chain, for every client) says so on the Job envelope of `generate_scenarios_start`
(`detail.input_document_union`); `regen_spent` then writes that backend's origin into
`$WORKDIR/.server_input_union`. While that marker names the CURRENT backend, check 1 stands down for a
call that names `previous_scenario_set_id`, the calls the backend checks, except for the scenario bundle and
the world reference rows: the backend only warns when a later batch leaves those out, so while their markers
are present the local check still denies the drop. A call naming no predecessor, an older backend, or a
session switched to another backend keeps the whole local check. The check is removed in
a release after every backend runs the server check.

Scoped to INPUT DOCUMENTS (see `_gen_inputs.INPUT_DOC_KEYS`) and to logical documents rather than
argument names: control keys legitimately differ per call — the skills' own rules require the
final delivery batch to omit `redraws` — and switching a document from inline text to a stashed
id is what stashing is FOR, not a drop. NOT flow-scoped, unlike the check below: a standalone
`/generate-scenarios` regeneration drops an input exactly as expensively.

--- 2. THE LOGGED RE-DRAW ---

WHY A HOOK. `append_loop_log.py --kind regeneration` types the reason for a re-draw and attaches
a finding when that reason is a coverage sweep at qualification stage. On its own that records a
LINE, not a re-draw: nothing forced the script to be called, so the rule reduced to a sentence in
a skill asking the model to call something it could skip. That is the shape this directory already
rejected once — `flow_arm`'s docstring: "a model that skipped one Bash line ran the whole flow
unguarded".

So the log entry mints a one-shot token (`.regen_logged`) and a re-draw spends it, exactly as
`ask_guard` spends `.ask_sanctioned` for a sanctioned question. The re-draw is impossible
unless the reason was recorded.

Check 1 runs first for REACHABILITY, not for cost: check 2 returns early outside a flow window
and again on a first draw, so a check placed after it would never see the standalone
`/generate-scenarios` regeneration — which drops an input exactly as expensively.

THE SPEND IS NOT HERE. This is PreToolUse: it can see the arguments and never the result, so
consuming the token here charged it to calls that started nothing — a call refused at input
validation spent the one-shot and the next legitimate attempt was denied. `hooks/regen_spent.py`
is the PostToolUse companion that consumes it, and only on a job-creating result.

SCOPE. Check 2 is kept narrow so it can only ever cost a re-draw and never a first draw:

* only `generate_scenarios_start` — no other tool is a re-draw;
* only inside a flow window, because outside one there is no loop whose log this is;
* only when `.scenario_set_id` ALREADY EXISTS. A first draw has nothing to re-draw and is
  always allowed; the file's presence is what makes this call a SECOND one. `/generate-scenarios`
  run standalone against a fresh workdir is therefore never touched by check 2.

Check 1 is scoped differently and deliberately wider — by the BUILD, not the flow: it fires
wherever `$WORKDIR/.gen_input_keys` records an earlier started generation, in or out of a flow
window, first draw of this session or not. What bounds it is that the baseline lives in the
workdir, so it only ever speaks about the build whose call this is.

FAIL OPEN on anything unexpected. A guard that denies because it could not read its own state
wedges a live flow mid-build, and the cost asymmetry is stark: a missed re-draw costs one
batch, a wedged flow costs the run. Check 1 is the one branch here that can block a call which
is otherwise legal, so every way it fails open ALSO prints one line to stderr — a guard that is
silently off is no guard — and its deny text carries the command that overrules a finding.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).parent))

from _gen_inputs import (  # noqa: E402
    GEN_INPUT_KEYS_MARKER,
    dropped_document_finding,
    input_doc_keys,
    names_predecessor,
    overruled_documents,
    parse_marker,
    server_checks_inputs,
)
from _workdir import find_marker, find_markers, marker_dirs, resolve_workdir  # noqa: E402

if TYPE_CHECKING:
    from _harness.model import Decision, Event

FLOW_MARKER = ".plurai_flow_active"
REGEN_TOKEN = ".regen_logged"
SET_MARKER = ".scenario_set_id"
#: The staged scenario bundle's marker. `/generate-scenarios` Step 6 clears it when the build the
#: bundle was FOR is finished, so its absence is the retire statement check 1 otherwise asks for.
BUNDLE_MARKER = ".scenario_bundle_id"
WORLD_ROWS_MARKER = ".world_reference_rows.json"
#: The documents the backend only warns about when a later batch leaves them out (``scenario.input_doc_omitted``):
#: the local check keeps them, marker-conditioned, even for a call the backend checks.
SERVER_WARNS_ONLY = frozenset({"scenario_bundle", "world_reference_rows"})
GUARDED_TOOL = "backend:generate_scenarios_start"
#: This hook's own plugin checkout. The Bash tool does not export $CLAUDE_PLUGIN_ROOT, so a command
#: this hook hands the model names the resolved path, never the variable.
PLUGIN_ROOT = str(Path(__file__).resolve().parent.parent)

def _teach() -> str:
    """The deny text for a re-draw with no logged reason. Its command names this checkout's scripts as
    ``plugin_root.shell_path`` spells them (one word in bash and PowerShell on Windows)."""
    from plugin_root import shell_path  # noqa: PLC0415 - hooks/ is on sys.path wherever a hook runs

    return (
        "This is a RE-draw (a scenario set already exists for this build) and the loop log has no "
        "accepted `regeneration` entry for it.\n\n"
        "Log the reason first:\n\n"
        f'  bash {shell_path(f"{PLUGIN_ROOT}/scripts/bin/plurai-in-workdir")} python3 '
        f'{shell_path(f"{PLUGIN_ROOT}/scripts/append_loop_log.py")} \\\n'
        "    --kind regeneration --flag-class <content|infra|coverage> \\\n"
        '    --defect <backend flag|finding:id> --finding "<one line>"\n\n'
        "A coverage-motivated re-draw of a qualification batch is logged with a finding: a "
        "~3-scenario sample cannot cover a flow graph by arithmetic, so a batch bought for breadth buys "
        "a batch, not a validated machine — the decision is yours. If the last draw hit a real defect, "
        "name its class and cite the flag; if it did not, do not re-draw — land short and route the "
        "finding to `generator@backend`."
    )


def _missing_input_docs(tool_input: dict, workdir: Path | None, only: frozenset[str] | None = None) -> tuple[list, str | None]:
    """The ``plugin.regen.input_doc_dropped`` findings that still hold for this call — or ``[]`` — and why
    the overrule ledger could not be read, when it could not.

    One finding per input document an earlier STARTED generation sent that this call does not,
    minus each document whose drop the plugin has overruled in the workdir's ledger.

    Reads the WORKDIR copy only, matching its single writer (``regen_spent``). A session-scoped
    copy would outlive a ``manage-envs`` switch and deny the first-ever call in the new env for
    "dropping" documents that belong to a different agent — fail-CLOSED, which this directory
    never is.

    Every miss returns ``[]`` and says so on stderr rather than in a deny: this is the one check
    here that CAN block a legal call, so its inertness has to be visible. An unreadable LEDGER is the
    one exception: it lifts nothing, the deny stands — its remedy, re-sending the document, never
    needs the ledger — and the deny text names it, since an overrule cannot lift a finding until the
    ledger reads again.
    """
    if not tool_input:
        # The hook could not SEE the arguments — a payload shape it does not recognise, not a call
        # that sent nothing. Every real `generate_scenarios_start` carries arguments (the tool
        # refuses one with no variables at all), so treating this as "everything was dropped"
        # would deny on the hook's own blindness. Fail open, like every sibling.
        return [], None
    if workdir is None:
        print("regen_gate: no workdir resolved — the input-drop check is INERT for this call.", file=sys.stderr)
        return [], None
    path = workdir / GEN_INPUT_KEYS_MARKER
    try:
        if not path.exists():
            return [], None  # no baseline — this build has started no generation this hook could see
        # `exists()`, then read — NOT `is_file()` as the gate. A path that exists and is not a
        # readable file (a directory, a broken mount) must reach the handler below and be
        # ANNOUNCED, not silently read as "no baseline recorded".
        recorded = parse_marker(path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        print(f"regen_gate: could not read {path} ({exc}) — the input-drop check is INERT for this call.", file=sys.stderr)
        return [], None
    if not recorded:
        return [], None  # no baseline — this build has started no generation this hook could see
    missing = sorted(recorded - input_doc_keys(tool_input))
    if only is not None:
        missing = [key for key in missing if key in only]  # the backend checks the rest of this call
    # A scenario bundle is consumed by the ONE build it was staged for, and `/generate-scenarios`
    # Step 6 clears its markers when that build is finished — so a later batch that no longer
    # sends `scenario_bundle_id` is not a dropped input, it is the retire step already taken on
    # disk. Only while the marker is still there is an omitted bundle a drop.
    if "scenario_bundle" in missing and not (workdir / BUNDLE_MARKER).exists():
        missing.remove("scenario_bundle")
    # The same for the world reference rows: their marker is rewritten at every suite Step 0 and removed when no
    # table could be stashed, which leaves those types' rows authored. Advisory by design, so its absence is no drop.
    if "world_reference_rows" in missing and not (workdir / WORLD_ROWS_MARKER).exists():
        missing.remove("world_reference_rows")
    if not missing:
        return [], None
    try:
        findings = [dropped_document_finding(key) for key in missing]
    except Exception as exc:  # noqa: BLE001 - a broken gate_ledger install is the hook's own fault: fail open, loudly
        print(f"regen_gate: could not build the gate findings ({type(exc).__name__}: {exc}) — the input-drop check is INERT for this call.", file=sys.stderr)
        return [], None
    ledger_error = None
    try:
        overruled = overruled_documents(workdir, missing)
    except (OSError, ValueError) as exc:
        ledger_error = f"{type(exc).__name__}: {exc}"
        print(f"regen_gate: could not read the overrule ledger ({ledger_error}) — no overrule lifts this call's findings.", file=sys.stderr)
        overruled = set()
    return [f for f in findings if f.subject not in overruled], ledger_error


def _teach_missing(findings: list, workdir: Path, *, ledger_error: str | None) -> str:
    """The deny text for the findings that hold: what each dropped document is, where to get it, and the
    overrule that retires one on purpose — or, when the ledger cannot be read, that no overrule lifts
    anything until it is repaired."""
    from plugin_root import shell_path  # noqa: PLC0415 - hooks/ is on sys.path wherever a hook runs

    lines = "\n".join(f"  finding: {f.gate_id} {f.authority} holding {f.fingerprint} — {f.subject}: {f.remedy}" for f in findings)
    art = workdir / "artifacts"
    unreadable = ""
    if ledger_error:
        import gate_ledger  # noqa: PLC0415 - scripts/ is on sys.path (_gen_inputs); it already read this ledger

        unreadable = (
            f"\n\nTHE OVERRULE LEDGER IS UNREADABLE — {gate_ledger.ledger_path(art)} ({ledger_error}). No overrule "
            "lifts a finding until it reads again: repair or remove the bad line, or re-send the document."
        )
    return (
        f"This generation call DROPS {len(findings)} input document(s) that an earlier call in this "
        f"build sent: {', '.join(f.subject for f in findings)}.\n\n"
        "`previous_scenario_set_id` keeps FLOW IDS stable and inherits NO inputs, so an input you do not re-send "
        "is an input this batch is generated WITHOUT.\n\n"
        f"{lines}\n\n"
        "Compose the call from the WORKDIR, not from what you remember sending — the record of what this build "
        'sends is `$WORKDIR/.gen_input_keys`, which is why a compacted or resumed session is bound by it too. '
        "Control arguments are not checked here: `count`, `redraws`, `interest`, `steering_demands` and "
        "`previous_scenario_set_id` may differ freely, and the final delivery batch is SUPPOSED to omit `redraws`.\n\n"
        "A scenario bundle is consumed by the ONE build it was staged for: once its markers are cleared "
        "(scenario generation Step 6 — `$WORKDIR/.scenario_bundle_id` gone) this gate stops expecting it. "
        "RETIRING any other document on purpose (a corpus deleted) is your decision alone: overrule its finding "
        "with a reason that states why this build no longer needs it, then re-send the call —\n\n"
        f'  python3 {shell_path(f"{PLUGIN_ROOT}/scripts/gate_ledger.py")} overrule --art {shell_path(art)} \\\n'
        "    --gate-id <gate_id> --fingerprint <fingerprint> --reason \"<why this build no longer needs the document>\""
        f"{unreadable}"
    )


def handle(event: Event) -> Decision:
    """Deny a re-draw that drops an input document, or that has no logged reason inside a flow."""
    from _harness import model

    if event.tool != GUARDED_TOOL:
        return model.none()
    bases = marker_dirs(event.raw)
    # CHECK 1 MUST run before the flow gate below, not merely happen to: check 2 returns early outside
    # a flow window and again on a first draw, so placing this after them would make it unreachable
    # in exactly the standalone `/generate-scenarios` regeneration that drops an input just as
    # expensively. Its baseline is the workdir's, so it is scoped by the build either way.
    workdir = resolve_workdir()
    # The backend refuses this drop itself when it says it checks and the call names a predecessor; the local
    # check stays for everything else, and for the two documents the backend only warns about.
    deferred = names_predecessor(event.tool_input) and server_checks_inputs(workdir)
    holding, ledger_error = _missing_input_docs(event.tool_input, workdir, only=SERVER_WARNS_ONLY if deferred else None)
    if holding:
        return model.deny(_teach_missing(holding, workdir, ledger_error=ledger_error))
    # Existence is the whole answer for these two, so `find_marker` (first hit) is enough; the
    # token below takes the UNION, because every copy of it has to be spent.
    if find_marker(FLOW_MARKER, bases) is None:
        return model.none()  # no flow, no loop log, nothing to gate
    if find_marker(SET_MARKER, bases) is None:
        return model.none()  # FIRST draw: there is nothing to re-draw
    if not find_markers(REGEN_TOKEN, bases):
        return model.deny(_teach())
    # No decision, and the token is left alone. The SPEND is `hooks/regen_spent.py`, a PostToolUse
    # companion that consumes it only when a job was actually created (the Pre+Post pair
    # `ask_guard`/`ask_answered` already uses).
    #
    # This hook cannot see the result, so spending here would spend the token on calls that
    # start nothing: a call refused at INPUT VALIDATION would burn the one-shot and deny the next
    # legitimate attempt until a reason already logged and accepted was logged again.
    return model.none()
