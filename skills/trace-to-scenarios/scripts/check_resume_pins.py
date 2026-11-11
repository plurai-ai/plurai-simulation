#!/usr/bin/env python3
"""Judge every staged `resume_pin.json` the way the backend will, before anything is uploaded.

A pin whose history stops mid-turn cannot be resumed from. The backend knows this and refuses
it — but only after the bundle is uploaded and a generation job has started, so the customer
pays for the refusal and reads it as a failed folder in a job report. The same verdict is
computable from the staged files with no network and no model, which is what this does.

The rule mirrors the backend's normalization exactly, in its order. A history ending on a USER
message is fine: that message is lifted out to become the scripted opener, and whatever precedes
it is the seed. What is left must then end on a COMPLETED agent turn, because the seeded
conversation has to hand the turn to the user simulator. That is an agent message, or the tool
results of the agent's last call when a user message was lifted: many agents close a reply with a
status call, and the user speaking next is the proof the reply was over. Those results must
answer the agent message right before them, all of it. A trailing tool result with no user
message after it means the export was captured between the tool returning and the agent replying
— the agent's own turn was never recorded, so resuming there drops it, and every judge reads that
missing reply as the agent ignoring its own tool result.

A seed whose tail passes is then read whole, as the backend reads it. Every tool call has one
result after it, every result answers a call an earlier agent message made, no call reuses the
id of a call still waiting on its result, and nothing but tool results sits between a call and its
result: a history carrying half an exchange, or splitting one, is a hard 400 on the agent's first
live call. At least one message carries text or a tool call. Message ids are unique
across the whole pin, judged before the lift, because the backend's pin model checks them first.

These are the backend's published seed rules, one for one and deliberately no stricter.
`SEED_SHAPE_RULES_MIRRORED` names them by the ids of the vendored `resume_seed_shape` contract,
and a test holds the two sets equal.

The declared `schema_version` is judged first. Ingest refuses a foreign version outright rather
than reading a missing key as an absent value, and so does this: a pin written before the current
schema carries no `resume_point` at all, and the difference between "no start node was pinned"
and "this pin predates start nodes" is not visible in the file — only in the version above it.

`resume_point` is judged for shape only, and echoed on the passing line when it names a node. A
pinned start node is invisible at every later stage: nothing between the converter and the harness
reads it, so a malformed one is dropped in silence and the scenario scores a full replay as the
pinned resume. Seeing the node named here is the only offline confirmation that `--start-node` took.

Nothing here rewrites a pin. Trimming a transcript to make it resumable changes what the
conversation was, and that is the customer's call to make explicitly — on the export, before
conversion, not on a generated artifact behind their back.

Output is structural only: folder names, message indices, types and counts. Conversation text
never reaches stdout, so this is safe to run and paste under any posture.

Usage:

    python3 check_resume_pins.py <bundle-dir>

Exit codes: 0 every pin resumes cleanly, 2 at least one pin the backend will refuse (or a file
that cannot be read).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PIN_NAME = 'resume_pin.json'
HUMAN, AI, TOOL = 'human', 'ai', 'tool'

#: The one pin schema ingest accepts. Anything else is refused whole, before its messages are
#: read, so this check refuses it in the same place for the same reason.
PIN_SCHEMA_VERSION = 2

#: The ``resume_seed_shape`` rule ids the backend publishes, each implemented by :func:`judge`, and
#: the rule-set version they come from. A backend on a higher version refuses pins this check passes.
SEED_SHAPE_RULES_MIRRORED: frozenset[str] = frozenset(
    {
        'ends_on_completed_agent_turn',
        'tool_tail_closes_last_call',
        'tool_result_follows_its_call',
        'tool_call_has_result',
        'tool_result_once',
        'open_call_ids_unique',
        'tool_results_adjacent',
        'seed_has_content',
        'message_ids_unique',
    }
)
SEED_SHAPE_RULES_VERSION = 2


class PinRefused(Exception):
    """A pin the backend will refuse. The message is the operator-facing reason."""


def require_schema_version(pin: dict[str, Any]) -> None:
    """Raise unless the pin declares the schema ingest reads.

    Judged before every other rule, because the rules below read keys whose ABSENCE only carries
    meaning at this version. An older pin has no ``resume_point`` at all, and taking that missing
    key for "the operator pinned no start node" prints a clean OK over a folder ingest rejects
    on the version alone — the exact outcome this check exists to reach first.
    """
    version = pin.get('schema_version')
    if version != PIN_SCHEMA_VERSION:
        raise PinRefused(
            f'the pin declares schema_version {version!r} and ingest accepts only '
            f'{PIN_SCHEMA_VERSION}, so this folder is refused whole whatever its history holds — '
            're-convert the trace with the current SDK, which writes the schema the backend reads')


def flatten(content: Any) -> str:
    """The text of a message, for the one place a blank one matters: a lifted opener.

    Mirrors the backend's flattener — string content as-is, text out of a block list, non-text
    blocks dropped — because the value being judged is whether the backend finds an opener here.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b if isinstance(b, str) else b.get('text') for b in content
                 if isinstance(b, str) or (isinstance(b, dict) and isinstance(b.get('text'), str))]
        return '\n'.join(p for p in parts if p)
    return '' if content is None else str(content)


