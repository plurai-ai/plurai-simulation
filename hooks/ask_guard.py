#!/usr/bin/env python3
"""Default-deny guard for AskUserQuestion while a plurai command flow is active.

A ``PreToolUse`` hook on the ``AskUserQuestion`` matcher. The customer-facing commands
run AUTONOMOUSLY — prose alone demonstrably does not hold that contract (see
docs/adr/0003-flow-fidelity-enforcement.md for the field failures); this hook makes it
mechanical.

MECHANISM — the flow window + one-shot sanctions:
* Each command's Step 0 writes ``$FLOWDIR/.plurai_flow_active`` (line 1 = command name)
  and removes it after the final report renders. While it exists, AskUserQuestion is
  DENIED by default. ``$FLOWDIR`` is keyed by SESSION, not by agent — see ``_workdir`` for
  why (a per-agent location silently disarms both guards for a whole run).
* The few LEGITIMATE asks inside a flow (the suite's grounding gate,
  scan-agent's topology approval, fixes' selection gate, a genuine blocker escalation,
  and a missing run PREREQUISITE only the user can supply — a key, credentials, a live
  run target: not a decision, so autonomy never covered it)
  are executed via a ONE-SHOT sanction: the skill snippet at that exact step writes
  ``$FLOWDIR/.ask_sanctioned`` (content = gate name); this guard consumes (deletes) it
  and allows exactly one ``AskUserQuestion`` **CALL** — not one question. The guard never
  inspects the questions, so it cannot see (or care) how many a call carries: a gate
  needing several related answers should BATCH them into that single call rather than
  sanctioning twice. An ask is impossible unless the flow's own script sanctioned it at
  that step.
* The deny reason TEACHES: it restates the decision table and the sanction mechanism, so
  the model self-corrects and proceeds autonomously (the reason string is shown to the
  model on deny — that is the documented PreToolUse contract).
* Outside every window the guard allows, with one exception: a sub-skill run directly
  (``DIRECT_RUN_GATES``) that sanctioned its scripted ask while the run driver is headless. That
  ask is dispatched as its driver's window would dispatch it, since nobody would answer it.

DESIGN INVARIANTS
-----------------
* **Fail-open.** The mirror image of ``url_guard`` (fail-closed): a crashed guard must
  not lock the whole plugin's ask surface — outside a flow window nothing changes, and
  an internal error allows. Deny fires only on POSITIVE marker detection.
* **The payload cannot crash the guard.** The entry point decodes it (``hooks/run.py``); the
  session id it carries is what locates the flow window, and a missing or garbled payload
  degrades to the environment variable and then to the cwd cascade. The verdict depends only on
  the marker files, and the guard never reads the questions.
* **Harness-neutral.** The guard takes an ``Event`` and returns a ``Decision``; how each harness
  spells the ask tool and the deny lives in ``registry.py`` and ``hooks/_harness/``.
* **A decision, or no opinion** (allow is ``none``, same as url_guard).

CODEX. A Codex flow holds its gates with a turn-ending ask: the model sanctions the gate, asks (as one
plain-text question, or through the async ask tool where it has one) and ends its turn, and the user's
reply is the next prompt. There the guard checks the sanction on the async ask and does NOT spend it:
Codex rejects a call with bad arguments after this hook has run, and a spent sanction would deny the
corrected retry. ``report_guard`` spends the sanction when the turn ends on the question. The blocking
``request_user_input`` is denied inside a window whatever the sanction: it is unavailable in Default
mode, or closes itself with nothing after about two minutes, and either way it never passes a gate.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from _harness import model

if TYPE_CHECKING:
    from _harness.model import Decision, Event

FLOW_MARKER = ".plurai_flow_active"
ASK_SANCTION = ".ask_sanctioned"
#: A sanctioned question that has been ASKED but not yet answered. Written when the sanction is
#: consumed, cleared by ask_answered.py on an answered call. See the note at the consume site.
ASK_PENDING = ".ask_pending"
#: Timestamps of this guard's recent denies, one per line — hook-written, so the permission
#: system never sees the write. The DENY-LOOP BREAKER reads it: after two unsanctioned denies
#: inside the window, the third ask is ALLOWED. The harness aborts a turn after ~3 consecutive
#: denied tool calls, so a guard that can deny without bound can wedge a session it exists to
#: keep on rails (observed live when the classifier also denied the sanction write). The
#: breaker inverts the worst case: the user gets asked a question — mildly off-contract —
#: instead of the flow dying. Reset on every consumed sanction and on the breaker itself;
#: swept by arm's residue clear and flow_cleanup.
ASK_DENY_STREAK = ".ask_deny_streak"
#: Denies older than this no longer count toward the breaker: two drift asks an hour apart are
#: two separate teach moments, not a loop. Same window as the sanction TTL, deliberately — both
#: bound "how long ago still counts as *now*" for this guard.
DENY_STREAK_WINDOW_SECONDS = 600
#: How many recent denies stand before the next ask is allowed through.
DENY_STREAK_LIMIT = 2
#: The ``.ask_pending`` content a breaker allowance writes. NOT a gate name: report_guard's
#: unanswered-ask branch re-arms a pending gate into a fresh ``.ask_sanctioned`` (so a user who
#: rejected the tool to clarify can be re-asked) — and re-arming THIS content would launder the
#: breaker into a real bearer token: allow → reject → re-arm → allow, default-allow for the rest
#: of the window. report_guard imports this constant to skip exactly that promotion; a rejected
#: breaker ask falls back to the normal deny/teach path, so the guard stays bounded at one
#: allowed ask per two denies.
BREAKER_GATE = "deny-loop-breaker"
#: Records that THIS window has already spent its one breaker allowance. Swept with the rest of
#: the residue on the next arm (flow_arm.RESIDUE_MARKERS) and by the flow close.
BREAKER_SPENT = ".ask_breaker_spent"

#: How long a sanction stays a bearer token. It is written IMMEDIATELY BEFORE the question by
#: contract (`flow-window.sh sanction`), so ten minutes is enormous for its intended use — and
#: short enough that one written and then NOT spent (the flow changed its mind, the ask was
#: answered in prose, the turn died) cannot silently authorize an unrelated ask an hour later,
#: including the DRIVER's when this runs as a sub-step. Aged by the marker's mtime; an OSError
#: reading it resolves to LIVE, because failing closed here would deny a legitimately
#: sanctioned blocker escalation and wedge the flow.
SANCTION_TTL_SECONDS = 600

#: WHICH gates each armed flow may spend — the register's ask budget, enforced at runtime.
#: A flat namespace check was not enough: with the window armed for `scan-agent`, minting
#: `which-kb` (a /build-rag-corpus gate) consumed cleanly and allowed the very question the
#: allowlist was added to stop. The name check made the mistake cost one word. A gate is only
#: a gate INSIDE the flow whose text scripts it.
#:
#: Each entry is the command's own gates PLUS the gates of the sub-skills it drives — a driven
#: sub-skill arms no window of its own and legitimately asks inside its driver's, so its gates
#: are part of that driver's budget. `blocker` is universal by design: every flow may escalate
#: something only the user can supply. Pinned to the skills' own snippets, and to
#: docs/flow-fidelity-register.md's budget table, by test_flow_register_contract.py.
_UNIVERSAL_GATES = frozenset({"blocker", "workdir-location"})
#: Gates owned by sub-skills, inherited by whichever flow drives them.
_SUBSKILL_GATES = frozenset({
    "anonymization-posture",  # build-rag-corpus, upload-*, analyze-traces
    # SPLIT OUT of `which-kb` (Step 3's reuse-vs-rebuild). One name cannot carry two questions once
    # a headless run has to answer it: `which-kb` asks WHICH bases to build ("default: all of them"
    # in the skill's own words), while this one asks whether to reuse the corpus the scenarios and
    # evals are already grounded against — and `all` is not an answer to that.
    "corpus-reuse",           # build-rag-corpus
    "delete-confirm",         # build-rag-corpus, list-scenarios, upload-*
    "extraction-scope",       # build-rag-corpus
    "input-fix",              # generate-evals
    "rescan-diff-approval",   # simulation-setup
    "which-kb",               # build-rag-corpus
    "which-scenario",         # simulation-diagnose
})
FLOW_GATES: dict[str, frozenset[str]] = {
    # scan-agent drives simulation-setup, whose overwrite guard asks inside this window, and the
    # hosted-agent setup, whose knowledge export asks about articles outside the agent's audience.
    "scan-agent": _UNIVERSAL_GATES | {
        "component-pick", "saved-setup-gate", "topology-approval", "rescan-diff-approval", "document-generation", "knowledge-audience",
        "grounding-consent",
    },
    # The suite drives build-rag-corpus (its Step-0 grounding gate), generate-evals,
    # simulation-run and analyze-simulation, so it inherits their gates.
    "generate-suite": _UNIVERSAL_GATES | _SUBSKILL_GATES | {
        "grounding-gate", "degraded-mode-consent", "observability-opt-in", "platform-key", "provider-key",
    },
    "run": _UNIVERSAL_GATES | {"observability-opt-in", "platform-key", "provider-key", "which-scenario"},
    # analyze drives simulation-run, and reaches edit-evals / edit-scenarios / diagnose. A harness repair is
    # not on this list: it is applied without a question in every mode, so no flow can ask one.
    "analyze": _UNIVERSAL_GATES | {
        "degraded-mode-consent", "observability-opt-in", "platform-key", "provider-key",
        "which-scenario", "delete-confirm", "apply-brief",
    },
    "fixes": _UNIVERSAL_GATES | {"fix-selection"},
    # traces drives trace-to-scenarios' brief-driven mode, whose confirmation asks inside it.
    "traces": _UNIVERSAL_GATES | {
        "anonymization-posture", "apply-brief", "delete-confirm", "sink-project",
        "input-fix", "which-kb", "extraction-scope", "trace-scenario-selection",
    },
    # /simulate asks only its start question (and its Connect / Rescan / credential follow-ups,
    # each under the same gate) inside its OWN window. It drives nothing: every stage it invokes
    # arms that stage's own window, so the stages' gates are spent under their own budgets.
    "simulate": _UNIVERSAL_GATES | {"simulate-start"},
}

#: Sub-skill gates whose command a run driver can also run DIRECTLY, with no window armed, mapped to
#: that command. A sub-skill arms no window of its own, so run directly its sanctioned ask is not
#: this guard's to police — until nobody is at the terminal. Headless, that ask would reach no one,
#: so it is dispatched exactly as its driver's window dispatches it: the declared default, recorded.
DIRECT_RUN_GATES: dict[str, str] = {
    "trace-scenario-selection": "trace-to-scenarios",
}

#: Every gate name a skill's own text may sanction — the union of the per-flow budgets above,
#: derived rather than restated so the two can never disagree. WHY a name check at all: the
#: sanction marker is written by the same model this guard polices, so the marker alone is a
#: bearer token that model can mint for any question it talks itself into. The gate NAME is the
#: one part of a sanction that is machine-checkable: a name no skill defines is a question no
#: skill scripts, and the ask is denied with the same teach text as an unsanctioned one (the
#: marker is VOIDED, not spent). denial_watch imports this set too — its hook-side completion of
#: a denied raw sanction write must not become the mint this check closes.
KNOWN_GATES = frozenset().union(*FLOW_GATES.values())


def _show_waiting(event: Event, bases: list[Path]) -> None:
    """The overview page's running step reads "waiting for your answer" (``simulate_continue.wait_page``). Never raises."""
    try:
        import simulate_continue  # noqa: PLC0415 - hooks/ is on sys.path at call time

        simulate_continue.wait_page(event, bases, True)
    except Exception:  # noqa: BLE001 - the page is an affordance; the ask is already allowed
        pass


