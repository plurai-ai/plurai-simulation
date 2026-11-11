#!/usr/bin/env python3
"""A failed backend call or a failed poll tells the model, at that moment, to say nothing about it and what to do next.

PostToolUse on the plugin backend's tools and the shell, and on Claude Code also PostToolUseFailure, run by `run.py`.
Claude Code reports a tool error (an MCP `Error POSTing to endpoint`, a non-zero shell exit) as `PostToolUseFailure` and
a failed job envelope as an ordinary `PostToolUse`. Codex has no failure event and Copilot's `PostToolUse` carries a
failed result as `result_type: failure`; both are read from the same result text. Whether Copilot passes a PostToolUse
`additionalContext` to the model is not confirmed: where a harness ignores it, nothing is lost.

WHY HERE. The Stop hook sees only a turn's final message, and text that has streamed between tool calls cannot be unsaid
("I'm now preparing the stop message…", "The backend isn't answering, so I'm stopping the run here", "I'm waiting briefly
and then retrying once"). Those sentences are written right after a failed result, so the instruction goes in with it.

The kinds, and the one next step each names (the error posture holds the rules; this repeats only the step):

* a typed capacity, unavailable or credit refusal: the refusal stop (`stop_message.py refusal --refusal`);
* a job the backend interrupted (`interrupted`, `watchdog`): one wait, then the same call once more;
* any other failed job envelope: fix an input this side can change and re-run once, silently, else the contact hand-off.
  The hook cannot choose the hand-off for these, because fix-first needs the model's judgement of the error;
* a transport failure (`Error POSTing to endpoint`, `HTTP 502/503/504`, a timeout): one wait, the same call again once,
  and a second failure is the unavailable stop;
* `poll_job.sh` exiting 69: the step its own last lines name.

A result that is none of these (a validation error, an ordinary shell failure, a finished job) decides nothing, and so
does a client error (`_client_error`), whatever transport words it carries: an outage wait would only delay the
version-refusal or login answer it needs. Scoped: the gate starts this hook only while a flow window or a /simulate plan
is open. Open: a result it cannot read decides nothing.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).parent))
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

if TYPE_CHECKING:
    from _harness.model import Decision, Event

SAY_NOTHING = "Say nothing about this failure, before or after the next step."

_POLL_UNREACHABLE = "Plurai could not be reached, so the job's state is unknown"
_POLL_STOP = re.compile(r"poll_job: run python3 .*stop_message\.py\"? unavailable ")
_SHELL_TRANSPORT = re.compile(r"\bfailed: HTTP 50[234]\b")
_TRANSPORT = re.compile(
    r"Error POSTing to endpoint|upstream unavailable|\bHTTP 50[234]\b|\btimed out\b|connection (?:reset|refused|closed)", re.IGNORECASE
)
#: The response's own HTTP status, as the MCP client spells it before the body (`Error POSTing to endpoint (HTTP 426): …`).
_ENDPOINT_STATUS = re.compile(r"Error POSTing to endpoint \(HTTP (\d{3})\)", re.IGNORECASE)
#: An HTTP status anywhere on a line, read from the text's first line when no endpoint status is there.
_HTTP_STATUS = re.compile(r"\bHTTP (\d{3})\b", re.IGNORECASE)
#: The `error` of the version refusal's and the login's bodies.
_CLIENT_REFUSALS = frozenset({"client_upgrade_required", "unauthenticated"})
_INTERRUPTED = ("interrupted", "watchdog")
_RESULT_KEYS = ("error", "tool_response", "tool_result")


def _strings(node: Any, out: list[str], depth: int = 0) -> None:
    """Every string in ``node``: a result is a string, a content-block list or an object with the text in a field."""
    if depth > 5:
        return
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for value in node.values():
            _strings(value, out, depth + 1)
    elif isinstance(node, list):
        for value in node:
            _strings(value, out, depth + 1)


def _result_text(raw: dict[str, Any]) -> list[str]:
    pieces: list[str] = []
    for key in _RESULT_KEYS:
        _strings(raw.get(key), pieces)
    return pieces


def _objects(pieces: list[str]) -> list[dict[str, Any]]:
    """The JSON objects the result holds: a piece that is one, and objects after a prefix such as `Error POSTing …:`."""
    found: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    for piece in pieces:
        for match in list(re.finditer(r"\{", piece))[:6]:
            try:
                value, _end = decoder.raw_decode(piece[match.start() :])
            except ValueError:
                continue
            if isinstance(value, dict):
                found.append(value)
                break
    return found


def _word(script: str) -> str:
    from plugin_root import shell_path  # noqa: PLC0415 - a sibling module

    return shell_path(_SCRIPTS / script)


def _stop_message(subcommand: str, *arguments: str) -> str:
    return f"`python3 {_word('stop_message.py')} {subcommand} {' '.join(arguments)}`"


def _wait_then(step: str) -> str:
    return f"run `bash {_word('wait.sh')} 45 --quiet`, then {step}"


def _stop_message_module():
    """`scripts/stop_message.py`, with scripts/ on `sys.path`: only a failed result pays for the import."""
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    import stop_message  # noqa: PLC0415

    return stop_message


def _refusal_command(refusal: dict[str, Any] | None) -> str:
    """The refusal stop's command with the typed fields of this result filled in (`stop_message.typed_refusal`: backend text
    never reaches the model's context). When the refusal cannot be rebuilt the argument stays a placeholder. A credit kind
    that is the contact text takes no `--continue`; every other credit kind needs the user's own command."""
    stop_message = _stop_message_module()
    typed = stop_message.typed_refusal(refusal)
    payload = "'" + json.dumps(typed, ensure_ascii=True) + "'" if typed is not None else "<this result's JSON>"
    credit = stop_message.credit_block(typed) if typed is not None else None
    needs_continue = typed is None or (credit is not None and credit[0] != stop_message.CREDIT_CONTACT)
    continue_argument = ' --continue "<the user\'s command>"' if needs_continue else ""
    return _stop_message("refusal", f"--refusal {payload}{continue_argument}", '--done "<outcome>"')


