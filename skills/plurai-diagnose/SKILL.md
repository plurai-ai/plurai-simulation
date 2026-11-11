---
name: plurai-diagnose
description: Print a bundle to send to Plurai support.
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plurai_diagnose.py")
---

# Plurai diagnose

**Usage:** `/plurai-diagnose`

Plurai clients send no telemetry, so what the user sends to support is all support has. This prints that
text. It holds no token, no credential value and no file from the user's project.

## Steps

1. Run the bundle in the project the user works in:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plurai_diagnose.py"
   ```

   It prints the plugin version, the harness, the operating system and Python. When the SDK is installed it then
   runs `plurai diagnose` and prints that bundle too: the SDK version and extras, whether a saved login is
   present (never its values), the command run id, the latest run's name and
   error ids, and the local references. When the SDK is missing or too old it says so, names the install line and
   prints what the plugin can see itself.

2. End the turn with this message and nothing else: one line telling the user to send the output to
   support@plurai.ai or the Plurai Discord (https://discord.gg/bg34gh76uJ), then the output
   exactly as it is, in one code block. The message ends at the closing fence. Add nothing to it, and nothing
   around it but that one line.

3. The lines in the bundle such as `hook_refused hook-1a2b3c4d` or `install_failure inst-1a2b3c4d` are local
   references for support to search. The user needs no explanation of the
   references or of which one fits what failed.

## Rules

- Never add anything to the bundle: not the contents of a file, not an error message from the session, not a path.
  If the user wants to describe the problem, that goes in their message to support.
- Never read or print `~/.plurai/credentials.json`, the token or a `.env` file to help the user contact support.
- The text after the code block is at most the one pointer line. Advice, a note on which
  reference to quote and an offer to help further all belong to a later turn, never to this message. A Stop hook
  (`hooks/report_guard.py`) refuses a message that has more than one line after the block, and a message with no
  line outside the block that names the support email from step 2.
- If the SDK is missing, the bundle says so and names the install line; that is already in the block.
