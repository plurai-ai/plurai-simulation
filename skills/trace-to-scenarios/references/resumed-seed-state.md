<!-- generated from resumed-seed-state.md of the simulation-setup-python skill; edit the source and run sync_shared_text.py --write -->
# Resumed scenarios — the optional third `seed_state` parameter (Python only)

Read this when a scenario set was generated from **production traces** (`/trace-to-scenarios`)
and the agent must pick the conversation up **from the end of the trace** rather than from a
blank thread.

Everything here is additive **to the body** of an existing `seed_state`, not to its signature.
The third parameter is not optional and never was optional in the shipped SDK: the child calls
`seed_state(initial_agent_context, initial_world_state, resume_seed)` with three positional
arguments on every scenario, passing `None` when the scenario is fresh. A two-parameter
`seed_state` raises `TypeError` in the `Init` handshake — see `seed-state-contract.md`, which is
where the base signature lives. What this reference adds is the **handling** of a non-`None`
seed; a fresh scenario runs through the steps below and returns at step 2 unchanged.

The resume-seed schema is owned by the backend and pinned in the Plurai backend decision records. Read it
there. This reference covers how to HANDLE the seed and says nothing normative about its shape:
re-deriving or restating the schema here creates a second source of truth, and a stale copy of a
schema drifts without failing anything.

## How the seed reaches you

```
`plurai convert-trace [--start-node NODE]` writes resume_pin.json into a BUNDLE folder
        │   (pin: messages + agent_state + resume_point + provenance + schema_version)
        ▼
the backend generation writes resume_seed.yaml into the SCENARIO folder
        │   (seed: messages + agent_state, plus resume_point when a node was pinned)
        ▼
the backend /threads  →  CreateThreadResponse.resume_seed: dict | None
        │                    (None ⇔ fresh scenario — the file's ABSENCE is the discriminator)
        ▼
SDK Init.resume_seed  →  your seed_state's optional THIRD parameter, VERBATIM
```

The SDK never interprets the seed and the backend never learns the agent's state shape. Rebuilding
the messages in the shape your agent holds them in, and writing them where its history lives, is
**your template's job** — that is the whole reason the parameter exists.

The dict has two top-level keys always, and a third when the operator pinned a start node:

- `messages` — the transcript, as LangChain-native minimal dicts. Each carries `type`,
  `content` and `id`; ai messages add `tool_calls: [{name, args, id}]` (args already parsed
  dicts); tool messages add `tool_call_id`. Nothing else. That wire shape is a naming convention,
  not a requirement to run LangChain — a harness on anything else renames the fields on the way
  in.
- `agent_state` — the rest of the resumed values dict, opaque to the backend and to the SDK, ferried
  byte-for-byte from the trace. It mixes the agent's own state with what the recorded turn
  reported about itself, and a production trace carries its own channel names. Restore the keys
  the agent has a slot for; skip every other one and name it (see step 3).
  A regression pin's `agent_state` is the recorded turn's `invoke_agent` return and nothing else,
  so `invoke_agent` returns every key the agent routes on: the graph's invoke result, or the saved
  values spread under the keys you add. A dict hand-picked from the saved state resumes into a
  state production never reaches (`invoke_agent_returns_picked_state`). Every key `seed_state` restores is a
  top-level key of that return, never nested under one (`seed_state_slots_never_returned`), and so is
  every key a restore deferred to the first turn writes.
- `resume_point` — **optional**, and present only when the trace was converted with
  `--start-node`. A single-key mapping, `{"node": "<name>"}`, naming the node turn 1 must start
  the agent at instead of its entry point. Nothing between the operator and this hook reads it:
  the converter writes it, the backend ferries it, the SDK ferries it. The harness is the only place
  it can be honoured, and therefore the only place it can be refused. The key's absence means
  "start turn 1 where you normally would".

## The shape of the write

Five steps, and only steps 3–5 change with the framework:

1. **Your existing `seed_state` body, kept verbatim.** Do not retype it and do not replace it with
   an `entities` apply — the world-state contract is more than one line, and the parts most easily
   lost are the ones that fail silently: the empty-`entities` synthesis fallback, the HEAD-entity
   inline-list reconciliation (the empty-HEAD defect), the FORWARD-ONLY scalar mirror, your
   fail-closed raises. All of it is specified in `seed-state-contract.md`; none of it becomes
   conditional just because a seed arrived — a resumed scenario still has entity history to
   honour. If there is no `seed_state` yet, write one from `seed-state-contract.md` FIRST, then
   come back and append steps 2–5.
