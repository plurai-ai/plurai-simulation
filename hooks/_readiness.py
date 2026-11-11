"""Step 0 readiness: is this session set up so a flow's guards and backend calls work?

``scripts/flow-window.sh ready <command>`` runs this module. Each flow's Step 0 calls it right after
arming, and it answers either ``ready`` or one line per unmet check, each naming the fix. There are two kinds of line.

* ``NOT READY (<check>)``: something only the user can change, usually with a restart or an edit to a config file. A
  flow that is not ready stops there: it would otherwise run with its guards silently off or reach a backend nobody
  chose.
* ``SETUP (<check>)``: something the ``harness-setup`` skill fixes in the session itself: ``login`` (no usable saved
  login) and ``connect`` (Claude Code has a login but not the backend's tools). The flow runs the skill and goes on.
  The user sees nothing for a session that is already set up.
* ``ready, limited (hooks off)``: Claude Code skipped the plugin's hooks, and the flow runs without them
  (``docs/limited-mode.md``). An organization's managed settings that run only the hooks an admin approved
  (``allowManagedHooksOnly``) or none (``disableAllHooks``) do that without a word to the session; without limited mode
  no flow could run there at all. The first time in a session it is followed by one ``WARN (hooks)`` line for the user,
  the plugin root the skills' commands need, and the glossary the SessionStart hook would have injected; the check
  records that it said so in ``.hooks_degraded`` (``_workdir.limited_mode`` reads it). Copilot and Codex keep the
  refusal.

Setup cannot help until a ``NOT READY`` cause is gone, so when any ``NOT READY`` line exists only those print; when
none does but a ``SETUP`` line exists, only the ``SETUP`` lines print, and the limited-mode lines wait for the check
that runs after setup.

The checks, in the order they print:

* ``hooks``: the SessionStart hook ran in this session (its marker is in the flow directory), and on
  Codex every hook the plugin registers is trusted in the user's ``config.toml`` (Codex writes that
  when the user chooses "Trust all and continue" at startup). Without the hooks
  ``ask_guard``, ``report_guard`` and ``regen_gate`` never run. On Claude Code a session with a session id and no
  marker runs in limited mode instead of stopping, unless the session the process tree names did write its marker.
* ``backend tools``: the backend tools are available. A shell cannot see the model's tool list, so
  the caller reports a missing tool with ``--no-backend-tools``; what the files can tell is checked
  here too (a manifest with no backend server, or whose bridge script is missing). On Claude Code, when the
  organization's managed settings keep the plugin's MCP server out (``_mcp_policy.py``), ``ready`` comes with one
  ``WARN (backend tools)`` line that sends the model to its tool list; the stop comes only from ``--no-backend-tools``,
  and then names the admin's fix instead of a reconnect.
* ``network``: the shell's network is open. Codex's default sandbox blocks it, loopback included, and
  the scripts and the runner reach the backend from the shell.
* ``backend``: a backend is configured (``PLURAI_URL`` and ``PLURAI_TOKEN``, or the saved login), the
  configuration is not partial, and the platform is supported. A session with no usable login needs the ``login``
  setup, or, headless (``PLURAI_SIM_NONINTERACTIVE=1``), where nobody can sign in, is refused with one plain line.
* ``one backend``: the url MCP calls use equals the one the shell side uses. On Claude Code the MCP url is
  the exported ``PLURAI_URL`` (else the manifest default), which a saved login for another backend cannot move; on
  Codex it can differ when a user-level server entry replaces the bridge.
* ``window``: the flow window is armed. Without it the guards allow everything while the hooks look
  healthy.

Everything is read from the environment and the harness's own config files, which are parsed with
the strict reader in this package (hooks run under the oldest interpreter ``python3`` may be, so
no ``tomllib``). A file that cannot be read counts as unmet, never as satisfied: the Codex config is
read whole or not at all, and the checks that depend on it refuse when it is not.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# `flow-window.sh ready` runs this file under `python3 -I`, which leaves the script's directory off sys.path.
_HOOKS = Path(__file__).resolve().parent
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import _codex_trust  # noqa: E402 - hooks/ is on sys.path above
import _platform  # noqa: E402
import registry  # noqa: E402
import _toml_min  # noqa: E402
import _workdir  # noqa: E402
from _workdir import FLOW_ROOT, HARNESS_OF_SESSION_VAR, HOOKS_DEGRADED_MARKER, HOOKS_READY_MARKER, SESSION_VARS  # noqa: E402
import _login_store  # noqa: E402
import _mcp_policy  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
FLOW_MARKER = ".plurai_flow_active"


NOT_READY = "not-ready"
SETUP = "setup"
#: The flow runs, without what the check names: Claude Code's hooks (limited mode). Never a stop.
DEGRADED = "degraded"


@dataclass(frozen=True)
class Refusal:
    """One unmet check: its name and what to do about it. ``kind`` says whether setup fixes it (``SETUP``), the flow runs
    without it (``DEGRADED``) or neither (``NOT_READY``)."""

    check: str
    text: str
    kind: str = NOT_READY

    def line(self) -> str:
        label = {SETUP: "SETUP", DEGRADED: "WARN"}.get(self.kind, "NOT READY")
        return f"flow-window: {label} ({self.check}): {self.text}"


def session_var(env: Mapping[str, str]) -> str:
    """The first of ``SESSION_VARS`` the shell exports, ``""`` for a shell outside every harness."""
    return next((var for var in SESSION_VARS if env.get(var, "").strip()), "")


def _name(harness: str) -> str:
    from session_glossary import HARNESS_NAMES

    return HARNESS_NAMES.get(harness, "the harness")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


#: Set by a run nobody is watching: no one can sign in, approve an install or type a reconnect.
HEADLESS_VARIABLE = "PLURAI_SIM_NONINTERACTIVE"

#: What a headless run with no login says. The run waits for no one, so it stops.
HEADLESS_LOGIN_LINE = "Plurai is not logged in. Sign in once in an interactive session, then run this again."
HEADLESS_CONNECT_LINE = "Plurai is not connected in this session. Start a new session, then run this again."


def _headless(env: Mapping[str, str]) -> bool:
    return (env.get(HEADLESS_VARIABLE) or "").strip() == "1"

_HOOKS_OFF = {
    "claude": "Check that the plugin is installed and enabled, then restart the session.",
    "copilot": "Check that the plugin is installed (its hooks load only from an install), then restart the session.",
    "codex": (
        "Every hook is trusted in config.toml, so this session most likely started before they were trusted "
        "or installed: restart Codex."
    ),
}


def read_json(path: Path) -> dict:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def relative_path(path: str) -> str:
    """A manifest path (``./hooks/codex.json``) as a path inside the plugin."""
    return path.removeprefix("./")


def codex_config_path(env: Mapping[str, str]) -> Path:
    """Codex's user config: ``$CODEX_HOME/config.toml``, ``~/.codex/config.toml`` by default."""
    home = env.get("CODEX_HOME", "").strip()
    return (Path(home) if home else Path(env.get("HOME") or os.path.expanduser("~")) / ".codex") / "config.toml"