def _said_last(stop: str) -> str:
    return (f"run {stop}. In a /simulate run, then publish the overview page silently, as the error posture's stop "
            "paragraph says. Your final message is what that command printed, verbatim, then the page's link line if there "
            "is one, and nothing else: no sentence about the stop before it, and it is said once.")


def _notice(kind: str, refusal: dict[str, Any] | None = None) -> str:
    unavailable = _stop_message("unavailable", '--done "<outcome>"')
    contact = _stop_message("contact", '--done "<outcome>"')
    if kind == "refusal":
        return f"{SAY_NOTHING} Next: {_said_last(_refusal_command(refusal))}"
    if kind == "retry":
        return (f"{SAY_NOTHING} Next: {_wait_then('run the same call once more')}. A second failure is the error posture's: "
                f"fix what this side can change, else run {contact} and say its output alone.")
    if kind == "poll_stop":
        return f"{SAY_NOTHING} Next: {_said_last(unavailable)}"
    if kind == "poll":
        return (f"{SAY_NOTHING} Next: the step the last lines above name: wait once, run it again once, and a second failure "
                f"is the stop: {_said_last(unavailable)}")
    if kind == "transport_write":
        return f"{SAY_NOTHING} A call that writes is never made twice, so this is the stop: {_said_last(unavailable)}"
    if kind == "transport":
        return (f"{SAY_NOTHING} Next: {_wait_then('make the same call again once, with the same arguments')}. If it fails again, "
                f"that is the stop: {_said_last(unavailable)}")
    return (f"{SAY_NOTHING} Next: if an input you can change names the cause, fix it and re-run once, silently. "
            "If it needs something only the user can supply, ask once, as the error posture's item 2 says. "
            "If this skill's error table marks the failure as retried once, retry once. "
            f"Otherwise that is the stop: {_said_last(contact)}")


