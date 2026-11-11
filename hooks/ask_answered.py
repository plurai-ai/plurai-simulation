#!/usr/bin/env python3
"""PostToolUse(AskUserQuestion): clear the in-flight marker when the user actually answered.

The pair to ``ask_guard``'s ``.ask_pending`` write. ``ask_guard`` runs at PreToolUse and consumes
the one-shot sanction there, so by the time the user responds the sanction is gone — and if they
REJECT the tool (to clarify, to change the question, to think), the Stop hook saw no sanction, no
harness-tracked work, and a question-shaped message, and reported the turn as a mid-flow stall with
"do the next step NOW … no questions".

Observed live on the corpus-scope gate: the model complied by fabricating the user's answer — it
wrote "Got it — 20 furniture products" when no user message contained that number, and uploaded a
20-record corpus that became the entire product universe for every scenario in the suite.

So: an ANSWERED call clears the marker (the normal path, unchanged behaviour downstream), and an
unanswered one leaves it standing for ``report_guard`` to read as "this turn ended on the user".

IT IS ALSO THE ONE PLACE THE PRODUCT KNOWS. ``ask_guard`` writes the two typed run driver findings
inside ``_headless_deny``, so interactively a sanctioned gate is consumed, ALLOWED and — answered
or not — nothing typed is written by anyone; ``build_run_json._run_driver_block`` then returns
``None``, which its own docstring calls a CLAIM, *"there was a person here"*. That claim was false
on every round whose question nobody answered. A PreToolUse hook that ALLOWS cannot hand over a
default, so there is no interactive site that could attest to one — but whether the call came back
answered is exactly the fact that IS observable here, so both outcomes file it
(``_run_driver.record_ask_outcome``). What the model then decided is not this hook's to say.

Whether a call was answered is read from its result by the sending harness's answer reader
(``_harness/answers.py``) and arrives as ``Event.answered``; this hook is harness-neutral.

Fail-open like its siblings: any fault here must leave the flow working, never deny or block.

CODEX. A Codex flow asks and ends its turn on the question, and the async ask tool returns only that the
question was shown, so no tool result says whether anyone answered. The registry runs this hook on
``UserPromptSubmit`` there instead: a prompt a person typed while a gate awaits its reply IS the answer.
A gate awaits its reply while ``.ask_pending`` stands (``report_guard`` writes it when the turn ends on
the question) or a sanction is still live (the reply can reach the hook before the turn ends). Recording
the answer clears both, so a replayed prompt files nothing. "Answered" there means only that a person
replied, which is also all the Claude Code and Copilot readers claim.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from _harness import model

if TYPE_CHECKING:
    from _harness.model import Decision, Event

ASK_PENDING = ".ask_pending"
ASK_SANCTION = ".ask_sanctioned"
#: Where the armed command's name is, for the ``command`` field of the record. Same fallback as
#: ``ask_guard``'s, so one gate cannot be filed under two spellings of "no window found".
FLOW_MARKER = ".plurai_flow_active"
UNNAMED_COMMAND = "plurai-simulation"


def _sanctioned_gate(marker: Path) -> str | None:
    """The GATE this in-flight ask was sanctioned for, or None when the marker names none.

    ``.ask_pending`` has a second writer: the DENY-LOOP BREAKER, whose content is deliberately not
    a gate name. ``report_guard`` already refuses to promote that one (re-arming it would launder a
    breaker allowance into a bearer token), and the same line is drawn here for the record's own
    sake — a finding minted from it would publish ``deny-loop-breaker``, or any stale hand-written
    name, in ``run.json`` as a gate some skill scripts.
    """
    try:
        gate = marker.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    try:
        from ask_guard import BREAKER_GATE, KNOWN_GATES  # noqa: PLC0415 - hooks/ is on sys.path here
    except Exception:  # noqa: BLE001 — without the allowlist nothing may be classified as a gate
        return None
    return None if gate == BREAKER_GATE or gate not in KNOWN_GATES else gate


def _armed_command(base: Path) -> str:
    """The command whose window this ask was asked inside — ``ask_guard``'s own read of line 1."""
    try:
        lines = (base / FLOW_MARKER).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return UNNAMED_COMMAND
    return (lines[0].strip() if lines else "") or UNNAMED_COMMAND


#: An answer that accepts what the question proposed.
_APPROVES = re.compile(r"^\W*(approve|yes|accept|confirm)", re.IGNORECASE)


