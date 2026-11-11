# Troubleshooting

## Auto permission mode

The plugin's commands run under Claude Code's **auto mode** with no setup.

The overview page is published once per run, at its end, from your project's `plurai_sim/artifacts/`. Claude Code asks
before publishing a file outside the folder it was started in, in every permission mode, and an `Artifact` allow rule
does not skip that question: start `claude` from your agent's folder or any folder above it. Otherwise the plugin
publishes nothing and names the local page instead. The first publish of the project's page may still ask once.

If the classifier ever blocks a plugin script call in your environment, the durable fix is an
`autoMode.environment` or `autoMode.allow` entry in `~/.claude/settings.json` (keep the literal
`"$defaults"` entry in any list you set), for example:

```json
{
  "autoMode": {
    "allow": [
      "$defaults",
      "Running scripts under the plurai-simulation plugin directory (flow-window.sh, poll_job.sh, wait.sh) is routine simulation-flow work"
    ]
  }
}
```
