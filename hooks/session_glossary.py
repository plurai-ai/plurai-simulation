"""SessionStart: tell the model which tool each capability word of the plugin's prose means here.

Skills and hook messages say what they need ("ask the user", "wait in the background") and never
name a harness's tool. This hook injects, once per session, a short glossary that maps each
capability word to the tool or fallback of the harness the session runs on. ``RAW_TOKENS`` is the
other half of that contract: the spellings that must not appear in model-facing text because they
belong to one harness, each with the capability word that replaces it
(``scripts/dev/check_harness_prose.py`` enforces it).

The tool names below are what the model calls, which is not always what a hook sees: Copilot's
ask tool is ``ask_user`` and reaches hooks as ``AskUserQuestion``, so the registry spells the
latter. The backend tool prefix is the registry's, the one place it is written down.
"""

from __future__ import annotations

import shlex
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import _platform
import registry
from plugin_root import spelled_root

if TYPE_CHECKING:
    from _harness.model import Decision, Event

#: How each harness is named to its model.
HARNESS_NAMES = {"claude": "Claude Code", "copilot": "GitHub Copilot CLI", "codex": "Codex CLI"}


@dataclass(frozen=True)
class Capability:
    """One word of the plugin's prose. ``phrase`` is how skills and hook messages say it;
    ``means`` gives, per harness, what it stands for there. ``when`` says on which harnesses the word
    is defined at all: a harness without the capability is told nothing about it, because the prose
    that uses the word is printed only where it can be acted on."""

    phrase: str
    means: Callable[[str], str]
    when: Callable[[str], bool] = lambda harness: True
    #: What the word means on native Windows, where that differs; ``None`` keeps ``means``.
    on_windows: Callable[[str], str] | None = None
    #: What the word means under a declared headless operator, where that differs (``None`` from the function,
    #: or no function, keeps the other meanings). It wins over ``on_windows``.
    on_headless: Callable[[str], str | None] | None = None


def available(capability: Capability, harness: str) -> bool:
    """Whether ``capability`` is defined in the glossary of ``harness``."""
    return capability.when(harness)


@dataclass(frozen=True)
class RawToken:
    """A spelling that belongs to one harness. ``pattern`` is a regex; ``capability`` the phrase
    that replaces it, or empty when the line is reworded instead (``hint`` says how).
    ``hooks_only`` tokens are checked in hook messages and left alone in skills."""

    name: str
    pattern: str
    capability: str
    hint: str
    hooks_only: bool = False


def _per_harness(claude: str, copilot: str, codex: str) -> Callable[[str], str]:
    table = {"claude": claude, "copilot": copilot, "codex": codex}
    return lambda harness: table[harness]


#: The plugin's name as Copilot's qualified command form spells it.
PLUGIN_NAME = "plurai-simulation"


def command_spelling(harness: str, name: str) -> str:
    """What the user types to run the flow command ``name`` on ``harness``.

    Skill text names every command ``/<name>``, which is right on Claude Code only. Copilot takes
    a bare ``/<name>`` as an unknown command when arguments follow (its picker guesses the nearest
    skill when none do), so the plugin-qualified form is the one that runs. Codex has no slash form
    of a plugin skill; it takes ``$<name>``.

    Claude Code keeps the bare form, the one the README documents. A hand-off reason in that spelling is read by
    the model, which runs the next stage through the ``Skill`` tool under the qualified skill name
    (``plurai-simulation:<name>``) that the tool lists; the bare slash form is for the user.
    """
    if harness == "copilot":
        return f"/{PLUGIN_NAME}:{name}"
    if harness == "codex":
        return f"${name}"
    return f"/{name}"


def respell_commands(text: str, harness: str) -> str:
    """``text`` with each ``/<name>`` that names one of this plugin's skills spelled the way ``harness`` types it.

    Skill text and the plugin's stored routes write every command ``/<name>``. A name preceded by a word character
    or a slash (a path), or followed by one (a file such as ``simulate_continue.py``), is not a command and stays; a
    full stop that ends the sentence is not part of the name.
    """
    if harness not in ("copilot", "codex"):
        return text
    import re  # noqa: PLC0415

    from flow_arm import plugin_skills  # noqa: PLC0415 - flow_arm imports the registry, as this module does

    skills = plugin_skills()
    if not skills:
        return text  # an empty alternation would match every lone slash
    names = "|".join(re.escape(n) for n in skills)
    return re.sub(rf"(?<![\w/.:$-])/({names})(?![\w-]|\.\w|/)", lambda m: command_spelling(harness, m.group(1)), text)


