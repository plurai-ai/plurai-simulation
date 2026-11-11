# Sign in without a browser

The first Plurai command normally signs you in through your browser. On a machine with no browser, such as a remote
server, use a key from the [Plurai dashboard](https://simulation.plurai.ai) instead. The backend address (`<url>`) is given by Plurai with your account.

1. Create a key in the [Plurai dashboard](https://simulation.plurai.ai).
2. Save it, in a terminal of your own. The command asks for the key and does not show it as you type:

   ```bash
   python3 <plugin root>/scripts/plurai_login.py --url <url>
   ```

   `<plugin root>` is where the plugin is installed: `~/.claude/plugins/cache/plurai-plugins/plurai-simulation/<version>/`.

3. Claude Code reads the backend address from its launch environment, so export the same address there and restart it:

   ```bash
   export PLURAI_URL=<url>
   claude
   ```

To use environment variables instead of a saved login, export both in the shell that launches Claude Code:

```bash
export PLURAI_URL=https://<host>
export PLURAI_TOKEN=<token>
claude
```
