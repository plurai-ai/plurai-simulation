"""WHO is running the command — the declared run driver, and what each gate does without one.

The customer-facing flows were written for ONE run driver shape: a human at a terminal who can be
asked a question. A CI pipeline, a nightly suite or a remote agent driving the plugin reaches the
same gates with nobody to answer, and the flow parks. This module is the ONE declaration that
shape is dispatched on, plus the per-gate taxonomy the dispatch reads.

THE DECLARATION, AND ITS PRECEDENCE
-----------------------------------
``workspace.run_driver: interactive | headless`` in ``plurai.config.yaml`` (validated by
``skills/simulation-setup/scripts/verify_config.py``). Absent, the mode is ``interactive`` and
every hook asks as it always does.

``PLURAI_SIM_NONINTERACTIVE=1`` (read by ``simulation-setup`` Step 7 and documented in
``references/claude-skill-action-channel.md``) is an alias for the headless declaration, so there is
one headless switch and the two cannot disagree. The precedence:

1. an explicit ``workspace.run_driver`` WINS, in BOTH directions. ``interactive`` beats the env var
   — a CI image that exports ``PLURAI_SIM_NONINTERACTIVE`` globally must not silently
   un-interactive a human's run in that shell;
2. else ``PLURAI_SIM_NONINTERACTIVE=1`` means ``headless`` (the accepted alias);
3. else ``interactive``.

DECLARED, NEVER SNIFFED. No TTY probe, no ``CI=true`` heuristic, no command-name special case: a
sniff passes a fixture test and fails the real case, and it cannot be flipped on one repo to prove
the mechanism. ``hooks/cron_gate.py``'s read of ``permission_mode`` is the one precedent in the
tree for a DECLARED session signal, and it is the shape this follows.

THE GATE TAXONOMY — DECISION vs PREREQUISITE
--------------------------------------------
``ask_guard`` already enumerates the legitimate in-flow asks and already states the split, in its
own words: *"a missing run PREREQUISITE only the user can supply — a key, credentials, a live run
target: not a decision, so autonomy never covered it"*. That is the taxonomy, so this module
partitions the SANCTION NAMES (``ask_guard.KNOWN_GATES``, the names
``scripts/flow-window.sh sanction <name>`` already writes) rather than inventing a second
vocabulary that could drift from them:

* :data:`GATE_DEFAULTS` — a DECISION. Headless resolves it to the declared default and the flow
  continues without a prompt.
* :data:`PREREQUISITE_GATES` — something only the user can supply. Headless takes its SAFE
  default from :data:`PREREQUISITE_DEFAULTS` (the guarded step does not run) and continues; a gate
  with NO safe default files a typed ``run_driver_input_required`` finding and stops. **It never
  guesses**, and every finding is written
  BY THE HOOK: ``hooks/ask_answered.py`` records what this system does when an ask cannot complete.
  A model pressed not to stall can invent the answer to an ask it could not make, and an invented
  corpus scope becomes the product universe of every scenario in the suite. A model asked to emit
  its own "I could not ask" finding is under exactly that pressure; a hook is not.

A DEFAULT IS A RUN DRIVER'S ANSWER AND NEVER GROUND TRUTH. Every value in :data:`GATE_DEFAULTS` is a
token from :data:`DEFAULT_VOCABULARY` — a choice among the options the skill already offers, never
a count, a name, a path or any other content that could reach a scenario, a corpus or a judge.
That is mechanical (``test_run_driver.py``), because the fabrication above is precisely a
defaulted run driver's answer becoming the product universe.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NamedTuple

MODE_INTERACTIVE = "interactive"
MODE_HEADLESS = "headless"
#: Every mode that may be declared. `verify_config.py` validates against this same tuple.
MODES = (MODE_INTERACTIVE, MODE_HEADLESS)
#: The THIRD mode, and deliberately NOT in :data:`MODES`: nobody declares it. It is in force only
#: while a ``/simulate`` run's plan file is live (:func:`autonomous_plan`), and only ``ask_guard``
#: and ``hooks/simulate_continue.py`` read it. :func:`resolve` never returns it, so the config
#: validator, the arm-time stamp and the run record are unchanged. Precedence: a declared headless
#: run driver, then this, then interactive.
MODE_AUTONOMOUS = "autonomous"
#: The mode when no run driver is declared.
DEFAULT_MODE = MODE_INTERACTIVE

#: An alias for the headless declaration (see the precedence in the docstring).
ENV_ALIAS = "PLURAI_SIM_NONINTERACTIVE"

CONFIG_NAME = "plurai.config.yaml"
ROOT_KEY = "workspace"
SUB_KEY = "run_driver"

#: The mode in force for the armed flow, stamped beside the window by whichever half armed it.
#: A SEPARATE file, deliberately: `.plurai_flow_active` holds one command name and is compared
#: WHOLE-FILE by `scripts/flow-window.sh`, so a second line there makes every arm read the window
#: as foreign (see hooks/flow_arm.py's note on exactly that).
MODE_MARKER = ".run_driver"
#: The typed findings this run filed, one JSON object per line. Hook-written.
FINDINGS_MARKER = ".run_driver_findings.jsonl"
#: The `kind`s this module files, all HOOK-WRITTEN into FINDINGS_MARKER.
#:
#: `INPUT_REQUIRED` is the refusal — a gate with no declared default, not guessed.
#: `DEFAULT_APPLIED` is its mirror, and it exists for the same reason: a round that says only what
#: it refused leaves WHICH decisions it took for the run driver readable in one place only — the
#: model's own one-line prose, which is the channel this whole design exists to distrust. The hook
#: sees the gate reached and the default handed over; that is what it attests, and it is the record
#: `run.json` carries to the next critic.
INPUT_REQUIRED = "run_driver_input_required"
DEFAULT_APPLIED = "run_driver_default_applied"
#: A refusal of the software's version, filed by the script that stopped on it when the run driver is headless:
#: ``source`` names the stopper (one of :data:`REFUSAL_SOURCES`) and ``text`` is the text the customer reads, verbatim. It
#: belongs to no gate, so no gate reader treats it as one. The headless driver ends the phase red on it, because the turn
#: that said the text exits 0.
VERSION_REFUSED = "run_driver_version_refused"
#: The kinds of stop text (`report_guard._stop_text_kind`), which a /simulate plan records as the kind of its halt and a
#: headless run files in :data:`HALTED`.
STOP_CAPACITY, STOP_CREDIT, STOP_CONTACT = "capacity", "credit", "contact"
#: A stop text the Stop hook let through, filed when the run driver is headless: ``halt`` is the stop's kind (``capacity``,
#: ``credit`` or ``contact``) and ``text`` is the stop text with its done lines, verbatim. The turn that said it exits 0,
#: so the headless driver ends the phase red on this row instead of relaunching turns until its bound. A halt is never
#: retried by the driver: a person reads the log and decides.
HALTED = "run_driver_halted"
#: The stoppers that file :data:`VERSION_REFUSED`: the backend check (``scripts/backend_check.py``), the resolver's stop for
#: an SDK that is not the release's pin (``scripts/resolve_plurai.py``), an SDK command the backend refused while it
#: ran (``scripts/_client_handshake.py``'s ``sdk_refusal_message``, which every SDK call site prints), a backend tool
#: call the backend refused (``scripts/mcp_call.py``), and the Codex and Copilot bridge's refused request
#: (``scripts/mcp_bridge.py``).
SOURCE_BACKEND_CHECK = "backend-check"
SOURCE_SDK_RESOLVER = "sdk-resolver"
SOURCE_SDK_CALL = "sdk-call"
SOURCE_MCP_CALL = "mcp-call"
SOURCE_MCP_BRIDGE = "mcp-bridge"
REFUSAL_SOURCES = (SOURCE_BACKEND_CHECK, SOURCE_SDK_RESOLVER, SOURCE_SDK_CALL, SOURCE_MCP_CALL, SOURCE_MCP_BRIDGE)
#: The INTERACTIVE pair, filed by `hooks/ask_answered.py` at PostToolUse. Both of the kinds above
#: are written inside `ask_guard._headless_deny`, so on the interactive path a sanctioned gate was
#: consumed, ALLOWED, and — answered or not — nothing typed was ever written. That silence is
#: published: `build_run_json._run_driver_block` then returns `None`, which its own docstring calls
#: a CLAIM, *"there was a person here"*.
#:
#: A PreToolUse hook that ALLOWS cannot hand anything over, so interactively there is no
#: default-resolution site to attest from: the model resolves the gate by reading
#: :data:`GATE_DEFAULTS` itself. **A hook cannot attest to a decision it did not make.** The one
#: fact this system does observe is `ask_answered._answered()` — the call came back with, or
#: without, a structured answer — so that is exactly what these two say, and no more: never which
#: option was applied afterwards.
#:
#: A gate can be asked twice (errored, then answered on the re-ask `report_guard` re-arms), so BOTH
#: outcomes are filed and the reader takes the LATEST event per gate — `ASK_ANSWERED` exists to
#: retire an `ASK_UNANSWERED` that a retry has overtaken, not to be reported on its own.
ASK_UNANSWERED = "run_driver_ask_unanswered"
ASK_ANSWERED = "run_driver_ask_answered"
#: The gate's APPLIED outcome, written by the producer of the phase that carries it out
#: (:data:`GATE_OUTCOME_PHASE`) when that phase lands. It supersedes an earlier default or stop for
#: the same gate: what the flow was handed at the gate and what it then did are different records.
GATE_RESOLVED = "run_driver_gate_resolved"
#: The run driver's OWN answer, read from their config (:data:`DECLARED_BY_CONFIG`) at a headless
#: gate. Not a default: ``answer`` is what they declared and ``declared_in`` is where; ``declared_by``
#: keeps its meaning on every row — the source of the run driver, not of the answer.
ANSWER_DECLARED = "run_driver_answer_declared"


class Resolution:
    """The mode in force and WHERE it was declared — both, because the source is the diagnostic."""

    __slots__ = ("mode", "source")

    def __init__(self, mode: str, source: str) -> None:
        self.mode = mode
        self.source = source

    @property
    def headless(self) -> bool:
        return self.mode == MODE_HEADLESS

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Resolution(mode={self.mode!r}, source={self.source!r})"


#: Sources, in precedence order. Named constants because the run record and the driver print them.
SOURCE_CONFIG = f"{ROOT_KEY}.{SUB_KEY}"
SOURCE_ENV = ENV_ALIAS
SOURCE_DEFAULT = "default"
SOURCE_STAMP = "flow-window"


# ── the gate taxonomy ────────────────────────────────────────────────────────────────────────
#
# Partitioned from ask_guard.KNOWN_GATES; test_run_driver.py asserts the two sides cover it
# exactly, in both directions, so a gate added later with NEITHER classification fails the sweep.

#: The closed vocabulary a declared default may be drawn from. A default is the run driver's answer
#: to a question whose options the skill already wrote down — never content. Anything outside this
#: set (a count, an id, a name, a path, a free-text sentence) is refused mechanically.
DEFAULT_VOCABULARY = frozenset({
    "all",                    # take every candidate the skill listed
    "approve-no-probe",       # approve what was presented; run no consent-gated probe (tool probes need none)
    "env-only",               # apply a trace brief to the EXISTING mock env only; the next suite stage regenerates the rest
    "build-from-recorded-sources",  # build over exactly the sources scan recorded on the map
    "decline",                # do not do the destructive / optional thing
    "enable",                 # turn on what a typed declaration already names
    "key-present",            # the agent's provider key is reachable, so the run it guards is not skipped
    "most-recent-failure",    # the skill's own stated fallback pick
    "neither",                # opt into none of the offered add-ons
    "none",                   # select nothing from a menu
    "reuse-existing",         # keep what is already built rather than rebuilding it
    "proceed-without-corpus", # continue, and record the consequence
    "recommended-subset",     # take the skill's own recommended focused subset, recorded, and say which
    "refuse",                 # do not enter the degraded/lesser mode
    "skip",                   # the step only the user could unblock does not run
    "use-saved",              # reuse what is already on disk
    "whole-agent",            # no narrowing to one component
})

#: DECISIONS — a headless run answers these itself, with the answer stated here.
#:
#: Each default is the CONSERVATIVE reading of the skill's own options: the exit the skill marks
#: recommended where it marks one, and otherwise the answer that starts nothing unbounded and
#: destroys nothing. The comment on each line is where that answer comes from, so a reviewer can
#: check it against the skill rather than against this file.
GATE_DEFAULTS: dict[str, str] = {
    # analyze-traces: apply nothing to surfaces that already exist — the brief would rewrite (and
    # redeploy) them; fresh generation still reads the brief.
    "apply-brief": "none",
    # scan-agent: no `--component` narrowing. The unfocused scan is the flow's own baseline.
    "component-pick": "whole-agent",
    # build-rag-corpus Step 3: REUSE. A fresh manifest starts a NEW corpus, silently orphaning the
    # one the scenarios and evals are already grounded against — and the rebuild branch destroys
    # ground truth, which is not a purchase an absent run driver made.
    "corpus-reuse": "reuse-existing",
    # analyze: the skill's refusal branch — "stop and say the gate cannot run". A self-certified
    # quality gate is not a verdict a flow may hand a run driver who never asked for it.
    "degraded-mode-consent": "refuse",
    # every delete gate: destructive, so the default is the no-op. The flow continues undeleted.
    "delete-confirm": "decline",
    # simulation-setup: the ask fires on a typed document declaration. Declining would mean dropping
    # a true declaration to pass Step 9e, so the default generates the document it names.
    "document-generation": "enable",
    # simulation-setup-agentforce: articles published only outside the agent's audience stay in the
    # org. Their yes is egress of content the customer never published to that audience.
    "knowledge-audience": "decline",
    # fixes: "The plan is a menu, not a mandate … apply nothing until they choose." The report
    # still lands; nothing is applied to the customer's source.
    "fix-selection": "none",
    # scan-agent: the one consent screen over the descriptions, the traces and the corpus. Consent to send
    # customer content is an absent run driver's to give, so the default is no: nothing is planned into a
    # delivery and each skill delivers its own part as it does without the screen.
    "grounding-consent": "decline",
    # generate-suite: decided from the typed `grounding` block (:data:`FACT_DISPATCH`). A knowledge
    # agent whose recorded sources are readable builds over exactly the scope scan recorded on the
    # map — bounded by it; its egress still stops at `anonymization-posture` (no default) and a large
    # source at `extraction-scope`. This value is the answer for an agent with no knowledge surface.
    "grounding-gate": "proceed-without-corpus",
    # run: an opt-in nobody took is `no`, for both halves (experiment and plain traces).
    "observability-opt-in": "neither",
    # scan-agent: the skill's own "(recommended, fast)" option 1.
    "saved-setup-gate": "use-saved",
    # simulate: rerun the existing suite when there is one, and skip the traces question. The
    # flow then runs only what already exists; nothing is rescanned or rebuilt for an absent run driver.
    "simulate-start": "reuse-existing",
    # trace-to-scenarios --from-brief: `all` means all within the room — the plan's `default`, every
    # trace failure located at a cut that the final batch has room for and the total cap allows, in
    # rank order; that is the unnarrowed answer, the same shape as apply-brief. Nothing is
    # uploaded: the folders are staged, never committed, and reach a set only through the next
    # final batch.
    "trace-scenario-selection": "all",
    # scan-agent: approve the map as presented AND run no consent-gated probe — a live-agent probe
    # reaches the customer's real agent, which is not something an absent run driver consented to.
    # Tool probes need no consent: they call only local or mocked surfaces and run with no network.
    "topology-approval": "approve-no-probe",
    # build-rag-corpus: the skill states it in terms — "default: all of them".
    "which-kb": "all",
    # diagnose: the skill states it in terms — "otherwise take the most recent failure and say
    # which you took".
    "which-scenario": "most-recent-failure",
}

#: PREREQUISITES — only the user can supply these. The value is the one line a headless run
#: carries for it: what the run driver must declare. What the run DOES without it is
#: :data:`PREREQUISITE_DEFAULTS`.
PREREQUISITE_GATES: dict[str, str] = {
    # The PII-egress posture is a declaration, not a decision a pipeline may take for a customer.
    "anonymization-posture": f"declare `anonymize: true|false` in {CONFIG_NAME} — only an answer the run driver gave; never write a default for them",
    # By definition: the escalation for something only the user holds.
    "blocker": "answer the escalated question, or declare the fact it asks for",
    # THE fabrication site: a defaulted corpus scope becomes the suite's whole product universe
    # (see the module docstring). Narrowing it is information only the customer has.
    "extraction-scope": "name the corpus scope (which subtree / area / tables) before building",
    # generate-evals: the failing input is the customer's, and the skill's own fallback for a
    # second failure is contact-Plurai, never a guess.
    "input-fix": "fix the named input document, or say what it should say",
    # The key check found it in neither the environment the runner inherits nor the file the SDK reads
    # itself, so the fix the run driver is told is that file.
    "platform-key": "put the named observability key in the agent folder's gitignored .env (the check found it in neither the environment nor that file), or declare observability off",
    # The run and the provider-key check both read the environment and the agent module's .env.
    "provider-key": "put the agent's model-provider key in the agent module's gitignored .env (the check found it in neither the environment nor that file)",
    "rescan-diff-approval": "approve or reject each item the rescan would drop or invert",
    "sink-project": "declare the observability platform and project the traces live in",
    # The driver supplies this from its own environment; without one, only the user knows which
    # repo is the agent.
    "workdir-location": "point PLURAI_AGENT_PATH (or the driver's cwd) at the agent",
}


#: What a headless run does at a PREREQUISITE with nobody to supply it: the SAFE side, where the
#: step the gate guards does not run (never consent: nothing is uploaded unscrubbed, overwritten,
#: or delivered as complete). None = NO safe default: the run STOPS with run_driver_input_required.
PREREQUISITE_DEFAULTS: dict[str, str | None] = {
    # no default: whether real data leaves the machine scrubbed is the customer's declaration.
    "anonymization-posture": None,
    "blocker": "skip",
    "extraction-scope": "skip",
    "input-fix": "skip",
    "platform-key": "skip",
    "provider-key": "skip",
    # moving the draft in DROPS the listed items; a rejected item keeps the OLD sentence.
    "rescan-diff-approval": "decline",
    "sink-project": "skip",
    # nothing can run without knowing which repo is the agent.
    "workdir-location": None,
}

#: Gates whose run driver's answer IS a top-level (strict-bool) key of ``plurai.config.yaml``. Once the
#: run driver declares it, :func:`gate_disposition` hands that answer over before any default or stop,
#: and the key settles an earlier stop wherever it was filed (:func:`declared_answer`). Only the
#: run driver writes these keys; nothing in the plugin authors one for them.
DECLARED_BY_CONFIG: dict[str, str] = {"anonymization-posture": "anonymize"}
#: Top-level config keys that answer no gate: a harness repair is applied without a question, so `approve_harness_edits`
#: in an old config changes no outcome. They are left out of the config digest (``bench_manifest.config_digest``) so a
#: config carrying one is not made stale.
RETIRED_DECLARATION_KEYS: frozenset[str] = frozenset({"approve_harness_edits"})

#: THE dispatch table: every sanction name -> the answer a headless run applies (hook deny
#: under a declared headless, or `flow-window.sh unasked` when there is no ask tool), or None to
#: STOP. One table for both routes, so a session with the tool and one without cannot disagree.
SAFE_DEFAULTS: dict[str, str | None] = {**GATE_DEFAULTS, **PREREQUISITE_DEFAULTS}
#: The `why` on a row `flow-window.sh unasked` files — the model reported it had no ask tool.
WHY_NO_ASK_TOOL = "no-ask-tool"
#: Why ``flow-window.sh sanction`` applied a gate's default itself: the run driver is declared headless, so the
#: ask would reach nobody, and on a harness that asks in plain text no hook sees the ask to dispatch it.
WHY_HEADLESS = "headless"

#: The gates whose question carries a SCOPE — the inputs, switches and tools the simulation proposes to leave out,
#: typed in ``<workdir>/artifacts/scope_proposal.json`` (``scripts/scope_proposal.py`` writes it before the gate) —
#: mapped to the defaults of theirs that approve it. Only these gates' records back a scope cut, and only for the items
#: the proposal named: an answer that approves (``hooks/ask_answered.py``) or one of these defaults, applied because the
#: run driver is headless or a live ``/simulate`` run takes it (:func:`scope_of_default`). Every other gate backs no cut:
#: a prerequisite (``blocker``) is never a scope approval.
SCOPE_GATES: dict[str, frozenset[str]] = {"topology-approval": frozenset({"approve-no-probe"})}
SCOPE_PROPOSAL = "scope_proposal.json"
#: The typed lists a scope proposal, and every record that backs one, carries.
SCOPE_KEYS = ("inputs", "switches", "tools")


def proposed_scope(gate: str, art: Path | None) -> dict[str, list[str]] | None:
    """The items ``<art>/scope_proposal.json`` proposes for ``gate``: ``{inputs, switches, tools}`` as lists of names.

    None when ``gate`` carries no scope (:data:`SCOPE_GATES`), there is no readable proposal, or the proposal is for
    another gate. Total: a record writer calls it, so any fault reads as no proposal, never as an exception.
    """
    if gate not in SCOPE_GATES or art is None:
        return None
    try:
        proposal = json.loads((art / SCOPE_PROPOSAL).read_text(encoding="utf-8"))
        if not isinstance(proposal, dict) or proposal.get("gate") != gate:
            return None
        lists = {key: proposal.get(key) for key in SCOPE_KEYS}
        return {key: [str(n) for n in value if isinstance(n, str) and n] if isinstance(value, list) else []
                for key, value in lists.items()}
    except Exception:  # noqa: BLE001 - no proposal on record backs nothing
        return None


def retire_scope(art: Path, command: str) -> bool:
    """Drop ``<art>/scope_proposal.json`` when ``command``'s flow holds a scope gate: a NEW build of that flow (or a set-aside
    attempt) proposes its own scope, and an earlier scan's proposal must not ride its gate. A resumed build keeps it.
    True when a file was removed. Never raises."""
    try:
        from ask_guard import FLOW_GATES  # noqa: PLC0415 - hooks/ is on sys.path wherever this module runs

        if not set(SCOPE_GATES) & FLOW_GATES.get(command, frozenset()):
            return False
        (art / SCOPE_PROPOSAL).unlink()
    except Exception:  # noqa: BLE001 - absent, or not removable: the next proposal overwrites it
        return False
    return True


def scope_of_default(gate: str, default: str, *, art: Path | None, mode: str, command: str) -> dict[str, list[str]]:
    """The scope a DEFAULT backs, to file on its row: the proposal's items when ``default`` approves a scope gate, the
    declaration dispatched it (``mode`` headless, or autonomous under a live ``/simulate`` plan) and ``command``'s flow
    has the gate in its budget. Empty otherwise — a default the model reached by saying it has no ask tool in an
    interactive session backs nothing, exactly as an ask nobody answered backs nothing."""
    if mode not in (MODE_HEADLESS, MODE_AUTONOMOUS) or default not in SCOPE_GATES.get(gate, frozenset()):
        return {}
    try:
        from ask_guard import FLOW_GATES  # noqa: PLC0415 - hooks/ is on sys.path wherever this module runs
    except Exception:  # noqa: BLE001 - without the budget nothing may be backed
        return {}
    if gate not in FLOW_GATES.get(command, frozenset()):
        return {}
    return proposed_scope(gate, art) or {}


#: The phase whose LANDED record is a gate's applied decision. `bench_manifest.py phase record`
#: files :data:`GATE_RESOLVED` when it lands, and `build_run_json` supersedes that gate's earlier
#: default or stop BY GATE (one owner per gate by declaration) rather than by (gate, command).
GATE_OUTCOME_PHASE: dict[str, str] = {"grounding-gate": "corpus"}

#: The command a row is filed under when no flow window names one.
FALLBACK_COMMAND = "plurai-simulation"


def flow_command(bases: list[Path]) -> str:
    """The armed flow's command (the first readable ``.plurai_flow_active``), else :data:`FALLBACK_COMMAND`."""
    for base in bases:
        try:
            command = (base / ".plurai_flow_active").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if command:
            return command
    return FALLBACK_COMMAND


