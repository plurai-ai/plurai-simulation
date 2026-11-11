// This file belongs to you. Plurai generated it from your agent as a starting point;
// review it, change it and keep it with your code.
// A worked bridge entry for a Mastra agent (contract A.12). A framework example, not protocol: copy what fits to
// plurai_sim/bridge/entry.mjs and replace every <...> from the agent's own code, citing the lines.
//
// It serves a turn exactly as a Mastra server route serves one: the NEW user message only, memory
// {thread, resource}, a RequestContext carrying the request values; history lives in Mastra Memory, keyed on the
// thread. The agent's storage module is swapped for an in-memory store by the map (modules.<path>.replace), so
// memory is per child and dies with it.

import { RequestContext } from '@mastra/core/request-context'; // bare: resolved from the agent root
import { seedToUiMessages, trailFromSteps, TRAIL_SOURCE } from './runtime/adapters/ai_sdk.mjs';

// The construction module: where `new Mastra({agents, storage, ...})` is built. Never the server entry.
import { mastra } from '../../<src/mastra/index.ts>';

/** The agent production's route serves (`mastra.getAgent('<id>')` at <file:line>). */
const AGENT_ID = '<agent id>';

let agent;

export async function load() {
  agent = mastra.getAgent(AGENT_ID);
}

/**
 * Production's resource key for a conversation (<file:line>); one per thread keeps parallel scenarios apart.
 * Production's rule goes here, reading only `threadId` and `request` (which may be undefined): turn() and seed()
 * both call it, so a resumed thread is created under the resource its turns will send.
 */
function resourceOf(threadId, request) {
  return `sim-${threadId}`;
}

export async function turn({ text, parts, thread_id, request }) {
  const requestContext = new RequestContext();
  for (const [key, value] of Object.entries(request ?? {})) {
    if (value !== undefined && value !== null && value !== '') requestContext.set(key, value);
  }
  const content = Array.isArray(parts) ? parts : text;
  const result = await agent.generate([{ role: 'user', content }], {
    memory: { thread: thread_id, resource: resourceOf(thread_id, request) },
    requestContext,
  });
  // Mastra runs on the AI SDK: its steps are the agent's own record of every tool it dispatched this turn.
  const trail = trailFromSteps(result);
  return {
    text: result.text ?? '',
    trail,
    trail_source: trail ? TRAIL_SOURCE : null,
    finish_reason: result.finishReason ?? null,
  };
}

export async function seed({ thread_id, messages, request }) {
  const memory = await agent.getMemory();
  const resourceId = resourceOf(thread_id, request);
  // Mastra memory stores AI SDK v4 UI messages. The adapter is strict: a message or content part with no faithful
  // form throws PLURAI_SEED_UNSUPPORTED (the child's seed_unsupported) instead of being flattened away.
  const converted = seedToUiMessages(messages, { version: 4 });
  await memory.createThread({ threadId: thread_id, resourceId });
  const now = Date.now();
  await memory.saveMessages({
    messages: converted.map((m, i) => ({
      id: m.id,
      role: m.role,
      threadId: thread_id,
      resourceId,
      createdAt: new Date(now - (converted.length - i) * 1000),
      content: { format: 2, parts: m.parts },
    })),
  });
  const back = await memory.recall({ threadId: thread_id, resourceId });
  // Mastra memory holds messages only: no agent_state key has a slot, so every one is reported as dropped.
  return { saved: (back?.messages ?? []).length, restored_state_keys: [] };
}
