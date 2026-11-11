<!-- generated from seed-state-contract.md of the simulation-setup-python skill; edit the source and run sync_shared_text.py --write -->
# `seed_state` hook contract (Python only)

Read this when the dispatcher's Step 6.5 state-surfaces audit found essential
state surfaces (storage / cache / repository / env-driven HTTP that ≥1 tool
reads from) — you MUST seed them through the `seed_state` hook before turn 1.
Otherwise tools fail their first read, the React loop retries, and
`GraphRecursionError` exhausts the iteration budget.

If the dispatcher's audit found nothing beyond mock-env world state + LangGraph
state, **skip `seed_state`** — it is optional.

This step is the Python-specific implementation of that audit.

Alongside `invoke_agent`, define a one-time setup callable as a **module-level function** in simulate.py:

```python
def seed_state(
    initial_agent_context: dict,
    initial_world_state: dict,
    resume_seed: dict | None = None,   # required parameter, defaulted value
) -> dict | None:
    """Called once by the child subprocess after start_session, before turn 1.
    Use this to write seed data from agent_context into any non-LangGraph
    state surface (storage, cache, repository) that tools read from.

    The return value is the resume fidelity report — see below. Return
    ``None`` only when the restore was complete.

    If your underlying storage API is async, declare this `async def` —
    the child awaits it. **Never call asyncio.run() inside this
    function**; same rule as invoke_agent (see the Python sub-skill's
    Step 8 adapter rules)."""
    ...
```

That's it. **No wiring step.** The child subprocess (`python -m plurai.child <simulate.py>`) auto-discovers `seed_state` as a module attribute and calls it during the `Init` IPC handshake. Absence is a no-op.

`seed_state` receives **three** arguments on every call, and MUST honour the first two:

- `initial_world_state` — the scenario's per-revision entity history (records, labels, example_sets, commit_id). Populated by the backend's per-scenario extraction session (`the backend.scenario_generator.property_extraction.sdk_extraction_node`) and stored on disk in the scenario's `initial_world_state.yaml`. **This is the ground truth** for the agent's persisted state — when present, apply it verbatim. Apply the values verbatim, not the keys: each row also carries `_entity_type`, and an `id` copy of its id whenever its type keys by another `id_field` (the backend adds it for by-id readers). Neither is a field of your store. When you build a store record from a row (a harness-owned store, `owner: agent` / `owned_types`), build it with `plurai.store_record(row)`, which drops both keys against the declared schema: a store that validates its records (a config schema that forbids extra keys) drops one with an unknown key, and the agent meets a world without it.
- `initial_agent_context` — the scenario's session-scoped fields (an active account id, a configured policy text, a selected template, etc.) from `agent_context.yaml`. Used to fill in fields that are NOT entity attributes (per-session settings the agent reads from its runtime state, not from the world).

**The THIRD parameter is REQUIRED — declare it even if you expect every scenario to be fresh.** The SDK calls
`seed_state(initial_agent_context, initial_world_state, resume_seed)` with three positional
arguments **unconditionally**; there is no signature inspection, and `resume_seed` is simply
`None` for a fresh scenario. A two-parameter `seed_state` therefore raises `TypeError` inside the
`Init` IPC handshake, which the customer sees as a bare `InitError` on every scenario. Declare it
**with a default** — `resume_seed: dict | None = None` — so the parameter is also inert under any
caller that passes two arguments — the skill lane's `verify_harness_opacity` probe does, and so
does an SDK build older than `Init.resume_seed` whose version `resolve_plurai.py` cannot read (it
refuses one that reports a version below the floor). Implemented in plurai-sdk `child.py`.

**Declaring it is not the whole obligation — the body must HANDLE it.** Scenarios generated from
production traces (`/trace-to-scenarios`) start the agent mid-conversation and deliver the
transcript + agent state through this parameter, and there is no way for an environment to opt
out of receiving one: `plurai convert-trace` writes a resume pin into every bundle it converts,
capability is declared in no config key, and nothing is inferred from `agent.framework`.
So a body that accepts the seed and drops it is a defect in any environment, not just a
trace-driven one — the agent opens on an empty history, the orchestrator excises turn 1 as
already-said, and the run scores a conversation that never happened. Two shapes are honest, and
the example below is the first of them: **replay it**, per
`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/references/resumed-seed-state.md` (the
checkpointer-seeding recipe and the five ways it silently misfires). A harness that genuinely
cannot restore a conversation **refuses it** with a raise instead — honest, and a much smaller
class of agent than it looks, since resuming is usually a saver plus this replay away. Step 9e's
`seed_state_ignores_resume_seed` fails the third shape, accept-and-drop, and only that one.