def _sanction_age(path: Path) -> float | None:
    """Seconds since the sanction was written, or None when the age cannot be read."""
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _ask_instruction(script: str, turn_ending: bool) -> str:
    """The deny text's last step: how to ask the one sanctioned question. ``script`` is already a quoted word."""
    if turn_ending:
        return (
            f"sanction the gate, then ask it and end the turn: bash {script} sanction <gate-name> — then ask as "
            "ONE plain-text question with its options, or through the async ask tool; carry every question "
            "that gate needs in that one ask, and end the turn on it. The user's reply is your next prompt."
        )
    return (
        f"sanction the gate and re-ask, one call: bash {script} sanction <gate-name> — that buys one CALL, "
        "not one question, so batch every question that gate needs into that single ask."
    )


def _blocking_ask_reason(command: str) -> str:
    """Why the blocking Codex ask is refused, and what to do instead."""
    return (
        f"The /{command} flow does not use {CODEX_BLOCKING_ASK}: it is unavailable in Default mode, and where it is "
        "enabled it closes by itself after about 2 minutes with an empty answer, so it never holds a gate. "
        + _teach(command, turn_ending=True)
    )


def _flow_window_sh() -> str:
    """The absolute path of the sanctioned marker writer, derived from this hook's own location.

    The teach text instructs actions, and every action it names must be a single invocation of
    this ONE script: that is the shape the auto-mode permission classifier treats as routine
    work. Naming a marker PATH instead ("write the gate name to <path>") steers models into
    a raw `echo >` that the classifier denies as self-modification, and the flow deadlocks with
    every recovery itself denied.
    """
    return str(Path(__file__).resolve().parent.parent / "scripts" / "flow-window.sh")


