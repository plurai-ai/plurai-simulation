# Data & privacy

Any flow that moves your data to the Plurai-hosted backend — building a RAG corpus, uploading a
scenario bundle or document examples, analyzing production traces — runs through the `plurai` SDK's
local ingest commands (`plurai ingest-corpus`, `plurai ingest-bundle`, `plurai ingest-doc-examples`,
`plurai ingest-traces`, `plurai ingest-run`, `plurai ingest-run-report`). What you upload, anonymized or raw, is handled
under the Terms of Service and the Privacy Policy you accepted at sign-up. `anonymize:` applies only to the uploads that
build the env (traces, documents and knowledge base, scenario bundles, document examples, and the bundle overview);
`plurai ingest-run` and `plurai ingest-run-report` always upload the simulation run as it is.
Besides these commands and the backend calls the flows make, one thing leaves your machine: on Claude Code, the overview
page is published to claude.ai as a private page at the end of a run. It shows the run's content (conversations, verdicts,
scenarios and findings) whatever the anonymization posture: `anonymize:` governs only what is uploaded while the env is
built, never the simulation run or its page.

**Local anonymization is opt-in defense-in-depth on top of that, not the only control.** Each
simulation env declares its **anonymization posture** once, via a top-level `anonymize: true | false`
key in `plurai.config.yaml` — `/simulate` or `/simulation-scan-agent` asks this once per env, defaulting to
**`false` (raw)**. Under `anonymize: true`, every upload that builds the env scrubs PII locally before
upload, fail-closed (it refuses to run without the SDK's `anonymize` extra + a spaCy model installed) — so
none of those env-build uploads leaves your machine un-anonymized *within that posture*. Under the raw default, ingestion uploads data
as-is and prints a loud `RAW (anonymization off)` banner; that upload is governed by the terms rather
than a local scrub. Either way, the SDK does the scrubbing and the upload, not the plugin. **Mixed-posture envs are refused fail-closed** — once an env has uploaded under
one posture, switching to the other is rejected; start a fresh env instead.

Each `plurai ingest-*` invocation is a normal Bash tool call, so in Claude Code's default permission
mode you see the exact command and approve it before it runs. Under `/simulate` or auto mode it runs without that
prompt. The CLI also writes a
disclosure preview (field/file counts, bytes, scrub summary, and the posture used) to stderr and an
audit trail to `<your-project>/.plurai/uploads/audit.log`. That file is the authoritative record of
what left your machine and under which posture — for both postures.

## Privacy and local state

The plugin sends no client-side telemetry. No hook records which commands or tools you ran, and nothing is
posted to the Plurai backend outside the MCP tool calls and the backend calls these flows make. The only
local record of what left your machine is the upload audit log described in the section above.

**One outside read: the update notice.** At most once a day, when the backend has accepted the plugin, the plugin
reads the list of release tags of the public plugin repository (`https://api.github.com/repos/plurai-ai/plurai-simulation/tags`)
to tell you when a newer plugin release exists. The request is a plain HTTPS GET with no token and no redirect followed. It
carries the plugin's own name and version and nothing about you, your
project or your Plurai account; GitHub sees your IP address as it does for any request. A failed read says nothing and is not
retried that day. Set `PLURAI_NO_UPDATE_NOTICE=1` in the environment that starts your session to turn the read off: nothing
is requested and no notice is printed. The SDK install that follows a plugin update
fetches from PyPI, and only after you say yes.

**`.plurai/` is runtime state — gitignore it.** Add `.plurai/` (upload previews and audit log, flow state)
to your project's `.gitignore`.
