// The bridge child's egress guard (bridge contract A.7).
//
// child.mjs imports `armEgress` and nothing else from this file, calls it exactly once after the map and constants are
// loaded and BEFORE the entry module is imported, and talks to it only through the arguments and the returned object
// below. `hostMatcher` is exported for the shared host-pattern table test only.

import net from 'node:net';
import { constants as osConstants } from 'node:os';
import { AsyncLocalStorage } from 'node:async_hooks';
import { Interceptor } from '@mswjs/interceptors';
import { HttpRequestInterceptor } from '@mswjs/interceptors/http';
import { SocketInterceptor } from '@mswjs/interceptors/net';

/**
 * @typedef {{kind: 'egress_refused' | 'sink', req: string | null, host: string, path?: string, via: 'http' | 'socket', site: string | null, count: number}} EgressEvent
 *   The `event` message body minus `type` (contract A.3). child.mjs adds `type: 'event'` and writes it.
 *
 * @typedef {{ok: true, value: any} | {ok: false, error: {kind: 'mock_error' | 'harness', message: string}}} ExtResult
 *   The `ext_result` body (contract A.3), as resolved by `extCall`.
 *
 * @typedef {{host: string, status?: number, network?: true}} ProviderFailure
 *   The `provider` field of an `agent_raised` error (contract A.6).
 */

/**
 * Arm the guard: install the fetch/undici + http/https interceptors (`@mswjs/interceptors`) and the socket guard
 * (`net.Socket.prototype.connect`, `tls.connect`). Called once per child.
 *
 * Matching order, first match wins (contract A.7): `map.http` route -> passthrough -> sink -> refused.
 * - passthrough = `constants.provider_hosts` ∪ `map.passthrough_hosts` ∪ `allowHosts`;
 * - sink = `constants.sink_hosts` ∪ `map.sink_hosts` ∪ the endpoint of each set `constants.sink_env` var. An endpoint
 *   matches its own host AND port (scheme default when absent), and its path prefix when it has one, so a loopback
 *   collector never swallows the agent's other local ports;
 * - `env` rows (routes and host rows) are resolved from `env` once, at arm time; unset or empty -> inert.
 * A route answers with `extCall(route.tool, args, {via: 'http', path: route.path, site})`: `ok` -> JSON `Response` with
 * `route.status ?? 200`; `mock_error` -> `route.error_status ?? 502` with `{"error": message}`; `harness` -> a 500 (the host
 * faults the request; contract A.4.4). A refusal -> `Response.error()` (fetch/http) or a connect error
 * (socket) plus `emit({kind: 'egress_refused', ...})`. A sink -> `202 {}` plus `emit({kind: 'sink', ...})`. An exception
 * while handling a request (matching, body parsing, argument binding, answering) -> a 500 plus a faulting
 * `egress_refused` event for that host and path (the request was served by nothing), and the stack on stderr.
 *
 * @param {object} o
 * @param {object} o.map               the parsed bridge.map.json (already checked by the child for `schema` + `child`)
 * @param {object} o.constants         the parsed runtime/constants.json
 * @param {string[]} o.allowHosts      the config's egress.allow_hosts (parsed from PLURAI_BRIDGE_ALLOW_HOSTS by child.mjs)
 * @param {Record<string, string | undefined>} o.env  a snapshot of process.env taken after PLURAI_BRIDGE_* were deleted
 * @param {(tool: string, args: object, meta: {via: 'http', path: string, site: string | null}) => Promise<ExtResult>} o.extCall
 *   sends an `ext_call` stamped with the current `req` and resolves with its `ext_result` (owned by pipe.mjs)
 * @param {(event: EgressEvent) => void} o.emit       sends an `event` message (owned by pipe.mjs)
 * @param {() => string | null} o.currentReq          the in-flight host request id, from the AsyncLocalStorage
 * @param {(error?: Error) => string | null} o.siteOf the first agent-source frame `<agent-relative file>:<line>` of the
 *   given (or a fresh) stack, or null (owned by child.mjs; the same rule the proxy uses)
 * @returns {{
 *   probe(): Promise<{fetch: 'refused' | 'open', socket: 'refused' | 'open'}>,
 *   providerFailure(req: string): ProviderFailure | null,
 * }}
 *   `probe()` issues a `fetch` and a `net.connect` to `constants.probe_host` and reports whether each was refused; it
 *   emits no `egress_refused` event for the probe host. `providerFailure(req)` is the LAST standing failed request to a
 *   `constants.provider_hosts` host (HTTP status in `constants.provider_statuses` or >= 500, or a network error) made
 *   while `req` was in flight, or null. A later 2xx from the same host within `req` clears that host's failure (a retry
 *   that succeeded). child.mjs reads it when the entry throws, to set `error.provider`.
 */
