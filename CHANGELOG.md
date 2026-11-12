# Changelog

## [1.0.5]

- The plugin installs SDK 1.0.2. With `anonymize: true` set, uploads no longer replace dates and times, durations, web addresses, company names, order numbers or SKUs with hash tokens. Personal identifiers (names, emails, phone numbers, account, card and ID numbers, IP addresses), places and nationality, religion or political group are still scrubbed. Knowledge files and scenario bundles uploaded under the old rule keep their tokens until you upload them again.

## [1.0.4]

- The report no longer withholds scenario/run text when `anonymize: true`; that setting governs only data uploaded while building the env. Run artifacts (`ingest-run`, `ingest-run-report`) upload as-is whatever the posture.

## [1.0.3]

- The README opens with the Plurai Simulation Plugin banner, with a Claude Code plugin badge next to the Discord one. No change to how the plugin works.

## [1.0.2]

- After a stop that is a matter for Plurai ("We hit an issue that needs a closer look…"), the next message you type lets any command start work on Plurai again, in the same session. In 1.0.1 every job start was refused until `/simulate` was run again or the run was ended. The stopped run itself does not go on by itself: it still waits for `/simulate` (Finish the suite or a rerun), feedback that rewinds a stage, or your request to stop.

## [1.0.1]

- Claude Code now connects to Plurai without any setup. In 1.0.0 the connection could not start unless `PLURAI_URL` was exported, so `/mcp reconnect all` failed after sign-in.

## [1.0.0]

First public release of the Plurai simulation plugin. It sets up Plurai simulation for an AI agent: it reads the agent's code, writes a `simulate.py` that stubs the agent's production dependencies and routes its tool calls to mocks, builds a mocked world, scenarios and evals, runs the suite against the Plurai backend, and writes an evidence-backed fix plan. It does not modify the agent's source.

- Install from the public `plurai-plugins` marketplace: `/plugin marketplace add plurai-ai/plurai-plugins`, then `/plugin install plurai-simulation@plurai-plugins`. The README is the quickstart.
- The first Plurai command signs you in. With no login saved, it installs the SDK when it is missing (it asks first) and runs the SDK's `plurai login`, which opens the browser; the key is saved by the SDK and never shown, typed or copied. A machine without a browser signs in as `docs/sign-in-without-a-browser.md` describes. A Claude Code session that started before the first login asks once to run `/mcp reconnect all`, then carries on by itself.
- `/simulate <agent module>` runs the whole flow. Each stage is also a command of its own: `/simulation-scan-agent`, `/analyze-traces` (optional, grounds the suite in real production traces), `/simulation-generate-suite`, `/simulation-run` and `/simulation-fixes`. Further commands refine scenarios, edit a mock environment, build a knowledge corpus, upload document examples or scenario bundles, record a regression, and diagnose or uninstall the plugin.
- A finished run ends on one message with its outcome, the number of agent issues with a link to the fix plan, the overview page link, and the choices the run took on a default. The overview page opens in your browser when the run starts and updates live; it shows the agent map, each eval's kind and what it checks, each scenario's card and every conversation's verdicts. The page stays in place after the run ends, so the open tab still refreshes.
- Claude Code is supported. Codex CLI and Copilot CLI are beta. macOS and Linux are supported. Windows (PowerShell, with Git for Windows; see `docs/install-windows.md`) is supported for Python agents; agents that need the Node bridge are not supported on Windows. On a hosted Agentforce agent, `/simulate` asks once at the start whether it may use your org.
- The plugin installs the one release of the `plurai-simulation-sdk` package it was tested with, into the project's environment. The SDK needs Python 3.12 or newer; the plugin's own hooks and scripts need a `python3` on the PATH. An agent written in Node also needs Node 22.15 or newer, on macOS or Linux.
- Data leaves your machine only through the SDK's `plurai ingest-*` commands and the backend calls of the flows. You approve each command before it runs, the SDK prints a disclosure preview of each upload, and an append-only audit log at `.plurai/uploads/audit.log` records what was sent and under which anonymization posture. The plugin sends no telemetry.
- The files the plugin generates in your repository belong to you. `docs/what-the-plugin-writes.md` lists every file and folder the plugin writes, and `/plurai-uninstall` removes them after you confirm.
- A newer plugin release is announced once a day, from the public plugin repository's tag list, and never blocks; `PLURAI_NO_UPDATE_NOTICE=1` turns the read off. A plugin or SDK version that Plurai refuses reads one short text with the update command for your harness, on Claude Code, Codex CLI and Copilot CLI alike. An SDK older than the one the plugin installs is offered the install once: "This plugin release needs plurai-simulation-sdk 1.0.0; this project has the older 1.0.0rc1. Install 1.0.0 into .venv now? It may change other packages there, and Plurai commands wait until it is done." (the versions and the environment are your own).
- A stop that is a matter for Plurai reads one of three fixed texts, the same in all three harnesses and in the SDK. When something on Plurai's side needs a closer look: "We hit an issue that needs a closer look from the Plurai team. Reach out at support@plurai.ai or on Discord: https://discord.gg/bg34gh76uJ and we'll dig into it." When the provider is out of room: "Plurai is in high demand right now and we couldn't fit your run in. Please try again in about 20 minutes." When Plurai cannot be reached or says it is unavailable, a third text says so and asks you to try again shortly. A credit stop names what to do to continue and the command that continues it. A stop is said once, as the last message of the turn, and halts the run until you type a message.
- A run with nobody at the keyboard ends with exit 5 and the stop text in its log when Plurai stops it, and as a failure with the refusal text when Plurai refuses its version; neither is retried until a time limit.
- On Claude Code, when your organization's settings keep the plugin's hooks from running, the flows run in a limited mode and say once, in plain words, that some automatic checks are off and how to turn them back on.
- The plugin is proprietary software under the terms in `LICENSE`; it is not open source. `THIRD-PARTY-NOTICES.md` lists the libraries the overview page bundles, with their licenses.
- Problems are reported as GitHub issues, with the output of `/plurai-diagnose`. To reach the Plurai team directly, write to support@plurai.ai or join the Discord.