def _scope_row(gate: str, event: Event | None, answered: bool) -> dict | None:
    """What an answered ask settled about the items its question named: the question text, the answer, and — only
    when the answer approves — the typed ``inputs`` / ``switches`` / ``tools`` the scan proposed for ``gate``. None
    when ``gate`` carries no scope (``_run_driver.SCOPE_GATES``: a ``blocker`` never does) or the scan proposed nothing
    for it. The record is what lets ONE approval back exactly the items it named."""
    if event is None:
        return None
    import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path at call time
    from _workdir import resolve_workdir  # noqa: PLC0415

    cwd = event.raw.get("cwd") if isinstance(event.raw, dict) else None
    workdir = resolve_workdir(Path(cwd) if isinstance(cwd, str) and cwd else None)
    proposal = _run_driver.proposed_scope(gate, workdir / "artifacts") if workdir else None
    if proposal is None:
        return None
    questions = [q for q in (event.tool_input or {}).get("questions") or [] if isinstance(q, dict)]
    question = "\n".join(str(q.get("question") or "") for q in questions)
    response = event.raw.get("tool_response") if isinstance(event.raw, dict) else None
    answers = response.get("answers") if isinstance(response, dict) and isinstance(response.get("answers"), dict) else {}
    answer = str(answers.get(questions[0].get("question")) if questions and answers else "") if answered else ""
    approved = bool(answered and _APPROVES.search(answer))
    row: dict = {"question": question, "answer": answer, "approved": approved, "art": str(workdir / "artifacts")}
    if approved:
        row.update(proposal)
    return row


#: Every answered question, verbatim as the ask tool returned it: `{"at", "question", "answer"}` rows in `<workdir>/artifacts`,
#: the one record a validator reads to tell the user's answer from the model's (`writer_readers.recorded_answers`, which
#: names the same file). Never swept: a re-scan reuses what it backs. Only the answers the ask tool returns as an
#: `answers` mapping are recorded here; another harness's answer leaves no row.
ASK_ANSWERS = ".ask_answers.jsonl"


def _record_answers(event: Event) -> None:
    """Append each question of an answered call with its answer, read from the tool's own input and result, never from
    model prose. Durable beside the workdir (a later session's validator reads it); written only where the workdir exists."""
    try:
        from _workdir import resolve_workdir  # noqa: PLC0415

        response = event.raw.get("tool_response") if isinstance(event.raw, dict) else None
        answers = response.get("answers") if isinstance(response, dict) else None
        if not isinstance(answers, dict):
            return
        cwd = event.raw.get("cwd")
        workdir = resolve_workdir(Path(cwd) if isinstance(cwd, str) and cwd else None)
        if workdir is None or not workdir.is_dir():
            return
        now = time.time()
        rows = [json.dumps({"at": now, "question": q["question"], "answer": answers[q["question"]]}, ensure_ascii=False)
                for q in (event.tool_input or {}).get("questions") or []
                if isinstance(q, dict) and isinstance(q.get("question"), str) and isinstance(answers.get(q["question"]), str)]
        if rows:
            (workdir / "artifacts").mkdir(exist_ok=True)
            with (workdir / "artifacts" / ASK_ANSWERS).open("a", encoding="utf-8") as fh:
                fh.write("".join(f"{row}\n" for row in rows))
    except Exception:  # noqa: BLE001 - a record is not worth a broken flow; no row backs nothing
        return


def _record_outcome(markers: list[Path], *, answered: bool, event: Event | None = None) -> None:
    """File what became of the sanctioned ask these markers stand for.

    Beside the marker that named the gate, which is where ``ask_guard`` wrote it and where every
    sweep and every reader already look. One record per ask, not per marker copy: the gate is one
    gate however many directories the window spans.

    Fail-open in its OWN body rather than through the caller's: a fault here must not skip the
    unlink below, or an ANSWERED ask would leave ``.ask_pending`` standing and ``report_guard``
    would read every later turn of the flow as ending on the user.
    """
    try:
        import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path at call time

        for marker in markers:
            gate = _sanctioned_gate(marker)
            if gate is None:
                continue
            row = _scope_row(gate, event, answered)
            if row is None:
                _run_driver.record_ask_outcome(
                    marker.parent, gate=gate, command=_armed_command(marker.parent), answered=answered)
                return
            # Durable too: a later session's validator reads `<workdir>/artifacts`, never this session's flow dir.
            art = Path(row.pop("art"))
            _run_driver.record_durably(_run_driver.ASK_ANSWERED if answered else _run_driver.ASK_UNANSWERED, session_base=marker.parent, art=art,
                                     gate=gate, command=_armed_command(marker.parent), **row)
            return
    except Exception:  # noqa: BLE001 — a record is not worth a broken flow
        return


def _stop_waiting(event: Event, bases: list[Path]) -> None:
    """The overview page's step no longer says the flow waits for an answer (``simulate_continue.wait_page``). Never raises."""
    try:
        import simulate_continue  # noqa: PLC0415 - hooks/ is on sys.path at call time

        simulate_continue.wait_page(event, bases, False)
    except Exception:  # noqa: BLE001 - the next fill rewrites the step anyway
        pass


