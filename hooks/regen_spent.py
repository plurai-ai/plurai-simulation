#!/usr/bin/env python3
"""PostToolUse(generate_scenarios_start): spend the re-draw token, and record the inputs the
call actually sent. Run by `run.py` on every harness.

TWO jobs, both keyed on the same fact — a job was created — and that is why they share a hook.

The pair to ``regen_gate``'s PreToolUse check, and the same shape as ``ask_guard`` /
``ask_answered``: the guard decides, the companion records what actually happened.

WHY THE SPEND IS HERE. ``regen_gate`` is a PreToolUse hook: it runs BEFORE the guarded tool and
structurally cannot see the result. Spending there would spend the token on a call refused at
INPUT VALIDATION, with no job started, and deny the next legitimate attempt until a reason already
logged was logged again. A one-shot token that is spent by calls which never created anything is
a gate on typing, not on re-draws.

THE SPEND RULE, and its fail direction. The token is consumed only on POSITIVE evidence that a
job was created: a ``Job`` envelope came back carrying an id. Anything else — an error result,
an unparseable frame, a response this hook does not recognise — LEAVES the token, because the
two mistakes cost different amounts and the asymmetry is the same one ``regen_gate``'s own
docstring settles: a token wrongly kept costs at most one re-draw riding a reason that WAS
recorded and accepted, while a token wrongly spent wedges a live flow mid-build.

A job that was created and then FAILS is still a spend. What the reason bought was the batch,
and the batch was bought; the distinction this hook exists to draw is between a call that
started work and a call that never left the client.

THE INPUT-KEY BASELINE (`.gen_input_keys`) rides the same rule and for the same reason: only a
call that STARTED a generation is evidence about what this build sends. Recording at PreToolUse
would let a payload refused at input validation set the bar for every later call.

UNION, never replacement. The marker is the richest input set this build has successfully sent,
so a later call cannot lower the bar by sending less — which is precisely the failure it exists
to catch. The one way a document leaves it is the plugin's overrule of its
``plugin.regen.input_doc_dropped`` finding (``$WORKDIR/artifacts/.gate_overrules.jsonl``): a retired
document is subtracted, so the build stops expecting it. It is written to the WORKDIR (not only the session directory) because the case it must
survive is a compacted or resumed session: the source of truth has to be on disk, not in context.

THE SERVER-CHECK STAMP (`.server_input_union`) rides the same rule: a created job whose envelope carries
`detail.input_document_union` comes from a backend that refuses a dropped input document itself, and the
hook writes that backend's origin into the WORKDIR marker `regen_gate` reads. A created job without the
field removes the marker, so a backend rolled back to one without the check re-arms the local check.

Fail-open like every sibling: any fault here must leave the flow working.

THE RESULT'S FIELD differs by harness: Claude Code and Codex send ``tool_response`` (a JSON string
or a content-block object), Copilot sends ``tool_result`` (``result_type`` and the text the model
saw). Both are searched for the same Job envelope, and a Copilot result whose ``result_type`` is not
``success`` is a refusal.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).parent))

from _gen_inputs import (  # noqa: E402
    GEN_INPUT_KEYS_MARKER,
    SERVER_UNION_MARKER,
    current_backend_origin,
    input_doc_keys,
    overruled_documents,
    parse_marker,
    render_marker,
)
from _workdir import find_markers, marker_dirs, resolve_workdir  # noqa: E402

if TYPE_CHECKING:
    from _harness.model import Decision, Event

REGEN_TOKEN = ".regen_logged"
GUARDED_TOOL = "backend:generate_scenarios_start"

#: The Job envelope's identity field. Paired with a second envelope key so an arbitrary object
#: that merely has an `id` (an artifact echo, a validation error naming a field called "id")
#: cannot be read as a started job.
_JOB_ID_KEYS = ("id", "job_id", "jobId")
_JOB_COMPANION_KEYS = ("kind", "status", "phase")

#: The same pair, seen in the raw text of a content block — used only when the block is not
#: JSON this hook can parse. Deliberately narrow: it requires a NON-EMPTY id string.
_TEXT_JOB = re.compile(r'"(?:id|job_id|jobId)"\s*:\s*"[^"]+"')
_TEXT_COMPANION = re.compile(r'"(?:kind|status|phase)"\s*:')


def _looks_like_a_started_job(node: object, depth: int = 0) -> bool:
    """Whether ``node`` carries a Job envelope with an id.

    Walks a little, because the harness wraps an MCP result in a content-block list and some
    servers nest the payload one level further — but not far: the envelope is small, and a deep
    search is how an id quoted inside somebody's prose becomes a spend.
    """
    if depth > 4:
        return False
    if isinstance(node, dict):
        has_id = any(str(node.get(key) or "").strip() for key in _JOB_ID_KEYS)
        if has_id and any(key in node for key in _JOB_COMPANION_KEYS):
            return True
        return any(_looks_like_a_started_job(v, depth + 1) for v in node.values())
    if isinstance(node, list):
        return any(_looks_like_a_started_job(v, depth + 1) for v in node)
    if isinstance(node, str):
        try:
            return _looks_like_a_started_job(json.loads(node), depth + 1)
        except ValueError:
            return bool(_TEXT_JOB.search(node) and _TEXT_COMPANION.search(node))
    return False


#: The backend's statement that it checks dropped input documents, seen in raw text that is not JSON (Copilot
#: reports the text the model saw, which may hold the envelope twice).
_TEXT_UNION = re.compile(r'"input_document_union"\s*:\s*\[')


def _carries_server_union(node: object, depth: int = 0) -> bool:
    """Whether ``node`` holds a Job envelope whose ``detail`` carries the ``input_document_union`` list."""
    if depth > 4:
        return False
    if isinstance(node, dict):
        detail = node.get("detail")
        has_id = any(str(node.get(key) or "").strip() for key in _JOB_ID_KEYS)
        if has_id and isinstance(detail, dict) and isinstance(detail.get("input_document_union"), list):
            return True
        return any(_carries_server_union(v, depth + 1) for v in node.values())
    if isinstance(node, list):
        return any(_carries_server_union(v, depth + 1) for v in node)
    if isinstance(node, str):
        try:
            return _carries_server_union(json.loads(node), depth + 1)
        except ValueError:
            return bool(_TEXT_JOB.search(node) and _TEXT_UNION.search(node))
    return False


def _record_server_union(workdir: Path | None, payload: dict) -> None:
    """Write the current backend's origin into ``$WORKDIR/.server_input_union`` when the created job's envelope
    says the backend checks dropped input documents, and remove the marker when it does not.

    The WORKDIR only, never a directory it creates, written tmp+replace. A backend this hook cannot resolve
    writes nothing, so the local check stays.
    """
    if workdir is None or not workdir.is_dir():
        return
    path = workdir / SERVER_UNION_MARKER
    origin = current_backend_origin() if _carries_server_union(payload.get("tool_response", payload.get("tool_result"))) else ""
    if not origin:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_text(origin + "\n", encoding="utf-8")
    tmp.replace(path)


def _errored(response: object) -> bool:
    """An explicitly failed tool result. Checked before the id search, because a FastMCP error
    frame can quote the arguments it rejected — including an id the caller passed in."""
    if isinstance(response, dict):
        failed = response.get("result_type") not in (None, "success")
        return bool(response.get("is_error") or response.get("isError") or response.get("error") or failed)
    return False


def _created_a_job(payload: dict) -> bool:
    response = payload.get("tool_response", payload.get("tool_result"))
    if _errored(response) or payload.get("is_error") or payload.get("error"):
        return False
    return _looks_like_a_started_job(response)


def _record_input_keys(workdir: Path | None, sent: set[str]) -> None:
    """Union ``sent`` into the WORKDIR's baseline, minus every document the plugin has overruled as dropped.

    THE WORKDIR ONLY — never the session flow directory, unlike every other marker this hook
    touches. Those record facts about a SESSION; this records facts about a BUILD, and the two
    have different lifetimes in both directions. A session-scoped copy outlives a `manage-envs`
    switch, so the first-ever call in the new env is denied for "dropping" documents that belong
    to a different agent — fail-CLOSED, in a hook whose contract is to fail open. And a
    workdir-scoped copy is what survives the case this exists for: a compacted or resumed session,
    where the session id is gone but the build is not.

    Never creates a directory. A reader must not manufacture the thing it is testing for, and the
    cwd cascade can resolve to a git toplevel that has no ``plurai_sim/`` at all — writing there
    would litter an unrelated repo with a marker nothing will ever read.

    Written tmp+replace: the update is read-modify-write, so a torn write would leave a PARTIAL
    key list that parses cleanly with its last line lost — silently LOWERING the baseline, which
    is the one thing the union contract exists to prevent.

    Sending nothing recognisable (an argument-less frame, a payload shape this hook cannot read)
    records nothing rather than an empty baseline, so a frame it fails to parse cannot erase what
    earlier calls established.

    An unreadable overrule ledger subtracts nothing and says so: the baseline stays as high as the
    calls made it, and re-sending a document always satisfies the gate.
    """
    if not sent or workdir is None:
        return
    try:
        if not workdir.is_dir():
            return
        path = workdir / GEN_INPUT_KEYS_MARKER
        existing = parse_marker(path.read_text(encoding="utf-8", errors="replace")) if path.is_file() else set()
        merged = existing | sent
        try:
            merged -= overruled_documents(workdir, merged)
        except Exception as exc:  # noqa: BLE001 - an unreadable ledger (or a broken gate_ledger) subtracts nothing; the baseline still lands
            print(f"regen_spent: could not read the overrule ledger ({type(exc).__name__}: {exc}) — no retired document is subtracted.", file=sys.stderr)
        if merged == existing:
            return
        tmp = path.with_suffix(".tmp")
        tmp.write_text(render_marker(merged), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        # Not silent: with no baseline the input-drop gate is inert for the WHOLE build, which is
        # a strictly worse failure than the one-shot token this hook's other half can lose. The
        # flow still proceeds — every sibling fails open — but the operator gets a line saying the
        # guard is off rather than discovering it from a suite shipped against an empty world.
        print(f"regen_spent: could not record the generation input baseline ({exc}) — the input-drop gate is INERT for this build.", file=sys.stderr)


def handle(event: Event) -> Decision:
    """Spend the re-draw token and record the sent inputs when the call started a job; never decides."""
    from _harness import model

    if event.tool != GUARDED_TOOL:
        return model.none()
    if not _created_a_job(event.raw):
        return model.none()  # nothing was started: the reason is still unspent, and the retry is free
    # ONE-SHOT: spend every copy, so the next re-draw states its own reason rather than
    # riding this one's. Cleared in every marker dir, like a released lease.
    for token in find_markers(REGEN_TOKEN, marker_dirs(event.raw)):
        try:
            token.unlink()
        except OSError:
            pass
    # SEPARATE try, and deliberately so: the two halves of this hook fail in OPPOSITE
    # directions. An unspent token costs one denied re-draw and says so; an unrecorded
    # baseline turns the input-drop gate off for the whole build in silence. Sharing one
    # handler would let one bury the other.
    try:
        _record_input_keys(resolve_workdir(), input_doc_keys(event.tool_input))
    except Exception as exc:  # noqa: BLE001 - fail open, but never silently
        print(f"regen_spent: input baseline not recorded ({type(exc).__name__}: {exc}) — the input-drop gate is INERT for this build.", file=sys.stderr)
    # A third try for the same reason: a stamp that cannot be written leaves the local check on, which is safe.
    try:
        _record_server_union(resolve_workdir(), event.raw)
    except Exception as exc:  # noqa: BLE001 - fail open: without the stamp the local input-drop check stays on
        print(f"regen_spent: server-check stamp not recorded ({type(exc).__name__}: {exc}) — the local input-drop check stays on.", file=sys.stderr)
    return model.none()
