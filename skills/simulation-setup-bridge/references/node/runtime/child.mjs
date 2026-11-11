// The Node bridge child (bridge contract protocol 1). Spawned by the host as
//   node --import <runtime>/register.mjs <runtime>/child.mjs
// with cwd at the agent root and the channel on PLURAI_BRIDGE_FD.
//
// Order: a boot failure is fatal -> start the parent-death watchdog -> arm egress (ONCE, before the entry is imported) ->
// open the channel -> install the proxy bridge -> refuse worker threads -> `ready`. The entry is imported, and its
// `load()` run, on the first `ping`. One host request is in flight at a time.

import fs from 'node:fs';
import module from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import workerThreads from 'node:worker_threads';
import { boot } from './register.mjs';
import { armEgress } from './egress.mjs';
import { ProtocolError, currentReq, currentRequest, openPipe, runRequest, toWire } from './pipe.mjs';
import { PluraiUnsupportedShape, installBridge, isPluraiError } from './proxy.mjs';

const C = boot.constants;

// --- the A.6 frame rule -------------------------------------------------------------------------------------------

// `at fn (<file>:<line>:<col>)` or `at <file>:<line>:<col>`; `node:` and `<anonymous>` frames carry no file.
const FRAME = /\(?((?:file:\/\/|\/)[^()]*?):(\d+):(\d+)\)?\s*$/;

function framesOf(stack) {
  const frames = [];
  for (const line of String(stack ?? '').split('\n')) {
    if (!/^\s+at /.test(line)) continue;
    const m = FRAME.exec(line);
    let file = m ? m[1] : null;
    if (file?.startsWith('file://')) {
      try {
        file = fileURLToPath(file);
      } catch {
        file = null;
      }
    }
    frames.push({ file, line: m ? Number(m[2]) : null });
  }
  return frames;
}

const within = (file, dir) => file === dir || file.startsWith(dir + path.sep);
const isNodeModules = (file) => file.split(path.sep).includes('node_modules');
const isAgentSource = (file) => file !== null && within(file, boot.agentRoot) && !within(file, boot.bridgeDir) && !isNodeModules(file);
const siteText = (frame) => `${path.relative(boot.agentRoot, frame.file)}:${frame.line}`;

/** The first agent-source frame as `<agent-relative file>:<line>`; else the first agent-root frame (a framework importer). */
function siteOf(error) {
  let stack = error?.stack;
  if (stack === undefined) {
    const limit = Error.stackTraceLimit;
    Error.stackTraceLimit = 64;
    stack = new Error().stack;
    Error.stackTraceLimit = limit;
  }
  const frames = framesOf(stack).filter((f) => f.file !== null);
  const agent = frames.find((f) => isAgentSource(f.file));
  if (agent) return siteText(agent);
  const importer = frames.find((f) => within(f.file, boot.agentRoot) && !within(f.file, boot.bridgeDir));
  return importer ? siteText(importer) : null;
}

/** A.6: skip runtime internals and node_modules; the first remaining frame is the agent's, or it is `other`. */
function ownerOf(err) {
  if (!(err instanceof Error) || isPluraiError(err)) return 'other';
  for (const frame of framesOf(err.stack)) {
    if (frame.file === null) continue;
    if (isNodeModules(frame.file)) continue;
    return isAgentSource(frame.file) ? 'agent' : 'other';
  }
  return 'other';
}

const messageOf = (err) => (err instanceof Error ? `${err.name}: ${err.message}` : `a non-Error value was thrown: ${safeString(err)}`);

function safeString(value) {
  try {
    return typeof value === 'string' ? value : JSON.stringify(toWire(value, { strict: false }));
  } catch {
    return Object.prototype.toString.call(value);
  }
}

// --- boot ---------------------------------------------------------------------------------------------------------

function die(message) {
  process.stderr.write(`[plurai bridge] fatal: ${message}\n`);
  process.exitCode = 1;
  if (boot.fd === null) process.exit(1);
  // An `error` with no id is the child's own fatal report (A.3); write it synchronously, then exit.
  try {
    fs.writeSync(boot.fd, JSON.stringify({ type: 'error', kind: 'protocol', message }) + '\n');
  } catch {}
  process.exit(1);
}