export function armEgress(o) {
  const { map, constants, allowHosts, env, extCall, emit, currentReq, siteOf } = o;
  const probeHost = normHost(constants.probe_host);
  const providerStatuses = new Set(constants.provider_statuses);
  const isProvider = hostSet(constants.provider_hosts);

  // Every table and per-agent row is compiled once, here; `env` rows resolve now or never.
  const routes = (map.http ?? []).map((route) => compileRoute(route, env)).filter(Boolean);
  const passthrough = hostSet([
    ...constants.provider_hosts,
    ...hostRows(map.passthrough_hosts, env),
    ...(allowHosts ?? []),
  ]);
  const sinkHost = hostSet([...constants.sink_hosts, ...hostRows(map.sink_hosts, env)]);
  const sinkEndpoints = constants.sink_env.map((name) => endpointOfEnv(env, name)).filter(Boolean);
  const sink = (host, url) => sinkHost(host) || sinkEndpoints.some((endpoint) => endpointMatches(endpoint, host, url));

  // Where an outbound call came from: the agent's synchronous stack at fetch() / socket.connect(), and the host request
  // in flight then. A connection's HTTP exchange runs later on library frames, so the origin rides on the client socket
  // itself (the library hands its listeners a Proxy of that socket, which reads the property through).
  const fetchOrigin = new AsyncLocalStorage();
  const failures = new Map();
  const pendingPassthrough = new Map();
  const probeHits = { fetch: 0, socket: 0 };

  // The HTTP layer's own connection listener must run first (it watches the client's first write); ours runs after it,
  // still before any passthrough (the library schedules those for a later tick), but possibly after that connection's
  // first `request` event. So whatever the request listener needs is recorded at connect() time, on the origin.
  const http = new HttpRequestInterceptor();
  http.apply();
  const sockets = Interceptor.singleton(SocketInterceptor);

  const libConnect = net.Socket.prototype.connect;
  net.Socket.prototype.connect = function connect(...args) {
    if (!Object.hasOwn(this, kOrigin)) {
      const origin = newOrigin(fetchOrigin.getStore() ?? originError(), currentReq(), unixPathOf(args));
      Object.defineProperty(this, kOrigin, { value: origin });
    }
    return libConnect.apply(this, args);
  };
  const realFetch = globalThis.fetch;
  if (typeof realFetch === 'function') {
    globalThis.fetch = function fetch(...args) {
      return fetchOrigin.run(originError(), () => realFetch.apply(this, args));
    };
  }

  const site = (origin) => {
    try {
      return siteOf(fetchOrigin.getStore() ?? origin?.error) ?? null;
    } catch {
      return null;
    }
  };
  const report = (event) => {
    try {
      emit({ ...event, count: 1 });
    } catch (error) {
      diag(`emit failed: ${error?.message}`);
    }
  };
  // req -> Map(host -> failure), in recency order: only provider hosts, and a later success to a host clears it.
  const recordFailure = (req, failure) => {
    if (req == null || !isProvider(failure.host)) return;
    let byHost = failures.get(req);
    if (!byHost) failures.set(req, (byHost = new Map()));
    byHost.delete(failure.host);
    byHost.set(failure.host, failure);
  };
  const clearFailure = (req, host) => {
    if (req != null) failures.get(req)?.delete(host);
  };
  // A unix-socket path is never a passthrough host, so it is refused whichever listener sees it first; reported once.
  const reportUnix = (origin) => {
    if (origin.unixReported) return;
    origin.unixReported = true;
    report({ kind: 'egress_refused', req: origin.req, host: `unix:${origin.unixPath}`, via: 'socket', site: site(origin) });
  };

  // Socket guard. Only a passthrough host may reach the network: any other connection either gets answered at the HTTP
  // layer (route, sink, refusal) or, when the library would pass it through, is refused like a closed port.
  sockets.on('connection', ({ connectionOptions, controller }) => {
    const client = controller.socket;
    const origin = client[kOrigin] ?? newOrigin(undefined, currentReq(), connectionOptions.path || null);
    if (origin.unixPath) {
      reportUnix(origin);
      client.destroy(refusedError(`unix:${origin.unixPath}`));
      return;
    }
    const host = normHost(connectionOptions.host ?? 'localhost');
    const refuse = (silent) => {
      if (!silent) report({ kind: 'egress_refused', req: origin.req, host, via: 'socket', site: site(origin) });
      client.destroy(refusedError(host, connectionOptions.port));
    };

    if (passthrough(host)) {
      // A network failure is an error while a passthrough exchange is outstanding (or before any completed); a client
      // tearing down an idle kept-alive socket after its response is not one.
      client.once('error', () => {
        if (origin.outstanding > 0 || origin.exchanges === 0) {
          recordFailure(origin.lastReq ?? origin.req, { host, network: true });
        }
      });
      return;
    }
    controller.passthrough = () => {
      if (host === probeHost) probeHits.socket += 1;
      refuse(host === probeHost);
      const inert = new net.Socket();
      inert.destroy();
      return inert;
    };
  });

  http.on('request', async ({ request, requestId, controller, initiator }) => {
    const url = new URL(request.url);
    const host = normHost(url.hostname);
    const origin = initiator?.[kOrigin] ?? initiator?.socket?.[kOrigin];
    const req = currentReq() ?? origin?.req ?? null;
    // Never throws: a request the agent already abandoned must not read as a handler failure below.
    const respond = (response) => {
      try {
        controller.respondWith(response);
      } catch (error) {
        diag(`could not answer ${request.method} ${host}${url.pathname}: ${error?.message ?? error}`);
      }
    };
    try {
      if (origin?.unixPath) {
        reportUnix(origin);
        respond(Response.error());
        return;
      }
      if (host === probeHost) {
        probeHits.fetch += 1;
        respond(Response.error());
        return;
      }
      const hit = await matchRoute(routes, request, url, host);
      if (hit) {
        respond(await answerRoute(hit, extCall, site(origin)));
        return;
      }
      if (passthrough(host)) {
        pendingPassthrough.set(requestId, { req, host, origin });
        if (origin) {
          origin.outstanding += 1;
          origin.lastReq = req;
        }
        return;
      }
      if (sink(host, url)) {
        report({ kind: 'sink', req, host, path: url.pathname, via: 'http', site: site(origin) });
        respond(jsonResponse(constants.sink_status, {}));
        return;
      }
      report({ kind: 'egress_refused', req, host, path: url.pathname, via: 'http', site: site(origin) });
      respond(Response.error());
    } catch (error) {
      // The agent got a 500 it may swallow; the faulting event makes the request a HarnessFault anyway (A.4.5).
      diag(`request handling failed for ${request.method} ${host}${url.pathname}: ${error?.stack ?? error}`);
      report({ kind: 'egress_refused', req, host, path: url.pathname, via: 'http', site: site(origin) });
      respond(jsonResponse(500, { error: `plurai bridge: ${error?.message ?? error}` }));
    }
  });

  http.on('response', ({ response, responseType, requestId }) => {
    const pending = pendingPassthrough.get(requestId);
    if (!pending) return;
    pendingPassthrough.delete(requestId);
    if (pending.origin) {
      pending.origin.outstanding -= 1;
      pending.origin.exchanges += 1;
    }
    if (responseType !== 'original') return;
    if (providerStatuses.has(response.status) || response.status >= 500) {
      recordFailure(pending.req, { host: pending.host, status: response.status });
    } else if (response.ok) {
      clearFailure(pending.req, pending.host);
    }
  });

  return {
    async probe() {
      const before = { ...probeHits };
      try {
        await globalThis.fetch(`http://${probeHost}/`);
      } catch {
        // a refusal (or no guard at all) rejects; the hit counter tells the two apart
      }
      await new Promise((resolve) => {
        const socket = net.connect({ host: probeHost, port: 80 });
        const done = () => {
          clearTimeout(timer);
          socket.destroy();
          resolve();
        };
        const timer = setTimeout(done, 5000);
        socket.once('error', done).once('close', done).once('data', done);
        socket.resume();
      });
      return {
        fetch: probeHits.fetch > before.fetch ? 'refused' : 'open',
        socket: probeHits.socket > before.socket ? 'refused' : 'open',
      };
    },
    providerFailure(req) {
      const byHost = failures.get(req);
      return byHost?.size ? [...byHost.values()].at(-1) : null;
    },
  };
}