def _flow_window_word() -> str:
    """``_flow_window_sh()`` as one quoted word for a command the text tells the model to run (``plugin_root.shell_path``)."""
    from plugin_root import shell_path  # noqa: PLC0415 - hooks/ is on sys.path at call time

    return shell_path(_flow_window_sh())


#: The one built-in ask a Codex flow must not use (the codec marks a call to it ``Event.blocking_ask``).
CODEX_BLOCKING_ASK = "request_user_input"


def _teach(command: str, *, turn_ending: bool = False) -> str:
    # Generic on purpose: this fires for every flow command, and handing (say) /scan-agent
    # the suite's iteration table would misdirect it at exactly the moment it is blocked
    # and looking for guidance. The command's OWN skill carries its decision table.
    script = _flow_window_word()
    return (
        f"The /{command} flow is running AUTONOMOUSLY — every decision inside it is pre-authorized by the "
        f"invocation. Do not ask: apply the branch's rule from the /{command} skill's own decision table "
        "(continue/land the loop per its caps, fixed counts, defaults at every branch) and continue. "
        "Cost/spend is internal telemetry, never a decision input and never shown. "
        "An action the flow's own text instructs — an upload it scripts, a sub-agent it mandates, "
        "a file it writes — is pre-authorized by the invocation: do it and announce it in product "
        "terms, never ask consent for it. "
        "A gate's finding or verdict is never an ask: decide it yourself, per "
        f"{Path(__file__).resolve().parent.parent / 'docs' / 'gate-findings.md'}. "
        f"If (and only if) this is a gate the skill itself marks as a sanctioned ask, a genuine BLOCKER "
        f"per the skill's definition, or a PREREQUISITE only the user can supply that blocks the rest of "
        "the flow (a provider key, credentials, a run target that vanished — never a decision, never "
        f"fixable from here) — {_ask_instruction(script, turn_ending)} Never write or delete a flow marker file directly; the "
        "script call is the sanctioned shape. "
        f"If NO flow is actually running (a stale window from a crashed session), run: bash {script} "
        f"close {command} — then re-ask."
    )