**The RETURN VALUE is a protocol field, not a convenience.** What `seed_state` hands back is the
resume fidelity report: the SDK carries it verbatim into the run record as `resume_fidelity`, where
the scoring lane and the human reading the artifacts both see it. `None` there is a positive claim —
*this scenario resumed at full fidelity* — so a body that restores part of the seed and returns
nothing has the run scored as though the agent saw state it never got. A partial restore says so:

```python
return {'fidelity': 'reduced', 'restored': ['messages'], 'not_restored': ['agent_state']}
```

Name in `restored` what the agent can actually observe after the call, and in `not_restored`
everything the seed carried that this harness cannot put back. A harness that refuses a non-`None`
seed outright never reaches a return, so it needs no report; a harness that replays everything the
seed carries returns `None`.

## The one pattern the implementation MUST get right

`seed_state` writes the scenario's per-session entities through `plurai.world_state.apply(...)`. Lead with `initial_world_state['entities']` when present, fall back to a synthesised single-revision seed only when it's empty (hand-authored scenarios). **For a world the mock owns — every record the agent reads lives in `plurai.world_state` — no module-level singleton and no `clear()` before seed**, because two properties of the mock world make in-memory boilerplate unnecessary:

- **Per-session isolation by construction.** `plurai.world_state` is keyed by `session_id`; each scenario gets a fresh session. There is nothing in THAT world to reset between scenarios — scenario 1's writes live under session A, scenario 2's reads happen under session B, they cannot collide.
- **No instance state to share.** A storage pass-through (Step 8.4) holds nothing — every instance calls the same seam mocks over the same world; the factory returning a fresh instance per call is fine. There is no singleton to keep in lock-step with `seed_state`.

### …and the world the mock does NOT own: `reset()`

Both properties above are properties of `plurai.world_state`, not of your harness. Exactly two
things are reset per scenario: the mock session the backend opens (keyed on `session_id`, above) and
the child process the runner spawns (so a plain module-level list is born and dies inside one
scenario — that is why the singleton ban is about tidiness, not leakage). **Anything else your
harness writes to survives the scenario**: a file, a sqlite/Postgres row, your checkpointer's
database, a real CRM, a server-side conversation store. Scenario 2 then opens on scenario 1's
writes, scenario 3 on both, and every verdict is partly about the order the scenarios happened to
run in — on a run that completes GREEN, because nothing in the output says a scenario started
dirty.

A bench like that declares who owns its world, as a ROOT key of `plurai.config.yaml`:

```yaml
world_state:
  owner: external        # `mock` (the default — omit the key) | `agent` | `external`
```

`agent` is a store inside the agent's own process or beside it; `external` is a real system behind
it. Either one is reset only when `world_state.confirmation` names it (`{answer: harness_created |
disposable_copy, target}`, recorded by the scan). They differ in where the store is, not in what you owe: **a harness declaring either one owes
TWO hooks at simulate.py's module scope** — `reset()` to CLEAR that store between scenarios, and
`world_snapshot()` to READ it at the end of one. Nothing else can reach it, because only your
harness knows where it is.

`reset()` is the blocking one: `plurai-run-simulation` holds the run at start (exit 65, before a
thread is opened) when it is missing, until the gap is fixed or its finding overruled, and Step
9e's `world_owner_unreset` names the same gap at authoring time. Only on the user's explicit opt-in to
conversations seeing each other's writes, `world_state.shared_world_ok: true` lets the run proceed
instead, carrying the named risk `shared_world`. `world_snapshot()` is not blocking — the run starts, warns at start, and reports
NO world, so every world-reading policy resolves to `world_unobservable` (not-measured) instead of
passing vacuously on a store nobody read. Step 9e reports it as `world_owner_unobservable`.

