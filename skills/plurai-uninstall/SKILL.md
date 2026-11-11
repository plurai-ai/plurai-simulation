---
name: plurai-uninstall
description: Remove what the Plurai plugin created in a project and in your home folder, after you confirm each part. Run it before removing the plugin itself.
disable-model-invocation: true
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plurai_uninstall.py":*)
---

# Plurai uninstall

**Usage:** `/plurai-uninstall`

Removes the files the plugin's flows created, in the project and in the user's home folder. It does not remove the
plugin: the last step gives the command for that. Everything it can remove is listed in
`docs/what-the-plugin-writes.md`.

## Steps

1. Show what exists. This removes nothing:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plurai_uninstall.py" plan
   ```

   Show the output as it is. It groups the files so each can be confirmed on its own:

   - `project`: each `plurai_sim/` folder (the generated `simulate.py`, config, PRD, mocks, scenarios, evals and runs),
     the plugin's lines in `.gitignore`, and generated Agentforce files.
   - `project-state`: `.plurai/` folders (upload previews and the upload audit log).
   - `home`: the plugin's flow markers, suite windows and tool environments under `~/.plurai/`.
   - `login`: the saved login, `~/.plurai/credentials.json`.

   If every group says "nothing found", say so and go to step 4.

2. Ask the user which groups to remove, one question with a choice per group that has files. Say before they answer:
   - `project` deletes files that may be committed; `git checkout -- <path>` restores them. The git index may still
     list files the scan staged; `git status` shows them as deleted.
   - `project-state` includes `.plurai/uploads/audit.log`, the local record of what was uploaded. Keep it if they
     need that record.
   - `login` signs this machine out. The key stays valid until it is revoked in the Plurai dashboard.

   Do nothing for a group they did not choose, and ask again for one they did not answer.

3. Remove the chosen groups, naming only those:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plurai_uninstall.py" remove --groups project,home
   ```

   Show the `removed` rows. A `could not remove` row is the user's to fix; say which path and why.

4. Tell the user what this skill does not remove:
   - Data on the Plurai backend (corpora, runs, mock environments). The dashboard manages it.
   - The Plurai SDK (`plurai-simulation-sdk`) in the project's virtualenv: `pip uninstall plurai-simulation-sdk plurai-mockenv`.
   - Agentforce org changes: run the Agentforce `clean` step first if they used it.
   - Files of their own: the agent's source, `.env` files, and hook trust in `~/.codex/config.toml`.

5. To finish, tell the user to remove the plugin and restart the session. Use the scope it was installed with:

   ```bash
   claude plugin uninstall plurai-simulation@plurai-plugins
   ```

   (`--scope project` for a project-level install; `plurai-simulation@plurai` when the plugin was installed from its own repository's marketplace.) This session's hooks recreate flow markers until it ends, so they
   run `/plurai-uninstall` once more after the restart only if `plan` still lists `home` files.

## Rules

- Never run `remove` without a group the user chose in this conversation, and never pass a group they declined.
- Never delete anything by hand that `plan` did not list.
- The script never follows a symlink and never touches files outside its lists; do not work around a refusal.