def _read_codex_config(env: Mapping[str, str]) -> tuple[dict, str]:
    """The parsed user config and, when the file is there but outside the reader's subset, why not."""
    path = codex_config_path(env)
    try:
        text = path.read_text(encoding="utf-8-sig")  # a byte-order mark is not part of the first key
    except (OSError, UnicodeDecodeError):
        return {}, ""
    try:
        return _toml_min.loads(text), ""
    except _toml_min.Unreadable as problem:
        return {}, f"the Codex config at {path} cannot be read by this check ({problem})"


def codex_config(env: Mapping[str, str]) -> dict:
    """The parsed user config; empty when the file is missing or unreadable."""
    return _read_codex_config(env)[0]


def _unreadable(env: Mapping[str, str]) -> str:
    """Why the checks cannot read the user's Codex config, or ``""`` when they can (or it is absent).

    A key in a file that cannot be read is not a key that is unset: a check that depends on one refuses.
    """
    return _read_codex_config(env)[1]


def _trust(env: Mapping[str, str], plugin_root: Path) -> list[Refusal]:
    """Codex runs a hook only once trusted: every hook the plugin registers must be."""
    manifest = read_json(plugin_root / ".codex-plugin" / "plugin.json")
    name = manifest.get("name")
    source = relative_path(str(manifest.get("hooks") or "hooks/hooks.json"))
    hooks = read_json(plugin_root / source)
    if not name or not hooks:
        return [Refusal("hooks", f"cannot read the plugin's Codex manifest or its hook file ({source}) under {plugin_root}; reinstall the plugin.")]
    unreadable = _unreadable(env)
    if unreadable:
        return [Refusal("hooks", f"{unreadable}, so it cannot tell which hooks are trusted. Fix or remove that line, then restart the session.")]
    config = codex_config(env)
    path = codex_config_path(env)
    candidates = _codex_trust.marketplaces(config, name) or [""]
    best: tuple[int, dict, dict] | None = None
    fingerprint = _codex_trust.cached_fingerprint(plugin_root)  # the folder is hashed once, whatever the marketplaces
    for marketplace in candidates:
        expected = _codex_trust.expected_state(f"{name}@{marketplace}", hooks, source)
        standing = _codex_trust.status(config, expected, plugin_root, fingerprint)
        armed = sum(1 for s in standing.values() if s == _codex_trust.ARMED)
        if best is None or armed > best[0]:
            best = (armed, expected, standing)
    assert best is not None
    _, expected, standing = best
    unarmed = [(expected[key][1], s) for key, s in standing.items() if s != _codex_trust.ARMED]
    if not unarmed:
        return []
    fix = (
        'Restart Codex and choose "Trust all and continue" in the "Hooks need review" dialog it shows at '
        "startup; the hooks then run in that same session, and skills work without trust. If Codex shows no "
        "dialog, open `/hooks` and press `t` to trust them all (turn a hook that is off back on with Enter), "
        "then restart the session."
    )
    total = len(expected)
    if all(s == _codex_trust.MISSING for _, s in unarmed) and len(unarmed) == total:
        return [Refusal("hooks", f"none of the plugin's {total} hooks is trusted in {path}, so none runs. {fix}")]
    listed = ", ".join(f"{label} ({s})" for label, s in unarmed[:6])
    more = f" and {len(unarmed) - 6} more" if len(unarmed) > 6 else ""
    text = f"Codex has not trusted {len(unarmed)} of the plugin's {total} hooks, so they do not run: {listed}{more}. {fix}"
    recorded = [s for s in standing.values() if s != _codex_trust.MISSING]
    if recorded and all(s == _codex_trust.CHANGED for s in recorded):
        text += (
            " Every recorded hash differs from the one computed here: cannot verify hook trust on this Codex "
            "version; approve the hooks in `/hooks`."
        )
    if any(s == _codex_trust.PLUGIN_CHANGED for s in standing.values()):
        text += (
            " Codex also pins the plugin's files: after the plugin was updated, or when its files cannot be read, "
            "it skips these hooks until you approve them again in `/hooks`."
        )
    return [Refusal("hooks", text)]