```python
def reset() -> None:
    """Clear the stores THIS harness owns. Called before every scenario."""
    _STORE_FILE.unlink(missing_ok=True)        # or: DELETE FROM …, client.purge(), …
```

The hook is the opposite of `seed_state` and the child calls it just before: `reset()` takes the
last scenario's writes OUT, `seed_state` puts this scenario's preconditions IN. Four rules:

- **Take no arguments and return nothing.** It is not preparing a scenario, so it is told nothing
  about one; a signature that demands an argument is refused by name at `Init`. `async def` is
  fine — the child awaits it.
- **Clear only what you own.** Never touch `plurai.world_state` here: the backend opens a fresh
  session and re-seeds it per scenario, so clearing it from the harness fights the seeding
  instead of completing it.
- **Empty, not "reseeded".** Leave the store in the state a first scenario would find; anything
  the scenario needs arrives through `seed_state`.
- **Safe under parallel children.** Up to concurrency × trials copies of this harness run at once
  on one host — fewer when the world is `external` or declares `slots` (below) — and sibling trials
  seed the same scenario. Make anything the harness *creates or
  claims* per child: a listener binds port 0 and the agent gets the bound port through its own
  config (never a fixed port or a hash of an id). A port the agent or its runner listens on itself,
  at a production default included, is handed to it per child through its config, env or argument:
  port 0 where it accepts that, else `free_loopback_port()`, and through a wrapper of the runner's own
  call when the runner passes that config inline (PHASE 1c of the Python template). A working or config dir comes from
  `tempfile.mkdtemp()`; a socket, lock file or throwaway sqlite it creates carries
  `plurai.active_session_id()` in its name. Resources it only *connects to* stay shared: a model
  server, a vector index or corpus, a surface the topology runs for real, an app database that is
  already running (it keeps one schema; the rows in it are scoped to the child).
  - **In a shared store, partition before rewriting.** Key every record `seed_state` creates by
    `plurai.active_session_id()` (or give the child its own namespace) rather than changing a
    scenario value. Only when the store forces a value unique across children, take a per-child
    value, and have `world_snapshot` report the scenario's value.
  - **Reuse only a record this child owns** (keyed by its id), never a sibling's row: two children
    writing one record is silent cross-talk. Create it atomically (an upsert on the child's own key,
    a unique constraint), never check-then-create. A value the store keeps unique never falls
    back to a constant; a constant default for a non-unique field (a display name, a locale) is fine.

**A store no child can own runs in turn, or in copies.** An `external` world runs one attempt at a
time. An `agent` store that cannot be made per-child or partitioned (a fixed-path file, SQLite, a
local index) must declare `world_state.slots`: `1` runs one attempt at a time; `N` gives N identical
copies. With `slots: N` beside either owner, the harness maps its slot to its copy at import —
`SLOT = int(os.environ.get('PLURAI_WORLD_SLOT', '0'))` indexing a `{slot: url-or-path}` table,
raising on an unknown slot — and `reset()` and `world_snapshot()` touch only that copy. The run then
takes `min(--concurrency, N)` attempts at once, trials in turn; smoke and verify children carry no
slot, so they use copy 0.
A store each child holds in its own process needs no `slots`, and declaring it serializes the run.
The variable must appear in a `.py` file beside `simulate.py`, or a run with `slots ≥ 2` is refused
at start.

`world_snapshot()` is the other direction: the child calls it right after `seed_state` (the world
the run started from) and at finalize, and reports what it returns at finalize VERBATIM as that
scenario's `final_world_state` — which is what `entities_of_type()` and `find_entity()` are scored against.

```python
def world_snapshot() -> dict:
    """The world THIS harness owns, as {entity_id: {...}}. Called at finalize."""
    return {f'reading_{row["id"]}': {'_entity_type': 'reading', **row} for row in _STORE_FILE_ROWS()}
```

- **`{entity_id: {...}}`, with `_entity_type` inside each value.** One key per record; the type
  lives in the value, because that is how a policy selects by type.