2. **Fresh scenario: return.** `if not resume_seed: return` — the agent opens on a blank thread.
3. **Resumed scenario: write the transcript, and the `agent_state` keys the agent has a slot for,
   into whatever holds this thread's history, in ONE write**, under the key `invoke_agent` reads.
   On a graph that key is the channel the conversation lives in (`_CONVERSATION_CHANNEL` below):
   `messages`, unless the converter's `trace.messages_channel_inferred` finding named another.
   A key with no slot is skipped, never raised on: return `{"resume_state_dropped": [<keys>]}`
   from `seed_state` so the replay says it ran with less state than the recording (`None` when
   nothing was skipped). A seed with no `messages` is the one refusal: there is no conversation.
   **Look for the slot before you skip a key.** A key the agent branches on when the next message
   arrives — a pending question that message answers, a selection awaiting confirmation — is state
   even when `invoke_agent` returned it as a report: restore it into the object the agent reads it
   from (the same `Conversation`/session `invoke_agent` drives, a private attribute included). When
   the pin holds too little to rebuild that slot (a label without the options behind it), make
   `invoke_agent` return the slot's own value, so the next recording carries it — and name the key
   dropped until then. A regression replay whose seed reported any dropped key other than the
   turn's own report keys (`tool_trail` when the transcript carries the tool exchanges,
   `host_effects`, `input_context`, `appended_output`, `run_info`) is read as NOT measured at its
   cut (single-hop replays fall back to full ones; the record step samples nothing), so an honest
   drop costs replays; a silent one reads as "not caught". Anything the agent routes on never goes
   inside those keys (`run_info` is free-form).
4. **Tripwire: prove the write landed where turn 1 will look for it**, and that it left nothing
   half-done.
5. **Pinned start node: arm turn 1 to begin there** — only when the seed carries `resume_point`.
   This is the one part of the seed `seed_state` cannot finish by itself: where execution begins
   is an argument to the *invoke*, not a value in the checkpoint. So `seed_state` validates the
   node and records it against this thread, and `invoke_agent` spends it on the first turn.
   A harness with no node to start from raises here instead, naming the node.

**Step 1 is an instruction, not code to copy.** Pasting a worked example over a working body is the
failure this warning exists to prevent: every rule it drops fails *quietly* — the write succeeds,
the verifier's `seed_state_ignores_world_state` only asserts the parameter is **read**, and turn 1
runs against state that is present but wrong. Append steps 2–5 to what is there; do not rewrite
step 1 from here.

`invoke_agent` changes in exactly one case: a seed carrying `resume_point`. Without that key it
stays a plain pass-through with the same `thread_id`, and a resumed run reaches turn 1 down the
same code path a fresh one does. With it, the first turn — and only the first — carries the
start-node override; see points 6–9.

Three worked examples follow. A harness in none of those shapes still does these same steps
against its own persistence handle: find the thing that holds history for a thread, write the seed
through it, read it back, and start where the pin says.

### Worked example — a graph framework with a checkpointer (LangGraph)

