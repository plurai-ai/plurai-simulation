# Install on Codex CLI

Codex CLI support is in beta. This page takes you from nothing to a running simulation flow on Codex CLI, and says what is not the
same as on Claude Code. The flows are the same, but you start them with a dollar sign
(`$simulation-scan-agent`, `$simulation-generate-suite`, `$simulation-run`, `$simulation-fixes`).
The README describes them.

The plugin installs from the same marketplace as on Claude Code. Codex reads its own manifest
(`.codex-plugin/plugin.json`), its own hook file (`hooks/codex.json`) and its own MCP file
(`.codex-plugin/mcp.json`); all three are generated or shipped with the plugin, and the install needs
no edit. Codex needs three things that no plugin can do for you: the token, one keypress of hook trust
and the shell network setting. The setup command prints them. What is the same on Copilot CLI (the token,
the backend url, the Step 0 checks, headless runs, privacy) is on [install-shared.md](install-shared.md),
and this page links to it.

## Requirements

- macOS or Linux, with `python3` on the PATH. Every hook runs as `python3 …`.
- Codex CLI. Measured on 0.160.0.
- A Plurai backend token, exported as `PLURAI_TOKEN` or saved once with `scripts/plurai_login.py` ([the backend token](install-shared.md#the-backend-token)).

Supported versions are listed here; a newer CLI is unverified until this list changes.

## Install

```bash
codex plugin marketplace add plurai-ai/plurai-plugins
codex plugin add plurai-simulation@plurai-plugins
```

Skills load with the install. Codex has no limit on a description's length, so no skill of the plugin
fails to load there, and its limit on a name (64 characters) is not reached. Hooks and the backend tools
need the settings below.

To run a checkout, add the checkout as the marketplace instead:
`codex plugin marketplace add /path/to/the/plugin/checkout`.

- Codex caches an install by version, under `$CODEX_HOME/plugins/cache/<marketplace>/<plugin>/<version>/`.
  The copy of a local checkout includes its `.claude/worktrees/` folder, if it has one.
- Skills, hooks and the MCP server are read at session start: restart Codex after you change the
  install.

## What you set up

Run the setup command in a Codex session. It prints the exact text for your install:

```text
$harness-setup
```

It runs `scripts/harness_setup.py`; run it from a shell the same way if you prefer
(`python3 <plugin root>/scripts/harness_setup.py codex`). The three things:

1. **The backend token.** Give the backend to the plugin in one of two ways:

   ```bash
   export PLURAI_URL=<backend url> PLURAI_TOKEN=<your token>    # both, in the environment that launches Codex
   python3 <plugin root>/scripts/plurai_login.py --url <backend url>   # once, in a terminal of your own
   ```

   What the login command saves, and how the bridge behaves with no token, is in
   [the backend token](install-shared.md#the-backend-token). Codex starts the plugin's MCP bridge either way.

2. **Hook trust.** Codex runs a plugin hook only after you trust it. The first start after you install
   or update the plugin shows "Hooks need review": press "Trust all and continue" once. See "Hook
   trust" below.

3. **Shell network.** The sandbox blocks the shell's network by default, loopback included, and the
   shell sees `CODEX_SANDBOX_NETWORK_DISABLED=1`. The scripts and the runner reach the backend from
   the shell. Run Codex with the `workspace-write` sandbox and open the shell's network in one of two ways.

   Every session, with an edit to `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`):

   ```toml
   [sandbox_workspace_write]
   network_access = true
   ```

   One session only, with no change to the file; the flag also works for `codex exec`:

   ```bash
   codex -c sandbox_workspace_write.network_access=true
   ```

   The setting gives every shell command in the `workspace-write` sandbox network access to any host,
   loopback included. Writes stay limited to the workspace and the temp folders, and no command runs outside
   the sandbox. It does not narrow the hosts a command may reach, so a command that a prompt injection
   steers could send workspace data to any host; the config-file form also applies to every Codex project.
   MCP calls to the backend do not depend on it: the MCP server runs outside the sandbox.

   **Hardened option (experimental).** A permission profile can open the network only to the hosts you
   name. It relies on Codex's `network_proxy` feature, which Codex marks experimental. A profile replaces
   the setting above, which does nothing under a profile, so do not combine the two. The proxy blocks
   loopback unless you allow it: a local backend needs `"127.0.0.1" = "allow"`, and the agent under test,
   its model host and any package index need an entry each. Measured on Codex 0.160.0 (macOS): with this
   profile a loopback request succeeds, a host that is not listed fails, and writes outside the workspace
   stay blocked.

   ```toml
   default_permissions = "plurai"

   [features]
   network_proxy = true

   [permissions.plurai]
   extends = ":workspace"

   [permissions.plurai.network]
   enabled = true
   domains = { "api.example.com" = "allow", "127.0.0.1" = "allow" }
   ```

The backend tools need no approval setting: the plugin's own MCP file approves the backend's known tools
by name, so a run with nobody to ask does not cancel their calls. The destructive tools (`cancel_job`,
`delete_artifact`, `delete_trace_brief`, `delete_upload`, `undeploy_env`), the three tools no skill calls
(`fetch_upload`, `get_mock_event_log`, `recover_trace_brief`) and any tool the backend adds later keep Codex's
own approval; `codex exec` cancels them. To make one of the approved tools ask again,
set `approval_mode = "prompt"` on that tool in your `config.toml`
(`[plugins."plurai-simulation@plurai-plugins".mcp_servers.plurai.tools.<tool>]`). A server-wide
`default_tools_approval_mode = "prompt"` does not override them, because Codex lets a tool's own setting
outrank the server's (measured on 0.160.0); `enabled = false` in the same table turns the server off.

Restart Codex after you change the configuration or the environment.

## Hook trust

The plugin's guards are hooks, and Codex does not run a plugin hook until you trust it.

- **Press "Trust all and continue" once.** On the first start after an install, or after an update that
  adds or changes a hook, Codex shows "Hooks need review". Choose "Trust all and continue". The hooks
  run in that same session, so you do not restart.
- **Skills work without trust.** Only the hooks need it, and a flow refuses to start (Step 0, `hooks`)
  until they are trusted, so nothing runs with its guards off.
- **"Continue without trusting"** leaves the hooks off for that session, and Codex asks again at the next
  start. In a running session, open `/hooks` and press `t` to trust them all.
- **`codex exec`** shows no dialog and skips hooks that are not trusted yet, so start Codex once and
  trust them before a headless run.
- Trust is positional. Codex records it per hook in your `config.toml`, under
  `[hooks.state."<plugin>@<marketplace>:<hooks file>:<event>:<group>:<hook>"]`, with a
  `trusted_hash` of the hook's entry as written. The key names the hook's position in
  `hooks/codex.json`, not the plugin version. You do not write these entries; the dialog does.
- On the Codex versions the plugin was measured with (0.160.0), a version bump does not re-untrust hooks. The hash
  has no version in it and the hook command keeps the literal `${CLAUDE_PLUGIN_ROOT}`, so an update that does not
  touch `hooks/codex.json` keeps every approval.
- A newer Codex also records a fingerprint of the plugin's files (`trusted_content_hash`) when you approve, and runs
  the hooks only while the folder still matches it. So after every plugin update you approve the hooks again
  in `/hooks`, even when `hooks/codex.json` did not change. Step 0 computes the same fingerprint and refuses with
  "plugin files changed" until you do; if it cannot read the plugin's files it refuses too, and never reports the
  hooks as ready. `codex exec` shows no dialog and skips hooks that are not trusted, so approve them again before a
  headless run.
- A session does not undo your approval. The plugin writes its session state (the session pin) under `~/.plurai/pins`,
  never into its install folder, so the folder Codex fingerprints is the same before and after a session. Only a
  plugin update changes it.
- A hook that is edited, added or moved needs review again, and so does every hook behind a moved one.
  The plugin runs all the handlers of one event and matcher in a single hook entry, so a release that adds a
  handler to an entry you approved asks about that entry again; the release notes list each such entry under
  "Codex re-approval". Codex lists it as "Modified since last trusted" or as new. Stale entries stay in your config, and
  Step 0 ignores them.
- `enabled = false` on a hook belongs to its key, not to its content. Switching a hook off in `/hooks`
  leaves a `config.toml` entry that keeps the next hook on that key off as well.
- Step 0 reads your user `config.toml` only. Trust that a project or managed configuration layer
  supplies is not visible to it, and the flow refuses to start until the user file records it.

A plugin update on Codex keeps your hook approvals. When an update adds or changes a hook, Codex asks again at the next start; choose "Trust all and continue" before you run a flow, because the flow refuses to start until you do.

## Check the install

Every flow checks the install in its Step 0 and stops with one line per problem,
`flow-window: NOT READY (<check>): <what to fix>`. The checks are `hooks`, `backend tools`, `network`,
`one backend` and `window`; each line names its fix, and
[Step 0 checks](install-shared.md#step-0-checks) lists them, with the Codex-only `hooks` trust check and
`network` check.

## A non-default backend

A self-hosted or local backend needs the pair exported, in the environment that launches Codex, and
no `config.toml` entry. The url rules, the saved token's backend and the `one backend` check are in
[A non-default backend](install-shared.md#a-non-default-backend).

```bash
export PLURAI_URL=http://127.0.0.1:8200
```

A user-level `[mcp_servers.plurai]` entry in `config.toml` still replaces the plugin's server,
and the entry then carries its own url, token and approval mode. You do not need one.
Under `inherit = "core"` the shell sees neither backend variable; see `shell_environment_policy.set` below.

## Who can read the token

The backend token reaches the model's shell on Codex in either form, and the plugin's own shell steps need it
there. Know what each form exposes:

- **The saved file (`~/.plurai/credentials.json`).** Mode 0600 keeps other users out. It does not keep out
  anything that runs as you, and every command the model runs does: `cat ~/.plurai/credentials.json` works under
  Codex's default sandbox (measured), and `url_guard.py --token-for` and `resolve-plurai-urls.sh` print or
  export the token for the pinned backend by design. No check in the plugin can tell the model's shell from
  yours, so none is added. The file is the convenient form: no export, and it survives a new terminal.
- **The exported variable.** With no `[shell_environment_policy]`, Codex 0.160 passes `PLURAI_TOKEN`
  to the model's shell too (measured, as above). Setting `ignore_default_excludes = false` there makes Codex drop
  variables whose names contain `KEY`, `SECRET` or `TOKEN` from that shell (measured with `codex sandbox`), and
  the variable still reaches the MCP bridge, which the plugin's MCP file starts with `env_vars`.

So, to keep the token out of the model's shell: export the variable where Codex launches, set
`ignore_default_excludes = false`, and do not save a file. The MCP tools then work. The plugin's shell steps that
call the backend (uploads, polling, the runner) find no token and stop with the resolver's message, because
they run in the shell the token is hidden from. If those steps must work, the token is reachable by the model
in either form, and the credential is only as safe as the model's behaviour and your review of the commands
it proposes. Rotate the token when a session went wrong. Storing it in the operating system's keychain is a
later option.

A model that holds a terminal can run `plurai_login.py` too. That replaces the saved account; it cannot change
the backend the token is sent to, which comes from the launch environment and the session pin, and login saves
only a url under `*.plurai.ai` or on this machine.

## Local and development installs: `shell_environment_policy.set`

Codex filters the environment of the shell tool with `[shell_environment_policy]`. Measured on Codex
0.160 (a `codex exec` run in a read-only sandbox):

- With `inherit = "core"`, a `PLURAI_TOKEN` exported where Codex launched does not reach the
  shell. The scripts and the runner read the token
  from the shell, and the Step 0 `one backend` check reads `PLURAI_URL` from it.
- `[shell_environment_policy.set]` puts a variable into every shell command, whatever `inherit` says.
  Under `inherit = "core"` it delivers the token and a plugin root.

For a local or development install, set the backend variables there:

```toml
[shell_environment_policy.set]
PLURAI_TOKEN = "<your development token>"
PLURAI_URL = "http://127.0.0.1:8200"
```

- This writes the token into `config.toml` in plain text. Use it with a development token, never a
  production one.
- It affects the shell tool only. The MCP bridge reads `PLURAI_TOKEN` and
  `PLURAI_URL` from the environment that launches Codex (or the saved credential), so keep the
  export there too.
- `CLAUDE_PLUGIN_ROOT` can go in the same table. The value must name the copy of the plugin whose
  hooks Codex runs (the cache folder above), or scripts and hooks come from two different copies.
  Most installs do not need it, because Codex gets the plugin root another way (below).

## The plugin root

Codex does not fill `${CLAUDE_PLUGIN_ROOT}` in a skill's text, and the variable is empty in the
shell. Hooks do get it. Codex learns the plugin root through the SessionStart glossary: the hook
adds the root to the session, with a rule that every shell command that uses it first exports
`CLAUDE_PLUGIN_ROOT` set to that root. The plugin does not auto-approve those commands on Codex;
Codex's own approval applies.

## Run a flow

Start Codex in your agent's project and invoke the command with a dollar sign:

```text
$simulation-scan-agent <path to your agent>
```

- **Start with `$simulation-scan-agent`.** The skills `simulation-setup` and `simulation-setup-python`
  (and the other building blocks) are steps of the scan, which reaches them by file path. Claude Code
  hides them from its command menu with `user-invocable: false`; Codex has no such setting, so
  `$simulation-setup` and `$simulation-setup-python` also run when you type them.
- **The hand-off names the next command with a dollar sign.** The plugin's session context tells the
  model to write `$<command>` wherever it names a command to you, and not the `/<command>` form that
  skill text uses.
- **A gate ends the turn.** Codex has no blocking ask tool in Default mode. At a gate the model
  asks one question with its options and ends the turn, so type your answer as the next message.
- **Use the Default mode,** not Plan mode. Plan mode forbids the file edits the flows make.
- **Choose a Claude model if Codex offers one.** Codex CLI runs the OpenAI model you configure, and
  the skills are tuned on Claude. The ask behaviour differs by model, and the flows handle both.

The flow's guard window stays armed while a gate waits for your reply.

## Headless runs

`codex exec` has no ask tool, and it declines every MCP form request. Declare that nobody is there with
`PLURAI_SIM_NONINTERACTIVE=1` or `workspace.run_driver: headless`; the gates, the defaults and the precedence
are in [Headless runs](install-shared.md#headless-runs). `codex exec` also runs no hook that is not trusted
yet, so trust the hooks first (setting 2).

## Approval

Codex applies a hook's rewrite of a tool call only together with an approval that would skip its own
prompt, so no hook of the plugin rewrites a Codex call. No hook of the plugin approves a call for you.

## Calls from a code cell

Some Codex models call their tools from a JavaScript code cell (`exec`) instead of one call at a time.
The plugin's hooks run for those calls as for any other: the shell, `apply_patch` and MCP tools, the
backend's included, send `PreToolUse` and `PostToolUse` from inside the cell, a hook's deny stops the call,
and Codex's own approval prompt applies. Two things differ. Codex sends no hook for `write_stdin`, in a cell
or outside one. And Codex 0.160 does not offer a cell the tools that ask you a question, so a gate is always a call at
the top level, where `ask_guard` sees it.

## What is not the same as on Claude Code

Each difference is stated where it applies above: `$name` commands, hook trust, the shell network, a gate that
ends the turn, the backend variables, the plugin root, the OpenAI model and the platforms. The two both
harnesses share are in [A turn that dies](install-shared.md#a-turn-that-dies) (no heartbeat, no background
wake-up) and [The overview page](install-shared.md#the-overview-page) (a file, not a link).

## Privacy

The plugin sends no client-side telemetry. What a gate's reply records is in [Privacy](install-shared.md#privacy).

## Known residual risks

On Copilot and Codex the simulation flows run on that harness's model. The target is that the model follows Plurai's report and question guards in at least 95% of runs on the pinned versions; this is not yet measured live. A run that ends with a question you did not expect, or a report with build details in it, is a known residual and should be re-invoked.

If a flow stops with no report and no question, re-invoke the same command; it resumes.

`copilot -p` and `codex exec` cannot run a flow that needs your answer unless you declare a headless run driver; otherwise they stop with a typed finding.

On Codex, answer a gate with one of the options it lists; a reply that matches none is read as text and may be asked again.

Codex support was measured on OpenAI models only.

If your organisation disables hooks or MCP servers, the flow refuses to start and names the setting.

Supported on macOS and Linux with `python3` on the PATH.

Measured on reference agents; very long sessions are a known residual.

## Update

```bash
codex plugin marketplace upgrade
```

Restart the session afterwards, and check `/hooks`.
