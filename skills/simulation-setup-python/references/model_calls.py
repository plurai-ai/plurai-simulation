# This file belongs to you. Plurai generated it from your agent as a starting point;
# review it, change it and keep it with your code.
"""The turn's model calls, as the message chain `invoke_agent` returns. Copy beside `simulate.py` as `_model_calls.py`.

Observability sinks draw one model call per assistant message of the turn's chain: a LangSmith `llm` run,
a Langfuse generation, each with the model name and the token usage the message carries. A harness that
returns only the final reply (`[{'role': 'assistant', 'content': reply}]`) gives them one call with neither,
and the intermediate tool-call steps are gone.

The chain a call needs, in the spelling the SDK reads from any framework:

* an assistant message: `{'role': 'assistant', 'content': <text>, 'tool_calls': [{'name', 'args', 'id'}],
  'response_metadata': {'model_name': <model>, 'model_provider': <provider>},
  'usage_metadata': {'input_tokens', 'output_tokens', 'total_tokens'}}`
* a tool result: `{'role': 'tool', 'tool_call_id': <id>, 'content': <text>}`

A LangGraph / LangChain agent needs none of this: its `AIMessage` objects already carry `tool_calls`,
`response_metadata` and `usage_metadata`, and the SDK serialises them whole. Return the graph's result as
it is. This module is for the frameworks whose native turn is not in that shape.

Every value comes from the framework's own record of the call. A model name or a count the framework does
not expose is left out of the message, never filled from a default, an estimate or the run's configuration:
a sink shows a missing value as missing, and a wrong one as fact.

Each assistant message's `content` is what the product shows for that message: apply the product's own display
transform (cite it) to every assistant message, not only the final one, and give a message whose text the product
never shows `''`. Its tool calls, model and usage stay; the trace then shows those steps with no text.
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Mapping, Sequence
from typing import Any


def _get(obj: Any, name: str) -> Any:
    """`obj[name]` for a mapping, `obj.name` for an object, else None."""
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def usage_metadata(input_tokens: Any, output_tokens: Any, total_tokens: Any = None) -> dict[str, int] | None:
    """LangChain's `usage_metadata` from the counts a framework reports, or None when it reports no pair.

    A reported total is carried as it is (a provider's total can count tokens the other two do not). Only a
    pair with no total gets their sum.
    """
    tokens_in, tokens_out = _count(input_tokens), _count(output_tokens)
    if tokens_in is None or tokens_out is None:
        return None
    total = _count(total_tokens)
    return {'input_tokens': tokens_in, 'output_tokens': tokens_out, 'total_tokens': tokens_in + tokens_out if total is None else total}


def ai_message(
    content: str,
    *,
    tool_calls: Sequence[Mapping[str, Any]] = (),
    model: str | None = None,
    provider: str | None = None,
    usage: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """One assistant message: its text, the tool calls it requested, the model that answered, its usage.

    `provider` is LangChain's `model_provider` (`'openai'`, `'anthropic'`): the API family that served the
    call, which LangSmith pairs with the model name to price it. It rides only beside a model name.
    """
    message: dict[str, Any] = {'role': 'assistant', 'content': content}
    if tool_calls:
        message['tool_calls'] = [{'name': call['name'], 'args': call.get('args') or {}, 'id': call.get('id')} for call in tool_calls]
    if model:
        message['response_metadata'] = {'model_name': model, **({'model_provider': provider} if provider else {})}
    if usage:
        message['usage_metadata'] = dict(usage)
    return message


def tool_message(call_id: str | None, content: str, *, status: str | None = None) -> dict[str, Any]:
    """The result a tool returned for the call `call_id`. `call_id` is the id the assistant message carries."""
    message: dict[str, Any] = {'role': 'tool', 'tool_call_id': call_id, 'content': content}
    if status:
        message['status'] = status
    return message


def _arguments(raw: Any) -> dict[str, Any]:
    """Tool arguments a provider sends as JSON text, as a mapping; text that is not JSON is kept as it came."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except ValueError:
            return {'_unparsed_arguments': raw}
    return dict(raw) if isinstance(raw, Mapping) else {}


# ---------------------------------------------------------------- Strands Agents


def strands_model_id(agent: Any) -> str | None:
    """The model id the Strands agent's model is configured with, None when it exposes none."""
    getter = getattr(_get(agent, 'model'), 'get_config', None)
    try:
        config = getter() if callable(getter) else None
    except Exception:  # noqa: BLE001 - a model that cannot report its config has no id to report
        return None
    model_id = _get(config, 'model_id')
    return model_id if isinstance(model_id, str) and model_id else None


def strands_cycle_usage(agent: Any) -> list[dict[str, int] | None]:
    """Token usage of each event-loop cycle of the agent's latest invocation, in order.

    One cycle is one model call. `Agent.event_loop_metrics` accumulates over the agent's life, so this
    reads only `latest_agent_invocation`: call it right after the turn, before the next one starts. That
    property is strands-agents 1.21.0+; an older agent has no per-call usage to read, so the chain carries
    none (with one warning) rather than a total spread over calls it does not belong to.
    """
    metrics = _get(agent, 'event_loop_metrics')
    reports = 'latest_agent_invocation' in metrics if isinstance(metrics, Mapping) else hasattr(metrics, 'latest_agent_invocation')
    if metrics is not None and not reports:
        _warn_once('strands-agents before 1.21.0 reports no per-invocation cycles; the chain carries no token usage')
    invocation = _get(metrics, 'latest_agent_invocation')
    usage: list[dict[str, int] | None] = []
    for cycle in _get(invocation, 'cycles') or ():
        reported = _get(cycle, 'usage')
        usage.append(usage_metadata(_get(reported, 'inputTokens'), _get(reported, 'outputTokens'), _get(reported, 'totalTokens')))
    return usage


_WARNED: set[str] = set()


def _warn_once(text: str) -> None:
    if text not in _WARNED:
        _WARNED.add(text)
        warnings.warn(text, stacklevel=3)


class StrandsTurn:
    """Collects the messages a Strands agent adds during one turn. A hook provider: `agent.hooks.add_hook(turn)`.

    The turn's messages are the ones the agent added since the last `take()`, so a conversation manager that
    trims history mid-turn cannot move the boundary the way a message count read before the turn would.
    """

    def __init__(self) -> None:
        self.messages: list[Any] = []

    def register_hooks(self, registry: Any, **kwargs: Any) -> None:
        from strands.hooks import MessageAddedEvent  # pyright: ignore[reportMissingImports]

        registry.add_callback(MessageAddedEvent, self._added)

    def _added(self, event: Any) -> None:
        self.messages.append(event.message)

    def take(self) -> list[Any]:
        taken, self.messages = self.messages, []
        return taken


def _strands_result_text(blocks: Any) -> str:
    """A Strands `toolResult` content list as text: `text` blocks as they are, `json` blocks as JSON."""
    parts = []
    for block in blocks or ():
        if (text := _get(block, 'text')) is not None:
            parts.append(str(text))
        elif (data := _get(block, 'json')) is not None:
            parts.append(json.dumps(data, default=str))
    return '\n'.join(parts)


def strands_chain(
    messages: Sequence[Any],
    *,
    model: str | None = None,
    cycle_usage: Sequence[Mapping[str, int] | None] | None = None,
) -> list[dict[str, Any]]:
    """The turn's Strands (Bedrock Converse) messages as an assistant / tool chain.

    `messages` is what `StrandsTurn.take()` returned. The user's own message is left out (the SDK holds
    the turn's input); every assistant message and every tool result is kept in order. `model` is
    `strands_model_id(agent)`.

    `cycle_usage` is `strands_cycle_usage(agent)`. A cycle is a model call, so its usage belongs to the
    assistant message of the same position, and only when the two lists are the same length: a turn whose
    cycles and assistant messages do not line up (a retry, a sub-agent's messages mixed in) is left without
    per-message usage rather than given a count that belongs to another call.
    """
    assistants = [m for m in messages if _get(m, 'role') == 'assistant']
    usage = list(cycle_usage) if cycle_usage is not None and len(cycle_usage) == len(assistants) else [None] * len(assistants)
    chain: list[dict[str, Any]] = []
    position = 0
    for message in messages:
        role, blocks = _get(message, 'role'), _get(message, 'content') or ()
        if role == 'assistant':
            text = '\n'.join(str(t) for block in blocks if (t := _get(block, 'text')) is not None)
            calls = [
                {'name': use['name'], 'args': use.get('input') or {}, 'id': use.get('toolUseId')}
                for block in blocks
                if isinstance(use := _get(block, 'toolUse'), Mapping) and use.get('name')
            ]
            chain.append(ai_message(text, tool_calls=calls, model=model, usage=usage[position]))
            position += 1
        elif role == 'user':
            for block in blocks:
                if isinstance(result := _get(block, 'toolResult'), Mapping):
                    chain.append(tool_message(result.get('toolUseId'), _strands_result_text(result.get('content')), status=result.get('status')))
    return chain


# ---------------------------------------------------------------- OpenAI and Anthropic clients


def openai_chat_message(completion: Any, *, choice: int = 0, provider: str | None = 'openai') -> dict[str, Any]:
    """The assistant message of one OpenAI chat completion, with the model and usage that response reports.

    For a loop that calls `client.chat.completions.create(...)` itself: append this per call, then a
    `tool_message(call.id, result)` per tool call the loop ran, and return the list under `messages`.
    `provider` is `'openai'`, as LangChain's `ChatOpenAI` reports it; pass the serving provider's name, or
    None, when the client points at another vendor's OpenAI-compatible endpoint.
    """
    message = _get(_get(completion, 'choices')[choice], 'message')
    usage = _get(completion, 'usage')
    calls = [
        {'name': _get(_get(call, 'function'), 'name'), 'args': _arguments(_get(_get(call, 'function'), 'arguments')), 'id': _get(call, 'id')}
        for call in _get(message, 'tool_calls') or ()
    ]
    return ai_message(
        _get(message, 'content') or '',
        tool_calls=calls,
        model=_get(completion, 'model'),
        provider=provider,
        usage=usage_metadata(_get(usage, 'prompt_tokens'), _get(usage, 'completion_tokens'), _get(usage, 'total_tokens')),
    )


def anthropic_message(response: Any, *, provider: str | None = 'anthropic') -> dict[str, Any]:
    """The assistant message of one Anthropic Messages response, with the model and usage it reports.

    The same loop shape as `openai_chat_message`; a tool result the loop sends back is
    `tool_message(block.id, result_text)` for the `tool_use` block of the same id. `provider` as for
    `openai_chat_message` (a Bedrock or Vertex client serves the same shape under another provider).
    """
    blocks = _get(response, 'content') or ()
    text = '\n'.join(t for block in blocks if _get(block, 'type') == 'text' and isinstance(t := _get(block, 'text'), str))
    calls = [
        {'name': _get(block, 'name'), 'args': _arguments(_get(block, 'input')), 'id': _get(block, 'id')}
        for block in blocks
        if _get(block, 'type') == 'tool_use'
    ]
    usage = _get(response, 'usage')
    return ai_message(
        text,
        tool_calls=calls,
        model=_get(response, 'model'),
        provider=provider,
        usage=usage_metadata(_get(usage, 'input_tokens'), _get(usage, 'output_tokens')),
    )


# ---------------------------------------------------------------- OpenAI Agents SDK


def openai_agents_chain(result: Any, *, model: str | None = None) -> list[dict[str, Any]]:
    """A `Runner.run` result as an assistant / tool chain: one assistant message per model response.

    `result.raw_responses` is the run's model calls in order, each with the output items it produced and the
    usage it was billed; `result.new_items` holds the tool outputs. `model` is the agent's model name when the
    harness knows it (`agent.model` as configured); a response does not carry one, so None leaves it out.

    `model` is stamped on EVERY response, so pass it only for a run that cannot hand off: after a handoff the
    responses come from other agents, possibly on other models, and one name for all of them is wrong as fact.
    A run with handoffs leaves `model` out.
    """
    outputs: dict[str, str] = {}
    for item in _get(result, 'new_items') or ():
        raw = _get(item, 'raw_item')
        if _get(item, 'type') == 'tool_call_output_item' and isinstance(call_id := _get(raw, 'call_id'), str):
            output = _get(item, 'output')
            outputs[call_id] = output if isinstance(output, str) else json.dumps(output, default=str)
    chain: list[dict[str, Any]] = []
    for response in _get(result, 'raw_responses') or ():
        texts: list[str] = []
        calls: list[dict[str, Any]] = []
        for item in _get(response, 'output') or ():
            kind = _get(item, 'type')
            if kind == 'message':
                texts.extend(t for part in _get(item, 'content') or () if isinstance(t := _get(part, 'text'), str))
            elif kind == 'function_call' and (name := _get(item, 'name')):
                calls.append({'name': name, 'args': _arguments(_get(item, 'arguments')), 'id': _get(item, 'call_id')})
        usage = _get(response, 'usage')
        chain.append(
            ai_message(
                '\n'.join(texts),
                tool_calls=calls,
                model=model,
                usage=usage_metadata(_get(usage, 'input_tokens'), _get(usage, 'output_tokens'), _get(usage, 'total_tokens')),
            )
        )
        chain.extend(tool_message(call['id'], outputs[call['id']]) for call in calls if call['id'] in outputs)
    return chain
