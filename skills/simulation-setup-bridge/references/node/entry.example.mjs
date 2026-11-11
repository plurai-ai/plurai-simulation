// This file belongs to you. Plurai generated it from your agent as a starting point;
// review it, change it and keep it with your code.
// plurai_sim/bridge/entry.mjs: the per-agent entry of the bridge child (contract A.12).
//
// TEMPLATE: copy to plurai_sim/bridge/entry.mjs and replace every <...> and every name marked PLACEHOLDER. The bridge child imports this module
// and calls its exports; nothing else does. It imports the agent's CONSTRUCTION module (where the agent object
// is built), never its server or main module: no listener, no port, no process.exit.
//
// - Relative imports resolve from this file (plurai_sim/bridge/); bare imports resolve as if imported from the
//   map's agent_root, so the agent's own dependencies are found.
// - stdout and stderr belong to the agent. Write diagnostics with console.error; never touch process.stdout.
// - Every external call the agent makes is answered by the mocks through bridge.map.json; this file never mocks.

// The agent's construction module, unchanged. A top-level import is fine: load() runs during the start handshake,
// so an import error surfaces as entry_failed before turn 1.
import { buildAgent } from '../../<src/agent.ts>'; // PLACEHOLDER: the agent's factory and its module

let agent;

/** Called once, before any turn. Build the agent the way production builds it at startup. */
export async function load() {
  agent = await buildAgent();
}

/**
 * One user turn: the NEW user message only. History is the agent's own, keyed on thread_id, exactly as
 * production keys a conversation.
 *
 * @param {{text: string, parts: Array|null, thread_id: string, turn_index: number, request: object}} input
 *   parts: the message's content parts, unconverted, when the user turn carried a list; request: the values
 *   production's client sends with every call (the config's `inject: request` fields).
 * @returns {{text: string, trail: Array|null, trail_source?: string|null, finish_reason: string|null,
 *            input_context?: Array, appended?: Array<{site: string, text: string}>}}
 *   trail: one {tool, args, call_id, result, status} per tool the agent dispatched this turn, in order, from
 *   the framework's own record, or null when this entry cannot observe the agent's tools. Never [] for
 *   "unknown": [] means "observed, and none ran".
 *   appended: text the product's own code adds to `text` after the model's output (a tool-failure warning), each
 *   entry verbatim as it appears in `text`, with the `<agent-relative file>:<line>` that adds it. Omit when none.
 */
export async function turn({ text, parts, thread_id, turn_index, request }) {
  // PLACEHOLDER: the call production's route makes, with production's own run settings (cite the line).
  const result = await agent.respond(text, { threadId: thread_id, context: request });
  // An agent whose memory saves the turn itself needs nothing here. When this entry keeps the thread's history
  // (production's route does), store it in the shape production stores (for example, UI messages converted back
  // with convertToModelMessages). When that shape is model messages, append every step's messages, never the
  // final step's `response.messages` alone, which on AI SDK 7 drops earlier steps' tool calls and results:
  //   import { turnMessages } from './runtime/adapters/ai_sdk.mjs';
  //   history.get(thread_id).push(userMessage, ...(await turnMessages(result)));
  return {
    text: result.text, // PLACEHOLDER
    trail: null,
    trail_source: null,
    finish_reason: result.finishReason ?? null, // PLACEHOLDER
  };
}

/**
 * OPTIONAL. Write a recorded conversation into the thread before turn 1 (a resumed scenario). Export it only
 * when the agent's own memory can take it, and declare the map's entry.resume as "seed"; with no seed export the
 * map declares {"unsupported": "<why>"}. Thread-scoped, create-or-reuse.
 *
 * @param {{thread_id: string, messages: Array, agent_state: object|null, request: object}} input
 *   messages: the SDK's resume-seed messages, unconverted ({type: human|ai|tool|system, content, ...}).
 *   request: the same values turn() receives; key the memory write exactly as turn() keys it (thread, resource,
 *   user, session), so turn 1 addresses the thread written here.
 * @returns {{saved: number, restored_state_keys: string[]}}
 *   restored_state_keys: the agent_state keys this entry put back; the harness reports the rest as dropped.
 *   A message this entry cannot convert: throw an Error whose code is 'PLURAI_SEED_UNSUPPORTED'.
 */
export async function seed({ thread_id, messages, agent_state, request }) {
  const converted = messages.map((m) => {
    if (m.type === 'human') return { role: 'user', content: m.content };
    if (m.type === 'ai') return { role: 'assistant', content: m.content };
    if (m.type === 'system') return null; // the agent builds its own system prompt each turn
    throw Object.assign(new Error(`no mapping for a ${m.type} seed message`), { code: 'PLURAI_SEED_UNSUPPORTED' });
  }).filter(Boolean);
  // PLACEHOLDER: the agent's own memory write, under the same keys turn() passes (here: threadId, context: request).
  await agent.memory.save(thread_id, converted, { context: request });
  return { saved: converted.length, restored_state_keys: [] };
}