def call_ids(message: dict[str, Any]) -> list[Any]:
    """The ids of the tool calls a message makes, in order. A malformed call counts as id ``None``."""
    return [call.get('id') if isinstance(call, dict) else None for call in message.get('tool_calls') or []]


def duplicate_message_id_problem(messages: list[dict[str, Any]]) -> str | None:
    """Rule ``message_ids_unique``: why two messages share an id, or ``None``.

    The backend's reader reconciles messages by id, so two sharing one collapse into a single
    message and the seeded history is short a turn with nothing reporting it.
    """
    seen: dict[Any, int] = {}
    for index, message in enumerate(messages):
        message_id = message.get('id')
        if message_id in seen:
            return (f'messages[{index}].id {message_id!r} duplicates messages[{seen[message_id]}] — '
                    'add_messages reconciles by id, so the two would collapse into one')
        seen[message_id] = index
    return None


def unpaired_tool_call_problem(seed: list[dict[str, Any]]) -> str | None:
    """Why the seed carries a broken tool exchange, read over the whole seed, or ``None``.

    Four rules, in the backend's order and words: a result answers a call an earlier message made
    (``tool_result_follows_its_call``), a call takes one result (``tool_result_once``), no call
    reuses the id of an earlier call that is still open (``open_call_ids_unique``), and every call
    has a result after it (``tool_call_has_result``). A later message that reuses an answered id
    makes a new call, which takes one more result. A fifth, ``tool_results_adjacent``, is reported
    only when those four hold: no message but a tool result sits between a call and its result.
    """
    requested: dict[Any, int] = {}
    unanswered: dict[Any, int] = {}
    interrupted: str | None = None
    for index, message in enumerate(seed):
        if unanswered and interrupted is None and message.get('type') != TOOL:
            call_id, at = next(iter(unanswered.items()))
            interrupted = (f'messages[{index}] is a {message.get("type")} message between tool call {call_id!r} '
                           f"(messages[{at}]) and its result — a provider accepts a call's results only directly after "
                           'it; re-convert the pin from its source')
        if message.get('type') == TOOL:
            call_id = message.get('tool_call_id')
            if call_id not in requested:
                return (f'messages[{index}] carries the result of tool call {call_id!r}, which no earlier agent message '
                        'asks for — the trace appears to have been captured mid-turn, from a point after the request '
                        'that produced this result')
            if call_id not in unanswered:
                return (f'messages[{index}] carries a second result for tool call {call_id!r}, which '
                        f'messages[{requested[call_id]}] asks for once and an earlier message already answers — a call '
                        'takes exactly one result')
            unanswered.pop(call_id)
        for call_id in call_ids(message):
            if call_id in unanswered:
                return (f'messages[{index}] asks for tool call {call_id!r} while the call messages[{unanswered[call_id]}] '
                        'made under that id has no result yet — two open calls cannot share an id, since a result could '
                        'answer either')
            requested[call_id] = index
            unanswered[call_id] = index
    if unanswered:
        call_id, index = next(iter(unanswered.items()))
        return (f'messages[{index}] asks for tool call {call_id!r} and no later message carries its result — the trace '
                'appears to have been captured mid-turn, while that call was still in flight')
    return interrupted