```python
from langchain_core.messages import convert_to_messages
from langgraph.types import Command

#: thread_id -> the node turn 1 starts at. Written by seed_state, spent by invoke_agent on
#: the FIRST turn of that thread and never again. Per-thread because scenarios share the
#: process: a module-level scalar would leak one scenario's pin into the next.
_pending_goto: dict[str, str] = {}

#: The state channel this graph keeps its conversation in — where turn 1 reads the history, so
#: where the seed's messages are written. `messages` for a graph on MessagesState. When
#: `plurai convert-trace` read the conversation from another channel, its
#: `trace.messages_channel_inferred` finding names that channel in `evidence.channel`; set it here.
_CONVERSATION_CHANNEL = "messages"


def _thread_config() -> dict:
    """The ONE place this harness spells a thread_id.

    `seed_state` is handed no config and has to derive the id; `invoke_agent` is handed one.
    Both take it from here, because the arm and the spend must land on the same key: two
    spellings that drift leave the history under one id and the pin under another, the pop
    misses, and every turn starts at the graph's entry point with nothing logged.
    """
    thread_id = plurai.active_session_id()
    if not thread_id:
        raise RuntimeError("no active session; seed_state and invoke_agent run inside a scenario")
    return {"configurable": {"thread_id": thread_id}}


async def seed_state(
    initial_agent_context: dict,
    initial_world_state: dict,
    resume_seed: dict | None = None,   # <- the default is REQUIRED, see below
) -> dict | None:
    """Seed world state, then (for resumed scenarios) the agent's checkpointer.

    Runs once per scenario after start_session, before turn 1.
    """
    # 1. YOUR EXISTING seed_state BODY, KEPT VERBATIM — see step 1 above.
    ...

    # 2. Fresh scenario: nothing more to do. Note what this also prevents: arming a goto
    #    on a thread with no seed behind it, which runs the graph TWICE (see point 8).
    if not resume_seed:
        return

    # 3. Resumed scenario: replay the trace into the checkpointer in ONE write — the
    #    messages, and the state keys this graph has a channel for. ASYNC methods, see below.
    if not resume_seed.get("messages"):
        raise plurai.HarnessFault("resume seed carries no messages: there is no conversation to resume")
    config = _thread_config()
    state = resume_seed.get("agent_state") or {}
    restored = {key: value for key, value in state.items() if key in _app.channels and key != _CONVERSATION_CHANNEL}
    dropped = sorted(set(state) - set(restored))
    # Whole messages: a `tool` message's `tool_call_id` and an `ai` message's `tool_calls` ride in them.
    # A `{role, content}` rebuild breaks every pin with a tool exchange.
    values = {_CONVERSATION_CHANNEL: convert_to_messages(resume_seed["messages"]), **restored}
    await _app.aupdate_state(config, values, as_node="restore")

    # 4. Tripwire: the seeded thread MUST sit on a clean turn boundary.
    #    A non-empty `next` means the graph believes it is mid-step, and turn 1
    #    would resume that step instead of answering the user.
    state = await _app.aget_state(config)
    if state.next != ():
        raise plurai.HarnessFault(
            f"resume seed left the graph mid-step (next={state.next!r}); "
            f"as_node must name a node whose only outgoing edge is END"
        )

    # 5. Pinned start node, if the operator set one. Validate BEFORE arming: a node the
    #    compiled graph does not expose is a SILENT no-op at invoke time (point 6).
    point = resume_seed.get("resume_point")
    if point is not None:
        node = point["node"]
        if node not in _app.nodes:
            raise plurai.HarnessFault(
                f"resume_point.node {node!r} is not a top-level node of this graph; "
                f"known nodes: {sorted(_app.nodes)}"
            )
        _pending_goto[config["configurable"]["thread_id"]] = node

    # The state keys with no channel: skipped, and named in the fidelity report.
    return {"resume_state_dropped": dropped} if dropped else None


async def invoke_agent(payload: dict, config: dict) -> dict:
    # Spend the pin on the first turn of this thread, under the id seed_state armed it with
    # rather than one read off the caller's config. `pop` is what makes it one-shot: turn 2
    # finds nothing and enters at the graph's normal entry point (point 7).
    thread = _thread_config()
    node = _pending_goto.pop(thread["configurable"]["thread_id"], None)
    if node is not None:
        payload = Command(goto=node, update=payload)
    # Merged, not replaced: every key the orchestrator sets per scenario has to survive,
    # and only the id is this harness's to decide.
    config = {**config, "configurable": {**config.get("configurable", {}), **thread["configurable"]}}
    return await _app.ainvoke(payload, config)
```

**Use the async checkpointer methods (`aupdate_state` / `aget_state`).** `invoke_agent` already
requires `await _app.ainvoke(...)`, so the graph is driven asynchronously either way — and an
async-only saver (`AsyncSqliteSaver`, `AsyncPostgresSaver`) implements **only** the `a*` methods,
with the sync base methods raising `NotImplementedError`. The sync spelling therefore works on
some savers and hard-fails on others; the async spelling works on both, since a sync saver's
`a*` methods are provided by the base class. It fails loudly rather than silently, so it is not
one more silent trap — but it stops customers cold for no reason.

### Worked example — a harness with its own thread-keyed store

The payload contract already requires you to keep history somewhere: the SDK sends ONE new user
message per turn, so a framework-free harness persists the conversation in its own store keyed on
`config['configurable']['thread_id']`. Resuming is writing the seed into **that** store before
turn 1. Nothing else changes — same seed, same fail-closed posture, same "declare the parameter
with a default" rule. `_RETURNS_TURN_ONLY` is a module constant you set from your own
`invoke_agent`: `True` when it returns only this turn's messages, `False` when it returns the full
history.