def _started(own_id: str, harness: str) -> float:
    """When ``harness``'s SessionStart hook last wrote its marker for session ``own_id``; 0 when it did not."""
    if not own_id or "/" in own_id:
        return 0.0
    marker = FLOW_ROOT / own_id / HOOKS_READY_MARKER
    if _read(marker) != harness:
        return 0.0
    try:
        return marker.stat().st_mtime
    except OSError:
        return 0.0


def _inherited(env: Mapping[str, str]) -> list[Refusal]:
    """A session id inherited from an outer harness's shell, which hides this session's own.

    The shell takes the innermost harness to be the first of ``SESSION_VARS`` that is set. That is
    right for Copilot or Codex launched from a Claude Code shell, and wrong for the reverse: Claude
    Code launched from a Copilot or Codex shell inherits the outer id, so the shell looks for the
    markers of the outer session. Both sessions' hooks wrote a marker under their own id, and the
    session that started last is the one running. A later variable whose marker is newer than the
    first variable's names that situation.
    """
    first = session_var(env)
    if not first:
        return []
    outer_started = _started(env[first].strip(), HARNESS_OF_SESSION_VAR[first])
    for var in SESSION_VARS[SESSION_VARS.index(first) + 1 :]:
        if HARNESS_OF_SESSION_VAR[var] == HARNESS_OF_SESSION_VAR[first]:
            continue  # Codex's two variables name one harness: they disagree only inside a sub-agent
        inner_started = _started(env.get(var, "").strip(), HARNESS_OF_SESSION_VAR[var])
        if inner_started and inner_started > outer_started:
            outer, inner = _name(HARNESS_OF_SESSION_VAR[first]), _name(HARNESS_OF_SESSION_VAR[var])
            return [
                Refusal(
                    "hooks",
                    f"{first} is set next to {var}: this is a {inner} session launched from a {outer} shell, which "
                    f"is not supported. The inherited {first} points the flow at the wrong session, so its "
                    f"markers are not found. Start {inner} from a shell where {first} is unset.",
                )
            ]
    return []