#: Said on every harness: a skill with no user command is a step the model takes, never one it hands over.
_BUILDING_BLOCK = (
    "A building-block skill (`simulation-setup`, `edit-evals` and the like) has no user command: never name it "
    "as a next step; name its entry point (`simulation-scan-agent` sets the agent up) or "
    'say "ask me to <task>".'
)


def _flow_command(harness: str) -> str:
    typed = f"type it as `{command_spelling(harness, 'simulation-run')}`."
    if harness == "claude":
        return f"{typed} {_BUILDING_BLOCK}"
    return (
        f"{typed} Skill text writes every command `/<name>`; wherever you name one to the user "
        f"(a next step, a hand-off, a remedy), write `{command_spelling(harness, '<name>')}`. {_BUILDING_BLOCK}"
    )


def _backend_tool(harness: str) -> str:
    prefix = registry.BACKEND_PREFIXES[harness][0]
    base = f"the plurai server's tool named `<verb>`; here its full name is `{prefix}<verb>`."
    if harness in ("copilot", "codex"):
        return base + " If only `bridge_status` is listed, call it: the bridge refused its configuration and says what to change."
    return base


#: "wait in the background" where a headless session ends with its turn (``registry`` ``wakes_when_headless``).
_WAIT_WHEN_THE_TURN_ENDS_IT = (
    "nothing wakes you once this turn ends: the session ends with it and kills its background commands. A poll "
    "(`poll_job.sh`, `run_wait.sh`): run it in the FOREGROUND with `timeout: 600000` on the Bash call and re-run it "
    "on exit 2, in this turn. A subagent: spawn it with `run_in_background` false."
)


#: The headless meaning per harness, where its own meaning would be wrong there. Copilot's own entry already says
#: nothing wakes it and to keep the turn open on bounded polls, which is the headless rule too; Codex never wakes.
_HEADLESS_WAIT = {"claude": _WAIT_WHEN_THE_TURN_ENDS_IT}


