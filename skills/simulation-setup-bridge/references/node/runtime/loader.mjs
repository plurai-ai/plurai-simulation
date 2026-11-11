// The bridge child's module hooks (bridge contract A.9), registered by register.mjs with `module.registerHooks`.
//
// - Package swaps match a mapped bare specifier (or a declared subpath) BEFORE resolution, for every importer outside
//   the bridge runtime itself, and resolve to a generated module of proxies.
// - Module swaps PRE-match the candidates of a relative or absolute specifier (exact, the extension variants and
//   `/index.*`) before deferring to the next resolver, so a swap never depends on which tsx mode is active; they
//   POST-match the next resolver's result too (tsconfig `paths` aliases). A `proxy` entry resolves to a generated module;
//   a `replace` entry to the model-written `bridge/replace/*.mjs`.
// - Bare imports inside `bridge/entry.mjs` and `bridge/replace/*.mjs` resolve as if imported from `agent_root`.
// - `plurai-bridge:probe?parent=<file URL>&spec=<specifier>` resolves `spec` as if `parent` imported it (route probe).
//
// Only a `resolve` hook is registered. Generated modules are files (ESM `.mjs`, CJS `.cjs`) written into the child's own
// scratch directory, so the default loaders load them: on the Node floor tsx runs as async `module.register` hooks, and
// there any synchronous `load` hook breaks every plain-CommonJS import. A CommonJS module an async-mode tsx loads through
// the ESM translator calls `Module._resolveFilename` directly and never reaches registered hooks, so
// `installCjsFallback` applies the same table there.

import { createHash } from 'node:crypto';
import fs from 'node:fs';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const PROBE_SCHEME = 'plurai-bridge:probe?';
const EXTS = ['.ts', '.tsx', '.mts', '.cts', '.js', '.mjs', '.cjs'];

/** Every concrete file a map module key (or a specifier's resolution base) can name. */
export function fileVariants(abs) {
  const ext = path.extname(abs);
  if (EXTS.includes(ext)) {
    const stem = abs.slice(0, -ext.length);
    return [abs, ...EXTS.map((e) => stem + e)];
  }
  return [abs, ...EXTS.map((e) => abs + e), ...EXTS.map((e) => path.join(abs, 'index' + e))];
}

function isBare(spec) {
  return !(spec.startsWith('.') || spec.startsWith('/') || spec.startsWith('#') || /^[A-Za-z][A-Za-z0-9+.-]*:/.test(spec));
}

function isRequire(ctx) {
  return [...(ctx?.conditions ?? [])].includes('require');
}

function within(file, dir) {
  return file === dir || file.startsWith(dir + path.sep);
}

function filePathOf(url) {
  return typeof url === 'string' && url.startsWith('file:') ? fileURLToPath(url) : null;
}

/**
 * @param {object} o
 * @param {object} o.map           the parsed bridge.map.json
 * @param {string} o.agentRoot     real path
 * @param {string} o.bridgeDir     real path of plurai_sim/bridge
 * @param {string[]} o.runtimeDirs real paths whose importers are never swapped (the runtime and its node_modules)
 * @param {string} o.genDir        this child's scratch directory for generated modules (real path, exists)
 */