```python
    # 3'. Resumed scenario: write the transcript into the SAME thread-keyed store
    #     invoke_agent reads. Replaces steps 3-4 of the example above.
    thread_id = plurai.active_session_id()
    if not thread_id:
        raise RuntimeError("no active session in seed_state; the resumed history has no key")

    if not resume_seed.get("messages"):
        raise plurai.HarnessFault("resume seed carries no messages: there is no conversation to resume")
    _HISTORY[thread_id] = [_to_my_message(m) for m in resume_seed["messages"]]

    # 4'. Tripwire: prove EVERY seeded message landed where turn 1 will look for it.
    restored, seeded = len(_HISTORY.get(thread_id) or []), len(resume_seed["messages"])
    if restored < seeded:
        raise plurai.HarnessFault(f"resume seed: restored {restored} of {seeded} messages")

    # 4''. Restore the agent_state keys this store has a slot for. It holds messages, so the one
    #      slot is `tool_trail`, the harness's OWN per-turn report: the seed carries the LAST seeded
    #      turn's calls only (earlier turns' calls are not in it). When the transcript has no tool
    #      exchanges, fold them back as call and result pairs before that turn's reply — ONLY when
    #      invoke_agent returns just THIS turn's messages (`_RETURNS_TURN_ONLY`). A harness that
    #      returns its FULL history must not fold: the SDK strips the seed off a cumulative reply by
    #      an exact prefix match, and inserted pairs break it, so the whole chain is filed as turn 1.
    #      There `tool_trail` is dropped and named like any other key.
    state = dict(resume_seed.get("agent_state") or {})
    messages = resume_seed["messages"]
    trail = state.get("tool_trail") or []
    if _RETURNS_TURN_ONLY and trail and not any(m.get("type") == "tool" for m in messages):
        del state["tool_trail"]
        at = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get("type") == "ai"), len(messages))
        pairs = []
        for n, call in enumerate(trail):
            call_id = call.get("call_id") or f"seeded-{n}"
            pairs.append(_to_my_message({"type": "ai", "content": "", "tool_calls": [{"name": call["tool"], "args": call.get("args") or {}, "id": call_id}]}))
            pairs.append(_to_my_message({"type": "tool", "content": str(call.get("result") or ""), "tool_call_id": call_id}))
        _HISTORY[thread_id][at:at] = pairs
    # Every other key is the recorded turn's report (its kind, run info) or state this store has
    # no slot for: skipped and named.
    dropped = sorted(state)

    # 5'. There are no graph nodes here, so a pinned start node cannot be honoured.
    #     Refuse it — dropping it runs the whole conversation over and scores that as
    #     the pinned resume.
    point = resume_seed.get("resume_point")
    if point is not None:
        raise plurai.HarnessFault(
            f"resume_point pins the start node {point['node']!r}, and this harness drives no "
            f"graph — re-convert the trace without --start-node, or point the export at a "
            f"graph harness"
        )
    return {"resume_state_dropped": dropped} if dropped else None
```

A harness whose state is a typed model rather than graph channels — a crew's or a flow's state
class — restores the same way, with the model's fields as the slots:
`restored = {k: v for k, v in state.items() if k in MyState.model_fields}`, the rest named in
`resume_state_dropped` — and a field whose value fails the model's validation is dropped and named the same way.

A harness whose "nodes" are its own control flow — a state machine, a dispatch table — can
honour the pin instead of refusing, by mapping the node name onto its own entry points. The
requirement is not a graph; it is a name the harness can resolve and a first turn that actually
begins there. What it must never do is resolve the name loosely: an unrecognised node is a
refusal, never a fallback to the default entry point.

`_to_my_message` is yours to write, and it is a rename, not a translation: a seed message names its
speaker in `type`, so read `type` (fall back to `role` only when `type` is absent) — a `role`-only read
restores none of them, and the count tripwire above is what refuses that at Init. `type` folds to your
provider's role (`human`→user, `ai`→assistant, `tool`→tool, `system`→system), `content` carries
across verbatim, and an ai message's `tool_calls: [{name, args, id}]` / a tool message's
`tool_call_id` map to whatever tool-call shape your client already emits. Raise on a `type` you
do not handle rather than skipping the message — a silently dropped tool exchange is a transcript
that no longer explains itself.

### Worked example — a harness that constructs its session per scenario