def contentless_seed_problem(seed: list[dict[str, Any]]) -> str | None:
    """Rule ``seed_has_content``: why the seed says and does nothing, or ``None``.

    Judged on the seed as a whole, never per message: an agent message carrying only tool calls
    has empty content, and that is the most common resumed shape.
    """
    if any(flatten(message.get('content')).strip() or message.get('tool_calls') for message in seed):
        return None
    return ('the seeded history carries no content — no message has text and none carries a tool call, so there is '
            'no conversation to resume from')


def tool_tail_problem(seed: list[dict[str, Any]]) -> str | None:
    """Rule ``tool_tail_closes_last_call``: why a seed ending on tool results does not end on a
    completed agent turn, or ``None``.

    The trailing run of tool results answers the agent message right before it, and all of it
    once: every call that message asks for has exactly one result, and the run answers no other
    call. The other half of the rule, that a user message follows the results, is the caller's.
    """
    start = len(seed)
    while start > 0 and seed[start - 1].get('type') == TOOL:
        start -= 1
    if start == len(seed):
        return None
    caller_index = start - 1
    caller = seed[caller_index] if caller_index >= 0 else {}
    calls = (caller.get('tool_calls') or []) if caller.get('type') == AI else []
    if not calls:
        return (f'the seeded history ends on tool result(s) at messages[{start}:] with no agent tool call right '
                'before them, so they close no agent turn')
    requested = call_ids(caller)
    if asked_twice := sorted({call_id for call_id in requested if requested.count(call_id) > 1}, key=requested.index):
        return (f'the seeded history ends on the results of messages[{caller_index}], which asks for tool call id(s) '
                f'{asked_twice} more than once, so no run of results can answer each call exactly once')
    answered = [m.get('tool_call_id') for m in seed[start:]]
    if pending := [call_id for call_id in requested if call_id not in answered]:
        return (f'the seeded history ends on the results of messages[{caller_index}], but its tool call(s) '
                f'{pending} have no result — the trace appears to have been captured mid-turn, while the agent '
                'was still waiting on them')
    if stray := [call_id for call_id in answered if call_id not in requested]:
        return (f'the seeded history ends on the result of tool call(s) {stray}, which messages[{caller_index}] — '
                'the agent message right before the results — does not ask for, so the results close no single '
                'agent turn')
    if repeated := sorted({call_id for call_id in answered if answered.count(call_id) > 1}, key=answered.index):
        return (f'the seeded history ends on more than one result for tool call(s) {repeated} of '
                f'messages[{caller_index}], and a call takes exactly one result — the trace recorded a result twice')
    return None


def start_node(pin: dict[str, Any]) -> str | None:
    """The node the operator pinned with ``--start-node``, or ``None`` when they pinned none.

    Null is the ordinary value — most conversions pin nothing, and the key is written out either
    way. Anything else has to be an object whose one key is a non-blank ``node`` string, extras
    included: the model on the other side forbids them, so every shape refused here is one that
    would be refused at ingest rather than a rule this check invents.
    """
    point = pin.get('resume_point')
    if point is None:
        return None
    if not isinstance(point, dict):
        raise PinRefused(
            f'resume_point must be an object holding a node name, but is a {type(point).__name__} — '
            're-convert the trace, with a --start-node the graph exports or without the flag')
    node = point.get('node')
    if not isinstance(node, str) or not node.strip():
        raise PinRefused(
            'resume_point names no node, so nothing tells the harness where to start — re-convert '
            'the trace, with a --start-node the graph exports or without the flag')
    if extra := sorted(set(point) - {'node'}):
        raise PinRefused(
            f'resume_point holds {", ".join(extra)}, and node is the only key it may carry — this '
            'pin was not written by convert-trace, and ingest refuses it for the same reason')
    return node


