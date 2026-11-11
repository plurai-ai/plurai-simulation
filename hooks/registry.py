"""The hook registry: what every hook is, in no harness's vocabulary, and the facts that translate it.

One table, read by the decoders (native tool name to canonical tool), by ``hooks/run.py`` (fail
posture) and by ``scripts/dev/generate_harness_files.py`` (the per-harness hook files). Adding a hook
or a harness is adding an entry here; nothing else restates a name that appears below.

Stdlib only, importable under the oldest interpreter ``python3`` can resolve to (``hooks/_hookfail.py``
explains the floor), and free of environment reads: a decoder that consulted the environment could
be steered by it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

#: Harnesses that have a codec in ``hooks/_harness/``, in the order their files are generated.
HARNESSES = ("claude", "copilot", "codex")


class Capabilities(TypedDict):
    """What a harness can do, as the ``Event`` fields of the same names.

    Constant per harness, so a codec spreads its ``CAPABILITIES`` row into the ``Event`` it builds.
    Hook logic asks the event what the harness can do and never what it is called. An ``Event`` nobody
    decoded has Claude Code's value for each.
    """

    #: A gate is held by ending the turn on the question: the harness's ask is plain text or an async
    #: tool whose answer arrives as the next prompt.
    turn_ending_ask: bool
    #: There is no command-expansion event, so the prompt names the command.
    command_from_prompt: bool
    #: A ``PreToolUse`` delivery waits for the next event instead of starting one of its own.
    queues_tool_pre: bool
    #: The codec reads a background command off the shell result, so a call's input flag alone is no
    #: evidence of one.
    reports_background_shell: bool
    #: No tool timeout can be raised for a long command, which keeps running while the model polls it
    #: with ``write_stdin``.
    polls_shell_with_stdin: bool
    #: A turn starts by itself when a background command finishes, so a turn may end with such work in
    #: flight and the work resumes the session.
    wakes_on_background: bool
    #: A declared headless operator leaves ``wakes_on_background`` standing. Claude Code's headless session
    #: (``claude -p``) ends with its turn: it kills a background shell seconds after the turn goes idle and waits
    #: on a background subagent only up to a ceiling, so there nothing started in the background wakes a turn.
    #: Copilot's headless session (``copilot -p --autopilot``) waits for background commands only up to a ~600 s
    #: ceiling: a 90 s command woke a turn on its completion, a 1200 s one was abandoned when Copilot exited at
    #: ~611 s ("Timed out after 600s waiting for background tasks to finish", ``COPILOT_TASK_WAIT_TIMEOUT_SECONDS``).
    #: A scored run outlasts that, so it does not count as waking (measured on 1.0.95:
    #: ``scripts/tests/fixtures/harness_payloads/copilot-1.0.95/``).
    wakes_when_headless: bool
    #: A hook process starts in the directory the session works in, so ``run.py`` leaves it there
    #: (Copilot starts it in the plugin directory).
    starts_in_session_dir: bool


CAPABILITIES: dict[str, Capabilities] = {
    "claude": {
        "turn_ending_ask": False,
        "command_from_prompt": False,
        "queues_tool_pre": False,
        "reports_background_shell": False,
        "polls_shell_with_stdin": False,
        "wakes_on_background": True,
        "wakes_when_headless": False,
        "starts_in_session_dir": True,
    },
    "copilot": {
        "turn_ending_ask": False,
        "command_from_prompt": True,
        "queues_tool_pre": True,
        "reports_background_shell": True,
        "polls_shell_with_stdin": False,
        "wakes_on_background": True,
        "wakes_when_headless": False,
        "starts_in_session_dir": False,
    },
    # Codex reports no background command (``background_shell`` and ``finished_shell`` stay unset) and
    # never starts a turn when one finishes.
    "codex": {
        "turn_ending_ask": True,
        "command_from_prompt": True,
        "queues_tool_pre": True,
        "reports_background_shell": False,
        "polls_shell_with_stdin": True,
        "wakes_on_background": False,
        "wakes_when_headless": True,
        "starts_in_session_dir": True,
    },
}

#: Canonical tool -> harness -> the names that harness gives it. The first name is the one a
#: PreToolUse payload carries; the others are spellings the harness also matches or reports (Copilot's
#: PermissionRequest says ``bash``). Matchers list all of them, decoders accept all of them.
TOOLS: dict[str, dict[str, tuple[str, ...]]] = {
    "shell": {"claude": ("Bash",), "copilot": ("Bash", "bash"), "codex": ("Bash",)},
    # Codex has two ask tools: the model and the mode decide which one a session gets. Copilot's
    # ``ask_user`` reaches its hooks as ``AskUserQuestion``, and either name in a matcher selects it,
    # so the matcher lists both in one group: two groups would run a guard twice for one call.
    "ask": {
        "claude": ("AskUserQuestion",),
        "copilot": ("AskUserQuestion", "ask_user"),
        "codex": ("request_user_input", "request_user_input_async"),
    },
    # Copilot keeps the lower-case name; a ``Skill`` matcher never fires there. Codex loads a skill
    # without a tool call, so the tool has no name on it.
    "skill": {"claude": ("Skill",), "copilot": ("skill",)},
    # The file reader. Copilot's hooks report it as ``Read`` with the path in ``path``; ``view`` is the
    # name of the tool itself. Codex has none: it reads files through the shell.
    "read": {"claude": ("Read",), "copilot": ("Read", "view")},
    # Copilot's poll of a background command (``bash`` in ``mode: "async"``). Its result says whether the
    # command finished. Claude Code reads a background command with a tool its hooks do not select, and
    # Codex sends no hook for ``write_stdin``. On Windows Copilot's shell is ``powershell``: its hooks see
    # that call as ``Bash`` (the ``shell`` names above) but the poll under its own name, ``read_powershell``,
    # which a ``read_bash`` matcher does not select.
    "shell_read": {"copilot": ("read_bash", "read_powershell")},
    "subagent": {"claude": ("Agent", "Task"), "copilot": ("Agent", "task"), "codex": ("collaborationspawn_agent",)},
    # Claude Code's watch and scheduled-prompt tools. The other harnesses offer neither.
    "monitor": {"claude": ("Monitor",)},
    "cron": {"claude": ("CronCreate",)},
}

#: The matcher of every MCP tool, whatever its server, per harness. Copilot names MCP tools
#: ``<server>-<verb>``, which no single pattern separates from its built-in tools, so it has none.
ANY_MCP: dict[str, tuple[str, ...]] = {"claude": ("mcp__.*",), "codex": ("mcp__.*",)}

#: The plugin's name, which Claude Code puts into every tool name of the plugin's MCP servers.
PLUGIN = "plurai-simulation"

#: The name of the plugin's backend MCP server in every harness's manifest. It is the user's tool list and
#: permission rules that carry it.
SERVER = "plurai"

#: What every name of the plugin's backend MCP tools starts with, per harness. The rest of the name
#: is the tool's verb. Claude Code spells it ``mcp__plugin_<plugin>_<server>__<verb>``; Copilot
#: ``<server>-<verb>``, or ``<server>/<verb>`` in a PermissionRequest; Codex ``mcp__<server>__<verb>``
#: with the server name's dashes turned into underscores.
BACKEND_PREFIXES: dict[str, tuple[str, ...]] = {
    "claude": (f"mcp__plugin_{PLUGIN}_{SERVER}__",),
    "copilot": (f"{SERVER}-", f"{SERVER}/"),
    "codex": (f"mcp__{SERVER.replace('-', '_')}__",),
}


#: The backend tools the plugin's Codex MCP file approves, by exact name, for a run with nobody to ask.
#: Every tool of the backend's MCP surface that the skills call, except the destructive ones
#: (``cancel_job``, ``delete_artifact``, ``delete_trace_brief``, ``delete_upload``, ``undeploy_env``),
#: which keep Codex's own approval. A tool no skill calls (``fetch_upload``, ``get_mock_event_log``,
#: ``recover_trace_brief``) keeps it too. A tool the backend adds later is not here, so it asks until someone
#: lists it. Sorted, because the generated file is compared with this list.
BACKEND_APPROVED_TOOLS: tuple[str, ...] = (
    "attach_run_report",
    "backend_identity",
    "deploy_env",
    "diagnose_mock",
    "edit_scenario_gt",
    "edit_session_start",
    "edit_session_turn_status",
    "end_edit_session",
    "end_scenarios_session",
    "end_session",
    "eval_coverage_audit",
    "fetch_artifact",
    "generate_env_start",
    "generate_evals_start",
    "generate_scenarios_start",
    "get_artifact_meta",
    "get_scenario",
    "get_trace_brief_meta",
    "get_trace_failures",
    "get_upload_meta",
    "ground_workspace_start",
    "interact_edit_session",
    "interact_with_scenario_request",
    "job_status",
    "list_artifacts",
    "list_uploads",
    "optimize_eval_adopt",
    "optimize_eval_revert",
    "optimize_eval_start",
    "prewarm_dimension_graph_start",
    "refine_scenarios_start",
    "release_verdict_recompute",
    "relink_eval_scenario_set",
    "revert_edited_policies",
    "score_run",
    "score_sampled_replies",
    "solvability_check_start",
    "stash_input_doc",
    "suite_diagnostics_start",
    "upload_doc_examples_start",
    "upload_eval_labels",
    "upload_red_cell_audit",
    "upload_run_artifacts_start",
    "upload_scenario_bundle_start",
)


#: The backend tools that take the ``command_run_id`` argument: every tool that starts a job, and the three
#: uploads. The backend refuses an argument a tool does not declare, so the chokepoints add the id to these and
#: to no other tool. A tool that only reads, polls, edits or cancels is not here. ``refine_scenarios_start`` opens
#: a scenario session and ``score_run`` is a synchronous score, and neither creates a job, so neither takes it.
#: Sorted, and a subset of ``BACKEND_APPROVED_TOOLS``; a test compares it with the backend's tool source.
COMMAND_RUN_ID_TOOLS: tuple[str, ...] = (
    "generate_env_start",
    "generate_evals_start",
    "generate_scenarios_start",
    "ground_workspace_start",
    "optimize_eval_start",
    "prewarm_dimension_graph_start",
    "release_verdict_recompute",
    "solvability_check_start",
    "suite_diagnostics_start",
    "upload_doc_examples_start",
    "upload_run_artifacts_start",
    "upload_scenario_bundle_start",
)


def canonical_tool(harness: str, native: str) -> str | None:
    """The canonical name of ``native`` as ``harness`` spells it: ``shell``, ``ask``, ``skill``,
    ``subagent``, ``backend:<verb>`` or ``other:<native>``. ``None`` for no tool name at all."""
    if not native:
        return None
    for canonical, names in TOOLS.items():
        if native in names.get(harness, ()):
            return canonical
    for prefix in BACKEND_PREFIXES[harness]:
        if native.startswith(prefix):
            return "backend:" + native[len(prefix) :]
    return "other:" + native


def matcher_for(harness: str, selectors: tuple[str, ...]) -> str:
    """The matcher regex ``harness`` reads for a hook's canonical ``selectors``.

    A selector is a canonical tool from ``TOOLS``, ``backend:*`` (every tool of the plugin's backend
    server), ``backend:<verb>`` (one tool of that server, a name in ``BACKEND_APPROVED_TOOLS``) or ``mcp:*``
    (every MCP tool). The matcher is the alternation of every name the selectors have on ``harness``; the
    ``backend:<verb>`` selectors of one hook make one anchored alternative, ``^<prefix>(<verb>|<verb>)$``.
    """
    alternatives: list[str] = []
    verbs: list[str] = []
    for selector in selectors:
        if selector == "backend:*":
            # Anchored: the matcher is the gate of an ``always`` hook, so it must not select a tool
            # that merely contains the prefix (another plugin's server named alike).
            alternatives.append("^" + BACKEND_PREFIXES[harness][0] + ".*")
        elif selector.startswith("backend:"):
            verb = selector[len("backend:") :]
            if verb not in BACKEND_APPROVED_TOOLS:
                raise ValueError(f"unknown backend tool {verb!r} in matcher selector {selector!r}")
            if not verbs:
                # Held at the place of the first verb, so the alternation keeps the selectors' order.
                alternatives.append("")
            verbs.append(verb)
        elif selector == "mcp:*":
            alternatives.extend(ANY_MCP.get(harness, ()))
        elif selector in TOOLS:
            alternatives.extend(TOOLS[selector].get(harness, ()))
        else:
            raise ValueError(f"unknown matcher selector {selector!r}")
    if verbs:
        # Anchored at both ends, so a verb never selects a longer one that starts with it.
        named = verbs[0] if len(verbs) == 1 else "(" + "|".join(verbs) + ")"
        alternatives[alternatives.index("")] = "^" + BACKEND_PREFIXES[harness][0] + named + "$"
    if not alternatives:
        # An empty matcher means "every tool" to a harness, the opposite of what a selector with no
        # name there asks for.
        raise ValueError(f"{selectors!r} name no tool on {harness}; leave the hook off that harness")
    return "|".join(alternatives)


#: The two scopes of a hook. ``always`` hooks have tools of the plugin's own MCP server as their matcher
#: (``backend:*`` or ``backend:<verb>`` selectors only), so the matcher is their gate: a call to the server
#: is a Plurai command in itself, and the hook is never behind the ``sh`` gate. ``scoped`` is every other
#: hook: it only matters while a flow or command scope is open, so the gate fronts it.
SCOPES = ("always", "scoped")

#: Events whose hooks are never gated. ``session_pin`` writes the pin the plugin's scripts read and
#: ``session_glossary`` the marker Step 0 requires, so each session starts Python once for them.
UNGATED_EVENTS = ("SessionStart", "SessionEnd")

#: The files whose presence in a session's flow directory (or the agent's ``plurai_sim`` directory) opens
#: the scope: a flow window, a command scope, a ``/simulate`` plan. ``hooks/gate.sh`` spells the same
#: names; ``hooks/tests/test_hook_gate.py`` pins the two.
GATE_MARKERS = (".plurai_flow_active", ".plurai_command_scope", ".plurai_simulate.json")

#: What a hook's ``entry`` lets through the gate on the payload alone, with no scope open yet:
#: ``prompt`` a prompt that starts a plugin command (or the heartbeat's sentinel prompt), ``skill`` a call
#: of one of the plugin's skills, ``token`` a tool call whose input names ``CLAUDE_PLUGIN_ROOT``, which
#: only text from the plugin's skills carries.
ENTRIES = ("prompt", "skill", "token")


class Features(TypedDict):
    """What a harness reads in a hook entry beyond ``command`` and ``timeout``."""

    #: ``"if"``: a permission rule (``Bash(*flow-window.sh*)``) the harness checks before it starts the
    #: command. Where it is False the entry carries none and the gate is the only filter.
    filter: bool
    #: ``"async"``: the harness starts the command and does not wait for its answer. Where it is False the
    #: hook stays synchronous.
    run_async: bool
    #: ``"windows"``: the command the harness runs instead of ``command`` on Windows. The gate is an ``sh`` script, and
    #: a harness whose hook shell on Windows is PowerShell has no ``sh`` on its path, so a gated entry there names the
    #: ungated command: every event starts the hook's Python, and the hook's own scope check stands down.
    windows_command: bool
    #: ``"commandWindows"``: the harness runs a hook through ``%COMSPEC% /C`` on Windows, where there is no ``sh`` to start the gate,
    #: so EVERY entry there carries a ``cmd.exe`` command (``%CLAUDE_PLUGIN_ROOT%\hooks\run.py``, the variable form of
    #: ``cmd.exe``) that starts the hook directly.
    cmd_command: bool


#: Per harness, measured by ``scripts/tests/harness_probe/hook_features/run_hook_features.sh``; the facts are
#: ``scripts/tests/fixtures/harness_payloads/hook_entry_features.json`` and a test holds this table to them.
#: A harness that lacks a feature falls back to the ``sh`` gate and a synchronous hook; the answer of a hook
#: never depends on it. Codex reads ``async`` but is left synchronous: the hash it records for an async
#: handler is not measured, and a wrong hash would show every async hook as changed.
FEATURES: dict[str, Features] = {
    "claude": {"filter": True, "run_async": True, "windows_command": False, "cmd_command": False},
    "copilot": {"filter": False, "run_async": False, "windows_command": True, "cmd_command": False},
    "codex": {"filter": False, "run_async": False, "windows_command": False, "cmd_command": True},
}


def is_backend_matcher(selectors: tuple[str, ...]) -> bool:
    """Whether ``selectors`` select tools of the plugin's backend server and nothing else: ``("backend:*",)``, or
    one or more ``backend:<verb>`` selectors."""
    if selectors == ("backend:*",):
        return True
    return bool(selectors) and all(s.startswith("backend:") and s != "backend:*" for s in selectors)


#: The selectors of the backend tools that start a job (``COMMAND_RUN_ID_TOOLS``): the matcher of the hooks
#: that act on a job start only, so a read, a poll or an edit of the backend starts no hook process.
JOB_START: tuple[str, ...] = tuple(f"backend:{verb}" for verb in COMMAND_RUN_ID_TOOLS)


@dataclass(frozen=True)
class Hook:
    """One hook, registered on ``event`` for every harness in ``harnesses``.

    ``name`` is the module under ``hooks/`` whose ``handle(event)`` decides. ``event`` is spelled as
    Claude Code spells it; Copilot and Codex read the same names. ``matcher`` is canonical (see
    ``matcher_for``); empty means the hook sees every call of its event. A ``closed`` hook denies when
    it cannot decide, an open one stands down (``hooks/_harness/failpost.py``). ``timeout`` is the
    seconds the harness gives the command before killing it.

    ``scope`` is one of ``SCOPES``. ``filter`` is the pattern of a permission rule on the tool's main
    input (the command of a shell call): the harness starts the hook only for a call that matches it. It
    needs a matcher that selects one tool, because a rule names its tool. ``observe`` marks a hook whose
    record no synchronous hook of the same turn reads, so it may run async. ``entry`` is one of
    ``ENTRIES``. ``alone`` lists the harnesses on which the hook gets a process of its own instead of sharing
    its matcher's: Codex keys a hook's trust by its entry, so a hook that must not change another's trust takes an
    entry of its own.
    """

    name: str
    event: str
    matcher: tuple[str, ...] = ()
    argv: tuple[str, ...] = ()
    harnesses: tuple[str, ...] = HARNESSES
    closed: bool = False
    timeout: int = 10
    scope: str = "scoped"
    filter: str = ""
    observe: bool = False
    entry: str = ""
    alone: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.scope not in SCOPES:
            raise ValueError(f"{self.name}: scope {self.scope!r} is not one of {SCOPES}")
        if self.entry and self.entry not in ENTRIES:
            raise ValueError(f"{self.name}: entry {self.entry!r} is not one of {ENTRIES}")
        if self.filter and len(self.matcher) != 1:
            raise ValueError(f"{self.name}: a filter needs a matcher with one selector, because a rule names its tool")
        if self.scope == "always" and (not is_backend_matcher(self.matcher) or self.filter or self.entry):
            raise ValueError(f"{self.name}: an always hook is the plugin's own server matcher and nothing else")


#: Why each tool hook carries the filter it does (``Hook.filter``) or none. A rule is written only where it
#: keeps the hook's behaviour: a harness that reads ``if`` skips the hook for a call the rule does not fit, so
#: a rule that could miss a call the hook acts on would switch the hook off for it.
#:
#: * ``plugin_root`` on the shell: acts only on a command that holds ``${CLAUDE_PLUGIN_ROOT}``, so the rule is
#:   the substring ``CLAUDE_PLUGIN_ROOT`` (a superset; the hook still checks the exact token).
#: * ``plugin_root`` on the file reader: acts on the same token in a path, and a path rule is a gitignore
#:   glob that cannot match a token in the first directory name, so there is no rule.
#: * ``cron_gate`` on the shell: stamps only the command that runs ``flow-window.sh arm``, so the rule is
#:   ``*flow-window.sh*``. On ``CronCreate`` it denies every heartbeat cron, so there is no rule.
#: * ``denial_watch``: completes a denied raw sanction write or a denied ``flow-window.sh`` call, two
#:   patterns one rule cannot name, so there is no rule.
#: * ``ask_guard`` and ``ask_answered`` act on every call of the ask tools their matcher names.
#: * ``pending_register`` acts on a backgrounded call, which no rule on the input can tell.
#: * ``flow_autoarm`` acts on a skill name the gate already reads; a rule per skill would restate the list.
#: * ``regen_gate``, ``regen_spent``, ``command_run_id``, ``placeholder_guard`` and ``halt_gate`` are ``always`` hooks: their anchored
#:   matcher, which names the backend tools they act on, is the only filter.
#: * ``shell_syntax_guard`` acts only on a command that holds ``${(``, so the rule is ``*${(*``. The harness runs
#:   the hook for any command it cannot parse whatever the rule says, and a command with the construct is one it
#:   cannot parse, so the rule keeps every call the guard acts on and skips the ordinary ones.
#: Every registered hook, in registration order. The order is the position of the hook in a
#: generated file: Codex keys a hook's trust by its position, so a new hook goes at the END, a retired
#: one is deleted, and no entry moves. Moving one re-asks every Codex user to approve every hook
#: behind the move.
HOOKS: tuple[Hook, ...] = (
    # ``regen_gate`` decides on ``generate_scenarios_start`` only; it shares the job-start matcher so it runs in
    # the one process ``command_run_id`` starts there.
    Hook("regen_gate", "PreToolUse", JOB_START, scope="always"),
    Hook("regen_spent", "PostToolUse", ("backend:generate_scenarios_start",), scope="always"),
    Hook("session_glossary", "SessionStart"),
    Hook("flow_arm", "UserPromptSubmit", entry="prompt"),
    # Codex has no skill tool: a Codex flow arms through the skill's own Step-0 line.
    Hook("flow_autoarm", "PreToolUse", ("skill",), harnesses=("claude", "copilot"), entry="skill"),
    Hook("flow_cleanup", "SessionEnd", observe=True),
    # Work in flight: a backgrounded shell, a spawned subagent, a watch, a backgrounded MCP call and
    # the scheduled heartbeat; then the prompt and subagent events that end or bound that work. Codex
    # never wakes the model when such work finishes, so there is nothing for a register to hold a turn for.
    Hook("pending_register", "PostToolUse", ("shell", "subagent", "monitor", "cron", "mcp:*", "shell_read"), harnesses=("claude", "copilot")),
    Hook("pending_register", "UserPromptSubmit", harnesses=("claude", "copilot")),
    Hook("pending_register", "SubagentStart", harnesses=("claude", "copilot")),
    Hook("pending_register", "SubagentStop", harnesses=("claude", "copilot")),
    # The flow guards. Claude Code and Copilot ask through a tool that blocks, so its result says whether
    # the user answered. A Codex flow asks and ends its turn on the question: ``ask_guard`` checks the
    # sanction on the async ask and denies the blocking one, ``report_guard`` spends the sanction at the
    # turn end, and the answer is the next prompt, read by the ``UserPromptSubmit`` entry below.
    Hook("ask_guard", "PreToolUse", ("ask",)),
    Hook("ask_answered", "PostToolUse", ("ask",), harnesses=("claude", "copilot")),
    Hook("report_guard", "Stop"),
    Hook("plugin_root", "PreToolUse", ("shell",), harnesses=("claude", "copilot"), filter="*CLAUDE_PLUGIN_ROOT*", entry="token"),
    Hook("plugin_root", "PreToolUse", ("read",), harnesses=("claude", "copilot"), entry="token"),
    Hook("ask_answered", "UserPromptSubmit", harnesses=("codex",)),
    # Dropped-turn recovery and the auto-mode permission classifier exist on Claude Code only. A turn that
    # ends on an API error fires ``StopFailure``; ``flow_interrupt`` records it and ``flow_heartbeat``
    # releases the flow's scheduled prompt for that one drop. ``cron_gate`` and ``denial_watch`` keep that
    # scheduled prompt (a ``CronCreate`` call) and the flow's sanction writes out of the classifier's way.
    # ``flow_heartbeat`` runs open: once it knows a prompt is the heartbeat's, the handler blocks on any
    # failure itself, because blocking is the safe side there and an erased user prompt is not.
    Hook("cron_gate", "PreToolUse", ("shell",), harnesses=("claude",), filter="*flow-window.sh*"),
    Hook("cron_gate", "PreToolUse", ("cron",), harnesses=("claude",)),
    Hook("flow_heartbeat", "UserPromptSubmit", harnesses=("claude",)),
    Hook("flow_interrupt", "StopFailure", harnesses=("claude",), observe=True),
    Hook("denial_watch", "PermissionDenied", ("shell", "cron"), harnesses=("claude",)),
    # The session pin: the backend origins and write roots the plugin's scripts trust, recorded at the
    # start of every session and dropped at its end. The scripts it protects run on all three harnesses,
    # so it is registered on all three. Open: a pin that could not be written leaves "no pin", which
    # every consumer already reads as "refuse", so failing the hook would add nothing.
    Hook("session_pin", "SessionStart"),
    Hook("session_pin", "SessionEnd", observe=True),
    # The command run id on a backend job start. Codex applies a rewrite only together with ``allow`` (see
    # ``plugin_root``), so a Codex call gets the id from the stdio bridge, which Copilot also goes through.
    Hook("command_run_id", "PreToolUse", JOB_START, harnesses=("claude", "copilot"), scope="always"),
    # Claude Code's Bash safety parser cannot analyse zsh parameter flags (``${(P)v}``) and stops to ask the user;
    # the hook refuses them with the portable rewrite first. The other harnesses run no such parser.
    Hook("shell_syntax_guard", "PreToolUse", ("shell",), harnesses=("claude",), filter="*${(*"),
    # An edit-session message sent with an unfilled ``{NAME}`` placeholder is refused once (``_placeholder.py``);
    # ``scripts/mcp_call.py`` and the stdio bridge run the same check for the calls no hook sees.
    Hook("placeholder_guard", "PreToolUse",
         ("backend:edit_session_start", "backend:interact_edit_session", "backend:interact_with_scenario_request"), scope="always"),
    # No job starts over a /simulate run halted on a stop (contact, capacity or credit) until the next message a person
    # types: that message clears a capacity or credit halt and is stamped on a contact one. It shares the job-start matcher
    # with ``regen_gate``, so it runs in the one process that hook starts there. On Codex it is its own entry, so the
    # ``regen_gate`` entry's command and trust do not depend on it.
    Hook("halt_gate", "PreToolUse", JOB_START, scope="always", alone=("codex",)),
    # A failed backend call or poll: the model is told at that moment to say nothing about it and what the next step is
    # (``hooks/failure_notice.py``). Claude Code reports a tool error on ``PostToolUseFailure`` and a failed job envelope on
    # ``PostToolUse``; Codex has no failure event and Copilot reports a failed result on ``PostToolUse``, so the failure
    # entry is Claude's alone. That Copilot passes a PostToolUse ``additionalContext`` to the model is not confirmed.
    Hook("failure_notice", "PostToolUse", ("backend:*", "shell")),
    Hook("failure_notice", "PostToolUseFailure", ("backend:*", "shell"), harnesses=("claude",)),
)


def has_heartbeat(harness: str) -> bool:
    """Whether ``harness`` can schedule the dropped-turn heartbeat: it has the scheduled-prompt tool.

    Advice to set the heartbeat is given only where this holds; ``scripts/flow-window.sh`` spells the
    same set in its ``_has_heartbeat``, which ``hooks/tests/test_heartbeat_by_harness.py`` pins to this.
    """
    return harness in TOOLS["cron"]


def is_closed(hook: str) -> bool:
    """Whether ``hook`` denies when it cannot decide. A name the registry does not list is open."""
    return any(entry.closed for entry in HOOKS if entry.name == hook)
