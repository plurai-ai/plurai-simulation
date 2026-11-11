# Install on Copilot CLI and Codex CLI: what is the same

[install-copilot-cli.md](install-copilot-cli.md) and [install-codex-cli.md](install-codex-cli.md) link here for
the parts of the install that do not depend on the harness. A line labelled `Copilot:` or `Codex:` is the one
place the two differ inside a shared rule. "The harness" below is the CLI you installed the plugin into.

## The backend token

The plugin needs a Plurai backend address and token. Claude Code connects to Plurai's production backend unless `PLURAI_URL`
names another; Codex and Copilot have no default address. The first Plurai command logs in
through the browser and needs no key. To use a key created in the Plurai dashboard, give the plugin the backend in one of
two ways:

```bash
export PLURAI_URL=<backend url> PLURAI_TOKEN=<your token>   # both, in the environment that launches the harness
python3 <plugin root>/scripts/plurai_login.py --url <backend url>   # once, in a terminal of your own
```

The two variables are one pair: setting only one of them is an error that names the other, not a fallback to the
saved login. Without either, the first Plurai command signs you in.

The login command asks for the token with the echo off and saves it with the url in `~/.plurai/credentials.json`
(mode 0600), so no variable has to be exported. `--url` is required, and it must be `https` under `*.plurai.ai`
or loopback: login refuses any other origin and says which suffix is allowed. Run it in a terminal of your own,
not through the model: a token typed to a model, or piped through a command it runs, lands in its transcript.
The dashboard shows the full command next to a new API key, and it is where you create or revoke keys. The
login command writes the same file as the SDK's `plurai login`, the browser login the first Plurai command runs, and reads
the token from stdin (`printf '%s' "$TOKEN" | ... --url <backend url>`). `plurai_login.py --status` says
whether a credential is saved, for which backend and, after a browser login, for which organization and user; it
never prints the token. The exported pair wins when both exist.

The credentials file is `{"url": ..., "token": ...}`, plus an `account` object after a browser login;
`plurai_login.py` writes the first form. The bridge, the guard and Claude Code's helper read it the same way, and
use its token only for the backend its `url` names.

The saved file is readable by anything that runs as you, every command the model runs included. An
exported variable is no safer in a shell that inherits the launching environment. The plugin's own shell
steps need the token there, so it cannot be hidden from the model's shell and still work. The token is as
safe as the commands you approve. Rotate it when a session went wrong.

- `Codex:` the page's "Who can read the token" section measures what each form exposes and the one setting
  that hides the exported variable from the shell.

## A non-default backend

The plugin's MCP server is the MCP bridge (`scripts/mcp_bridge.py`), which the harness starts with `python3`.
It dials `<PLURAI_URL>/mcp`, where `PLURAI_URL` is the backend's API root (or the saved login's url when
neither variable is set), and sends the token to that one backend only. A test, self-hosted or local backend
needs the pair exported in the environment that launches the harness, and no harness configuration entry:

```bash
export PLURAI_URL=http://127.0.0.1:8200   # tokenless is allowed for a loopback url
```

- The url must be `https`, or `http` to a loopback address, with no user name, odd path or port 0; the bridge
  follows no redirect.
- With no token, or a url that fails those rules, the bridge still starts in its refused state and lists one
  tool, `bridge_status`, that says what to change. A flow refuses to start in that state.
- A token saved with `plurai_login.py` is for one backend, the one `--url` names; the bridge does not hand it
  to another. A backend outside `*.plurai.ai` and loopback is reached with `PLURAI_URL` and `PLURAI_TOKEN`
  in the launch environment, because login refuses to save it.
- The scripts and the runner resolve the same backend, so MCP calls and the shell
  side reach one backend. On Codex a flow also refuses to start when a user-level server entry dials another url.
- Passing `--url <backend url>` to the setup command prints the settings for your backend.

## Step 0 checks

Every flow checks the install in its Step 0, before it does anything, and stops with one line per problem
only the user can fix. Each line names its fix:

```text
flow-window: NOT READY (<check>): <what to fix>
```

A missing login, or Plurai tools that are not connected yet, is not a problem to stop on. The flow prints a
`SETUP` line to itself, runs the setup skill, which signs the user in and connects the tools, and goes on:

```text
flow-window: SETUP (login|connect): <what is missing>
```

A session that is already set up shows neither line. When any `NOT READY` line exists, only the `NOT READY` lines
print, because setup cannot help until the session restarts.