if (boot.error) die(boot.error);

// The host's death closes the channel, and the channel's end kills the process group (below). An agent that blocks the
// event loop never sees that end, so a worker thread of ours, which keeps its own loop, watches the parent pid instead:
// once this process is re-parented, the host is gone, and it takes the group (the agent's grandchildren too). It runs
// with `execArgv: []`, so the bridge preload (which refuses every other worker) never loads in it.
const RealWorker = workerThreads.Worker;
const WATCHDOG = `
const fs = require('node:fs');
const { workerData: w } = require('node:worker_threads');
setInterval(() => {
  if (process.ppid === w.ppid) return;
  try { if (w.genDir) fs.rmSync(w.genDir, { recursive: true, force: true }); } catch {}
  try { process.kill(-w.pid, 'SIGKILL'); } catch {}
  try { process.kill(w.pid, 'SIGKILL'); } catch {}
}, w.intervalMs);
`;

function startWatchdog() {
  if (process.ppid <= 1) return;
  const workerData = { ppid: process.ppid, pid: process.pid, genDir: boot.genDir, intervalMs: C.timeouts_s.exit_poll * 1000 };
  try {
    new RealWorker(WATCHDOG, { eval: true, execArgv: [], workerData }).unref();
  } catch (err) {
    die(`the parent-death watchdog failed to start: ${err?.message ?? err}`);
  }
}

startWatchdog();

let egress = null;
let entry = null;
let busy = null;
const impls = new Map();

let pipe = null;
const extCall = (tool, args, meta) => pipe.extCall(tool, args, meta);

// Egress is armed before anything else here touches a socket. The channel below is `new net.Socket({fd})` on the
// inherited descriptor and never calls connect, so the guard has nothing of ours to intercept.
try {
  egress = armEgress({ map: boot.map, constants: C, allowHosts: boot.allowHosts, env: boot.env, extCall, emit, currentReq, siteOf });
} catch (err) {
  die(`egress guard failed to arm: ${err?.message ?? err}`);
}

pipe = openPipe({
  fd: boot.fd,
  maxLineBytes: C.max_line_bytes,
  idPrefix: C.id_prefix.child,
  onMessage: (msg) => handle(msg),
  onProtocolError: (err) => die(err.message),
  // The host is gone: take the whole process group (the agent's grandchildren too) with us.
  onEnd: () => killGroup(),
});

function removeScratch() {
  if (boot.genDir) fs.rmSync(boot.genDir, { recursive: true, force: true });
}

process.on('exit', removeScratch);

function killGroup() {
  try {
    removeScratch();
  } catch {}
  try {
    process.kill(-process.pid, 'SIGKILL');
  } catch {
    process.exit(0);
  }
}

function emit(event) {
  const req = event.req === undefined ? currentReq() : event.req;
  try {
    pipe.send({ type: 'event', ...event, req });
  } catch (err) {
    const request = currentRequest();
    if (request) request.protocolFault ??= err.message;
  }
}

installBridge({
  extCall,
  emit,
  siteOf,
  toWire: (value) => toWire(value, { strict: true }),
  impl: (ref) => {
    const fn = impls.get(ref);
    if (!fn) throw new Error(`plurai bridge: local impl ${ref} was not loaded`);
    return fn;
  },
});

// Module swaps and the egress guard are the main thread's, so an agent worker would run unswapped and unguarded. The
// constructor is refused for every importer outside the bridge runtime (ESM bindings included; the runtime's own tsx
// runs esbuild in a worker), as a named unsupported shape that faults the request even when the agent swallows the
// throw. register.mjs refuses to boot inside a worker as the second line.
const SELF = fileURLToPath(import.meta.url);
const isRuntimeFile = (file) => boot.runtimeDirs.some((dir) => within(file, dir));
workerThreads.Worker = function Worker(...args) {
  const caller = framesOf(new Error().stack).find((f) => f.file !== null && f.file !== SELF);
  if (caller && isRuntimeFile(caller.file)) return new RealWorker(...args);
  emit({ kind: 'unsupported_shape', shape: 'worker_threads', path: 'node:worker_threads Worker', via: 'package', site: siteOf(), count: 1 });
  throw new PluraiUnsupportedShape(
    'worker_threads',
    'worker_threads are unsupported in a bridged agent: module swaps and the egress guard run only on the main thread',
  );
};
module.syncBuiltinESMExports();