def _recent_denies(streak: Path) -> list[float]:
    """The deny timestamps still inside the breaker window. Unreadable file → no history."""
    try:
        lines = streak.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    cutoff = time.time() - DENY_STREAK_WINDOW_SECONDS
    out: list[float] = []
    for line in lines:
        try:
            stamp = float(line.strip())
        except ValueError:
            continue
        if stamp >= cutoff:
            out.append(stamp)
    return out


def _deny_or_break(window: Path, reason: str, *, breakable: bool = True) -> Decision:
    """Deny with ``reason`` — unless this would be the third deny inside the window.

    ``breakable=False`` still RECORDS the deny (so a genuine mixed deny-loop can still break on
    a later ask) but can never allow on THIS call. It is for the voided-mint path: banking the
    denial keeps the wedge protection, while allowing there would make the gate allowlist
    defeatable by retry — mint the same invented gate three times and the third ask reaches the
    user with no sanction at all (reproduced in review).

    The breaker's asymmetry is the point: an unsanctioned question reaching the user costs a
    moment of their attention; a guard that CAN deny without bound has been observed to feed the
    harness's consecutive-denial abort and kill the whole flow. Every marker touch is guarded —
    an unwritable streak file degrades to plain denies (today's behaviour), never to a crash.
    """
    streak = window.parent / ASK_DENY_STREAK
    recent = _recent_denies(streak)
    spent = (window.parent / BREAKER_SPENT).exists()
    if breakable and len(recent) >= DENY_STREAK_LIMIT and not spent:
        print(
            f"ask_guard: deny-loop breaker — allowing this ask after {len(recent)} consecutive "
            "denies; the flow was headed for the harness's consecutive-denial abort. ONCE per "
            "window: the next deny-loop is denied through.",
            file=sys.stderr,
        )
        try:
            (window.parent / ASK_PENDING).write_text(BREAKER_GATE, encoding="utf-8")
        except OSError:
            pass
        try:
            # ONE per window. Unbounded, the breaker is a scheduled off-contract ask: a model
            # that wants a question the flow forbids gets it every third try, forever — the
            # UX-1 consent ambush reaches the user on the third attempt with no sanction at
            # all (verified in review by an executed reproduction). Its purpose is to keep a
            # wedged flow alive, and one allowance does that; a second is a habit. Cleared by
            # arming (residue) and by the flow close, so the next flow starts with it fresh.
            (window.parent / BREAKER_SPENT).write_text(str(time.time()), encoding="utf-8")
        except OSError:
            pass
        try:
            streak.unlink()
        except OSError:
            pass
        return model.none()
    try:
        streak.write_text("".join(f"{t}\n" for t in [*recent, time.time()]), encoding="utf-8")
    except OSError:
        pass
    return model.deny(reason)