def _wait_when_headless(harness: str) -> str | None:
    return None if registry.CAPABILITIES[harness]["wakes_when_headless"] else _HEADLESS_WAIT.get(harness)


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        "ask the user",
        _per_harness(
            "the `AskUserQuestion` tool; one call carries every question of the gate.",
            "the `ask_user` tool; it blocks until the user answers. Use the default interactive mode.",
            "no blocking ask tool: sanction the gate, then ask one plain-text question with its options "
            "(or, where you have `request_user_input_async`, through it) and end the turn; the reply is "
            "the next prompt. Never call `request_user_input`.",
        ),
    ),
    Capability("backend tool", _backend_tool),
    Capability(
        "wait in the background",
        _per_harness(
            "run the shell command with `run_in_background` set; the session re-invokes you when it exits.",
            "nothing wakes you. Start the shell command with `mode: async` and poll it with `read_bash`, "
            "in bounded steps; keep the turn open.",
            "nothing wakes you. Start it with `exec_command` and poll it with `write_stdin`, in bounded "
            "steps; keep the turn open.",
        ),
        # Copilot's Windows shell is PowerShell, polled with ``read_powershell`` (there is no ``read_bash``).
        on_windows=_per_harness(
            "run the shell command with `run_in_background` set; the session re-invokes you when it exits.",
            "nothing wakes you. Start the shell command with `mode: async` and poll it with `read_powershell`, "
            "in bounded steps; keep the turn open.",
            "nothing wakes you. Start it with `exec_command` and poll it with `write_stdin`, in bounded "
            "steps; keep the turn open.",
        ),
        on_headless=_wait_when_headless,
    ),
    Capability(
        "end the run",
        _per_harness(
            "end your turn on the message; nothing continues the session.",
            "a plain final message does not end an autopilot run: Copilot answers it with a nudge to keep "
            "working. Call `task_complete` with the message as its `summary`, and make no call after it.",
            "end your turn on the message; nothing continues the session.",
        ),
    ),
    Capability(
        "set a heartbeat",
        lambda harness: "the `CronCreate` tool, with the prompt the flow's arm line names.",
        when=registry.has_heartbeat,
    ),
    Capability(
        "list the MCP servers",
        _per_harness("`claude mcp list` in a shell.", "`copilot mcp list` in a shell.", "`codex mcp list` in a shell."),
    ),
    Capability(
        "update the plugin",
        _per_harness(
            "`claude plugin update plurai-simulation`, then restart the session.",
            "`copilot plugin update plurai-simulation`, then restart the session.",
            "`codex plugin marketplace upgrade`, then restart the session.",
        ),
    ),
    Capability(
        "run a skill",
        _per_harness(
            "the `Skill` tool, by the skill's name.",
            "the `skill` tool, by the skill's name.",
            "load it by name; it takes no arguments.",
        ),
    ),
    Capability("run a flow command", _flow_command),
    Capability(
        "write the PRD facts",
        lambda harness: (
            "run `scripts/scaffold_rule_facts.py --profile` first (one `rule` fact per instruction, the agent's own bytes, "
            "and the measured facts it finds), add only measured facts, then `lint_prd.py` until it exits 0; "
            "worked block: `references/prd_authoring_contract.md`."
        ),
    ),
    # A skill, reference or template file the flow says to read "to its end" can outrun one tool output. Codex cuts an
    # output near 10k tokens, keeps the head and the tail and drops the middle, and `sed -n` prints no line numbers,
    # so the gap goes unseen; a numbered slice makes it visible and bounds each read under the cut.
    Capability(
        "read a long file",
        _per_harness(
            "`Read` it, then `Read` again with `offset` set to the line after the last one shown, until a read ends "
            "at the last line.",
            "read it in slices of at most 250 lines with line numbers, from line 1 to the last line.",
            "`nl -ba <file> | sed -n '<first>,<last>p'` in slices of at most 250 lines, to the last line (`wc -l`): "
            "an output is cut near 10k tokens, losing the middle: re-read any slice that says truncated.",
        ),
    ),
    # Codex's workspace-write sandbox leaves `.git` read-only, so a git command that writes the index
    # fails there, and asking to run it unsandboxed puts an approval prompt in front of the operator
    # for a step the plugin has a read-only answer to (`stage-inputs.sh`).
    Capability(
        "git writes",
        lambda harness: (
            "the workspace-write sandbox keeps `.git` read-only, so `git add` and `git rm` fail and running "
            "them unsandboxed costs the operator an approval prompt. Run none of your own. When "
            "`stage-inputs.sh` prints `NOT STAGED`, give the operator its `git add` command in your summary."
        ),
        when=lambda harness: harness == "codex",
    ),
)

RAW_TOKENS: tuple[RawToken, ...] = (
    RawToken("AskUserQuestion", r"AskUserQuestion", "ask the user", "ask the user"),
    RawToken("CronCreate", r"CronCreate", "set a heartbeat", "set a heartbeat"),
    RawToken("run_in_background", r"run_in_background", "wait in the background", "wait in the background"),
    RawToken("mcp__plugin_", r"mcp__plugin_", "backend tool", "the `<verb>` backend tool"),
    RawToken("claude mcp list", r"claude mcp list", "list the MCP servers", "list the MCP servers"),
    RawToken("claude mcp add", r"claude mcp add", "", "reword: the plugin registers its MCP server itself"),
    RawToken("claude plugin update", r"claude plugin update", "update the plugin", "update the plugin"),
    RawToken("Claude Code", r"Claude Code", "", "name no harness, or take its name from the event", hooks_only=True),
)


def _root_line(root: str) -> str:
    """The rule that gives a Codex model the plugin root, or "" when the root cannot be named safely.

    Codex fills the token in nowhere and has no rewrite that its approval flow lets through, so the
    model sets the variable itself in each command. The root is quoted for the shell, and a root with
    a control character is not injected at all: it could not stay one line of this text.
    """
    if not root or any(ord(char) < 32 or ord(char) == 127 for char in root):
        return ""
    return (
        "- plugin root: `${CLAUDE_PLUGIN_ROOT}` is not set in your shell here. Start every shell command "
        f"that uses it with `export CLAUDE_PLUGIN_ROOT={shlex.quote(root)};`."
    )