// The agent can exit the process; a fatal report then names what killed it when nothing of the agent's handles it.
process.on('uncaughtExceptionMonitor', (err) => {
  if (process.listenerCount('uncaughtException') > 0) return;
  try {
    const report = { type: 'error', kind: 'agent_raised', owner: ownerOf(err), message: messageOf(err), stack: err?.stack, site: siteOf(err) };
    fs.writeSync(boot.fd, JSON.stringify(report) + '\n');
  } catch {}
});

pipe.send({
  type: 'ready',
  protocol: C.protocol,
  child: 'node',
  runtime_version: process.versions.node,
  tsx_mode: boot.tsxMode,
  hooks: 'registerHooks',
});

// --- requests -----------------------------------------------------------------------------------------------------

const HANDLERS = { ping, turn, seed, route_probe: routeProbe };

function reply(id, body) {
  const request = currentRequest();
  if (request?.protocolFault) body = { type: 'error', kind: 'protocol', message: request.protocolFault };
  try {
    pipe.send({ ...body, id });
  } catch (err) {
    if (!(err instanceof ProtocolError)) throw err;
    pipe.send({ type: 'error', id, kind: 'protocol', message: err.message });
  }
}

function handle(msg) {
  if (msg.type === 'shutdown') return killGroup();
  const handler = HANDLERS[msg.type];
  const id = typeof msg.id === 'string' ? msg.id : undefined;
  if (!handler || id === undefined) {
    const body = { type: 'error', kind: 'protocol', message: `unexpected host message ${JSON.stringify(msg.type)}${id ? '' : ' without a string id'}` };
    if (id !== undefined) body.id = id;
    return pipe.send(body);
  }
  if (busy !== null) {
    return pipe.send({ type: 'error', id, kind: 'protocol', message: `${msg.type} ${id} arrived while ${busy} is in flight` });
  }
  busy = id;
  runRequest(id, async () => {
    try {
      reply(id, await handler(msg));
    } catch (err) {
      reply(id, { type: 'error', kind: 'protocol', message: `the child failed serving ${msg.type}: ${err?.stack ?? err}` });
    } finally {
      busy = null;
    }
  });
}

function entryFailed(err, what) {
  return { type: 'error', kind: 'entry_failed', message: `${what}: ${messageOf(err)}`, stack: err?.stack, site: siteOf(err) };
}

function agentRaised(id, err) {
  const body = { type: 'error', req: id, kind: 'agent_raised', owner: ownerOf(err), message: messageOf(err), site: siteOf(err) };
  if (err instanceof Error && err.stack) body.stack = err.stack;
  const provider = egress.providerFailure(id);
  if (provider) body.provider = provider;
  return body;
}

async function loadImpls() {
  const refs = new Set();
  const collect = (body) => Object.values(body?.local ?? {}).forEach((l) => l.impl && refs.add(l.impl));
  Object.values(boot.map.packages ?? {}).forEach(collect);
  Object.values(boot.map.modules ?? {}).forEach((m) => collect(m.proxy));
  for (const ref of refs) {
    const [file, name] = ref.split('#');
    const ns = await import(pathToFileURL(path.join(boot.bridgeDir, file)).href);
    if (typeof ns[name] !== 'function') throw new Error(`${file} exports no function ${name}`);
    impls.set(ref, ns[name]);
  }
}