def safe_default(gate: str) -> str | None:
    """The answer a headless run applies at ``gate`` (:data:`SAFE_DEFAULTS`); None = stop or unclassified."""
    return SAFE_DEFAULTS.get(gate)


def declared_line(gate: str, disposition: "Disposition") -> str:
    """The line both headless routes print for a ``declared`` disposition."""
    key = DECLARED_BY_CONFIG.get(gate, "")
    return (f"The run driver DECLARED their answer to '{gate}' in {disposition.declared_in} (`{key}: {disposition.value}`). "
            "That is the run driver's answer, not a default: apply it as the skill defines that value, say in ONE line "
            "that you applied the run driver's declared answer, and continue.")


def prerequisite_rule(gate: str, default: str = "") -> str:
    """The one sentence a PREREQUISITE's safe default carries; empty for a decision.

    ``default`` is the answer being applied: :data:`KEY_PRESENT` says the prerequisite is met, so the
    step it guards runs.
    """
    if gate not in PREREQUISITE_GATES:
        return ""
    if default == KEY_PRESENT:
        return (" The agent's provider key is set where the run reads it (the environment or the agent's module "
                ".env), so the prerequisite is met: do not skip the run.")
    return (f" This gate needs: {PREREQUISITE_GATES[gate]}. Until that is supplied the step it guards does not run"
            " — never consent: nothing is uploaded unscrubbed, overwritten, or delivered as complete.")


