// The bridge child's client proxy (bridge contract A.11) and the generated-module exports it backs (A.9.5).
//
// A mapped package or module facade never runs its real code: every declared export is a Proxy node that accumulates a
// call path (`<export>`, `<export>.<m>`, `<export>#<m>` for a member of an instance of a `ctor` export). Calling a path
// the map names sends an `ext_call`; a `local` path runs a builtin; anything else is refused by name, fail-closed.
//
// child.mjs installs the shared state with `installBridge` before the entry is imported; the modules loader.mjs generates
// (ESM and CJS alike) read their exports through `moduleExports`, so one export object serves both module systems.

import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import util from 'node:util';

const BRIDGE = Symbol.for('plurai.bridge');
const requireFromRuntime = createRequire(import.meta.url);
const INSPECT = util.inspect.custom;

export class PluraiMockError extends Error {
  constructor(message, tool) {
    super(message);
    this.name = 'PluraiMockError';
    this.tool = tool;
  }
}

export class PluraiHarnessError extends Error {
  constructor(message, tool) {
    super(message);
    this.name = 'PluraiHarnessError';
    this.tool = tool;
  }
}

export class PluraiUnmappedCall extends Error {
  constructor(message) {
    super(message);
    this.name = 'PluraiUnmappedCall';
  }
}

export class PluraiUnsupportedShape extends Error {
  constructor(shape, message) {
    super(message);
    this.name = 'PluraiUnsupportedShape';
    this.shape = shape;
  }
}

/** True for an error this runtime made (the A.6 owner rule reads a re-thrown one as `other`). */
export function isPluraiError(err) {
  return (
    err instanceof PluraiMockError ||
    err instanceof PluraiHarnessError ||
    err instanceof PluraiUnmappedCall ||
    err instanceof PluraiUnsupportedShape
  );
}

/**
 * @param {object} o
 * @param {(tool: string, args: object, meta: {via: string, path: string, site: string | null}) => Promise<object>} o.extCall
 * @param {(event: object) => void} o.emit       sends an `event` (child.mjs stamps `req` and `type`)
 * @param {(error?: Error) => string | null} o.siteOf
 * @param {(value: any) => any} o.toWire          the A.5 child -> host conversion (strict: throws on a function)
 * @param {(ref: string) => Function} o.impl     a preloaded `{impl: "replace/x.mjs#name"}` function
 */
export function installBridge(o) {
  globalThis[BRIDGE] = { ...o, moduleExports, requireReal, cache: new Map() };
  return globalThis[BRIDGE];
}

function bridge() {
  const b = globalThis[BRIDGE];
  if (!b) throw new Error('plurai bridge: a generated module was loaded before the bridge was installed');
  return b;
}

/**
 * The export object of one generated module.
 *
 * @param {'package' | 'module'} via
 * @param {string} key    the map key (package specifier or agent-relative module path)
 * @param {object} body   the map entry (`packages[key]` or `modules[key].proxy`)
 * @param {object | null} real  the real module namespace (ESM) or `module.exports` (CJS) when an export is `real`
 * @returns {{named: Record<string, any>, default: any, cjs: any}}
 */
export function moduleExports(via, key, body, real = null) {
  const b = bridge();
  const cacheKey = `${via}\u0000${key}\u0000${real ? 'real' : ''}`;
  if (b.cache.has(cacheKey)) return b.cache.get(cacheKey);
  const root = new Root(via, key, body);
  const named = {};
  for (const [name, kind] of Object.entries(body.exports)) {
    if (name === 'default') continue;
    named[name] = kind === 'real' ? realExport(real, name, key) : root.exportNode(name, kind);
  }
  const defaultKind = body.exports.default ?? 'object';
  let dflt;
  let cjs;
  if (defaultKind === 'real') {
    dflt = real && 'default' in real ? real.default : real;
    // CJS consumers still get the proxied members: a plain object, never the real module.exports.
    cjs = { ...(dflt && typeof dflt === 'object' ? dflt : {}), ...named, default: dflt, __esModule: true };
  } else {
    // `default` and `__esModule` are resolved here and never accumulate into a path (A.11); when the default is an
    // object its members are the other declared exports (A.9.5).
    dflt = root.exportNode('default', defaultKind, { ...named, __esModule: true });
    root.overlayDefault(dflt);
    cjs = dflt;
  }
  const out = { named, default: dflt, cjs };
  b.cache.set(cacheKey, out);
  return out;
}