async function ping() {
  if (!entry) {
    let ns;
    try {
      await loadImpls();
      ns = await import(pathToFileURL(boot.entryPath).href);
    } catch (err) {
      return entryFailed(err, `importing ${path.relative(boot.bridgeDir, boot.entryPath)}`);
    }
    const missing = ['load', 'turn'].filter((name) => typeof ns[name] !== 'function');
    if (missing.length) return { type: 'error', kind: 'entry_failed', message: `the entry exports no ${missing.join(', ')}()` };
    const hasSeed = typeof ns.seed === 'function';
    const declared = boot.map.entry.resume === 'seed';
    if (hasSeed !== declared) {
      const message = hasSeed
        ? 'the entry exports seed() but the map declares entry.resume unsupported'
        : 'the map declares entry.resume "seed" but the entry exports no seed()';
      return { type: 'error', kind: 'entry_failed', message };
    }
    try {
      await ns.load();
    } catch (err) {
      return entryFailed(err, 'entry load()');
    }
    entry = ns;
  }
  const count = (v) => (Array.isArray(v) ? v.length : Object.keys(v ?? {}).length);
  return {
    type: 'pong',
    entry_exports: Object.keys(entry).sort(),
    mapped: { packages: count(boot.map.packages), modules: count(boot.map.modules), http: count(boot.map.http) },
  };
}

function requireEntry(type) {
  return entry ? null : { type: 'error', kind: 'protocol', message: `${type} before a successful ping` };
}

async function turn(msg) {
  const early = requireEntry('turn');
  if (early) return early;
  let out;
  try {
    out = await entry.turn({
      text: msg.text,
      parts: msg.parts ?? null,
      thread_id: msg.thread_id,
      turn_index: msg.turn_index,
      request: msg.request ?? {},
    });
  } catch (err) {
    return agentRaised(msg.id, err);
  }
  return turnDone(out);
}

function turnDone(out) {
  const bad = (why) => ({ type: 'error', kind: 'entry_failed', message: `entry turn() returned ${why}` });
  if (!out || typeof out !== 'object') return bad(`${out === null ? 'null' : typeof out}, not {text, trail, finish_reason}`);
  // An empty string is a legal reply; a missing one is an entry that lost the agent's answer.
  if (out.text == null) return bad('no text');
  if (typeof out.text !== 'string') return bad(`a ${typeof out.text} text`);
  // Trail tri-state (A.4.6): an absent trail is null ("cannot observe"), never [].
  const trail = out.trail === undefined ? null : out.trail;
  if (trail !== null) {
    if (!Array.isArray(trail)) return bad(`a trail that is neither a list nor null`);
    const statuses = new Set(C.trail_statuses);
    const wrong = trail.findIndex((e) => !e || typeof e.tool !== 'string' || !statuses.has(e.status));
    if (wrong !== -1) return bad(`trail[${wrong}] without a string tool and a status in ${C.trail_statuses.join('|')}`);
  }
  let body;
  try {
    body = {
      type: 'turn_done',
      text: out.text,
      trail: trail === null ? null : toWire(trail, { strict: false }),
      trail_source: out.trail_source ?? null,
      finish_reason: out.finish_reason ?? null,
    };
    if (out.input_context !== undefined) body.input_context = toWire(out.input_context, { strict: false });
    if (out.appended !== undefined) body.appended = toWire(out.appended, { strict: false });
  } catch (err) {
    return bad(`a value that cannot cross the bridge (${err.message})`);
  }
  return body;
}

async function seed(msg) {
  const early = requireEntry('seed');
  if (early) return early;
  if (typeof entry.seed !== 'function') return { type: 'error', kind: 'seed_unsupported', message: 'the entry exports no seed()' };
  let out;
  try {
    out = await entry.seed({
      thread_id: msg.thread_id,
      messages: msg.messages ?? [],
      agent_state: msg.agent_state ?? null,
      request: msg.request ?? {},
    });
  } catch (err) {
    if (err?.code === 'PLURAI_SEED_UNSUPPORTED') return { type: 'error', kind: 'seed_unsupported', message: messageOf(err) };
    return agentRaised(msg.id, err);
  }
  if (!out || typeof out !== 'object') return { type: 'error', kind: 'entry_failed', message: 'entry seed() returned no {saved, restored_state_keys}' };
  const keys = out.restored_state_keys ?? [];
  if (!Array.isArray(keys) || !keys.every((k) => typeof k === 'string')) {
    return { type: 'error', kind: 'entry_failed', message: 'entry seed() returned restored_state_keys that is not a list of strings' };
  }
  return { type: 'seed_done', saved: toWire(out.saved ?? null, { strict: false }), restored_state_keys: keys };
}