A harness driving a `ClaudeSDKClient` (the `/simulation-setup-skill` lane) has no store to write
into, and does not need one: `seed_state` is where the session is CONSTRUCTED, so a transcript can
go in at construction, folded into the context the session opens on. That is messages fidelity,
and it is also the ceiling — the agent has no state outside its own conversation, so every
`agent_state` key is skipped. The skill-lane templates wire exactly that: a non-`None` seed's
messages are rendered into the system prompt the session is constructed with — and they REPORT
the shortfall, returning `{'fidelity': 'reduced', 'restored': ['messages'], 'not_restored':
['agent_state']}` from `seed_state`, plus `resume_state_dropped` naming the seed's keys. That return value is the fidelity channel: the SDK carries it into the run record
verbatim, and `None` there means a full-fidelity resume, so a reduced restore that returns
nothing is recorded as one that restored everything.

`resume_point` is the one part of the seed that lane cannot report its way out of. There is no
node to start at, so a reduced-fidelity note would be describing a resume the operator did not
ask for: they pinned a starting step, and running from the top of the conversation is a
different scenario, not a lesser one. The templates therefore RAISE on it, naming the node, and
the run dies at init where an operator can see it. Reserve `not_restored` for state the agent
merely lacks; a start node nothing can start at is a refusal.

### Worked example — a Claude Code CLI session (`claude_agent_sdk`)

A harness confining a `claude_agent_sdk` agent (`plurai.confine`, the Claude Agent SDK pattern of the Python lane) resumes
the CLI's OWN session at the cut, with the SDK's own options — nothing is re-rendered. The
recorded half is what makes the replay half possible.

**Recording, on every turn.** After the turn, copy the CLI's session file out of the scenario's own
config dir — it is deleted with the scenario — through the SDK's credential redaction, and report
where it went and the uuid of the turn's last entry beside `tool_trail`:

```python
def _record_cli_session(session) -> dict:
    # {session_id, at, file}: the CLI's session transcript as of this turn's end, scrubbed.
    project = plurai.host_effects.cli_project_dir(session.options.cwd, config_dir=session.options.env["CLAUDE_CONFIG_DIR"])
    source = max(project.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)   # the session this turn wrote
    lines = source.read_text(encoding="utf-8").splitlines()
    at = next(entry["uuid"] for entry in map(json.loads, reversed(lines)) if entry.get("uuid"))
    kept = HERE / "artifacts" / "cli_sessions" / source.name
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(plurai.host_effects.redact_cli_credentials("\n".join(lines) + "\n"), encoding="utf-8")
    return {"session_id": source.stem, "at": at, "file": str(kept.relative_to(HERE))}

# per turn:  return {"messages": [...], "tool_trail": ..., "host_effects": record, "cli_session": _record_cli_session(session)}
```

The regression recorder copies that file into the folder as `cli_session/<session_id>.jsonl`, and
the pin's `agent_state` carries the report of the turn before the cut.

**Replaying.** `seed_state` finds the recorded file by the seed's `cli_session.session_id`; where
the session is first built, the file is placed — content unchanged — under the NEW scenario's cwd
in the scenario's own config dir (never the operator's home), and the session resumes it at `at`,
forked. The file and the cut are validated in `seed_state`, so Init refuses a bad pin:

```python
_PENDING_RESUME: dict[str, tuple[Path, str, str]] = {}   # thread_id -> (recorded file, session id, at)
_RESUME_MAX_BUFFER = 64 * 1024 * 1024   # the client's per-line limit on a RESUMED session (a limit, not an allocation)

async def seed_state(initial_agent_context: dict, initial_world_state: dict, resume_seed: dict | None = None) -> dict | None:
    ...  # your existing body: the world, the scenario's input documents
    if not resume_seed:
        return None
    if not resume_seed.get("messages"):
        raise plurai.HarnessFault("resume seed carries no messages: there is no conversation to resume")
    state = resume_seed.get("agent_state") or {}
    recorded = state.get("cli_session") or {}
    found = sorted(HERE.glob(f"regressions/*/cli_session/{recorded.get('session_id')}.jsonl"))
    if not found or not recorded.get("at"):
        raise plurai.HarnessFault(f"no recorded CLI session for {recorded.get('session_id')!r}: the conversation cannot be resumed")
    # Checked HERE, in Init, not at the first query: a session file that does not parse, or a cut
    # it does not hold, is refused before any turn runs.
    try:
        uuids = {json.loads(line).get("uuid") for line in found[0].read_text(encoding="utf-8").splitlines() if line.strip()}
    except ValueError as exc:
        raise plurai.HarnessFault(f"{found[0]} does not parse as a CLI session ({exc}): the conversation cannot be resumed") from exc
    if recorded["at"] not in uuids:
        raise plurai.HarnessFault(f"{found[0]} holds no entry {recorded['at']!r}: the cut is not in the recorded session")
    _PENDING_RESUME[plurai.active_session_id()] = (found[0], recorded["session_id"], recorded["at"])
    dropped = sorted(key for key in state if key != "cli_session")
    return {"resume_state_dropped": dropped} if dropped else None


def _resume_recorded_session(session, thread_id: str) -> None:
    # Where the thread's session is first built: after plurai.confine(...), before connect.
    if (pending := _PENDING_RESUME.pop(thread_id, None)) is None:
        return
    recorded, session_id, at = pending
    project = plurai.host_effects.cli_project_dir(session.options.cwd, config_dir=session.options.env["CLAUDE_CONFIG_DIR"])
    project.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(recorded, project / recorded.name)
    session.options.resume, session.options.resume_session_at, session.options.fork_session = session_id, at, True
    session.options.max_buffer_size = max(session.options.max_buffer_size or 0, _RESUME_MAX_BUFFER)
```

