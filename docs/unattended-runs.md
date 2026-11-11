# Unattended and CI runs

Claude Code asks before reading the plugin's own files, and nobody can answer that question in an unattended or CI run.
Three steps set a run up to work without it.

1. Sign in. An unattended run cannot sign in by itself, so sign in once interactively, or set `PLURAI_URL` and `PLURAI_TOKEN`
   as [Sign in without a browser](sign-in-without-a-browser.md) shows.

2. Add the plugin's folder as an additional working directory. Print its absolute path:

   ```bash
   echo "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache/plurai-plugins/plurai-simulation"
   ```

   Then put it in `~/.claude/settings.json`:

   ```json
   {
     "permissions": {
       "additionalDirectories": ["<the absolute path printed above>"]
     }
   }
   ```

   The folder has no version in it, so an upgrade needs no change. If you installed from a marketplace with another name,
   replace `plurai-plugins` with that name; `ls ~/.claude/plugins/cache` lists them.

3. Declare the run, because a headless run ends with its turn:

   ```bash
   PLURAI_SIM_NONINTERACTIVE=1 claude -p "<prompt>"
   ```

   Undeclared, the flow runs as interactive, and a run it starts in the background dies with the turn. See
   [Headless runs](install-shared.md#headless-runs).