/** The real module behind a `real` export, for a generated CommonJS module (an ESM one imports it). */
function requireReal(url) {
  return requireFromRuntime(url.startsWith('file:') ? fileURLToPath(url) : url);
}

function realExport(real, name, key) {
  if (!real) throw new Error(`plurai bridge: ${key} declares ${name} real but the real module was not loaded`);
  if (name in real) return real[name];
  if (real.default && typeof real.default === 'object' && name in real.default) return real.default[name];
  throw new Error(`plurai bridge: ${key} declares ${name} real but the real module has no such export`);
}

class Root {
  constructor(via, key, body) {
    this.via = via;
    this.key = key;
    this.calls = body.calls ?? {};
    this.local = body.local ?? {};
    this.dates = new Set(body.revive?.dates ?? []);
  }

  exportNode(name, kind, overlay = null) {
    return makeNode(this, { path: name, kind, exportName: name, parent: null, overlay });
  }

  overlayDefault(node) {
    nodeState.get(node).overlay.default = node;
  }

  label(path) {
    return `${this.key} ${path}`;
  }

  /** A member of `path` that the map itself names (so `call`/`apply`/`bind` stay paths when the API has them). */
  names(path) {
    return path in this.calls || path in this.local;
  }
}

const nodeState = new WeakMap();

function childPath(path, member) {
  return path.endsWith('#') ? `${path}${member}` : `${path}.${member}`;
}

function makeNode(root, { path, kind, exportName, parent, overlay }) {
  // A bound function has [[Construct]] (unlike an arrow) and no own `prototype`, so `ownKeys` -> [] keeps the Proxy
  // invariants (an unbound `function(){}` carries a non-configurable `prototype`).
  const target = function pluraiBridgeNode() {}.bind(null);
  const label = root.label(path);
  Object.defineProperty(target, INSPECT, { value: () => `[plurai bridge proxy ${label}]`, configurable: true });
  const state = { root, path, kind, exportName, parent, overlay: overlay ?? {}, children: new Map(), assigned: new Map() };
  const proxy = new Proxy(target, {
    get(_t, key) {
      if (typeof key === 'symbol') return symbolMember(state, key);
      if (Object.prototype.hasOwnProperty.call(state.overlay, key)) return state.overlay[key];
      if (state.assigned.has(key)) return state.assigned.get(key);
      if (key === 'then') return undefined; // a node is never thenable; only a mapped call's result is
      if (key === 'toJSON') return () => `[plurai bridge proxy ${label}]`;
      const next = childPath(path, key);
      if ((key === 'call' || key === 'apply' || key === 'bind') && !root.names(next)) return Function.prototype[key];
      let child = state.children.get(key);
      if (!child) {
        child = makeNode(root, { path: next, kind: 'member', exportName, parent: proxy, overlay: null });
        state.children.set(key, child);
      }
      return child;
    },
    set(_t, key, value) {
      state.assigned.set(key, value);
      return true;
    },
    deleteProperty(_t, key) {
      state.assigned.delete(key);
      return true;
    },
    has(_t, key) {
      return Object.prototype.hasOwnProperty.call(state.overlay, key) || state.assigned.has(key);
    },
    ownKeys() {
      return [];
    },
    getOwnPropertyDescriptor() {
      return undefined;
    },
    apply(_t, thisArg, args) {
      if (state.kind === 'ctor') return construct(state, proxy, args); // the factory form: `Stripe(key)`
      return callPath(state, proxy, args);
    },
    construct(_t, args, newTarget) {
      if (newTarget !== proxy) {
        throw refuse(state, 'subclass', `class extends ${label} is not supported: map the agent's own subclass module`);
      }
      if (state.kind !== 'ctor') throw unmapped(state, `new ${label}`);
      return construct(state, proxy, args);
    },
  });
  nodeState.set(proxy, state);
  return proxy;
}

function symbolMember(state, key) {
  if (key === Symbol.toPrimitive) {
    return () => {
      throw unmapped(state, state.root.label(state.path));
    };
  }
  if (key === Symbol.hasInstance) return () => false; // vendor error classes are never reproduced
  if (key === INSPECT) return () => `[plurai bridge proxy ${state.root.label(state.path)}]`;
  return undefined;
}

function construct(state, self, _args) {
  // Constructor args stay with the agent: the instance is a path root, and each `new` is its own instance.
  return makeNode(state.root, { path: `${state.exportName}#`, kind: 'instance', exportName: state.exportName, parent: self, overlay: null });
}