def judge(pin: dict[str, Any]) -> tuple[int, int, str, str | None]:
    """Return (seed_length, lifted, tail_type, start_node) or raise :class:`PinRefused`.

    ``lifted`` is 1 when a trailing user message becomes the opener, matching what the backend
    does before it looks at the tail. The history rules run in the backend's order: message ids
    across the whole pin, then the tail, then the whole-seed tool pairing and content rules. The
    schema version comes before all of them, since at any other version the keys read below mean
    something else, or nothing. The start node's shape comes after them, so a pin that has to be
    re-exported names that edit first.
    """
    require_schema_version(pin)
    messages = pin.get('messages')
    if not isinstance(messages, list) or not messages:
        raise PinRefused('the pin carries no messages, so there is no history to resume from')
    if not all(isinstance(m, dict) for m in messages):
        raise PinRefused('the pin holds a message that is not an object')
    if problem := duplicate_message_id_problem(messages):
        raise PinRefused(problem)

    seed = list(messages)
    lifted = 0
    if seed[-1].get('type') == HUMAN:
        if not flatten(seed[-1].get('content')).strip():
            raise PinRefused(
                'the trace ends on a user message with no text content, so there is no opener to '
                'lift from it — the trailing turn is an attachment or an empty message')
        seed.pop()
        lifted = 1

    if not seed:
        raise PinRefused(
            'the pin holds no agent history — after lifting the trailing user message there is '
            'nothing to resume from')

    tail = str(seed[-1].get('type'))
    if tail == TOOL:
        if not lifted:
            raise PinRefused(
                'the seeded history ends on a tool result and no user message follows it, so the trace '
                'was captured mid-turn, after the tool answered and before the agent read the result')
        if problem := tool_tail_problem(seed):
            raise PinRefused(problem)
    elif tail == HUMAN:
        raise PinRefused(
            'the seeded history must end on a completed agent turn, but the trace ends on two user '
            'messages in a row — only the last one can be lifted as the opener, and the one before it '
            'would reach the agent unanswered')
    elif tail != AI:
        raise PinRefused(
            f'the seeded history must end on a completed agent turn, but ends on a {tail!r} message — '
            'the trace appears to have been captured mid-turn')
    if problem := unpaired_tool_call_problem(seed) or contentless_seed_problem(seed):
        raise PinRefused(problem)
    return len(seed), lifted, tail, start_node(pin)


def report(bundle_dir: Path) -> int:
    pins = sorted(bundle_dir.rglob(PIN_NAME))
    if not pins:
        print(f'No {PIN_NAME} under {bundle_dir}. Nothing to check — run this AFTER conversion.')
        return 0

    status = 0
    for pin_path in pins:
        folder = pin_path.parent.name
        try:
            pin = json.loads(pin_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            print(f'UNREADABLE  {folder}: {PIN_NAME} could not be read ({type(exc).__name__}).')
            status = 2
            continue
        try:
            length, lifted, tail, node = judge(pin)
        except PinRefused as exc:
            print(f'REFUSED     {folder}: {exc}')
            status = 2
            continue

        opener = ' + a lifted opener' if lifted else ''
        start = f' Pinned start node {node!r}.' if node else ''
        print(f'OK          {folder}: {length} seeded message(s){opener}, ends on {tail!r}.{start}')

    if status == 2:
        print(
            '\nA REFUSED folder is one the backend will reject after upload, generating nothing '
            'for it while its siblings proceed. Fix it at the SOURCE — re-export the thread so it '
            'ends on a completed agent turn, or drop it from this run. Do NOT edit a pin: the pin is '
            'the record of what the conversation was, and trimming it here changes that record '
            'invisibly. If a trim is what the customer wants, they trim the EXPORT and you convert '
            'again, so the shortened conversation is the one on file.')
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or '').split('\n')[0])
    parser.add_argument('bundle_dir', type=Path, help='the staging dir holding the converted folders')
    args = parser.parse_args(argv)
    if not args.bundle_dir.is_dir():
        print(f'{args.bundle_dir} is not a directory.', file=sys.stderr)
        return 2
    return report(args.bundle_dir)


if __name__ == '__main__':
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