// --- route probe (model-free: every map entry, no entry import) ---------------------------------------------------

const pointer = (...parts) => '/' + parts.map((p) => String(p).replaceAll('~', '~0').replaceAll('/', '~1')).join('/');

function probeArgs(call) {
  const given = call.probe_args ?? {};
  const args = [];
  for (const param of call.params) {
    const name = param.replace(/^(\.\.\.|\*)/, '');
    const value = given[name] ?? null;
    if (param.startsWith('*')) {
      if (Array.isArray(value)) args.push(...value);
    } else args.push(value);
  }
  return args;
}

function probeResolve(parentFile, spec) {
  return import.meta.resolve(`plurai-bridge:probe?${new URLSearchParams({ parent: pathToFileURL(parentFile).href, spec })}`);
}

async function probeImport(parentFile, spec) {
  const url = probeResolve(parentFile, spec);
  return { url, ns: await import(url) };
}

function walkPath(ns, callPath) {
  const [exportPart, ...rest] = callPath.split('#');
  const [exportName, ...statics] = exportPart.split('.');
  let target = exportName === 'default' ? ns.default : ns[exportName];
  for (const m of statics) target = target[m];
  if (rest.length) {
    target = new target();
    for (const m of rest[0].split('.')) target = target[m];
  }
  return target;
}

async function probeCall(result, ns, callPath, call) {
  const request = currentRequest();
  const before = request.extCalls.length;
  try {
    await walkPath(ns, callPath)(...probeArgs(call));
  } catch (err) {
    result.error = err?.name === 'PluraiMockError' ? { kind: 'mock_error', message: err.message } : messageOf(err);
  }
  const sent = request.extCalls.slice(before).filter((c) => c.tool === call.tool);
  result.dispatched = sent.length > 0 && sent.every((c) => c.answered);
  if (result.error && typeof result.error !== 'string' && result.error.kind === 'mock_error') return;
  const failed = sent.find((c) => c.answered && !c.ok && c.error?.kind === 'harness');
  if (failed) result.error = { kind: 'harness', message: failed.error.message };
}

async function probeProxyBody(results, base, parentFile, spec, body, kind) {
  let loaded;
  try {
    loaded = await probeImport(parentFile, spec);
  } catch (err) {
    for (const p of Object.keys(body.calls ?? {})) {
      results.push({ entry: pointer(...base, 'calls', p), kind, swapped: false, path: p, tool: body.calls[p].tool, dispatched: false, error: messageOf(err) });
    }
    return;
  }
  const swapped = boot.loader.isSwapped(loaded.url);
  for (const [p, call] of Object.entries(body.calls ?? {})) {
    const result = { entry: pointer(...base, 'calls', p), kind, swapped, path: p, tool: call.tool, dispatched: false };
    if (swapped) await probeCall(result, loaded.ns, p, call);
    else result.error = `resolved to ${loaded.url}, not the bridge`;
    results.push(result);
  }
}

function evidenceFile(evidence) {
  return path.join(boot.agentRoot, String(evidence).replace(/:\d+$/, ''));
}

/** A concrete label for each `*` of a host pattern: a host the route matches and nothing else names. */
const PROBE_LABEL = 'plurai-probe';

/** The host an http route is probed at, or why it cannot be probed (an env row the egress guard leaves inert too). */
function probeHostOf(route) {
  if (typeof route.host === 'string') return { host: route.host.replaceAll('*', PROBE_LABEL) };
  const name = route.host.env;
  const value = boot.env[name];
  if (!value) return { error: `env ${name} is unset or empty, so this route has no host and is inert: set ${name} as production does, then verify again` };
  for (const candidate of [value, `http://${value}`]) {
    try {
      const { hostname } = new URL(candidate);
      if (hostname) return { host: hostname };
    } catch {
      // not a URL in this spelling
    }
  }
  return { error: `env ${name}=${JSON.stringify(value)} names no host, so this route is inert` };
}