- `hooks`: the SessionStart hook ran in this session. Without it no guard runs.
  - `Copilot:` the fix is to check that the plugin is installed, then restart the session.
  - `Codex:` every hook the plugin registers must also be trusted in `config.toml` and not switched off. The
    fix is "Trust all and continue" in Codex's startup dialog. A newer Codex also pins the plugin's files: after a
    plugin update the same hooks need approving again in `/hooks`, and Step 0 says "plugin files changed" until then.
- `backend tools`: the `plurai` tools are in the session. With a `bridge_status` tool in the list,
  call it: the bridge refused its configuration (no token, a url that is not https) and says what to change.
  - `Copilot:` a missing tool means `python3` is not on the PATH that launched Copilot, or the plugin is not
    enabled or the server is not connected (`copilot mcp list`). Fix it, then restart.
  - `Codex:` a missing tool means the bridge did not start (`python3` not on the PATH that launched Codex) or
    the server is off. A user-level `[mcp_servers.plurai]` with `enabled = false` also hides the
    tools.
- `backend`: a backend is configured (the `PLURAI_URL` / `PLURAI_TOKEN` pair, or the saved login) and the
  configuration is not partial. With no usable login the flow signs the user in (`SETUP (login)`); a headless
  run, where nobody can sign in, stops with one line instead.
  - `Claude Code:` when the organization's managed settings keep the plugin's MCP server out, `ready` comes with a
    `WARN (backend tools)` line naming the `allowedMcpServers` entry that admits it (see "For admins" below).
- `one backend`: the url MCP calls use equals the one the shell side uses. On Claude Code that is the
  exported `PLURAI_URL`, and the line names the `export PLURAI_URL=…` that matches a saved login.
- `window`: the flow's guard window is armed.
- `Codex:` `network`: the shell has network access (`CODEX_SANDBOX_NETWORK_DISABLED` is unset).

## For admins: managed settings

On Claude Code under an organization's managed settings (`managed-settings.json` and its `managed-settings.d/`
drop-ins, the Windows policy key, or server-managed settings), two settings decide whether the plugin works.

**The hooks.** Under `allowManagedHooksOnly`, hooks run only from plugins the managed settings enable. Name the
plugin's marketplace and enable the plugin there, with the source your organization installs from:

```json
{
  "allowManagedHooksOnly": true,
  "extraKnownMarketplaces": {"plurai": {"source": {"source": "directory", "path": "C:\\plurai\\plurai-simulation-main"}}},
  "enabledPlugins": {"plurai-simulation@plurai": true}
}
```

The `enabledPlugins` key is `<plugin>@<marketplace>`. Without it the plugin runs in [limited mode](limited-mode.md).

**The MCP server.** Under `allowManagedMcpServersOnly`, Claude Code loads only the MCP servers `allowedMcpServers`
admits. Plurai's server is remote, so admit it by URL, with your backend's host (Plurai's backends are under
`https://*.plurai.ai/*` and `https://*.plurai.uk/*`; list both to admit any of them):

```json
{
  "allowManagedMcpServersOnly": true,
  "allowedMcpServers": [{"serverUrl": "https://<backend host>/*"}]
}
```

- A `serverName` entry cannot admit it. Claude Code names a plugin's server `plugin:plurai-simulation:plurai`, and a
  `serverName` may hold only letters, digits, `_` and `-`: `{"serverName": "plurai"}` matches nothing, and
  `{"serverName": "plugin:plurai-simulation:plurai"}` is ignored as invalid. A list with no valid entry left blocks
  every server.
- Once the list holds one `serverUrl` entry, every remote server must match a URL entry. `serverName` and
  `serverCommand` entries still admit stdio servers.
- The URL matched is the one the session dials: `${PLURAI_URL}/mcp`, with `PLURAI_URL` from the environment that
  launches Claude Code, or the production address when it is not set. `https://<host>` and `https://<host>/*` match it; `https://<host>/` (a trailing slash and no
  `*`) does not.
- Write the entry as a literal URL. An entry whose `${VAR}` expansion changes its scheme, host or path is ignored.
- Do not provide Plurai's server through `managedMcpServers` or `managed-mcp.json`. A server provided there takes
  precedence over a plugin server at the same URL, renames its tools and has no per-user login; and while a
  `managed-mcp.json` is present, plugin servers do not load at all.