#: The plugin's name, which the id Claude Code keys it by starts with (``<name>@<marketplace>``).
PLUGIN_NAME = "plurai-simulation"


def _plugin_id(env: Mapping[str, str], plugin_root: Path) -> str:
    """The id Claude Code installed this copy under (``<name>@<marketplace>``), the one an admin approves; else the name."""
    try:
        installed = read_json(_workdir.sessions_dir(env).parent / "plugins" / "installed_plugins.json").get("plugins")
        if not isinstance(installed, dict):
            return PLUGIN_NAME
        here = os.path.realpath(plugin_root)
        for key, entries in installed.items():
            for entry in entries if isinstance(entries, list) else ():
                path = entry.get("installPath") if isinstance(entry, dict) else None
                if isinstance(key, str) and isinstance(path, str) and path and os.path.realpath(path) == here:
                    return key
    except Exception:  # noqa: BLE001 - the warning still names the plugin
        pass
    return PLUGIN_NAME


#: The folder Claude Code reads its managed settings from, per platform (one table with ``_mcp_policy``);
#: ``CLAUDE_CODE_MANAGED_SETTINGS_PATH`` names another (a folder, or the file itself).
_POLICY_FOLDERS = _mcp_policy.POLICY_FOLDERS


def _managed_hooks_setting(env: Mapping[str, str]) -> str:
    """``disableAllHooks`` or ``allowManagedHooksOnly``, whichever the organization's managed settings set
    (``disableAllHooks`` first: it turns off every hook, so it decides), else ``""``. Read only to pick the remedy the warning names: the managed-settings
    file, its ``managed-settings.d`` drop-ins and the cached server-managed settings. A policy kept only in the Windows
    registry, or anything unreadable, reads as ``""``, whose warning names both remedies."""
    named = (env.get("CLAUDE_CODE_MANAGED_SETTINGS_PATH") or "").strip()
    platform = "windows" if _platform.WINDOWS else "darwin" if sys.platform == "darwin" else "linux"
    folder = Path(named or _POLICY_FOLDERS[platform])
    files = [folder] if folder.is_file() else [folder / "managed-settings.json", *sorted((folder / "managed-settings.d").glob("*.json"))]
    remote = (env.get("CLAUDE_CODE_REMOTE_SETTINGS_PATH") or "").strip()
    files.append(Path(remote) if remote else _workdir.sessions_dir(env).parent / "remote-settings.json")
    documents = [read_json(path) for path in files]
    for key in ("disableAllHooks", "allowManagedHooksOnly"):
        if any(document.get(key) is True for document in documents):
            return key
    return ""


def _limited_warning(env: Mapping[str, str], plugin_root: Path) -> str:
    """The one warning limited mode shows the user: what is off, that the flow still runs, and the remedy that turns the
    checks back on for the setting that turned them off (:func:`_managed_hooks_setting`). Under ``disableAllHooks``
    approving the plugin changes nothing, so that text never asks for it."""
    harness, plugin = _name("claude"), _plugin_id(env, plugin_root)
    try:
        setting = _managed_hooks_setting(env)
    except Exception:  # noqa: BLE001 - unknown: the warning names both remedies
        setting = ""
    head = (
        "Plurai's hooks did not start in this session, so some of its automatic checks are off. The simulation still "
        "runs. If it goes quiet mid-step, type continue."
    )
    if setting == "disableAllHooks":
        return (
            f"{head} Your organization's {harness} settings turn off all hooks (disableAllHooks), so approving the plugin "
            f"will not help: only your admin can lift that setting, then restart {harness}."
        )
    if setting == "allowManagedHooksOnly":
        return (
            f"{head} Your organization's {harness} settings run only the hooks of plugins your admin approved: ask your "
            f"{harness} admin to approve {plugin} in managed settings, then restart {harness}."
        )
    return (
        f"{head} To turn the checks on, restart {harness}. If they stay off, {harness} settings keep plugin hooks from "
        f"running: where disableAllHooks is set, it has to be removed (by your admin, when it is in your organization's "
        f"managed settings); where your organization runs only the hooks of plugins your admin approved, ask your admin "
        f"to approve {plugin} in managed settings. Then restart {harness}."
    )