The resumed CLI re-emits the recorded conversation, and one line of it (an image a tool returned) can exceed the
client's default 1 MiB line limit, which fails the replay before any turn runs. The larger limit is set on resume
only: a fresh run keeps production's own client setting.

The fork gets a new session id and the placed file is never written, so the recording stays the
recording; turn 1 then sends the issue turn's user message. The workspace is NOT restored: a
session whose earlier turns wrote files would replay against a scenario folder that lacks them,
which is why the recorder refuses such a cut.

A bridge harness resumes through its entry's `seed` export; an entry with none declares `entry.resume: {unsupported: <reason>}` in `bridge/bridge.map.json`.

## The nine things that make this work (or silently not)

Points 1–5 are the transcript half and apply to every seed. Points 6–9 are the start-node half
and apply only when the seed carries `resume_point` — but a harness that never reads that key
is failing 6–9 all at once, without a branch to put a breakpoint in.

**1. The third parameter MUST have a default.** The arity is the base contract — the SDK always
passes three arguments and `verify_simulate.py` fails a narrower signature as
`seed_state_bad_signature`. Whether the body then *reads* the parameter is gated too, as
`seed_state_ignores_resume_seed`, and that one is a flat failure with nothing to opt out of: the
converter pins every bundle it writes and no config key marks an environment fresh-only, so
accepting the seed and dropping it is a defect everywhere. Replaying it satisfies that gate; so
does refusing it (`if resume_seed: raise ...`) in a harness that cannot replay. Neither gate
reaches the traps below. The **default** is the part no gate here holds: a third positional without
one turns a two-argument call into a `TypeError` inside the `Init` IPC handshake, which the
customer sees as a bare `InitError`. Two classes of two-argument caller exist, and neither is
hypothetical. `verify_harness_opacity.py`'s harness probe calls
`asyncio.run(mod.seed_state({}, {}))`. And an SDK build older than `Init.resume_seed` passes two
arguments on every scenario: `scripts/resolve_plurai.py` refuses a runner that reports a version
below the SDK floor, but one whose version cannot be read still proceeds. The default is plainly
visible in the signature — the skill lane's harness-opacity suite reads it back with
`inspect.signature` — but `verify_simulate.py` does not gate it. That is a choice about what that
check covers, not a limit on what a checker could see, so `resume_seed: dict | None = None` is on
you.

**2. Write to the thing that OWNS the history, under the key `invoke_agent` reads.** Two halves,
and both are green when wrong.

- *The object.* On a graph framework, `aupdate_state` writes through the compiled graph's
  checkpointer, so it must be called on the same object `invoke_agent` invokes, and that object
  must have been compiled *with* a saver. Wrapper architectures are where this goes wrong: in a
  two-layer graph the inner agent is frequently compiled persistence-less and only the outer
  wrapper carries the saver. Seeding the inner one raises, or writes to a store nothing reads. A
  harness with its own store has the same defect in different vocabulary: a store rebuilt per
  call, or a second module-level dict imported from somewhere else.
- *The key.* `seed_state` receives no `config`, so it takes the key from
  `plurai.active_session_id()`. That is the same value the orchestrator passes `invoke_agent` as
  `config['configurable']['thread_id']` — by construction, since at `Init` the child calls
  `plurai.start_session(msg.thread_id, ...)`, so the active session id *is* the thread id for the
  whole scenario. **Derive it in one place and call that from both hooks** (`_thread_config()`
  above): whatever is written under one id and looked up under another — the history, the pending
  goto — is silently lost, and a harness with two spellings has no failure to notice. Seeding
  under anything else (a module default, a fresh uuid, `"default"`) writes a history nothing ever
  reads.