- A `deniedMcpServers` entry that matches the URL blocks the server, whatever the allowlist says.

Restart Claude Code after a change. While the server is kept out, Step 0 prints a `WARN (backend tools)` line that
names the entry to add.

## The session pin folder

At session start a hook pins the session in `~/.plurai/pins`, in the home the account database names (the profile folder on
Windows), never in `HOME`: a command sets its own `HOME`, and a pin a command could move would bind nothing. The folder must be
yours alone: your own, not a symlink, and not writable by group or others (on Windows, an access list that names only you,
SYSTEM and Administrators). A script that finds the pin unusable says so ("the session pin cannot be used: ...") beside its
"not the backend this session is bound to" error. If the hook cannot write there, the session starts with the notice "session_pin could not write
..." and every script that sends the bearer refuses until it is fixed. The usual causes:

- **`HOME` is not the account's home** (a Nix build, `docker run --user` with a passwd home such as `/nonexistent`, a sandbox that
  points `HOME` at a scratch folder, `sudo -E` to another account). Make the account's home writable by the account that runs the
  session, or run the session as the account that owns it. Exporting `HOME` does not help.
- **A read-only or full home** (a managed profile, a disk quota). Free it or make it writable.
- **A pin folder another account created, or one that is group- or world-writable.** Delete `~/.plurai/pins`; the next session
  makes it again with the right mode.
- **A session that started before a plugin update** looks for its pin in the new place and finds none: restart the session.

## Headless runs

`copilot -p` and `codex exec` have no ask tool, and they decline every MCP form request. A flow that reaches a
gate there has nobody to ask. Declare that nobody is there, with either of:

- `PLURAI_SIM_NONINTERACTIVE=1` in the environment, or
- `workspace.run_driver: headless` in `plurai.config.yaml` (an explicit `interactive` there wins over the
  variable).

Each gate then takes its declared default, or, for something only a person can supply (a key, credentials, a
run target), records a typed finding and stops. A headless run that does not declare it ends on the
question with nobody to answer.

- `Codex:` `codex exec` also runs no hook that is not trusted yet, so trust the hooks first.
- `Copilot:` the full unattended launch line (autopilot, the folders to allow) is in
  [Headless runs](install-copilot-cli.md#headless-runs).
- `Claude Code:` a `claude -p` run ends with its turn, and the work it started in the background dies with it.
  Declare it the same way, for example `PLURAI_SIM_NONINTERACTIVE=1 claude -p "/simulation-run"`. The flow then
  waits on its runs and jobs inside the turn. Undeclared, it runs as interactive and a backgrounded run is lost.

A run driven by `scripts/headless_driver.py` ends a phase on a stop instead of relaunching turns until its time limit.
Its exit code says which:

| Exit | The phase ended because |
|---|---|
| 0 | it completed |
| 2 | it outlived its time limit with nothing completing it |
| 3 | a gate had no declared default (`run_driver_input_required`) |
| 4 | its launcher exited with no completion artifact |
| 5 | Plurai stopped it: a contact, capacity, unavailable or credit stop (`run_driver_halted`); the stop text is in the log |
| 7 | Plurai refused the plugin or SDK version (`run_driver_version_refused`); the update text is in the log |
| 64 | usage error |

## A turn that dies

Neither harness has a hook for a turn that ends on an API error, and neither has a cron tool, so the plugin's
dropped-turn heartbeat does not run. A turn that dies on an API error is not re-driven. Re-invoke the same
command; it resumes. For the same reason a flow waits on a long job by polling in short steps and keeps the
turn open; nothing wakes it in the background.

## The overview page

The overview page is a file, not a link. There is no Artifact tool on either harness, so each hand-off names
the page's path in your work folder (open it in a browser). It has no web URL; each
hand-off rewrites the file. A link to a local report in a hand-off is a `file://` URL, because the
terminal opens a link target as a URL. While the scored run plays, the run's own call refills the file within a
few seconds of each finished conversation, and an open copy picks the new data up in place (it never reloads, so
your scroll position and an open conversation stay put): the page counts the finished conversations without any
step from the model.

## Privacy

The plugin sends no client-side telemetry on either harness. The only thing that leaves your machine is what
you approve: the backend calls and the uploads previewed under `.plurai/uploads/`.

- `Codex:` a gate's reply is an ordinary prompt; the plugin records only that the gate was answered, never
  the answer.