const kOrigin = Symbol('plurai.egress.origin');

/** `lastReq` / `outstanding` / `exchanges` track passthrough exchanges on the socket, for network-failure scoping. */
function newOrigin(error, req, unixPath) {
  return { error, req, unixPath, unixReported: false, lastReq: null, outstanding: 0, exchanges: 0 };
}

/** The unix-socket path a `Socket#connect(...)` call targets, or null (Node's own rule: a non-numeric string is a path). */
function unixPathOf(args) {
  const first = (Array.isArray(args[0]) ? args[0] : args)[0];
  if (first != null && typeof first === 'object') return first.path || null;
  return typeof first === 'string' && !/^\d+$/.test(first) ? first : null;
}

// ---- hosts -------------------------------------------------------------------------------------------------------------

/** Lower-case hostname: brackets of an IPv6 literal, a `:port` and trailing dots dropped. */
function normHost(host) {
  let h = String(host ?? '').trim().toLowerCase();
  if (h.startsWith('[')) {
    const end = h.indexOf(']');
    if (end !== -1) h = h.slice(1, end);
  } else if (h.split(':').length === 2) {
    h = h.slice(0, h.indexOf(':'));
  }
  return h.replace(/\.+$/, '');
}

/**
 * The shared host-pattern rule (the Python validator applies the same one): a `*` is one or more characters inside a
 * single label, never a dot; a leading `*.` is one or more extra labels, never the apex; case, trailing dots and ports
 * are ignored on both sides. A pattern with no literal label (`*`, `*.*`) is too broad and matches nothing.
 */
