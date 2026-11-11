# S4 `code`-channel variants (SDK sub-mode + CLI sub-mode)

Read this when `action_channel: code` (either sub-mode) — it carries the
interception-opacity contract (shared by both `code` sub-modes) and the two
`code` S4 variants (SDK-shadow and CLI-shadow) in full. The `tools` variant and
the S4 branch selector stay in the SKILL.md body; `tools`-channel benches never
need this file.

## Interception opacity (`code` / `cli_http` — the Bash-bearing channels)

The nested agent-under-test must believe it is hitting the **real upstream API**.
For `tools` it cannot tell (no `Bash`). For `code` / `cli_http` it CAN run `Bash`,
so opacity must be filesystem- and content-shaped, never a tool-flag cosmetic:

- **Isolated cwd (load-bearing; enforced by `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-skill/scripts/verify_harness_opacity.py`
  `assert_opaque_cwd`).** Both `code` templates run the nested session from a fresh
  tempdir (`_isolated_agent_cwd()`) holding ONLY a copy of the production `skills/`
  surface — never the harness dir (`plurai_sim/`, which holds `simulate.py`,
  `code_surface.yaml`, `simulation_env/mocks.py`, `plurai.config.yaml`). Without it
  the agent `cat`s `mocks.py` and announces "this is a simulation." The documented
  `../skills/<name>/…` command invocation still resolves because the
  `skills/` tree is copied alongside the cwd. Because the prompt is injected as a
  `system_prompt`, there is **no** "directory you loaded this file from": the CLI is
  reachable ONLY via the `{skill_dir}` token → `../skills/<name>` (filled by `seed_state`).
  A source command's loaded-from / absolute-path CLI instruction is therefore false in this
  runtime, which is why setup rule 5 substitutes that token.
