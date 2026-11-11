# Limited mode — a flow in a session where the plugin's hooks are off

Step 0's `ready` line printed `ready, limited (hooks off)`. Claude Code loaded the plugin's skills and its
backend tools but did not run its hooks, most often because the organization's managed settings run only the hooks
an admin approved. The flow runs anyway. What the hooks would have done for it, the flow does itself, by the rules
below. Copilot and Codex never print this line: a session there without hooks stops at Step 0. The managed settings
that turn the hooks on, and that admit the plugin's MCP server, are in
[install-shared.md](install-shared.md#for-admins-managed-settings).

A stop's halt and its lift by the customer's next typed message run on every harness with hooks (Claude Code, Codex and
Copilot). Limited mode has none: no hook records the halt, so the flow stops where it said the stop text.

Show the user the `WARN (hooks)` line once, verbatim, the first time Step 0 prints it, and say nothing more about
it. Its text tells the user what to do if the session goes quiet, and how the checks come back on.

## The rules

1. **The plugin root.** Nothing fills in `${CLAUDE_PLUGIN_ROOT}` in a reference file or doc a skill sends you to,
   and the shell may not export it. Step 0 printed the root once, on its `flow-window: plugin root:` line: start
   every shell command that uses `${CLAUDE_PLUGIN_ROOT}` with the `export CLAUDE_PLUGIN_ROOT=…;` that line names,
   and read the plugin's files under that root.
2. **No heartbeat.** None is asked for, and none is set: with no hook to hold its firings back, each one would
   reach you as a prompt.
3. **Wait in the foreground.** Never end a turn while a job or a run you started is still going, unless a
   background command or a subagent you started will wake the session when it finishes. Otherwise wait inside the
   turn: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/poll_job.sh" <job_id>` for a backend job and
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/run_wait.sh" "$OUT"` for a run, each re-run until it is done.
4. **Every hand-off.** Right after the flow's hand-off, run
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" handoff <command>`, with the name the Step 0 `arm` line used,
   and do what it prints in the same turn. It closes the flow's window. Inside a `/simulate` run it also records the
   stage and prints the next step: the next stage's invocation, which you start right away, or the closing. When it
   says the stage's file was not written, stop there and say in one line what is missing. For a command run on its
   own it prints nothing more, and the turn ends on the hand-off.
5. **`/simulate`.** After `simulate begin`, `done`, `skip`, `rewind` or `resume`, run
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" simulate next` and do what it prints in the same turn.
   `simulate end` is not refused in limited mode.
6. **The guards are off.** No hook checks the flow's questions, its hand-off or where its files go, so keep the
   skills' rules yourself: ask only at the gates the skills name, sanction each ask as the skill says, end the flow
   on one of its anchor lines, and write only inside the agent's `plurai_sim` folder (a script refuses any other
   path in this session).
7. **When the user types continue.** The session went quiet mid-step. In a `/simulate` run, run
   `flow-window.sh simulate next` and do what it prints; otherwise pick the flow up at the step it stopped on.
