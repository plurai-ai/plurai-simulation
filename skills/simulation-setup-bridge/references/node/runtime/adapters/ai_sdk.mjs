// Trail and seed adapter for agents built on the Vercel AI SDK `steps` record (AI SDK v4 and v5, and Mastra, which
// wraps each tool call and result in `payload`). An entry imports it; the child never does.
//
//   import { trailFromSteps, seedToUiMessages, turnMessages, TRAIL_SOURCE } from './runtime/adapters/ai_sdk.mjs';
//   const res = await agent.generate(...);
//   const trail = trailFromSteps(res);            // null when the result carries no step record
//   return { text: res.text, trail, trail_source: trail ? TRAIL_SOURCE : null, finish_reason: res.finishReason ?? null };
//
// An entry that keeps model-message history itself appends `turnMessages(res)`: every step's messages, so earlier
// steps' tool calls and results stay in history. Never the final step's `response.messages` alone (AI SDK 7).
//
// Trail statuses are the SDK's (bridge contract A.4.6): ok | error | not_dispatchable | denied.

export const TRAIL_SOURCE = 'ai_sdk';

const unwrap = (x) => (x && typeof x === 'object' && x.payload && typeof x.payload === 'object' ? x.payload : x);
const idOf = (x) => x?.toolCallId ?? x?.id ?? null;

/**
 * One trail entry per tool call, in execution order, from the result's own `steps` record.
 * Returns null when `result.steps` is not a list: this agent's tools cannot be observed, which is not "none".
 */
export function trailFromSteps(result) {
  const steps = result?.steps;
  if (!Array.isArray(steps)) return null;
  const trail = [];
  for (const step of steps) {
    const calls = (step?.toolCalls ?? []).map(unwrap);
    const results = new Map();
    for (const r of (step?.toolResults ?? []).map(unwrap)) results.set(idOf(r), r);
    const errors = new Map();
    const denied = new Set();
    for (const part of Array.isArray(step?.content) ? step.content.map(unwrap) : []) {
      if (part?.type === 'tool-error') errors.set(idOf(part), part.error);
      else if (typeof part?.type === 'string' && part.type.includes('denied')) denied.add(idOf(part));
      else if (part?.type === 'tool-approval-response' && part.approved === false) denied.add(idOf(part));
    }
    for (const call of calls) {
      const id = idOf(call);
      const res = results.get(id);
      let status;
      let value = null;
      if (denied.has(id)) status = 'denied';
      else if (errors.has(id) || call.invalid === true || res?.isError === true) {
        status = 'error';
        value = describe(errors.get(id) ?? call.error ?? res?.result ?? res?.output ?? null);
      } else if (res) {
        status = 'ok';
        value = res.result !== undefined ? res.result : (res.output ?? null);
      } else status = 'not_dispatchable';
      trail.push({ tool: call.toolName, args: call.args ?? call.input ?? {}, call_id: id, result: value, status });
    }
  }
  return trail;
}

/**
 * Every message this turn added, in order: each step's assistant message and its tool results.
 * AI SDK 7 (verified from the 7.0.99 source): `response` is the FINAL step only and `responseMessages` covers every
 * step, so `responseMessages` is read when the result has it. With no such getter, the fallback is `response.messages`,
 * which AI SDK v4/v5 kept for the whole turn (not verified here; v6 not verified either). The smoke's tool-history
 * probe is the guard if a version changes this again.
 */
export async function turnMessages(result) {
  if (result && typeof result === 'object' && 'responseMessages' in result) return (await result.responseMessages) ?? [];
  return (await result?.response)?.messages ?? [];
}

function describe(error) {
  if (error instanceof Error) return `${error.name}: ${error.message}`;
  return error;
}

function unsupported(message) {
  const err = new Error(message);
  err.code = 'PLURAI_SEED_UNSUPPORTED';
  return err;
}

function textOf(content, index) {
  if (typeof content === 'string') return content;
  if (content == null) return '';
  if (!Array.isArray(content)) throw unsupported(`seed message ${index}: content is neither text nor a list of parts`);
  return content
    .map((part) => {
      if (typeof part === 'string') return part;
      if (part?.type === 'text' && typeof part.text === 'string') return part.text;
      throw unsupported(`seed message ${index}: a ${JSON.stringify(part?.type)} content part has no text form`);
    })
    .join('\n');
}

const ROLE = { human: 'user', user: 'user', ai: 'assistant', assistant: 'assistant', tool: 'tool', system: 'system' };

/**
 * The SDK's resume-seed messages (`{type: human|ai|tool|system, content, tool_calls?, tool_call_id?, id?}`, or the same
 * with `role`) as AI SDK UI messages `{id, role, parts}`. `version: 4` (Mastra memory) writes `tool-invocation` parts;
 * `version: 5` writes `tool-<name>` parts. System messages are dropped (the agent builds its own each turn). Anything
 * with no faithful form throws an Error whose `code` is 'PLURAI_SEED_UNSUPPORTED' (the child's `seed_unsupported`).
 */
export function seedToUiMessages(messages, { version = 4 } = {}) {
  if (!Array.isArray(messages)) throw unsupported('the seed carries no message list');
  if (version !== 4 && version !== 5) throw unsupported(`AI SDK UI message version ${version} is not supported`);
  const out = [];
  const calls = new Map();
  messages.forEach((m, i) => {
    const role = ROLE[m?.type ?? m?.role];
    if (!role) throw unsupported(`seed message ${i}: type ${JSON.stringify(m?.type ?? m?.role)} has no AI SDK form`);
    if (role === 'system') return;
    if (role === 'tool') {
      const part = calls.get(m.tool_call_id);
      if (!part) throw unsupported(`seed message ${i}: a tool result with no matching call (${m.tool_call_id})`);
      const result = typeof m.content === 'string' ? m.content : (m.content ?? null);
      if (version === 4) Object.assign(part.toolInvocation, { state: 'result', result });
      else Object.assign(part, { state: 'output-available', output: result });
      return;
    }
    const text = textOf(m.content, i);
    const parts = text ? [{ type: 'text', text }] : [];
    if (role === 'assistant') {
      for (const c of m.tool_calls ?? []) {
        if (!c?.id || !c?.name) throw unsupported(`seed message ${i}: a tool call without an id and a name`);
        const part =
          version === 4
            ? { type: 'tool-invocation', toolInvocation: { state: 'call', toolCallId: c.id, toolName: c.name, args: c.args ?? {} } }
            : { type: `tool-${c.name}`, toolCallId: c.id, state: 'input-available', input: c.args ?? {} };
        calls.set(c.id, part);
        parts.push(part);
      }
    }
    out.push({ id: m.id ?? `seed-${i}`, role, parts });
  });
  return out;
}