def retire_durable(art: Path, command: str, kinds: tuple[str, ...] | None) -> int:
    """Retire ``command``'s earlier durable rows in ``art`` — every kind (``kinds=None``) on
    ``scripts/open_epoch.py``'s ``new`` decision, only its STOPs (``(INPUT_REQUIRED,)``) on ``kept``.

    A new flow decides again, and a person who answers it leaves no durable row to supersede a
    stale default. A RESUMED flow keeps its decisions but not its stops: a stop is a halt, not a
    decision, and the resume re-reaches any gate still unmet and files a fresh one — so a stop the
    run driver has since satisfied (by declaring it) is not published as unanswered forever. Other
    commands' rows stay (a scan's decisions reach its suite). Returns how many rows were retired.
    Never raises, never creates.
    """
    path = art / FINDINGS_MARKER
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    kept = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        if not (isinstance(row, dict) and row.get("command") == command and (kinds is None or row.get("kind") in kinds)):
            kept.append(line)
    if len(kept) == len(lines):
        return 0
    try:
        if kept:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        else:
            path.unlink()
    except OSError:
        return 0
    return len(lines) - len(kept)


class Disposition(NamedTuple):
    """``('default', <answer>)``, ``('declared', <answer>)``, ``('answered', <answer>)`` (the user's answer at
    ``/simulate``'s start question) or ``('input_required', <needs>)``, plus a ``note`` the routes print and
    record beside it — set only when a typed fact the dispatch should have read could not be read — and, for
    ``declared`` / ``answered``, ``declared_in``: ``<file>:<key>`` the answer came from."""

    kind: str
    value: str
    note: str = ""
    declared_in: str = ""


def _grounding_disposition(art: Path | None) -> Disposition:
    """The grounding gate, decided from the typed ``grounding`` block in ``<art>/agent_topology.json``.

    ``needs_corpus: false`` keeps the static answer; ``true`` with every ``sources`` entry readable
    builds over exactly those sources. A block that is ABSENT (a topology written before scan
    recorded it, or no topology file) is derived first, the way the skill derives it
    (:func:`_derived_grounding`), then dispatched the same way. A present block that is malformed, or
    a knowledge surface with no readable source, is a typed STOP naming what the run driver records —
    never a guessed scope. Sources resolve through ``bench_manifest.resolve_source``, the one resolver.
    """
    remedy = ("re-run /simulation-scan-agent naming the folder(s) that hold the knowledge (it records them in "
              "`grounding.sources` of artifacts/agent_topology.json; this flow never edits the map), or run the suite "
              "with a run driver present to answer the grounding gate")
    topology = _read_json_object(art / "agent_topology.json") if art is not None else None
    note = ""
    if art is None or topology is None or "grounding" not in topology:
        grounding, note = _derived_grounding(art, topology or {})
    else:
        grounding = topology["grounding"]
    if not isinstance(grounding, dict) or not isinstance(grounding.get("needs_corpus"), bool):
        return Disposition("input_required", f"{remedy} (the typed `grounding` block is present but not readable)")
    if grounding["needs_corpus"] is False:
        return Disposition("default", GATE_DEFAULTS["grounding-gate"], note)
    sources = grounding.get("sources")
    if not isinstance(sources, list) or not sources or not all(isinstance(s, str) and s.strip() for s in sources):
        return Disposition("input_required", f"{remedy} (`grounding.needs_corpus` is true and no source is recorded)", note)
    scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    if scripts not in sys.path:
        sys.path.append(scripts)
    from bench_manifest import resolve_source  # noqa: PLC0415 - the one resolver

    unreadable = [rel for rel in sources if art is None or not _readable(resolve_source(art.parent, rel))]
    if unreadable:
        return Disposition("input_required", f"{remedy} (missing, empty or unreadable: {', '.join(unreadable)})", note)
    return Disposition("default", "build-from-recorded-sources", note)