export function hostMatcher(pattern) {
  const p = normHost(pattern);
  const labels = p.split('.');
  if (!p || labels.every((label) => label.includes('*'))) return () => false;
  const anySub = labels.length > 1 && labels[0] === '*';
  const body = (anySub ? labels.slice(1) : labels).map((label) => label.split('*').map(escapeRe).join('[^.]+')).join('\\.');
  const re = new RegExp(`^${anySub ? '(?:[^.]+\\.)+' : ''}${body}$`);
  return (host) => re.test(normHost(host));
}

function hostSet(patterns) {
  const matchers = patterns.map(hostMatcher);
  return (host) => matchers.some((m) => m(host));
}

function hostOfEnv(env, name) {
  return endpointOfEnv(env, name)?.host ?? null;
}

/** An env-named endpoint (a URL, or a bare `host[:port]`): its host, its port (the scheme's default when absent) and its
 *  path prefix ('' for `/`). Null when the variable is unset, empty or names no host. */
function endpointOfEnv(env, name) {
  const value = env?.[name];
  if (!value) return null;
  for (const candidate of [value, `http://${value}`]) {
    try {
      const url = new URL(candidate);
      const host = normHost(url.hostname);
      if (host) return { host, port: portOf(url), prefix: url.pathname.replace(/\/+$/, '') };
    } catch {
      // not a URL in this spelling
    }
  }
  return null;
}

function portOf(url) {
  return url.port || { 'http:': '80', 'https:': '443' }[url.protocol] || '';
}

function endpointMatches(endpoint, host, url) {
  if (endpoint.host !== host || endpoint.port !== portOf(url)) return false;
  return !endpoint.prefix || url.pathname === endpoint.prefix || url.pathname.startsWith(`${endpoint.prefix}/`);
}

function hostRows(rows, env) {
  return (rows ?? []).map((row) => (row.host != null ? row.host : hostOfEnv(env, row.env))).filter(Boolean);
}

// ---- routes ------------------------------------------------------------------------------------------------------------

function compileRoute(route, env) {
  const host = typeof route.host === 'string' ? route.host : hostOfEnv(env, route.host.env);
  if (!host) return null;
  const names = [];
  const source = route.path
    .split('/')
    .map((segment) => {
      if (segment.startsWith(':') && segment.length > 1) {
        names.push(segment.slice(1));
        return '([^/]+)';
      }
      return escapeRe(segment);
    })
    .join('/');
  return {
    route,
    host: hostMatcher(host),
    path: new RegExp(`^${source.replace(/\/$/, '')}/?$`),
    names,
  };
}

