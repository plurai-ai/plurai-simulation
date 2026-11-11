# Install on Windows (PowerShell)

This page takes a Windows machine from nothing to a running simulation flow, in PowerShell, without WSL. Every
command is typed in a PowerShell window: Windows PowerShell 5.1 (built in) or PowerShell 7 (`pwsh`). The flows,
the backend token and the Step 0 checks are the ones the other install pages describe; this page covers what
differs on Windows. For the harness itself, [install-copilot-cli.md](install-copilot-cli.md) and
[install-shared.md](install-shared.md) still apply.

Measured on GitHub Actions `windows-latest` (Windows Server, build 26100) with Python 3.12, Git for Windows 2.56 and
GitHub Copilot CLI 1.0.103, in both PowerShells: the CI runs real Copilot sessions there. Claude Code 2.1.308 is
installed on that runner, but no Claude Code session is run. What is measured for it is the hook gate that its
`hooks.json` starts (`sh hooks/gate.sh`) under Git Bash with a Windows path in the payload, and the plugin's scripts
under Git Bash.

## Requirements

- Windows 10 or 11, or Windows Server 2022 or later.
- Python 3.12 or newer from python.org, with the Python launcher installed for all users (step 1).
- Git for Windows. The plugin's scripts are bash and run under its Git Bash (step 2).
- GitHub Copilot CLI (needs Node.js 22 for `npm`) or Claude Code.
- A Plurai backend token.

Non-Python agents (the Node bridge) are not supported on Windows.

## 1. Python

Install Python from python.org. In the installer's "Customize installation" page, tick **py launcher** and
**for all users (requires admin privileges)**, and tick **Add python.exe to PATH**.

Why the launcher must be for all users: the plugin's scripts that hold your backend token never run a Python
they find on `PATH`, because a command can change `PATH`. They run the interpreter the session's hooks started
with (pinned when the session starts), or else the all-users launcher in the Windows folder,
`C:\Windows\py.exe`. A per-user launcher (under `%LOCALAPPDATA%`) is not used, so without the all-users one
the scripts have only the session's pinned interpreter. The copies the plugin writes into your agent's work folder
(the backend URL resolver, and the Agentforce lifecycle script for an Agentforce agent) cannot read the session's pin
at all: they run only with the all-users launcher. That launcher starts the newest Python installed on the machine, not
your project's: this is fine for the plugin's own checks, and your agent itself still runs with your project's
virtualenv Python. Check that the launcher is there:

```powershell
Test-Path "$env:SystemRoot\py.exe"
py -3 --version
```

The hooks run `python3`, and python.org installs no `python3.exe`. Add one next to `python.exe`:

```powershell
$d = Split-Path (Get-Command python).Source
Copy-Item (Join-Path $d 'python.exe') (Join-Path $d 'python3.exe')
where.exe python3
```

The first line `where.exe` prints must be your Python, not `...\Microsoft\WindowsApps\python3.exe`. That one is
the Microsoft Store alias, and it opens the Store instead of running Python. Turn it off in Settings › Apps ›
Advanced app settings › App execution aliases (both "python.exe" and "python3.exe"). Copy `python3.exe` again
after you upgrade Python.

## 2. Git for Windows

Install Git for Windows with its defaults. It puts only `Git\cmd` on `PATH`, so a bare `bash` in PowerShell is
not Git Bash: on a machine with WSL it is `C:\Windows\System32\bash.exe`, which starts WSL. The plugin never
runs a bare `bash`. It finds Git Bash from the folder `git.exe` is in (`...\Git\bin\bash.exe`), or at
`C:\Program Files\Git\bin\bash.exe`. If Git is installed somewhere else, name its `bash.exe`:

```powershell
[Environment]::SetEnvironmentVariable('CLAUDE_CODE_GIT_BASH_PATH', 'D:\Tools\Git\bin\bash.exe', 'User')
```

On GitHub Copilot CLI the model's shell is PowerShell. The plugin tells the model to run each of the skills'
bash steps as a file under that Git Bash, so you do nothing for it.

## 3. The SDK, in your agent's virtualenv

In your agent's project folder, create the virtualenv. The first scan installs the SDK the plugin was tested with
(`plurai-simulation-sdk`) into it:

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe --version
```

After the first scan, `& .\.venv\Scripts\plurai.exe --version` prints the SDK's version.

Call `.venv\Scripts\python.exe` directly, as above. If `Activate.ps1` is refused ("running scripts is disabled",
or "is not digitally signed" under an AllSigned policy your organization sets), you do not need it. Activating only
puts the venv first on PATH, which you can do in the current window, from the project folder:

```powershell
$env:VIRTUAL_ENV = "$PWD\.venv"
$env:Path = "$env:VIRTUAL_ENV\Scripts;$env:Path"
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
```

Start Claude Code (or your harness) from this same window: only programs started from it see the change.
`VIRTUAL_ENV` must name the project's own `.venv`, because the plugin reads it to find the SDK. In `cmd.exe`,
`.venv\Scripts\activate.bat` is not subject to PowerShell's execution policy.
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` helps only when `Get-ExecutionPolicy -List` shows
`MachinePolicy` and `UserPolicy` as `Undefined`.

`curl` in Windows PowerShell 5.1 is `Invoke-WebRequest`; use `curl.exe`.