def _headless_deny(
    marker: Path, sanction: Path | None, command: str, gate: str, mode_source: str,
) -> Decision:
    """Dispatch ONE sanctioned gate for a run driver who is not there.

    THE one seam. This guard already enumerates the legitimate asks and already states the split
    headless dispatches on — a DECISION the flow may take, versus *"a missing run PREREQUISITE only
    the user can supply … not a decision, so autonomy never covered it"* (the module docstring) —
    so ``_run_driver`` partitions these same sanction NAMES rather than introducing a second
    vocabulary that could drift from them.

    A PreToolUse hook cannot hand the model an answer; it can only deny with a reason the model
    reads. So "resolve to the declared default" IS a deny whose reason states that default and
    tells the flow to apply it and continue — the same shape as the teach text. The FINDING, by
    contrast, is written HERE: the pressure that turned "do not stall" into "Got it — 20 furniture
    products" (see the ASK_PENDING note below) would turn "file an run_driver_input_required" into a
    fabricated one just as easily, and a hook is not under that pressure.

    Never breakable. The deny-loop breaker exists because *"the user gets asked a question — mildly
    off-contract — instead of the flow dying"*; with no user, that trade inverts exactly, and an
    allowed ask is a run that hangs until its budget runs out.
    """
    import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path at call time

    if sanction is not None:
        try:
            sanction.unlink()  # spent on this dispatch, never left to authorize a later ask
        except OSError:
            pass
    from _workdir import resolve_workdir  # noqa: PLC0415 - hooks/ is on sys.path at call time

    workdir = resolve_workdir()
    art = workdir / "artifacts" if workdir is not None else None
    disposition = _run_driver.gate_disposition(gate, art=art)
    declared = (
        f"run_driver: headless (declared by {mode_source}) — nobody is at the terminal, so the "
        f"/{command} flow does not ask. "
    )
    # A typed fact the dispatch could not read is carried on the row and said, never silently skipped.
    note = disposition.note if disposition is not None else ""
    noted = {"note": note} if note else {}
    said = f" NOTE — {note}." if note else ""
    # RECORDED, not merely handed over, and DURABLY — in the session's flow dir AND beside
    # `unasked`'s rows in `<workdir>/artifacts`, by the same writer — so a later session's run.json
    # sees this decision or this stop. Without the record the run's own artifact would leave which
    # defaults fired, or which prerequisite stopped it, readable only in the model's prose, which is
    # the channel this design exists to distrust. Each claim below states which copy landed: the
    # session copy is what this run's own readers (`headless_driver`, `report_guard`) act on, the
    # durable one is what a later session reads — a gap in either is said, never implied away.
    if disposition is not None and disposition.kind == "declared":
        # The run driver's OWN answer, declared in their config: handed over as theirs, never as a default.
        _, gaps = _run_driver.record_durably(
            _run_driver.ANSWER_DECLARED, session_base=marker.parent, art=art, gate=gate, command=command,
            answer=disposition.value, declared_in=disposition.declared_in, mode=_run_driver.MODE_HEADLESS,
            declared_by=mode_source, **noted)
        unrecorded = gaps.get(_run_driver.SESSION_BASE)
        durable_gap = gaps.get(_run_driver.DURABLE_BASE) if unrecorded is None else None
        return _deny_or_break(
            marker,
            declared + _run_driver.declared_line(gate, disposition)
            + (f" (This run could NOT record that answer — {unrecorded} — so say in that line "
               "that the record is incomplete.)" if unrecorded is not None else "")
            + (f" (Recorded for this session only: the durable copy did NOT land ({durable_gap}) — say so in that line.)"
               if durable_gap is not None else "")
            + said,
            breakable=False,
        )
    if disposition is not None and disposition[0] == "default":
        # A scope gate's approving default backs exactly the items its scope proposal named, as an approval would.
        scope = _run_driver.scope_of_default(gate, disposition[1], art=art, mode=_run_driver.MODE_HEADLESS, command=command)
        _, gaps = _run_driver.record_durably(
            _run_driver.DEFAULT_APPLIED, session_base=marker.parent, art=art, gate=gate, command=command, default=disposition[1],
            **noted, **scope)
        unrecorded = gaps.get(_run_driver.SESSION_BASE)
        durable_gap = gaps.get(_run_driver.DURABLE_BASE) if unrecorded is None else None
        return _deny_or_break(
            marker,
            declared
            + f"The '{gate}' gate's DECLARED DEFAULT is '{disposition[1]}': apply it, say in ONE line that you "
            "applied the headless default, and continue the flow. Never invent an answer the run driver did not give."
            + _run_driver.prerequisite_rule(gate, disposition[1])
            # Said only when it happened: the run's own record is what a reader counts the
            # defaults from, so a decision that never reached the file must not be left to look
            # like one that did.
            + (f" (This run could NOT record that decision — {unrecorded} — so say in that line "
               "that the record is incomplete.)" if unrecorded is not None else "")
            + (f" (Recorded for this session only: the durable copy did NOT land ({durable_gap}), so a later "
               "session's run record will not show this decision — say so in that line.)" if durable_gap is not None else "")
            + said,
            breakable=False,
        )
    # No default — by classification, or (unreachable while the G2 sweep holds) by omission. Both
    # land here, because the only safe answer to "I cannot ask" is not to answer.
    needs = disposition[1] if disposition is not None else "declare what this gate needs, or run interactively"
    finding, gaps = _run_driver.record_durably(
        _run_driver.INPUT_REQUIRED, session_base=marker.parent, art=art, gate=gate, command=command, needs=needs, **noted)
    unrecorded = gaps.get(_run_driver.SESSION_BASE)
    durable_gap = gaps.get(_run_driver.DURABLE_BASE) if unrecorded is None else None
    # THE STOP IS THE SAME EITHER WAY; only the claim about the record changes. Asserting a typed
    # finding "is now on this run's record" after a failed append made the model report a finding
    # the driver could not see, so the round carried on past a prerequisite it had refused to
    # guess — the one outcome this dispatch exists to prevent.
    landed = (
        f"A typed `{finding['kind']}` finding is now on this run's record: {needs}."
        if unrecorded is None and durable_gap is None else
        f"A typed `{finding['kind']}` finding is on this SESSION's record only — its durable copy did NOT land "
        f"({durable_gap}), so a later session will not see it: {needs}. Say that when you report it."
        if unrecorded is None else
        f"This run could NOT record the `{finding['kind']}` finding ({unrecorded}), so nothing "
        f"downstream will see it: {needs}. Report it in YOUR OWN WORDS."
    )
    return _deny_or_break(
        marker,
        declared
        + (f"The '{gate}' gate is a PREREQUISITE only the user can supply, so it has NO default and this run does "
           "not guess one. " if gate in _run_driver.PREREQUISITE_GATES else
           f"The '{gate}' gate has no default this run's typed facts support, and this run does not guess one. ")
        + f"{landed} STOP the flow, "
        "report that finding, end the run on it, and do NOT fabricate the run driver's answer — a run that did exactly that uploaded a "
        "20-record corpus which became the whole suite's product universe." + said,
        breakable=False,
    )


