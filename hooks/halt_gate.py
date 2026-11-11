#!/usr/bin/env python3
"""No backend job starts in the turn that printed a stop, nor in any later turn no person started. PreToolUse on the
plugin backend's job-start tools, run by `run.py` on every harness.

A stop halts the /simulate plan: `contact` (a fault on Plurai's side), `capacity` (which also records the unavailable stop
and `credit-unavailable`, whose text is the contact hand-off) or `credit`. A model that starts the job again right after
the stop pays for work the stop said would not run, and then narrates the cause. The gate holds until the next message a
person types (`flow_arm` -> `simulate_continue.record_user_turn`; neither counts a heartbeat, a task notification, a
re-injected block reason or a flow command):

* `capacity` and `credit` are cleared by that message, so the gate has no state of its own. Until then the reason says
  the run is stopped and to say nothing more.
* `contact` is not cleared: the message is stamped on the halt (`simulate_continue.typed_after_halt`). The halt stays on
  the plan and the run does not go on by itself; only a landing of the halted stage, `rewind`, `skip`, a new plan or
  `end` ends it. Before the stamp the deny reason is the flow-window line the model read after the stop
  (`simulate_continue.resume_line`), plus the order not to end the run: ending it is the customer's choice. After the
  stamp any skill may start jobs, so the plan may say the run is stopped at a stage while other work runs, until the
  run is directed again.

A `proof` halt (a stage's own file lifts it) or an untyped one gates nothing: the work that lifts it can be a job.

Open: a plan this hook cannot read decides nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).parent))

if TYPE_CHECKING:
    from _harness.model import Decision, Event


def handle(event: Event) -> Decision:
    from _harness import model  # noqa: PLC0415 - the codec package sits beside this file

    try:
        import _run_driver  # noqa: PLC0415 - a sibling module
        import simulate_continue  # noqa: PLC0415
        from _workdir import marker_dirs  # noqa: PLC0415

        plan = _run_driver.read_plan(_run_driver.find_plan(marker_dirs({"session_id": event.session_id})))
        kind = None if plan is None or plan.get("finished") is True else simulate_continue.halt_kind(plan)
        if plan is None or kind not in (_run_driver.STOP_CONTACT, *simulate_continue.LIFTABLE):
            return model.none()
        if kind == _run_driver.STOP_CONTACT and simulate_continue.typed_after_halt(plan):
            return model.none()
        stopped = simulate_continue.resume_line(plan, reopened=False)
    except Exception:  # noqa: BLE001 - an unreadable plan gates nothing
        return model.none()
    if kind == _run_driver.STOP_CONTACT:
        return model.deny(f"Start no job: {stopped}. Say the stop text and end the turn; "
                          "the customer's next message decides what runs. Do not end the run yourself.")
    return model.deny("Start no job: the run is stopped. Say nothing more: the customer's next message decides what runs.")