## 4. The backend token and URL

The plugin has no default backend. Give it the address and the token as a pair, in one of two ways.

Set them as user environment variables. They reach every new terminal, and the harness you start from it:

```powershell
[Environment]::SetEnvironmentVariable('PLURAI_URL', '<backend url>', 'User')
[Environment]::SetEnvironmentVariable('PLURAI_TOKEN', '<your token>', 'User')
```

Open a new PowerShell window afterwards; a variable set this way is not seen by windows that are already open.

Or save the login once instead. Run this in a PowerShell window, not in Git Bash: it asks for the token with the
echo off, and Git Bash's terminal is not one it can read from.

```powershell
python3 "<plugin folder>\scripts\plurai_login.py" --url <backend url>
```

It saves `.plurai\credentials.json` in your profile folder, the folder Windows names for your account (the plugin does
not read `HOME` or `USERPROFILE` for it, so a changed variable cannot move the file). The login limits the folder and
the file to your account every time it saves, and a read refuses a file or folder that another account owns or whose
access list lets anyone but you, SYSTEM and the administrators use it. A profile on a network share or a drive that
is not a fixed local disk is not used. `plurai login` (the SDK's command) reads and writes the same file.

Check that the backend answers:

```powershell
curl.exe -s -H "Authorization: Bearer $env:PLURAI_TOKEN" "$env:PLURAI_URL/healthz"
```

## 5. The harness and the plugin

### GitHub Copilot CLI

```powershell
npm install -g @github/copilot
copilot plugin marketplace add plurai-ai/plurai-plugins
copilot plugin install plurai-simulation@plurai-plugins
copilot skill list
```

Start Copilot in your agent's project, with the plugin's folder allowed (the reason is in
[install-copilot-cli.md](install-copilot-cli.md#run-a-flow)), and run the scan:

```powershell
copilot --add-dir "<plugin folder>"
```

```text
/plurai-simulation:simulation-scan-agent <path to your agent>
```

For a headless run, make the plugin's `.plurai` folder first, then start the command with `-p` in autopilot, with
both folders allowed (the reasons are in [Headless runs](install-copilot-cli.md#headless-runs)):

```powershell
New-Item -ItemType Directory -Force "$HOME\.plurai" | Out-Null
try { $env:PLURAI_SIM_NONINTERACTIVE = "1"; copilot -p "/plurai-simulation:simulation-run" --autopilot --allow-all-tools --add-dir "<plugin folder>" --add-dir "$HOME\.plurai" } finally { Remove-Item Env:PLURAI_SIM_NONINTERACTIVE -ErrorAction SilentlyContinue }
```

The `finally` removes the declaration again, even when the run is stopped, so it holds for that one run and not
for the rest of the PowerShell session.

The plugin keeps `.plurai` under your profile folder, which is `$HOME` in PowerShell in the usual case.
`/plurai-simulation:harness-setup` prints the line with both folders resolved.

### Claude Code

```powershell
irm https://claude.ai/install.ps1 | iex
[Environment]::SetEnvironmentVariable('Path', [Environment]::GetEnvironmentVariable('Path', 'User') + ";$HOME\.local\bin", 'User')
```

Open a new PowerShell window, then:

```powershell
claude plugin marketplace add plurai-ai/plurai-plugins
claude plugin install plurai-simulation@plurai-plugins
```

Claude Code runs its shell commands and its plugin hooks in Git Bash, so the skills' steps and the hook gate run there
as written.

### Codex

Codex on Windows runs a hook through `%COMSPEC% /C`, which is `cmd.exe`: it has no `sh` to start the gate with. So every entry in the plugin's Codex hook file carries a `commandWindows` command
that starts the hook directly with `%CLAUDE_PLUGIN_ROOT%` (the variable form of `cmd.exe`; Codex puts it in the hook's environment), and the hook's own scope check stands down when no flow is
open. This follows Codex's hook documentation and source; no Codex session is run on Windows in the plugin's CI, so
Codex on Windows is not measured. Its other install steps are the ones in [install-codex-cli.md](install-codex-cli.md).

## Known limits

- **The session of the config directory.** Under Git Bash the ancestry walk that matches Claude Code's session files
  sees MSYS process ids, not the Windows pids Claude Code records. A session id inherited from a parent session is
  therefore not corrected on Windows, and the inherited id stands.
- **Non-Python agents.** The Node bridge is not supported on Windows.

## Troubleshooting

- **`flow-window: the readiness check could not run (exit 127)`, or `plurai: no trusted python on this Windows
  host`.** The plugin found no Python it trusts: the session pinned none at its start, and
  `C:\Windows\py.exe` is missing. Install the Python launcher for all users (step 1), then restart the session.
- **`Windows Subsystem for Linux has no installed distributions`.** A bare `bash` reached the WSL launcher.
  Check that Git for Windows is installed (step 2), or set `CLAUDE_CODE_GIT_BASH_PATH`, then restart the session.
- **`python3` opens the Microsoft Store.** Turn off the App execution aliases (step 1).
- **A setting you just made is not seen.** User environment variables reach new windows only. Close the
  terminal, open a new one, and start the harness from it.

## Update

```powershell
copilot plugin update plurai-simulation
claude plugin update plurai-simulation
```

Restart the session afterwards.
