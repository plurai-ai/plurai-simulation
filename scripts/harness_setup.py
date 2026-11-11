#!/usr/bin/env python3
"""Print the exact configuration a harness needs before the simulation flows run on it.

    python3 harness_setup.py [claude|copilot|codex]

Without a harness name it takes the one the session variables name. The backend to configure is ``$PLURAI_URL``,
else the url of the saved login, else a placeholder: the same rule the SDK's ``plurai login`` follows, so the command
takes no url of its own.

Each flow's Step 0 refuses to start when one of these is missing (``hooks/_readiness.py``), and
names this command. Exit 0 prints the text; exit 64 is a usage error.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))

import _login_store
import _platform
import _readiness
import _workdir
from plugin_root import shell_path
from session_glossary import HARNESS_NAMES

USAGE = "usage: harness_setup.py [claude|copilot|codex]"
TOKEN = "    export PLURAI_TOKEN=<your token>"
PLACEHOLDER = "<backend url>"


def _plurai_home() -> Path:
    """The plugin's ``.plurai`` folder: the flow state's parent."""
    return _workdir.flow_root().parent


def _claude(url: str) -> list[str]:
    return [
        "Nothing to configure for the hooks, the tools or the shell: they load with the plugin install.",
        "",
        f"The MCP server dials ${{PLURAI_URL}}/mcp, which {HARNESS_NAMES['claude']} reads from the environment that launches it.",
        "So the url must be exported there even when you saved a login, which supplies the token and not the url:",
        "",
        f"    export PLURAI_URL={url}",
        "",
        "Then give the token in one of two ways.",
        "",
        "Either export it in the same environment:",
        "",
        TOKEN,
        "",
        "Or log in once: the first Plurai command signs you in when no login is saved. On a machine without a browser, save",
        "a dashboard key once instead, in a terminal of your own. The command asks for the key hidden and saves it:",
        "",
        f"    {_login(url)}",
        "",
        "A headless run (`claude -p`) ends with its turn, so declare that nobody is there, or the flow runs as",
        "interactive and a run it starts in the background dies with the turn:",
        "",
        '    PLURAI_SIM_NONINTERACTIVE=1 claude -p "/simulation-run"',
    ]


def _login(url: str) -> str:
    """The command that saves the token once, from a terminal of the user's own."""
    return f"python3 {ROOT}/scripts/plurai_login.py --url {url}"


def _token_ways(harness: str, url: str) -> list[str]:
    """The two ways to configure the backend, each command alone on its line.

    A command with a trailing comment is long enough to wrap in a terminal, and the wrapped half then
    runs as its own command; the explanation sits on the line above instead. The environment's url and
    token are one pair: exporting only one of them is an error, not a fallback to the saved login.
    """
    return [
        f"Either export the url and the token together, in the environment that launches {harness}:",
        "",
        f"    export PLURAI_URL={url}",
        TOKEN,
        "",
        "Or log in once: the first Plurai command signs you in when no login is saved. On a machine without a browser, save",
        "a dashboard key once instead, in a terminal of your own. The command asks for the key hidden and saves it:",
        "",
        f"    {_login(url)}",
    ]


def _token_status(harness: str) -> list[str]:
    """One line saying the token is already in the environment of this command, without its value."""
    if not _login_store.environment_token():
        return []
    return [
        "PLURAI_TOKEN is set in the environment of this command (its value is not shown). Give it as below",
        f"only if you launch {harness} from another environment.",
        "",
    ]


def _copilot(url: str) -> list[str]:
    lines = [
        "The hooks and the MCP server load with the plugin install; there is nothing to approve.",
        "",
        "Give the plugin the backend in one of two ways.",
        "",
        *_token_ways("Copilot", url),
        "",
        "Copilot asks you to allow path access for each command and file read that reaches the plugin's scripts and reference",
        "files, because the install sits outside your project. Allow its folder once, when you launch Copilot:",
        "",
        f"    copilot --add-dir {shell_path(ROOT)}",
        "",
        "(`copilot --allow-all-paths` allows every path.)",
        "",
        *_copilot_headless(),
    ]
    return lines