def _own_session_id(env: Mapping[str, str]) -> str:
    """The session the process tree names: Claude Code's record of ``CLAUDE_PID``, else ``_workdir.own_session``
    (which asks ``ps``, so not on Windows). ``""`` when neither names one, or the id is not a plain file name."""
    sid = ""
    pid = (env.get("CLAUDE_PID") or "").strip()
    if pid.isdigit():
        found = read_json(_workdir.sessions_dir(env) / f"{pid}.json").get("sessionId")
        sid = found.strip() if isinstance(found, str) else ""
    if not sid and not _platform.WINDOWS:
        try:
            sid = _workdir.own_session()[1]
        except Exception:  # noqa: BLE001 - no answer is no session
            sid = ""
    if not sid or any(char in sid for char in "/\\") or sid in (".", ".."):
        return ""
    return sid


def _own_marker(env: Mapping[str, str]) -> bool:
    """Did the SessionStart hook of the session the process tree names write its marker? Then the hooks run and this
    shell looks in the wrong session's folder (an inherited id), which limited mode would hide: the refusal stands."""
    try:
        sid = _own_session_id(env)
        return bool(sid) and _read(_workdir.FLOW_ROOT / sid / HOOKS_READY_MARKER) == "claude"
    except Exception:  # noqa: BLE001 - no session named: limited mode, the hooks did not start
        return False


def trust_refusal(env: Mapping[str, str], plugin_root: Path = PLUGIN_ROOT) -> Refusal | None:
    """The hook-trust refusal of a Codex session that has not trusted every hook the plugin registers, else ``None``.

    Untrusted hooks leave a session with no pin and no bearer, so a caller that finds neither names this cause before a
    login that cannot fix it.
    """
    if _workdir.harness(env) != "codex":
        return None
    untrusted = _trust(env, plugin_root)
    return untrusted[0] if untrusted else None


def _hooks(harness: str, flowdir: Path, env: Mapping[str, str], plugin_root: Path) -> list[Refusal]:
    inherited = _inherited(env)
    if inherited:
        return inherited
    if harness == "codex":
        untrusted = _trust(env, plugin_root)
        if untrusted:
            return untrusted
    if _read(flowdir / HOOKS_READY_MARKER):
        return []
    if not session_var(env):
        seen = ", ".join(SESSION_VARS)
        return [
            Refusal(
                "hooks",
                f"none of {seen} is set, so this shell is not inside a supported harness session and no "
                "SessionStart marker can exist. Run the flow from a harness session.",
            )
        ]
    if harness == "claude" and not _own_marker(env):
        return [Refusal("hooks", _limited_warning(env, plugin_root), DEGRADED)]
    return [
        Refusal(
            "hooks",
            f"the SessionStart hook did not run in this {_name(harness)} session, so the plugin's hooks are off "
            f"and no guard protects this flow. {_HOOKS_OFF[harness]}",
        )
    ]


def _network(env: Mapping[str, str]) -> list[Refusal]:
    if not env.get("CODEX_SANDBOX_NETWORK_DISABLED", "").strip():
        return []
    return [
        Refusal(
            "network",
            "the shell has no network: CODEX_SANDBOX_NETWORK_DISABLED is set, and the scripts and the runner "
            "reach the backend from the shell. Run the session with `sandbox_workspace_write.network_access = "
            "true` in config.toml, or start it with `codex -c sandbox_workspace_write.network_access=true` for "
            "that session only, then restart it.",
        )
    ]


SERVER = registry.SERVER


def _manifest_server(harness: str, plugin_root: Path) -> dict:
    """The backend server's entry of the MCP config the harness reads for this plugin; empty when it has none."""
    if harness == "copilot":
        servers = read_json(plugin_root / ".github" / "plugin" / "plugin.json").get("mcpServers")
    else:
        named = read_json(plugin_root / ".codex-plugin" / "plugin.json").get("mcpServers")
        servers = read_json(plugin_root / relative_path(named)).get("mcpServers") if isinstance(named, str) else named
    entry = servers.get(SERVER) if isinstance(servers, dict) else None
    return entry if isinstance(entry, dict) else {}


