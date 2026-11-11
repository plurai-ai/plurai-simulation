# Step 7 (claude-skill only): infer the `action_channel`

Read this when `LANG = "claude-skill"` (Step 0). It carries the full
`action_channel` inference procedure — how the skill acts during the bench,
orthogonal to each operation's mock mode. Non-claude-skill agents skip it.

**Only when `LANG = "claude-skill"`** (Step 0), determine the **`action_channel`**:
*how* the skill acts during the bench. It is orthogonal to each operation's mock
mode (a `mock`/`real`/`observe` op resolves the same way inside the mock env
regardless of channel). Two values — and `code` has two sub-modes (**SDK-shadow**
and **CLI-shadow**) selected by the surface S3b authored:

| | **`tools`** | **`code`** |
|---|---|---|
| How the skill acts | bench hands it one in-process MCP tool per operation; `Bash`/`Write`/`Read` blocked (hermetic) | skill runs its **real production code** via `Bash` — **SDK sub-mode:** writes + runs SDK snippets (`resend.Emails.send(...)`), a generic `plurai` import-hook shadows the vendor *module*; **CLI sub-mode:** runs the vendored `node scripts/cli.mjs <subcommand> '{…}'`, a `fetch`-shadow preload intercepts the CLI's HTTP egress — both land on the same mock world-state |
| Tests | tool orchestration + honesty | the same, PLUS the skill's **production-call correctness** (SDK: method, arg structure, idempotency-arg position, native-return handling; CLI: subcommand selection, JSON-param shape, HTTP method/URL the subcommand issues) — the production code path |
| Safety | nothing but mock tools is reachable | vendor creds stripped + the production surface shadowed (SDK module shadowed by the import-hook; CLI `fetch` egress shadowed by the preload, unmapped egress fails closed); an egress proxy is not part of the channel |
| Same evidence? | yes | yes — the hook/preload routes over the **same frozen `/mock/execute` wire**, so `tool_events` + world-state are recorded identically |

**Do NOT ask the operator and do NOT default to `tools`.** The channel is a
property *of the customer's code*, not an operator preference — the plugin must
work itself into the form the customer's code needs. `code` runs the customer *as
it actually behaves*; `tools` is a deliberately simplified (hermetic) abstraction
the bench imposes. So for a code-writing skill, `code` is **determined** by the
customer; an operator never overrides it in the normal flow.

**Inference rule — read it off the S3 tool surface (sub-skill S3/S3b).** There
are now THREE outcomes — `code` has two sub-modes (SDK-shadow and CLI-shadow),
and `tools` is the residual. Each is decided by which interceptable surface the
operations carry:

- **`code`, SDK sub-mode** — a claude-skill whose **operations are real vendor-SDK
  method calls**. The signal is extracted in S3b: each operation's descriptor
  carries `original_module` / `original_function` (the `(module, method)` the hook
  keys on — `{original_module: resend, original_function: "Emails.send"}`). Their
  presence means the skill **writes + runs SDK code** in production (`resend.Emails.send(...)`),
  so the faithful channel is `code` and the hook shadows the vendor *module*.
- **`code`, CLI sub-mode** — a claude-skill whose **action verb is a vendored
  `scripts/cli.*` invoked by the command** (e.g. `node <skill-dir>/scripts/cli.mjs rank-items '{…}'`), where each op's descriptor
  carries **`original_command`** (the subcommand, e.g. `"rank-items"`) **+
  `http_mapping`** (`{method, url_pattern}` — the HTTP shape the subcommand issues
  upstream). The vendored CLI *is* the customer's production code; the faithful
  channel runs it for real and shadows its `fetch` egress onto the mock wire. So a
  CLI skill is **`code`, not `tools`**, although there is no SDK *module* to shadow.
  There is no `original_module`/`original_function`; the CLI signal stands in for it.
- **`tools`** (the residual) — a skill with **no interceptable surface at all**:
  pure in-process tool invocation with neither an SDK method-call surface
  (`original_module`/`original_function`) nor a vendored-CLI surface
  (`original_command`/`http_mapping`). Nothing for the hook to shadow, so the
  hermetic in-process MCP tool surface is the faithful channel.

**FAIL CLOSED on ambiguous shape.** If BOTH a module-shadow signal
(`original_module`/`original_function`) AND a CLI signal
(`original_command`/`http_mapping`) appear across the operations, the skill's shape
is ambiguous — do NOT guess a sub-mode and do NOT silently prefer one. **Raise/stop**
and report the conflicting ops. There is **no operator override and no env var** to
resolve it; the customer's code is expected to be one shape, and a mixed surface is
a recording or authoring error that must be fixed upstream, not papered over.

Use judgment per customer — don't assume every claude-skill is `code`. The
decision is mechanical, in order: *do the operations carry a vendor-SDK
`module.Method(...)` surface (`original_module`/`original_function`)?* → `code`
SDK sub-mode. *Else do they carry a vendored-CLI surface
(`original_command`/`http_mapping`)?* → `code` CLI sub-mode. *Both?* → FAIL CLOSED.
*Neither?* → `tools`. A "Bash-deflector" bench is **part of `code`** (a
deflector is `code` with a `code_surface.yaml` shim that always errors + the
`tool_call_count('<raw>') > 0` violation) — it is not a separate axis.

Record the inferred value as `agent.action_channel: tools | code` in
`plurai.config.yaml` (Step 8) and thread it into Step 10's claude-skill branch —
the sub-skill's S4 selects the matching `simulate.py` variant (and, for `code`,
the SDK vs CLI sub-mode follows from whether S3b authored a `modules` surface or a
`cli_http` surface — see S3b).

**No override — always infer.** The channel is a determination, not a setting: the
plugin reads **nothing** from the environment to pick it and asks no one, under either
declared run driver (`workspace.run_driver: interactive | headless`, for which
`PLURAI_SIM_NONINTERACTIVE=1` is an accepted alias). Always apply the inference rule
above; never default to `tools`.