- **Relocated env paths (load-bearing; enforced by `assert_opaque_env`).** The
  injected `PLURAI_CODE_SURFACE` / `PYTHONPATH` (SDK) / `NODE_OPTIONS=--import` (CLI)
  must NOT resolve — via `dirname` / `PYTHONPATH` / `--import` — to any file under the
  customer `plurai_sim/`. Both templates relocate them PER SESSION: the surface is
  copied to a clean shim dir (`<shim>/surface.yaml`, its OWN `mkdtemp` root, SEPARATE
  from the isolated cwd's root); the SDK `sitecustomize.py` is written into that shim
  via `plurai.write_sitecustomize(<shim>)` and the nested `PYTHONPATH` heads with the
  shim (NOT `_DIR`); the CLI preload points at the INSTALLED `plurai` package
  (`Path(plurai.__file__).parent/_mocking/node/fetch_shadow.mjs`), never a harness
  copy (keeps the preload's require-trio co-located). This closes the measured
  `cat "$(dirname "$PLURAI_CODE_SURFACE")"/simulation_env/mocks.py` pivot.
- **Leaving `Read` out of `tools` is defense-in-depth only — COSMETIC while `Bash` is
  present** (`cat` defeats it). The cwd isolation + env-path relocation are the real
  controls, not the `tools` allowlist.
- **Served responses must read like the real API** — the deployed mock's error
  messages mimic the upstream vendor (the Plurai backend's mock generation requires this):
  no internal vocab `world-state` / `simulation_env` / `plurai_mockenv` and
  no Python `traceback` over the wire.
- **RESIDUAL — explicitly NOT closed (Option-D tier: an out-of-trust-domain runner).**
  Relocation closes the CUSTOMER-`mocks.py` pivot ONLY; it does NOT make the run
  undetectable. A Bash-bearing agent can still: env-dump and `cat` the shim's
  `sitecustomize.py` / the installed `plurai` runtime (`fetch_shadow.mjs` /
  `code_hook.cjs`) / the shim's `surface.yaml` — learning "this is a plurai sim" but
  NOT the customer's specific mock answers; dump a grandchild's `os.environ` (the agent
  authors the command and reads its stdout) or `/proc/<pid>/environ`; see `PLURAI_*` in
  a passive `env` (the persistent shell still carries them — silencing that is the
  DEFERRED Layer-2 PreToolUse hook, not this deliverable); and hit the localhost mock
  URL. Closing these needs running the grandchild outside the agent's trust domain
  (container/namespace + egress-deny, or a sidecar runner). Do NOT claim the leak is
  fully closed or that the agent cannot detect the simulation.

Gate (run before the S6 smoke):
`python ${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-skill/scripts/verify_harness_opacity.py plurai_sim/simulate.py`
must print `OPACITY GATE PASSED` — it now asserts BOTH that the `cwd` is harness-free
(`assert_opaque_cwd`) AND that no injected env value resolves inside the customer
`plurai_sim/` (`assert_opaque_env`). A failure means the template's `cwd` neighbours
the harness OR an env path was not relocated (regression) — fix before proceeding.

## S4 · `code` · SDK variant (skill writes + runs real SDK code, import-hook intercepts)

Generate this template when Step 7 inferred **`action_channel: code` and S3b
authored a `modules` surface** (the SDK sub-mode). It runs the skill exactly as
production does — the skill writes a snippet (`resend.Emails.send(...)`) and runs it
via `Bash` — while a generic `plurai` SDK import-hook shadows the vendor module and
routes every call over the **same frozen `/mock/execute` wire** as the `tools`
channel, so `tool_events` + world-state are recorded identically and the real vendor
API is never reached.

The hook is **generic SDK runtime shipped by `plurai-sdk`** — you generate NO
shim code. It is configured entirely by data: a `code_surface.yaml` (the plugin
authors `plurai.config.yaml`; the SDK projects `code_surface.yaml` from it via
`plurai.emit_code_surface`, called by the generated `simulate.py` at bench setup) plus
the env this template injects into the nested session. The
snippet's grandchild process (`python send.py`) installs the hook **before**
`import resend` via a `sitecustomize.py` on the HEAD of `PYTHONPATH` (a clean shim
dir, NOT `_DIR` — see "Interception opacity") that calls
`plurai.install_code_hook_from_env()` (it reads `PLURAI_CODE_SURFACE`); a Node SDK
snippet uses `node --require <hook>`.

The differences from the `tools` variant are confined to `seed_state`: (1) the
`tools` allowlist is `['Bash', 'Write']` (so the skill can author and run the
snippet); `Read` stays out (cosmetic defense-in-depth) and so does every other
built-in. The session runs from an ISOLATED cwd (`_isolated_agent_cwd()`, see
"Interception opacity"), NOT the harness dir. (2) No in-process MCP server / `@tool`
wrappers / `apply_mocks` — the world-state coherence comes from the hook → wire,
not from local tool bodies. (3) a nested `env=` is built that points the hook at
the live session + mock + surface (and the mock's bearer), and the vendor creds are
withheld from the CLI's environment (`_withheld_env()` around `connect()`).

**Read `references/simulate_py_code_sdk_variant.py.txt` and emit it verbatim** as `plurai_sim/simulate.py`. It reuses the `tools` variant's `invoke_agent` body (reproduce it verbatim from `references/simulate_py_tools_variant.py.txt`); only `seed_state` / `_nested_env` differ. Fill `_VENDOR_CRED_VARS` with every vendor secret the customer `.env` carries.

You do NOT emit a `plurai_sim/sitecustomize.py`. The template writes the
`sitecustomize.py` into a clean per-session shim dir at runtime via
`plurai.write_sitecustomize(<shim>)` (`_relocate_harness_paths()`), and heads the
nested `PYTHONPATH` with that shim — NOT `_DIR` — so the grandchild installs the hook
before any vendor import WITHOUT rooting any injected path in the harness (see
"Interception opacity"). The runtime `plurai.write_sitecustomize` (backed by the SDK's
`sitecustomize_source()`) is the sole source of the prelude body — the skill writes
nothing extra alongside `simulate.py`.

`code`-variant contract notes (the shared `tools` notes also apply):

- **Bash is enabled on purpose and runs unsandboxed; what keeps the vendor calls on
  the mock is the module shadow and the withheld creds, not tool denial.**
  `_VENDOR_CRED_VARS` MUST list every vendor secret the customer `.env` carries; the
  hook shadows the named module(s) so the real SDK can't be imported.
- **Withhold, never filter.** claude_agent_sdk lays `options.env` OVER the
  environment the CLI starts with, so a key left out of `options.env` still reaches
  the agent's shell. `_nested_env` carries only the injected keys, and
  `_withheld_env()` takes the vendor creds and `NETWORK__API_KEY` (`_WITHHELD_ENV`) out of
  this process's environment while `connect()` starts the CLI, then puts them back.
- **The agent's processes dial the mock url itself, with the harness's bearer.**
  `plurai.mock_service_url()` is the mock url this harness dials (a hosted mock's
  `/mock/<id>` url), and `_nested_env` passes `PLURAI_TOKEN` on, which the
  interception hooks (Python and Node) send as the bearer. Without it every nested mock
  call on a hosted mock is a 401.
- **The interception is recorded, so a rewrite is reported.** `write_sitecustomize`
  records the file it writes, and the template records the shim (`plurai.watch_interception`);
  a run whose agent changes one carries the `mock.interception_changed` finding and
  an `interception_changed` key on that row's transcript.
- **The deflector folds into this channel.** A Bash-deflector bench is just `code`
  with a `code_surface.yaml` whose method shim always errors, plus the same
  `tool_call_count('<raw>') > 0` violation expression — no separate template.
- **Read the session id AND the mock url inside `seed_state`, never at import — and
  never from `os.environ`.** `plurai.start_session` + `plurai.configure` run before
  `seed_state`, so `active_session_id()` and `mock_service_url()` are both
  valid there; each returns `None` outside an active session (e.g. after `shutdown`).
  The mock base url is NOT a harness env var: the orchestrator spawns this process
  with `env=None` and supplies the mock url over IPC onto the SDK's configured client,
  so the template reads the url the agent's processes dial via the public
  `plurai.mock_service_url()` accessor and **fails closed** if it is `None`. Do not reach
  for `os.environ['PLURAI_MOCK_URL']` — that key is unset in this process and would
  `KeyError` every `code` bench in connect mode.
- **`PLURAI_CODE_SURFACE` points at a per-session shim copy** (`<shim>/surface.yaml`,
  NOT `plurai_sim/code_surface.yaml`), so `dirname $PLURAI_CODE_SURFACE` is the shim,
  never the harness. The ensure-emit block at the top of this template projects
  `code_surface.yaml` from `plurai.config.yaml` if absent (the backend does not emit the
  surface), then `_relocate_harness_paths()` copies it into the shim — so the hook never
  fails closed on a missing file as long as `plurai.config.yaml` is present.
- **Node skills:** a Node snippet installs the hook via `node --require <hook>`
  (the SDK ships that recipe too); the same `code_surface.yaml` drives it, with the
  per-module `language: node` selecting the `{data, error}` return envelope.

## S4 · `code` · CLI variant (skill runs the vendored command, fetch-shadow preload intercepts)

Generate this template when Step 7 inferred **`action_channel: code` and S3b
authored a `cli_http` surface** (the CLI sub-mode). It runs the skill
exactly as production does — the skill runs its vendored command via `Bash` (for example
`node {skill_dir}/scripts/cli.mjs <subcommand> '{…}'`; another skill's may be `bin/run.js`
or `dispatch.py`. The `{skill_dir}`
placeholder in `skill_prompt.md` is filled by `seed_state` → `../skills/<name>`; see
SKILL.md S2 rule 5) — while a generic
`plurai` **`fetch`-shadow preload** (`runtime: fetch_shadow` in
the `cli_http` surface) intercepts the CLI's HTTP egress and routes every matched
request over the **same frozen `/mock/execute` wire** as every other channel, so
`tool_events` + world-state are recorded identically and the real upstream API is
never reached. Any egress NOT matched
by an `egress_guard` aborts (`unmapped_egress: fail_closed`).

The preload is **generic Node runtime shipped by `plurai-sdk`** — you generate NO
shim code; you only wire the env that points it at the deployed surface. It is loaded
into every `node` grandchild **before the CLI's first `fetch`** via
`NODE_OPTIONS=--import=<installed-package preload path>` (the propagation
path that reaches every grandchild) — resolved from the INSTALLED `plurai` package
(`Path(plurai.__file__).parent/_mocking/node/fetch_shadow.mjs`, via `_fetch_shadow_path()`),
NOT a harness copy, so the preload's require-trio stays co-located and no `--import`
path roots in `plurai_sim/`. `code_surface.yaml` is projected from `plurai.config.yaml`
by the SDK at bench setup, not by the backend; `_relocate_surface()` then copies it into a
clean per-session shim dir and `PLURAI_CODE_SURFACE` points at that
copy (`<shim>/surface.yaml`, NOT `plurai_sim/code_surface.yaml`). The preload reads
`PLURAI_CODE_SURFACE` to learn the subcommand→egress map and the mock wire.

The differences from the SDK variant are confined to `_nested_env` / `seed_state`:
(1) `Bash` is enabled (same as SDK); (2) **no `sitecustomize.py` / `PYTHONPATH`
injection** — the CLI is Node, so the preload is injected via `NODE_OPTIONS` instead;
(3) the preload path is an **absolute** path resolved from the INSTALLED `plurai`
package (`_fetch_shadow_path()`), NOT relative to `_DIR` — `NODE_OPTIONS=--import` needs
an absolute (or `file://`) specifier to resolve in the grandchild's cwd-independent
context, and pointing it at the installed package keeps it out of the harness AND keeps
the preload's require-trio co-located.

**Read `references/simulate_py_code_cli_variant.py.txt` and emit it verbatim** as `plurai_sim/simulate.py`. It reuses the `tools` variant's `invoke_agent` body (reproduce it verbatim from `references/simulate_py_tools_variant.py.txt`); only `seed_state` / `_nested_env` differ (the preload is injected via `NODE_OPTIONS=--import`, no `sitecustomize.py` / `PYTHONPATH`).

There is **no `sitecustomize.py` for the CLI variant** — the preload is Node and is
injected via `NODE_OPTIONS=--import`, not via a Python `PYTHONPATH` shim. (The
`fetch_shadow.mjs` preload itself ships with `plurai-sdk`;
this template injects its INSTALLED-package path + the env that points it at the shim
surface copy — it copies neither the preload nor the surface into the harness.)

`code` · CLI-variant contract notes (the shared notes + the SDK-variant safety notes
also apply):

- **The preload path MUST be absolute, resolved from the installed `plurai` package.**
  `NODE_OPTIONS=--import` resolves the specifier in the grandchild's process context,
  not the cwd; `_fetch_shadow_path()` returns
  `Path(plurai.__file__).parent/_mocking/node/fetch_shadow.mjs` (absolute, outside the
  harness, fail-closed if the package layout is missing). Do NOT copy the preload into
  `plurai_sim/` or the shim — the require-trio (`fetch_shadow.mjs` → `./code_hook.cjs`
  → `./code_hook_loader.mjs`) must stay co-located in the installed package. A relative
  or missing `--import` silently fails to load → the real `fetch` runs → a real upstream
  call. (The smoke's ≥1 tool_event check catches this.)
- **What keeps the CLI on the mock is `unmapped_egress: fail_closed` and the withheld
  creds, not network teardown.** The fetch-shadow preload aborts any egress not matched
  by an `egress_guard`, so a real upstream call is impossible even if the CLI changes
  its URL. List every vendor secret in `_VENDOR_CRED_VARS` (a public read-only
  CLI may have none). The template records this scenario's surface copy
  (`plurai.watch_interception`), so a run whose agent rewrites it says so; the shared
  installed preload is not recorded.
- **`Bash` is enabled; `Write`/`Read` stay blocked by default** (a pure CLI runner
  authors no files). Only unblock `Write` if the skill's workflow writes a params
  file before invoking the CLI.
- **The nested session runs from an ISOLATED cwd** (`_isolated_agent_cwd()`), not
  the harness dir — a copy of the `skills/` tree only, so
  `../skills/<name>/…` still resolves while `plurai_sim/` (mock
  source, channel config) is unreachable by relative path. See "Interception
  opacity"; `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-skill/scripts/verify_harness_opacity.py` enforces it.
- **`PLURAI_CODE_SURFACE` points at a per-session shim copy** (`<shim>/surface.yaml`,
  NOT `plurai_sim/code_surface.yaml`, same relocation as the SDK variant via
  `_relocate_surface()`), so `dirname $PLURAI_CODE_SURFACE` is the shim, never the
  harness. For CLI mode the surface must contain the `cli_http` block. The ensure-emit
  block at the top of this template projects `code_surface.yaml` from `plurai.config.yaml`
  if absent (the backend does not emit the surface), then `_relocate_surface()` copies it into
  the shim.