def _user_entry(env: Mapping[str, str]) -> dict:
    """Codex's user-level entry for the backend server; it replaces the plugin's server of that name."""
    servers = codex_config(env).get("mcp_servers")
    entry = servers.get(SERVER) if isinstance(servers, dict) else None
    return entry if isinstance(entry, dict) else {}


def _missing_script(entry: dict, plugin_root: Path) -> str:
    """The script a stdio server entry runs that is not in the plugin, or ``""``. A path is the plugin root's
    (``${CLAUDE_PLUGIN_ROOT}/…`` on Copilot, relative with ``cwd: "."`` on Codex)."""
    for arg in entry.get("args") or []:
        text = str(arg)
        if not text.endswith(".py"):
            continue
        relative = text.removeprefix("${CLAUDE_PLUGIN_ROOT}/")
        if not (plugin_root / relative).is_file():
            return relative
    return ""


def _no_tools(harness: str, env: Mapping[str, str]) -> Refusal:
    base = f"the {SERVER} server's tools are not in this session"
    if harness in ("copilot", "codex"):
        return Refusal(
            "backend tools",
            f"{base}. On {_name(harness)} the server is the plugin's MCP bridge, started with `python3`: check that "
            f"python3 is on the PATH of the environment that launched {_name(harness)}, that the plugin is enabled and "
            "that the server is connected (list the MCP servers). If a `bridge_status` tool is listed, call it: the "
            "bridge refused its configuration and says what to change (a missing token, a url that is not https). "
            "Then restart the session.",
        )
    # A server that failed at session start (no login yet) stays down until the user reconnects it; the reconnect
    # re-runs the manifest's headersHelper, which reads the saved login, so a login made in this session needs no
    # restart. Setup asks for the reconnect, which only an interactive session can type. A server the managed settings
    # keep out stays out after any reconnect: that is a stop naming the admin's fix.
    cause, url = _mcp_policy.excluded(env)
    if cause:
        return Refusal("backend tools", _mcp_policy.refusal(cause, url))
    if _headless(env):
        return Refusal("backend tools", HEADLESS_CONNECT_LINE)
    return Refusal("connect", "the Plurai tools are not connected in this session.", SETUP)


def _backend_tools(harness: str, env: Mapping[str, str], plugin_root: Path, reported_missing: bool) -> list[Refusal]:
    """Whether the backend tools can be there, from what the files say and what the caller saw.

    Claude Code with a login but no tools needs the ``connect`` setup; a bridge harness with no tools is a fault to repair.

    ``PLURAI_TOKEN`` is not read from the shell. The MCP server takes it from the environment
    that launched the harness, and the shell tool may carry a different set: Codex hides it from the
    shell under ``inherit = "core"`` and delivers it under ``[shell_environment_policy.set]``. The
    shell's copy therefore proves nothing about the server, in either direction.
    """
    problems: list[str] = []
    if harness in ("copilot", "codex"):
        entry = _manifest_server(harness, plugin_root)
        if not entry:
            problems.append(f"the MCP config this plugin ships for {_name(harness)} names no {SERVER} server.")
        else:
            missing = _missing_script(entry, plugin_root)
            if missing:
                problems.append(f"the shipped {SERVER} server runs {missing}, which is not in the plugin. Reinstall the plugin.")
        entry_unreadable = _unreadable(env) if harness == "codex" else ""
        if entry_unreadable:
            problems.append(f"{entry_unreadable}, so it cannot tell whether a user-level [mcp_servers.{SERVER}] entry turns the server off. Fix or remove that line.")
        if harness == "codex" and _user_entry(env).get("enabled", True) is not True:
            problems.append(
                f"your user-level [mcp_servers.{SERVER}] entry has `enabled = false`, which also replaces the "
                "plugin's server with a disabled one. Remove that line."
            )
    if problems:
        return [Refusal("backend tools", " ".join(problems))]
    return [_no_tools(harness, env)] if reported_missing else []


def _normal(url: str) -> str:
    return url.strip().rstrip("/")


#: Set by ``flow-window.sh ready`` to ``1`` when the caller exported ``PLURAI_TOKEN``. The script removes the token itself
#: from the helper it starts, so this flag is all the check learns about it.
TOKEN_PRESENT_FLAG = "PLURAI_READINESS_TOKEN_PRESENT"


