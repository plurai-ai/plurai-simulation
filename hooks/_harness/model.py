"""The harness-neutral types a hook sees: the ``Event`` it is called with and the ``Decision`` it returns.

Stdlib only, and importable under the oldest interpreter ``python3`` can resolve to (see
``hooks/_hookfail.py``), so no syntax newer than 3.9.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import Any

#: What a hook may decide. ``none`` leaves the call to the harness's normal flow.
KINDS = ("deny", "rewrite", "block_stop", "context", "none")


@dataclass(frozen=True)
class Event:
    """One hook invocation, decoded.

    ``kind`` is the event in snake case (``pre_tool_use``, ``user_prompt_submit``, ``stop``).
    ``tool`` is the canonical tool: ``shell``, ``ask``, ``skill``, ``subagent``, ``backend:<verb>``
    or ``other:<name>``; ``None`` when the event carries no tool. ``raw`` is the payload as the
    harness sent it, for the rare hook that needs a field the codec does not name. ``harness`` is
    the codec that decoded the event (``claude``, ``copilot``, ``codex``), for a hook whose text
    depends on the tools the session has.

    ``answered`` is set from a tool result: whether the user answered the ask tool's question. Only
    the answer reader of the harness that sent the result decides it (``_harness/answers.py``), and
    anything it cannot recognise is unanswered. ``transcript_path`` is the session transcript the
    payload names, in the harness's own format (``_harness/transcript.py`` reads it).
    ``last_message`` is the text of the turn's final assistant message at a Stop. It is None when the
    harness sends none and the transcript could not supply one; a hook reads that as "cannot judge
    the message", which is not an empty message. ``stop_hook_active`` is True on a Stop that follows a
    Stop block in the same turn, on a harness that says so (Codex).

    ``turn_ending_ask``, ``command_from_prompt``, ``queues_tool_pre``, ``reports_background_shell``,
    ``polls_shell_with_stdin``, ``wakes_on_background``, ``wakes_when_headless`` and ``starts_in_session_dir`` are what the
    harness can do, constant per harness and defined in ``registry.CAPABILITIES``. Hook logic asks them
    and never the harness's name. ``blocking_ask`` is the one per-call flag: this call is the ask tool
    that waits for an answer the user may never give. An event nobody decoded has Claude Code's value
    for every one of them.

    ``background_shell`` and ``finished_shell`` are read by the codec from a shell tool's result, because
    each harness spells a background command in its own way: ``background_shell`` is the handle of a command
    this call left running in the background (``""`` when the harness names none), and ``finished_shell`` the
    handle of a background command this result, or the harness's completion notification prompt, reports
    finished. Both are None for a harness whose background commands the codec does not read (Codex), and for
    every event that is not such a result or notification.

    ``answered`` and ``last_message`` are the costly fields (a parse of the tool result, a read of the
    transcript), so a codec supplies them through ``answered_source`` and ``last_message_source``: a
    value, or a function that computes it. The function runs the first time a hook reads the field, and
    only then, so a hook that reads neither pays for neither.
    """

    kind: str = ""
    tool: str | None = None
    tool_input: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    cwd: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    harness: str = ""
    transcript_path: str = ""
    stop_hook_active: bool = False
    turn_ending_ask: bool = False
    blocking_ask: bool = False
    command_from_prompt: bool = False
    queues_tool_pre: bool = False
    reports_background_shell: bool = False
    polls_shell_with_stdin: bool = False
    wakes_on_background: bool = True
    wakes_when_headless: bool = False
    starts_in_session_dir: bool = True
    background_shell: str | None = None
    finished_shell: str | None = None
    answered_source: bool | Callable[[], bool] = field(default=False, repr=False, compare=False)
    last_message_source: str | Callable[[], str | None] | None = field(default=None, repr=False, compare=False)

    @cached_property
    def answered(self) -> bool:
        source = self.answered_source
        return bool(source() if callable(source) else source)

    @cached_property
    def last_message(self) -> str | None:
        source = self.last_message_source
        return source() if callable(source) else source


@dataclass(frozen=True)
class Decision:
    """What a hook decided, in no harness's vocabulary.

    ``text`` is the reason for ``deny`` and ``block_stop`` and the injected text for ``context``;
    ``tool_input`` is the replacement input of a ``rewrite``. ``notice`` is a message for the human
    rather than the model, and may ride on any kind. ``after_delivery`` runs once the
    entry point has written and flushed the answer, for state a hook may change only after its
    answer is out; a failure in it is reported and affects nothing else.
    """

    kind: str
    text: str = ""
    tool_input: dict[str, Any] | None = None
    notice: str = ""
    after_delivery: Callable[[], None] | None = field(default=None, compare=False)
    #: A ``block_stop`` that corrects the turn rather than reports a fault. A harness with a channel at Stop that is not
    #: an error (Claude Code shows ``additionalContext`` there as feedback and the model acts on it) delivers it there; the
    #: others keep the block.
    feedback: bool = False

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown decision kind {self.kind!r}")

    def with_notice(self, notice: str) -> Decision:
        return replace(self, notice=notice)

    def after(self, work: Callable[[], None]) -> Decision:
        """This decision, with ``work`` to run once it has been delivered."""
        return replace(self, after_delivery=work)


def deny(reason: str) -> Decision:
    return Decision("deny", text=reason)


def rewrite(tool_input: dict[str, Any]) -> Decision:
    return Decision("rewrite", tool_input=tool_input)


def block_stop(reason: str) -> Decision:
    """Keep the turn going at a Stop. On Claude Code the same answer to a ``UserPromptSubmit`` erases the prompt."""
    return Decision("block_stop", text=reason)


def correct_stop(reason: str) -> Decision:
    """A correction at a Stop: the turn goes on with ``reason`` as its instruction.

    Same effect as ``block_stop`` everywhere. Where the harness has a Stop channel that is not an error, the
    user sees feedback there and not an error line; elsewhere it is the block.
    """
    return Decision("block_stop", text=reason, feedback=True)


def context(text: str) -> Decision:
    return Decision("context", text=text)


def none() -> Decision:
    return Decision("none")


#: How much each kind outranks the next when several handlers answer one event, strongest first.
_PRECEDENCE = ("deny", "block_stop", "rewrite", "context", "none")


def merge(decisions: list[Decision]) -> Decision:
    """The one answer for the decisions of the handlers that share an event.

    The strongest kind wins (``deny``, ``block_stop``, ``rewrite``, ``context``, ``none``). Reasons of
    several denies or stop blocks, texts of several context answers, and the notices of all of them are
    joined in handler order. A ``rewrite`` carries no text, so a context beside it is dropped: no
    handler pairs the two. ``after_delivery`` work is not merged; the caller runs each handler's own.
    """
    if not decisions:
        return none()
    kind = min((d.kind for d in decisions), key=_PRECEDENCE.index)
    same = [d for d in decisions if d.kind == kind]
    separator = "\n\n" if kind == "context" else "\n"
    notices = [d.notice for d in decisions if d.notice]
    merged = Decision(
        kind,
        text=separator.join(d.text for d in same if d.text),
        tool_input=next((d.tool_input for d in same if d.tool_input is not None), None),
        notice="\n".join(notices),
        feedback=kind == "block_stop" and all(d.feedback for d in same),
    )
    return merged