def _autonomous_dispatch(marker: Path, sanction: Path, command: str, gate: str, plan: dict) -> Decision | None:
    """Dispatch ONE sanctioned gate inside a live ``/simulate`` run. The decision when it was answered here, else None.

    The user is present, so only the QUESTION is removed, never the gate: a gate that still asks
    (``_run_driver.AUTONOMOUS_ASKS``, or a fact-dispatched gate whose facts need the user) returns
    None and takes the interactive path. Every other gate is denied WITH its default, recorded by
    this hook exactly as headless records it, with ``mode: autonomous, declared_by: simulate``. A
    config-declared answer is handed over first, as the run driver's, then the user's answer at the
    start question (both filed as the run driver's own answer, never as a default). Never breakable:
    the deny is the answer, not a teach moment.
    """
    import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path at call time
    from _workdir import resolve_workdir  # noqa: PLC0415

    workdir = resolve_workdir()
    art = workdir / "artifacts" if workdir is not None else None
    line = _run_driver.apply_autonomous(
        gate, plan, session_base=marker.parent, art=art, command=command, workdir=workdir)
    if line is None:
        return None
    try:
        sanction.unlink()  # spent on this dispatch, never left to authorize a later ask
    except OSError:
        pass
    return _deny_or_break(marker, line, breakable=False)


def _headless_outside_a_window(payload: dict, bases: list[Path]) -> Decision:
    """Dispatch a directly run sub-skill's sanctioned ask when the run driver is headless.

    Only a fresh sanction for a gate in ``DIRECT_RUN_GATES`` qualifies: the skill's own text wrote
    it immediately before its one question. Everything else outside a window — an interactive
    run driver, no sanction, an expired one, any other gate — is allowed.
    """
    from _workdir import find_marker, resolve_workdir  # noqa: PLC0415

    sanction = find_marker(ASK_SANCTION, bases)
    if sanction is None:
        return model.none()
    age = _sanction_age(sanction)
    if age is not None and age > SANCTION_TTL_SECONDS:
        return model.none()
    gate = sanction.read_text(encoding="utf-8", errors="replace").strip()
    command = DIRECT_RUN_GATES.get(gate)
    if command is None:
        return model.none()
    import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path at call time

    run_driver = _run_driver.resolve(payload, workdir=resolve_workdir())
    if run_driver.headless:
        # The sanction's directory is the session's flow directory, where the driver's window
        # would have stood, so the record and the deny streak land where the window's would.
        return _headless_deny(sanction, sanction, command, gate, run_driver.source)
    return model.none()