def _without_the_bearer(env: Mapping[str, str]) -> dict[str, str]:
    """``env`` as the resolver should read it here, where the token itself is never visible.

    The caller's exported token (the flag above) is there, with or without a url, so a lone token is the partial pair
    the shell side refuses. A url also comes with its token on Codex: its shell policy can hide the token from the shell
    while the MCP bridge, launched with it, has it. On Claude Code and Copilot a url with no exported token is a url
    alone: the token comes from the saved login, which the resolver checks against that url.
    """
    seen = dict(env)
    seen.pop(_login_store.TOKEN_VARIABLE, None)
    named = (seen.get(_login_store.URL_VARIABLE) or "").strip()
    exported = (env.get(TOKEN_PRESENT_FLAG) or "").strip() == "1"
    if exported or (named and _workdir.harness(env) == "codex"):
        seen[_login_store.TOKEN_VARIABLE] = "not-visible-to-readiness"
    return seen


def _resolved(env: Mapping[str, str]) -> _login_store.Backend:
    return _login_store.resolve_backend(_without_the_bearer(env))


def login_refusal(env: Mapping[str, str]) -> Refusal:
    """What a session with no usable login is told: the ``login`` setup, or, headless, where nobody can sign in, one plain line."""
    if _headless(env):
        return Refusal("backend", HEADLESS_LOGIN_LINE)
    return Refusal("login", "no usable Plurai login is saved.", SETUP)


def _backend(env: Mapping[str, str], resolved: _login_store.Backend) -> list[Refusal]:
    """A backend is configured: the environment pair or the saved login, not a half of either.

    Readiness runs without the bearer (``flow-window.sh`` removes ``PLURAI_TOKEN`` from the helpers it starts), so
    the token is not visible here. A url in the environment is taken to come with its token, which the MCP server
    and the scripts check where they hold it; with no url, the saved login must be usable. No login, or one a new
    login replaces, is the ``login`` setup; a headless run cannot sign in and is refused. ``resolved`` is ``_resolved(env)``.
    """
    if resolved.configured:
        return []
    if not resolved.needs_login:
        return [Refusal("backend", resolved.problem)]
    return [login_refusal(env)]


def backend_refusal(env: Mapping[str, str]) -> Refusal:
    """What a session with no usable backend is told: the configuration problem when one is named, else the login refusal."""
    return next(iter(_backend(env, _resolved(env))), None) or login_refusal(env)


def _one_backend(harness: str, env: Mapping[str, str], plugin_root: Path, resolved: _login_store.Backend) -> list[Refusal]:
    """MCP calls and the shell side must reach one backend.

    Claude Code dials ``<PLURAI_URL>/mcp`` from its launch environment, and a saved login for another backend
    would send the shell side elsewhere (``_login_store.claude_code_mismatch``). Codex and Copilot run the
    plugin's bridge, which resolves the backend as the shell side does; a Codex user-level entry replaces
    the bridge and brings a url of its own.
    """
    if harness == "claude":
        mismatch = _login_store.claude_code_mismatch(env)
        return [Refusal("one backend", mismatch)] if mismatch else []
    if harness != "codex" or _unreadable(env):
        return []  # the backend-tools check reports a Codex config it cannot read
    user = _user_entry(env)
    if not user:
        return []
    wanted = resolved.url
    used = _normal(str(user.get("url", "")))
    if not wanted or _login_store.api_root(used) == wanted:
        return []
    return [
        Refusal(
            "one backend",
            f"MCP calls go to {used or '(no url)'} while the shell side (uploads, polling, the runner) goes to {wanted}. "
            f"One flow would use two backends. MCP uses the url of your user-level [mcp_servers.{SERVER}] entry in "
            f"{codex_config_path(env)}. Make it name the backend the shell side uses: set the entry's url to `{wanted}`, "
            "or remove the entry, which restores the plugin's bridge.",
        )
    ]


def _window(command: str, flowdir: Path) -> list[Refusal]:
    from flow_arm import FLOW_DRIVERS

    held = _read(flowdir / FLOW_MARKER)
    if held == command or held in FLOW_DRIVERS.get(command, ()):
        return []
    state = f"holds '{held}'" if held else "does not exist"
    return [
        Refusal(
            "window",
            f"the flow window for '{command}' {state} after arming, so the ask and report guards would allow "
            "everything. Run the arm line again; if it still fails, stop.",
        )
    ]


