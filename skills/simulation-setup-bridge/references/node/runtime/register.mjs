// The bridge child's one preload (bridge contract A.1, A.9): `node --import <runtime>/register.mjs <runtime>/child.mjs`.
//
// 1. Read every PLURAI_BRIDGE_* variable, then delete them all from process.env (NODE_OPTIONS is never touched).
// 2. Read the map (only `schema` + `child` are checked here; bridge_map.py validates the rest before any spawn).
// 3. Import the bridge's OWN pinned tsx by absolute path, never `--import tsx`: with cwd at the agent root that would
//    resolve the agent's tsx, or none. tsx picks synchronous hooks on a current Node and async `module.register` on the
//    floor; which one it took is recorded as `tsxMode` (reported in `ready.tsx_mode`).
// 4. `module.registerHooks(ours)`: registered last, so ours run outermost in either tsx mode. Generated modules go to a
//    scratch directory of this child's own (`mkdtemp`), never into the agent tree.
//
// In the main thread nothing here throws: a failure is recorded in `boot.error` (hooks registered before it stay
// registered, unused), and child.mjs reports it as a fatal `error` and exits before anything of the agent's is imported.
//
// In a worker thread (the preload is inherited through `execArgv`) it throws: module hooks are per thread and the channel
// is the main thread's, so a worker would run the agent's code unswapped and unguarded. Throwing kills that worker
// visibly instead. The child's own watchdog worker is started with `execArgv: []`, so it never loads this file.

import fs from 'node:fs';
import os from 'node:os';
import module, { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { isMainThread } from 'node:worker_threads';
import { createLoader } from './loader.mjs';

if (!isMainThread) {
  throw new Error(
    'plurai bridge: worker_threads are unsupported in a bridged agent: module swaps and the egress guard run only on the ' +
      'main thread, so this worker would reach the real clients. Run the work on the main thread, or keep the module ' +
      'that starts the worker out of the entry import graph.',
  );
}

const RUNTIME_DIR = fs.realpathSync(path.dirname(fileURLToPath(import.meta.url)));

export const boot = {
  runtimeDir: RUNTIME_DIR,
  runtimeDirs: [RUNTIME_DIR],
  constants: null,
  fd: null,
  map: null,
  mapPath: null,
  allowHosts: [],
  env: {},
  agentRoot: null,
  bridgeDir: null,
  entryPath: null,
  tsxMode: null,
  genDir: null,
  loader: null,
  error: null,
};

function readEnv(constants) {
  const names = constants.env;
  const raw = {};
  for (const key of Object.keys(process.env)) {
    if (key.startsWith(constants.child_env_prefix)) {
      raw[key] = process.env[key];
      delete process.env[key];
    }
  }
  boot.env = { ...process.env };

  const fd = Number.parseInt(raw[names.fd] ?? '', 10);
  if (!Number.isInteger(fd) || fd < 0) throw new Error(`${names.fd} is not a file descriptor (${JSON.stringify(raw[names.fd])})`);
  boot.fd = fd;
  if (raw[names.protocol] !== String(constants.protocol)) {
    throw new Error(`${names.protocol}=${JSON.stringify(raw[names.protocol])}; this child speaks protocol ${constants.protocol}`);
  }
  const mapPath = raw[names.map];
  if (!mapPath || !path.isAbsolute(mapPath)) throw new Error(`${names.map} must be an absolute path (${JSON.stringify(mapPath)})`);
  boot.mapPath = mapPath;
  let allow;
  try {
    allow = JSON.parse(raw[names.allow_hosts] ?? '[]');
  } catch (err) {
    throw new Error(`${names.allow_hosts} is not JSON (${err.message})`);
  }
  if (!Array.isArray(allow) || !allow.every((h) => typeof h === 'string')) throw new Error(`${names.allow_hosts} is not a JSON array of strings`);
  boot.allowHosts = allow;
}

function readMap(constants) {
  const map = JSON.parse(fs.readFileSync(boot.mapPath, 'utf8'));
  if (map?.schema !== constants.map_schema) throw new Error(`${boot.mapPath}: schema is ${JSON.stringify(map?.schema)}, not ${constants.map_schema}`);
  if (map.child !== 'node') throw new Error(`${boot.mapPath}: child is ${JSON.stringify(map.child)}, not "node"`);
  boot.map = map;
  boot.bridgeDir = fs.realpathSync(path.dirname(boot.mapPath));
  const pluraiSim = path.dirname(boot.bridgeDir);
  boot.agentRoot = fs.realpathSync(path.resolve(pluraiSim, map.agent_root));
  boot.entryPath = path.resolve(boot.bridgeDir, map.entry.path);
}

function runtimeDirs() {
  const dirs = [RUNTIME_DIR];
  const nodeModules = path.join(RUNTIME_DIR, 'node_modules');
  if (fs.existsSync(nodeModules)) dirs.push(fs.realpathSync(nodeModules));
  return dirs;
}

async function importTsx(dirs) {
  const tsxPath = createRequire(path.join(RUNTIME_DIR, 'package.json')).resolve('tsx');
  if (!dirs.some((dir) => tsxPath.startsWith(dir + path.sep))) throw new Error(`tsx resolved outside the bridge runtime (${tsxPath})`);
  const { register, registerHooks } = module;
  let mode = null;
  module.register = function (...args) {
    mode ??= 'async';
    return register.apply(this, args);
  };
  module.registerHooks = function (...args) {
    mode = 'sync';
    return registerHooks.apply(this, args);
  };
  try {
    await import(pathToFileURL(tsxPath).href);
  } finally {
    module.register = register;
    module.registerHooks = registerHooks;
  }
  if (!mode) throw new Error(`tsx (${tsxPath}) registered no hooks`);
  boot.tsxMode = mode;
}

try {
  boot.constants = JSON.parse(fs.readFileSync(path.join(RUNTIME_DIR, 'constants.json'), 'utf8'));
  readEnv(boot.constants);
  if (typeof module.registerHooks !== 'function') {
    const floor = boot.constants.node_floor.join('.');
    throw new Error(`Node ${process.versions.node} has no module.registerHooks; the bridge needs Node >= ${floor}`);
  }
  readMap(boot.constants);
  const dirs = runtimeDirs();
  boot.runtimeDirs = dirs;
  await importTsx(dirs);
  boot.genDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'plurai-bridge-')));
  boot.loader = createLoader({ map: boot.map, agentRoot: boot.agentRoot, bridgeDir: boot.bridgeDir, runtimeDirs: dirs, genDir: boot.genDir });
  module.registerHooks(boot.loader.hooks);
  boot.loader.installCjsFallback();
} catch (err) {
  boot.error = err?.message ?? String(err);
}