**3. A state schema narrower than the seed drops `agent_state` without saying so.** On a graph
framework the gate is the **state** schema, not `input_schema`. `update_state` writes straight to
the state channels and never consults `input_schema`, so a seeded key with no channel to land in is
**discarded without error**. The write succeeds, `next` is still `()` so the step-4 tripwire stays
green, and turn 1 runs against a half-seeded state — quietly, because the transcript half lands
intact. Check that every key you expect in `agent_state` is declared on the state schema of the
graph you seed; if the wrapper's schema is narrower than the inner agent's, seed at the layer whose
schema is wide enough and carries the saver. (A narrow `input_schema` does drop non-message keys,
but only on the invoke path — that is the failure a harness hits when it seeds by invoking instead
of by writing the channels.) A harness with no per-thread state beyond the message list has the
harder version of this: often there is nowhere to put `agent_state` at all. Never drop it
silently, and never raise over it either: restore the keys that have a slot, and return the rest as
`resume_state_dropped`. A seed mixes the agent's state with the turn's own report keys and a
production trace's channel names, so a harness that raises on an unknown key refuses every replay.

**4. Leave the thread on a clean turn boundary.** A resumed thread that still looks mid-step
carries a task the graph believes it owes. On LangGraph this is the `as_node` argument, and the
name is not decorative: LangGraph computes what runs next from that node's edges, so `as_node` must
name a real node whose only outgoing edge is `END`, and any other successor leaves `next`
non-empty. What that pending task costs depends on how the harness drives the graph. A resume with
no input (`ainvoke(None, config)`) runs the owed step, so turn 1 finishes it instead of answering
the user; an invoke carrying a NEW input **discards** it and starts a fresh superstep. The SDK
drives the second way — one new user message per turn — so under it a wrong `as_node` yields a
transcript identical to a correct one while the defect sits unrun in the checkpoint. That is the
argument for step 4 being a tripwire rather than something you would notice in the results:
`next != ()` is the only reliable signal that a pending task exists. A node that already exists
purely to finish the turn is the right choice. (Worked example: a UI-filter wrapper
declares `restore` with `add_edge("restore", END)` as its only successor — hence
`as_node="restore"`.) If no such node exists, add one rather than reusing a node with live
branches. A store with no notion of a pending step has no analogue here; its step 4 is the
reachability assertion instead.

**5. Message `id`s are load-bearing — never strip or regenerate them.** On LangGraph the
`add_messages` reducer reconciles by `id`: with ids intact, re-seeding the same thread is
idempotent; without them every write appends a duplicate transcript. The converter emits ids for
this reason and `convert_to_messages` round-trips them. An appending store gets no such guarantee,
and `seed_state` runs once per scenario so double-seeding is not a live risk there — carry the ids
anyway. They are the only join back to the source trace when a run has to be explained after the
fact.

**6. A start node the compiled graph does not expose is a no-op that never fails.**
`Command(goto=…)` does not raise on a name it cannot resolve — LangGraph logs one line about a
write to an unknown channel (`wrote to unknown channel branch:to:<node>, ignoring it`) and carries
on. On a seeded thread the turn then runs NO node: the `update` still lands, so the user's message
is on the thread and the turn commits with the agent silent, and the scenario scores a turn the
agent never took. Two kinds of name fail
this way and the second is the one that catches people: a typo, and a node that is real but lives INSIDE a
subgraph. `app.nodes` holds `__start__` plus the graph's own top-level node names, so a
subgraph's interior is not addressable from out here at all. That makes `node not in _app.nodes`
in `seed_state` the only place the pin can be judged — validate there and raise, listing the
names the graph does have. Deferring the check to `invoke_agent` does not help: by then the
no-op has already happened, and there is nothing left to observe.

**7. The override lasts ONE turn, and every invoke path must spend it exactly once.** `Command`
is turn-1 input, not thread configuration: the next plain invoke re-enters at `START` on its
own, which is what you want — turn 2 is an ordinary turn of an ordinary conversation. So do not
re-apply it, and do not store it anywhere that outlives the first turn; a goto replayed every
turn pins the agent to that node forever and the transcript reads as an agent that cannot move
on. The mirror-image defect is quieter: if the harness has more than one way into the graph — a
streaming path beside `ainvoke`, a retry wrapper, a branch for tool-augmented turns — the pin
must be consumed on whichever one turn 1 actually takes. A path that skips the `pop` runs from
the entry point and scores green. `pop` rather than a read-then-clear is the whole discipline
here: one call both spends and disarms.

