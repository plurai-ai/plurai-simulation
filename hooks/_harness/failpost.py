"""Fail posture: what a hook does when its handler raises, hangs or answers wrongly.

An ``open`` hook fails to "no opinion": the call proceeds, and one stderr line names the failure
(``_hookfail``). A ``closed`` hook fails to an explicit ``deny``, because a guard that cannot verify
what it exists to enforce must not let the call through. Neither posture relies on the process
crashing: on some harnesses a non-zero exit denies the tool call, on others it is ignored.

The deadline is the hook's own, set below the timeout the harness registers, so that an overrun is
answered by this module rather than by the harness killing the process.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from _hookfail import note

from _harness import model
from _harness.model import Decision


def failure_decision(hook: str, failure: BaseException, closed: bool) -> Decision:
    if closed:
        return model.deny(f"{hook} failed ({failure!r}) - refusing the call because its check could not run.")
    note(hook, failure)
    return model.none()


def guard(
    work: Callable[[], Decision],
    hook: str,
    closed: bool,
    deadline: float,
    report: Callable[[str], None] | None = None,
) -> Decision:
    """``work()``'s decision, or the hook's failure decision if it raises, overruns or returns a non-decision.

    ``report`` is told which failure it was (``deadline``, ``raised`` or ``not_a_decision``) before the
    failure decision is made, for a caller that records outcomes.
    """
    outcome: list[object] = []

    def target() -> None:
        try:
            outcome.append(work())
        except BaseException as exc:  # noqa: BLE001 - a hook calling sys.exit is a failure too
            outcome.append(exc)

    # A daemon thread: a handler stuck past the deadline must not keep the process alive.
    runner = threading.Thread(target=target, daemon=True)
    runner.start()
    runner.join(deadline)
    if runner.is_alive():
        _report(report, "deadline")
        return failure_decision(hook, TimeoutError(f"no answer within {deadline:g}s"), closed)
    result = outcome[0]
    if isinstance(result, Decision):
        return result
    if isinstance(result, BaseException):
        _report(report, "raised")
        return failure_decision(hook, result, closed)
    _report(report, "not_a_decision")
    return failure_decision(hook, TypeError(f"handle() returned {result!r}, not a Decision"), closed)


def _report(report: Callable[[str], None] | None, failure: str) -> None:
    if report is not None:
        report(failure)


def after_delivery(decision: Decision, hook: str) -> None:
    """Run the work ``decision`` asked to do once its answer is delivered. A failure is reported, never raised:
    the answer is out, and the hook's own posture no longer applies."""
    work = decision.after_delivery
    if work is None:
        return
    try:
        work()
    except Exception as exc:  # noqa: BLE001 - see the docstring
        note(hook, exc)