def _read_json_object(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _derived_grounding(art: Path | None, topology: dict) -> tuple[dict, str]:
    """The verdict for a topology with no ``grounding`` block, derived from typed facts the way the
    skill derives it: a knowledge surface is a retrieval surface (``rag``) or prompt-inlined knowledge
    (``knowledge``) on the map, or a ``knowledge:`` record in ``plurai.config.yaml``
    (``prompt_inlined: true`` or a ``closed_doc_set``). The closed doc set's paths are the sources. No
    typed knowledge surface reads as none. The second
    value is the note a caller must carry when the config half could not be read.
    """
    record, note = _config_knowledge(art.parent) if art is not None else ({}, "")
    docs = record.get("closed_doc_set")
    docs = [d for d in docs if isinstance(d, str) and d.strip()] if isinstance(docs, list) else []
    needs = bool(topology.get("rag")) or bool(topology.get("knowledge")) or record.get("prompt_inlined") is True or bool(docs)
    return {"needs_corpus": needs, **({"sources": docs} if docs else {})}, note


#: The note a derived verdict carries when a config exists and this interpreter cannot parse it.
CONFIG_UNREAD = ("config_unread: plurai.config.yaml `knowledge:` was NOT read (pyyaml is not importable by this "
                 "interpreter), so the derived grounding verdict read agent_topology.json only")


def _config_knowledge(workdir: Path) -> tuple[dict, str]:
    """The ``knowledge:`` mapping of the nearest ``plurai.config.yaml`` (``{}`` when absent or unreadable),
    and :data:`CONFIG_UNREAD` when a config exists but pyyaml is not importable — never a silent ``{}``."""
    try:
        import yaml  # noqa: PLC0415 - optional, as in run_driver_from_text
    except Exception:
        return {}, (CONFIG_UNREAD if any(path.is_file() for path in _config_paths(workdir)) else "")
    for path in _config_paths(workdir):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if isinstance(doc, dict):
            block = doc.get("knowledge")
            return (block if isinstance(block, dict) else {}), ""
    return {}, ""


def _readable(path: Path) -> bool:
    """A non-empty readable file, or a directory holding at least one."""
    import _platform  # noqa: PLC0415 - hooks/ is on sys.path wherever this module is

    if not _platform.is_local_drive_path(path, within=_platform.cwd_roots()):  # Windows: a UNC source is never touched
        return False
    try:
        if path.is_file():
            return os.access(path, os.R_OK) and path.stat().st_size > 0
        return path.is_dir() and any(f.is_file() and os.access(f, os.R_OK) and f.stat().st_size > 0 for f in path.rglob("*"))
    except OSError:
        return False


#: What the ``provider-key`` gate answers when the key is reachable: there is nothing to skip.
KEY_PRESENT = "key-present"


def _provider_key_disposition(art: Path | None) -> Disposition:
    """The ``provider-key`` gate: ``skip`` (its declared default) unless the bench's key is reachable.

    The key counts where the run reads it (``bench_manifest.provider_key_state``, presence only). A
    model's own test of its shell's environment sees neither a ``.env`` nor a variable the harness
    scrubs from the shell, so a key that is there must not be skipped on that test. Anything the check
    cannot decide keeps the declared default: this runs inside a hook, which never raises.
    """
    skip = Disposition("default", str(PREREQUISITE_DEFAULTS["provider-key"]))
    if art is None:
        return skip
    try:
        scripts = str(Path(__file__).resolve().parent.parent / "scripts")
        if scripts not in sys.path:
            sys.path.append(scripts)
        import bench_manifest  # noqa: PLC0415 - the one owner of the provider-key check

        state = bench_manifest.provider_key_state(art.parent)[0]
    except Exception:  # noqa: BLE001 - an unreadable bench is no evidence of a key
        return skip
    return Disposition("default", KEY_PRESENT) if state == "present" else skip


#: Gates whose headless answer is decided from a typed fact under ``art`` (``<workdir>/artifacts``)
#: rather than read from :data:`SAFE_DEFAULTS`. Consulted first by :func:`gate_disposition`.
FACT_DISPATCH: dict[str, Callable[[Path | None], Disposition]] = {
    "grounding-gate": _grounding_disposition,
    "provider-key": _provider_key_disposition,
}


def gate_disposition(gate: str, *, art: Path | None) -> Disposition | None:
    """``('declared', <answer>)``, ``('default', <answer>)``, ``('input_required', <what the user must supply>)``, or None.

    ``art`` is ``<workdir>/artifacts`` (None when unresolved) — required, so no route can skip the
    typed facts :data:`FACT_DISPATCH` reads. None means a name this module does not classify — which
    ``ask_guard`` already voids as a self-minted gate, so headless adds no new verdict for it.
    An answer the run driver declared in their config (:data:`DECLARED_BY_CONFIG`) comes before any
    default or stop: it is the run driver's answer, not one this table supplies for them.
    """
    if gate not in SAFE_DEFAULTS:
        return None
    answer = declared_answer(gate, art.parent) if art is not None else None
    if answer is not None:
        return Disposition("declared", answer, declared_in=f"{CONFIG_NAME}:{DECLARED_BY_CONFIG[gate]}")
    if gate in FACT_DISPATCH:
        return FACT_DISPATCH[gate](art)
    default = SAFE_DEFAULTS[gate]
    return Disposition("default", default) if default is not None else Disposition("input_required", PREREQUISITE_GATES[gate])


# ── autonomous: a live /simulate run answers its decisions, and still asks what only the user has ──

#: The ``/simulate`` run's plan, beside the flow window (``$FLOWDIR``). Its one writer is
#: ``hooks/simulate_continue.py`` — behind ``flow-window.sh simulate …`` and ``report_guard``'s two calls.
PLAN_MARKER = ".plurai_simulate.json"
PLAN_SCHEMA = "plurai-simulate-plan/1"
#: A plan nobody has written for this long is no longer a live run. Every write refreshes it.
PLAN_TTL_SECONDS = 24 * 3600
#: ``declared_by`` on the rows an autonomous dispatch files: the source of the mode, as on every row.
DECLARED_BY_SIMULATE = "simulate"

#: Gates a ``/simulate`` run still PUTS TO THE USER, who is present: the start question itself and every
#: prerequisite (a credential or a fact no default can supply) EXCEPT :data:`AUTONOMOUS_TAKES_DEFAULT`. A
#: gate the start question already answered (:data:`START_ANSWERS`) is handed that answer before it gets here.
AUTONOMOUS_ASKS = frozenset({
    "anonymization-posture", "blocker", "grounding-consent", "input-fix", "platform-key",
    "provider-key", "simulate-start", "sink-project", "workdir-location",
})
#: Gates a live ``/simulate`` run answers with a default although a headless reading would call them a
#: prerequisite, so that no stop follows the start call.
#: ``rescan-diff-approval`` → ``decline`` (every listed item keeps its OLD sentence), from :data:`SAFE_DEFAULTS`;
#: ``extraction-scope`` → ``recommended-subset`` (:data:`AUTONOMOUS_DEFAULTS`: the skill's own recommended subset is
#: built — headless ``skip`` would leave the suite testing no knowledge). Each is listed at the run's end and
#: reversible by feedback. A config-declared answer still comes first.
AUTONOMOUS_TAKES_DEFAULT = frozenset({"extraction-scope", "rescan-diff-approval"})
#: The knowledge-base gates whose default starts an upload of the agent's documents. A ``/simulate`` run takes their
#: default only while the user's answer to its start question (which names the knowledge base,
#: ``simulate_plan.ANONYMIZE_PROMPT``) is on record; a config ``anonymize:`` alone does not count, because other skills
#: write it from questions about their own data. Without that answer they are asked.
KB_UPLOAD_GATES = frozenset({"grounding-gate", "extraction-scope", "which-kb"})


def _apply_brief_default(plan: dict) -> str:
    """``env-only`` — ground the REUSED mock in the trace brief — only when a NEW suite build follows in this plan:
    that build regenerates scenarios and evals on the re-grounded mock. A rerun has no suite stage, and a finish
    resumes the stopped build's scenarios, whose seeds were built for the old mock: there it is ``none`` (the brief
    is published, and the next new build is born grounded). New = the phase record of the plan's workdir leaves no
    build open (``bench_manifest.open_build``); a record that cannot be read is not a new build."""
    if "suite" not in remaining_stages(plan):
        return "none"
    if not plan.get("workdir"):
        return "env-only"  # no workdir recorded: no record to resume either
    scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    if scripts not in sys.path:
        sys.path.append(scripts)
    from bench_manifest import _suite_state, open_build  # noqa: PLC0415 - the phase record's one reader

    import _platform  # noqa: PLC0415 - hooks/ is on sys.path wherever this module is

    workdir = Path(str(plan["workdir"]))
    if not _platform.is_local_drive_path(workdir, within=_platform.cwd_roots()):  # a UNC path a plan names is never followed
        return "none"
    doc, _unreadable = _suite_state(workdir)
    return "env-only" if doc is not None and open_build(doc["phases"]) is None else "none"


#: Where a ``/simulate`` run's answer differs from :data:`SAFE_DEFAULTS`: a token, or a callable on the
#: plan. Consulted before :data:`SAFE_DEFAULTS`. ``apply-brief`` grounds a REUSED mock in the trace brief only
#: when a new suite build follows to regenerate scenarios and evals on it (:func:`_apply_brief_default`).
#: ``recommended-subset`` builds the focused subset build-rag-corpus recommends, so a large unscoped source
#: still yields a corpus (feedback widens it).
AUTONOMOUS_DEFAULTS: dict[str, str | Callable[[dict], str]] = {
    "apply-brief": _apply_brief_default,
    "extraction-scope": "recommended-subset",
}

#: ``/simulate``'s project record (``scripts/simulate_plan.py`` is its one writer), in the workdir.
SIMULATE_STATE = ".simulate_state.json"
#: Gates the ``/simulate`` start question already put to the user: gate -> (record, key) in
#: :data:`SIMULATE_STATE`, where ``simulate_plan.py traces --anonymize`` stores the answer. A live run hands
#: that answer over at the gate instead of asking again; a config-declared answer still comes first, and a
#: gate with no recorded start answer is asked as before.
START_ANSWERS: dict[str, tuple[str, str]] = {"anonymization-posture": ("traces", "anonymize")}
#: The command that writes a start answer into ``plurai.config.yaml``, by gate (``{workdir}`` filled in).
START_ANSWER_WRITERS: dict[str, str] = {
    "anonymization-posture": 'python3 "{scripts}/simulate_plan.py" traces "{workdir}" --anonymize',
}


def start_answer(gate: str, workdir: Path | None) -> str | None:
    """The user's start-question answer to ``gate`` — ``"true"`` or ``"false"`` — or None when none is recorded."""
    where = START_ANSWERS.get(gate)
    if where is None or workdir is None:
        return None
    section = (_read_json_object(workdir / SIMULATE_STATE) or {}).get(where[0])
    value = section.get(where[1]) if isinstance(section, dict) else None
    return ("true" if value else "false") if isinstance(value, bool) else None


def start_line(gate: str, disposition: "Disposition", workdir: Path | None) -> str:
    """The deny text for an ``answered`` disposition: the user's own start answer, recorded into the config."""
    key = DECLARED_BY_CONFIG.get(gate, START_ANSWERS[gate][1])
    writer = START_ANSWER_WRITERS.get(gate)
    scripts = Path(__file__).resolve().parent.parent / "scripts"
    record = (f"Record it first, in one call: {writer.format(scripts=scripts, workdir=workdir)} — then apply it"
              if writer is not None and workdir is not None else "Apply it")
    return (f"The user already answered '{gate}' at this /simulate run's start question (`{key}: {disposition.value}`), "
            f"so it is not asked again. {record} as the skill defines that value and continue, with no question and "
            "nothing about it in this hand-off.")


def find_plan(bases: list[Path]) -> Path | None:
    """The first ``.plurai_simulate.json`` across ``bases`` (``_workdir.marker_dirs``), or None."""
    for base in bases:
        path = base / PLAN_MARKER
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def read_plan(path: Path | None) -> dict | None:
    """The plan at ``path`` when it is one this schema wrote, else None. Never raises."""
    if path is None:
        return None
    plan = _read_json_object(path)
    if plan is None or plan.get("schema") != PLAN_SCHEMA:
        return None
    stages, done = plan.get("stages"), plan.get("done")
    if not isinstance(stages, list) or not isinstance(done, list):
        return None
    return plan


def remaining_stages(plan: dict) -> list[str]:
    """The plan's stages not yet in ``done``, in order."""
    done = set(plan.get("done") or [])
    return [s for s in plan.get("stages") or [] if s not in done]


def autonomous_plan(bases: list[Path], *, now: float | None = None) -> dict | None:
    """The live ``/simulate`` plan, or None: the file exists, was written less than
    :data:`PLAN_TTL_SECONDS` ago, is not ``finished``, and still has a stage not in ``done``.

    Read only by ``ask_guard`` (which gates take their default) and ``simulate_continue`` (whether
    the flow continues). Once every stage is done autonomy has ended, whether or not ``end`` ran.
    """
    path = find_plan(bases)
    plan = read_plan(path)
    if plan is None or plan.get("finished") is True or not remaining_stages(plan):
        return None
    try:
        age = (time.time() if now is None else now) - path.stat().st_mtime  # type: ignore[union-attr]
    except OSError:
        return None
    return plan if age < PLAN_TTL_SECONDS else None


def autonomous_disposition(gate: str, plan: dict, *, art: Path | None) -> Disposition | None:
    """What a live ``/simulate`` run does at ``gate``: ``('declared', …)``, ``('default', …)``, or None = ASK.

    In order: the run driver's own declared answer (:data:`DECLARED_BY_CONFIG`) is handed over exactly as
    headless hands it over; then the user's answer at the start question (:data:`START_ANSWERS`); an
    :data:`AUTONOMOUS_ASKS` gate, and a fact-dispatched gate whose facts say ``input_required``, is put to
    the user; every other gate takes :data:`AUTONOMOUS_DEFAULTS`, then :data:`SAFE_DEFAULTS`. A gate this
    module does not classify is asked (``ask_guard`` voids it first).
    """
    answer = declared_answer(gate, art.parent) if art is not None else None
    if answer is not None:
        return Disposition("declared", answer, declared_in=f"{CONFIG_NAME}:{DECLARED_BY_CONFIG[gate]}")
    answer = start_answer(gate, art.parent) if art is not None else None
    if answer is not None:
        return Disposition("answered", answer, declared_in=f"{SIMULATE_STATE}:{'.'.join(START_ANSWERS[gate])}")
    if gate in AUTONOMOUS_ASKS or gate not in SAFE_DEFAULTS:
        return None
    if gate in KB_UPLOAD_GATES and start_answer("anonymization-posture", art.parent if art is not None else None) is None:
        return None  # no start answer naming the knowledge base on record: the upload is the user's to approve
    if gate in FACT_DISPATCH:
        disposition = FACT_DISPATCH[gate](art)
        return disposition if disposition.kind == "default" else None
    special = AUTONOMOUS_DEFAULTS.get(gate)
    value = special(plan) if callable(special) else special
    if value is None:
        value = SAFE_DEFAULTS[gate]
    return Disposition("default", value) if value is not None else None


#: What a defaulted gate's step still says in a /simulate run — a progress line the stage's skill names for that case —
#: so "do not mention it" is never read as "say nothing at this step". A gate not here adds nothing.
DEFAULT_STILL_SAYS: dict[str, str] = {
    "topology-approval": "Still print the scan's map line — the count line and the map link — with nothing about approval or this default, "
                         "once per scan: a repeat of this gate in the same scan prints nothing.",
    "grounding-gate": "Say nothing about the knowledge base yet; say its outcome once, as Step 1's corpus wait says.",
    "extraction-scope": "Still say in ONE plain progress line, not a question, which part of the source the corpus will be built from, "
                        "and that the user can ask to widen it.",
}


def _org_access_applied(session_base: Path, workdir: Path | None) -> str:
    """The start question's org-access answer, turned into its grant now that the map exists, and what to say about it.
    Empty when there is none, or when this cannot run: the scan then goes on as it does without a start answer."""
    if workdir is None:
        return ""
    scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    if scripts not in sys.path:
        sys.path.append(scripts)
    try:
        import simulate_plan  # noqa: PLC0415 - the start answers' one reader and writer

        applied = simulate_plan.apply_org_access(workdir, session_base)
    except Exception:  # noqa: BLE001 - see the docstring
        return ""
    if applied is None:
        return ""
    return (f" The user already answered the org-access question, and it is applied: {applied} Ask nothing about it, on no "
            "gate, and leave the consent file as it is. When it names something left out, say so in ONE plain progress line.")


#: What a gate's default also applies: a question that would ride that gate's call and that the start call already put
#: to the user. The rest of the line is the same as any default's.
DEFAULT_ALSO_APPLIES: dict[str, Callable[[Path, Path | None], str]] = {"topology-approval": _org_access_applied}


def autonomous_line(gate: str, value: str) -> str:
    """The deny text an autonomous default carries — in product terms, never the headless wording."""
    still = DEFAULT_STILL_SAYS.get(gate)
    return (f"This /simulate run takes the '{gate}' gate's default '{value}' instead of asking; the run's end lists it, "
            "and the user can reverse it by feedback. Apply it and continue; do not mention it in this hand-off."
            + (f" {still}" if still else ""))


def apply_autonomous(
    gate: str, plan: dict, *, session_base: Path, art: Path | None, command: str, workdir: Path | None,
) -> str | None:
    """Answer ``gate`` for a live ``/simulate`` run: record the decision and return the line the model is told.

    None = the run still asks (:func:`autonomous_disposition`). The ONE place the answer is filed
    (``mode: autonomous, declared_by: simulate``) and worded, for both seams that reach it: ``ask_guard``
    when the ask tool call arrives, and ``flow-window.sh sanction`` (:func:`_main` ``autonomous``) for
    harnesses whose ask is plain text or a form the guard does not always see. A config-declared answer and
    the start-question answer are filed as the run driver's own, never as a default.
    """
    disposition = autonomous_disposition(gate, plan, art=art)
    if disposition is None:
        return None
    mode = {"mode": MODE_AUTONOMOUS, "declared_by": DECLARED_BY_SIMULATE}
    noted = {"note": disposition.note} if disposition.note else {}
    if disposition.kind in ("declared", "answered"):
        record_durably(ANSWER_DECLARED, session_base=session_base, art=art, gate=gate, command=command,
                       answer=disposition.value, declared_in=disposition.declared_in, **mode, **noted)
        return declared_line(gate, disposition) if disposition.kind == "declared" else start_line(gate, disposition, workdir)
    scope = scope_of_default(gate, disposition.value, art=art, mode=MODE_AUTONOMOUS, command=command)
    record_durably(DEFAULT_APPLIED, session_base=session_base, art=art, gate=gate, command=command,
                   default=disposition.value, **mode, **noted, **scope)
    also = DEFAULT_ALSO_APPLIES.get(gate)
    return autonomous_line(gate, disposition.value) + (also(session_base, workdir) if also is not None else "")


#: The knowledge-base gates a ``/simulate`` run answers even when no stage window is armed — a stage that went on past
#: its own hand-off (a halt the user answered) has none — plus the personal-data posture its start question answered.
#: Each knowledge gate's answer is the skill's own recommendation (the map's recorded sources, the recommended
#: subset, every base), taken under the same start-answer rule as with a window (:data:`KB_UPLOAD_GATES`). The rows are
#: filed under the suite, the stage these gates belong to.
UNWINDOWED_GATES = KB_UPLOAD_GATES | {"anonymization-posture"}
UNWINDOWED_COMMAND = "generate-suite"


def apply_unwindowed(gate: str, flowdir: Path, art: Path | None) -> str | None:
    """:func:`apply_autonomous` for an :data:`UNWINDOWED_GATES` gate sanctioned with no window armed, inside a live
    ``/simulate`` plan of this workdir. None = asked as before: another gate, no live plan, a plan of another workdir,
    no workdir, or (:func:`autonomous_disposition`) a knowledge gate with no start-question answer on record."""
    if gate not in UNWINDOWED_GATES or art is None:
        return None
    plan = autonomous_plan([flowdir])
    workdir = art.parent
    if plan is None or (plan.get("workdir") and Path(str(plan["workdir"])).resolve() != workdir.resolve()):
        return None
    return apply_autonomous(gate, plan, session_base=flowdir, art=art, command=UNWINDOWED_COMMAND, workdir=workdir)


# ── reading the declaration ──────────────────────────────────────────────────────────────────


def _config_paths(workdir: Path) -> list[Path]:
    """Where ``plurai.config.yaml`` may live for this workdir, most specific first.

    ``workdir`` is the effective ``$WORKDIR`` — ``plurai_sim/`` or, under the named-env layout,
    ``plurai_sim/envs/<name>/``. A saved env carries its own aligned config; a bare workspace has
    only the one at ``plurai_sim/``. Both are read, nearest first.
    """
    out = [workdir / CONFIG_NAME]
    # `plurai_sim/envs/<name>` -> also `plurai_sim`. Walked by NAME, not by depth: a bare workspace
    # is already `plurai_sim` and adds nothing, and no walk ever climbs out of it.
    for parent in workdir.parents:
        if parent.name == "plurai_sim":
            candidate = parent / CONFIG_NAME
            if candidate not in out:
                out.append(candidate)
            break
    return out


def run_driver_from_text(text: str) -> str | None:
    """``workspace.run_driver`` read out of a ``plurai.config.yaml`` body, or None.

    pyyaml when it is importable. Hooks run under bare ``python3`` and are not entitled to a
    dependency, so the fallback is a narrow scan of the ONE key this module needs — it accepts the
    block form and the flow form, and returns None on anything it does not recognise. A mode it
    cannot read degrades to the DEFAULT (interactive), never to a guess;
    the env alias needs no parsing at all, which is why the driver exports it.
    """
    try:
        import yaml  # noqa: PLC0415 - optional; the scan below is the no-dependency path
    except Exception:
        yaml = None  # type: ignore[assignment]
    if yaml is not None:
        try:
            doc = yaml.safe_load(text)
        except Exception:
            doc = None
        if isinstance(doc, dict):
            block = doc.get(ROOT_KEY)
            if isinstance(block, dict):
                value = block.get(SUB_KEY)
                return str(value).strip() if isinstance(value, str) else None
            return None  # a scalar `workspace: acme` declares no run driver
        return None
    return _scan_run_driver(text)


def _scan_run_driver(text: str) -> str | None:
    """The no-pyyaml fallback: the block form and the one-line flow form, nothing else."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith(f"{ROOT_KEY}:"):
            continue
        rest = line[len(ROOT_KEY) + 1:].strip()
        if rest.startswith("{"):  # workspace: {name: acme, run_driver: headless}
            for chunk in rest.strip("{}").split(","):
                key, _, value = chunk.partition(":")
                if key.strip() == SUB_KEY:
                    return value.strip().strip("'\"") or None
            return None
        if rest and not rest.startswith("#"):
            return None  # a scalar `workspace: acme`
        for nested in lines[index + 1:]:
            if not nested.strip() or nested.lstrip().startswith("#"):
                continue
            if not nested[:1].isspace():
                return None  # left the block
            key, sep, value = nested.strip().partition(":")
            if sep and key.strip() == SUB_KEY:
                return value.split("#", 1)[0].strip().strip("'\"") or None
        return None
    return None


def declared_run_driver(workdir: Path | None) -> str | None:
    """The declared mode for ``workdir``, or None when nothing declares one. Never raises."""
    if workdir is None:
        return None
    for path in _config_paths(workdir):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        declared = run_driver_from_text(text)
        if declared in MODES:
            return declared
        if declared is not None:
            # Declared and UNREADABLE as a mode (a typo, a list, a number). Not silently
            # interactive: verify_config FAILS on it (`invalid_run_driver`, rc=1), and this
            # read keeps looking rather than adopting a value no dispatch understands.
            continue
    return None


#: YAML 1.1 booleans as pyyaml resolves them, unquoted. Quoted, each is a string and declares nothing.
_YAML_BOOLS = {
    **dict.fromkeys(("true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON"), "true"),
    **dict.fromkeys(("false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF"), "false"),
}


def _top_level_bool(text: str, key: str) -> tuple[bool, str | None]:
    """Whether ``key`` is a top-level key of this config body, and its value as ``"true"``/``"false"``
    when it is a strict bool — None when it is anything else (quoted, null, a number, unparseable).

    pyyaml when importable, else a scan of the one top-level line, the way :func:`run_driver_from_text` degrades.
    The scan reads a value only out of a body it can vouch for (:func:`_plain_body`), so it never settles
    a stop on a config pyyaml, and the SDK, would refuse.
    """
    try:
        import yaml  # noqa: PLC0415 - optional; the scan below is the no-dependency path
    except Exception:
        yaml = None  # type: ignore[assignment]
    if yaml is not None:
        try:
            doc = yaml.safe_load(text)
        except Exception:
            return True, None  # a config that cannot be parsed cannot testify
        if not isinstance(doc, dict) or key not in doc:
            return False, None
        value = doc[key]
        return True, (("true" if value else "false") if isinstance(value, bool) else None)
    lines = text.lstrip("\ufeff").splitlines()
    found = [line for line in lines if re.match(rf"{re.escape(key)}[ \t]*:(?:[ \t]|$)", line)]
    if not found:
        return False, None
    if len(found) > 1 or not _plain_body(lines):
        return True, None  # a body the scan cannot vouch for is one pyyaml may refuse, so it declares nothing
    match = re.fullmatch(rf"{re.escape(key)}[ \t]*:[ \t]+([^#]*?)[ \t]*(?:[ \t]#.*)?", found[0])
    return True, (_YAML_BOOLS.get(match.group(1)) if match else None)


#: A top-level line the scan understands: a plain key, then a colon that ends the line or is followed
#: by a blank. ``key:value`` is one scalar to YAML, not a key.
_PLAIN_KEY_LINE = re.compile(r"[A-Za-z_][\w.-]*[ \t]*:(?:[ \t]+(?P<value>.*))?")
#: Where a nested line's value starts: past any ``- `` markers and a plain ``key:``.
_NESTED_LEAD = re.compile(r"(?:-[ \t]+)*(?:[^\s\"'#:\[\]{}][^:#]*[ \t]*:(?:[ \t]+|$))?")
_QUOTED = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^']|'')*'")
_CLOSING = {"'": re.compile(r"(?:[^']|'')*'"), "\"": re.compile(r"(?:[^\"\\]|\\.)*\"")}
_BLANK_OR_COMMENT = re.compile(r"[ \t]*(?:#.*)?")


def _plain_body(lines: list[str]) -> bool:
    """Whether a config body is plain enough for the no-pyyaml scan to stand behind.

    Every line is blank, a comment, space-indented, or a plain top-level ``key:`` line, and a quoted
    value left open runs on over indented lines to its close. Not: a document marker, a tab in the
    indent, any other top-level line, a quoted value that never closes, or a flow collection not
    closed on its line. One such line and the scan reads nothing.
    """
    open_quote: str | None = None
    for line in lines:
        if open_quote is not None:
            if line.strip() and not line.startswith(" "):
                return False
            closed = _CLOSING[open_quote].match(line)
            if closed is not None:
                open_quote = None
                if not _BLANK_OR_COMMENT.fullmatch(line[closed.end():]):
                    return False
            continue
        body = line.lstrip(" ")
        if not body or body.startswith("#"):
            continue
        if body.startswith("\t"):
            return False
        if line == body:
            top = _PLAIN_KEY_LINE.fullmatch(line)
            if top is None:
                return False
            value = top.group("value") or ""
        else:
            value = body[_NESTED_LEAD.match(body).end():]  # type: ignore[union-attr]  # every part is optional
        if value[:1] in _CLOSING:
            closed = _CLOSING[value[0]].match(value, 1)
            if closed is None:
                open_quote = value[0]
            elif not _BLANK_OR_COMMENT.fullmatch(value[closed.end():]):
                return False
        elif value[:1] in ("[", "{"):
            bare = _QUOTED.sub("", value).split(" #", 1)[0]
            if bare.count("[") != bare.count("]") or bare.count("{") != bare.count("}"):
                return False
    return open_quote is None


def declared_answer(gate: str, workdir: Path | None) -> str | None:
    """The run driver's declared answer to ``gate`` — ``"true"`` or ``"false"`` — or None when there is none.

    Only a :data:`DECLARED_BY_CONFIG` gate has one: its key in the nearest ``plurai.config.yaml`` that
    carries it (:func:`_config_paths`). The SDK's strict-bool rule decides what counts: a value that is
    not a bool, or a config that cannot be read or parsed, declares nothing, so the gate's stop stands.
    Never raises.
    """
    key = DECLARED_BY_CONFIG.get(gate)
    if key is None or workdir is None:
        return None
    for path in _config_paths(workdir):
        if not (path.exists() or path.is_symlink()):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None
        present, answer = _top_level_bool(text, key)
        if present:
            return answer
    return None


def _env_says_headless(env: dict[str, str] | None = None) -> bool:
    raw = (env if env is not None else os.environ).get(ENV_ALIAS, "")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def resolve(
    payload: dict | None = None,
    *,
    workdir: Path | None = None,
    env: dict[str, str] | None = None,
) -> Resolution:
    """The run driver in force, with the precedence stated in the module docstring.

    ``workdir`` is resolved through ``_workdir.resolve_workdir`` when the caller does not pass
    one. Every step is best-effort: an unreadable tree resolves to the default, interactive mode.
    """
    if workdir is None:
        try:
            from _workdir import resolve_workdir  # noqa: PLC0415 - hooks/ is on sys.path for hooks
        except Exception:  # pragma: no cover - a script that did not add hooks/ passes workdir
            resolve_workdir = None  # type: ignore[assignment]
        if resolve_workdir is not None:
            try:
                workdir = resolve_workdir()
            except Exception:  # pragma: no cover - resolution is best-effort by contract
                workdir = None
    declared = declared_run_driver(workdir)
    if declared is not None:
        # BOTH directions. An explicit `interactive` beats the env var on purpose: a CI image
        # exporting PLURAI_SIM_NONINTERACTIVE globally must not un-interactive a human's run.
        return Resolution(declared, SOURCE_CONFIG)
    if _env_says_headless(env):
        return Resolution(MODE_HEADLESS, SOURCE_ENV)
    return Resolution(DEFAULT_MODE, SOURCE_DEFAULT)


def nobody_answers(*, workdir: Path | None = None, env: dict[str, str] | None = None) -> bool:
    """Whether a question or a missing record would reach no person: the run driver is headless, or cannot be read.

    The error default is on purpose and closed: a run whose driver is unknown is treated as headless, because a question
    nobody can answer is worse than a stop with the command, and a refusal filed for a person who is there is harmless.
    The callers that act on the opposite reading (a hook that denies an ask) read :func:`resolve` themselves.
    """
    try:
        return resolve(workdir=workdir, env=env).headless
    except Exception:  # noqa: BLE001 - see the docstring
        return True


def wakes_on_background(wakes: bool, wakes_when_headless: bool, *, workdir: Path | None = None) -> bool:
    """Whether background work that finishes starts a turn in this session.

    ``wakes`` and ``wakes_when_headless`` are the harness's two flags (``registry.CAPABILITIES``). On a harness
    whose headless session ends with its turn, a declared headless run driver takes the wake away: the pending
    register then records no background work and the Stop guard tells a turn ending on such work to wait in the
    foreground. The mode is resolved only when the answer depends on it, and an unreadable declaration reads as
    interactive, as everywhere in this module.
    """
    if not wakes or wakes_when_headless:
        return wakes
    try:
        return not resolve(workdir=workdir).headless
    except Exception:  # noqa: BLE001 - no declaration, the harness's own flag stands
        return True


# ── the flow-window stamp + the typed findings ───────────────────────────────────────────────


def stamp(target: Path, resolution: Resolution) -> None:
    """Record the mode in force beside the window this arm opened. Never raises."""
    try:
        (target / MODE_MARKER).write_text(f"{resolution.mode}\n{resolution.source}\n", encoding="utf-8")
    except OSError:
        pass  # the stamp is a record, not a gate: failing to write it must never break an arm


def read_stamp(bases: list[Path]) -> Resolution | None:
    """The mode stamped at arm time, or None. First readable base wins (``find_marker``'s rule)."""
    for base in bases:
        try:
            lines = (base / MODE_MARKER).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        mode = (lines[0].strip() if lines else "")
        if mode in MODES:
            return Resolution(mode, (lines[1].strip() if len(lines) > 1 else SOURCE_STAMP) or SOURCE_STAMP)
    return None


def _record(base: Path, finding: dict) -> str | None:
    """Append one typed finding. Never raises. Returns None when the line LANDED, else why not.

    Appended rather than overwritten: a phase can reach several gates, and a reader that sees only
    the last one under-reports both what the round needed and what it decided. Cleared by the next
    arm, with the rest of the residue.

    The failure is REPORTED rather than swallowed because the caller states the consequence out
    loud: `ask_guard._headless_deny` tells the model a typed finding "is now on this run's
    record", and on a failed append that sentence was false — the model reported a finding nobody
    could read, `headless_driver._findings` saw nothing, and the round continued past a
    prerequisite it had refused to guess.
    """
    finding["at"] = time.time()
    try:
        with (base / FINDINGS_MARKER).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(finding, ensure_ascii=False) + "\n")
    except OSError as exc:
        return f"{type(exc).__name__} writing {FINDINGS_MARKER}"
    return None


#: The bases :func:`record_durably` writes, as its ``unrecorded`` map names them.
SESSION_BASE, DURABLE_BASE, DRIVER_BASE = "flow dir", "workdir", "driver record"

#: The directory a headless driver owns for the phase it drives. The driver creates it, exports its path to every
#: session it launches, reads the findings in it after each turn and removes it with the phase. The window marker and
#: the session's flow dir belong to the model's flow and go when the model closes the window, so a finding the driver
#: has to read after the turn is also filed here.
DRIVER_RECORD_ENV = "PLURAI_RUN_DRIVER_RECORD"


def driver_record_dir(env: Mapping[str, str] | None = None) -> Path | None:
    """The driver-owned record directory this session was launched with, or None when no driver launched it
    or the directory is gone."""
    raw = (os.environ if env is None else env).get(DRIVER_RECORD_ENV, "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


def record_durably(kind: str, *, session_base: Path, art: Path | None, **row: object) -> tuple[dict, dict[str, str]]:
    """File ONE run driver finding into every base a reader looks in. The one writer for every run driver
    dispatch: the hook route (`ask_guard._headless_deny`) and the no-tool route (`unasked`), each for
    a declared default and for a no-default STOP.

    ``session_base`` (the session-keyed flow dir) first, for the same-session readers
    (`headless_driver._findings`, `report_guard`); then ``art`` (`<workdir>/artifacts`), the one base
    a LATER session on the same workdir reads without this run's session id (`marker_bases`). Never
    creates the workdir: ``art`` is written only when its parent exists, because a phantom
    `plurai_sim/` re-points resolution. A base equal to ``session_base`` is written once.

    A headless driver's record directory (:data:`DRIVER_RECORD_ENV`) gets a third copy. It is the only one that
    outlives a turn on every path: the session copy goes with the window, and ``art`` exists only once the scan
    has mapped the project.

    Returns the finding and ``{base: why it did not land}`` (empty when every base landed). The readers
    dedup the copies (`_findings` on identity minus ``at``; `build_run_json` by gate+command).
    """
    finding = {"kind": kind, **row}
    unrecorded: dict[str, str] = {}
    why = _record(session_base, dict(finding))
    if why is not None:
        unrecorded[SESSION_BASE] = why
    if art is None or not art.parent.is_dir():
        where = "no workdir resolved" if art is None else f"{art.parent} does not exist"
        unrecorded[DURABLE_BASE] = f"{where}, and nothing is created for a record"
    elif art != session_base:
        try:
            art.mkdir(exist_ok=True)
        except OSError:
            pass  # _record reports the failed append
        why = _record(art, dict(finding))
        if why is not None:
            unrecorded[DURABLE_BASE] = why
    record = driver_record_dir()
    if record is not None and record not in (session_base, art):
        why = _record(record, dict(finding))
        if why is not None:
            unrecorded[DRIVER_BASE] = why
    return finding, unrecorded


def _record_when_headless(kind: str, **row: object) -> str | None:
    """File finding ``kind`` for the run in force when its run driver is headless, else nothing.

    An interactive run has a person to read the text, and an absent finding is how a run record says a person was here.
    The row goes to the session's flow dir, to ``<workdir>/artifacts`` and to the driver's record directory, as
    :func:`record_durably` files every finding. Returns None when the row landed in at least one of them, or the run is
    not headless; else why it landed nowhere, because the driver learns of the stop from this row alone."""
    if not nobody_answers():
        return None
    try:
        from _workdir import flow_dir, resolve_workdir  # noqa: PLC0415 - hooks/ is on sys.path wherever this module is

        workdir = resolve_workdir()
        base = flow_dir() or workdir
    except Exception as exc:  # noqa: BLE001 - resolution is best-effort by contract
        return f"{type(exc).__name__} resolving where the run record lives"
    if base is None:
        return "no session directory and no workdir resolved"
    art = workdir / "artifacts" if workdir is not None else None
    _, unrecorded = record_durably(kind, session_base=base, art=art, **row)
    driver_landed = driver_record_dir() is not None and DRIVER_BASE not in unrecorded
    if SESSION_BASE in unrecorded and DURABLE_BASE in unrecorded and not driver_landed:
        return "; ".join(f"{where}: {why}" for where, why in unrecorded.items())
    return None


def record_version_refusal(source: str, text: str) -> str | None:
    """File :data:`VERSION_REFUSED` for a headless run (:func:`_record_when_headless`)."""
    if source not in REFUSAL_SOURCES:
        return f"{source!r} is not a stopper that files a version refusal"
    return _record_when_headless(VERSION_REFUSED, source=source, text=text)


def record_halt(halt: str, text: str) -> str | None:
    """File :data:`HALTED` for a headless run (:func:`_record_when_headless`)."""
    return _record_when_headless(HALTED, halt=halt, text=text)


def record_ask_outcome(base: Path, *, gate: str, command: str, answered: bool) -> tuple[dict, str | None]:
    """WHAT BECAME OF A SANCTIONED ASK: it came back with an answer, or it came back without one.

    The interactive counterpart of the refusal and the decision ``record_durably`` files, and the whole
    record on that path — the ask was ALLOWED, so nothing here knows (or may claim) how the gate was
    resolved afterwards. Returns the finding and why it did not land (None when it did), because a
    record that did not land is a record no reader will see.
    """
    finding = {"kind": ASK_ANSWERED if answered else ASK_UNANSWERED, "gate": gate, "command": command}
    return finding, _record(base, finding)


#: The events that say how a gate was MET: handed a default, stopped, put to a person, or answered
#: by the run driver's own declaration.
GATE_EVENTS = (DEFAULT_APPLIED, INPUT_REQUIRED, ASK_UNANSWERED, ASK_ANSWERED, ANSWER_DECLARED)


def latest_gate_event(bases: list[Path], gate: str) -> dict | None:
    """The latest :data:`GATE_EVENTS` row for ``gate`` across ``bases``, or None when none was filed."""
    latest = None
    for finding in read_findings(bases):
        if finding.get("gate") == gate and finding.get("kind") in GATE_EVENTS:
            latest = finding
    return latest


def read_findings(bases: list[Path]) -> list[dict]:
    """Every typed finding across ``bases``, oldest first — :func:`read_findings_with_gaps` without
    the gaps, for the readers that act on rows and publish no record."""
    return read_findings_with_gaps(bases)[0]


def read_findings_with_gaps(bases: list[Path]) -> tuple[list[dict], list[dict]]:
    """``(rows, gaps)``: every typed finding across ``bases`` oldest first, and what could NOT be read.

    A gap is ``{"base", "line", "why"}`` for each line that is not a JSON object (a killed writer
    leaves a truncated one; ``line`` is 1-based) and ``{"base", "line": None, "why"}`` for a file
    that exists but could not be read. An absent file is not a gap: no gate reached that base.
    A record that could not be read in full is partial, never "no gate was reached".
    """
    out: list[dict] = []
    gaps: list[dict] = []
    for base in bases:
        try:
            text = (base / FINDINGS_MARKER).read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as exc:
            gaps.append({"base": str(base), "line": None, "why": f"{type(exc).__name__}: {exc.strerror or exc}"})
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError as exc:
                gaps.append({"base": str(base), "line": number, "why": f"not JSON ({getattr(exc, 'msg', exc)})"})
                continue
            if isinstance(record, dict):
                out.append(record)
            else:
                gaps.append({"base": str(base), "line": number, "why": "not a JSON object"})
    out.sort(key=lambda r: r.get("at") or 0)
    return out, gaps


def marker_bases(workdir: Path | None = None, session_id: str | None = None, record: Path | None = None) -> list[Path]:
    """Every directory THIS run's run driver markers may live in, most authoritative first.

    A hook is inside the session and gets its directory from ``_workdir.marker_dirs``. A SCRIPT
    often is not: ``scripts/headless_driver.py`` launches the session, so its own environment
    carries no ``CLAUDE_CODE_SESSION_ID`` and the session-keyed directory is invisible to it —
    which is why the extra sources are accepted here rather than re-derived per caller:

    * ``session_id`` — the id the driver read back from its own ``claude -p`` turn;
    * ``workdir`` — where the markers land when no session id was exported at all (the documented
      fallback layout, and the one ``flow-window.sh`` uses in that case);
    * ``record`` — the driver's own record directory (:data:`DRIVER_RECORD_ENV`), first because the model's
      flow cannot remove it.

    A reader that looked in only one of them "no findings" for a run that filed some,
    which is the exact shape of the silence this whole arm exists to remove.
    """
    out: list[Path] = [record] if record is not None else []
    try:
        from _workdir import FLOW_ROOT, marker_dirs  # noqa: PLC0415 - hooks/ only
    except Exception:
        FLOW_ROOT = None  # type: ignore[assignment]
        marker_dirs = None  # type: ignore[assignment]
    sid = (session_id or "").strip()
    if sid and "/" not in sid and sid not in (".", "..") and FLOW_ROOT is not None:
        out.append(FLOW_ROOT / sid)
    if marker_dirs is not None:
        try:
            out += [d for d in marker_dirs(None) if d not in out]
        except Exception:  # pragma: no cover - resolution is best-effort by contract
            pass
    if workdir is not None and workdir not in out:
        out.append(workdir)
    # Where `flow-window.sh unasked` writes: `$ART`, which no arm sweeps, so a suite sees the scan's rows.
    if workdir is not None:
        out.append(workdir / "artifacts")
    return out


def _main(argv: list[str]) -> int:
    """The shell half's entry point — `scripts/flow-window.sh` and the driver read the mode here.

    Subcommands, each printing ONE line so a shell can capture it with `$(...)`:
      mode                -> `<mode> <source>`
      stamp <dir>         -> stamps <dir> with the resolved mode, prints it
      findings [<dir>...] -> one JSON object per typed finding (nothing when there are none)
      unasked <gate> <flowdir> <art> [why] -> files the SAFE default row (``why`` is ``no-ask-tool`` unless
                          ``flow-window.sh sanction`` passes ``headless``), prints the line to show
      autonomous <gate> <flowdir> <art> -> a live /simulate run's answer to <gate>, filed; exit 3 when the run still asks
      retire <art> <command>          -> drops <command>'s earlier durable rows (a fresh arm)
    """
    action = argv[0] if argv else "mode"
    if action in ("mode", "stamp"):
        resolution = resolve()
        if action == "stamp":
            if len(argv) < 2:
                print(f"usage: {Path(__file__).name} stamp <dir>", file=sys.stderr)
                return 64
            stamp(Path(argv[1]), resolution)
        print(f"{resolution.mode} {resolution.source}")
        return 0
    if action == "findings":
        bases = [Path(a) for a in argv[1:]] or marker_bases()
        for finding in read_findings(bases):
            print(json.dumps(finding, ensure_ascii=False))
        return 0
    if action == "retire":
        if len(argv) != 3:
            print(f"usage: {Path(__file__).name} retire <art> <command>", file=sys.stderr)
            return 64
        retire_durable(Path(argv[1]), argv[2], None)
        return 0
    if action == "autonomous":
        # autonomous <gate> <flowdir> <art>: `flow-window.sh sanction` inside a live /simulate run. Exit 0 with the line to
        # show when the run answered the gate (decision filed, nothing to ask); exit 3 with no output when it still
        # asks: no window (except an UNWINDOWED_GATES gate of a live plan), a gate outside the armed flow's budget (the
        # guard voids those), no live plan, or a gate the run puts to the user. The caller has already left the
        # declared-headless run driver to its own dispatch.
        if len(argv) != 4:
            print(f"usage: {Path(__file__).name} autonomous <gate> <flowdir> <art>", file=sys.stderr)
            return 64
        from ask_guard import FLOW_GATES, KNOWN_GATES  # noqa: PLC0415 - hooks/ is on sys.path when run as a script

        gate, flowdir = argv[1], Path(argv[2])
        art = Path(argv[3]) if argv[3] else None  # unresolved: never a record under the cwd
        command = flow_command([flowdir])
        plan = autonomous_plan([flowdir]) if command != FALLBACK_COMMAND and gate in FLOW_GATES.get(command, KNOWN_GATES) else None
        line = None
        if plan is not None:
            line = apply_autonomous(gate, plan, session_base=flowdir, art=art, command=command,
                                    workdir=art.parent if art is not None else None)
        elif command == FALLBACK_COMMAND:
            line = apply_unwindowed(gate, flowdir, art)
        if line is None:
            return 3
        print(line)
        return 0
    if action == "unasked":
        # unasked <gate> <flowdir> <art>: the session has no ask tool. Dispatch on gate_disposition:
        # a safe default gets ONE durable row under <art>; a gate with none files the stop in <flowdir>.
        shaped = len(argv) in (4, 5)
        art = (Path(argv[3]) if argv[3] else None) if shaped else None  # unresolved: never a record under the cwd
        disposition = gate_disposition(argv[1], art=art) if shaped else None
        why = argv[4] if len(argv) == 5 and argv[4] in (WHY_NO_ASK_TOOL, WHY_HEADLESS) else WHY_NO_ASK_TOOL
        if disposition is None:
            print(f"usage: {Path(__file__).name} unasked <known gate> <flowdir> <art>", file=sys.stderr)
            return 64
        gate, flowdir = argv[1], Path(argv[2])
        command = flow_command([flowdir])
        if command == FALLBACK_COMMAND:
            # No window: a sub-skill run directly names its own command, as ``ask_guard`` records it.
            from ask_guard import DIRECT_RUN_GATES  # noqa: PLC0415 - hooks/ is on sys.path when run as a script

            command = DIRECT_RUN_GATES.get(gate, command)
        noted = {"note": disposition.note} if disposition.note else {}
        said = f" NOTE — {disposition.note}." if disposition.note else ""
        if disposition.kind == "input_required":
            _, unrecorded = record_durably(INPUT_REQUIRED, session_base=flowdir, art=art, gate=gate, command=command,
                                           needs=disposition.value, **noted)
            not_recorded = ", ".join(f"{base}: {why}" for base, why in unrecorded.items())
            print(f"no ask possible for '{gate}', and it has NO safe default — STOP the flow, end the run and report that the run driver "
                  f"must {disposition.value}" + (f" (NOT recorded — {not_recorded})." if not_recorded else ".") + said)
            return 0
        resolution = read_stamp([flowdir]) or resolve()
        if disposition.kind == "declared":
            _, unrecorded = record_durably(ANSWER_DECLARED, session_base=flowdir, art=art, gate=gate, command=command,
                                           answer=disposition.value, declared_in=disposition.declared_in, why=why,
                                           mode=resolution.mode, declared_by=resolution.source, **noted)
            durable_gap = unrecorded.get(DURABLE_BASE)
            print(declared_line(gate, disposition) + (f" (NOT recorded: {durable_gap}.)" if durable_gap is not None else "") + said)
            return 0
        default = disposition.value
        scope = scope_of_default(gate, default, art=art, mode=resolution.mode, command=command)
        _, unrecorded = record_durably(DEFAULT_APPLIED, session_base=flowdir, art=art, gate=gate, command=command, default=default,
                                       why=why, mode=resolution.mode, declared_by=resolution.source, **noted, **scope)
        # The durable row is the one a later session's run.json reads, so it is what this line reports.
        durable_gap = unrecorded.get(DURABLE_BASE)
        if durable_gap is None:
            where = f"recorded in {art / FINDINGS_MARKER}" if art is not None else "recorded"
        else:
            where = f"NOT recorded: {durable_gap}"
        print(f"no ask possible for '{gate}' — SAFE default '{default}' applied ({where}).{prerequisite_rule(gate, default)}"
              " Say so in ONE line and continue the flow." + said)
        return 0
    # THE REAL FILE NAME, derived rather than spelled: there is no `hooks/_run_driver.py` (that
    # spelling is only the in-hook `import _run_driver` alias), and both real
    # callers — `scripts/flow-window.sh` and `simulation-setup/SKILL.md` — invoke this file. An
    # run driver who copies a usage line naming a file that does not exist runs nothing.
    print(f"usage: {Path(__file__).name} mode|stamp <dir>|findings [<dir>...]|unasked <gate> <flowdir> <art>|autonomous <gate> <flowdir> <art>|retire <art> <command>  (got {action!r})",
          file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _utf8_stdio import utf8_stdio

    utf8_stdio()
    sys.exit(_main(sys.argv[1:]))