export function createLoader({ map, agentRoot, bridgeDir, runtimeDirs, genDir }) {
  const packages = map.packages ?? {};
  const modules = map.modules ?? {};
  const pkgKeyOf = new Map();
  for (const [key, body] of Object.entries(packages)) {
    pkgKeyOf.set(key, key);
    for (const sub of body.subpaths ?? []) pkgKeyOf.set(sub, key);
  }
  const modKeyOf = new Map();
  for (const key of Object.keys(modules)) for (const file of fileVariants(path.join(agentRoot, key))) modKeyOf.set(file, key);
  const agentParentURL = pathToFileURL(path.join(agentRoot, '__plurai_bridge_importer__.mjs')).href;
  const hasReal = (body) => Object.values(body.exports ?? {}).includes('real');
  const noSwap = (file) => file !== null && (within(file, genDir) || runtimeDirs.some((dir) => within(file, dir)));
  let resolvingReal = 0; // > 0 while a mapped package's REAL module is resolved (the CJS fallback stands aside)
  const written = new Set();

  /** The generated module file for one map entry, written on first use. */
  function generatedFile(via, key, { cjs, real }) {
    const body = via === 'package' ? packages[key] : modules[key].proxy;
    const tag = real ? '.' + createHash('sha256').update(real).digest('hex').slice(0, 12) : '';
    const file = path.join(genDir, via, encodeURIComponent(key) + tag + (cjs ? '.cjs' : '.mjs'));
    if (written.has(file)) return file;
    const args = `${JSON.stringify(via)}, ${JSON.stringify(key)}, ${JSON.stringify(body)}`;
    const bridge = `globalThis[Symbol.for('plurai.bridge')]`;
    let source;
    if (cjs) {
      const realArg = real ? `${bridge}.requireReal(${JSON.stringify(real)})` : 'null';
      source = `module.exports = ${bridge}.moduleExports(${args}, ${realArg}).cjs;\n`;
    } else {
      const lines = [];
      if (real) lines.push(`import * as __real from ${JSON.stringify(real)};`);
      lines.push(`const __m = ${bridge}.moduleExports(${args}, ${real ? '__real' : 'null'});`);
      lines.push('export default __m.default;');
      Object.keys(body.exports)
        .filter((name) => name !== 'default')
        .forEach((name, i) => lines.push(`const __e${i} = __m.named[${JSON.stringify(name)}]; export { __e${i} as ${name} };`));
      source = lines.join('\n') + '\n';
    }
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, source);
    written.add(file);
    return file;
  }

  const candidates = (spec, parentURL) => {
    let base;
    if (spec.startsWith('file:')) base = fileURLToPath(spec);
    else if (spec.startsWith('/')) base = spec;
    else if (spec.startsWith('.')) {
      const parent = filePathOf(parentURL);
      if (!parent) return [];
      base = path.resolve(path.dirname(parent), spec);
    } else return [];
    return fileVariants(base);
  };

  const swapFile = (key, cjs) => {
    const body = modules[key];
    return body.replace ? path.join(bridgeDir, body.replace) : generatedFile('module', key, { cjs });
  };
  const swapped = (file) => ({ url: pathToFileURL(file).href, shortCircuit: true });

  // A next-resolver result naming a mapped module file is swapped (the post-match).
  const postMatch = (result, ctx) => {
    const key = modKeyOf.get(filePathOf(result?.url));
    return key === undefined ? result : swapped(swapFile(key, isRequire(ctx)));
  };

  function resolve(spec, ctx, next) {
    if (spec.startsWith(PROBE_SCHEME)) {
      const q = new URLSearchParams(spec.slice(PROBE_SCHEME.length));
      return resolve(q.get('spec'), { ...ctx, parentURL: q.get('parent') }, next);
    }
    const parentURL = ctx.parentURL;
    const parentFile = filePathOf(parentURL);
    if (noSwap(parentFile)) return next(spec, ctx);

    if (isBare(spec)) {
      const fromBridge = parentFile !== null && within(parentFile, bridgeDir);
      const ctxForNext = fromBridge ? { ...ctx, parentURL: agentParentURL } : ctx;
      const key = pkgKeyOf.get(spec);
      if (key !== undefined) {
        let real;
        if (hasReal(packages[key])) {
          resolvingReal += 1;
          try {
            real = next(spec, ctxForNext).url;
          } finally {
            resolvingReal -= 1;
          }
        }
        return swapped(generatedFile('package', key, { cjs: isRequire(ctx), real }));
      }
      return postMatch(next(spec, ctxForNext), ctx);
    }

    for (const file of candidates(spec, parentURL)) {
      const key = modKeyOf.get(file);
      if (key !== undefined) return swapped(swapFile(key, isRequire(ctx)));
    }
    return postMatch(next(spec, ctx), ctx);
  }

  /** The CommonJS path that bypasses registered hooks (see the header). Installed once, after tsx. */
  function installCjsFallback() {
    const original = Module._resolveFilename;
    Module._resolveFilename = function pluraiBridgeResolveFilename(request, parent, isMain, options) {
      const parentFile = parent?.filename ?? null;
      if (resolvingReal || typeof request !== 'string' || !parentFile || noSwap(parentFile)) {
        return original.call(this, request, parent, isMain, options);
      }
      const key = pkgKeyOf.get(request);
      if (key !== undefined) {
        const real = hasReal(packages[key]) ? pathToFileURL(original.call(this, request, parent, isMain, options)).href : undefined;
        return generatedFile('package', key, { cjs: true, real });
      }
      if (!isBare(request)) {
        for (const file of candidates(request, pathToFileURL(parentFile).href)) {
          const modKey = modKeyOf.get(file);
          if (modKey !== undefined) return swapFile(modKey, true);
        }
      }
      const resolved = original.call(this, request, parent, isMain, options);
      const modKey = modKeyOf.get(resolved);
      return modKey === undefined ? resolved : swapFile(modKey, true);
    };
  }

  /** True when `url` is a module this loader put in place of an agent import (a generated module or a replace file). */
  function isSwapped(url) {
    const file = filePathOf(url);
    return file !== null && (within(file, genDir) || within(file, path.join(bridgeDir, 'replace')));
  }

  return { hooks: { resolve }, installCjsFallback, isSwapped };
}