function callPath(state, self, args) {
  const { root, path } = state;
  const call = root.calls[path];
  if (call) return mappedCall(state, call, args);
  const local = root.local[path];
  if (local) return localCall(state, local, args);
  throw unmapped(state, root.label(path));
}

function localCall(state, local, args) {
  const receiver = state.parent;
  if ('const' in local) return local.const;
  if (local.self) return receiver;
  if (local.await_all) return Promise.all(Array.isArray(args[0]) ? args[0] : []);
  if ('callback_self' in local) {
    const fn = args[local.callback_self];
    if (typeof fn !== 'function') {
      throw unmapped(state, `${state.root.label(state.path)} (argument ${local.callback_self} is not a function)`);
    }
    return fn(receiver);
  }
  if (local.impl) return bridge().impl(local.impl).apply(receiver, args);
  throw unmapped(state, state.root.label(state.path));
}

/** Positional args -> kwargs by `params` (A.11 / map `param`): `name`, `...name` (spread), `*name` (rest, last). */
export function buildKwargs(params, args) {
  const kwargs = {};
  params.forEach((param, i) => {
    if (param.startsWith('...')) {
      const value = args[i];
      if (value && typeof value === 'object') {
        for (const [k, v] of Object.entries(value)) if (v !== undefined) kwargs[k] = v;
      }
    } else if (param.startsWith('*')) {
      const rest = args.slice(i).filter((v) => v !== undefined);
      if (rest.length) kwargs[param.slice(1)] = rest;
    } else if (args[i] !== undefined) {
      kwargs[param] = args[i];
    }
  });
  return kwargs;
}

function mappedCall(state, call, args) {
  const b = bridge();
  const { root } = state;
  const label = root.label(state.path);
  const site = b.siteOf();
  let kwargs;
  try {
    kwargs = b.toWire(buildKwargs(call.params, args));
  } catch (err) {
    throw refuse(state, undefined, `${label}: an argument cannot cross the bridge (${err.message})`);
  }
  const dates = new Set([...root.dates, ...(call.revive?.dates ?? [])]);
  const promise = b.extCall(call.tool, kwargs, { via: root.via, path: label, site }).then((res) => {
    if (res.ok) return revive(res.value, dates);
    if (res.error?.kind === 'mock_error') throw new PluraiMockError(res.error.message, call.tool);
    throw new PluraiHarnessError(res.error?.message ?? 'the harness could not serve this call', call.tool);
  });
  return wrapResult(state, promise, label);
}

const PROMISE_MEMBERS = new Set(['then', 'catch', 'finally']);

/** A native Promise whose non-Promise members are named refusals (A.11): builder chains and async iteration. */
function wrapResult(state, promise, label) {
  return new Proxy(promise, {
    get(target, key) {
      if (PROMISE_MEMBERS.has(key)) return target[key].bind(target);
      if (key === 'constructor') return Promise;
      if (key === Symbol.toStringTag) return 'Promise';
      if (key === Symbol.asyncIterator) {
        target.catch(() => {});
        throw refuse(state, 'async_iteration', `async iteration of ${label} is not supported: map the paging call itself`);
      }
      if (typeof key === 'symbol') return undefined;
      target.catch(() => {});
      throw refuse(state, 'builder_chain', `builder chain after ${label} (.${key}) is not supported: map it at the HTTP seam`);
    },
  });
}

/** Revive `revive.dates` keys, recursively, into `Date` (A.5). */
export function revive(value, dates) {
  if (!dates.size || value === null || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map((v) => revive(v, dates));
  const out = {};
  for (const [k, v] of Object.entries(value)) {
    if (dates.has(k) && typeof v === 'string' && !Number.isNaN(Date.parse(v))) out[k] = new Date(v);
    else out[k] = revive(v, dates);
  }
  return out;
}

function unmapped(state, label) {
  const b = bridge();
  b.emit({ kind: 'unmapped_call', path: label, via: state.root.via, site: b.siteOf(), count: 1 });
  return new PluraiUnmappedCall(label);
}

function refuse(state, shape, message) {
  const b = bridge();
  const event = { kind: 'unsupported_shape', path: state.root.label(state.path), via: state.root.via, site: b.siteOf(), count: 1 };
  if (shape) event.shape = shape;
  b.emit(event);
  return new PluraiUnsupportedShape(shape ?? 'argument', message);
}
