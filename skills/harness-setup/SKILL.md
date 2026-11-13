---
name: harness-setup
description: Log in to Plurai through the browser, install the SDK, connect the Plurai tools and print what this harness needs. The Plurai commands run it themselves when a login or connection is missing. Run it directly to log in again or to see which account is saved.
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/harness_setup.py":*), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plurai_login.py" --status), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/wait_for_plurai_connect.py":*), Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py")
---

# Harness setup

**Usage:** `/harness-setup`, or `from <command>` when a Plurai command runs it.

The simulation commands check at Step 0 that the session is ready. When a login or a connection is missing, the
command runs this skill itself, then goes on. This skill logs the user in when no usable login is saved, installs the
SDK, connects the Plurai tools to the session, and, run directly, prints the configuration that fixes the rest for the
harness the session runs on.

`<command>` below is the command the user typed: the one this skill was started from, or `/harness-setup` for a direct
run. Spell it the way the user types it on this harness (see "run a flow command"), never with the plugin's name in
front of it, even when the harness shows you the command that way.

The plugin reads the backend url and API key from `~/.plurai/credentials.json`. The SDK's `plurai login` writes it: it
opens the browser, the user signs in (or signs up) and picks the organization there, and the SDK creates its own key
and saves it. The key is never shown, typed or copied. The plugin's MCP connection reads the credentials file again for
each connection, so a new login is used as soon as it is saved.

## What the user sees

The user wants to log in and use Plurai, not to follow how the setup works. Tell them only these lines, word for word:
add nothing to a line, reword none, and put `<command>` in where a line names it.

- Already logged in, run directly: `Logged in to Plurai.` From a command: nothing at all.
- The SDK is missing: one question, `Plurai needs its SDK in this project. Install it now?`, or, for a project with
  no environment, `Create a .venv in this project and install the Plurai SDK into it?`. The harness's approval prompt
  shows the exact command, so do not repeat it.
- The SDK is older than this plugin release needs: one question, the text the resolver prints under `question:` (it names
  both versions and the environment), with its header and options. On `Install it`, the line the install prints
  (`Installed plurai-simulation-sdk <version> into <environment>.`). On `Not now`, the stop lines the resolver prints, as
  they stand.
- The SDK is newer than this plugin release needs, or cannot be installed over from here: the stop lines the resolver prints, as they stand. No question.
- The login has started: `Sign in to Plurai in your browser.` and, on its own line, `If no browser opened, open: <url>`.
- The login has ended: `Logged in to Plurai.`, or one line saying what failed and what to do next.
- Run directly, then step 5: one line when the harness needs nothing more, else only what the user still has to set.
  A login made in this session, on Claude Code, with no `plurai` backend tool in your tool list: one more line,
  `Run /mcp reconnect all to connect Plurai in this session.`
- From a command, then step 6, when the Plurai tools are not in your tool list yet: on Claude Code
  `Run /mcp reconnect all to connect Plurai. I'll continue after that.`; on Copilot or Codex, only when the tools still
  do not appear, `Restart <harness> to connect Plurai, then run <command> again.`
- An update notice, from the backend check: its lines, as printed, once.
- A version refusal, from the backend check: its text, verbatim and alone, in place of every line after the login.
  The reconnect lines are never said with it.

Paths, environment variables, file formats, exit codes, timeouts, the terms of service, which environment the SDK went
into and what you do next stay out of these messages. Name one only when it is why the setup stopped or when the user must act on it (an
activated environment the resolver skipped, exit 5 of step 2), and then say what the user does.

## From a command

A Plurai command runs this skill when its Step 0 prints a `SETUP` line, or when no backend tool is in your tool list, or
when a call is refused because Plurai is not logged in. You are told `from <command>` (Codex takes no arguments: you run
it for that command). Setup happens inside the command: do the steps below, then return to the command, which goes on
from where it was. The user asked for the command, not for setup, so say only the lines of "What the user sees".

- Steps 1 to 4 run as written, with the same lines: the SDK question, `Sign in to Plurai in your browser.`, the fallback
  url, `Logged in to Plurai.`.
- Already logged in (step 1 says so, or says the session uses a login from its environment): nothing is shown, and no
  login starts.
