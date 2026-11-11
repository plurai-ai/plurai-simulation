# Bridge contract: protocol 1

A non-Python agent runs under the **Python-SDK harness** (`simulate.py`), which drives the agent in its own runtime through a
**bridge child** process. This document is the protocol between the two halves. It is language-neutral: a runtime ships a
bridge child by implementing the child side of it. **Node is the only child that ships.** Any other runtime is a stop that
names it ("no bridge child ships for `<runtime>` yet"), never an improvised harness.

The shared values (timeouts, limits, env names, the provider and sink tables, the message vocabulary) are machine-readable in
`references/node/runtime/constants.json` of the `simulation-setup-bridge` skill. The per-agent mapping is
`bridge/bridge.map.json`, schema `plurai.bridge.map/1`:
`references/node/bridge.map.schema.json` of the same skill.

---

## A.1 Process

`simulate.py` (through `bridge/host.py`) spawns the child **lazily**, on the first `seed`, `turn` or `route_probe`. It
never spawns at import, in `verify_config` or `verify_simulate`, or in Init without a resume seed.

- **Before spawning**, the host checks that every tool the map names is in the dispatch table, and refuses start
  (`HarnessFault`) if one is not.
- **Spawn** with `subprocess.Popen` (never a loop-bound subprocess):
  - `argv = [node, --import, <abs runtime>/register.mjs, <abs runtime>/child.mjs]`;
  - `cwd = <agent_root>`;
  - `start_new_session=True`;
  - `pass_fds=(child_end,)`;
  - `stdin=DEVNULL`; `stdout` and `stderr` go to the per-session log (A.8);
  - `env = os.environ` plus the map's `env` block (a row named `PLURAI_BRIDGE_*` or in `map_env_forbidden` is dropped,
    with a log line), plus `PLURAI_BRIDGE_FD`, `PLURAI_BRIDGE_MAP` (absolute path),
    `PLURAI_BRIDGE_PROTOCOL=1` and `PLURAI_BRIDGE_ALLOW_HOSTS` (a JSON array: the config's `egress.allow_hosts`, `[]` when
    absent; the host reads it from `plurai.config.yaml`, because the child has no YAML parser).
- The parent **closes `child_end` right after spawn**. Otherwise a dead child never yields EOF.
- The child **deletes every `PLURAI_BRIDGE_*` variable from `process.env`** after reading them, and never sets
  `NODE_OPTIONS`.
- A Windows host is refused, because it has no `pass_fds`.
- The bench's egress declaration says `agent_runtime: subprocess`: the SDK's in-process egress guard does not reach the
  child, and the child's own guard (A.7) does.

## A.2 Channel

- One AF_UNIX `socketpair` stream. Python side: `asyncio.open_unix_connection(sock=…, limit=64 MiB)`. Node side:
  `new net.Socket({fd, readable: true, writable: true})`.
- Framing is UTF-8 JSON, one object per `\n`, at most **64 MiB per line on both sides**. A longer line is a `protocol` error
  naming the message type and its size.
- Every message has `type`. Ids are strings, unique per direction for the child's life: host `r<n>`, child `x<n>`.
- The child's stdout and stderr belong to the agent. Bridge diagnostics go to raw stderr (the log), never to the product's
  logger. Nothing the agent prints can reach the channel.

## A.3 Messages

Host → child:

| type | fields |
|---|---|
| `ping` | `id` |
| `turn` | `id, thread_id, turn_index, text, parts?, request{}` (`parts`: the new user message's content parts, unconverted, when they are a list; `request`: the request-scoped values the production client sends on every call) |
| `seed` | `id, thread_id, messages[]` (the SDK resume-seed messages, unconverted), `agent_state?{}`, `request{}` (the same values `turn` carries) |
| `route_probe` | `id` |
| `ext_result` | `id` (echoes the `ext_call` id), `ok: true, value` **or** `ok: false, error{kind: mock_error\|harness, message}` |
| `shutdown` | `id` (no reply; the child exits) |

Child → host:

| type | terminal? | fields |
|---|---|---|
| `ready` | no (once, unsolicited) | `protocol: 1, child: "node", runtime_version, tsx_mode: sync\|async, hooks: "registerHooks"` |
| `pong` | yes | `id, entry_exports[]` (⊇ `load, turn`; `seed` optional), `mapped{packages, modules, http}` (counts) |
| `ext_call` | no | `id, req` (the in-flight host request id, or `null`), `tool, args{}, via: package\|module\|http, path, site` |
| `event` | no | `req, kind: egress_refused\|unmapped_call\|unsupported_shape\|sink, shape?, host?, path?, via: package\|module\|http\|socket, site, count` |
| `turn_done` | yes | `id, text, trail: [...] \| null, trail_source: <adapter id> \| null, finish_reason, input_context?, appended?` |
| `seed_done` | yes | `id, saved, restored_state_keys[]` |
| `route_probe_done` | yes | `id, results[{entry, kind: package\|module\|replace\|http, swapped, path?, tool?, dispatched, error?}], egress_guard: {fetch: refused\|open, socket: refused\|open}` |
| `error` | yes when `id` is set | `id?, req?, kind: entry_failed\|agent_raised\|seed_unsupported\|protocol, owner?: agent\|other, message, stack?, site?, provider?{host, status?, network?: true}` |

- **Exactly one terminal reply per host request.** The child refuses a second concurrent host request with
  `error{kind: protocol}`.
- `site` is the first agent-source frame, as `<agent-relative file>:<line>`, or `null`.
- `appended` (on `turn_done`, optional) is `[{site, text}]`: text the product's own code adds to the reply after the
  model's output, each `text` verbatim as it appears in `text`, `site` the `<agent-relative file>:<line>` that adds it.
  The `simulate.py` template forwards it as the turn's `appended_output`, each entry with `source: 'product'`.
- `shape` (on `unsupported_shape`) is one of the refused shapes named in A.11. It is absent for a value that cannot
  cross the bridge (a function argument, A.5).
- `count` is the number of occurrences the event stands for (≥ 1). A child may coalesce identical events within one request;
  the host sums them.
- An `error` with no `id` is a fatal report from the child itself. The child exits after sending it, and the host treats it as
  the child's death (A.8), carrying the message.
- `route_probe_done.results[].entry` is an RFC 6901 JSON pointer into the map (`/packages/@acme~1orders/calls/OrdersClient#get`,
  `/http/0`). `dispatched` means an `ext_call` was sent for it and answered. `error` is absent on success, else one of:
  - `{kind: mock_error | harness, message}`: the entry reached its tool and the mock or the dispatch failed;
  - a string: why the entry was not reached (it did not load, it resolved somewhere other than its swap, the call threw,
    a route's `env` host is unset, a route's host pattern has no concrete probe host).

## A.4 Semantics

1. **Inline serving.** The host reads only while it awaits a request's terminal reply. It serves each `ext_call` on that
   awaiting task, synchronously, in arrival order: it calls the dispatch-table function with `**args`, and the result goes
   back as `ext_result`. There is no background reader, because the SDK session and the turn are contextvars. One host
   request is in flight at a time; a turn may have many `ext_call`s in flight (`Promise.all`), served serially.
2. **Out-of-turn calls.** An `ext_call` with `req: null`, or with a `req` that is not in flight, is served at the host's next
   read and counted in `out_of_turn`. It is not a fault.
3. **Mock failure.** A mock body returns the real client's failure, `{'status': 'error', 'error': message}`
   (simulation-setup-python Step 8). For a tool declared `error_channel: raises`, `mock_wrap` raises it as `MockToolError`,
   which becomes `ok: false, kind: mock_error` carrying that message. The agent receives a thrown
   `PluraiMockError {message, tool}`; on an http route it receives `error_status` with body `{"error": message}`. An
   undeclared or `returns` channel arrives as `ok: true` (it is `mock_wrap`'s value). A raise inside a local mock body is a
   handler crash: the agent never gets the body's message.
4. **Harness failure in dispatch.** A `TypeError` (the args do not fit the mock signature; on a run against a deployed
   mock, only a name the mock does not take, since a key the call left out reaches the deployed handler), an unknown
   tool, or an unserializable value becomes `ok: false, kind: harness`. The agent sees a thrown `PluraiHarnessError`,
   and the request ends in `HarnessFault` naming the tool.
5. **Fail closed.** Any `event` of kind `egress_refused`, `unmapped_call` or `unsupported_shape` attributed to a request
   makes that request a `HarnessFault` naming the host or path, **even if the agent swallowed the JS error**. Events emitted
   during `ping` fault the start. `sink` events are recorded only.
   - **A late faulting event** (its `req` names a past request, as from a fire-and-forget call) faults the request that
     reads it: the host reads only while a request awaits its reply, so the next request after the late event is the one
     that fails, naming the earlier request.
   - **The post-reply read.** After a request's terminal reply, the host keeps reading for 50 ms before it returns, so a
     faulting event the agent's code raised just after its entry returned still faults that request.
   - **Teardown.** With the child gone, close and teardown read whatever no request read and report every faulting event
     attributed to a request the host sent. No request is left to fail, so each one is a sentence in
     `BridgeHost.late_faults`, the log and stderr.
6. **Trail tri-state.** `null` means "this child cannot observe the agent's tools"; `[]` means "observed none". A child never
   sends `[]` for "unknown". Entries are `{tool, args, call_id, result, status}`, with
   `status ∈ ok | error | not_dispatchable | denied`. An entry made while another call ran (a sub-agent's call inside a
   delegation call) also carries `parent_call_id`: the `call_id` of the trail entry it ran inside. `simulate.py` returns `tool_trail` **only when the trail is a list**;
   the key is absent otherwise. A `turn_done` with `trail: []` in a turn whose ledger has `ext_calls > 0` contradicts
   itself (the child served mapped calls its trail did not see): the host reads that trail as `null`, with a log line.
7. **Seed.** It is thread-scoped (create-or-reuse), so it is safe under concurrent trials. The message conversion happens in
   the child (`adapters/*`). An unconvertible message is `error{kind: seed_unsupported}`. If the entry has no `seed` export,
   `simulate.py` itself raises `HarnessFault` when `resume_seed` is set (the template always defines `seed_state`, so the SDK's
   own missing-hook refusal never fires). The `resume_point` refusal and the dropped `agent_state` keys
   (sent keys minus `restored_state_keys`) stay in Python.
8. **Ledger.** For each turn, `run_info.bridge = {ext_calls, out_of_turn, refused_egress, unmapped, sink}` (the keys are
   `constants.json` `ledger_keys`). Each event adds its `count`: `egress_refused` to `refused_egress`, `unmapped_call` and
   `unsupported_shape` to `unmapped`, `sink` to `sink`. It is kept out of `agent_state`.

## A.5 Serialization

- Child → host:
  - `Date` → ISO-8601;
  - `BigInt` → decimal string;
  - `undefined` → omitted (trailing positional args included);
  - `Buffer`/`Uint8Array` → `{"$bytes": "<base64>"}`;
  - a function argument → `unsupported_shape` unless a `local` builtin consumes it.
- Host → child: `json.dumps(..., allow_nan=False, default=…)`, with `datetime`/`date` → ISO and anything else raising, which
  becomes `kind: harness`. (JS `JSON.parse` rejects `NaN`.)
- The child revives `revive.dates` keys, recursively, into `Date`.

## A.6 Error ownership: host error mapping into the SDK's classifier

The SDK reads the owner of a raise in `invoke_agent` from its innermost Python frame. Every exception the bridge host raises
lies under the harness directory, so the **child** attributes ownership, with the same rule applied to its own stack, and the
host raises a typed class the SDK honours.

**The child's frame rule (`owner`).** On `agent_raised`, walk the thrown value's stack frames innermost first. Skip runtime
internals (`node:`, `internal/`) and every frame under a `node_modules/` directory (a library raises on its caller's behalf).
The first remaining frame decides: under `agent_root` and outside the bridge directory → `owner: agent`; anything else (a
bridge-directory file, no file, a thrown non-Error, a `Plurai*Error` the agent re-threw) → `owner: other`.

**Precedence within one request**, first match wins:

| # | child outcome | host raises | SDK reads it as |
|---|---|---|---|
| 1 | a faulting `event` (A.4.5), an `ext_result` of `kind: harness`, `entry_failed`, `seed_unsupported`, `protocol`, a `ready`/`pong` timeout, child exit mid-request, any request after a death, **any `error` during `seed`** | `plurai.HarnessFault(... + log tail)` | harness fault (`not_valid`) |
| 2 | `error{kind: agent_raised, provider: {status: 401\|403\|404\|429\|5xx}}` | `BridgeProviderError(message)` with a typed int `status_code` | `provider_infra` |
| 3 | `error{kind: agent_raised, provider: {network: true}}` | `BridgeProviderUnreachable(message)`, a `ConnectionError` | `provider_infra` |
| 4 | `error{kind: agent_raised, owner: agent}` | `plurai.AgentFault(message + JS stack)` | `agent_raised` (a failed trial the agent owns) |
| 5 | `error{kind: agent_raised, owner: other}` | `BridgeAgentError(message + JS stack)`, a plain `Exception` | `other` (errored) |

- The child sets `provider` when a request to a **provider host** (`constants.json` `provider_hosts`) inside the same
  `req` failed (an HTTP status in `provider_statuses` or ≥ 500, or a network error) and the entry then threw. A failure
  to any other passthrough host never sets it. A later 2xx from the same provider host within the same `req` clears that
  host's failure (a retry that succeeded); `provider` carries the last failure still standing.
- The host raises **outside any `except` block**: the SDK's provider check walks `__context__`, and an unrelated exception
  there would retype the turn.
- **On an SDK that does not export `AgentFault`**, row 4 raises `BridgeAgentError` instead and reads as `other`. The harness
  looks the class up as `getattr(plurai, 'AgentFault', None)`; it never requires it.

**`plurai.AgentFault`.** The interface:

```python
class AgentFault(Exception):  # plurai._protocol; exported as plurai.AgentFault, in __all__ and the README stability contract
    """Raised by a harness ON THE AGENT'S BEHALF: the agent's own code raised where no Python frame can show it
    (a bridge child in another runtime), and the harness has already attributed ownership to the agent."""

    def __init__(self, *args: object) -> None: ...  # same constructor shape as HarnessFault; records nothing
```

- `child._invoke_raise_kind`: immediately after the `_provider_unavailable(exc)` check, `isinstance(exc, AgentFault)` →
  `AGENT_RAISED_KIND`.
- Read on the raised exception itself, never on its chain: a `HarnessFault` raised from an `AgentFault` stays the harness's.
- It notes no harness fault, so a `HarnessFault` constructed earlier in the same turn still wins (`_ledgered_harness_fault`
  runs first).
- Read only in `invoke_agent`. Init, `seed_state`, `reset` and `invoke_pipeline` are unchanged.

## A.7 Egress (child)

Every outbound request goes to the **first** match:

1. an `http` route (method, host, path params, `match`) → `ext_call`, answered as a JSON `Response`: the mock's value
   with `status` (default 200); on `mock_error`, `{"error": message}` with `error_status` (default 502); on a harness
   failure, 500 (the host faults the request, A.4.4);
2. passthrough, which is the union of:
   - the **provider table** (`constants.json` `provider_hosts`);
   - the map's `passthrough_hosts` (host or env rows);
   - the config's `egress.allow_hosts` (`PLURAI_BRIDGE_ALLOW_HOSTS`);
3. sink, which is the union of the **sink table** (`sink_hosts`), the map's `sink_hosts`, and the endpoint of each
   `sink_env` variable that is set → `202 {}`, plus a `sink` event. A `sink_env` endpoint (a URL or a bare `host[:port]`)
   matches its host **and** its port (the scheme's default when it names none: 80 for http, 443 for https), and, when it
   has a path, only requests under that path prefix. A loopback collector therefore never swallows the agent's other
   local ports;
4. everything else → `Response.error()`, plus an `egress_refused` event.

An exception while the child handles an outbound request (matching, body parsing, binding the route args, answering)
answers 500 and emits an `egress_refused` event for that host and path, counted under `refused_egress` (A.4.8): the
request was served by nothing, so it faults like a refusal (A.4.5).

**Host patterns.** Matching is on the hostname only: the port is ignored, a trailing dot is stripped, and case is
ignored. A `*` matches one or more characters inside a single label, never a dot (`bedrock-runtime.*.amazonaws.com`,
`*-aiplatform.googleapis.com`). A leading `*.` needs at least one extra label and never matches the apex
(`*.openai.azure.com` matches `x.openai.azure.com`, never `openai.azure.com`). A pattern with no literal label (`*`,
`*.*`) is refused by the map validator as too broad. The Python validator and the Node child assert one shared table of
cases, so the two never disagree. An `env` row whose variable is unset or empty is inert.

**Route args.** A route's explicit `params` name each kwarg's source. Without `params`, the kwargs are the path params,
then the query, then the top-level JSON body keys, and the earlier source wins a clash (path > query > body). The map
validator reports a clash it can see as a map error; at run time a clash is never an error.

Coverage:
- **Intercepted:** `fetch` / undici-global, `http` and `https` (`@mswjs/interceptors`).
- **Socket guard:** `net.Socket.prototype.connect` allows passthrough hosts only; `tls.connect` builds on `net.Socket`,
  so it is covered there. A unix-socket path is always refused. Routes and sinks never reach a socket.
- **Not covered, stated:** `child_process` (governed by host-effects consent), `dgram`, native addons. A worker thread
  never runs unguarded: it is refused (A.9.8).

## A.8 Lifecycle

- **Start.** `ready` must arrive in ≤ `PLURAI_BRIDGE_READY_TIMEOUT` (30 s). Then `ping` → `pong` in
  ≤ `PLURAI_BRIDGE_START_TIMEOUT` (90 s); the entry's `load()` runs here. `route_probe` gets 60 s.
- **No host-side turn or seed timeout.** The SDK's `agent_turn_timeout` owns that wall; a second clock would race it.
- **The host races each read against child exit.** It polls `proc.poll()` (every 0.2 s); EOF alone is not trusted, because a
  grandchild that inherited the socket keeps it open after the child dies.
- **No respawn.** A dead child makes every later request in the process a `HarnessFault` naming the first death. A respawn
  would silently reset the agent's memory and in-memory stores.
- **Teardown** happens at `atexit` and is synchronous, with a total budget of ≤ 2.5 s (the SDK gives the process 5 s after
  shutdown): `shutdown` → 1 s → `killpg(SIGTERM)` → 1 s → `killpg(SIGKILL)`.
- **Cancellation.** A `CancelledError` while a request or the start is in flight marks the child dead ("a `<kind>` was
  cancelled mid-flight") and re-raises: the channel may hold a half-served exchange, so nothing reuses it.
- **The child exits on socket EOF** with `process.kill(-process.pid, 'SIGKILL')`, which takes its group and the agent's
  grandchildren with it.
- **Parent-death watchdog.** The child dies when its host dies, even while the agent blocks the event loop. At boot,
  before egress is armed, the child starts one internal worker thread with its own event loop, started with
  `execArgv: []` so the bridge preload never loads in it (exempt from A.9.8). Every `exit_poll` interval it compares
  `process.ppid` with the host pid it was started under; once the child is re-parented, it removes the child's scratch
  dir and sends `SIGKILL` to the child's process group, which takes the agent's grandchildren too. A watchdog that
  cannot start is a fatal boot error. A child already orphaned at boot (`ppid ≤ 1`) starts none.
- **Not covered, stated:** a grandchild the agent starts `detached` leaves the group, so neither the group kill nor
  teardown reaches it.
- **Log:** `plurai_sim/artifacts/bridge/<thread_id>.<pid>.log`. Each fault carries its last 60 lines.
- **Run-time state lives only under `plurai_sim/artifacts/bridge/`** (`constants.json` `log_dir`): a per-run or per-scenario
  file the bridge writes (a seed's id map, a cache) never goes under `bridge/`, which is harness and fingerprinted.
- **Parallel children.** One child per scenario process, no fixed port, `mkdtemp` for scratch space. A port the agent
  itself listens on (its default too) is handed to each child its own through its config or env (port 0, or a free
  loopback port the child picks), and a store account the bridge seeds is keyed per conversation, never a constant.
  `entry.mjs` imports the agent's *construction* module, never its server or main module.

## A.9 Loader (Node child)

1. One preload: `node --import <runtime>/register.mjs <runtime>/child.mjs`. `register.mjs` first imports the **bridge's
   pinned tsx by absolute path**, never `--import tsx` (with cwd at the agent root, that resolves the agent's own tsx or
   none). It then calls `module.registerHooks(ours)`. Ours is outermost either way. tsx runs synchronous hooks on a current
   Node and async `module.register` on the floor (22.15); both are supported.
2. **Module swaps pre-match** on candidates computed from `parentURL` + specifier: exact, `.ts .tsx .mts .cts .js .mjs .cjs`
   and `/index.*` variants. This happens before `next()`, so a relative swap never depends on which tsx mode is active. They
   also **post-match** on `next()`'s result, for tsconfig `paths` aliases.
3. **Package swaps** match the bare specifier (and declared subpaths) before resolution, **for every importer** outside
   the bridge runtime itself (its files, its `node_modules`, the generated modules), framework internals included. An
   unmapped call from a framework importer is still fail-closed, and its `site` names the importer.
4. **Bare imports inside `entry.mjs` and `replace/*.mjs` resolve as if imported from `agent_root`** (parentURL rewrite), so a
   monorepo layout with `plurai_sim/` outside the agent package still finds the agent's dependencies.
5. Generated modules: ESM gets named exports per declared export plus `default`. CJS gets `module.exports = default`-object
   with named members and `__esModule: true`. When `default` is an `object`, its members are the other declared exports
   (`import pg from 'pg'; pg.Pool`).
6. Virtual probe parent: `plurai-bridge:probe?parent=<file URL>&spec=<specifier>` (the resolve hook rewrites `parentURL`, so no
   file is written into the agent tree). Generated modules are files in the child's scratch dir; only a resolve hook is
   registered. A synchronous `load` hook breaks CommonJS under async-mode tsx on the floor.
7. **The CommonJS fallback.** A CommonJS module that async-mode tsx loads through the ESM translator resolves its
   `require`s with `Module._resolveFilename` directly, never reaching the registered hooks. `register.mjs` therefore
   calls the loader's `installCjsFallback()` once, after tsx: it wraps `Module._resolveFilename` with the same package
   and module swap table, under the same importer exemption.
8. **`worker_threads` fails loudly.** Hooks and the egress guard are per thread, so a worker would run the agent's code
   unswapped and unguarded. Two lines refuse it:
   - the child replaces `worker_threads.Worker` before `ready`. A construction from anywhere outside the runtime's own
     files emits `unsupported_shape` with `shape: worker_threads` and throws, so the request faults (A.4.5) even when the
     agent swallows the throw. This also catches `eval` workers, which never load `--import` preloads;
   - `register.mjs` throws in any worker thread (`!isMainThread`) that inherits the preload, naming `worker_threads` as
     unsupported, so that worker dies visibly.

   The runtime's own watchdog (A.8) is constructed with the real `Worker` and `execArgv: []`, so neither line applies.
9. Not covered, stated: Yarn PnP (refused as `yarn_pnp`).

## A.10 Paths

- Plugin source, under the `simulation-setup-bridge` skill: `references/node/runtime/{register,child,pipe,loader,proxy,egress}.mjs`,
  `adapters/ai_sdk.mjs`, `constants.json`, `package.json`, `package-lock.json`; the host at
  `references/host/bridge_host.py`.
- Workdir copies (verbatim, registered for the stale-copy check): `plurai_sim/bridge/runtime/**` (never `node_modules/`) and
  `plurai_sim/bridge/host.py`. `simulate.py` loads the host by file path
  (`spec_from_file_location('_plurai_bridge_host', …)`), never as `import bridge.host`.
- Model-written: `plurai_sim/bridge/{bridge.map.json, entry.mjs, replace/*.mjs}`.
- Install: `npm ci --prefix plurai_sim/bridge/runtime --ignore-scripts --no-audit --no-fund`. npm's cache in `~/.npm` is a
  host write outside the workdir, and the customer repo's `.npmrc` does not apply under `--prefix`.

## A.11 Proxy (normative)

- **Target and nodes.** The target is `function(){}.bind(null)`: a bound function has `[[Construct]]` (an arrow does
  not) and no own `prototype`, so `ownKeys → []` keeps the Proxy invariants. Only string keys accumulate a path.
  `then` returns `undefined` on non-call nodes. Apply on a `ctor` export also constructs (the factory form).
- **Mapped call.** It sends an `ext_call` and returns a native Promise, wrapped so that:
  - a non-Promise member access → `unsupported_shape` (`builder_chain`);
  - `Symbol.asyncIterator` → `unsupported_shape` (`async_iteration`).
- **Unmapped call or primitive coercion.** It throws `PluraiUnmappedCall: <specifier> <path>` and emits `unmapped_call`.
- **`instanceof`** against a proxy is `false`, so vendor error classes are never reproduced. `class X extends <proxy>`
  (`newTarget !== proxy`) → `unsupported_shape` (`subclass`).
- `util.inspect` / `toJSON` → `"[plurai bridge proxy <path>]"`. `ownKeys` → `[]`. `default` and `__esModule` are resolved
  by the generated module and never accumulate into a path. A synchronous member is `local` only (a mapped call is always
  async). Streams are refused.
- **`local` builtins:** `{const}`, `{self}`, `{await_all}`, `{callback_self: i}`, `{impl: "replace/x.mjs#name"}`.
- **Refused by name:** `builder_chain`, `raw_sql_driver`, `command_dispatch`, `sync_client_call`, `async_iteration`,
  `subclass`, `worker_threads`, `multi_instance`, `yarn_pnp`.
  - `raw_sql_driver` (`pg`, `mysql2`, `postgres`, `better-sqlite3`, …): one call path serves every operation, so the path
    cannot name the tool. Map the agent's own data-access module instead; with none, the bench is refused.
  - `command_dispatch` (`@aws-sdk/*` `send(new XCommand())`) and `builder_chain` clients that speak HTTP (supabase-js and
    other PostgREST clients): map them at the HTTP seam, with a `match` on the operation (`header.X-Amz-Target`, a path, a
    table).
  - A `packages` entry for a package listed under `refused_packages` in `constants.json` is a map error.

## A.12 The entry module (`bridge/entry.mjs`, Node)

The per-agent entry is model-written and imports the agent's construction module. The child calls it; nothing else does.

| export | required | called with | returns |
|---|---|---|---|
| `load()` | yes | — (during `ping`) | anything; a throw is `entry_failed` |
| `turn(input)` | yes | `{text, parts, thread_id, turn_index, request}` | `{text, trail: [...] \| null, trail_source?: <adapter id> \| null, finish_reason, input_context?, appended?}` |
| `seed(input)` | no | `{thread_id, messages, agent_state, request}` | `{saved, restored_state_keys: [...]}`; for an unconvertible message, throw an `Error` whose `code` is `'PLURAI_SEED_UNSUPPORTED'` (→ `seed_unsupported`) |

- **`saved`** is the number of messages the seed put into the agent's memory for the thread, read back from that memory
  where it can be. A zero or absent `saved` means the conversation never reached the agent, and `seed_state` raises
  `HarnessFault`.
- **No `seed` export ⇔ the map's `entry.resume` is `{"unsupported": "<reason>"}`.** It is declared, never discovered.
- **`turn()` and `seed()` derive every key the agent's memory is addressed by (thread, resource, user, session) from the
  same inputs**, so a resumed thread is created under the key its turns will send. `seed`'s `request` is `turn`'s.
- An entry never returns `trail: []` when it cannot observe the agent's tools (A.4.6).