async function matchRoute(routes, request, url, host) {
  let body;
  let bodyRead = false;
  const readBody = async () => {
    if (!bodyRead) {
      bodyRead = true;
      body = await parseBody(request);
    }
    return body;
  };
  for (const compiled of routes) {
    const { route } = compiled;
    if (route.method !== '*' && route.method !== request.method.toUpperCase()) continue;
    if (!compiled.host(host)) continue;
    const m = compiled.path.exec(url.pathname);
    if (!m) continue;
    const pathParams = Object.fromEntries(compiled.names.map((name, i) => [name, safeDecode(m[i + 1])]));
    const conditions = Object.entries(route.match ?? {});
    if (conditions.some(([key]) => key.startsWith('body.'))) await readBody();
    if (!conditions.every(([key, expected]) => matches(lookup(key, { pathParams, url, request, body }), expected))) continue;
    return { compiled, pathParams, url, request, body: await readBody() };
  }
  return null;
}

function safeDecode(segment) {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

async function parseBody(request) {
  const text = await request.clone().text();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    // not JSON
  }
  if ((request.headers.get('content-type') ?? '').includes('application/x-www-form-urlencoded')) {
    return queryObject(new URLSearchParams(text));
  }
  return text;
}

function queryObject(params) {
  const out = {};
  for (const key of new Set(params.keys())) {
    const all = params.getAll(key);
    out[key] = all.length === 1 ? all[0] : all;
  }
  return out;
}

function lookup(key, { pathParams, url, request, body }) {
  if (key === 'body') return body;
  const dot = key.indexOf('.');
  const [scope, rest] = [key.slice(0, dot), key.slice(dot + 1)];
  if (scope === 'path') return pathParams[rest];
  if (scope === 'query') return queryObject(url.searchParams)[rest];
  if (scope === 'header') return request.headers.get(rest) ?? undefined;
  if (scope === 'body') {
    return rest.split('.').reduce((v, k) => (v != null && typeof v === 'object' ? v[k] : undefined), body);
  }
  return undefined;
}

function matches(value, expected) {
  if (typeof value === 'string') return value === expected;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value) === expected;
  return false;
}

/** Explicit `params`, else path params, then query, then top-level JSON body keys (earlier sources win a clash). */
function routeArgs({ compiled, pathParams, url, request, body }) {
  const { params } = compiled.route;
  if (params) {
    const args = {};
    for (const [kwarg, from] of Object.entries(params)) {
      const value = lookup(from, { pathParams, url, request, body });
      if (value !== undefined) args[kwarg] = value;
    }
    return args;
  }
  const bodyKeys = body != null && typeof body === 'object' && !Array.isArray(body) ? body : {};
  return { ...bodyKeys, ...queryObject(url.searchParams), ...pathParams };
}

/** Throws on a handler failure (the request listener faults the request); a `harness` result is the host's to fault. */
async function answerRoute(hit, extCall, site) {
  const { route } = hit.compiled;
  const result = await extCall(route.tool, routeArgs(hit), { via: 'http', path: route.path, site });
  if (result?.ok) return jsonResponse(route.status ?? 200, result.value ?? null);
  const message = result?.error?.message ?? 'plurai bridge: ext_call failed';
  if (result?.error?.kind === 'mock_error') return jsonResponse(route.error_status ?? 502, { error: message });
  return jsonResponse(500, { error: message });
}

// ---- responses and errors ----------------------------------------------------------------------------------------------

const NULL_BODY = new Set([101, 103, 204, 205, 304]);

function jsonResponse(status, value) {
  if (NULL_BODY.has(status)) return new Response(null, { status });
  return new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } });
}

function refusedError(target, port) {
  const where = port != null && port !== '' ? `${target}:${port}` : target;
  const error = new Error(`connect ECONNREFUSED ${where} (plurai bridge: egress refused)`);
  const errno = -(osConstants.errno.ECONNREFUSED ?? 111);
  return Object.assign(error, { code: 'ECONNREFUSED', errno, syscall: 'connect', address: target, port });
}

/** The caller's stack, deep enough to get past the HTTP client's own frames to the agent's. */
function originError() {
  const limit = Error.stackTraceLimit;
  Error.stackTraceLimit = Math.max(limit ?? 0, 64);
  try {
    return new Error('plurai egress origin');
  } finally {
    Error.stackTraceLimit = limit;
  }
}

function escapeRe(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function diag(message) {
  try {
    process.stderr.write(`[plurai-bridge egress] ${message}\n`);
  } catch {
    // stderr gone: nothing left to tell
  }
}