def handle(event: Event) -> Decision:
    """Deny an unsanctioned ask inside a flow window; spend the sanction of a sanctioned one.

    The registered matcher decides which tool calls reach this, so the guard never looks at the
    tool or at the questions. An internal error propagates to the entry point, which allows the ask
    and names the failure on stderr: a crashed guard must not lock the ask surface, and must not
    do it silently either, since an allowed ask with nobody to answer it stalls a headless run.
    """
    from _workdir import find_marker, marker_dirs, resolve_workdir  # noqa: PLC0415

    payload = {"session_id": event.session_id}
    bases = marker_dirs(payload)
    marker = find_marker(FLOW_MARKER, bases)
    if marker is None:
        # No active flow → asks behave normally, except a directly run sub-skill's scripted ask
        # while nobody is at the terminal.
        return _headless_outside_a_window(payload, bases)
    # WHO is running this. Read LIVE from the declaration rather than from the arm-time stamp:
    # the stamp is the run's RECORD (`skills/simulation-run/scripts/build_run_json.py` is its
    # one reader), and one verdict deserves one source of truth. Resolved only past the window check, so a session outside
    # a flow pays for nothing; inside one it costs a second workdir cascade per denied ask,
    # which is the same cascade `marker_dirs` just ran.
    import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path at call time

    run_driver = _run_driver.resolve(payload, workdir=resolve_workdir())
    turn_ending = event.turn_ending_ask
    if turn_ending and event.blocking_ask:
        # Denied whatever the sanction says, and the sanction is left as it is: the model asks again the
        # way the reason says, and that ask is what the sanction was written for. Never breakable: the
        # breaker's allowance would let through a call that answers itself with nothing.
        lines = marker.read_text(encoding="utf-8", errors="replace").splitlines()
        return _deny_or_break(marker, _blocking_ask_reason((lines[0].strip() if lines else "") or "plurai-simulation"), breakable=False)
    # The sanction is looked up independently of WHERE the window was found: a flow armed
    # by flow_arm (session dir) can legitimately be sanctioned by a skill snippet that
    # fell back to the agent dir, and vice versa. Requiring both in one place would deny
    # a gate the flow's own script authorized.
    sanction = find_marker(ASK_SANCTION, bases)
    age = _sanction_age(sanction) if sanction is not None else None
    if sanction is not None and age is not None and age > SANCTION_TTL_SECONDS:
        # EXPIRED. Unlink first so the next ask sees a clean state, then deny with the same
        # teach text plus the age — "the marker is there and the ask was denied anyway" is
        # the one thing this branch must never look like.
        gate = sanction.read_text(encoding="utf-8", errors="replace").strip() or "ask"
        try:
            sanction.unlink()
        except OSError:
            pass
        lines = marker.read_text(encoding="utf-8", errors="replace").splitlines()
        command = (lines[0].strip() if lines else "") or "plurai-simulation"
        return _deny_or_break(marker, _teach(command, turn_ending=turn_ending)
                              + f" (the '{gate}' sanction was written {int(age)}s ago — EXPIRED after "
                                f"{SANCTION_TTL_SECONDS}s; sanction it again immediately before the question.)",
                              breakable=not run_driver.headless)
    if sanction is not None:
        gate = sanction.read_text(encoding="utf-8", errors="replace").strip()
        lines = marker.read_text(encoding="utf-8", errors="replace").splitlines()
        armed = (lines[0].strip() if lines else "") or ""
        # SCOPED to the armed flow, not merely known: a gate belonging to another command
        # bought any question at all before this check (verified — `which-kb` consumed
        # cleanly inside a scan and allowed an upload-consent ask). An unrecognised ARMED
        # name (a stale or hand-written window) falls back to the flat namespace, so a
        # window this guard cannot classify never becomes a free pass either.
        allowed = FLOW_GATES.get(armed, KNOWN_GATES)
        if gate not in allowed:
            # VOID, not spend. The marker is model-written, so an unknown name is a
            # question no skill scripts — the observed shape is a self-minted consent gate
            # for an action the flow's own step already instructs. Unlink first so a retry
            # of the same ask starts from a clean (denied) state.
            try:
                sanction.unlink()
            except OSError:
                pass
            command = armed or "plurai-simulation"
            why = (
                f"no skill defines that gate, so no skill scripts the question it was minted for"
                if gate not in KNOWN_GATES
                else f"that gate belongs to another command, and /{command} does not script it"
            )
            # A VOIDED MINT DOES NOT COUNT TOWARD THE BREAKER. The streak exists to rescue a
            # model stuck in a good-faith deny/teach loop; minting a gate no skill scripts is
            # not that, and letting it accrue made the allowlist defeatable by retry — mint
            # the same invented gate three times and the third ask reached the user with no
            # sanction at all (reproduced in review). Denied, taught, and not banked.
            return _deny_or_break(
                marker,
                _teach(command, turn_ending=turn_ending)
                + f" (the '{gate or 'unnamed'}' sanction was VOIDED — {why}.)",
                breakable=False,
            )
        if run_driver.headless:
            # THE DISPATCH. The gate is scripted and in this flow's budget; what is missing is
            # the run driver. Resolve it from the declared taxonomy, or file the typed finding.
            return _headless_deny(marker, sanction, armed or "plurai-simulation", gate, run_driver.source)
        # A live /simulate run: the decision gates take their defaults, the asks still ask.
        # Declared headless wins above; with no live plan this is the interactive path unchanged.
        plan = _run_driver.autonomous_plan(bases)
        if plan is not None:
            answered = _autonomous_dispatch(marker, sanction, armed or "plurai-simulation", gate, plan)
            if answered is not None:
                return answered
        # Metadata only — never the questions themselves, which carry customer content.
        questions = event.tool_input.get("questions")
        print(
            f"ask_guard: consumed sanction gate={gate or 'ask'} age={int(age or 0)}s "
            f"questions={len(questions) if isinstance(questions, list) else 'unknown'}",
            file=sys.stderr,
        )
        if turn_ending:
            # NOT spent here: Codex rejects a call with bad arguments after this hook has run, and a
            # spent sanction would deny the corrected retry. The turn end spends it (report_guard), and a
            # reply that comes back inside the turn spends it too (ask_answered). A deny streak still ends.
            try:
                (marker.parent / ASK_DENY_STREAK).unlink()
            except OSError:
                pass
            return model.none()
        # One-shot: consume the sanction so the NEXT ask is denied again.
        try:
            sanction.unlink()
        except OSError:
            pass  # unremovable sanction degrades to allow-until-removable, never to a stuck deny
        # Record that a sanctioned question is now IN FLIGHT. The guard runs at PreToolUse, so
        # the marker is already gone by the time the user answers — or declines to. When they
        # reject the tool to ask something first, the Stop hook then saw no sanction, reported
        # the turn as a mid-flow stall, and told the model to take the next step immediately
        # with no questions. Live, on the corpus-scope gate, the model complied by INVENTING
        # the answer ("Got it — 20 furniture products", which no user message ever said) and
        # uploading a 20-record corpus that capped every product draw in the whole suite.
        # ask_answered.py clears this on an answered call; a survivor at Stop means the turn
        # legitimately ended on the user.
        try:
            (sanction.parent / ASK_PENDING).write_text(gate or "ask", encoding="utf-8")
        except OSError:
            pass  # the marker is an affordance; failing to write it must never deny the ask
        try:
            (marker.parent / ASK_DENY_STREAK).unlink()  # a consumed sanction ends the streak
        except OSError:
            pass
        # The question now reaches the user: the overview page says so until it is answered (ask_answered). Run after the
        # allow is written, so a slow page write cannot turn this allow into a failure decision at the handler deadline;
        # the harness still waits for this process to exit before it shows the question.
        return model.none().after(lambda: _show_waiting(event, bases))
    lines = marker.read_text(encoding="utf-8", errors="replace").splitlines()
    command = (lines[0].strip() if lines else "") or "plurai-simulation"
    reason = _teach(command, turn_ending=turn_ending)
    if run_driver.headless:
        # An UNSANCTIONED ask headless is drift, exactly as it is interactively — the teach
        # text still applies — but the escape hatch it offers ("sanction and re-ask") now
        # leads to the dispatch above rather than to a human, so say which world this is.
        reason += (
            f" (run_driver: headless, declared by {run_driver.source} — nobody will answer an ask in "
            "this run. A gate this flow genuinely scripts resolves to its declared default, or files a typed "
            "run_driver_input_required finding; anything else is for you to decide and continue.)"
        )
    return _deny_or_break(marker, reason, breakable=not run_driver.headless)