def _copilot_headless() -> list[str]:
    """The unattended launch: declared headless, autopilot, both folders allowed, the ``.plurai`` one made first. The
    PowerShell form is in ``docs/install-windows.md``; on Windows this points there."""
    home = shell_path(_plurai_home())
    lines = [
        "For a headless run (nobody at the terminal), declare it, run in autopilot and allow both the plugin's folder and",
        "the plugin's .plurai folder: nobody answers a path prompt there, so a read outside them is denied. Make the",
        ".plurai folder first; Copilot refuses to start when an --add-dir folder is missing. `-p` starts every flow",
        "command but `simulate`, which the model cannot load by name: run its stages one by one.",
        "",
    ]
    if _platform.WINDOWS:
        return [*lines, f"    In PowerShell: the headless commands in {shell_path(ROOT / 'docs' / 'install-windows.md')}, with the folders",
                f"    {shell_path(ROOT)} and {home}."]
    return [*lines, f"    mkdir -p {home}",
            f'    PLURAI_SIM_NONINTERACTIVE=1 copilot -p "/plurai-simulation:<command> <arguments>" --autopilot --allow-all-tools '
            f"--add-dir {shell_path(ROOT)} --add-dir {home}"]


def _codex(url: str) -> list[str]:
    config = str(_readiness.codex_config_path(os.environ))
    lines = [
        "Codex needs these settings. Without them the flows refuse to start, each naming the one that is missing.",
        "",
        "1. Backend. Give it to the plugin in one of two ways. Without one the backend tools are replaced by a",
        "   `bridge_status` tool that says what is missing.",
        "",
        *_token_ways("Codex", url),
        "",
        "2. Hook trust. Codex runs a plugin hook only after you trust it. The first start after you install or",
        '   update the plugin shows "Hooks need review": press "Trust all and continue" once. The hooks run in',
        "   that same session, so there is nothing to restart. Skills work without trust; the flows refuse to",
        '   start until the hooks are trusted. If you chose "Continue without trusting", Codex asks again at the',
        "   next start (or open `/hooks` and press `t`).",
        "",
        "3. Shell network. Codex's sandbox blocks the shell's network by default, and the scripts and the runner",
        "   reach the backend from the shell. Run Codex with the `workspace-write` sandbox and add to",
        f"   {config}:",
        "",
        "[sandbox_workspace_write]",
        "network_access = true",
        "",
        "   Or open it for one session only, with no change to config.toml:",
        "",
        "    codex -c sandbox_workspace_write.network_access=true",
        "",
        "   The setting gives the shell, inside the sandbox, network access to any host, loopback included. Writes stay",
        "   limited to the workspace and the temp folders, and no command runs outside the sandbox.",
    ]
    lines += [
        "",
        "4. Shell variables. Codex may hide the backend variables from the shell tool, for instance with",
        '   `inherit = "core"` in [shell_environment_policy], and the scripts and the runner read them from the',
        "   shell. `[shell_environment_policy.set]` puts them into every shell command. It writes the token into",
        f"   {config} in plain text, so use a development token only:",
        "",
        "[shell_environment_policy.set]",
        'PLURAI_TOKEN = "<your development token>"',
        f'PLURAI_URL = "{url}"',
    ]
    return lines


#: What a Claude Code session that needs nothing more is told, instead of the whole configuration.
CLAUDE_READY = "Plurai is set up for Claude Code: nothing else to configure."


def claude_ready(env: Mapping[str, str]) -> bool:
    """Whether Claude Code needs nothing more: a usable backend, an MCP url, and the two on one backend."""
    return (
        _login_store.resolve_backend(env).configured
        and bool(_login_store.effective_mcp_url(env))
        and not _login_store.claude_code_mismatch(env)
    )


def render(harness: str, url: str = "", env: Mapping[str, str] | None = None) -> str:
    """The setup text for ``harness``. ``url`` is the backend to configure; empty prints a placeholder.

    A Claude Code session that is ready (``claude_ready``) gets one line: the rest would only repeat what already works.
    """
    if harness == "claude" and claude_ready(os.environ if env is None else env):
        return f"{CLAUDE_READY}\n"
    url = _login_store.api_root(url) or PLACEHOLDER
    if harness == "claude":
        body = _claude(url)
    elif harness == "copilot":
        body = _copilot(url)
    else:
        body = _codex(url)
    status = _token_status(HARNESS_NAMES[harness])
    return "\n".join([f"Plurai simulation: setup for {HARNESS_NAMES[harness]}", "", *status, *body, ""])


def main(argv: list[str]) -> int:
    harness, url = "", os.environ.get("PLURAI_URL", "") or _login_store.resolve_backend().url
    for arg in argv:
        if arg in HARNESS_NAMES and not harness:
            harness = arg
        else:
            print(USAGE, file=sys.stderr)
            return 64
    if not harness and _readiness.session_var(os.environ):
        harness = _workdir.harness(os.environ)
    if not harness:
        print(f"{USAGE}\nno harness named, and no session variable names one: pass claude|copilot|codex", file=sys.stderr)
        return 64
    sys.stdout.write(render(harness, url))
    return 0


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main(sys.argv[1:]))
