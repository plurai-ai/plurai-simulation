#!/usr/bin/env python3
"""Final-report style gate for the customer-facing plurai commands (Stop hook).

The report doctrine (docs/report-doctrine.md) demands the final message present the
PRODUCT, never the build's defect chronology or spend — and prose alone demonstrably did
not hold that (see docs/adr/0003-flow-fidelity-enforcement.md for the field failures).

MECHANISM: while a command's flow marker (``$FLOWDIR/.plurai_flow_active``, line 1 =
command name) exists, this Stop hook validates ``last_assistant_message`` against a base
profile (no backend-spend figures — the agent's OWN transaction amounts pass; no raw
backend vocabulary — a crash attributed to the agent passes; no build-chronology; no
suite-self-flaw narration outside agent-naming clauses; no unsanctioned question-shaped
stall — a quoted agent question passes) plus a per-command profile (the report skeleton's
anchor on completion claims; the contact-Plurai failure hand-off is anchor-exempt) plus
the PENDING contract (while a window is armed, a turn may end only if it LANDS the flow,
ends on the USER via a sanctioned ask, or has harness-tracked work in flight — recorded
in ``.flow_pending`` by ``pending_register.py``; any other turn end stalls the flow,
observed live as a completed run sitting unprocessed until a human nudged it). A turn that
answers a message the user TYPED since the window was armed (read from the payload's
``transcript_path``) is exempt from the turn-end rules — that contract, the question-stall
and the unilateral-stop rule — because the user, not the flow, decides where it ends. The
rule of thumb behind every exemption: a defect the suite found in the CUSTOMER'S AGENT is
a DELIVERABLE, never a violation — the report's whole spine for /simulation-run and
/simulation-fixes, one licensed line for /simulation-generate-suite and
/analyze-simulation. A violation is answered with ONE short line that names what to change
(``_reason``): the correction is shown to the user, so the rules it enforces are in the report
section the guarded skills carry (docs/report-doctrine.md), where the model reads them before it
writes. On Claude Code the line is ``additionalContext`` at the Stop (feedback, not an error), on
the others the block. A CLEAN anchored hand-off closes the flow
window itself — the skills must not ``rm`` the marker before rendering, or this hook
never sees the one message it exists to validate; early exits and session ends are
closed by the skills' own-marker-guarded ``rm`` and the SessionEnd sweeper.

Two checks need no window: the /plurai-diagnose hand-off (the support bundle in a code block) may carry at most
one line after the block, and its text outside the block must send the user to Plurai support. It is a single
print, not a flow, so the bundle's own last line identifies it (``_diagnose_hand_off``).

DESIGN INVARIANTS
-----------------
* **Fail-open** (a crashed guard must never wedge the session).
* **The strings are DATA, the shape is code.** Every anchor and lexicon lives in
  ``hooks/anchors.json`` (command → locale → strings); a non-English deployment adds a locale
  rather than being wedged by anchors it cannot express. Unloadable data disables the guard
  loudly (``_hookfail.note``) rather than blocking a turn it cannot judge.
* **Strike-limited**: after 3 consecutive blocks the guard stands down AND closes the
  window (marker + sanction + counter). Enforcement degrades to prose, never to a stuck
  turn — and, deliberately, for the REST of the flow, not just that message: leaving the
  marker armed with enforcement off kept ``ask_guard`` denying for the whole session
  (observed live). The harness's own 8-block cap is the outer bound. The one exception is the
  PREREQUISITE hold (``_unresolved_prerequisites``): it always leaves a legal exit, so it is never
  stood down.
* Allow = no decision; a correction = ``model.correct_stop`` carrying the line.
* **Harness-neutral.** The guard judges ``Event.last_message``, which the harness's codec fills: from
  the payload where the harness sends the message, from the harness's transcript where it does not.
  A message the codec could not fill is "cannot judge", never "nothing to complain about".
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).parent))
from flow_arm import drives  # noqa: E402 — THE driver table, shared with Step 0 (see flow_arm.FLOW_DRIVERS)
from flow_arm import _INVOKE_RE as _FLOW_INVOCATION_RE  # noqa: E402 — the one slash-invocation shape that arms a window
import _run_driver  # noqa: E402 - the stop kinds and the headless driver's finding
import _platform  # noqa: E402 - is_local_drive_path: never touch another host's path on Windows
from _harness import model, transcript  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable

    from _harness.model import Decision, Event

# --- the language, as data ---------------------------------------------------------------
# Every string this guard matches lives in `anchors.json`, keyed command -> locale -> strings
# (plus a `shared` block for the lexicons that are not per-command). The MACHINERY around them
# — a line-head position, the skeleton separator, sentence splitting, the tier rules — is
# language-neutral already and stays here.
#
# WHY. The anchors and lexicons are English, and the guard degrades in OPPOSITE directions on
# either side of that. The forbid-side rules under-fire on another language (a non-English
# report says nothing this can read, so nothing is caught); the anchor rule over-fires (no
# anchor, no progress word, so the message is neither a hand-off nor mid-flow and is BLOCKED
# demanding an anchor it cannot express — three times, then the guard stands down). A
# deployment reporting to its customer in another language was wedged by its own style gate,
# and there was nothing to configure. There is now: add a locale block. Every locale in the
# file is read and the results are UNIONED, so adding one never takes English away.
_ANCHOR_DATA_PATH = Path(__file__).with_name("anchors.json")
#: Never matches, whatever it is applied to — the safe stand-in for a lexicon the data does not
#: carry. An empty alternation would match EVERY string, which for `_COMPLETION_RE` means every
#: message owes an anchor and for `_ABORT_RE` means every message is an abort.
_NEVER = re.compile(r"(?!)")


def _anchors_are_off(exc: BaseException) -> None:
    """One loud line saying the guard has no language. `_hookfail.note` is the same channel a
    hook that cannot even import uses, because "the contract is OFF" is what an operator needs
    told — and it is the only signal that distinguishes an unloadable table from a quiet one."""
    try:
        from _hookfail import note

        note("report_guard (anchors.json)", exc)
    except Exception:  # noqa: BLE001 - the loud-failure helper must never BE the failure
        pass


def _load_anchor_data() -> dict:
    """`anchors.json`, or `{}` with one loud line — never an exception.

    Fail-open is this module's first design invariant: a guard that cannot load its own data
    must degrade to no enforcement, not to a wedged turn.

    EVERY `{}` IS ANNOUNCED, including the one that is not an exception. A file that parses as
    valid JSON but is not an OBJECT — a stray `[]`, a top-level string, an editor that saved the
    README array alone — would return `{}` from inside the `try` and take the same stand-down
    branch as an unreadable file with nothing printed. The operator would then see a session where
    every hand-off passed and no anchor was ever required, which is exactly what a correctly
    configured guard looks like from the outside.
    """
    try:
        loaded = json.loads(_ANCHOR_DATA_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - any failure here disables the guard, loudly
        _anchors_are_off(exc)
        return {}
    if isinstance(loaded, dict):
        return loaded
    _anchors_are_off(
        TypeError(
            f"{_ANCHOR_DATA_PATH.name} is valid JSON but a {type(loaded).__name__}, not an object — "
            "the guard has no anchors and no lexicons, so it enforces nothing"
        )
    )
    return {}


_ANCHOR_DATA = _load_anchor_data()


def _locales(block: object) -> list[dict]:
    """Every locale entry under one block, in file order. Unknown shapes are skipped, not raised."""
    return [v for v in block.values() if isinstance(v, dict)] if isinstance(block, dict) else []


def _union_re(sources: list[str], *, ignorecase: bool = True, multiline: bool = False) -> re.Pattern[str]:
    """One pattern over every locale's source, each branch parenthesised.

    Parenthesised because a locale's source is itself a top-level alternation: joining two of
    them raw would let one locale's last branch bind to the next locale's first.
    """
    usable = [s for s in sources if isinstance(s, str) and s]
    if not usable:
        return _NEVER
    flags = (re.IGNORECASE if ignorecase else 0) | (re.MULTILINE if multiline else 0)
    return re.compile("|".join(f"(?:{s})" for s in usable), flags)


def _shared_re(key: str) -> re.Pattern[str]:
    """The named shared lexicon, unioned across locales. Case-insensitive, like every one of them."""
    return _union_re([entry.get(key) for entry in _locales(_ANCHOR_DATA.get("shared"))])


def _shared_strings(key: str) -> tuple[str, ...]:
    """One literal per locale for a shared string key (today: the failure hand-off sentence)."""
    return tuple(
        dict.fromkeys(s for entry in _locales(_ANCHOR_DATA.get("shared")) if isinstance(s := entry.get(key), str) and s)
    )


def _command_strings(key: str) -> dict[str, tuple[str, ...]]:
    """`command -> every locale's literals` for `anchors` / `progress_anchors`.

    UNIONED rather than picked: nothing here knows which language this session reports in, and
    guessing would be the same wedge one level along. An English hand-off and a Hebrew one both
    land, which is also what a mixed-language session needs.
    """
    out: dict[str, tuple[str, ...]] = {}
    for command, block in (_ANCHOR_DATA.get("commands") or {}).items():
        values = [v for entry in _locales(block) for v in (entry.get(key) or []) if isinstance(v, str) and v]
        if values:
            out[str(command)] = tuple(dict.fromkeys(values))
    return out


FLOW_MARKER = ".plurai_flow_active"
ASK_SANCTION = ".ask_sanctioned"
ASK_PENDING = ".ask_pending"
# Pending-work register: written by ``pending_register.py`` (PostToolUse) when a tool call
# actually put harness-tracked work in flight — a ``run_in_background`` Bash call, a ``Task``
# spawn, a ``Monitor`` watch. Its existence is an OBSERVATION of a tool call, never a model
# assertion: an earlier cut had the skills write it, which let a model satisfy the gate by
# writing the file and doing nothing (verified), the same model-cooperative link ``flow_arm``
# exists to remove. Content is a human-readable note; the guard never parses it.
#
# The register RECORDS work, so it lives exactly as long as the work does: it is read here,
# never consumed. Consuming it on a passing turn made it a one-shot token while the task it
# stood for kept running, so the NEXT turn blocked with the runner still in flight — and each
# block is a strike, which is how four blocks landed on a flow that had never stalled. Its END
# is observed rather than assumed (``pending_register``): a background shell's completion in
# the tool result that reports it finished, a subagent's at ``SubagentStop``. Any flow close
# sweeps it too — landing or standing down (``_sweep``), the next flow opening
# (``scripts/flow-window.sh``), session end (``flow_cleanup.py``).
PENDING = ".flow_pending"
# The staleness BACKSTOP — deliberately not the primary clear, which is the completion above.
# This bound catches only the completions nobody ever observes: a ``Monitor`` that exits on its
# own, a harness that hands back no shell handle to match a register against, a session that
# never reads the result at all.
#
# So it is sized to OUTLIVE the work rather than to approximate it. Nothing refreshes a
# register mid-wait: ``poll_job.sh`` is foreground by design — it blocks inside the turn and
# never registers at all — so the only records that span turns are a backgrounded runner, a
# subagent and a ``Monitor``, none of which re-stamp themselves, and a backgrounded suite run
# is tens of minutes that grow with the scenario count. Cutting it short blocks a turn with a
# live runner behind it, which is the failure this record exists to end; leaving it long only
# delays a strand whose completion signal never arrived.
PENDING_TTL_SECONDS = 4 * 60 * 60
# The same backstop for a BACKGROUNDED MCP CALL, which is a different kind of record. The
# harness moves an MCP call to the background after ~2 minutes and notifies at its terminal
# status, and ``pending_register`` releases on that notification — but an MCP call is a REGISTER
# and not a lease: there is no start/stop pair, so a notification the session never receives
# (a compact, a resumed session, a dropped queue) leaves the record standing. Sized to the work
# instead of to the four-hour runner: the longest diagnostic call is an edit-session turn, tens
# of minutes at worst, so twenty is generous without blessing a whole afternoon.
MCP_PENDING_TTL_SECONDS = 20 * 60
# The register-note prefix that selects the bound above — written by ``pending_register`` and
# read here, so the two halves cannot pick different clocks for the same record.
MCP_PENDING_PREFIX = "background mcp"
# The subagent LEASE (see pending_register): a SET of live holders keyed by agent id, each
# added at ``SubagentStart`` and removed at ``SubagentStop`` — held while the set is
# non-empty. The register above answers "was work launched"; only the lease
# answers "is a subagent still running", and a spawned critic is not re-spawned to re-announce
# itself — so a wait it spans has nothing else to back its second turn. Released on the STOP
# event rather than by age, with the same bound as a backstop for a lease whose stop never
# arrives. Read by the check, never consumed by it: the holder's ``SubagentStop`` releases it,
# and the flow-end sweep (landing or standing down) clears whatever is left.
LEASE = ".flow_lease"
# The dropped-turn record written by ``flow_interrupt`` (StopFailure) and consumed by
# ``flow_heartbeat``. Swept with the window: it says "a turn of THIS flow died", which stops being
# true the moment the flow closes, and a survivor would release one heartbeat firing into the next.
DROPPED = ".flow_dropped"
# The command run id leaves with the window it was minted for (hooks/_command_run.py).
COMMAND_RUN_ID = ".command_run_id"
STRIKES = ".report_guard_strikes"
MAX_STRIKES = 3
# THE CLOCK: a turn that ends on the mid-flow line with work put in flight MOMENTS AGO is the
# turn that STARTS a silence, and an unexplained silence is indistinguishable from a dead flow —
# observed live, eleven quiet minutes behind a bare 'Suite: in progress' provoked a nudge 19
# seconds before the critic naturally finished. Skill prose could not hold the rule (a live
# session sliced the skill file starting two lines below it), so the wait-announcing message is
# validated here: it must carry BOTH a rough duration and the resumes-by-itself promise. Only
# the turn whose register is younger than this bound owes it — later wait updates ride free,
# and the bound is generous because the spawn and the turn end are seconds apart in practice.
FRESH_PENDING_SECONDS = 5 * 60
#: A number, an optional range ("10-30", "10 to 30"), and an optional "more"-word before the unit.
#: The "more"-word is not decoration: "about 5–10 more minutes" is the natural clock for a wait
#: already under way, and without it the live message carrying exactly that sentence was blocked
#: as "a wait with no clock" and re-rendered (RT37). A number is still required.
_CLOCK_QUANTITY = r"\d+\s*(?:(?:[-–—]|\bto\b)\s*\d+\s*)?(?:(?:more|further|additional|extra)\s+)?"
_CLOCK_DURATION_RE = re.compile(
    # ONE group: an alternation that bound at the top level would let a bare "minute" or
    # "hour" — "the last-minute edits" — satisfy a rule whose own remedy demands a
    # number. A duration with no number is not a duration. Seconds are a duration too ("150 seconds",
    # "90 secs", "45s"); the bare "s" must touch its digits, and the word forms end at a boundary so
    # "3 sections" is not one.
    r"~?\s*" + _CLOCK_QUANTITY + r"(?:min|minute|hour|sec(?:ond)?s?\b|(?<=\d)s\b)",
    re.IGNORECASE,
)
#: The honest ceiling for a SUBAGENT wait, in minutes. Requiring *a* number was not enough: a
#: live session promised "5-10 minutes" for a critic, and 46 measured critic runs on this machine
#: say median 13.5 min with only 20% finishing inside 10 — so that promise breaks four times in
#: five, and the wait LOOKS hung the moment it lapses. An under-promise is worse than no promise:
#: it manufactures a deadline reality then breaches. p75 is 15.5 min, so a stated ceiling below
#: this is a claim the work usually exceeds. Scoped to subagent waits ONLY (the register note /
#: the lease say which) — a backgrounded run or an edit-session turn is genuinely shorter, and
#: forcing this floor on those would buy honesty in one place with a lie in another.
SUBAGENT_WAIT_FLOOR_MINUTES = 15
_DEAD_LINK_VIOLATION = "a local link that opens nowhere"
#: The opening words of the violations that are COURTESY rules — a single missing or dishonest
#: sentence about a wait, or one link — as opposed to the report doctrine the strike budget was sized for.
#: Matched on the message's own prefix so the list cannot drift from the strings emitted above.
#: The Agent verdict line is one long pasted sentence: a hand-off that re-words it is corrected once per turn, never
#: charged a strike — three of them would disarm the consent guard over a paste (`_agent_line_violation`).
_AGENT_LINE_VIOLATION = "the hand-off does not carry the Agent verdict"
_COURTESY_VIOLATIONS = ("a wait with no clock", "an under-promised wait", _DEAD_LINK_VIOLATION, _AGENT_LINE_VIOLATION)
#: Every minute-count and second-count a clock sentence states; the LARGEST, in minutes, is the ceiling it
#: promises. Same quantity shape as the duration above, so a clock the first rule accepts is never one this
#: ceiling reader cannot see ("5–10 more minutes" must still meet the subagent floor). Seconds match what
#: ``_CLOCK_DURATION_RE`` accepts as a duration.
_CLOCK_MINUTES_RE = re.compile(r"(\d+)\s*(?:(?:[-–—]|\bto\b)\s*(\d+)\s*)?(?:(?:more|further|additional|extra)\s+)?min", re.IGNORECASE)
_CLOCK_SECONDS_RE = re.compile(r"(\d+)\s*(?:(?:[-–—]|\bto\b)\s*(\d+)\s*)?(?:(?:more|further|additional|extra)\s+)?(?:sec(?:ond)?s?\b|(?<=\d)s\b)", re.IGNORECASE)
#: An estimate that declines to cap itself ("or longer", "sometimes more") keeps its promise no
#: matter how long the work runs, so it satisfies the floor whatever number it pairs with.
_CLOCK_OPEN_ENDED_RE = re.compile(
    r"\bor (?:so|more|longer)\b|sometimes (?:longer|more)|occasionally longer|often longer|\bup to\b|\bat least\b",
    re.IGNORECASE,
)
_CLOCK_RESUME_RE = re.compile(
    r"by itself|on its own|automatic|resumes? when|nothing (?:for you )?to (?:type|do)|no need to (?:type|do|ask|nudge)",
    re.IGNORECASE,
)

# --- base profile -----------------------------------------------------------------------

# Spend-context required in BOTH directions: a commerce agent's own prices ("the agent
# quoted $19.99") are the domain-concrete detail the doctrine DEMANDS — only backend
# spend is banned. The trigger words are the BACKEND's spend vocabulary (cost / spend /
# budget / total_cost_usd), never a transaction verb: "the agent charged $19.99 instead of
# refunding it" is a FINDING, and blocking it made /simulation-run's own deliverable
# unrenderable (observed live).
#
# The backend's own field name is spend whatever sentence carries it, so it blocks outright. A
# `$` figure beside cost/spend/budget does not: "the agent ignored the customer's $200 budget" and
# "the agent quoted a total cost of $1,240" are findings about the product under test, so those
# figures can be findings — but the DEFAULT is spend. A report line addresses "your agent"
# constantly ("For your agent, this cost $2.10"), so naming the agent proves nothing about whose
# money it is. A figure passes only when its clause names the agent, ATTRIBUTES the figure to the
# agent's own output or to the customer's side of the conversation (`_SPEND_ATTRIBUTION_RE`), and
# names none of the product's own machinery (`_SPEND_MACHINERY_RE`).
_COST_FIELD_RE = re.compile(r"total_cost_usd", re.IGNORECASE)
_COST_FIGURE_RE = re.compile(
    r"(?:\bcost|\bspen[dt]\b|\bbudget\b)[^.\n]{0,20}\$\s?\d"
    r"|\$\s?\d[\d.,]*[^.\n]{0,20}\b(?:cost|spen[dt]|budget|of spend)\b",
    re.IGNORECASE,
)
_SPEND_ATTRIBUTION_RE = re.compile(
    r"\b(?:quot(?:e[sd]?|ing)|sa(?:y|ys|id)|stat(?:e[sd]?|ing)|offer(?:s|ed)?|t(?:ell|ells|old)|list(?:s|ed)?"
    r"|propos(?:e[sd]?|ing)|pric(?:e[sd]?|ing)|charg(?:e[sd]?|ing)|wr(?:ite|ites|ote)|repl(?:y|ies|ied)"
    r"|answer(?:s|ed)?|recommend(?:s|ed)?)\b"
    r"|\b(?:customer|user|caller|client|traveller|traveler|guest|buyer|patient)(?:'s|s'|’s)",
    re.IGNORECASE,
)
# The machinery words, the measuring words, and the two phrasings that price a piece of WORK rather
# than name an amount in the conversation ("at a cost of $1.10", "$1.40 to grade"): a clause carrying
# any of them is spend however it attributes the figure.
_SPEND_MACHINERY_RE = re.compile(
    r"\b(?:suites?|scenarios?|polic(?:y|ies)|evals?|evaluat\w*|judg\w*|generat\w*|simulat\w*|backend|build\w*|built"
    r"|model calls?|tokens?|jobs?|runs?|running|test(?:s|ing)?|qualif\w*|spen[dt]|so far"
    r"|grad\w*|scor\w*|map(?:s|ped|ping)?|index\w*|measur\w*|benchmark\w*|audit\w*|probes?|probing)\b"
    # "$X to <verb>" only: "$1,240 to the customer" names who was quoted, not what the work cost.
    r"|\bat a cost of\b|\$\s?\d[\d.,]*\s+to\s+(?!(?:the|a|an|your|their|his|her|my|our|its|this|that|these|those|each|every|all)\b)[a-z]",
    re.IGNORECASE,
)
# charge/bill ARE spend when the sentence is not about the agent ("the backend billed
# $4.10") — so they keep the agent-sentence exemption instead of being dropped outright.
_DOMAIN_CHARGE_RE = re.compile(
    r"(?:\bcharge[ds]?\b|\bbilled\b)[^.\n]{0,20}\$\s?\d"
    r"|\$\s?\d[\d.,]*[^.\n]{0,20}\b(?:charge[ds]?|billed)\b",
    re.IGNORECASE,
)
# A markdown link (or image) target, angle form unwrapped. The `[` must not follow a word or a
# `]` — `handlers[0](event)` is prose, not a link. Code is stripped first — fenced (``` and ~~~),
# indented, and backtick spans of any length: a link QUOTED as code is an example, not a link
# the reader can click.
_LINK_TARGET_RE = re.compile(r"(?<![\w\]])\[[^\[\]\n]*\]\(\s*(?:<([^>\n]*)>|([^)\s]*))")
_CODE_RE = re.compile(
    r"^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$"  # fenced block
    r"|(?:^[ \t]*\n)((?:(?: {4}|\t)[^\n]*\n?)+)"  # indented block, after a blank line
    r"|(`+)[^\n]*?\3",  # code span
    re.MULTILINE | re.DOTALL,
)
_WORKING_LINK = ("http://", "https://", "mailto:", "#")
# A code location's line suffix: `:42`, `:42:7`, `:40-44`, `#L42`, `#L40-L44`.
_LINE_SUFFIX_RE = re.compile(r"(?:#L\d+(?:-L?\d+)?|:\d+(?::\d+)?(?:-\d+)?)$")


def _dead_local_links(message: str, workspace: tuple[Path, ...] = ()) -> list[str]:
    """Link targets that open nowhere: anything but a URL, an anchor, a ``file://`` URL of a file on this machine or a path that
    EXISTS — absolute, or relative to the WORKSPACE (the session cwd, which is the base VS Code resolves a
    chat link against; the extension's own prompt asks for `[a.py:42](src/a.py#L42)`). Never
    relative to the bench: the agent dir is a subdirectory of the workspace in every monorepo,
    and `plurai_sim/artifacts/<file>` from there is the dead link this rule exists for.
    scripts/report_link.py prints the target that works."""
    dead = []
    for m in _LINK_TARGET_RE.finditer(_CODE_RE.sub("\n", message)):
        target = m.group(1) if m.group(1) is not None else m.group(2)
        if target.startswith(_WORKING_LINK):
            continue
        if target.startswith("file://"):
            # What report_link.py prints under Copilot and Codex for a report with no page: the URL opens
            # when it names a file on this machine. Another host or a query opens nothing here; a
            # fragment is an anchor inside the file.
            from urllib.request import url2pathname  # noqa: PLC0415 - only a file:// link needs it

            parts = urllib.parse.urlsplit(target)
            path = Path(url2pathname(parts.path))  # Windows: `/C:/a%20b` is `C:\a b`; elsewhere the path unquoted
            if (
                parts.netloc.lower() not in ("", "localhost")
                or parts.query
                or not path.is_absolute()
                # Windows: a UNC or device path is dead before anything touches it (that would contact its host).
                or not _platform.is_local_drive_path(path, within=workspace)
                or not path.is_file()
            ):
                dead.append(target)
            continue
        path = Path(_LINE_SUFFIX_RE.sub("", target))
        if path.is_absolute() and _platform.is_local_drive_path(path, within=workspace) and path.exists():
            continue
        if not path.is_absolute() and target and any(
            _platform.is_local_drive_path(root / path, within=workspace) and (root / path).exists() for root in workspace
        ):
            continue
        dead.append(target)
    return dead


# Backend identifiers with no legitimate finding use — always internal.
_JARGON_RE = re.compile(r"detail\.warnings|job_status", re.IGNORECASE)
# Crash vocabulary is different in kind: "the agent crashed with a stack trace on turn 4"
# is a finding about the product under test, so it takes the same agent-sentence exemption
# the self-flaw rule uses. A traceback from the SUITE's own machinery still blocks.
_CRASH_JARGON_RE = re.compile(r"Traceback \(most recent call last\)|stack trace", re.IGNORECASE)
_CHRONOLOGY_RE = re.compile(
    r"one more cycle|remaining work|critic pass|critic #\d|qualification batch|iteration \d|\bcycle #?\d",
    re.IGNORECASE,
)
# Sentences attributing defects to the SUITE'S OWN parts (scenarios/evals/harness/suite),
# in either direction. Deliberately does NOT match "agent" — /simulation-fixes reports
# agent bugs as its product, and every command may present "the suite CAUGHT X".
def _self_flaw_re(command: str) -> re.Pattern[str]:
    # 'scenario' leaves the noun set for the two commands whose PRODUCT is a ranked list of
    # defects found in the AGENT: /simulation-fixes cites scenarios as the EVIDENCE LOCUS
    # ("scenario 3 shows the refund path is broken"), and /simulation-run's report names
    # WHICH scenario failed — that IS the report. 'persona' stays for run (a hollow persona
    # is suite self-criticism, not a finding); suite/eval/harness self-flaws block
    # everywhere, for every command.
    #
    # /simulation-generate-suite and /analyze-simulation also present agent findings now (one
    # licensed line each), and they DELIBERATELY do not get this carve-out. Their findings are
    # required to name the agent as the clause subject, which ``_unexempted_hit`` already
    # exempts — so the carve-out would buy them nothing while un-blocking the sentence they
    # are most likely to get wrong. Measured, under a generate-suite marker: widening the set
    # let through "Two of the seven scenarios are hollow", "Scenario 4 is broken", and
    # "Two scenarios were broken, so I regenerated them" — textbook suite self-flaw from the
    # one command whose loop exists to find and fix broken scenarios. A scenario-subject
    # finding still blocks here, and that is correct: the reason text tells it to re-phrase
    # with the agent as the subject, which is exactly what its skill prescribes.
    nouns = {
        "fixes": "suite|eval|harness",
        "run": "suite|eval|harness|persona",
    }.get(command, "suite|scenario|eval|harness|persona")
    # Clause-bounded gap ([^.;\n\u2014\u2013]): a noun and a flaw word in DIFFERENT clauses
    # are different claims — without this the match itself crossed a dash and the
    # agent-exemption below read a neighbouring clause's "agent" as its own. BOTH dashes:
    # an en-dash parenthetical is the same construction as an em-dash one, and
    # ``_clause_breaks`` splits on both, so the character class must too — otherwise the
    # two halves disagree about where a clause ends.
    gap = "[^.;\\n\\u2014\\u2013]{0,80}"
    return re.compile(
        rf"({nouns}){gap}\b(flaw|defect|broken|hollow|unearned|mis-?cited)"
        rf"|\b(flaw|defect|broken|hollow)\w*{gap}({nouns})",
        re.IGNORECASE,
    )


def _clause_breaks(message: str, *, dashes: bool = True) -> list[int]:
    """Indices of the sentence/clause boundaries in ``message``.

    ``.`` counts — EXCEPT between two digits. A decimal point is not a sentence end, and
    treating it as one truncated the clause immediately after every money match: executed
    under the ``run`` marker, "The order was charged $19.99 by the agent instead of
    refunded." lost its "agent" to the split and BLOCKED, while the same sentence with
    ``$20`` passed. The exemption must not depend on whether the amount carries cents, nor
    on the agent being named BEFORE the figure.

    BOTH dashes break. The en-dash was missing, so "The suite – which the agent drives –
    does not cover partial refunds." was ONE clause and the parenthetical's "agent"
    exempted the whole line — the exact leak the em-dash break was added to close.

    ``dashes=False`` yields SENTENCES instead (``.``/``;``/newline only). The gap rule
    scopes its SUITE-subject test that way: a subject in a parenthetical still owns the
    predicate it interrupts.
    """
    breaks: list[int] = []
    seps = ";\n—–" if dashes else ";\n"
    for i, ch in enumerate(message):
        if ch in seps:
            breaks.append(i)
        elif ch == "." and not (0 < i < len(message) - 1 and message[i - 1].isdigit() and message[i + 1].isdigit()):
            breaks.append(i)
    return breaks


def _unexempted_hit(message: str, pattern: re.Pattern[str], *, deny: re.Pattern[str] | None, require: re.Pattern[str] | None) -> bool:
    """A match whose SENTENCE does not mention the agent. Sentences naming the agent are
    findings about the product under test — the suite's value, never its flaw, and never
    leaked backend vocabulary either. Four rules share this exemption (self-flaw nouns,
    crash vocabulary, cost/budget and charge/bill figures, coverage-gap idioms): each is a violation when
    it is ABOUT THE SUITE and the deliverable when it is about the AGENT.

    ``deny`` and ``require`` narrow the exemption for a rule whose violation is routinely phrased
    AROUND the agent: a clause ``deny`` matches is never excused, and one ``require`` does not match
    is not excused by naming the agent alone. ``None`` for both keeps the plain exemption."""
    # Sentence AND clause level: an em-dash clause about a broken part scenario must not be
    # excused by "agent" appearing in a NEIGHBOURING clause.
    breaks = _clause_breaks(message)
    for m in pattern.finditer(message):
        start = max((b for b in breaks if b < m.start()), default=-1) + 1
        end = min((b for b in breaks if b >= m.end()), default=len(message))
        clause = message[start:end]
        if (
            "agent" not in clause.lower()
            or (deny is not None and deny.search(clause))
            or (require is not None and not require.search(clause))
        ):
            return True
    return False


# The phrase arm is unconditional; the trailing-'?' arm is quote-exempt (see _stall_hit).
# A trailing '?' that fired on ANY report would catch a verbatim quoted agent utterance offered as
# evidence, which is what a run/fixes report is made of; hence the exemption.
# ('.' already excludes newlines here - no DOTALL - so the 120-char window is one line.)
_STALL_PHRASE_RE = re.compile(
    r"(do you want|should i|how do you want|would you like|let me know (if|whether|which)).{0,120}$",
    re.IGNORECASE,
)


def _quoted_tail(message: str) -> bool:
    """Does the trailing '?' sit inside a QUOTATION rather than end a question to the user?

    Scoped to the message's LAST LINE: a markdown blockquote (the report's quotation form),
    or an opening quote still unclosed when the '?' lands. An inline quote that CLOSES
    (... last four digits?") never reaches this rule - the message then does not
    END in '?'. An unquoted trailing question still stalls.
    """
    line = message.rstrip().rsplit("\n", 1)[-1].strip()
    if line.startswith(">"):
        return True
    head = line[:-1]  # everything before the trailing '?'
    if head.count('"') % 2 or head.count("`") % 2:
        return True
    return head.rfind("\u201c") > head.rfind("\u201d")


#: A ``?`` that closes a sentence: followed by white space, the end, or the closing mark of a quote, a
#: parenthesis or a markdown emphasis. A query string (``report?run=7``) is followed by a letter.
_SENTENCE_QUESTION_RE = re.compile(r"\?(?=\s|$|[)\]\"'\u201d*_`])")
#: What a model writes after a question to tell the reader how to answer it, as opposed to the answer
#: itself ("Does it refuse a refund? Yes, in 3 of 4 runs.").
_REPLY_INVITATION_RE = re.compile(
    r"\s*(?:\([^()\n]*\)\s*$|(?:options?|choices?|alternatives?)\s*:|(?:please\s+)?(?:reply|answer|respond|choose|pick|select|say|tell me|let me know)\b)",
    re.IGNORECASE,
)
_LIST_ITEM_RE = re.compile(r"\s*(?:[-*+\u2022]|\d{1,2}[.)]|[a-zA-Z][.)])\s+\S")


def _asks(line: str) -> bool:
    """Does a sentence of ``line`` end in a ``?`` that is asked of the user and not answered on the same line?

    A ``?`` counts when nothing follows it but closing marks, or when what follows tells the reader how to
    reply. An unclosed quote or code span before it makes it part of a quotation.
    """
    for mark in _SENTENCE_QUESTION_RE.finditer(line):
        head = line[: mark.start()]
        if head.count('"') % 2 or head.count("`") % 2 or head.rfind("\u201c") > head.rfind("\u201d"):
            continue
        if not line[mark.end() :].strip(" \t)]\"'\u201d*_`") or _REPLY_INVITATION_RE.match(line, mark.end()):
            return True
    return False


def _lead_before_options(lines: list[str]) -> str | None:
    """The line that introduces the options list ending ``lines``, or None when no list ends them.

    Blank lines, list items and indented continuation lines are skipped; the first other line is the lead,
    whether or not a blank line separates it from the list.
    """
    index = len(lines)
    listed = False
    while index:
        line = lines[index - 1]
        if _LIST_ITEM_RE.match(line):
            listed = True
        elif line.strip() and not line[:1].isspace():
            break
        index -= 1
    return lines[index - 1].strip() if listed and index else None


def _question_in_last_paragraph(message: str) -> bool:
    """Is the message's closing paragraph a question the user must answer, beyond one that ends in ``?``?

    Two shapes, both from a model that asks in plain text: a question introducing an options list (the
    list is what ends the message), and a question sentence followed by how to reply. A ``?`` the paragraph
    goes on to answer, a heading, a blockquote line and a URL's query string are not a question to the user.
    """
    lines = [line.rstrip() for line in message.strip().splitlines()]
    lead = _lead_before_options(lines)
    if lead is not None:
        return not lead.startswith(("#", ">")) and _asks(lead)
    paragraph: list[str] = []
    for line in reversed(lines):
        if not line.strip():
            break
        if not line.lstrip().startswith(">"):
            paragraph.insert(0, line.strip())
    return _asks(" ".join(paragraph))


def _stall_hit(message: str) -> bool:
    """An unsanctioned question-shaped stall - a quoted agent question is evidence, not a stall."""
    stripped = message.strip()
    if _STALL_PHRASE_RE.search(stripped):
        return True  # an explicit ask stalls even when dressed as a quotation
    if stripped.endswith("?") and not _quoted_tail(stripped):
        return True
    return _question_in_last_paragraph(stripped)


# Suite-doubt framing — the fourth evasion shape, observed live in a /simulation-run
# hand-off: "Next: /simulation-fixes — it separates genuine agent bugs from test-setup
# artifacts …". The suite those scores came from was gate-validated; offering "maybe the
# tests are the problem" as a reading of the customer's failures un-earns the whole arc
# (and pre-diagnoses the NEXT command's work). /simulation-fixes itself is exempt — the
# conservative agent-vs-test classification IS its product; everywhere else the framing
# is doubt, not method.
_SUITE_DOUBT_RE = re.compile(
    r"test[- ]?setup (artifacts?|issues?|problems?)"
    r"|test artifacts?"
    r"|\b(scenario|eval|suite|test)s? (may|might|could) (be|have been) (wrong|broken|the (issue|problem|cause))",
    re.IGNORECASE,
)

# SUITE-COVERAGE narration — doctrine item 3's own words ("accepted gaps, uncovered flows,
# known limitations of the SUITE"), which the self-flaw rule never caught because a gap
# sentence carries no flaw WORD: "the suite does not cover multi-currency refunds" sailed
# through.
#
# WHOSE gap is it? That is the whole question, and the first draft answered it with the
# absence of the literal token "agent". That proxy failed in BOTH directions, reproduced:
#   * it blocked the DELIVERABLE whenever a finding named the component instead of "the
#     agent" — which the doctrine simultaneously demands ("Hebrew input hits a known
#     limitation in the tokenizer", "Scenario 4 exposed an uncovered path in the escalation
#     router"). The doctrine asks for domain language; the proxy punished it.
#   * it was voice-dependent, hence arbitrary: the subject-free "not covered by …" arm
#     passed "The mock does not cover webhooks" but blocked "The payment API is not covered
#     by the mock" — the same claim, flipped. It also blocked doctrine item 4's own
#     licensed run-integrity line in the passive voice.
#   * and it leaked the other way: the gap character class did not exclude the em-dash, so
#     a match spanned two clauses and the exemption read a NEIGHBOURING clause's "agent" as
#     its own — "The suite — which the agent drives — does not cover partial refunds."
#     passed.
#
# So the rule now keys on an explicit SUITE-SIDE SUBJECT in the same sentence, and drops
# the agent-token exemption entirely (a subject test does the separating, and keeping the
# exemption would re-open the parenthetical leak above). Consequence, accepted
# deliberately: a subject-less gap sentence ("multi-currency refunds are not covered")
# passes. False negatives here cost a line of narration; false positives cost the product.
#
# Its counterpart is doctrine item 4: run-INTEGRITY facts (never executed, errored rather
# than failed, a substituted model, a suite that shipped short) are RESULTS about the run,
# not coverage claims, so that vocabulary is explicitly non-blocking, per sentence.
_SUITE_SUBJECT_RE = re.compile(
    r"\b(?:the |this |our |that )?suites?\b"
    r"|\b(?:the|this|these|our) (?:generated |shipped |final |qualification )?(?:scenarios|evals|tests|policies|judges)\b"
    r"|\b(?:the|this) (?:generated |shipped |final )?(?:scenario set|eval set|test set|policy set|set of scenarios|set of evals)\b"
    r"|\bthe generated set\b|\bthis set\b|\bcoverage of the (?:suite|set)\b",
    re.IGNORECASE,
)
# Doctrine item 4's vocabulary. A sentence carrying any of it is a RESULT statement and is
# exempt from the gap rule outright — "Run integrity — 4 scenarios errored and are not
# covered by the totals below." is the licensed line, not coverage narration.
#
# The last four alternatives cover one case. A flow that cannot finish inside the scenario TIME
# BUDGET is removed from the suite, and the doctrine's one run-integrity line says so:
#   Run integrity — flow <X> was removed from the suite: it cannot complete inside the
#   40-minute scenario time budget; extending the budget (--scenario-timeout) is your call.
# The label alone would license that sentence — but only while the label and the clause stay in
# ONE sentence, and `_sentences` splits on newline, so a report that renders the label as a
# bullet head ("- **Run integrity**\n  flow X was removed …") loses the exemption on exactly the
# clause that needs it. Keying on the CLAIM as well as the label makes the license a property of
# what the sentence says, not of how the report happened to lay it out.
#
# They match the CLAIM, never the topic. "Removed from the suite" is the one that had to be
# narrowed after the fact: on its own it is a topic, and it would have exempted a removal for a
# CONTENT defect — which is suite-coverage narration and item 3 keeps it out. It now only
# licenses a removal the same sentence attributes to the budget. A bare "time budget" alternative would have exempted any
# sentence that merely mentions one — including "the scenarios do not cover partial refunds
# because of the scenario time budget" (coverage narration, which item 3 forbids) and "I stopped
# the round — we ran out of time budget" (an abort, which `_abort_hit` reads through this same
# regex). Both stay blocked: the possessive in "ran out of THEIR time budget" is what separates
# a statement about scenarios from a statement about the session. All four are run-side by
# construction — they describe what this run could execute, never what the suite chose to cover,
# which is the only thing the gap rule is defending.
_RUN_INTEGRITY_RE = _shared_re("run_integrity")
# Inherently suite-side, whatever the subject: doctrine item 3's own label, and an idiom
# whose subject IS a suite part.
_GAP_ALWAYS_RE = re.compile(
    r"\baccepted gaps?\b"
    r"|\bno (?:scenario|eval|test) (?:covers|exercises|tests|touches)\b",
    re.IGNORECASE,
)
# A SECTION named for the suite's gaps is suite-side by shape — a line-leading label is a
# report structure, not a finding inside a sentence. ("coverage gaps in the agent's refund
# handling", mid-sentence, stays a finding.)
# The LABEL shape is what marks it — the idiom followed by a separator or end of line, not
# an ordinary sentence that happens to start with it ("coverage gaps in the tokenizer's
# Hebrew handling …" on its own line is a finding).
_GAP_HEADING_RE = re.compile(
    r"^[ \t]*(?:[>#*_•\-]+[ \t]*)*(?:\*\*|__|\*|_)?[ \t]*(?:accepted |suite |known )?(?:coverage gaps?|gaps? in coverage)"
    r"(?:\*\*|__|\*|_)?[ \t]*(?=[:\u2014\u2013]|$)",
    re.IGNORECASE | re.MULTILINE,
)
# EVAL-SET FITNESS, which no anchor licenses. Doctrine keeps it loop-internal outright: the
# customer is not made an adjudicator of the eval set's internals, and "I would rather tell you
# than spend a run" is deliberation, not a product statement. The run-integrity licence above is
# for facts about THIS RUN (what executed, what errored, what landed incomplete) — a fitness
# caveat wearing that anchor ("Run integrity — some judges could not be shown to discriminate;
# treat their greens with care.") is the exact phrasing the skill's own prose calls "the one that
# would reach the customer", and it passed. Two halves, both required in one sentence, so an
# ordinary score line ("mean eval score 0.8") is untouched.
_EVAL_FITNESS_SUBJECT_RE = _shared_re("eval_fitness_subject")
_EVAL_FITNESS_CLAIM_RE = _shared_re("eval_fitness_claim")
# How sure the product is of its own result (doctrine item 5) — never hand-off content.
_RESULT_DOUBT_RE = _shared_re("result_doubt")

# Gap idioms that need a suite-side subject in the same sentence to count.
_GAP_SCOPED_RE = re.compile(
    r"\b(?:do(?:es)? not|don'?t|doesn'?t|never|fail(?:s)? to) (?:cover|exercise|test|touch)\b"
    r"|\bnot (?:covered|exercised|tested)\b"
    r"|\buncovered (?:flow|path|surface|area|case|scenario)s?\b"
    r"|\bknown limitations?\b|\bcoverage gaps?\b|\bgaps? in coverage\b",
    re.IGNORECASE,
)


# WHY the guard is not carved out for /generate-evals. That skill
# prescribes a dropped-metric line, has no flow marker of its own, and runs inside
# /simulation-generate-suite's armed window — so its summary IS validated here, and its two
# prescribed phrasings ("Coverage gaps:", "not covered by the generated set") were exactly
# what this rule blocks. The conflict was resolved on the SKILL's side, not here: an
# eval-generation gap is a genuine RESULT the customer must decide about, but there is no
# signal in the text that separates "the generated set does not cover X" (which is textbook
# item-3 narration in a final report) from the same words in an eval-gen summary — the two
# are the same sentence. Carving it out would mean carving out the phrasing itself
# everywhere. The skill now says "couldn't be generated" / "has no matching metric in the
# shipped set", which is more concrete anyway and passes untouched. See
# skills/generate-evals/SKILL.md Step 6.
def _sentences(message: str) -> list[str]:
    """``message`` split on sentence boundaries only (``.``/``;``/newline, not dashes)."""
    breaks = _clause_breaks(message, dashes=False)
    out, start = [], 0
    for b in breaks + [len(message)]:
        if b > start:
            out.append(message[start:b])
        start = b + 1
    return out


# A sentence that OPENS on a missing input ("Without it, …", "Unless a knowledge base is built, …")
# states what a prerequisite decides, not what a delivered suite left out. Licensed ONLY in the scan
# hand-off (``_SCAN_COMMAND``): no suite exists yet there, and the scan skill MANDATES exactly this
# consequence for an agent that needs a knowledge corpus ("Without a corpus the suite contains 0
# retrieval scenarios …"). Its natural paraphrase — "Without it, no scenario tests the agent's answers
# against your guides" — tripped the always-suite idiom, blocked the live scan hand-off and had it
# re-rendered with the mandated sentence dropped (RT37). After the suite exists the same sentence IS
# a coverage gap of the delivered suite, so every other command still blocks it.
_PREREQUISITE_COUNTERFACTUAL_RE = re.compile(r"^[\s>#*_•\-]*(?:without|unless)\b", re.IGNORECASE)
_SCAN_COMMAND = "scan-agent"


def _suite_gap_hit(message: str, command: str = "") -> bool:
    """SUITE-coverage narration: a gap idiom whose subject is OUR suite, not the agent."""
    if _GAP_HEADING_RE.search(message):
        return True
    for sentence in _sentences(message):
        # BEFORE the run-integrity licence, deliberately: eval-set fitness is loop-internal in
        # EVERY phrasing, and the anchor is what a fitness caveat would otherwise wear to get out.
        if _EVAL_FITNESS_SUBJECT_RE.search(sentence) and _EVAL_FITNESS_CLAIM_RE.search(sentence):
            return True
        if _RUN_INTEGRITY_RE.search(sentence):
            continue  # doctrine item 4 — a RESULT, licensed one line
        if command == _SCAN_COMMAND and _PREREQUISITE_COUNTERFACTUAL_RE.match(sentence):
            continue  # the scan's mandated corpus consequence — see _PREREQUISITE_COUNTERFACTUAL_RE
        if _GAP_ALWAYS_RE.search(sentence):
            return True
        if _GAP_SCOPED_RE.search(sentence) and _SUITE_SUBJECT_RE.search(sentence):
            return True
    return False

# --- command profiles -------------------------------------------------------------------

# Anchor strings the skills' report skeletons carry; a message CLAIMING completion must
# include its command's anchor (that is how the skeleton is enforced, not just forbidden
# words). Keys match the marker content written by each skill's Step 0. Read from
# `anchors.json` — see the loader above for why the strings are data and the shape is code.
_ANCHORS: dict[str, tuple[str, ...]] = _command_strings("anchors")

# An anchor counts only in its SKELETON POSITION — first thing on a line (markdown
# decoration allowed: the skeletons are rendered as `> **Anchor** — …`), and followed by
# the skeleton's separator. Substring matching was the bug: /simulation-fixes' whole anchor
# is the bare word ``Findings``, so an ordinary mid-flow line — "Findings so far: 2 of 7
# scenarios failed. Still polling…" — read as a fixes hand-off, re-targeted the profile and
# DELETED a live run window (reproduced). The docstring below promises this fail-open is
# "strictly gated"; position + shape is what makes that true.
_ANCHOR_LEAD = r"^[ \t]*(?:[>#*_•-]+[ \t]*)*"
# The separator is DERIVED, not listed: `[^\w\s]` is every Unicode punctuation and symbol, which
# is the same set the old hand-written class `[:.,;!?—–·()\[\]-]` was reaching for and could only
# reach for in ASCII. A locale writes the skeleton in its own punctuation — `スイート: 検証済み。`,
# `…（シナリオ7件）`, `…、` — and every one of those rendered the anchor VERBATIM and was rejected as
# a BENT anchor, which is the wedge `anchors.json` exists to remove, still armed for exactly the
# deployments that most need it. Hebrew never showed it: it writes ASCII punctuation. What does NOT
# change is that a separator is REQUIRED — the anchor running straight on into prose (a word in
# English, a character in an unspaced script) is not a skeleton line, and `[ \t]*` still may not be
# followed by a word character.
_ANCHOR_TAIL = r"(?:\*\*|__|\*|_)?[ \t]*(?=[^\w\s]|$)"


#: A short bold label a model puts before a stop text (`**Stopped:** <text>`). Only the stop patterns allow it.
_STOP_LABEL = r"(?:(?:\*\*|__)[^\n*_]{1,24}(?:\*\*|__)[ \t]*:?[ \t]*)?"


def _head_pattern(
    anchor: str, tail: str = "", flags: int = 0, *, label: bool = False, escape: Callable[[str], str] = re.escape
) -> re.Pattern[str]:
    """``anchor`` as the first thing on a line (:data:`_ANCHOR_LEAD`, then a short bold label when ``label``),
    followed by ``tail``. ``escape`` turns the literal anchor into its pattern."""
    return re.compile(_ANCHOR_LEAD + (_STOP_LABEL if label else "") + escape(anchor) + tail, re.MULTILINE | flags)


_ANCHOR_RES: dict[str, tuple[re.Pattern[str], ...]] = {
    cmd: tuple(_head_pattern(a, _ANCHOR_TAIL) for a in anchors) for cmd, anchors in _ANCHORS.items()
}

#: The anchors a FAILED flow's skeleton still ends on (`floor_anchors`): below a stop text they are the failure's own floor
#: line, not a finished report that quotes the stop as a row.
_FLOOR_ANCHOR_RES: dict[str, tuple[re.Pattern[str], ...]] = {
    cmd: tuple(_head_pattern(a, _ANCHOR_TAIL) for a in anchors) for cmd, anchors in _command_strings("floor_anchors").items()
}

# The IN-PROGRESS anchor: the honest way to end a turn while the flow's work is
# still executing. The anchor gate below is deliberately not tier-gated, and _COMPLETION_RE
# is a lexical net — so a mid-flow status line about a still-running qualification pass
# ("the suite's 3 scenarios are generated, the run is underway") was blocked demanding a
# hand-off anchor whose true variants were all false. The only compliant moves were a fake
# provenance anchor or a near-empty line (observed live). A progress anchor satisfies the
# gate WITHOUT reading as a hand-off: it never closes the window (it is not in _ANCHORS,
# and handle()'s clean-pass close checks those only), it earns the relaxed mid-flow tier,
# and it must be renderable only while something is actually still running.
_PROGRESS_ANCHORS: dict[str, tuple[str, ...]] = _command_strings("progress_anchors")
_PROGRESS_ANCHOR_RES: dict[str, tuple[re.Pattern[str], ...]] = {
    cmd: tuple(_head_pattern(a, _ANCHOR_TAIL) for a in anchors) for cmd, anchors in _PROGRESS_ANCHORS.items()
}


def _anchor_present(message: str, command: str) -> bool:
    """Does ``message`` carry one of ``command``'s anchors as a skeleton line?"""
    return any(p.search(message) for p in _ANCHOR_RES.get(command, ()))


def _progress_anchor_present(message: str, command: str) -> bool:
    """Does ``message`` carry ``command``'s IN-PROGRESS anchor as a skeleton line?"""
    return any(p.search(message) for p in _PROGRESS_ANCHOR_RES.get(command, ()))


def _without_progress_anchor(message: str, command: str) -> str:
    """``message`` with its IN-PROGRESS anchor LINES removed.

    The progress anchor is itself stem-shaped ('Suite: in progress' matches the 'Suite:'
    stem), so every stem test has to be run against the rest of the message — not waived
    wholesale when the anchor is present. Waiving it wholesale let the anchor cover a bent
    anchor anywhere else in the same message ('Suite: in progress' + 'Suite: built, not yet
    qualified' scored clean), which is the exact evasion the stems exist to catch."""
    patterns = _PROGRESS_ANCHOR_RES.get(command, ())
    if not patterns:
        return message
    return '\n'.join(line for line in message.splitlines() if not any(p.match(line) for p in patterns))


def _driven_substep(message: str, command: str) -> bool:
    """Is this an INNER sub-step's report inside ``command``'s flow?

    True when the message presents the anchor of a command ``command`` DRIVES —
    /simulation-generate-suite's Step 3.1 qualification run renders "**Run complete** …"
    inside the suite's own window. That render is mid-flow for the driver, not its
    hand-off: it must not be held to the driver's anchor (which would block the inner
    step), must not re-target the profile, and must never close the window.
    """
    return any(_anchor_present(message, sub) for sub in drives(command))


def _effective_command(message: str, command: str) -> str:
    """Fail OPEN on a stale marker: validate the contract the message actually presents.

    Observed in production: /simulation-scan-agent left 'scan-agent' in the marker, so a
    later /simulation-run hand-off was validated against SCAN's skeleton and blocked for
    lacking "Environment ready" — and because every skill's cleanup is
    ``grep -qx '<own name>'``-guarded, no later command could clear the foreign name
    either; only the 3-strike stand-down ever closed the window. When the message carries
    another known command's FULL anchor and NONE of the marker's, the marker is the thing
    that is wrong: adopt the presented command's whole profile (anchors, stem, self-flaw
    noun set, suite-doubt exemption, reason text), and handle() then clears the stale marker
    on the clean pass.

    Strictly gated so the fail-open cannot become an opt-out — THREE ways:
    * a message with NO known anchor keeps the MARKER's contract and still owes it an
      anchor, exactly as before;
    * an anchor counts only in its skeleton POSITION/SHAPE (``_anchor_present``), never as
      a substring of running prose;
    * a command the marker DRIVES is never adopted. A driven sub-step's report is not a
      stale marker — /simulation-generate-suite legitimately runs /simulation-run inside
      its own window, and re-targeting there handed the clean-pass close the inner step's
      anchor, tearing the SUITE's window down at its own qualification run (reproduced:
      the rest of the flow, hand-off included, then ran with both guards off). The Step-0
      layer already preserves a driver's marker; the table is shared, not re-spelled here.
    (generate-suite and analyze share anchors, so neither ever reads as stale for the
    other.)
    """
    if _anchor_present(message, command):
        return command
    driven = drives(command)
    for other in _ANCHORS:
        if other != command and other not in driven and _anchor_present(message, other):
            return other
    return command


# The three stop texts the plugin prints verbatim as the last thing a turn says (`stop_message.py`): the contact
# hand-off, the capacity stop and the unavailable stop. A flow that genuinely failed ends with one of them plus the
# relevant ids, and the skeleton anchors are NOT required for it: demanding a product skeleton from a failure report
# would force either a fake success or a wedged turn. The other base checks (cost, jargon, chronology, stalls) still apply.
#
# Each text is recognised by its whole FIRST SENTENCE at a line head, with the decoration a skeleton line allows (`> `,
# `**`, `- `) and nothing else before it, and the sentence must end where the text's does: a status line that opens the
# same way and runs on (the unavailable sentence followed by ", so I'll retry") is narration, not a stop. What follows the first
# sentence is not compared, so a linked email still halts. A sentence of prose that quotes a text is not a stop, and
# neither is a finished report whose scenario row quotes one: a report anchor ABOVE the text makes it a row. The stop's
# own done lines sit below the text, so an outcome that opens like a skeleton line does not unmake the stop.
#: What may follow a stop text's first sentence: closing emphasis, then whitespace or the line's end.
_CLOSING_EMPHASIS = r"(?:\*\*|__|\*|_)?"
_STOP_TAIL = _CLOSING_EMPHASIS + r"(?=\s|$)"
#: The full stops of a script that puts no space after them: the text's next sentence follows at once, so after one of
#: these a word character may follow too. A run-on (more punctuation, a comma) is still narration, as in a spaced script.
_UNSPACED_FULL_STOPS = "。．！？"
_STOP_TAIL_UNSPACED = _CLOSING_EMPHASIS + r"(?=\s|$|[^\W_])"
#: The apostrophes a stop text may be retyped with: a model, or the chat's rendering, turns `'` into `’`.
_APOSTROPHES = "'\u2019"


def _stop_escape(anchor: str) -> str:
    """``re.escape`` of ``anchor``, with each apostrophe matching either form (:data:`_APOSTROPHES`)."""
    return "".join(f"[{_APOSTROPHES}]" if char in _APOSTROPHES else re.escape(char) for char in anchor)


def _stop_pattern(anchor: str) -> re.Pattern[str]:
    """``anchor``, a stop text's first sentence, as a line of its own (:func:`_head_pattern`) that ends where it does."""
    tail = _STOP_TAIL_UNSPACED if anchor[-1:] in _UNSPACED_FULL_STOPS else _STOP_TAIL
    return _head_pattern(anchor, tail, re.IGNORECASE, label=True, escape=_stop_escape)


#: Which stop each text is, checked in this order. The unavailable stop records the liftable `capacity` halt.
_STOP_TEXT_RES: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = tuple(
    (kind, tuple(_stop_pattern(a) for a in anchors))
    for kind, anchors in (
        (_run_driver.STOP_CAPACITY, (*_shared_strings("capacity_anchor"), *_shared_strings("unavailable_anchor"))),
        (_run_driver.STOP_CONTACT, _shared_strings("contact_anchor")),
    )
)
#: The credit anchor is the continue sentence every credit kind but `credit-unavailable` ends on. Substring, case-insensitive.
_CREDIT_ANCHORS = _shared_strings("credit_anchor")


def _scripts_on_path() -> None:
    """Put the plugin's ``scripts/`` (a stdlib-only sibling tree) on ``sys.path`` once, for the first reader that needs it."""
    scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


@functools.cache
def _done_head() -> str:
    """``stop_message.DONE_HEAD``, imported once: scripts/ joins ``sys.path`` only when a stop text is first read."""
    _scripts_on_path()
    from stop_message import DONE_HEAD  # noqa: PLC0415

    return DONE_HEAD


def _without_done_block(message: str) -> str:
    """``message`` without the stop's own done lines (``stop_message.DONE_HEAD`` and the bullets under it). An outcome
    that opens like a skeleton line is not a report anchor."""
    done_head = _done_head()
    kept: list[str] = []
    in_block = False
    for line in message.splitlines():
        if line.strip().lstrip(">").strip() == done_head:
            in_block = True
        elif in_block and re.match(r"[ \t>]*[-*+][ \t]", line):
            pass
        else:
            in_block = False
            kept.append(line)
    return "\n".join(kept)


def _without_floor_lines(text: str, commands: tuple[str, ...]) -> str:
    """``text`` without the lines that are a floor anchor of one of ``commands`` (``floor_anchors`` in `anchors.json`)."""
    patterns = [p for c in commands for p in _FLOOR_ANCHOR_RES.get(c, ())]
    if not patterns:
        return text
    return "\n".join(line for line in text.splitlines() if not any(p.match(line) for p in patterns))


def _is_a_row_of_a_report(message: str, start: int, command: str | None) -> bool:
    """Is the stop text that heads at offset ``start`` of ``message`` a row of a report, not a stop? True when
    ``message`` carries, as a skeleton line, a report anchor of ``command`` (the one the message presents, see
    :func:`_effective_command`) or of a command it drives, above the text or below it; with no command (no flow window),
    any command's anchor. Not counted: the stop's own done lines, and below the text a floor anchor, the line a failed
    flow's skeleton ends on."""
    if command is None:
        commands: tuple[str, ...] = tuple(_ANCHORS)
    else:
        command = _effective_command(message, command)
        commands = (command, *drives(command))
    below = _without_floor_lines(_without_done_block(message[start:]), commands)
    trimmed = f"{message[:start]}\n{below}"
    return any(_anchor_present(trimmed, c) for c in commands)


def _stop_text_found(message: str, command: str | None = None) -> tuple[str, int] | None:
    """``(kind, offset of the text's head)`` of the stop this message is, or None. A stop text that is a row of a report
    (:func:`_is_a_row_of_a_report`) is no stop. The credit anchor is a substring and never a row: its offset is the
    message's start."""
    for kind, patterns in _STOP_TEXT_RES:
        for pattern in patterns:
            found = pattern.search(message)
            if found and not _is_a_row_of_a_report(message, found.start(), command):
                return kind, found.start()
    lowered = message.lower()
    if any(anchor.lower() in lowered for anchor in _CREDIT_ANCHORS):
        return _run_driver.STOP_CREDIT, 0
    return None


def _recorded_halt_kind(bases: list[Path]) -> str | None:
    """The stop kind the session's open /simulate plan is halted with, or None (no plan, no halt, a ``proof`` or
    ``untyped`` halt, or a plan this guard cannot read)."""
    try:
        import simulate_continue  # noqa: PLC0415 - hooks/ is on sys.path at call time

        plan = _run_driver.read_plan(_run_driver.find_plan(bases))
        if plan is None or plan.get("finished") is True:
            return None
        kind = simulate_continue.halt_kind(plan)
    except Exception:  # noqa: BLE001 - an unreadable plan recorded no kind
        return None
    return kind if kind in (_run_driver.STOP_CAPACITY, _run_driver.STOP_CREDIT, _run_driver.STOP_CONTACT) else None


def _run_stop_found(message: str, command: str | None, bases: list[Path]) -> tuple[str, int] | None:
    """:func:`_stop_text_found` for the run: its kind is the plan's recorded halt kind when the plan holds one. The
    recorded kind wins because the script that printed the stop knew its cause and the text may not say it:
    `credit-unavailable` prints the contact text and records the liftable ``capacity`` halt. A halted plan takes no second
    halt, so the recorded kind is also the one every later reader of the plan acts on."""
    found = _stop_text_found(message, command)
    return None if found is None else (_recorded_halt_kind(bases) or found[0], found[1])


def _run_stop_kind(message: str, command: str | None, bases: list[Path]) -> str | None:
    """The kind of the stop ``message`` is, for the run (:func:`_run_stop_found`), or None when it is no stop."""
    found = _run_stop_found(message, command, bases)
    return found[0] if found else None


def _record_headless_halt(message: str, command: str | None, bases: list[Path]) -> None:
    """Record the stop ``message`` is for the headless driver (``_run_driver.record_halt``), of the kind
    :func:`_run_stop_found` gives, with the text from the stop's head on, so what the model wrote before it stays out. A
    credit stop is a substring match with no head, so its record is the whole message. A message that is no stop records
    nothing. Fail-open: recording never breaks the guard."""
    found = _run_stop_found(message, command, bases)
    if found is None:
        return
    kind, start = found
    try:
        _run_driver.record_halt(kind, message[start:].strip())
    except Exception:  # noqa: BLE001 - the recording is best-effort
        pass


def _stop_text_kind(message: str, command: str | None = None) -> str | None:
    """Which stop this message's text is (``capacity``, ``contact``, ``credit``), in ANY locale, or None.

    Checked in the order capacity, contact, credit, so the contact text reads as ``contact`` even when a
    `credit-unavailable` refusal printed it: a reader that has the run's plan takes its recorded kind instead
    (:func:`_run_stop_kind`). ``command`` is the flow window's command, None when none is armed."""
    found = _stop_text_found(message, command)
    return found[0] if found else None


def _is_stop_text(message: str, command: str | None = None) -> bool:
    """Does this message carry ANY locale's stop (:func:`_stop_text_kind`)?"""
    return _stop_text_kind(message, command) is not None


def _printed_stop(event: Event, bases: list[Path]) -> list[str] | None:
    """The ``stop_message.py`` arguments of a stop the model printed in this run and no typed message has lifted, or None.

    The script halts the /simulate plan as it prints (``simulate_continue.record_printed_stop``), so the turn that prints a
    stop and then words its own final message still stops. A message a person typed after the halt (the plan's last write)
    gives that turn to them, and a capacity or credit halt it lifted is no halt at all. Fail-open."""
    try:
        import simulate_continue  # noqa: PLC0415 - hooks/ is on sys.path at call time

        path = _run_driver.find_plan(bases)
        plan = _run_driver.read_plan(path)
        if path is None or plan is None or plan.get("finished") is True:
            return None
        args = simulate_continue.printed_stop_args(plan)
        if args is None or user_spoke_since(event, path):
            return None
        return args
    except Exception:  # noqa: BLE001 - a plan this guard cannot read recorded no stop
        return None


def _stop_command(args: list[str]) -> str:
    """The exact command that printed a stop, as the text a hook gives the model spells it."""
    import shlex  # noqa: PLC0415 - only a recorded stop pays for it

    from plugin_root import shell_path  # noqa: PLC0415 - hooks/ is on sys.path at call time

    def word(arg: str) -> str:
        # The posture spells a plain argument with double quotes (`--done "<outcome>"`); a value that holds a quote, `$`,
        # a backtick, a backslash or `!` (a refusal's JSON) goes inside single quotes instead.
        if re.fullmatch(r"[\w./:=@%+-]+", arg):
            return arg
        return f'"{arg}"' if not re.search(r'["$`\\!\n]', arg) else shlex.quote(arg)

    script = Path(__file__).resolve().parent.parent / "scripts" / "stop_message.py"
    return f"python3 {shell_path(script)} {' '.join(word(a) for a in args)}"


_UNSAID_STOP_VIOLATION = "a stop the final message does not say"


def _unsaid_stop(args: list[str]) -> _Violation:
    return _Violation(
        f"{_UNSAID_STOP_VIOLATION} (the run is stopped and the stop text is the whole message)",
        f"the run is stopped: say what `{_stop_command(args)}` prints, verbatim and alone, as your whole message.",
    )


_BUNDLE_END = "--- end of bundle ---"
_FENCE_LINE = re.compile(r"^[ \t]*(?:`{3,}|~{3,})")
#: Lines the diagnose hand-off may carry after the bundle's code block: the pointer to Plurai support, when it
#: follows the block instead of leading it.
_DIAGNOSE_TRAILING_LINES = 1

_DIAGNOSE_REASON = (
    "/plurai-diagnose ends at the bundle's code block: the message is one line pointing to Plurai support, then "
    "the bundle in one code block, and nothing after the closing fence. Re-send it with everything after the block "
    "removed: no explanation of the references, no advice."
)


@functools.cache
def _diagnose_pointer() -> str:
    """What the pointer line has to say, compared case-insensitively: ``stop_message.CONTACT_EMAIL``, imported once like
    ``_done_head``, so the address keeps its one home."""
    _scripts_on_path()
    from stop_message import CONTACT_EMAIL  # noqa: PLC0415

    return CONTACT_EMAIL.lower()


def _diagnose_pointer_reason() -> str:
    return (
        "/plurai-diagnose tells the user where the bundle goes: the message carries one line, before or after the code "
        f"block, that says to send the output to {_diagnose_pointer()} or the Plurai Discord. "
        "Re-send it with that line and the bundle in one code block, and nothing else."
    )


def _diagnose_hand_off(message: str) -> tuple[list[str], list[str]] | None:
    """``(lines before the code block, lines after it)`` of a /plurai-diagnose hand-off, or None when ``message`` is not one.

    The hand-off is recognised by the bundle's own last line (``plurai_diagnose.py`` prints it), so the checks
    need no flow window: the command is a single print, not a flow. A message without that line, or whose bundle
    line is not followed by a closing fence, is not a hand-off and is never judged here.
    """
    lines = message.splitlines()
    end = next((i for i, line in enumerate(lines) if line.strip() == _BUNDLE_END), None)
    if end is None:
        return None
    fence = next((i for i in range(end + 1, len(lines)) if _FENCE_LINE.match(lines[i])), None)
    if fence is None:
        return None
    opening = next((i for i in range(end - 1, -1, -1) if _FENCE_LINE.match(lines[i])), end)
    return lines[:opening], lines[fence + 1 :]


def _diagnose_trailing_text(message: str) -> bool:
    """Does a /plurai-diagnose hand-off carry text after its code block beyond the pointer line?"""
    hand_off = _diagnose_hand_off(message)
    return hand_off is not None and sum(1 for line in hand_off[1] if line.strip()) > _DIAGNOSE_TRAILING_LINES


def _diagnose_pointer_missing(message: str) -> bool:
    """Does a /plurai-diagnose hand-off leave out the line that sends the user to Plurai support?"""
    hand_off = _diagnose_hand_off(message)
    return hand_off is not None and _diagnose_pointer() not in "\n".join([*hand_off[0], *hand_off[1]]).lower()


# A message "claims completion" when it reads as the command's final hand-off — only then
# is the anchor REQUIRED (mid-flow progress lines and side-answers to user interjections
# pass untouched).
_COMPLETION_RE = _shared_re("completion")

# Anchor STEMS: a message that starts an anchor-shaped claim ("Suite: built, not yet
# qualified") without matching an EXACT anchor is a bent skeleton — the live evasion
# that sailed through the verb list. Stem present + no exact anchor = block.
#
# Line-anchored for the same reason the anchors are (above): a stem hit also forfeits the
# relaxed mid-flow tier, so a stem loose enough to fire on running prose makes honest
# progress lines unsayable. 'fixes' additionally needs the separator — its stem is the
# single word "Findings", and without it "Findings so far: …" read as a bent anchor. (A
# one-word anchor has no bendable variant anyway: any line-leading "Findings" + separator
# IS the exact anchor, so this stem can only ever mark the message hand-off-shaped.)
def _stems() -> dict[str, re.Pattern[str]]:
    """`command -> its stem`, unioned across locales and always line-anchored.

    Per-locale case sensitivity rides an INLINE `(?i:…)` group rather than a compile flag,
    because one pattern now carries several locales' branches and a compile flag would apply
    another locale's answer to all of them.
    """
    out: dict[str, re.Pattern[str]] = {}
    for command, block in (_ANCHOR_DATA.get("commands") or {}).items():
        branches = [
            (f"(?i:{src})" if entry.get("stem_ignorecase") else f"(?:{src})")
            for entry in _locales(block)
            if isinstance(src := entry.get("stem"), str) and src
        ]
        if branches:
            out[str(command)] = re.compile(_ANCHOR_LEAD + "(?:" + "|".join(branches) + ")", re.MULTILINE)
    return out


_ANCHOR_STEMS: dict[str, re.Pattern[str]] = _stems()

# Unilateral mid-flow abort — the third evasion shape (after tool-asks and prose
# stalls): no question, no anchor, the model just declares it stopped early
# (observed live: "I stopped before the qualification run … the reason is timing,
# not a blocker"). Timing/context/budget is never a stop reason; only a genuine
# blocker (sanctioned escalation) or the landing rule ends a flow early.
_ABORT_RE = _shared_re("abort")
# The part of the abort lexicon that names a timing/context/budget REASON. On a failure hand-off
# it is the only part checked: that hand-off IS the sanctioned blocker escalation the rule exempts,
# and its account of the failed job ("that build stopped before it could finish") is the failure
# itself, not a stop the model chose. Blocking it re-rendered the failure (RT37). A "failure" that
# gives timing, context or budget as its reason is still an abort.
_ABORT_REASON_RE = _shared_re("abort_reason")
#: THE QUALITY LOOP IS INTERNAL (product rule, RT37). While the suite calibrates the simulation — its
#: qualification run, the reviewer, the repair round — the in-progress line names the work only as
#: calibrating the simulation: never the runs, their conversations or scores, a reviewer, or a fix.
_QUALITY_LOOP_RE = _shared_re("quality_loop")
_QUALITY_LOOP_COMMANDS = ("generate-suite", "analyze")


def _abort_hit(message: str, *, failure: bool = False) -> bool:
    """An early stop the model chose — NOT doctrine item 4's licensed result statement.

    Reproduced: "Run integrity — regeneration stopped short of the target count; 5 of 7
    shipped." matched ``stopped short of`` and was blocked with "a unilateral early stop …
    resume the flow", i.e. the guard told the model to resume over a line the doctrine
    explicitly permits (and ``/analyze-simulation`` step 5 explicitly prescribes, after two
    consecutive short regenerations). Same for "I did not run the remaining 7 scenarios".

    The separator is item 4's own vocabulary, per SENTENCE — a run-integrity RESULT is
    exempt, everything else still blocks. "I stopped before the qualification run — the
    reason is timing, not a blocker" carries none of it and is untouched.

    ``failure`` (a failure hand-off): only a timing/context/budget reason counts — see
    ``_ABORT_REASON_RE``.
    """
    pattern = _ABORT_REASON_RE if failure else _ABORT_RE
    for sentence in _sentences(message):
        if _RUN_INTEGRITY_RE.search(sentence):
            continue
        if pattern.search(sentence):
            return True
    return False


# A message EARNS the relaxed tier by looking like live progress. Requiring a positive
# signal is the whole point: keying the relaxation off the ABSENCE of a
# hand-off signal instead handed the model a silent opt-out — a full defect-narrating
# report with no anchor and no completion verb read as "mid-flow", skipped every style
# rule AND the anchor requirement (which is self-gated on those same two signals), and
# left the window armed because no anchor ever closed it. Same evasion class as the BENT
# anchor above, one level down: don't bend the anchor, omit it.
_PROGRESS_RE = _shared_re("progress")


def _handoff_shaped(message: str, command: str) -> bool:
    """Does this message read as the command's FINAL hand-off (vs mid-flow progress)?

    The stem is tested against the message WITHOUT its in-progress anchor line: that line is
    a declaration of the opposite, so counting it would make rendering the honest mid-flow
    line forfeit the mid-flow tier. Everything else is unchanged — in particular
    ``_COMPLETION_RE`` still reads the FULL message, so a message that declares progress and
    then claims completion is a hand-off, and gets the full style profile."""
    stem = _ANCHOR_STEMS.get(command)
    stem_hit = stem is not None and stem.search(_without_progress_anchor(message, command)) is not None
    return bool(stem_hit or _COMPLETION_RE.search(message) or _is_stop_text(message, command))


def _progress_shaped(message: str, command: str) -> bool:
    """Does this message EARN the relaxed (mid-flow) tier?

    Two-tier profile: the full style profile applied to EVERY turn-end while a window was
    armed would leave a multi-turn polling flow no honest voice — 'still
    polling, phase evolving' tripped the jargon rule and 'the backend is down' had to be
    dressed as a failure hand-off. The relaxed tier fixes that, but it must be EARNED by
    a positive progress signal and forfeited the moment the message also looks like a
    hand-off; everything else keeps the full profile. The always-on rules (spend,
    question-stalls, unilateral aborts) apply to every tier.

    The IN-PROGRESS anchor deliberately does NOT earn this tier. Letting a
    DECLARED signal grant it made the anchor a general escape hatch: a full hand-off
    carrying build chronology, suite-self-flaw and coverage narration dropped from four
    violations to zero merely by leading with 'Suite: in progress'. The anchor answers one
    question — "does this turn owe the hand-off skeleton" — and buys nothing else. What a
    message may SAY is judged the same either way."""
    return _PROGRESS_RE.search(message) is not None and not _handoff_shaped(message, command)


#: Markdown decoration a report may legitimately wrap the line in. Stripped from BOTH sides
#: before the equality test, so the contract is the WORDS: a report that bolds the label or
#: renders the line as a bullet is still carrying the sentence the runner printed, and a guard
#: that blocked it would be failing correct hand-offs on layout — this file's oldest mistake.
_DECORATION_RE = re.compile(r"[*_`]+")
#: Dash forms that mean the same thing to a reader and different things to `==`. The enforced
#: sentence opens `Run integrity — …`, and an em dash retyped as a hyphen is the single most common
#: copy mangling there is: without this the model is blocked for "missing" a line it believes it
#: pasted, and the violation text gives it no way to see the difference.
_DASH_RE = re.compile(r"[\u2014\u2013]")
_LINE_LEAD_RE = re.compile(r"^[ \t]*(?:[>#]+|[-+•])[ \t]*", re.MULTILINE)


def _normalised(text: str) -> str:
    """The words of a line, free of wrapping and markdown, for this guard's ONE equality test."""
    # A typographic apostrophe is the same word ("Don’t ship" pasted as "Don't ship").
    return " ".join(_DASH_RE.sub("-", _DECORATION_RE.sub("", _LINE_LEAD_RE.sub(" ", text))).replace("’", "'").split())


def _integrity_violation(message: str, command: str, integrity_line: str | None) -> str | None:
    """The scan's run-integrity line, required VERBATIM, or None.

    This is the FIRST of the two rules in this file that test for a literal rather than for a
    shape (`_anchor_violation` below is the other), and that is the point. Every other rule
    guesses at intent from prose and carries the false-positive history to prove it (see the
    suite-gap rule's three rewrites above); this one compares against
    a sentence `bench_manifest.py` PRINTED from `artifacts/verification.json`, so a correct
    hand-off cannot fail it and an invented one cannot pass. The doctrine already required the
    line (item 4: what was NOT executed is a RESULT the reader is owed); what was missing was
    anything that noticed its absence — a scan reporting "Environment ready" over three checks
    that never ran was, until now, indistinguishable from one where everything ran.

    Fires only when the runner actually produced a line: a bench with no `verification.json`
    predates the manifest, and this rule must not block it.
    """
    if command != "scan-agent" or not integrity_line:
        return None
    if _normalised(integrity_line) in _normalised(message):
        return None
    return (
        "the hand-off is missing its run-integrity line. The scan's verification recorded "
        f"exactly this sentence and it must appear VERBATIM, on its own line: {integrity_line!r} "
        "(print it with `bench_manifest.py integrity-line`; never re-word it, and never write one "
        "from memory — it names the checks this scan did not execute)"
    )


def _artifact_bases(bases: list) -> list[Path]:
    """Every directory a recorded artifact might sit under, in the order the readers below try them.

    A resolver that will not answer contributes nothing rather than raising: every caller
    fail-opens, and an empty candidate list is already their "no rule".
    """
    candidates: list[Path] = []
    for base in bases or []:
        candidates += [Path(base), Path(base) / "plurai_sim"]
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from _workdir import resolve_workdir  # noqa: PLC0415 - hooks/ is only on sys.path here

        resolved = resolve_workdir()
    except Exception:
        resolved = None
    if resolved is not None:
        candidates.append(resolved)
    return candidates


def _recorded_integrity_line(bases: list) -> str | None:
    """The line `bench_manifest.py verify` recorded for this bench, or None.

    Fail-open at every step, like the rest of this hook: a bench that predates the manifest, an
    unreadable file, or a workdir that will not resolve all mean "no rule", never "block". The
    flow marker's own directory is tried first (it is where the window was armed), then the
    resolver's answer — a session-armed window lives beside the session, not beside the bench.
    """
    for candidate in _artifact_bases(bases):
        try:
            payload = json.loads((candidate / "artifacts" / "verification.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        # BOUND TO THIS BENCH'S MANIFEST. `verification.json` is committed and staged, so without
        # this a teammate's clone — or a manifest edited after the last verify — carries a record
        # of a run that never happened here, and the rule would compel the model to paste that
        # stale sentence into a fresh hand-off. That is the same "a report describing a run that
        # did not happen" this whole mechanism exists to end, re-entering through its enforcement.
        # No match means NO RULE, which is the fail-open this function already promises.
        try:
            live = hashlib.sha256((candidate / "bench_manifest.json").read_bytes()).hexdigest()
        except OSError:
            continue
        if payload.get("manifest_hash") != live:
            continue
        line = payload.get("integrity_line")
        if isinstance(line, str) and line.strip():
            return line.strip()
    return None


def _anchor_violation(message: str, command: str, anchor_line: str | None) -> str | None:
    """The suite hand-off's COMPUTED anchor, required verbatim, or None.

    The second literal rule in this file, and for the same reason as the first: the rung was
    model-authored — nothing computed it and this guard checked only that SOME anchor sat at the
    head of a line — so `Suite: qualified` was written both for a gated set that landed with
    accepted gaps and for a fresh set no critic pass had judged. Two different claims, one
    sentence, on two real hand-offs the same day.

    `lane_pin.py anchor` now derives the one licensed line from the pin's `set_lineage` — with the
    declared staleness as the fallback for pins written before that field — and the final coverage
    audit, and records it. Comparing against a line a SCRIPT printed is what makes this checkable:
    a correct hand-off cannot fail it, and an invented rung cannot pass.

    Fires only when the verb actually ran: a workdir with no `suite_anchor.json` predates this and
    must not be blocked.

    GENERATE-SUITE ONLY, because that is the one recipe that computes a record. `/analyze-simulation`
    still chooses its own provenance line and has no `anchor` step, so holding it to this rule meant
    holding it to whatever record an EARLIER suite build happened to leave in the same workdir —
    blocking a correct analyze hand-off and compelling a rung analyze's own ladder does not define.
    A guard for analyze has to wait for analyze to compute one.
    """
    if command != "generate-suite" or not anchor_line:
        return None
    if _normalised(anchor_line) in _normalised(message):
        return None
    return (
        "the hand-off does not carry the anchor this suite's state licenses. `lane_pin.py anchor` "
        f"computed it from the pin and the final coverage audit, and it must appear VERBATIM: {anchor_line!r} "
        "— re-run `lane_pin.py anchor` if the audit has moved since, and use what it prints rather than "
        "choosing a rung."
    )


def _agent_line_violation(message: str, command: str, agent_head: str | None) -> str | None:
    """The Agent line's first sentence (its verdict and score), required verbatim beside the rung, or None.

    Same gate and same literal test as `_anchor_violation`: `lane_pin.py anchor` printed the line, so a hand-off that
    drops the agent's verdict is checkable. Only the head is required — the rest is long and a re-bulleted clause must
    not fail a correct hand-off. None when nothing is recorded (no run, an unaudited final run, or a record no longer
    bound to its run summary: `_recorded_anchor`)."""
    if command != "generate-suite" or not agent_head:
        return None
    if _normalised(agent_head) in _normalised(message):
        return None
    return (
        f"{_AGENT_LINE_VIOLATION} `lane_pin.py anchor` printed beside the rung; its first sentence must appear "
        f"VERBATIM: {agent_head!r} — paste the Agent line it printed, right after the Suite line."
    )


def _agent_head(line: str) -> str:
    """The Agent line's first sentence: up to the first full stop followed by a space (`63.7` is not one)."""
    return re.split(r"(?<=\.)\s", line.strip(), maxsplit=1)[0]


def _unscored_quotes(bases: list, message: str) -> list[str]:
    """The hand-off's quotes found only in a conversation the scored final run did not score (`scored_findings.py`).

    Fail-open like the readers around it: no scored final run, no quote, or an unreadable workdir is "no rule".
    """
    if not any(q in message for q in ('"', "“", "「")):
        return []
    try:
        _scripts_on_path()
        import scored_findings  # noqa: PLC0415 - plugin scripts/, a stdlib-only sibling tree

        for candidate in _artifact_bases(bases):
            if (candidate / "artifacts" / "runs").is_dir():
                return scored_findings.unscored_quotes(candidate, message)
    except Exception as exc:  # noqa: BLE001 - a check that cannot read the runs checks nothing, never wedges the turn
        try:
            from _hookfail import note

            note("report_guard (scored_findings)", exc)
        except Exception:  # noqa: BLE001 - the loud-failure helper must never BE the failure
            pass
    return []


def _recorded_anchor(bases: list) -> tuple[str | None, str | None]:
    """`(the anchor `lane_pin.py anchor` computed for this workdir, the first sentence of its Agent line)`, each None
    when there is none to require.

    Fail-open at every step, like `_recorded_integrity_line` above and for the same reasons: a
    workdir that predates the verb, an unreadable file, or a resolver that will not answer all mean
    "no rule", never "block". The Agent line is bound to the `run_summary.json` it was read off too
    (`agent_summary_sha256`): a re-run into the same run rescores it, and a moved summary drops that
    rule while the rung rule stands.
    """
    for candidate in _artifact_bases(bases):
        try:
            payload = json.loads((candidate / "artifacts" / "suite_anchor.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        # BOUND TO THE AUDIT IT WAS COMPUTED FROM, exactly as `_recorded_integrity_line` binds to
        # its manifest. Without this the record outlives its input and the rule compels a stale
        # rung. A record that cannot be bound is NO RULE — the fail-open already promised here.
        #
        # TWO WAYS THE INPUT MOVES, and the digest alone only sees the first. It is REWRITTEN (the
        # digest stops matching), or it is SUPERSEDED — an additive round lands
        # `…_final2.json` beside the untouched `…_final.json`, so the digest matches forever while
        # the audit the rung was computed from is not the one that governs. The second is the
        # common case: the recipe calls for a re-audit on every branch of the triage.
        recorded_digest = payload.get("audit_sha256")
        audit_path = payload.get("audit")
        if recorded_digest is not None:
            if not isinstance(audit_path, str) or not audit_path:
                continue
            # The record's path is a file a command can write: never followed off this machine's disks.
            if not _platform.is_local_drive_path(audit_path, within=(candidate,)):
                continue
            try:
                live = hashlib.sha256(Path(audit_path).read_bytes()).hexdigest()
            except OSError:
                continue
            if live != recorded_digest:
                continue
        recorded_names = payload.get("audit_names")
        if isinstance(recorded_names, list):
            on_disk = sorted(p.name for p in (candidate / "artifacts").glob("coverage_audit_final*.json"))
            if on_disk != sorted(str(n) for n in recorded_names):
                continue
        line = payload.get("anchor")
        if isinstance(line, str) and line.strip():
            return line.strip(), _bound_agent_head(payload, candidate)
    return None, None


def _bound_agent_head(payload: dict, candidate: Path) -> str | None:
    """The recorded Agent line's first sentence while the run summary it was read off is unchanged, else None."""
    line, summary, digest = payload.get("agent_line"), payload.get("agent_summary"), payload.get("agent_summary_sha256")
    if not (isinstance(line, str) and line.strip() and isinstance(summary, str) and summary and isinstance(digest, str)):
        return None
    if not _platform.is_local_drive_path(summary, within=(candidate,)):
        return None
    try:
        if hashlib.sha256(Path(summary).read_bytes()).hexdigest() != digest:
            return None
    except OSError:
        return None
    return _agent_head(line)


class _Violation(str):
    """A violation's full description, and the one short line the correction says in its place.

    It is a ``str``, so every rule that reads a violation's opening words sees the description. ``fix`` is what the
    model is told: the correction is shown to the user, so it names what to change in one line and leaves the rules
    to the skill's report section, which carries them.
    """

    fix: str

    def __new__(cls, text: str, fix: str) -> _Violation:
        found = super().__new__(cls, text)
        found.fix = fix
        return found


def _fix_of(violation: str) -> str:
    """The short line for ``violation``: its own ``fix``, or its opening words for a violation built elsewhere."""
    return getattr(violation, "fix", "") or violation.split(" (", 1)[0]


_HOLD_VIOLATION = "a delivery over an unanswered PREREQUISITE"  # _reason keys its frame on it
_STALL_VIOLATION = "a question-shaped stall"
_ABORT_VIOLATION = "a unilateral early stop"
_STRANDED_VIOLATION = "a turn that ends mid-flow with nothing to resume it"
#: A Stop whose final message the harness did not send and the transcript could not supply. Nothing is
#: known about the turn, so it is neither a report nor a stall; it is blocked so the model writes the
#: message again, and it spends a strike like any other block, so a harness whose transcript is never
#: readable stands the guard down after three.
_UNREADABLE_VIOLATION = "a final message the guard could not read"
_VOCABULARY_VIOLATION = "our own machinery on a customer surface"


#: The vocabulary term that keeps a stop's cause out of the chat. Its words (capacity, gateway, upstream) are also the
#: customer's own, so it applies to a message that is a stop or that carries no report anchor (a paraphrased stop has
#: none), and never to a finished report.
_STOP_CAUSE_TERM = "stop_cause_words"


def _customer_vocabulary(message: str, command: str | None = None) -> list[_Violation]:
    """The customer-text vocabulary (`scripts/customer_text_guard.py`, kind `final_chat`) over a hand-off: the final chat
    is a customer artifact like the files it links, so one list of internal terms answers both. ``command`` is the flow
    window's command, which decides whether the stop-cause term applies. Fail-open, loudly."""
    try:
        _scripts_on_path()
        import customer_text_guard  # noqa: PLC0415 - plugin scripts/, a stdlib-only sibling tree

        found = customer_text_guard.text_violations("final_chat", message)
    except Exception as exc:  # noqa: BLE001 - an unloadable vocabulary checks nothing, never wedges the turn
        try:
            from _hookfail import note

            note("report_guard (customer_text_guard)", exc)
        except Exception:  # noqa: BLE001 - the loud-failure helper must never BE the failure
            pass
        return []
    reported = command is not None and any(_anchor_present(message, c) for c in (command, *drives(command)))
    cause_applies = _is_stop_text(message, command) or not reported
    found = [v for v in found if v.term != _STOP_CAUSE_TERM or cause_applies]
    return [
        _Violation(
            f'{_VOCABULARY_VIOLATION} [{v.term}/{v.cls}] "{v.excerpt}" — {v.say_instead}',
            f'replace "{v.excerpt}" with plain product words: {v.say_instead}',
        )
        for v in found
    ]
#: The rules that judge whether a turn may END here, as opposed to what its message says. All three
#: push "continue the flow NOW", which is the wrong instruction for a turn that answers the USER: a
#: user who said stop, or asked a side question, is owed the answer and the floor, not the next step.
#: handle() drops exactly these for such a turn (``user_spoke_since``); every content rule, the
#: computed-anchor and integrity bindings and the prerequisite hold still apply to it.
_TURN_END_VIOLATIONS = (_STALL_VIOLATION, _ABORT_VIOLATION, _STRANDED_VIOLATION)
_COMPLETION_VIOLATION = "a completion claim without the report skeleton"
#: What gives way when the account's usage limit ended the turn (``_usage_limited``): the harness told the model to
#: stop and say what is done and what is left, so the turn-end pushes and the completion-claim rule (that summary IS
#: a completion claim) would order what the harness forbids. Every other content rule still applies.
_LIMIT_STANDDOWN = (*_TURN_END_VIOLATIONS, _COMPLETION_VIOLATION)
# A row's text is the HARNESS's, not the user's, when it is nothing but tag-wrapped blocks: a slash
# command (`<command-name>…</command-name>`), a background task's completion
# (`<task-notification>…`), a local command's output, an IDE context block or a system reminder.
_TAGGED_BLOCK_RE = re.compile(r"<([A-Za-z][\w-]*)\b[^>]*>.*?</\1\s*>", re.DOTALL)


#: Harness text that reaches a reader as an untagged prompt, matched at its start: the reminder a harness writes after a
#: long silent stretch ("The user hasn't heard from you in a while — say …"). Claude Code's transcript records it as an
#: `attachment` row, which no reader takes for a prompt; a harness that hands it over as plain prompt text is refused here.
_HARNESS_TEXT_RE = re.compile(r"the user (?:hasn['\u2019]t|has not) heard from you in a while\b", re.IGNORECASE)


def _human_typed(text: str) -> bool:
    """Did a person type this prompt? Never a harness injection, a heartbeat, or a flow's own arming.

    The reader has already refused what the harness or this plugin wrote (``Prompt.injected``); what
    is left is judged by shape: tag-wrapped blocks (a slash command, a task's completion), the
    heartbeat sentinel, known untagged harness text (:data:`_HARNESS_TEXT_RE`) and a flow command
    typed as plain text are not an interjection.
    """
    from flow_arm import typed_text  # noqa: PLC0415 - hooks/ is on sys.path at call time

    words = _TAGGED_BLOCK_RE.sub("", typed_text(text)).strip()
    if not words:
        return False
    try:
        from flow_heartbeat import SENTINEL  # noqa: PLC0415 - hooks/ is on sys.path at call time
    except Exception:  # noqa: BLE001 - no sentinel to tell apart: refuse the stand-down, never guess
        return False
    if words.startswith(SENTINEL) or _HARNESS_TEXT_RE.match(words):
        return False
    # A flow command typed as plain text opens a window; it does not answer one.
    return _FLOW_INVOCATION_RE.match(words) is None


#: The public name of :func:`_human_typed`, for ``simulate_continue``'s record of the user's messages.
human_typed = _human_typed


#: How far apart a prompt and the record of its being taken for a gate's answer can be: the record is
#: written by a hook that runs moments after the prompt, and the transcript's clock is the harness's own.
_ANSWER_SKEW_BEFORE_SECONDS = 5
_ANSWER_SKEW_AFTER_SECONDS = 120


def gate_answered_near(bases: list[Path], epoch: float) -> bool:
    """Was the prompt written at ``epoch`` recorded as the answer to a gate, in the findings under ``bases``?

    PUBLIC: ``hooks/simulate_continue.py`` asks it when `simulate end` must tell a stop from a gate's answer."""
    import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path at call time

    return any(
        finding.get("kind") == _run_driver.ASK_ANSWERED
        and isinstance(finding.get("at"), (int, float))
        and epoch - _ANSWER_SKEW_BEFORE_SECONDS <= finding["at"] <= epoch + _ANSWER_SKEW_AFTER_SECONDS
        for finding in _run_driver.read_findings(bases)
    )


def _answered_a_gate_at(session_id: str, epoch: float) -> bool:
    """Was the prompt written at ``epoch`` recorded as the answer to a gate?

    A Codex gate is answered by the next prompt, which opens the turn that carries the flow on. That
    prompt is the flow's own request being met, not the user stepping in, so it must not switch the
    turn-end rules off. ``ask_answered`` records the answer when it reads the prompt.
    """
    from _workdir import marker_dirs  # noqa: PLC0415

    return gate_answered_near(marker_dirs({"session_id": session_id}), epoch)


def _last_user_act(event: Event, marker: Path) -> str | None:
    """``"typed"``, ``"interrupted"`` or None: what the user last did after ``marker``'s mtime, as the
    turn ending now sees it.

    The transcript reader of the event's harness lists the prompts on the conversation's thread, newest
    first (``_harness/transcript.py``). The first of them started this turn: a person typed it
    (``_human_typed``) -> ``"typed"``. A prompt the harness wrote (a background task's completion, a
    heartbeat) is walked PAST, because an Esc before it still holds the floor: Esc fires no hook, so the
    turns after it are started only by such completions, and each would otherwise read as "nobody spoke".
    An interrupt row reached that way -> ``"interrupted"``; an earlier typed prompt was already answered
    in its own turn -> None. Every prompt must be newer than the marker: the prompt that OPENED the flow is
    the request it serves, not an interjection. A prompt that answers a gate is the flow's own request
    being met, never the user stepping in.

    Only a harness whose transcript records an Esc as a row (``Prompt.interrupted``) can answer ``"interrupted"``.
    """
    armed_at = marker.stat().st_mtime
    first = True
    for prompt in transcript.prompts_on_thread(event.harness, event.transcript_path, event.session_id):
        if prompt.epoch is None or prompt.epoch <= armed_at:
            return None
        if first and event.turn_ending_ask and _answered_a_gate_at(event.session_id, prompt.epoch):
            return None
        if prompt.interrupted:
            return "interrupted"
        if not prompt.injected and _human_typed(prompt.text):
            return "typed" if first else None
        first = False
    return None


def user_spoke_since(event: Event, marker: Path) -> bool:
    """Is the turn ending now the answer to the USER (a prompt they typed, or an Esc) after this window was armed?

    PUBLIC: ``hooks/simulate_continue.py`` asks it of the /simulate plan file (``marker`` is any file
    whose mtime is the moment that counts: the window's arm, or the plan's last write). See
    :func:`_last_user_act` for how the transcript is read.

    False on anything unreadable, so the turn-end rules stay on: a stand-down needs evidence. Never
    raises: a failure here would otherwise release the whole guard, anchor binding included.
    """
    try:
        return _last_user_act(event, marker) is not None
    except Exception:  # noqa: BLE001 - see the docstring: no evidence, no stand-down
        return False


def _usage_limited(event: Event, marker: Path) -> bool:
    """Has the account's usage limit been holding the session since this window was armed?

    The harness's transcript reader reports its newest usage-limit note (``transcript.usage_limit_since``); a harness
    that writes none, an unreadable transcript, or a note from before the arm is False, so the rules stay on. Never
    raises."""
    try:
        return transcript.usage_limit_since(event.harness, event.transcript_path, marker.stat().st_mtime)
    except Exception:  # noqa: BLE001 - no evidence, no stand-down
        return False


def _headless(workdir: Path | None) -> bool:
    """Is the run driver declared headless? An unreadable declaration reads as interactive."""
    try:
        import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path at call time

        return _run_driver.resolve(workdir=workdir).headless
    except Exception:  # noqa: BLE001 - no declaration, the interactive remedy stands
        return False


def _shell_read(event: Event) -> str:
    """The harness's own poll of a background command, spelled for the model ("" where it has none)."""
    import registry  # noqa: PLC0415 - hooks/ is on sys.path at call time

    names = registry.TOOLS["shell_read"].get(event.harness, ())
    return " or ".join(f"`{name}`" for name in names)


def _session_wakes(event: Event, workdir: Path | None) -> bool:
    """Whether background work wakes this session: the harness's flag, less a headless session that ends with its turn."""
    try:
        import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path at call time

        return _run_driver.wakes_on_background(event.wakes_on_background, event.wakes_when_headless, workdir=workdir)
    except Exception:  # noqa: BLE001 - no declaration, the harness's own flag stands
        return event.wakes_on_background


def _violations(
    message: str,
    command: str,
    *,
    stall_sanctioned: bool = False,
    pending_backed: bool = True,
    pending_fresh: bool = False,
    pending_subagent: bool = False,
    integrity_line: str | None = None,
    anchor_line: str | None = None,
    agent_head: str | None = None,
    unscored_quotes: list[str] | None = None,
    unresolved: dict[str, str] | None = None,
    workspace: tuple[Path, ...] = (),
    foreground_wait: bool = False,
    wakes: bool = True,
    stdin_polls: bool = False,
    ends_with_turn: bool = False,
    shell_read: str = "",
) -> list[str]:
    out: list[str] = []
    # A PREREQUISITE the run driver never answered (``_unresolved_prerequisites``): the turn that
    # ends without landing is ending ON the run driver, which is what ask_guard told it to do.
    on_run_driver = bool(unresolved)
    ends_on_user = stall_sanctioned or on_run_driver
    # A stale marker must not enforce a foreign command's contract (idempotent — handle()
    # resolves this too, and re-resolving a already-effective command is a no-op).
    command = _effective_command(message, command)
    # Relaxed only for messages that LOOK like live progress; a hand-off — or anything
    # that is neither — gets the full profile. Defaulting the other way was an opt-out.
    relaxed = _progress_shaped(message, command)
    # Always-on tier — these hold mid-flow as much as at the hand-off:
    if (
        _COST_FIELD_RE.search(message)
        or _unexempted_hit(message, _COST_FIGURE_RE, deny=_SPEND_MACHINERY_RE, require=_SPEND_ATTRIBUTION_RE)
        or _unexempted_hit(message, _DOMAIN_CHARGE_RE, deny=None, require=None)
    ):
        out.append(
            _Violation(
                "cost figures (backend spend is internal telemetry — remove every $ figure / total_cost_usd; "
                "the AGENT's own transaction amounts are domain evidence and stay)",
                "remove the cost figures (the $ amounts of the build's spend).",
            )
        )
    if dead := _dead_local_links(message, workspace):
        out.append(
            _Violation(
                f"{_DEAD_LINK_VIOLATION} ({', '.join(dead)}) — a hand-typed path resolves on no surface the "
                "reader has; for a report, paste the line scripts/report_link.py link printed for it (the file's "
                "own link); for a code location, link its ABSOLUTE path (/abs/file.py:42)",
                f"fix the local link that opens nowhere ({', '.join(dead[:3])}): use the report's own link, or link code by absolute path.",
            )
        )
    if not ends_on_user and _stall_hit(message):
        out.append(
            _Violation(
                f"{_STALL_VIOLATION} (the flow is pre-authorized — apply the decision table and continue)",
                "no question-shaped stall: the flow is pre-authorized, so apply the skill's decision table and continue.",
            )
        )
    # NOT exempted by question-shape. A co-occurrence test was tried and reverted: the
    # reported false positive ("I paused the build to confirm which X you meant") matches
    # neither _STALL_PHRASE_RE nor a trailing '?', so the exemption never covered it — while
    # a sanctioned message that was BOTH abort- and question-shaped ("...budget is nearly
    # exhausted. Should I continue in a fresh session?") drew no violation from either rule.
    # The USER's turn is what separates the two, and handle() reads it from the transcript
    # (``user_spoke_since``): a turn answering the user drops this rule's verdict there.
    # The unresolved PREREQUISITE is the one stop reason this rule must not answer with "resume":
    # that push past an unanswered gate is how the corpus-scope answer got fabricated.
    if not on_run_driver and _abort_hit(message, failure=_is_stop_text(message, command)):
        out.append(
            _Violation(
                f"{_ABORT_VIOLATION} (timing/context/budget is never a stop reason — resume the flow and apply "
                "the decision table: continue the loop, run the qualification pass, and land per the skill's rules; "
                "only a sanctioned BLOCKER escalation or the landing rule ends a flow early)",
                "no unilateral early stop: continue the flow (timing, context and budget are not stop reasons).",
            )
        )
    # Hand-off tier — the report doctrine's style rules bind the FINAL rendering; an
    # honest mid-flow status line ('phase evolving, 0/3 complete, next poll in 5 min')
    # must not be blockable for naming the machinery it is reporting on.
    if not relaxed and (_JARGON_RE.search(message) or _unexempted_hit(message, _CRASH_JARGON_RE, deny=None, require=None)):
        out.append(
            _Violation(
                "raw backend vocabulary (warnings/tracebacks/job internals stay internal — a crash you "
                "attribute to the AGENT is a finding and stays)",
                "remove raw backend vocabulary (warnings, tracebacks, job internals).",
            )
        )
    if not relaxed and _CHRONOLOGY_RE.search(message):
        out.append(
            _Violation(
                "build-chronology narration (iterations/critic passes/cycles are process, not product)",
                "remove build-chronology narration (iterations, critic passes, cycles).",
            )
        )
    if not relaxed and _unexempted_hit(message, _self_flaw_re(command), deny=None, require=None):
        out.append(
            _Violation(
                "suite-self-flaw narration (defects you found and fixed are process, and accepted gaps are not "
                "report content either — present the suite by what it CATCHES; coverage detail lives in the artifacts)",
                "remove suite-self-flaw narration (defects you found and fixed); say what the suite catches.",
            )
        )
    if not relaxed and _suite_gap_hit(message, command):
        out.append(
            _Violation(
                "SUITE-coverage narration (what the suite does NOT cover is never report content — the rendered "
                "artifacts carry it; run-INTEGRITY facts — not executed / errored / model substituted / shipped "
                "short — are RESULTS and get their one line, but a coverage gap does not)",
                "remove SUITE-coverage narration (what the suite does not cover); run-integrity facts keep their one line.",
            )
        )
    if not relaxed and command != "fixes" and _SUITE_DOUBT_RE.search(message):
        out.append(
            _Violation(
                "suite-doubt framing (the suite was validated by the quality gate — failures are findings about "
                "the AGENT; present /simulation-fixes as the agent-repair step, never as suite-vs-test arbitration, "
                "and never offer 'test-setup artifacts' as a reading of the customer's results)",
                "remove suite-doubt framing: present failures as findings about the agent.",
            )
        )
    if not relaxed:
        out += _customer_vocabulary(message, command)
    if not relaxed and _RESULT_DOUBT_RE.search(message):
        out.append(
            _Violation(
                "an internal quality caveat (how sure the product is of its own result stays in the internal files — "
                "say what the result means and the one action that changes it; a suite that must be rebuilt → "
                "/simulation-generate-suite)",
                "remove the internal quality caveat: say what the result means and the one action that changes it.",
            )
        )
    anchors = _ANCHORS.get(command)
    stem = _ANCHOR_STEMS.get(command)
    # The in-progress anchor answers ONE question — does this turn owe the hand-off
    # skeleton — and nothing else. It is itself stem-shaped ("Suite: in progress" matches the
    # "Suite:" stem), so the bent test runs against the message WITHOUT its anchor line rather
    # than being waived when the anchor is present: waiving it wholesale let one progress line
    # excuse a bent anchor anywhere else in the same message.
    in_progress = _progress_anchor_present(message, command)
    if in_progress and command in _QUALITY_LOOP_COMMANDS and (exposed := _QUALITY_LOOP_RE.search(message)):
        out.append(
            _Violation(
                f"the suite's calibration loop named in chat ('{exposed.group(0)}'): while the suite calibrates the "
                "simulation, the in-progress line says only that, with its clock — e.g. 'Suite: in progress — "
                "calibrating the simulation, about 10-20 minutes; this session picks up again by itself.' Never name "
                "the calibration runs, their conversations or scores, a reviewer, or what was fixed. Once the final set "
                "has landed and the suite's own final run is going, the line is 'Suite: in progress — scoring your agent "
                "on the final suite, about <N> minutes; this session picks up again by itself.'",
                "say only 'Suite: in progress — calibrating the simulation, about 10-20 minutes; this session picks up again by itself.'"
                " (during the suite's final run: 'Suite: in progress — scoring your agent on the final suite, about <N> minutes;"
                " this session picks up again by itself.')",
            )
        )
    bent = stem is not None and stem.search(_without_progress_anchor(message, command)) is not None
    # The exact-anchor tests read the message WITHOUT its in-progress line for the same reason
    # the bent test does — the progress anchor is anchor-SHAPED. /simulation-fixes proves why
    # it matters: its exact anchor is the bare word "Findings" and ':' is in _ANCHOR_TAIL, so
    # "Findings: in progress" matched the exact anchor — reading a suspension as a hand-off,
    # exempting that command from the rule below AND closing its window (handle()) on work that
    # was still in flight.
    body = _without_progress_anchor(message, command)
    exact = _anchor_present(body, command)
    failure_handoff = _is_stop_text(message, command)
    # THE PENDING CONTRACT. While a window is armed, a turn may end in exactly three states:
    # it LANDS the flow (exact anchor / failure hand-off), it ends on the USER (a sanctioned
    # ask, whose reply is the re-invocation, or an unresolved PREREQUISITE), or the harness holds
    # work that will re-invoke the session (the pending register, written mechanically by
    # pending_register.py). Any other turn end stalls the flow: nothing resumes it and it sits
    # idle until a human notices — observed live, a completed run unprocessed for 14 minutes.
    #
    # Deliberately a STATE test, not a shape test. The first cut fired only on an in-progress
    # anchor or an announce-verb finale, which inverted the incentive it was built for:
    # dropping the anchor passed ("the critic is out; report expected shortly"), and that
    # re-render also reset the strike counter — so the guard punished the one honest shape
    # the skills ask for and rewarded every dishonest one. Judging the STATE cannot be
    # evaded by rephrasing, and it needs no lexicon to maintain.
    #
    # The REMEDY, though, is shape-aware — because the reason is the model's only instruction
    # and ``in_progress`` says which half it is already doing. A message that has NOT rendered
    # the mid-flow line is told the whole move (launch the work, then end on that line); one
    # that HAS is told the part it is missing, and never to re-render a line it just wrote.
    if not pending_backed and not (exact or failure_handoff) and not ends_on_user:
        mid_flow = ', '.join(repr(a) for a in _PROGRESS_ANCHORS.get(command, ())) or 'the mid-flow line for this command'
        remedy = (
            f"The mid-flow line {mid_flow} is not itself a resume — it reports work, it does not start any, so "
            "re-rendering it changes nothing. Either do the next step NOW in this turn (spawn the critic / launch "
            "the run / keep polling) and continue, or start the wait in the background (never a detached "
            "nohup) so the harness re-invokes you when it finishes and KEEP the mid-flow line"
            if in_progress
            else "Do the next step NOW in this turn (spawn the critic / launch the run / keep polling) and "
            "continue; or, if the work genuinely runs in the background, start it in the background (never "
            "a detached nohup) — the harness registers it and re-invokes you when it finishes — and end on the "
            f"mid-flow line {mid_flow}"
        )
        if foreground_wait:
            # Nothing re-invokes this session after its turn ends, so a backgrounded wait is a lost
            # result. /analyze-traces has no interactive background wait; a headless run has no
            # notification to wake it.
            # Codex has no tool timeout to raise: a command that outruns a call keeps running and is polled.
            how = (
                "(keep the command, or its `write_stdin` polls, going until it exits; `poll_job.sh` stays a "
                "foreground command, re-run on exit 14)"
                if stdin_polls
                else "(`poll_job.sh` with a Bash timeout of 600000, re-run on exit 14)"
            )
            remedy = (
                f"The mid-flow line {mid_flow} reports work, it does not start any. Nothing re-invokes this "
                f"session once the turn ends, so do not background the wait: keep polling in the FOREGROUND "
                f"{how} in this turn until the job is `done` or `failed`, then land the flow with its earned "
                "hand-off"
            )
            if ends_with_turn and shell_read:
                # A harness whose background command is polled by a tool of its own: the launch stays where it is,
                # and the turn is held open on that tool's bounded polls, or on the scripts in the foreground.
                remedy = (
                    f"The mid-flow line {mid_flow} reports work, it does not start any. This session ends with "
                    "the turn and takes its background work with it, so hold the work inside this turn: keep "
                    f"polling the run's launch command with {shell_read} in bounded steps until it exits, or wait "
                    "in the FOREGROUND on `run_wait.sh <its run dir>` for a run and `poll_job.sh <job id>` for a "
                    "backend job, re-run on exit 2; a reviewer subagent spawned in the foreground, never in the "
                    "background. Then land the flow with its earned hand-off"
                )
            elif ends_with_turn:
                # A headless session that ends with its turn also kills what it started in the background, so
                # the wait is per kind of work, each one held inside this turn.
                remedy = (
                    f"The mid-flow line {mid_flow} reports work, it does not start any. This session ends with "
                    "the turn and takes its background work with it, so wait in the FOREGROUND in this turn: a "
                    "run on `run_wait.sh <its run dir>` and a backend job on `poll_job.sh <job id>`, each with a "
                    "Bash timeout of 600000 and re-run on exit 2; a reviewer subagent spawned in the foreground, "
                    "never in the background. Then land the flow with its earned hand-off"
                )
        if foreground_wait:
            # The harness never wakes this session, so the wait happens inside the turn; how (the polling scripts, their
            # timeouts) is in the report section of the skill, not in this line.
            fix = "this turn ends mid-flow with nothing to resume it: keep waiting in the foreground until the work finishes, then land the flow."
        elif in_progress:
            fix = (
                "this turn ends mid-flow with nothing to resume it: do the next step now, or start the wait in the "
                f"background and keep {mid_flow}."
            )
        else:
            fix = (
                "this turn ends mid-flow with nothing to resume it: do the next step now, or start the wait in the "
                f"background and end on {mid_flow}."
            )
        out.append(
            _Violation(
                f"{_STRANDED_VIOLATION} (no landing, no question to the user, no "
                "harness-tracked work in flight, and no MCP call the harness moved to the background — so this flow "
                f"stops here until a human notices). {remedy}; or land the flow with its earned hand-off",
                fix,
            )
        )
    # THE CLOCK (see FRESH_PENDING_SECONDS). Only the wait-ANNOUNCING turn owes it: pending
    # work registered moments ago + the mid-flow line = this message starts the silence.
    if (
        wakes  # the promise is "this session resumes by itself", which a harness that never wakes it cannot keep
        and pending_backed
        and pending_fresh
        and in_progress
        and not ends_on_user  # a turn holding an unanswered sanctioned question ends ON
        # THE USER, not on a silence — telling it "nothing for the user to type" is false, and
        # complying is the fabricate-the-answer failure .ask_pending exists to stop.
        and not (_CLOCK_DURATION_RE.search(message) and _CLOCK_RESUME_RE.search(message))
    ):
        out.append(
            _Violation(
                "a wait with no clock (this turn ends on the mid-flow line with background work just put in "
                "flight, and says nothing about the silence that follows — an unexplained multi-minute wait "
                "reads as a dead flow and provokes a nudge. Keep everything else and ADD one product-terms "
                "sentence: roughly how long this stage typically takes, in minutes, and that this session "
                "resumes by itself the moment it reports — nothing for the user to type)",
                "a wait with no clock: say how long this stage usually takes, in minutes, and that the session resumes by itself.",
            )
        )
    # THE CLOCK MUST BE HONEST. Requiring a number let a live session promise "5-10 minutes" for a
    # reviewer whose measured median is 13.5 — a deadline that lapses four times in five, turning a
    # healthy wait into an apparent hang exactly when the user starts watching for one.
    if (
        pending_backed
        and pending_fresh
        and pending_subagent
        and in_progress
        and not ends_on_user  # same reason as the rule above
        and not _CLOCK_OPEN_ENDED_RE.search(message)
    ):
        ceiling = _promised_ceiling_minutes(message)
        if ceiling is not None and ceiling < SUBAGENT_WAIT_FLOOR_MINUTES:
            out.append(
                _Violation(
                    f"an under-promised wait (this turn tells the user the reviewer takes about {ceiling} "
                    f"minutes, but a spawned reviewer usually runs longer than that — measured median 13.5 "
                    f"min, only 1 in 5 finishing inside 10. A ceiling reality breaches is worse than no "
                    f"estimate: it manufactures a deadline, and the wait reads as HUNG the moment it "
                    f"lapses. State a range that holds — at least {SUBAGENT_WAIT_FLOOR_MINUTES} minutes at "
                    f"the top, or say it can run longer — and keep the rest of the message as it is)",
                    f"an under-promised wait: {ceiling} minutes is too short, a spawned reviewer usually takes 10-20 minutes; give a "
                    f"range that tops out at {SUBAGENT_WAIT_FLOOR_MINUTES} minutes or more, or say it can run longer.",
                )
            )
    if anchors and failure_handoff:
        anchors = None  # a genuine-failure hand-off carries no product skeleton
    if anchors and not bent and _driven_substep(message, command):
        # An inner sub-step's own report is not the DRIVER's hand-off, so it does not owe
        # the driver's anchor. Waived only while the message does NOT also reach for the
        # driver's own skeleton — otherwise a driver could shed its anchor requirement by
        # prefixing a driven command's line.
        anchors = None
    if anchors:
        if not exact and bent:
            out.append(
                _Violation(
                    f"a BENT anchor (an anchor-shaped line that matches none of the exact anchors: {', '.join(anchors)} "
                    f"— render the one line that is TRUE, verbatim; never invent variants like 'built, not yet qualified'; "
                    f"while the flow's work is still executing, the mid-flow line is "
                    f"{', '.join(repr(a) for a in _PROGRESS_ANCHORS.get(command, ()))})",
                    f"a BENT anchor: use one of these lines, verbatim: {', '.join(anchors)}; while work still runs, "
                    f"{', '.join(_PROGRESS_ANCHORS.get(command, ()))}.",
                )
            )
        elif not exact and not in_progress and _COMPLETION_RE.search(message):
            out.append(
                _Violation(
                    f"{_COMPLETION_VIOLATION} (the hand-off must carry one of the anchors: "
                    f"{', '.join(anchors)} — or, while the flow's work is STILL EXECUTING, lead with the mid-flow line "
                    f"{', '.join(repr(a) for a in _PROGRESS_ANCHORS.get(command, ()))}, which passes without closing the flow)",
                    f"a completion claim without the report skeleton: start the hand-off with {', '.join(anchors)}, or, "
                    f"while work still runs, {', '.join(_PROGRESS_ANCHORS.get(command, ()))}.",
                )
            )
    # Owed only by a LANDING hand-off: a mid-flow progress line reports nothing about what ran,
    # and a failure hand-off carries no product skeleton to hang it on.
    if exact and not in_progress and not failure_handoff and (missing := _integrity_violation(message, command, integrity_line)):
        out.append(_Violation(missing, f"the hand-off is missing its run-integrity line: add it verbatim, {integrity_line!r}"))
    # Same gate as the line above, and owed by the same hand-off: the suite rung is a claim about
    # what was measured, so a mid-flow line owes none and a failure hand-off has no ladder to land on.
    if exact and not in_progress and not failure_handoff and (wrong_rung := _anchor_violation(message, command, anchor_line)):
        out.append(_Violation(wrong_rung, f"the hand-off does not carry the anchor this suite's state licenses: use this line verbatim, {anchor_line!r}"))
    if exact and not in_progress and not failure_handoff and (no_agent := _agent_line_violation(message, command, agent_head)):
        out.append(_Violation(no_agent, f"the hand-off does not carry the Agent verdict: add the Agent line after the Suite line, starting {agent_head!r}"))
    if exact and not in_progress and not failure_handoff and command == "generate-suite" and unscored_quotes:
        out.append(
            _Violation(
                "an example quoted from a conversation the scored run did not score ("
                + "; ".join(repr(q) for q in unscored_quotes)
                + ") — quote an audited true red of the scored final run, verbatim with its scenario and turn, and state "
                "only what that turn shows; never a qualification run or an abandoned attempt",
                "an example quoted from a conversation the scored run did not score ("
                + "; ".join(repr(q) for q in unscored_quotes)
                + "): quote only an audited true red of the scored final run, verbatim with its scenario and turn.",
            )
        )
    # THE HOLD. Every one of the command's anchors is a delivery claim, and a flow told to stop at a
    # gate only the run driver can answer has nothing to deliver. The stop itself is legal (above).
    if unresolved and exact and not failure_handoff:
        owed = "; ".join(f"'{gate}': {needs}" for gate, needs in unresolved.items())
        out.append(
            _Violation(
                f"{_HOLD_VIOLATION} ({owed}) — only the run driver can supply it and no "
                "answer came back, so this flow has no hand-off to render. Do NOT supply the answer yourself and do "
                "NOT render the anchor: end this turn ON the run driver, saying in product terms what they must supply "
                "(that turn end is accepted as it is). If a person is here, sanction the gate and ask it again "
                "through the ask-the-user tool — an answer releases the hold. The failure hand-off stays available",
                f"no delivery over an unanswered PREREQUISITE ({owed}): end this turn on the run driver and say what they must supply.",
            )
        )
    return out


def _reason(command: str, violations: list[str]) -> str:
    """The one short line a correction says: what to change, never the rules.

    The correction is shown to the user next to the turn it corrects, so it stays a line. The rules the gate checks are
    in the report section every guarded skill carries (``docs/report-doctrine.md``), where the model reads them before it
    writes the message.
    """
    # A held turn gets its own frame: "re-render the report" tells the model to write the delivery the hold refuses.
    if any(v.startswith(_HOLD_VIOLATION) for v in violations):
        return f"Run driver hold: {' '.join(dict.fromkeys(_fix_of(v) for v in violations))}"
    return "Report: " + " ".join(dict.fromkeys(_fix_of(v) for v in violations))


def _with_printed_stop(violations: list[str], message: str, command: str, args: list[str], retried: bool) -> list[str]:
    """``violations`` for a turn that ends after a printed stop: without the rules that push the run on (the turn-end
    rules and the completion claim), and with the one correction that names the command when the message is not the stop
    text or carries a cause sentence. A retried turn drops the cause-word block and the correction: one block per turn
    converges, and the halt already holds."""
    kept = [v for v in violations if not v.startswith(_LIMIT_STANDDOWN)]
    caused = [v for v in kept if v.startswith(_VOCABULARY_VIOLATION) and f"[{_STOP_CAUSE_TERM}/" in v]
    if retried:
        return [v for v in kept if v not in caused]
    if caused or not _is_stop_text(message, command):
        kept.append(_unsaid_stop(args))
    return kept


def _unsanctioned_question_reason() -> str:
    """The correction for a question that ends a turn with no sanction behind it.

    The command that sanctions a gate is in the skills' error-posture text, so the line names only the choice.
    """
    return (
        "Question: the flow is pre-authorized, so decide it from the skill's decision table; if it is a gate the skill scripts "
        "or a blocker only the user can clear, sanction it first, then ask and end the turn on the question."
    )


def freshest_sanction(paths: list[Path]) -> Path | None:
    """The youngest LIVE sanction among ``paths``, or None when every copy is expired.

    The TTL is IMPORTED from ask_guard, never restated — two numbers would drift and the two
    halves would then disagree about one marker. An unreadable age resolves to LIVE, matching
    ask_guard's direction: failing closed here would deny a legitimate blocker escalation.
    """
    if not paths:
        return None
    try:
        from ask_guard import SANCTION_TTL_SECONDS
    except ImportError as exc:  # the feature, not one marker, would be silently off
        print(
            f"report_guard: sanction TTL unavailable ({exc}) — stale sanctions will excuse a prose "
            "stall for this session. Report this.",
            file=sys.stderr,
        )
        return paths[0]

    def _age(path: Path) -> float | None:
        try:
            return max(0.0, time.time() - path.stat().st_mtime)
        except OSError:
            return None  # unknown age reads as LIVE

    live = [(p, a) for p in paths for a in (_age(p),) if a is None or a <= SANCTION_TTL_SECONDS]
    if not live:
        return None
    return min(live, key=lambda pair: pair[1] if pair[1] is not None else -1.0)[0]


def pending_backed(bases: list[Path]) -> bool:
    """Is harness-tracked work registered or leased, and still plausibly in flight?

    TWO records, because a launch and a lifetime are different facts: the REGISTER says work
    was put in flight, the LEASE says a subagent is still holding one. A launch-time record
    alone cannot carry a wait the launcher does not re-announce.

    The UNION of the marker directories, not the first hit. The register is written beside the
    window it was observed under, and the two windows arm independently — so a leftover in the
    session directory would otherwise mask a live register in the agent directory (or the other
    way round) and block a turn with a running task behind it. Age decides here, so the reader
    has to see every copy, not the first one.
    """
    from _workdir import find_markers

    now = time.time()
    for path in find_markers(PENDING, bases):
        try:
            age = now - path.stat().st_mtime
        except OSError:
            continue
        # WHICH bound, by what the record says it is. A backgrounded MCP call is released by a
        # notification that a compacted or resumed session never sees, so it gets the shorter
        # one; everything else keeps the runner-sized bound.
        try:
            note = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            # The PERMISSIVE bound was being chosen precisely when the record is least
            # trustworthy. A short bound costs one extra block whose reason says what to do; a
            # long one costs a silent strand that needs a human.
            print(
                f"report_guard: register at {path} unreadable ({exc}) — applying the SHORT bound; "
                "an unreadable record is not evidence of a four-hour runner.",
                file=sys.stderr,
            )
            note, ttl = "", MCP_PENDING_TTL_SECONDS
        else:
            ttl = MCP_PENDING_TTL_SECONDS if note.startswith(MCP_PENDING_PREFIX) else PENDING_TTL_SECONDS
        if age <= ttl:
            return True
    from pending_register import live_lease_holders  # the writer owns the format

    return any(live_lease_holders(path, now - PENDING_TTL_SECONDS) for path in find_markers(LEASE, bases))


def _pending_fresh(bases: list[Path]) -> tuple[bool, bool]:
    """``(fresh, is_subagent)`` — was work put in flight MOMENTS ago, and is it a SUBAGENT?

    Freshness is mtime only: the youngest register or lease inside ``FRESH_PENDING_SECONDS``
    marks this as the turn that starts a wait, which is the one turn that owes the user a clock.
    Unreadable ages count as not-fresh: the clock is a courtesy contract, and its trigger must
    never manufacture a block out of a stat error.

    The KIND is read from the record, because only the subagent wait has a measured duration to
    be honest about (``SUBAGENT_WAIT_FLOOR_MINUTES``): a LEASE is written by nothing else, and
    ``pending_register`` prefixes a spawn's register note "subagent:". An unreadable note is a
    plain wait — the floor is never applied on a guess.
    """
    from _workdir import find_markers

    now = time.time()
    fresh = subagent = False
    for name in (PENDING, LEASE):
        for path in find_markers(name, bases):
            try:
                if now - path.stat().st_mtime > FRESH_PENDING_SECONDS:
                    continue
            except OSError:
                continue
            fresh = True
            if name == LEASE:
                subagent = True
                continue
            try:
                subagent = subagent or path.read_text(encoding="utf-8", errors="replace").startswith("subagent")
            except OSError:
                pass
    return fresh, subagent


def known_gate_marker(path: Path | None) -> bool:
    """Does this sanction/pending marker name a gate some skill actually scripts?

    ``ask_guard`` voids an unknown gate at spend time; this hook must apply the same rule, or
    the identical self-minted marker still buys the question — asked in prose rather than
    through the tool. The BREAKER gate counts as legitimate here (it marks a real in-flight
    question the guard itself allowed) but is never re-armed; see the promotion site.
    """
    if path is None:
        return False
    try:
        gate = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return True  # unreadable: treat as present, exactly as the age check does
    try:
        from ask_guard import BREAKER_GATE, KNOWN_GATES
    except Exception:  # pragma: no cover - a missing import must not manufacture a stall
        return True
    return gate in KNOWN_GATES or gate == BREAKER_GATE


def _asked_and_awaiting(event: Event, sanction: Path | None) -> bool:
    """Did this turn make an ask the harness accepted, under THIS sanction, for a gate some skill scripts, with no answer back yet?

    Only a turn-ending-ask harness: ``ask_guard`` leaves the sanction standing there, so the turn end is
    what spends it, and a final message that reads as a statement ("I have asked ...") says nothing about
    whether the turn ended on the question. The transcript does. The gate is checked strictly: an unknown
    gate and the breaker allowance are never promoted, whatever the turn did. The gate is read from the
    sanction file, so the ask must be newer than the sanction: one written after the ask names a gate that
    has not been asked yet.
    """
    if not (event.turn_ending_ask and sanction is not None and event.transcript_path):
        return False
    try:
        gate = sanction.read_text(encoding="utf-8", errors="replace").strip()
        written_at = sanction.stat().st_mtime
        from ask_guard import BREAKER_GATE, KNOWN_GATES  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - without the gate or the allowlist nothing may be promoted
        return False
    if gate == BREAKER_GATE or gate not in KNOWN_GATES:
        return False
    asked_at = transcript.unanswered_ask_epoch(event.harness, event.transcript_path)
    return asked_at is not None and written_at <= asked_at


def ask_outstanding(bases: list[Path]) -> bool:
    """Is a question to the user standing — a live sanction, or a sanctioned ask in flight — for a
    gate some skill scripts? The same two reads handle() makes; PUBLIC for ``simulate_continue``."""
    from _workdir import find_marker, find_markers

    return known_gate_marker(freshest_sanction(find_markers(ASK_SANCTION, bases))) or known_gate_marker(find_marker(ASK_PENDING, bases))


def _record_interrupt(event: Event, bases: list[Path]) -> None:
    """Every turn end, window or not, before anything else: an Esc since the last one pauses a /simulate run
    (``simulate_continue.record_interrupt``). Fail-open."""
    try:
        import simulate_continue  # noqa: PLC0415 - hooks/ is on sys.path at call time

        simulate_continue.record_interrupt(event, bases)
    except Exception:  # noqa: BLE001 - never allowed to break the guard
        return


def _wait_page(event: Event, bases: list[Path]) -> None:
    """The turn ended on the user's answer: the overview page's running step says so (``simulate_continue.wait_page``)
    until the user next types (``flow_arm``). Fail-open."""
    try:
        import simulate_continue  # noqa: PLC0415 - hooks/ is on sys.path at call time

        simulate_continue.wait_page(event, bases, True)
    except Exception:  # noqa: BLE001 - never allowed to break the guard
        return


def _continuation(event: Event, bases: list[Path], kind: str, *, command: str | None = None,
                  failure: bool | str | None = None) -> Decision | None:
    """The block that continues a /simulate run, when ``simulate_continue.decide`` returns one.

    Called ONLY where this hook itself allows the stop (after a clean hand-off closed the window, and
    with no window armed), so it can never push past a hand-off this hook is blocking. ``failure`` is the
    hand-off's failure kind (:func:`_stop_text_kind`), None for none. Fail-open.
    """
    try:
        import simulate_continue  # noqa: PLC0415 - hooks/ is on sys.path at call time

        reason = simulate_continue.decide(event, bases, kind, command=command, failure=failure, guard=sys.modules[__name__])
    except Exception:  # noqa: BLE001 - the continuation is never allowed to break the guard
        return None
    if not reason:
        return None
    return model.correct_stop(reason) if simulate_continue.is_closing_reason(reason) else model.block_stop(reason)


def _unresolved_prerequisites(bases: list[Path], workdir: Path | None) -> dict[str, str]:
    """``gate -> needs`` for every gate whose LATEST typed event says nobody answered.

    Read from ``.run_driver_findings.jsonl``, which the hooks write and this hook only reads: an
    headless route files ``run_driver_input_required`` — on ANY gate, a fact-dispatched one
    included — and says "STOP the flow", and ``needs`` is read from that row; interactively
    ``ask_answered`` files ``run_driver_ask_unanswered`` for an ask that came back empty (held only on a
    PREREQUISITE gate — a DECISION has a declared default) and ``run_driver_ask_answered`` when a re-ask
    is answered, which supersedes it — as does a later default for that gate (a fact-dispatched gate
    re-reached once the run driver recorded its fact). Any other kind is skipped. A stop or an unanswered
    ask on a gate whose answer is a config key is settled once ``workdir``'s config declares it
    (``declared_answer``).
    """
    try:
        import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path at call time
    except Exception as exc:  # noqa: BLE001 - the hold, not the whole guard, is off
        print(f"report_guard: run driver findings unreadable ({exc}) — an unanswered PREREQUISITE will not be held.", file=sys.stderr)
        return {}
    cleared = (_run_driver.ASK_ANSWERED, _run_driver.DEFAULT_APPLIED, _run_driver.ANSWER_DECLARED)
    events = (_run_driver.INPUT_REQUIRED, _run_driver.ASK_UNANSWERED, *cleared)
    latest: dict[str, dict] = {}
    for finding in _run_driver.read_findings(bases):
        gate, kind = finding.get("gate"), finding.get("kind")
        if isinstance(gate, str) and kind in events and (gate in _run_driver.PREREQUISITE_GATES or kind != _run_driver.ASK_UNANSWERED):
            latest[gate] = finding
    return {
        gate: str(row.get("needs") or _run_driver.PREREQUISITE_GATES.get(gate) or "declare what this gate needs")
        for gate, row in latest.items() if row.get("kind") not in cleared
        and not (row.get("kind") in (_run_driver.INPUT_REQUIRED, _run_driver.ASK_UNANSWERED) and _run_driver.declared_answer(gate, workdir) is not None)
    }


def _promised_ceiling_minutes(message: str) -> int | None:
    """The largest duration the message states, in whole minutes, or None when it states none.

    A seconds figure rounds up to a whole minute and counts as at least one, so "about 30 seconds" is a
    one-minute ceiling that the under-promise floor then judges like any other.
    """
    best: int | None = None
    for pattern, per_minute in ((_CLOCK_MINUTES_RE, 1), (_CLOCK_SECONDS_RE, 60)):
        for m in pattern.finditer(message):
            for group in m.groups():
                if group is None:
                    continue
                try:
                    value = max(1, -(-int(group) // per_minute))
                except ValueError:
                    continue
                best = value if best is None else max(best, value)
    return best


def _workspace_roots(event: Event) -> tuple[Path, ...]:
    """The bases a relative chat link resolves against: the session cwd (the Stop payload's, else this
    process's, which is the session's directory on every harness: Claude Code and Codex start hooks there
    and the entry point moves a Copilot hook there) and the project dir the harness exports."""
    roots = [Path(event.cwd or Path.cwd())]
    if project := os.environ.get("CLAUDE_PROJECT_DIR"):
        roots.append(Path(project))
    return tuple(roots)


def handle(event: Event) -> Decision:
    """Judge the turn that is ending: block it with the re-render instructions, or let it end.

    An internal error propagates to the entry point, which lets the turn end and names the failure
    on stderr: the style gate must never wedge a session.
    """
    from _workdir import find_marker, find_markers, marker_dirs  # noqa: PLC0415

    # None: the harness sent no message and its transcript supplied none. That is not an empty
    # message (nothing was said) but an unjudgeable one, so it is checked against the window below.
    unreadable = event.last_message is None
    message = event.last_message or ""
    bases = marker_dirs({"session_id": event.session_id})  # once: the fallback entry shells out to git
    _record_interrupt(event, bases)  # every turn end, an empty one included
    if not unreadable and not message.strip():
        return model.none()
    # One block per turn: Claude Code and Codex mark the retry with ``stop_hook_active``, which ends the turn, so the
    # check never wedges one. The codec decodes the flag for Codex only, so Claude Code's is read from the payload.
    retried = event.stop_hook_active or event.raw.get("stop_hook_active") is True
    if not retried and _diagnose_trailing_text(message):
        return model.block_stop(_DIAGNOSE_REASON)
    if not retried and _diagnose_pointer_missing(message):
        return model.block_stop(_diagnose_pointer_reason())
    marker = find_marker(FLOW_MARKER, bases)
    if marker is None:
        # No active flow → ordinary conversation, never validated. A /simulate run between stages lands
        # here too, and its continuation decides whether the run goes on. A stage's error-posture exit
        # closes its own window BEFORE its failure hand-off renders, so that hand-off arrives HERE; it
        # halts the run, never "continue with stage <the same stage>".
        printed = _printed_stop(event, bases)
        if printed is not None and not retried and not _is_stop_text(message):
            return model.correct_stop(_reason("", [_unsaid_stop(printed)]))
        _record_headless_halt(message, None, bases)
        return _continuation(event, bases, "idle", failure=_run_stop_kind(message, None, bases)) or model.none()
    # Whichever directory the window was found in owns this flow's strike counter, so a
    # session-armed window and a fallback-armed one each stay self-consistent.
    workdir = marker.parent
    # First line only. The file holds just the command name today, but reading it this way
    # is what keeps a marker with any trailing content from becoming the command name and
    # silently matching no profile — the failure a second line nearly introduced.
    lines = marker.read_text(encoding="utf-8", errors="replace").splitlines()
    command = (lines[0].strip() if lines else "") or "plurai-simulation"
    window = command
    # …unless the marker is STALE (a previous command's window that outlived it): then
    # the message's own anchor names the contract to validate, and the clean-pass close
    # below clears the foreign marker that no skill's own-name-guarded cleanup could.
    command = _effective_command(message, command)

    # A sanction written for a gate whose question the model asked in PROSE (instead
    # of the AskUserQuestion tool) is still standing at Stop: the stall is sanctioned
    # — consume the marker and let the question through. Without this, the sanctioned
    # blocker escalation survives only via the tool.
    # An AGED sanction is absent here for the same reason ask_guard denies on it: it is a
    # bearer token written immediately before a question, so one older than its TTL was
    # never spent and says nothing about THIS turn. Treating it as present let a stale
    # marker sanction a prose stall for the rest of the session.
    #
    # The UNION, not first-hit-wins — _workdir.find_markers' own rule: first-hit is right for
    # a marker whose mere EXISTENCE is the answer, and wrong the moment its age decides. A
    # stale copy in one base was shadowing a fresh one in the other, so a legitimately
    # sanctioned question was reported as a stall AND took a strike, every turn until the
    # next arm. The YOUNGEST copy decides.
    if event.turn_ending_ask:
        # A gate whose answer came back inside the turn (the async ask's reply row) is settled before
        # anything below reads its markers: it has been answered, so this turn does not end on the user.
        try:
            import ask_answered  # noqa: PLC0415 - hooks/ is on sys.path at call time

            ask_answered.settle_in_turn_reply(event)
        except Exception as exc:  # noqa: BLE001 - the reply is then recorded by the reply prompt, or not at all
            print(f"report_guard: could not read a gate's reply from the transcript ({exc}).", file=sys.stderr)
    sanction = freshest_sanction(find_markers(ASK_SANCTION, bases))
    # A sanctioned question that was ASKED and not answered — the user rejected the tool to
    # clarify, or the turn ended while it stood. ask_guard consumes the sanction at PreToolUse,
    # so without this marker the Stop hook sees no sanction and reports a mid-flow stall,
    # telling the model to take the next step immediately with no questions. Live, the model
    # complied by fabricating the user's answer. A standing marker means the turn legitimately
    # ended ON the user: no violation, no strike, and the sanction is re-armed below so the
    # re-ask is permitted.
    pending_ask = find_marker(ASK_PENDING, bases)
    # THE NAME IS CHECKED HERE TOO. This hook is the FOURTH writer/reader of the sanction
    # pair, and taking either marker's mere existence as proof of a legitimate question would
    # let a self-minted gate ask_guard VOIDs buy the ask, simply by being asked in PROSE
    # instead of through AskUserQuestion. A gate no skill scripts suppresses nothing.
    stall_sanctioned = known_gate_marker(sanction) or known_gate_marker(pending_ask)
    # The pending register (see PENDING): written by pending_register.py when a tool call
    # actually put harness-tracked work in flight, and still within its staleness bound.
    pending_work = pending_backed(bases)
    # A Stop means this turn COMPLETED, which means any dropped-turn record beside this window
    # has been recovered — by the heartbeat, or by a human typing anything at all. Clearing it
    # here is the counterpart of flow_interrupt writing it on every StopFailure, and it is what
    # keeps a hand-resumed flow from being re-driven by a rescue it does not need.
    for _stale in find_markers(DROPPED, bases):
        try:
            _stale.unlink()
        except OSError:
            pass

    def _sweep(*names: str) -> None:
        """Remove ``names`` from EVERY location a flow marker can live.

        Closing only the directory the window was found in would strand its twin: a
        session-armed window plus a skill snippet that fell back to the agent directory
        leaves a sanction (or a second marker) behind, and the next flow inherits it.
        """
        for base in bases:
            for name in names:
                try:
                    (base / name).unlink()
                except OSError:
                    pass

    def _close_window(*extra: str) -> None:
        """Close the window everywhere it lives — and a suite's window takes its account claim."""
        _sweep(*extra, FLOW_MARKER, ASK_SANCTION, ASK_PENDING, PENDING, LEASE, DROPPED, COMMAND_RUN_ID)
        if "generate-suite" in (window, command):
            try:
                import _suite_window  # noqa: PLC0415 - hooks/ is on sys.path at call time
                from _workdir import resolve_workdir  # noqa: PLC0415

                _suite_window.release_flow(bases, resolve_workdir())
            except Exception:  # noqa: BLE001 - the claim expires by TTL; the close must not fail
                pass

    if not _ANCHOR_DATA:
        # THE GUARD HAS NO LANGUAGE. `anchors.json` did not load, and the loader already said
        # so loudly. Enforcement now degrades exactly as the 3-strike stand-down does — and
        # for the same reason: the anchor table is empty, so no message can carry an anchor,
        # so every turn end would block on the pending contract with a skeleton nobody can
        # render, and leaving the window armed converts "no enforcement" into "ask_guard
        # denies for the rest of the session". Close the window and get out of the way.
        _close_window(STRIKES)
        return model.none()
    strikes_path = workdir / STRIKES
    fresh, subagent_wait = _pending_fresh(bases)
    try:
        from _workdir import resolve_workdir  # noqa: PLC0415 - hooks/ is on sys.path at call time

        config_workdir = resolve_workdir()
    except Exception:  # noqa: BLE001 - no workdir reads no declaration; the stop stands
        config_workdir = None
    unresolved = _unresolved_prerequisites(bases, config_workdir)
    # A harness that starts no turn when background work ends (Codex) has nothing that resumes the session,
    # so it is told to poll in the foreground exactly as /analyze-traces and a headless run are.
    foreground_wait = command == "traces" or _headless(config_workdir) or not event.wakes_on_background
    # A headless session that ends with its turn: nothing started in the background wakes it, and the work dies.
    # The one decision pending_register reads too, so the guard cannot back a turn end the register refused.
    ends_with_turn = event.wakes_on_background and not _session_wakes(event, config_workdir)
    if unreadable:
        violations = [
            _Violation(
                _UNREADABLE_VIOLATION + " (re-send the final message; nothing it says can be checked until it is readable)",
                "re-send the final message; the guard could not read it.",
            )
        ]
    else:
        anchor_line, agent_head = _recorded_anchor(bases)
        violations = _violations(
            message,
            command,
            stall_sanctioned=stall_sanctioned,
            pending_backed=pending_work,
            pending_fresh=fresh,
            pending_subagent=subagent_wait,
            integrity_line=_recorded_integrity_line(bases),
            anchor_line=anchor_line,
            agent_head=agent_head,
            unscored_quotes=_unscored_quotes(bases, message) if _effective_command(message, command) == "generate-suite" else None,
            unresolved=unresolved,
            workspace=_workspace_roots(event),
            foreground_wait=foreground_wait,
            wakes=event.wakes_on_background and not ends_with_turn,
            stdin_polls=event.polls_shell_with_stdin,
            ends_with_turn=ends_with_turn,
            shell_read=_shell_read(event),
        )
    # A turn that answers the USER ends where the user put it. "Do the next step NOW" over a
    # user's own stop, or over the answer to their side question, overrules the person the
    # flow works for. Only the turn-end rules stand down, and the turn takes no strike for them.
    if violations and user_spoke_since(event, marker):
        violations = [v for v in violations if not v.startswith(_TURN_END_VIOLATIONS)]
    # A stop the model printed halted the run: no rule pushes it to go on, and the model gets one block, naming the
    # command, when its message is not the stop (the retry of that turn ends it, whatever it says).
    printed = _printed_stop(event, bases)
    if printed is not None:
        violations = _with_printed_stop(violations, message, command, printed, retried)
    # A turn the account's usage limit ended ends where the harness put it: no push, no strike.
    if violations and _usage_limited(event, marker):
        violations = [v for v in violations if not v.startswith(_LIMIT_STANDDOWN)]
    if not violations:
        # An UNANSWERED sanctioned ask re-arms its own gate: the sanction was consumed at
        # PreToolUse and the user never answered, so denying the re-ask would push the model
        # back toward answering for them — the failure this marker exists to stop.
        if pending_ask is not None:
            gate = pending_ask.read_text(encoding='utf-8', errors='replace').strip() or 'ask'
            try:
                from ask_guard import BREAKER_GATE, KNOWN_GATES
                if gate == BREAKER_GATE or gate not in KNOWN_GATES:
                    # A breaker allowance is NOT a sanctioned gate, and NEITHER IS AN
                    # UNKNOWN ONE. Re-arming either would launder it into a bearer token
                    # (allow → reject → re-arm → allow, default-allow for the rest of the
                    # window — found in review by an executed reproduction), and this hook
                    # would be a fourth `.ask_sanctioned` writer that bypasses the very
                    # allowlist the other three enforce. The rejected ask falls back to
                    # ask_guard's normal deny/teach path instead.
                    pending_ask.unlink()
                else:
                    (pending_ask.parent / ASK_SANCTION).write_text(gate, encoding='utf-8')
                    pending_ask.unlink()
                    _wait_page(event, bases)  # the turn ended on the user's unanswered question
            except OSError:
                pass
        elif stall_sanctioned and (_stall_hit(message) or _asked_and_awaiting(event, sanction)):
            _wait_page(event, bases)  # the turn ended on a sanctioned question, asked in words or by a turn-ending ask
            try:
                if event.turn_ending_ask and sanction is not None:
                    # The turn ended on the question, in words or by an ask the harness accepted: spend the
                    # sanction, and keep the gate as awaiting its reply, which is the next prompt
                    # (``ask_answered`` reads it there).
                    gate = sanction.read_text(encoding="utf-8", errors="replace").strip() or "ask"
                    (sanction.parent / ASK_PENDING).write_text(gate, encoding="utf-8")
                if sanction is not None:
                    sanction.unlink()
            except OSError:
                pass
        # The register is NOT consumed here — see PENDING. A passing turn says nothing
        # about whether the task it launched has finished, and unlinking on the pass
        # blocked the next turn while the runner was still going.
        try:
            strikes_path.unlink()
        except OSError:
            pass
        # Guard-side window close: the skills render the hand-off and STOP — the close
        # cannot live in the skill (it would run before this hook fires, un-arming the
        # validation). A clean message carrying the EFFECTIVE command's own anchor as a
        # skeleton line (or the failure hand-off) IS the flow's end; early exits are
        # swept by the SessionEnd hook. Two things are deliberately NOT a close: an
        # anchor belonging to a command this one DRIVES (``_effective_command`` never
        # re-targets there, so ``command`` is still the driver and its anchor is
        # absent), and an anchor word appearing in ordinary prose (position/shape).
        # Read WITHOUT the in-progress line, as _violations does: "Findings: in progress"
        # matched /simulation-fixes' bare "Findings" anchor and closed the window on a
        # message declaring the opposite — mid-flow, with the work still running.
        if _anchor_present(_without_progress_anchor(message, command), command) or _is_stop_text(message, command):
            _close_window()
            # A validated hand-off: a /simulate run records the stage (with file proof), halts on a
            # failure, or continues into its next stage.
            _record_headless_halt(message, command, bases)
            return _continuation(event, bases, "handoff", command=command, failure=_run_stop_kind(message, command, bases)) or model.none()
        return model.none()
    strikes = 0
    try:
        strikes = int(strikes_path.read_text(encoding="utf-8").strip() or "0")
    except Exception:
        strikes = 0
    # NOT while a PREREQUISITE is unresolved: ending on the run driver is always legal then, so there
    # is no wedge to escape, and a stand-down would sweep the window and with it the hold.
    if strikes >= MAX_STRIKES and not unresolved:
        # Stand down: enforcement degrades to prose rather than wedging the turn.
        # Close the WHOLE window, not just the strike counter: the message about to pass
        # unvalidated is the flow's de-facto end, and leaving the marker armed would silently
        # turn "enforcement off" into "ask_guard denies for the rest of the session" — a
        # stuck-open window.
        _close_window(STRIKES)
        return model.none()
    if (
        event.turn_ending_ask
        and not event.stop_hook_active
        and any(v.startswith(_STALL_VIOLATION) for v in violations)
        and all(v.startswith((_STALL_VIOLATION, _STRANDED_VIOLATION)) for v in violations)
    ):
        # THE ONE FREE BLOCK. A Codex flow asks by ending its turn on the question, so a model that
        # forgot the sanction is not drifting, it is asking the way Codex asks. The turn reads as a stall
        # and as stranded (nothing resumes it) at once, and three such blocks spend the strikes whose
        # stand-down closes the window and with it both guards. The retry of this block carries
        # ``stop_hook_active``, so at most one block per user turn is free, and a question that comes with
        # any other violation is not.
        return model.correct_stop(_unsanctioned_question_reason())
    # COURTESY violations do not spend the doctrine's strike budget. The stand-down above
    # is an off-switch for BOTH guards (it sweeps the window, so ask_guard stops denying
    # too), and it was sized for a model that cannot render an acceptable report. A missing
    # or under-promised wait clock is a politeness rule about ONE sentence; letting three of
    # them exhaust the budget would disarm the consent guard over phrasing — a new violation
    # class silently spending the safety of the old ones (found in review by an executed
    # reproduction). Such a turn is still BLOCKED and still taught; it just does not count.
    if not all(v.startswith(_COURTESY_VIOLATIONS) for v in violations):
        try:
            strikes_path.write_text(str(strikes + 1), encoding="utf-8")
        except OSError:
            pass
    return model.correct_stop(_reason(command, violations))
