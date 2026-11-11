# Step 0 block — canonical source

Each customer-facing flow command opens with `flow-window.sh arm <command>` and `flow-window.sh ready <command>`
in its Step 0. The two paragraphs below follow that code block and are the same in all seven flow skills:
they say what to do with the heartbeat instruction the `ready` line can print, and what to do when it prints
`NOT READY`, `SETUP` or `ready, limited`. Every flow skill carries a generated copy between `<!-- BEGIN step0: … -->` and
`<!-- END step0 -->` markers, with `{command}` replaced by the skill's own flow name and `{skill}` by the skill's name. `STEP0_FLOWS` in
Each file's copy carries its own flow name and indent (the `/simulation-generate-suite`
copy sits inside a list item).

A refusal's text is what the customer reads first, so no model turn may sit between the check and that text. The check's
call therefore carries the window's close (`backend_check.py --close {command}`) and the block names no separate close step
for exit 7 or 8. A heartbeat set in step 1 is left alone: a closed window makes its firings inert, since
`hooks/flow_heartbeat.py` blocks every firing that finds no armed window before any model call, so no tool call is needed
after the refusal text.

The block is harness-neutral. What "set the heartbeat" means on each harness lives in the SessionStart
glossary (`hooks/session_glossary.py`), not here.

The `arm` and `ready` code block itself stays in each skill: prose specific to that flow (what arming stamps,
who owns the marker, which gates it sanctions) sits between that code block and this one.

This block is the one source; the plugin's development repository regenerates every copy from it and fails
when a copy differs. The generator rewrites existing marked copies only.

<!-- BEGIN CANONICAL BLOCK -->
**If that line prints a heartbeat instruction, set the heartbeat it names in the SAME turn** — it is this flow's dropped-turn recovery (`${CLAUDE_PLUGIN_ROOT}/docs/polling-cadence.md` → *Dropped turns*). If the call is denied by the permission mode, continue without the heartbeat — expected under auto mode; never retry it.

Step 0 has three steps, run in this order. A line that lets the flow go on ends only its own step.

**1. Check readiness.** The `ready` line prints `ready`, or one line for each thing that is not set up.

- A `NOT READY` line names something only the user can change (the plugin's hooks not running, the flow window not armed, no shell network, MCP and the shell on different backends), with its fix. Close the window with `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" close {command}`, tell the user in plain words what the line says to fix, and stop; the flow must not start. The command's exit status is 0 whatever it prints, so a `NOT READY` line stops the flow all the same.
- A `SETUP` line names something the `harness-setup` skill fixes in this session (no Plurai login, or Plurai not connected). Close the window the same way, then run the `harness-setup` skill `from /{skill}` (where the harness takes no arguments: "you run it for /{skill}"). Add nothing of your own, and show the user nothing from the `SETUP` line. The window stays closed during setup, so the ask and report guards do not block its install question or the turn that waits for the login. When the skill reports Plurai set up, run the `arm` line and the `ready` line above again and go on from here; when it stops, stop. If the same `SETUP` line comes back after the skill reported Plurai set up, do not run the skill again: tell the user `Setup could not finish. Run /plurai-diagnose and share its output with Plurai support.` and stop.
- A `ready, limited` line means the plugin's hooks are off in this session, and the flow runs without them. When a `WARN (hooks)` line follows it, show the user its text once, verbatim, and go on to step 2. Follow `${CLAUDE_PLUGIN_ROOT}/docs/limited-mode.md` for the rest of the flow. Right after this flow's hand-off, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" handoff {command}` and do what it prints in the same turn, also where a step says to end the turn because the flow continues by itself.

**2. Check the backend.** Step 1 ends on `ready` or `ready, limited` (a `NOT READY` or `SETUP` line ends it first). Before you look at your tool list, run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py" --close {command}`. It needs no backend tool. A plugin the backend refuses has none. On a refusal (exit 7 or 8) the same call closes the window, so no close step follows it.

- Exit 0: go on to step 3. A line it printed is an update notice: say it once, as printed, first; it never stops the flow.
- Exit 7 is a version refusal. Say what the check printed as the error posture's version refusal rule says, with no word before it, and stop; the flow must not start, and no tool call, scan or setup follows.
- Exit 8 is a credit refusal: its line is `CREDIT-STOP: <sentence>`. Follow the error posture's credit refusal rule.
- Exit 3 prints one line. A `SETUP` line: handle it as the `SETUP` line above. A `NOT READY` line (for example hooks the harness has not trusted): handle it as the `NOT READY` line above.

**3. Check your tool list.** The script cannot see your tool list: if no backend tool is available to you in this session, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" ready {command} --no-backend-tools` and act on the line it prints as above.
<!-- END CANONICAL BLOCK -->
