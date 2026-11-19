// Trail adapter for agents whose turn runs the Claude Code CLI with `--output-format stream-json`. An entry imports
// it; the child never does.
//
//   import { withDenials, TRAIL_SOURCE } from './runtime/adapters/claude_cli.mjs';
//   let stream = '';
//   // through the agent's own runner seam: keep the raw stream the CLI printed, change nothing else
//   const runner = async (args) => { const run = await runClaude(args); stream += run.out ?? ''; return run; };
//   const trail = withDenials(agentTrail, stream);
//   return { text, trail, trail_source: TRAIL_SOURCE, finish_reason };
//
// A permission denial is `denied` from the CLI's own result record (`permission_denials[].tool_use_id`), never from a
// tool result's wording. A trail entry is matched by its `call_id` when that is the CLI's `tool_use_id`; when the
// agent's parser keeps other ids, by position, but only when the trail lists the stream's tool calls in order (the same
// tool names). A denial matched neither way is printed on stderr, never dropped in silence. A stream with no result
// line (a killed or timed-out CLI) records no denials: that is "unknown", not "none".
// Trail statuses are the SDK's (bridge contract A.4.6): ok | error | not_dispatchable | denied.

export const TRAIL_SOURCE = 'claude_stream_json';

function readStream(streamText) {
  const uses = [];
  const denied = new Set();
  for (const line of String(streamText ?? '').split('\n')) {
    if (!line.trim()) continue;
    let record;
    try { record = JSON.parse(line); } catch { continue; }
    if (record?.type === 'assistant' && Array.isArray(record.message?.content)) {
      for (const block of record.message.content) {
        if (block?.type === 'tool_use') uses.push({ id: block.id, name: block.name });
      }
    } else if (record?.type === 'result' && Array.isArray(record.permission_denials)) {
      for (const denial of record.permission_denials) {
        if (typeof denial?.tool_use_id === 'string') denied.add(denial.tool_use_id);
      }
    }
  }
  return { uses, denied };
}

/** The `tool_use_id` of every call the CLI's result lines list under `permission_denials`. Unparsable lines are skipped. */
export function deniedCallIds(streamText) {
  return readStream(streamText).denied;
}

/**
 * `{trail, unmatched}`: a copy of `trail` where every entry the CLI listed as denied has `status: 'denied'` (every other
 * field and entry unchanged), and the denied `tool_use_id`s no entry matched. A `null` trail stays `null`.
 */
export function markDenials(trail, streamText) {
  if (!Array.isArray(trail)) return { trail, unmatched: [] };
  const { uses, denied } = readStream(streamText);
  if (denied.size === 0) return { trail: trail.slice(), unmatched: [] };
  const inOrder = uses.length === trail.length && uses.every((use, i) => trail[i]?.tool === use.name);
  const matched = new Set();
  const marked = trail.map((entry, i) => {
    if (!entry) return entry;
    const id = denied.has(entry.call_id) ? entry.call_id : inOrder && denied.has(uses[i].id) ? uses[i].id : null;
    if (id === null) return entry;
    matched.add(id);
    return { ...entry, status: 'denied' };
  });
  return { trail: marked, unmatched: [...denied].filter((id) => !matched.has(id)) };
}

/** `markDenials(trail, streamText).trail`; a denial no entry matched is named on stderr. */
export function withDenials(trail, streamText) {
  const { trail: marked, unmatched } = markDenials(trail, streamText);
  if (unmatched.length > 0) {
    console.error(
      `plurai claude_cli adapter: ${unmatched.length} permission denial(s) match no trail entry, by call_id or by tool `
      + `name and order (${unmatched.join(', ')}); those calls keep the status the agent's parser gave them`,
    );
  }
  return marked;
}
