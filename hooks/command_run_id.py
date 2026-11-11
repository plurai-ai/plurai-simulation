"""Give a backend job start the command run id: a rewrite, or context under auto mode. PreToolUse on the plugin's MCP server.

The backend groups every job and upload one command starts under the id the command was given
(``scripts/open_epoch.py``). The id is added here, in code, so that no skill has to carry it and no model has
to remember it. ``scripts/mcp_call.py`` does the same for the calls it makes, and the stdio bridge for the
harnesses whose hooks cannot rewrite a call.

Only a tool that declares the argument is rewritten (``registry.COMMAND_RUN_ID_TOOLS``): the backend refuses
an undeclared one. A call that already names an id keeps it. The rewrite never decides permission, and the hook
never denies: a call without its label is still the call the user approved.

Under Claude Code's auto mode the call is never rewritten. Auto mode reviews the input the model wrote, and it
gives no verdict on a call whose input a hook then changes: the call is refused, and the model must re-issue it
with the changed input or stop. So the call goes out as written, and the hook tells the model the id instead,
which the model then puts on its later calls itself. Only a job start that names no id costs its label.

Stdlib only, importable under the oldest interpreter ``python3`` can resolve to.
"""

from __future__ import annotations

from _harness import model
from _harness.model import Decision, Event

PREFIX = "backend:"


def handle(event: Event) -> Decision:
    tool = event.tool or ""
    if not tool.startswith(PREFIX):
        return model.none()
    import _command_run  # noqa: PLC0415 - a sibling module

    verb = tool[len(PREFIX) :]
    arguments = dict(event.tool_input)
    run_id = _command_run.for_call(verb, {"session_id": event.session_id})
    updated = _command_run.with_id(verb, arguments, run_id)
    if updated == arguments:
        return model.none()
    if str(event.raw.get("permission_mode") or "") == "auto":
        return model.context(
            f"This call goes out without its command run id, because auto mode refuses a call whose input a hook "
            f'changes. Put `"{_command_run.ARGUMENT}": "{run_id}"` in the input of every later backend tool call '
            f"that takes `{_command_run.ARGUMENT}`, so the backend groups this command's jobs."
        )
    return model.rewrite(updated)