def check(
    command: str,
    flowdir: Path,
    env: Mapping[str, str],
    *,
    no_backend_tools: bool = False,
    plugin_root: Path = PLUGIN_ROOT,
) -> list[Refusal]:
    """Every unmet check for ``command``'s flow in this session, in print order."""
    harness = _workdir.harness(env)
    resolved = _resolved(env)
    refusals: list[Refusal] = []
    refusals += _hooks(harness, flowdir, env, plugin_root)
    # With no login the tools are missing because of it (a bridge lists only its status tool), and the login setup
    # brings them: a missing tool is a second need only once a login exists.
    refusals += _backend_tools(harness, env, plugin_root, no_backend_tools and not resolved.needs_login)
    refusals += _network(env)
    refusals += _backend(env, resolved)
    refusals += _one_backend(harness, env, plugin_root, resolved)
    refusals += _window(command, flowdir)
    return refusals


def main(argv: list[str]) -> int:
    flags = [a for a in argv if a.startswith("--")]
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 3 or args[0] != "check" or set(flags) - {"--no-backend-tools"}:
        print(f"usage: {Path(__file__).name} check <command> <flowdir> [--no-backend-tools]", file=sys.stderr)
        return 64
    flowdir = Path(args[2])
    refusals = check(args[1], flowdir, os.environ, no_backend_tools="--no-backend-tools" in flags)
    if _read(flowdir / HOOKS_READY_MARKER):
        _forget_limited(flowdir)
    tools_line = "" if "--no-backend-tools" in flags else _policy_warning(os.environ)
    if not refusals:
        if tools_line:
            print(tools_line)
        print(f"flow-window: ready ({_name(_workdir.harness(os.environ))}).")
        return 0
    # The lines are the answer; a non-zero exit would reach the user as an error.
    for kind in (NOT_READY, SETUP):
        shown = [r for r in refusals if r.kind == kind]
        for refusal in shown:
            print(refusal.line())
        if shown:
            return 0
    if tools_line:
        print(tools_line)
    _say_limited(refusals[0], flowdir)
    return 0


def _policy_warning(env: Mapping[str, str]) -> str:
    """The ``WARN (backend tools)`` line for a Claude Code session whose managed settings keep the plugin's MCP server
    out, or ``""``. A warning, never a stop: the model checks its tool list and reports with ``--no-backend-tools``."""
    if _workdir.harness(env) != "claude":
        return ""
    cause, url = _mcp_policy.excluded(env)
    return Refusal("backend tools", _mcp_policy.warning(cause, url), DEGRADED).line() if cause else ""


def _forget_limited(flowdir: Path) -> None:
    """The hooks run: a ``.hooks_degraded`` an earlier check wrote no longer stands."""
    try:
        (flowdir / HOOKS_DEGRADED_MARKER).unlink()
    except OSError:
        pass


def _root_line(root: Path) -> str:
    """The plugin root the skills' commands need, which no hook fills in this session; ``""`` for a root that cannot be one line."""
    from plugin_root import shell_path

    text = str(root)
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        return ""
    return (
        f"flow-window: plugin root: {text} — start every shell command that uses ${{CLAUDE_PLUGIN_ROOT}} with "
        f"export CLAUDE_PLUGIN_ROOT={shell_path(text)};"
    )


def _say_limited(degraded: Refusal, flowdir: Path) -> None:
    """``ready, limited``: the flow runs without the hooks. The first time in the session (no ``.hooks_degraded`` yet) it
    also says why, once, with the plugin root and the glossary the SessionStart hook would have given; then it records it."""
    print("flow-window: ready, limited (hooks off).")
    record = flowdir / HOOKS_DEGRADED_MARKER
    if record.is_file():
        return
    print(degraded.line())
    root = _root_line(PLUGIN_ROOT)
    if root:
        print(root)
    try:
        import session_glossary

        print(session_glossary.render("claude", str(PLUGIN_ROOT), session_glossary.windows_shell(_workdir.session_id())))
    except Exception:  # noqa: BLE001 - the skills' capability words then go without their meanings
        pass
    try:
        record.write_text(f"{degraded.text}\n", encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":
    from _utf8_stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main(sys.argv[1:]))
