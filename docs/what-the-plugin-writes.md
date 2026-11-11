# What the plugin writes

Installing the plugin writes nothing outside its own install folder. Its session-start hook creates the session's
folder under `~/.plurai/flow/`; everything else appears only when you run a flow. These are all the files and folders
the plugin writes, and `/plurai-uninstall` removes each of them after you confirm.

| Where | Path | What |
| --- | --- | --- |
| Inside the project | `plurai_sim/` | The working folder beside the agent module (for `src/app/agent.py`, `src/app/plurai_sim/`): `simulate.py`, `plurai.config.yaml`, `simulation_prd.md`, the mocks, scenarios, evals, artifacts and runs. Everything the flows generate lives here. With several agents in one repository each has its own `plurai_sim/`: run a flow from the agent's folder, and `plurai diagnose` reads the run written last unless `--workdir` names an agent's folder. |
| Inside the project | `.gitignore` | One managed block between `# BEGIN plurai-simulation` and `# END plurai-simulation`, so customer data in that folder is never committed. No other line. |
| Inside the project | `generated/` | Agentforce agents only: the rewired copy of the agent and its generated Apex, under the package folder, and the smoke script and manifests under the DX root. |
| Inside the project | `.plurai/` | Upload previews, the upload audit log (`.plurai/uploads/audit.log`) and gate files. Add it to your `.gitignore`. |
| Outside the project | `~/.plurai/credentials.json` | The backend url and API key saved by the browser login or by `plurai_login.py` (mode 0600). |
| Outside the project | `~/.plurai/flow/` | One folder per session with the flow markers. |
| Outside the project | `~/.plurai/pins/` | One file per running session: the backend the session was started with, the project and temp folders it works in, and on Windows the Python it runs. Written at session start, removed at its end (mode 0600). |
| Outside the project | `~/.plurai/suite_windows/` | One small file per working folder that claims the account window for a suite. |
| Outside the project | `~/.plurai/poll_unreachable/` | One marker per job while the backend can't be reached. |
| Outside the project | `~/.plurai/tools/` | Plugin-owned virtualenvs for the mock contract checks. |
| Outside the project | `~/.plurai/support_refs.jsonl` | One line for each install failure and each refused hook decision, so a failure the backend never saw still has a reference to quote. Nothing reads it but `plurai diagnose` and `/plurai-diagnose`, and nothing sends it. `PLURAI_NO_SUPPORT_REFS=1` turns it off. |
| Outside the project | `~/.plurai/update_notice` | The day the update notice last read the public tag list (the file's modification time) and the newest release that read saw. `PLURAI_NO_UPDATE_NOTICE=1` turns the read off; see [Privacy and local state](data-and-privacy.md#privacy-and-local-state). |
| Outside the project | `~/.plurai/mcp_connected` | The time Claude Code last connected the Plurai server. The setup's reconnect wait reads it. |
| Outside the project | `.plurai_url_split_*` | One marker a day in the temporary folder, written when a backend url differs between the MCP server and the shell. |

`PLURAI_STATE_HOME` moves `suite_windows/`, `tools/` and `update_notice` to another folder; `flow/`, `pins/` and `credentials.json` stay under `~/.plurai/`.

While it builds the suite, the plugin may fix your `simulate.py` without asking, re-run once, and tell you what it changed
at the end of the run. That includes a runner of your own that `simulate.py` imports, if the scan showed it on the map
you approved.

Outside the plugin's reach, and left alone by `/plurai-uninstall`:

- The Plurai SDK (`plurai-simulation-sdk`) in the project's virtualenv, which the first scan installs: `pip uninstall plurai-simulation-sdk plurai-mockenv`.
- The scan stages the files it generates with `git add`, so the git index may list them until you commit or reset.
- A non-Python agent's bridge installs its pinned runtime under `plurai_sim/bridge/runtime/` (part of the
  working folder) and fills npm's own cache under `~/.npm`.
- Your own files: the agent's source, its `.env`, and Codex hook trust in `~/.codex/config.toml`.
- Data you uploaded to the Plurai backend (corpora, runs, mock environments), which the dashboard manages.
- The plugin's install folder, including its per-session backend pins, which `claude plugin uninstall` removes.

To leave cleanly, run `/plurai-uninstall`, then `claude plugin uninstall plurai-simulation@plurai-plugins`
(add `--scope project` for a project-level install).