- Step 5 is skipped. The command's own readiness check reports any configuration that is still missing. The backend
  check still runs before step 6.
- A failure ends the setup. Say the one reason line of the step 4 table, which ends `Run <command> again.`, and stop.
  Nothing retries on its own, and `plurai login` never starts a second time.
- The user says no to the SDK install or to the new `.venv`: say `Plurai needs its SDK in this project to run <command>.`
  and stop.
- The user says no to the install for an older SDK (`Not now`): say the stop lines the resolver printed, as they stand, and stop.
- Step 6 connects the Plurai tools to the session. Then return to the command.
- No one can answer (a headless run: no ask tool, as in `claude -p`): start no install and no login. Run step 1 only. With
  no usable login, say `Plurai is not logged in. Sign in once in an interactive session, then run this again.` With a login
  but no Plurai tools in your tool list, say `Plurai is not connected in this session. Start a new session, then run this
  again.` Then stop.

Run directly (`/harness-setup` typed by the user), the skill works as written below: it logs in again when asked, shows
the status, and prints the configuration in step 5. The user can switch organization by logging in again.

## Steps

Run every command below exactly as written. Each one ends with success and says its result in what it prints, so the
user never sees an exit code: never add `echo $?`, `; echo` or anything else that prints one, and read the result from
the printed lines, not from an exit status. When a command prints something this skill does not name (a traceback,
`command not found`), stop and tell the user `Setup could not finish. Run /plurai-diagnose and share its output with
Plurai support.`

1. Check what is saved. The first line is the answer; a saved login is never shown, only its backend and account:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plurai_login.py" --status
   ```

   - `Logged in to Plurai.`: no new login is needed. Run directly, tell the user the "already logged in" line of "What
     the user sees" and go to the backend check, then step 5; when the user asks to log in again, go on to step 2. From a
     command, tell the user nothing and go to the backend check, then step 6.
   - `Not logged in to Plurai.` or `Your saved Plurai login can't be used.`: go on to step 2, telling the user nothing
     about it yet.
   - `This session uses a Plurai login from its environment.`: whoever launched this session gave it a login of its
     own, and `plurai login` does not run beside it. Do not start the login: tell the user
     `This session already has a Plurai login.` and go to the backend check, then step 5. From a command, tell the user
     nothing and go to the backend check, then step 6.
   - `Plurai cannot look for your login here: …`: stop, and tell the user that line.