function setPath(target, keys, value) {
  let node = target;
  for (const key of keys.slice(0, -1)) {
    if (node[key] === null || typeof node[key] !== 'object') node[key] = {};
    node = node[key];
  }
  node[keys.at(-1)] = value;
}

async function probeHttp(results, route, i) {
  const result = { entry: pointer('http', i), kind: 'http', swapped: false, path: route.path, tool: route.tool, dispatched: false };
  const match = Object.entries(route.match ?? {});
  const method = route.method === '*' ? 'POST' : route.method;
  const bodyKeys = match.map(([k]) => k).filter((k) => k.startsWith('body.'));
  const { host, error } = probeHostOf(route);
  const unprobeable =
    error ??
    (method === 'GET' && bodyKeys.length
      ? `a GET carries no request body, so no request can satisfy match ${bodyKeys.join(', ')}: match on query or header instead`
      : null);
  if (unprobeable) {
    results.push({ ...result, error: unprobeable });
    return;
  }
  const url = new URL(`https://${host}`);
  url.pathname = route.path
    .split('/')
    .map((segment) => (segment.startsWith(':') && segment.length > 1 ? 'probe' : segment))
    .join('/');
  const headers = {};
  const body = {};
  for (const [k, v] of match) {
    const [where, ...rest] = k.split('.');
    if (where === 'query') url.searchParams.set(rest.join('.'), v);
    else if (where === 'header') headers[rest.join('.')] = v;
    else setPath(body, rest, v);
  }
  const init = { method, headers };
  if (method !== 'GET') {
    init.body = JSON.stringify(body);
    headers['content-type'] ??= 'application/json';
  }
  const request = currentRequest();
  const before = request.extCalls.length;
  try {
    await (await fetch(url, init)).arrayBuffer();
  } catch (err) {
    result.error = messageOf(err);
  }
  const sent = request.extCalls.slice(before).filter((c) => c.tool === route.tool);
  result.swapped = sent.length > 0;
  result.dispatched = sent.length > 0 && sent.every((c) => c.answered);
  const failed = sent.find((c) => c.answered && !c.ok);
  if (failed) result.error = { kind: failed.error?.kind ?? 'harness', message: failed.error?.message ?? '' };
  results.push(result);
}

async function routeProbe() {
  const results = [];
  try {
    await loadImpls();
  } catch (err) {
    process.stderr.write(`[plurai bridge] route probe: a local impl failed to load: ${messageOf(err)}\n`);
  }
  for (const [spec, body] of Object.entries(boot.map.packages ?? {})) {
    await probeProxyBody(results, ['packages', spec], evidenceFile(body.evidence), spec, body, 'package');
  }
  for (const [key, body] of Object.entries(boot.map.modules ?? {})) {
    const file = path.join(boot.agentRoot, key);
    if (body.proxy) {
      await probeProxyBody(results, ['modules', key, 'proxy'], evidenceFile(body.evidence), pathToFileURL(file).href, body.proxy, 'module');
      continue;
    }
    const result = { entry: pointer('modules', key), kind: 'replace', swapped: false, path: key, dispatched: false };
    let url = null;
    try {
      url = probeResolve(evidenceFile(body.evidence), pathToFileURL(file).href);
    } catch (err) {
      result.error = `${key} did not resolve: ${messageOf(err)}`;
    }
    if (url !== null) {
      result.swapped = url === pathToFileURL(path.join(boot.bridgeDir, body.replace)).href;
      if (!result.swapped) result.error = `resolved to ${url}, not ${body.replace}`;
      else {
        try {
          await import(url);
        } catch (err) {
          result.error = `${body.replace} did not load: ${messageOf(err)}`;
        }
      }
    }
    results.push(result);
  }
  for (const [i, route] of (boot.map.http ?? []).entries()) await probeHttp(results, route, i);
  return { type: 'route_probe_done', results, egress_guard: await egress.probe() };
}