def _live_sanctions(bases: list[Path]) -> list[Path]:
    """The sanctions of ``bases`` still inside their TTL. An unreadable age counts as live, as in ``ask_guard``."""
    from _workdir import find_markers  # noqa: PLC0415

    try:
        from ask_guard import SANCTION_TTL_SECONDS  # noqa: PLC0415 - hooks/ is on sys.path here
    except Exception:  # noqa: BLE001 - without the TTL no sanction can be called live
        return []
    live = []
    for path in find_markers(ASK_SANCTION, bases):
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            age = 0.0
        if age <= SANCTION_TTL_SECONDS:
            live.append(path)
    return live


def _typed_by_a_person(prompt: object) -> bool:
    """Whether ``prompt`` is words a person typed: not a heartbeat, a tag-wrapped harness block or a flow command.

    False when the judge cannot be loaded. A false "answered" lets the model pass off its own reply as the
    user's, which costs more than the re-ask a false "unanswered" costs.
    """
    if not isinstance(prompt, str):
        return False
    try:
        from report_guard import _human_typed  # noqa: PLC0415 - hooks/ is on sys.path here
    except Exception:  # noqa: BLE001 - see the docstring
        return False
    return _human_typed(prompt)


def settle_answered(bases: list[Path]) -> bool:
    """Record the gate that awaits its reply as answered and clear its markers. False when none awaits.

    The one place a Codex answer is recorded, whichever path carried it (the reply prompt, or the answer
    row the transcript shows). Clearing the markers is what makes a second path find nothing to record.
    """
    from _workdir import find_markers  # noqa: PLC0415

    awaiting = [*find_markers(ASK_PENDING, bases), *_live_sanctions(bases)]
    if not awaiting:
        return False
    _record_outcome(awaiting, answered=True)
    for marker in [*find_markers(ASK_PENDING, bases), *find_markers(ASK_SANCTION, bases)]:
        try:
            marker.unlink()
        except OSError:
            pass
    return True


def settle_in_turn_reply(event: Event) -> bool:
    """Settle a gate whose answer reached the model inside the turn, as the transcript shows (Codex).

    The async ask can be answered while the turn runs: the answer is a row in the transcript that no hook
    event carries. It counts when it is newer than every marker that says the gate awaits, so an answer to
    an earlier question is not taken for this one's.
    """
    from _harness import transcript  # noqa: PLC0415
    from _workdir import find_markers, marker_dirs  # noqa: PLC0415

    bases = marker_dirs({"session_id": event.session_id})
    awaiting = [*find_markers(ASK_PENDING, bases), *_live_sanctions(bases)]
    if not awaiting:
        return False
    try:
        asked_at = max(path.stat().st_mtime for path in awaiting)
    except OSError:
        return False
    if not transcript.gate_reply_since(event.harness, event.transcript_path, asked_at):
        return False
    return settle_answered(bases)


def _on_reply(event: Event) -> Decision:
    """A prompt while a gate awaits its reply (Codex): record the gate answered, once, and clear its markers."""
    from _workdir import find_markers, marker_dirs  # noqa: PLC0415

    bases = marker_dirs({"session_id": event.session_id})
    # Every prompt of every Codex session reaches here; the judge of what a person typed is a large
    # module, so it is loaded only when there is a gate whose reply this could be.
    if not (find_markers(ASK_PENDING, bases) or _live_sanctions(bases)):
        return model.none()
    if _typed_by_a_person(event.raw.get("prompt")):
        settle_answered(bases)
    return model.none()


def handle(event: Event) -> Decision:
    """Record the outcome of an ask and clear its in-flight marker when it was answered. Never a decision."""
    if event.kind == "user_prompt_submit":
        return _on_reply(event)
    # Imported inside the body, like every sibling hook: at module scope a broken or missing
    # _workdir would be an unhandled ImportError, which is neither fail-open nor silent.
    from _workdir import find_markers, marker_dirs  # noqa: PLC0415

    bases = marker_dirs({"session_id": event.session_id})
    markers = find_markers(ASK_PENDING, bases)
    # BOTH outcomes, BEFORE the unlink: the answered one is what retires an earlier "nobody
    # answered" for the same gate, and after the unlink there is nothing left to read the gate
    # name out of.
    _record_outcome(markers, answered=event.answered, event=event)
    # The question is back from the user, answered or not: the overview page stops saying it waits. Unanswered, the model
    # may carry on (a cancelled or unavailable answer); a turn that ends on the user turns it on again (report_guard).
    _stop_waiting(event, bases)
    if not event.answered:
        return model.none()  # leave the marker standing: report_guard reads it as "ended on the user"
    _record_answers(event)
    for marker in markers:
        try:
            marker.unlink()
        except OSError:
            pass
    return model.none()