_READ_PREFIXES = ("get_", "list_", "fetch_", "read_", "download_", "describe_")


def _may_recall(verb: str) -> bool:
    """Whether a failed call of backend tool ``verb`` may be made once more: a read, or a `*_start`, which the backend
    answers with the first call's job. Every other call writes, and a re-send is a second edit, commit or paid score."""
    return verb.endswith("_start") or verb == "job_status" or verb.startswith(_READ_PREFIXES)


def _transport_kind(verb: str | None) -> str:
    return "transport" if verb is None or _may_recall(verb) else "transport_write"


def _backend_kind(pieces: list[str], verb: str) -> tuple[str, dict[str, Any] | None] | None:
    """``(kind, refusal)`` of a failed backend result: the refusal is the typed object a refusal stop is built from. A typed
    block wins over `terminal_reason`: the wire stays additive, so an unavailable job reports `internal_error` (or any
    reason) beside its `unavailable` block, and that is the unavailable stop, never a fault or a retry."""
    stop_message = _stop_message_module()
    objects = _objects(pieces)
    for obj in objects:
        if stop_message.is_capacity(obj) or stop_message.is_unavailable(obj) or stop_message.credit_block(obj) is not None:
            return "refusal", obj
    for obj in objects:
        if obj.get("status") == "failed":
            return ("retry" if obj.get("terminal_reason") in _INTERRUPTED else "other"), None
    text = "\n".join(pieces)
    if _TRANSPORT.search(text) and not _client_error(text, objects):
        return _transport_kind(verb), None
    return None


def _client_error(text: str, objects: list[dict[str, Any]]) -> bool:
    """Is this failure the caller's to fix, never an outage, whatever transport words it carries? The response's own
    status decides: the endpoint's `(HTTP nnn)`, else an `HTTP nnn` on the text's first line, else a body's top-level
    RFC 9457 `status`, else the version refusal's or the login's `error`. A status line wins over the body on purpose: a
    gateway's 5xx can wrap an upstream 4xx body, and that is still an outage. A 4xx mentioned anywhere else (a nested
    field, an earlier attempt on another line) does not unmake a 5xx. The version-refusal and login handling answer a
    client error, so this hook decides nothing for one."""
    status = _ENDPOINT_STATUS.search(text) or _HTTP_STATUS.search(text.lstrip().split("\n", 1)[0])
    if status is not None:
        return status.group(1).startswith("4")
    return any(
        (isinstance(obj.get("status"), int) and 400 <= obj["status"] < 500) or obj.get("error") in _CLIENT_REFUSALS
        for obj in objects
    )


_MCP_HELPER_CALL = re.compile(r"mcp_call\.py\s+['\"]?(\w+)")


def _shell_kind(pieces: list[str], command: str) -> str | None:
    text = "\n".join(pieces)
    if _POLL_STOP.search(text):
        return "poll_stop"
    if _POLL_UNREACHABLE in text:
        return "poll"
    if not _SHELL_TRANSPORT.search(text):
        return None
    called = _MCP_HELPER_CALL.search(command)
    return _transport_kind(called.group(1) if called else None)


def handle(event: Event) -> Decision:
    from _harness import model  # noqa: PLC0415 - the codec package sits beside this file

    try:
        tool = event.tool or ""
        pieces = _result_text(event.raw)
        if not pieces:
            return model.none()
        command = str((event.raw.get("tool_input") or {}).get("command") or "") if isinstance(event.raw.get("tool_input"), dict) else ""
        if tool.startswith("backend:"):
            found = _backend_kind(pieces, tool.split(":", 1)[1])
        else:
            found = _shell_kind(pieces, command) if tool == "shell" else None
        if found is None:
            return model.none()
        kind, refusal = found if isinstance(found, tuple) else (found, None)
        return model.context(_notice(kind, refusal))
    except Exception:  # noqa: BLE001 - a result this hook cannot read decides nothing
        return model.none()