- **A record keeps one key for the whole run.** A seeded record keeps the key it was seeded under;
  one the agent creates is keyed by the store's own primary key; never a list position or a count. Take no arguments; `async def`
  is fine. Whatever you return must be JSON-serializable — a value that is not is reported as a
  finalize error, never silently replaced.
- **Give a row read from a file a `sha256` of its bytes**, so an in-place edit reads as `modified`
  even when the size is unchanged; nothing adds it for you.
- **A split world is declared, never merged by hand.** `owner: agent` says the mock does not own
  EVERY record this agent reads, not that it owns none. If some of your world really is in the
  mock, declare `world_state.owned_types: [<the entity types your store holds>]` and return ONLY
  your store's rows: the run composes them with the mock's rows of every other type, in one place.
- **A value the snapshot DERIVES by reading a file fails as a typed absence.** A produced file's
  preview whose read raises is `text_preview: None`, and the entity lists it under the reserved
  `_unreadable` key: `{"text_preview": "<ExcClass>: <message>"}` — never the error string in the
  content slot, where a judge reads it as the file's text. The reader ships with the SDK
  (`markitdown[docx,pptx,xlsx,pdf]`); the Init smoke refuses a reached reader missing an extra
  the SDK requires.
- **A produced file's row is one call.** Never write a preview reader of your own:

```python
world[f'output_file:{rel}'] = plurai.file_row(path, entity_type='output_file')
```

  The row carries `name`, `suffix`, `size_bytes`, `sha256` and a `text_preview` of what the file
  stores, and nothing it does not: pptx table cells, grouped ones included, and each spreadsheet
  formula as its formula text plus the value the file saved for it, labelled as stored; a formula the
  file saved no value for is named in `_unevaluated_formulas` (past a limit, `_unevaluated_formulas_count`
  holds the total). The harness never calculates a value.
  A preview longer than the SDK's limit is cut there and the row carries `_preview_truncated`.
  A file handed to the agent takes `preview=False` and carries its `content_type` instead.

- **`{}` is an answer, and a different one from no hook.** An exported hook returning an empty map
  says "this agent left the store empty", which a policy may fail the agent on. No hook says
  nobody looked, and every such policy abstains instead.

Steps 2-5 below are the LangGraph replay, byte-identical to the worked example in
`references/resumed-seed-state.md` — a test pins the two copies together, so fix a defect in
either one and the other follows. That file defines the five names they use (`_app`, the compiled
graph; `_thread_config()`, the one spelling of this scenario's thread id; `_pending_goto`, the
per-thread start-node pin `invoke_agent` spends; `_CONVERSATION_CHANNEL`, the state channel the
graph keeps its conversation in; `convert_to_messages`, LangChain's loader) and numbers the
pitfalls the comments cite. A harness with no graph takes the thread-keyed-store
variant from the same file in place of steps 3-5; the world-state half above is unchanged either
way.

```python
async def seed_state(
    initial_agent_context: dict,
    initial_world_state: dict,
    resume_seed: dict | None = None,
) -> dict | None:
    """Write the scenario's seed data to plurai.world_state, then replay the seed.

    Runs once per scenario after start_session, before turn 1. Honours
    ``initial_world_state['entities']`` as ground truth when present;
    falls back to synthesising from ``initial_agent_context`` otherwise.

    ``resume_seed`` is always passed and is ``None`` for a fresh scenario.
    Every scenario needs the world-state half, so it runs first and neither
    of its branches returns: the replay is the last thing this hook does,
    and a body that returns early above never reaches it.
    """
    entities_block = (initial_world_state or {}).get("entities") or {}

    # Rich path: the scenario carries a full entity history. Apply it
    # verbatim — per-session isolation means there's nothing to clear,
    # and the scenario's commit_id / multi-revision history lives in
    # these entities. Synthesising a fresh __r0 instead would throw away
    # the seeded state and cause the agent's first read to return empty.
    # CAVEAT: if any agent_context field seeds a HEAD entity's inline list
    # (e.g. annotations → revision.records), a bare apply is NOT safe — see
    # "Reconcile agent_context rows that belong on the HEAD entity" below.
    if entities_block:
        plurai.world_state.apply(dict(entities_block))
    else:
        # Fallback: no entities present. Synthesise a single-revision seed
        # from agent_context fields. Field names are agent-specific —
        # substitute YOUR scenario_state_schema's keys.
        synthesised = {}
        for i, record in enumerate(initial_agent_context.get("<your_records_field>", [])):
            synthesised[f"rec_{i}"] = {"_entity_type": "<your_record_type>", **record}
        synthesised["root_HEAD"] = {
            "_entity_type": "<your_root_entity_type>",
            "owner_id": initial_agent_context.get("<your_owner_field>") or "default",
            "metadata": initial_agent_context.get("<your_metadata_field>", []),
            "child_ids": list(synthesised),  # link to seeded child entities
        }
        plurai.world_state.apply(synthesised)

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
```

**Why this order matters.** When `initial_world_state['entities']` is populated, it carries the scenario's multi-revision history — the agent's `commit_id`, the records seeded against each revision, the example_sets the scenario expects. Synthesising a fresh `__r0` revision from `initial_agent_context` instead discards that history; the next tool that reads from world_state sees zero records and the agent's retry loop runs to its iteration budget (LangGraph's `GraphRecursionError`). The verifier at Step 9e (`verify_simulate.py`) catches this with the `seed_state_ignores_world_state` rule.