2. Find the SDK:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require login || true
   ```

   A path printed on a line of its own is the `plurai` command: it is `<plurai>` below; go to step 3. Lines that start
   with `resolve_plurai:` are for you; show none of them. A `resolve_plurai: REFUSED` line means an SDK is installed that
   this plugin release will not run, or that the project's environment holds none, and what follows it says what to do:

   - A question (`header:`, `question:` and `options:` lines) is an SDK older than this plugin release needs, or none in
     the project's environment (its question is the one of "What the user sees"). Ask it
     with that header, text and options. The resolver call writes the ask's sanction itself; where the lines instead say to run the sanction
     first, run it (it is harmless here: no flow window is armed during setup). On `Install it`, run the `--install-pin`
     command the lines name, unchanged, and tell the user its last line; then run this step's command again for the path. On `Not now`, or when nobody can answer, say the stop lines that follow
     the question, verbatim and alone, and stop. A failed install prints one sentence and the stop lines: say them, and stop.
   - Stop lines with no question (an SDK newer than this plugin release needs, or one in a place this setup does not
     install into): say them, verbatim and alone, and stop. Never offer the install below over it.

   Anything else printed on stdout or stderr with no path means no usable SDK (none installed, or it does not provide
   `login`; the install below fixes both). Then find the project's Python environment. The SDK goes into it, because
   the agent's `simulate.py` imports the SDK beside the agent's own dependencies:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --venv 2>&1 || true
   ```

   - A path alone: the environment the SDK installs into (the activated venv, the active conda environment, a venv in
     the project folder, or the venv poetry or pipenv keeps for the project). A `skipped the …` line above it names an
     activated environment of another project that was passed over: tell the user that in one sentence. Go on with the
     install line below.
   - A command that creates `.venv` (`uv venv …` or `… -m venv …`) alone: the project has no environment. Ask whether
     to create it, with the question of "What the user sees"; it covers the install below too, so do not ask again
     before the install line. On yes, run that exact command, unchanged; if it prints an error, stop and tell the user
     in one sentence. On no, stop. Run directly, tell the user they can run `/harness-setup` later; from a command, say the SDK line of
     "From a command".
   - That command together with an `is a Python project` line: the project's own environment was not found. Tell the
     user in plain words, with the activation the line names for the project's tool, and ask which way to go. Either
     they start the harness again from a shell where the agent's environment is active and run <command> there:
     stop. Or they want a new `.venv` here: go on as for the command alone, and say that the agent's dependencies must
     go into it too.
   - Anything else (a Python older than the SDK needs, no Python new enough, a folder name the shell would act on):
     stop, and tell the user in one sentence what the line says and what to do.

   Then print the install line. It names the environment's own interpreter, so the install lands where the resolver
   looks:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line
   ```

   Ask the user whether to install it now, with the question of "What the user sees"; the harness shows them the line
   when it asks to run it.

   - On yes, run that exact line, unchanged. If the install prints an error, stop: tell the user in one sentence that
     the install failed and why, and do not start the login. If it succeeds, run the SDK check above again: a path is
     `<plurai>`; nothing printed means the install did not give a usable SDK: stop and say so in one sentence.
   - On no, stop. Run directly, tell the user they can run `/harness-setup` later; from a command, say the SDK line of
     "From a command".

3. Start the login as a background job:

   ```bash
   "<plurai>" login || true
   ```

   Run exactly this: no argument, nothing piped into it and no variable set in front of it. `|| true` ends the job with
   success whatever the login's outcome, so the harness reports no failure of its own to the user; the job's output
   says the outcome. It is not pre-approved: the harness asks the user to approve it, with the path the resolver
   printed, so only the SDK the resolver checked runs. Read its output until it prints the login URL (`https://…`), then
   tell the user the "login has started" lines of "What the user sees", with that URL as the fallback. The browser may
   have opened it already; there the user signs in or signs up, picks the organization and approves. The URL belongs to
   this login only: never suggest sending it to anyone else. Its host is the sign-in server's, not the backend's: the
   two differ by design, so do not mention it.

4. Wait for the job to end, the way the harness waits on a background command (it reports the end, or you read the job
   until it does). The job ends by itself when the login finishes, fails or times out after an hour; never stop it.
   Then read its output:

   - A line of its output reads `Logged in to Plurai.`: the login succeeded; tell the user that line. The lines after it
     are notes for a terminal user; say nothing about them. Then run the backend check, before step 5 or step 6.
   - No such line: read its last line and tell the user the one sentence this table gives for it, and nothing else. Never name an exit code,
     an HTTP status, the SDK's own line or what you did not do; they are for you, not for the user.

     | the job's last line says | tell the user |
     |---|---|
     | the sign-in was declined | `Sign-in was declined. Run <command> again.` |
     | the backend did not accept the sign-in, the sign-in carries no organization, or the backend refused or did not create the key | `Plurai could not create a key for this account. Check that your organization is set up in the Plurai dashboard, then run <command> again.` |
     | no sign-in arrived | `The sign-in timed out. Run <command> again.` |
     | the backend asked to wait, a name was taken, or a server was temporarily unavailable | `Plurai is busy. Wait a minute, then run <command> again.` |
     | a server could not be reached, a connection failed, or a server answered something unusable | `Plurai could not be reached. Check your connection, then run <command> again.` |
     | this SDK release is refused, or the SDK has no `login` | `This plugin is too old for Plurai. Update the plugin, then run <command> again.` |
     | the session already has a login of its own | what step 1 says for a session with a login from its environment |
     | a folder, a file or the backend address cannot be used | one sentence that says, in plain words, what it names and what to do about it |

   After any outcome but `Logged in to Plurai.`, stop: leave the next run to the user. From a command, that line is the
   whole report. Never start `plurai login` again
   yourself; each run opens a new sign-in in the user's browser.

   If the harness cannot tell you whether the job has ended (a new turn after the wait, or a wait the harness gave up
   on), run the status command of step 1 once instead: a `key:` line other than the one step 1 showed means the login
   finished; an unchanged one (or still none) means no new login is saved yet, so wait for the job again and read its
   output as above.