**8. Never arm a goto that has no seeded state behind it.** The order in `seed_state` is not
cosmetic. On a thread with no written channels, an invoke carrying `Command(goto=…)` runs the
START path AND the goto — the graph executes twice in one turn, once from the top and once from
the pin. Writing the seed first is what makes the goto a jump instead of an extra run. The same
argument covers the fresh-scenario branch: `resume_seed is None` means no transcript and no pin,
so there is nothing to arm, and a harness that arms unconditionally turns every fresh scenario
into a double run.

**9. A start node that calls `interrupt()` is out of scope.** Starting there returns an
`__interrupt__` payload on turn 1 rather than an assistant message, and the orchestrator has no
resume channel to answer it with — one new user message per turn is the whole protocol. Nothing
in the pipeline detects this, so it surfaces as a first turn whose content is not a reply. If a
customer needs that, it is a harness question rather than a pin question; pin the node that
FOLLOWS the interrupt.

Note the asymmetry all of this leaves: the SINK is framework-neutral, the SOURCE is not. `plurai
convert-trace` reads a **thread export**, so a harness that resumes fine still needs its
production traces in that shape before there is anything to resume from.

## Fail closed

Same rule as the rest of the seeding contract, and it bites harder here: a resumed scenario's
entire premise is the conversation the seed was supposed to restore. A seed with no messages is a
refusal — there is no "start fresh instead", since a scenario that quietly degrades to a blank
thread still runs and scores. State is the other way round: a key with no slot is skipped and
named in `resume_state_dropped`, never raised on.

This mirrors the backend's stance: the backend fails a folder with a malformed pin **closed**, with no
LLM-authored fallback. Do not reintroduce that degrade path client-side.

`resume_point` is the sharpest case, because ignoring it costs nothing at init and everything at
scoring time. A harness that cannot start at a graph node must REFUSE the seed, naming the node:

```python
point = resume_seed.get("resume_point")
if point is not None:
    raise plurai.HarnessFault(
        f"resume_point pins the start node {point['node']!r}, and this harness has no graph "
        f"node to start from — re-convert the trace without --start-node, or point the export "
        f"at a graph harness"
    )
```

Accept-and-drop is the shape to avoid, and it is also the tempting one: the transcript restores
correctly, the run completes, and the only thing wrong is that the agent redid the steps the
trace already contained — which reads, in every artifact, as the agent doing its job. The
operator asked for a resume that begins at a named step and got a full replay scored as one.
A refusal is legible; a dropped pin is not.

## Verifier interaction

- `seed_state_bad_signature` — the check is `positionals >= 3`, so the three-parameter signature
  above is what passes it and a two-parameter hook FAILS it. What the gate does not check is whether
  the third parameter carries a **default** — see point 1.
- `seed_state_ignores_world_state` — **still applies.** The body must genuinely read
  `initial_world_state`. Step 1 above is not optional garnish; a resumed `seed_state` that
  early-returns into the replay branch without touching world state trips this rule, and the rule
  is right to trip. Note what it does NOT check: it asserts only that the parameter is read, so a
  body that reads `initial_world_state` and then handles it wrongly stays green. That is the second
  reason step 1 says keep your existing body rather than retyping it.
- `seed_state_ignores_resume_seed` — a **fail**, not a warning; see point 1. It asserts only that
  the third parameter is READ, which a replay and a fail-closed raise both satisfy. Every trap in
  points 1–5 reads it too and still leaves the history empty — that is the point of those five.
- `seed_state_ignores_resume_point` — a **fail** on the same argument, one level down: a hook
  that takes the seed and never names `resume_point` anywhere is dropping an operator's pin in
  silence. Honouring the key satisfies it and so does refusing it, so accept-and-drop is the only
  shape that trips. What it cannot see is everything in points 6–9: it reads the merged
  simulate.py + helper source for the string, which proves the harness has an opinion about the
  pin, not that turn 1 starts at the node. Prose does not count — a comment leaves no AST node,
  and docstrings are excluded deliberately, since writing about the key is what an author does
  instead of handling it. Suppressed when `seed_state_bad_signature` or
  `seed_state_ignores_resume_seed` already fires, so one edit is reported once, and suppressed
  again for a hook whose body is `if resume_seed: raise` — that shape refuses the pin whole, and
  a hook that never resumes cannot resume at the wrong node.
- `asyncio_run_in_seed_state` — unchanged; never call `asyncio.run` in here.
