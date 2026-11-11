// The bridge child's channel to the host (bridge contract A.2, A.3, A.5).
//
// One AF_UNIX stream inherited on PLURAI_BRIDGE_FD; UTF-8 JSON, one object per line, at most `max_line_bytes` per line
// in both directions. The agent owns stdout and stderr; nothing it prints can reach this socket. The in-flight host
// request id lives in an AsyncLocalStorage, so every `ext_call` and `event` carries the `req` it happened under (or null).

import { AsyncLocalStorage } from 'node:async_hooks';
import net from 'node:net';

const scope = new AsyncLocalStorage();

/** The host request id the current async context runs under, or null. */
export function currentReq() {
  return scope.getStore()?.req ?? null;
}

/** The per-request record of the current async context (`{req, extCalls, protocolFault}`), or undefined. */
export function currentRequest() {
  return scope.getStore();
}

/** Run `fn` as host request `req`; every async continuation it starts keeps that `req`. */
export function runRequest(req, fn) {
  return scope.run({ req, extCalls: [], protocolFault: null }, fn);
}

export class ProtocolError extends Error {
  constructor(message) {
    super(message);
    this.name = 'ProtocolError';
  }
}

/**
 * Child -> host conversion (A.5): Date -> ISO-8601, BigInt -> decimal string, undefined -> omitted, bytes ->
 * `{"$bytes": base64}`. `strict` (call arguments) throws on a function; otherwise a function is omitted.
 */
export function toWire(value, { strict = true } = {}) {
  const seen = new Set();
  const walk = (v) => {
    if (v === null || typeof v === 'string' || typeof v === 'boolean') return v;
    if (typeof v === 'number') return Number.isFinite(v) ? v : null;
    if (typeof v === 'bigint') return v.toString();
    if (typeof v === 'undefined' || typeof v === 'symbol') return undefined;
    if (typeof v === 'function') {
      if (strict) throw new TypeError('a function value');
      return undefined;
    }
    if (v instanceof Date) return Number.isNaN(v.getTime()) ? null : v.toISOString();
    if (ArrayBuffer.isView(v)) return { $bytes: Buffer.from(v.buffer, v.byteOffset, v.byteLength).toString('base64') };
    if (v instanceof ArrayBuffer) return { $bytes: Buffer.from(v).toString('base64') };
    if (seen.has(v)) throw new TypeError('a circular structure');
    seen.add(v);
    try {
      if (typeof v.toJSON === 'function' && !Array.isArray(v)) return walk(v.toJSON());
      if (Array.isArray(v)) return v.map((x) => (walk(x) ?? null));
      if (v instanceof Map) return walk(Object.fromEntries(v));
      if (v instanceof Set) return walk([...v]);
      const out = {};
      for (const [k, x] of Object.entries(v)) {
        const w = walk(x);
        if (w !== undefined) out[k] = w;
      }
      return out;
    } finally {
      seen.delete(v);
    }
  };
  return walk(value);
}

/**
 * Open the channel on `fd`.
 *
 * @param {object} o
 * @param {number} o.fd
 * @param {number} o.maxLineBytes
 * @param {string} o.idPrefix              the child's id prefix (`x`)
 * @param {(msg: object) => void} o.onMessage   every host message except `ext_result`
 * @param {(error: ProtocolError) => void} o.onProtocolError   an unreadable or oversized inbound line
 * @param {() => void} o.onEnd             the host closed its end (or the socket failed)
 */
export function openPipe({ fd, maxLineBytes, idPrefix, onMessage, onProtocolError, onEnd }) {
  const socket = new net.Socket({ fd, readable: true, writable: true });
  const pending = new Map();
  let seq = 0;
  let chunks = [];
  let buffered = 0;
  let ended = false;

  const end = () => {
    if (ended) return;
    ended = true;
    onEnd();
  };

  const dispatch = (line) => {
    if (!line.trim()) return;
    let msg;
    try {
      msg = JSON.parse(line);
    } catch (err) {
      onProtocolError(new ProtocolError(`unparseable line from the host (${err.message})`));
      return;
    }
    if (!msg || typeof msg !== 'object' || typeof msg.type !== 'string') {
      onProtocolError(new ProtocolError('a host message without a string `type`'));
      return;
    }
    if (msg.type === 'ext_result') {
      const waiter = pending.get(msg.id);
      if (!waiter) {
        process.stderr.write(`[plurai bridge] ext_result for an unknown id ${JSON.stringify(msg.id)}\n`);
        return;
      }
      pending.delete(msg.id);
      waiter(msg.ok ? { ok: true, value: msg.value } : { ok: false, error: msg.error ?? { kind: 'harness', message: 'no error body' } });
      return;
    }
    onMessage(msg);
  };

  const oversized = (head, size) => {
    const type = /"type"\s*:\s*"([A-Za-z_]+)"/.exec(head.subarray(0, 256).toString('utf8'))?.[1] ?? 'unknown';
    return new ProtocolError(`a ${type} line from the host is ${size} bytes, over the ${maxLineBytes}-byte line limit`);
  };

  socket.on('data', (chunk) => {
    let start = 0;
    let nl = chunk.indexOf(10);
    while (nl !== -1) {
      chunks.push(chunk.subarray(start, nl));
      const line = Buffer.concat(chunks);
      chunks = [];
      buffered = 0;
      start = nl + 1;
      nl = chunk.indexOf(10, start);
      if (line.length > maxLineBytes) {
        onProtocolError(oversized(line, line.length));
        return;
      }
      dispatch(line.toString('utf8'));
    }
    if (start < chunk.length) {
      chunks.push(chunk.subarray(start));
      buffered += chunk.length - start;
      if (buffered > maxLineBytes) {
        const head = Buffer.concat(chunks);
        chunks = [];
        buffered = 0;
        onProtocolError(oversized(head, `${head.length}+`));
      }
    }
  });
  socket.on('end', end);
  socket.on('close', end);
  socket.on('error', (err) => {
    process.stderr.write(`[plurai bridge] channel error: ${err.message}\n`);
    end();
  });

  /** Write one message; an oversized one is a ProtocolError naming its type and size (nothing is written). */
  const send = (msg) => {
    const line = JSON.stringify(msg);
    const size = Buffer.byteLength(line, 'utf8');
    if (size > maxLineBytes) throw new ProtocolError(`${msg.type} is ${size} bytes, over the ${maxLineBytes}-byte line limit`);
    socket.write(line + '\n');
  };

  /**
   * Send an `ext_call` stamped with the current `req`; resolves with the `ext_result` body. A call that cannot be sent
   * resolves as a harness failure and marks the request's terminal reply a protocol error.
   */
  const extCall = (tool, args, meta) => {
    const id = `${idPrefix}${++seq}`;
    const request = currentRequest();
    const record = { tool, via: meta.via, path: meta.path, answered: false, ok: null };
    request?.extCalls.push(record);
    return new Promise((resolve) => {
      pending.set(id, (result) => {
        record.answered = true;
        record.ok = result.ok;
        record.error = result.ok ? undefined : result.error;
        resolve(result);
      });
      try {
        send({ type: 'ext_call', id, req: request?.req ?? null, tool, args, via: meta.via, path: meta.path, site: meta.site ?? null });
      } catch (err) {
        pending.delete(id);
        if (request) request.protocolFault ??= err.message;
        resolve({ ok: false, error: { kind: 'harness', message: err.message } });
      }
    });
  };

  return { socket, send, extCall };
}