**The backend check.** Every path that reaches step 5 or step 6 runs this first: a login that just succeeded, or a login
that step 1 found. It makes one request and needs no Plurai tool, so it reads the backend's answer on a plugin whose
tools never connected:

```bash
python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py"
```

- Exit 0: go on to step 5, or step 6 from a command. A line it printed is an update notice: say it once, as printed,
  before the next step; it stops nothing.
- Exit 7 is a version refusal. Say what the check printed, verbatim and alone, and stop: no reconnect line, no wait, no
  configuration, no other line after it. Run from a command, the command stops too.
- Exit 8 is a credit refusal: its line is `CREDIT-STOP: <sentence>`. Tell the user the sentence after the label, as
  printed, and stop.
- Exit 3: the backend did not accept the saved login. Tell the user `Setup could not finish. Run /plurai-diagnose and
  share its output with Plurai support.` and stop. Never start the login again.

5. Print the configuration this harness still needs. The script reads the harness from the session:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/harness_setup.py"
   ```

   The script takes no url: it configures the backend the user logged in to, the rule `plurai login` follows too.

   When it prints one line (the harness needs nothing more), tell the user that line. Otherwise show the user the
   output as it is, in a code block. Do not edit the user's config files yourself:
   hook trust and the sandbox are the user's own settings, and the harness reads
   them only when it starts. A change to the configuration or the launch environment needs a new session; a new
   login does not. On Claude Code the plurai server tried to connect when the session started, before the login, and
   stays down until it is reconnected: after a login in this session, when no `plurai` backend tool is in your tool
   list, add the reconnect line of "What the user sees". `/mcp` reconnects it with the saved login.

6. From a command only: connect the Plurai tools to this session. Look at your tool list.

   - A `plurai` backend tool is in it: return to the command. Say nothing.
   - Claude Code, no backend tool: the server tried to connect before the login and stays down until the user reconnects
     it. Start the wait as a background job, then tell the user the connect line of "What the user sees" and end your
     turn:

     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}/scripts/wait_for_plurai_connect.py"
     ```

     The job prints `reconnected` when the server connects again, or `timed out` after ten minutes. On `reconnected`,
     look at your tool list: with the Plurai tools there, return to the command; with none (another session's connect
     woke the job), run the wait once more, and say nothing. On a second `reconnected` with no Plurai tools, tell the
     user `Plurai is not connected in this session. Start a new session, then run <command> again.` and stop. On
     `timed out`, say nothing: the user's next message resumes the command.
   - Copilot or Codex, no backend tool: the plugin's bridge looks for the new login on its next request. Call its
     `bridge_status` tool once. If the Plurai tools are in your tool list afterwards, return to the command. If not, tell
     the user `Restart <harness> to connect Plurai, then run <command> again.` (name the harness as the user knows it:
     Copilot or Codex) and stop.

## What the configuration covers

- **Every harness:** the backend and its key, which the saved login supplies. On a machine with no
  browser, the user saves a dashboard key once with `scripts/plurai_login.py --url <backend url>`, in a terminal of
  their own (never through the model: the token would land in the transcript).
- **Codex:** hook trust (press "Trust all and continue" once at startup), and shell network access.

## Rules

- Never ask for the key in the chat, never accept it as an argument, and never pass it on a command line or pipe it
  into one: whatever is typed to a model, or run through it, lands in its transcript. If the user pastes a key into the
  chat, say that it is now in the transcript, and tell them to revoke it in the dashboard and log in again.
- Never read or print `~/.plurai/credentials.json`; the status command says everything the user needs.
- Never inspect or name the plugin's environment variables, and never tell the user to set, export or unset one: they
  are for developers of the plugin. The commands above say everything the setup needs.
- To revoke a key, or to see which keys exist, the user uses the Plurai dashboard, or `plurai logout` for the key this
  machine saved. Deleting `~/.plurai/credentials.json` only makes this machine forget the key.