def _directory_line(root: str, *, headless: bool = False, home: str = "") -> str:
    """The Copilot line that names the plugin directory, or "" when the root cannot be named safely.

    Copilot asks the user to allow path access for every shell command and file read that reaches the plugin's scripts
    and reference files, because the install sits outside the project; ``--add-dir`` (or ``--allow-all-paths``) at launch
    ends those prompts. The model cannot change Copilot's launch flags, so it tells the user, once.

    Under a declared headless operator nobody answers a path prompt, so a denied read is final: the line says to end
    the run naming the launch flags (the plugin folder and ``home``, the plugin's ``.plurai`` folder) rather than
    work around the denial. The documented headless launch passes both, so this is a backstop.
    """
    if not root or any(ord(char) < 32 or ord(char) == 127 for char in root + (home if headless else "")):
        return ""
    if headless:
        folders = f"`--add-dir {shlex.quote(root)}`" + (f" and `--add-dir {shlex.quote(home)}`" if home else "")
        return (
            f"- plugin directory: the plugin's scripts and reference files are under `{root}`, outside the project. Nobody "
            "answers a path prompt in this session: if a command or read under it is DENIED, the denial is final. End the "
            f"run with one line saying to start Copilot with {folders}; never work around it."
        )
    return (
        f"- plugin directory: the plugin's scripts and reference files are under `{root}`, outside the project, so Copilot "
        "asks the user to allow path access to them. If it does, tell the user once that starting Copilot with "
        f"`--add-dir {shlex.quote(root)}` (or `--allow-all-paths`) ends those prompts."
    )


@dataclass(frozen=True)
class WindowsShell:
    """What the Copilot-on-Windows shell rule names, resolved once at SessionStart: the absolute Git Bash
    (``None`` when there is none) and the directory the step files go in."""

    bash: str | None
    temp: str
    tag: str = ""


def windows_shell(session_id: str = "", env: Mapping[str, str] | None = None) -> WindowsShell | None:
    """The Windows facts of this session, or ``None`` off native Windows (where the glossary is unchanged)."""
    if not _platform.WINDOWS:
        return None
    bash = _platform.git_bash(env=env)
    tag = "".join(char for char in session_id if char.isalnum())[:8]
    return WindowsShell(bash=bash, temp=tempfile.gettempdir(), tag=tag)


def _powershell_quoted(text: str) -> str:
    """``text`` as one PowerShell single-quoted word. PowerShell also ends such a word on a typographic
    single quote, so each quote character is doubled."""
    return "'" + "".join(char * 2 if char in "'\u2018\u2019\u201a\u201b" else char for char in text) + "'"


def _windows_shell_line(root: str, shell: WindowsShell) -> str:
    """The Copilot-on-Windows rule: the model's shell is PowerShell, and every bash command or block a skill gives
    runs as a step file under the absolute Git Bash, never a bare ``bash`` (with only ``Git\\cmd`` on ``PATH`` that
    resolves to the WSL launcher in ``System32``). The plugin root is set by the file's first line, spelled
    literally: a step file is written with the file tool, which no hook rewrites. "" when a path cannot be one line."""
    if any(ord(char) < 32 or ord(char) == 127 for char in root + (shell.bash or "") + shell.temp):
        return ""
    if not shell.bash:
        return (
            f"- shell: your shell here is PowerShell and the skills' shell steps are bash, but {_platform.GIT_BASH_MISSING}. "
            "Run no skill step; tell the user that and to restart the session after fixing it."
        )
    temp = shell.temp.replace("\\", "/").rstrip("/")
    step = f"{temp}/plurai-{shell.tag + '-' if shell.tag else ''}<n>.sh"
    bash = shell.bash.replace("\\", "/")
    return (
        "- shell: your shell here is PowerShell; the skills' shell commands and blocks are bash. Run each one with Git Bash, "
        "never a bare `bash` (here it can start WSL): write the block unchanged with `create` to a new file "
        f"`{step}` (<n> counts up from 1), its first line `export CLAUDE_PLUGIN_ROOT={shlex.quote(root.replace(chr(92), '/'))}`, "
        f"then run `& {_powershell_quoted(bash)} {_powershell_quoted(step)}`."
    )