**`as_node="restore"` names a node the graph has to declare.** `aupdate_state` attributes the
write to that node and then computes what comes next from its outgoing edges, so the name must
belong to a top-level node of the compiled graph whose only edge is `END`: an unknown name is
rejected outright, and a name borrowed from a working node leaves the thread pointing at that
node's successor, which is the mid-step state the tripwire above refuses. A graph with no such
node needs one added — a no-op wired straight to `END`, entered only by this write.

**Reconcile `agent_context` rows that belong on the HEAD entity — don't apply blindly.** If your `scenario_state_schema` marks an `agent_context` field as the scenario's initial rows that are "seeded as the HEAD `<entity>`'s inline `<records>`" (e.g. `agent_context.annotations` → the HEAD `revision`'s inline `records`), the entities path above is **not** safe as a bare `apply(dict(entities_block))`. The initial-state extractor frequently puts those rows ONLY in `agent_context` and leaves the seeded HEAD entity's inline list **empty** — and since the store reads the inline list (not `agent_context`), the agent's first query returns an **empty dataset** and it loops/reverts against data that exists but in the wrong place. This is the silent empty-HEAD defect. Materialise the rows onto the HEAD entity when it carries none of its own, BEFORE applying:

```python
    if entities_block:
        entities = dict(entities_block)
        rows = initial_agent_context.get("<your_agent_context_rows_field>") or []  # e.g. "annotations"
        if rows:
            head_id = next(
                (eid for eid, e in entities.items()
                 if isinstance(e, dict)
                 and e.get("_entity_type") == "<your_head_entity_type>"   # e.g. "revision"
                 and e.get("is_head")),
                None,
            )
            if head_id is not None and not (entities[head_id].get("<inline_records_field>") or []):
                head = dict(entities[head_id])
                # Map each source row to the inline-record shape EXPLICITLY — rename
                # source keys → target keys for YOUR domain (no shared helper exists):
                head["<inline_records_field>"] = [
                    {"id": str(r.get("<id_key>") or ""), "<target_key>": r.get("<source_key>")}
                    for r in rows if isinstance(r, dict)
                ]
                entities[head_id] = head
        plurai.world_state.apply(entities)
```

Only fill when the HEAD's inline list is empty — never clobber inline records the extractor DID seed. Agents with no such `agent_context`→inline-records contract keep the bare `apply(dict(entities_block))`.

**Mirror scalar session fields onto the HEAD entity — FORWARD ONLY.** The same gap exists one
shape down. An `agent_context` scalar the agent reads from its *entity* rather than from its
session settings (a configured policy text, a selected template, an active account id) is delivered in
`agent_context` and absent from the seeded entities — so the first read works (the value is in
session state) and a `revert` "restores" a revision that never carried it, wiping the
scenario's own precondition mid-run. Mirror it onto the HEAD entity, next to the inline-rows
fill above, and only when the HEAD carries none of its own.

**Onto the HEAD entity and nowhere else.** Writing the value onto the ancestor revisions too —
so that "history looks consistent" — fabricates a past the store never had: revision 1 is made
to carry a prompt authored at revision 9, and every diff, `revert` and history question is
then answered from invented data, all of it green. The seeded history is ground truth; the
mirror closes a gap at the HEAD, it does not rewrite what came before.

**A seed that cannot be materialised fails the scenario.** Every branch above either writes the
seed or raises. There is no "degrade to an empty page / an empty context / today's date and
carry on": the scenario's premise is precisely the state the seed was supposed to create, so a
silently degraded seed produces a run that scores green against a precondition that never
happened — indistinguishable, from the outside, from a run that worked. Raise with the field
name in the message; a failed scenario is a fixable seed, a green one over a missing seed is
not even visible. Same rule as `_storage.py`'s fail-closed helpers.

**No `clear()` because there's nothing to clear.** `plurai.world_state.apply` writes under the current session id; the next scenario gets a new session id; old session's state is invisible to the new one. The cross-scenario contamination a module-level singleton would guard against simply doesn't exist in the mock world.

**Sync vs async signature.** `plurai.world_state.apply` is synchronous (it speaks HTTP to the mock service via the SDK's client). Your `seed_state` can be `def` or `async def` — the child subprocess awaits either. Match whichever your surrounding code prefers.

## now: pin the agent's clock reads to the scenario's `current_date`

Every scenario declares its "now" in `initial_agent_context['current_date']` (an ISO 8601 instant, or a date
alone), and the mock answers every time-relative question against it. A read of "now" the agent makes OUTSIDE the
mock (its own prompt builder's `date.today()`, a `mode: real` tool's `datetime.now()`) reads the host's clock
instead, so the agent is told one day and graded on another. `seed_state` pins each such read where it happens: it
rebinds the clock name THAT module imported (`datetime`, `date`, `time`, the `datetime` module under any alias),
never the process's clock, which the SDK, the agent's provider auth (request signing, token expiry) and its traces
all read. The pinned clock keeps ticking from the scenario's time, so durations the agent measures stay real. It
reads `current_date` the way the mock's clock does: a date alone is that day's last second (and stays there, as
the mock's clock does, so it never reads the next day), and a time with no offset is UTC. A naive read (`datetime.now()`, `date.today()`) answers in the declared instant's own offset; an
aware one (`datetime.now(tz)`) converts.

```python
import datetime as _dt
import email.utils
import re
import time as _time
import types


def _scenario_instant(value) -> _dt.datetime:
    """`current_date` read as the backend's run clock reads it: a date ALONE is that day's last second; a time with no
    offset, or a trailing UTC / GMT / Z, is UTC."""
    text = re.sub(r'\s*(?:UTC|GMT|Z)$', '', str(value).strip(), flags=re.IGNORECASE)
    try:
        stamp = _dt.datetime.combine(_dt.date.fromisoformat(text), _dt.time(23, 59, 59))
    except ValueError:
        try:
            stamp = _dt.datetime.fromisoformat(text)
        except ValueError:
            stamp = email.utils.parsedate_to_datetime(text)  # an RFC 2822 date; an unreadable clock raises: the seed fails
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=_dt.UTC)


class _Real(type):  # a real datetime / date still passes isinstance() against the pinned name
    def __instancecheck__(cls, obj):
        return isinstance(obj, cls.__mro__[1])


def pin_now(module: types.ModuleType, agent_context: dict) -> type[_dt.datetime]:
    """Rebind the clock names `module` imported to the scenario's clock, ticking on from it (a date alone stays put). Never process-wide.
    Returns the pinned `datetime` class."""
    declared = next((agent_context[k] for k in ('current_date', 'today') if agent_context.get(k) is not None), None)
    if declared is None:
        raise ValueError('the scenario declares no current_date to pin "now" to')
    anchor, t0 = _scenario_instant(declared), _time.monotonic()
    try:  # a date ALONE stays at its last second, as the backend's run clock does; ticking would read the next day
        _dt.date.fromisoformat(re.sub(r'\s*(?:UTC|GMT|Z)$', '', str(declared).strip(), flags=re.IGNORECASE))
        ticks = False
    except ValueError:
        ticks = True

    class PinnedDatetime(_dt.datetime, metaclass=_Real):
        @classmethod
        def now(cls, tz=None):
            moment = anchor + _dt.timedelta(seconds=_time.monotonic() - t0 if ticks else 0)
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)  # naive: the scenario's own wall clock

        today = classmethod(lambda cls: cls.now())
        utcnow = classmethod(lambda cls: cls.now(_dt.UTC).replace(tzinfo=None))

    class PinnedDate(_dt.date, metaclass=_Real):
        today = classmethod(lambda cls: PinnedDatetime.now().date())

    shims = {
        id(_dt.datetime): PinnedDatetime,
        id(_dt.date): PinnedDate,
        id(_dt): types.SimpleNamespace(**{**vars(_dt), 'datetime': PinnedDatetime, 'date': PinnedDate}),
        id(_time): types.SimpleNamespace(**{**vars(_time), 'time': lambda: PinnedDatetime.now(_dt.UTC).timestamp()}),
    }
    for name, value in list(vars(module).items()):
        if id(value) in shims:
            setattr(module, name, shims[id(value)])
    return PinnedDatetime
```

Call it once per module that reads the clock, from `seed_state` (import the module first if the agent imports it
lazily): `pin_now(importlib.import_module('<agent>.prompts'), initial_agent_context)`. A clock helper of the agent's
own is pinned the same way, in the module that calls it: rebind the helper's name there to a shim over the class
`pin_now` returns, e.g. `mod.time_utils = types.SimpleNamespace(**{**vars(time_utils), 'now': lambda tz=None:
clock.now(tz or <the helper's default zone>)})` with `clock = pin_now(mod, initial_agent_context)`. The
`input_context` record of the date the agent was given then carries the pinned value with no extra step.

## Worked example — a stateful record-managing agent (illustrative only)

> ⚠️ **The names below are placeholders.** `SimRecordStorage`, `records_get_record`, `_records_factory.create_record_storage`, `metadata_fields`, `records`, `owner_id` — all of these are illustrative for an imagined record-managing agent. **Do NOT mechanically copy these names** into a different agent's `simulate.py`. Substitute every identifier with your agent's real types, factory paths, and field names. The *pattern* (the store mocked at its seam with `serves:`, a pass-through swapped in where the seam needs it, `seed_state` applying param 2 verbatim) is universal; the *API surface* is per-agent.

Imagine an agent that exposes a `RecordStorage` interface used by tools that read, modify, and list records (e.g. `update_record`, `compare_records`, `restore_record`). Its `NullRecordStorage` stub returns `get_record(HEAD)` → `metadata_fields=[]`, `records=[]`, and `create_record` returns success but persists nothing. The first tool that calls `get_fresh_state` raises `"Data integrity error: HEAD record exists but has no metadata_fields"`.

Fix: the records service the storage calls is mocked at its `client` seam (`records_get_record`, `records_create_record`, … each `mode: mock` with `serves: [update_record, compare_records, restore_record]` in `plurai.config.yaml`), and `simulate.py` imports `_storage.py` (the `SimRecordStorage` pass-through per Step 8.4 — every method calls one of those seams and converts its dict to the production type, nothing else) BEFORE the agent module imports. The backend seeds the mock world from `initial_world_state["entities"]` (the per-revision history produced by its initial-state extractor) through `start_session`; `seed_state` still applies param 2 verbatim when it is non-empty, and synthesises a single `record` entity from `agent_context["records"]` + `agent_context["metadata_fields"]` only when no entities are declared. Any `mode: real` tool calling `get_record(HEAD)` then gets a `SimRecordStorage` from the patched factory, the seam mock reads the seeded entity, the pass-through converts it to `RecordView`; tools see populated state on first call and the React loop converges. The record history itself — HEAD, revision numbering, snapshots — is the seam mock's contract, pinned by its contract tests, never `_storage.py` code. And because the same world backs `@plurai.mock` handlers, a read-only `list_records` mock and a `mode: real` `create_record` call share a coherent record history.

If you skip this step and a tool reads an unseeded surface, you'll see the symptoms in the **Step 10e symptom-to-cause table** (kept in the main SKILL.md — the table is keyed to this language's adapter shapes).
