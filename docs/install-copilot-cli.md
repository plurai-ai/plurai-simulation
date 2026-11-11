# Install on GitHub Copilot CLI

Copilot CLI support is in beta. This page takes you from nothing to a running simulation flow on GitHub Copilot CLI, and says what
is not the same as on Claude Code. The flows are the same, but you start them with the plugin's name in
front of the command (`/plurai-simulation:simulation-scan-agent`,
`/plurai-simulation:simulation-generate-suite`, `/plurai-simulation:simulation-run`,
`/plurai-simulation:simulation-fixes`). The README describes them.

The plugin installs from the same marketplace as on Claude Code. Copilot reads its own manifest
(`.github/plugin/plugin.json`) and its own hook file (`hooks/copilot.json`); both are generated, and
the install needs no edit. What is the same on Codex CLI (the token, the backend url, the Step 0 checks,
headless runs, privacy) is on [install-shared.md](install-shared.md), and this page links to it.

## Requirements

- macOS, Linux or Windows ([install-windows.md](install-windows.md)), with `python3` on the PATH. Every hook runs as `python3 …`.
- GitHub Copilot CLI. Measured on 1.0.91; the recorded transcripts also cover 1.0.92, and the skill
  loading check below was run on 1.0.93.
- A Plurai backend token, exported as `PLURAI_TOKEN` or saved once with `scripts/plurai_login.py` ([the backend token](install-shared.md#the-backend-token)).

Supported versions are listed here; a newer CLI is unverified until this list changes. Copilot updates itself unless `COPILOT_AUTO_UPDATE=false`.

## Install

```bash
copilot plugin marketplace add plurai-ai/plurai-plugins
copilot plugin install plurai-simulation@plurai-plugins
```

The skills, the hooks and the backend MCP server load with the install. There is nothing to approve.

Copilot validates every skill's front matter when it discovers skills, and a skill that fails to
load is listed under `/skills`, Problems, and has no command. A description over 1024 characters
and a front matter that is not strict YAML (an unquoted `: ` in a description) both fail it. Every
skill of the plugin passes both, and a test keeps it so. To check an install, list the skills; no
model session is needed:

```bash
copilot skill list
```

The output has no "failed to load" section when every skill loaded.

To run a checkout instead (a branch under review, your own edits), add the checkout as the
marketplace: `copilot plugin marketplace add /path/to/the/plugin/checkout`. Copilot loads a
local-marketplace plugin live from the checkout.

- Do not add a `.mcp.json` at the root of the checkout. Copilot loads a root `.mcp.json`, and it
  hides the MCP servers of every manifest, so the backend tools disappear.
- Skills, hooks and the MCP server are read at session start: restart Copilot after an edit.

## Connect to the backend

Give the plugin the token in one of two ways:

```bash
export PLURAI_URL=<backend url> PLURAI_TOKEN=<your token>    # both, in the environment that launches Copilot
copilot
```

```bash
python3 <plugin root>/scripts/plurai_login.py --url <backend url>   # once, in a terminal of your own
```

What the login command saves, who can read the token and how the MCP bridge behaves with no token are in
[the backend token](install-shared.md#the-backend-token).

A self-hosted or local backend needs the pair exported, in the environment that launches Copilot. The
url rules, the saved token's backend and the `one backend` check are in
[A non-default backend](install-shared.md#a-non-default-backend).

```bash
export PLURAI_URL=http://127.0.0.1:8200
```

## Check the install

Run the setup command in a Copilot session:

```text
/plurai-simulation:harness-setup
```

It runs `scripts/harness_setup.py` and prints the configuration this harness needs. For Copilot that
is the token (exported or saved) and, for a non-default backend, the url to export.

Every flow also checks the install in its Step 0, before it does anything, and stops with one line per
problem, `flow-window: NOT READY (<check>): <what to fix>`. The checks that apply on Copilot are `hooks`,
`backend tools`, `one backend` and `window`; each line names its fix, and
[Step 0 checks](install-shared.md#step-0-checks) lists them.

## Run a flow

Start Copilot in your agent's project and type the command with a slash and the plugin's name:

```text
/plurai-simulation:simulation-scan-agent <path to your agent>
```

- **Use the qualified form.** A bare `/simulation-scan-agent <path to your agent>` is an unknown
  command on Copilot. A bare command with no arguments goes through Copilot's picker, which runs the
  first match on Enter; that can be a different flow than the one you typed.
- **The hand-off names the next command in the same form.** The plugin's session context tells the
  model to write `/plurai-simulation:<command>` wherever it names a command to you.
- **Start with `simulation-scan-agent`.** The skills `simulation-setup` and `simulation-setup-python`
  (and the other building blocks) are marked `user-invocable: false`: they are steps of the scan,
  which reaches them by file path. Claude Code keeps them out of its command menu; Copilot reads the
  same flag, and whether its picker hides them is not measured here.

- **Use the default interactive mode.** The flows ask you a question at their gates (grounding,
  run readiness, topology approval, fixes selection), and Copilot's `ask_user` tool waits for your
  answer. Autopilot mode answers `ask_user` with a canned reply, so there is no autopilot for gated
  flows. Measured on Copilot 1.0.93: in autopilot, `ask_user` returned the canned text "user is not
  available…", and two shell calls were denied as unattended.
- **Allow the plugin's folder when you launch Copilot.** The plugin installs outside your project, so
  Copilot asks you to allow path access for each shell command that names a plugin script and each
  read of a plugin reference file. A full `/plurai-simulation:simulate` run on Copilot 1.0.93 asked 22
  times (14 commands, 8 reads), every one under the plugin's folder, and a prompt left unanswered for a
  long time stalls the run. `--allow-all-tools` does not cover paths. Start Copilot with the folder
  allowed and the prompts do not appear:

  ```bash
  copilot --add-dir <the plugin's folder>
  ```

  The plugin's folder is printed by `/plurai-simulation:harness-setup`, and the model is given it in the
  session context. `--add-dir` also loads that folder's `.github/skills` and `.github/agents` as trusted
  configuration; `--allow-all-paths` allows every path instead.
- **Choose a Claude model if Copilot offers one.** Copilot routes to a GPT model by default, and the
  skills are tuned on Claude. Pass `--model <model>` to pick one.
- **Keep gated steps in the main agent.** A Copilot subagent has its own session id, so the guards,
  which are keyed by session, do not follow it into one.
- **`COPILOT_ALLOW_ALL=true` loads hooks.** Per Copilot's own help, setting it to exactly `true`
  auto-approves every tool and also trusts the working directory without asking, which loads that
  directory's skills, plugins, MCP servers and hooks, including hooks that run shell commands. Set it
  only in a directory you trust. The other truthy spellings only auto-approve tools.

## Headless runs

`copilot -p` has no ask tool, and it declines every MCP form request. Declare that nobody is there with
`PLURAI_SIM_NONINTERACTIVE=1` or `workspace.run_driver: headless`; the gates, the defaults and the precedence
are in [Headless runs](install-shared.md#headless-runs).

- **The launch line.** Make the plugin's `.plurai` folder first, then start the flow command with `-p` in
  autopilot, with both folders allowed:

  ```bash
  mkdir -p "$HOME/.plurai"
  PLURAI_SIM_NONINTERACTIVE=1 copilot -p "/plurai-simulation:simulation-run" --autopilot --allow-all-tools \
    --add-dir "<the plugin's folder>" --add-dir "$HOME/.plurai"
  ```

  `/plurai-simulation:harness-setup` prints this line with both folders resolved and quoted. Measured on
  1.0.95, with `/plurai-simulation:harness-setup` as the command:
  - `-p` hands the text to the model as a plain message, and the model loads the named skill through its skill
    tool. The skill ran its plugin script, the session ended on `task_complete` and Copilot exited 0. Every flow
    command starts this way except `simulate`: it is not model-invocable, so `-p "/plurai-simulation:simulate …"`
    gets "Skill not found: simulate" and the model improvises. Run its stages one by one instead, one `-p` call
    each.
  - Without `--add-dir` for the plugin's folder, the first command that names a plugin script was denied
    ("Permission denied because no interactive user response was available") and the flow stopped.
  - An `--add-dir` folder that does not exist stops Copilot before the session starts ("Directory does not exist
    or cannot be accessed", exit 1). A CI run that gives the token through `PLURAI_TOKEN` never logs
    in, so nothing has made `.plurai` yet: hence the `mkdir`.
  - The `.plurai` folder holds the flow's state and a saved token, and the skills read it through the shell.
    That it needs its own `--add-dir` is inferred from those reads, not measured.

  `--allow-all-paths` allows every path instead of the two folders.
- **Autopilot nudges past a typed stop.** A gate with no default (the anonymization posture, for one) ends the
  flow on a typed finding. Under `--autopilot`, Copilot answers a final message that is not a `task_complete`
  call with "keep working", so the plugin tells the model to end such a stop through `task_complete`. Declare
  what the stop asks for (`anonymize: true|false` in `plurai.config.yaml`) before you start, so that the
  stop never happens.
- **Put the agent's provider key in the agent's own `.env`.** Copilot removes provider variables such as
  `OPENAI_API_KEY` from the model's shell (measured on 1.0.96 in a headless run: set where Copilot launched,
  missing in the model's shell). The runner reads the gitignored `.env` next to the agent module, and the
  plugin's key check (`bench_manifest.py provider-key`) reads the environment and that file.
- **Allow every folder the run reads.** Copilot asks to allow path access for a folder outside the work
  folder, and nobody answers in a headless run. The launch line covers the plugin's folder and `.plurai`; add an
  `--add-dir` for each other folder the run reads, such as a local SDK checkout.

## What is not the same as on Claude Code

Each difference is stated where it applies above: the qualified `/plurai-simulation:<command>` form, no
autopilot for gated flows, the model Copilot chooses unless you pass `--model`, subagents outside the
session-keyed guards, a root `.mcp.json` that hides the manifest's servers, and the platforms. The two both
harnesses share are in [A turn that dies](install-shared.md#a-turn-that-dies) (no heartbeat, no background
wake-up) and [The overview page](install-shared.md#the-overview-page) (a file, not a link).

## Privacy

The plugin sends no client-side telemetry. See [Privacy](install-shared.md#privacy).

## Known residual risks

Gated steps run in the main agent; do not move a flow into a custom agent.

Run gated flows in the default interactive mode, never in autopilot.

`copilot -p` and `codex exec` cannot run a flow that needs your answer unless you declare a headless run driver; otherwise they stop with a typed finding.

On Copilot and Codex the simulation flows run on that harness's model. The target is that the model follows Plurai's report and question guards in at least 95% of runs on the pinned versions; this is not yet measured live. A run that ends with a question you did not expect, or a report with build details in it, is a known residual and should be re-invoked.

If a flow stops with no report and no question, re-invoke the same command; it resumes.

If your organisation disables hooks or MCP servers, the flow refuses to start and names the setting.

Supported on macOS and Linux with `python3` on the PATH. Windows (PowerShell) is covered by [install-windows.md](install-windows.md).

Measured on reference agents; very long sessions are a known residual.

## Update

```bash
copilot plugin update plurai-simulation
```

Restart the session afterwards.