def _meaning(capability: Capability, harness: str, windows: WindowsShell | None, headless: bool) -> str:
    """What ``capability`` means here: the headless meaning where there is one, else the Windows one, else ``means``."""
    if headless and capability.on_headless:
        meant = capability.on_headless(harness)
        if meant is not None:
            return meant
    return (capability.on_windows if windows and capability.on_windows else capability.means)(harness)


def render(harness: str, root: str = "", windows: WindowsShell | None = None, *, headless: bool = False, home: str = "") -> str:
    """The glossary for ``harness``: a header line, then one line per capability word, then on Codex
    the plugin-root rule for ``root``. ``windows`` (native Windows only) switches the words that differ
    there, and on Copilot adds the PowerShell rule. ``headless`` (a declared headless operator) switches the
    words that differ for one, and ``home`` is the plugin's ``.plurai`` folder the Copilot headless line names."""
    lines = [
        (f"Plurai simulation plugin on {HARNESS_NAMES[harness]}. Its skills and messages use these capability "
        "words; this is what each one means in this session:")
    ]
    lines.extend(
        f"- {capability.phrase}: {_meaning(capability, harness, windows, headless)}"
        for capability in CAPABILITIES
        if available(capability, harness)
    )
    if harness == "codex" and _root_line(root):
        lines.append(_root_line(root))
    directory = _directory_line(root, headless=headless, home=home)
    if harness == "copilot" and directory:
        lines.append(directory)
    if harness == "copilot" and windows and _windows_shell_line(root, windows):
        lines.append(_windows_shell_line(root, windows))
    return "\n".join(lines)


def _mark_session(event: Event) -> None:
    """Record in the session's flow directory that its hooks run, and which harness's they are.

    Step 0's readiness check (``hooks/_readiness.py``) reads it: a session whose SessionStart hook
    never ran has none of the plugin's guards. Best effort: a session that cannot be marked is
    refused later by that check, which is the safe direction, so a failure here must not cost the
    session its glossary.

    Claude Code's Bash tool may not export the session id, and the scripts then key the flow
    markers by ``$WORKDIR`` instead (the hooks read both places). The marker is written there too,
    when that directory already exists, so the check finds it on the path the window is on.
    ``flow_cleanup`` sweeps it with the rest.
    """
    from _workdir import HOOKS_READY_MARKER, flow_dir, resolve_workdir

    targets = []
    directory = flow_dir({"session_id": event.session_id})
    if directory is not None:
        targets.append(directory)
    if event.harness == "claude":
        workdir = resolve_workdir(Path(event.cwd) if event.cwd else None)
        if workdir is not None and workdir.is_dir():
            targets.append(workdir)
    for target in targets:
        try:
            target.mkdir(parents=True, exist_ok=True)
            (target / HOOKS_READY_MARKER).write_text(event.harness, encoding="utf-8")
        except OSError:
            pass


def handle(event: Event) -> Decision:
    """Inject the glossary of the harness that sent the event, and mark the session's hooks as running."""
    from _harness import model

    _mark_session(event)
    return model.context(render(event.harness, spelled_root(), windows_shell(event.session_id), headless=_headless(event), home=_plurai_home()))


def _headless(event: Event) -> bool:
    """Whether a headless operator is declared for this session. At SessionStart only the env var is sure to be
    readable (a first scan has written no config yet); an unreadable declaration reads as interactive."""
    try:
        import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path at call time
        from _workdir import resolve_workdir  # noqa: PLC0415

        return _run_driver.resolve(workdir=resolve_workdir(Path(event.cwd) if event.cwd else None)).headless
    except Exception:  # noqa: BLE001 - no declaration, the interactive glossary stands
        return False


def _plurai_home() -> str:
    """The plugin's ``.plurai`` folder (the flow state's parent), or "" when it cannot be named."""
    try:
        from _workdir import flow_root  # noqa: PLC0415

        return str(flow_root().parent)
    except Exception:  # noqa: BLE001 - the line then names the plugin folder alone
        return ""
