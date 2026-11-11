"""Which tool reads back what a write tool writes — declared, never guessed from names or prose.

The declaration is `plurai.config.yaml` `tool_graph`: an edge `{source, target, shared_entity, relationship}` whose
`source` writes (`descriptor.writes_to` non-null) names in `target` a tool that reads what it wrote. A write tool nothing
reads back says so with `target: null`, so its absence of readers is a decision, not an omission. Only an edge to a
READER declares one (:func:`reads_back`): a tool that writes nothing, or a writer whose `reads_from` is the source's
`writes_to`. An edge to another writer (an editor, a lister of what was written) is a dependency, not a read-back.

Two readers of this one relation:

* `verify_config.py` FAILs a writer and a real reader of its writes on different sides of the mock (hard
  `writer_reader_mode_split`: a store is real or simulated as a whole, and a `read_through` overlay is no cure there),
  and warns on a mocked write tool that declares no reader and no `target: null` (`write_tool_reader_undeclared`).
* `bench_manifest.py`'s mock gate joins it with the world's typing (env-gen's `unread_writes`: the types a mocked write
  writes that no mocked tool reads) — a real reader declared over one is the same split, found after the world is built.

Both also read :func:`store_splits`, the same sides at STORE level (`reads_from` / `writes_to` and the C12 `seam`): a
split no edge declares — a write-only mock of a store the agent reads for real — is reported all the same.

A `mode: real` tool named in a seam mock's `serves:` reads through that mock (:func:`served_by_seam`), so it reads the mock
world. :func:`world_served_without_mock` names the real tools the config still routes to the world with no mock under them
(the retired route: an effective `read_strategy: mocked`, or an explicit `write_strategy: mocked`) — `verify_config`'s
`world_served_without_mock` and the mock gate's unwitnessed rows read it.

:func:`local_store_writers` names the writers whose writes land only in the declared per-conversation local store
(`world_state.owner: agent`, `owned_types`) AND in a store `world_state.confirmation` names (:func:`confirmed_store`):
not live writes, so `host_effects` never asks a grant for them. Every other store is simulated as a whole: its writes
and its reads are mocked unless the user confirmed it.

Stdlib only: it runs under the tooling interpreter and under the verifier's.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, NamedTuple

import agent_fakes

#: Where a tool's reads or writes land. A `mode: real` tool reaches the mock's world through a seam mock that names it in
#: `serves:`, or through the adapter its descriptor DECLARES (`read_strategy` / `write_strategy: mocked`, the retired
#: route legacy configs still carry), never through the default a non-empty `reads_from` / `writes_to` implies: that
#: default says nothing about whether the harness patched anything in. An `observe` write runs for real AND is captured
#: into the world, so either side reads it. A `mode: real` reader that declares `read_through: {via: [...]}` reads the
#: real store with the mock world overlaid at those accessors, so it sees both.
MOCK_WORLD = 'the mock world'
REAL_STORE = 'the real store'
BOTH = 'both'

_WRITE_SIDE = {'mock': MOCK_WORLD, 'observe': BOTH}
_READ_SIDE = {'mock': MOCK_WORLD, 'observe': REAL_STORE}


class Edge(NamedTuple):
    """One declared writer→reader edge. `reader` is None when the writer declared that nothing reads it back."""

    writer: str
    reader: str | None
    entity: str


def _tools(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tools = config.get('tools') if isinstance(config, dict) else None
    return {str(name): entry for name, entry in tools.items() if isinstance(entry, dict)} if isinstance(tools, dict) else {}


def _descriptor(entry: dict[str, Any]) -> dict[str, Any]:
    descriptor = entry.get('descriptor')
    return descriptor if isinstance(descriptor, dict) else {}


def mode(entry: dict[str, Any]) -> str:
    """The tool's mode; absent is `mock`, the SDK's default."""
    return str(entry.get('mode') or 'mock')


def writes(entry: dict[str, Any]) -> bool:
    """`descriptor.writes_to` is set: the tool changes a store."""
    value = _descriptor(entry).get('writes_to')
    return isinstance(value, str) and value.strip().lower() not in ('', 'none', 'null')


def write_side(entry: dict[str, Any]) -> str:
    if mode(entry) == 'real':
        return MOCK_WORLD if _descriptor(entry).get('write_strategy') == 'mocked' else REAL_STORE
    return _WRITE_SIDE.get(mode(entry), MOCK_WORLD)


def read_through_via(entry: dict[str, Any]) -> list[str]:
    """The store accessors a `read_through: {via: [...]}` declares (the harness rebinds each to overlay the mock world on
    the real store); empty when the tool declares none or declares it malformed (`verify_config` FAILs that)."""
    block = entry.get('read_through')
    via = block.get('via') if isinstance(block, dict) else None
    return [str(v) for v in via if isinstance(v, str) and v.strip()] if isinstance(via, list) else []


def served_by_seam(config: dict[str, Any]) -> frozenset[str]:
    """The tools a `mode: mock` entry names in `serves:` — the real tools that call through that seam mock."""
    return frozenset(
        str(name).strip() for entry in _tools(config).values() if mode(entry) == 'mock' and isinstance(entry.get('serves'), list)
        for name in entry['serves'] if isinstance(name, str) and name.strip()
    )


def read_side(entry: dict[str, Any], *, served: bool) -> str:
    """Where the tool's reads look. `served`: the tool is named in a seam mock's `serves:` (:func:`served_by_seam`), so a
    real one reads through that mock."""
    if mode(entry) == 'real':
        if served or _descriptor(entry).get('read_strategy') == 'mocked':
            return MOCK_WORLD
        return BOTH if read_through_via(entry) else REAL_STORE
    return _READ_SIDE.get(mode(entry), MOCK_WORLD)


def _effective_read_mocked(entry: dict[str, Any]) -> bool:
    """`ToolDescriptor.effective_read_strategy == 'mocked'`: explicit, or the default a non-empty `reads_from` implies."""
    explicit = _descriptor(entry).get('read_strategy')
    if isinstance(explicit, str) and explicit:
        return explicit == 'mocked'
    return bool(_store(_descriptor(entry).get('reads_from')))


def world_served_without_mock(config: dict[str, Any]) -> list[str]:
    """The `mode: real` tools served by harness-authored storage: an effective `read_strategy: mocked` or an EXPLICIT
    `write_strategy: mocked`, and named in no seam mock's `serves:`. The write side keys on the explicit value only: the
    default a non-null `writes_to` implies is how a harness-owned world's own writes read, and is no claim of a fake. A
    write into the declared per-conversation local store (:func:`local_store_writers`) is a real engine the harness runs
    (route (a)), whatever its label says, so it is no claim of a fake either; nor is a reader that declares `read_through`:
    it reads its real store with the mock world overlaid at the declared accessors (:func:`read_side` `BOTH`)."""
    served, local = served_by_seam(config), set(local_store_writers(config))
    return sorted(
        name for name, entry in _tools(config).items()
        if mode(entry) == 'real' and name not in served
        and ((_effective_read_mocked(entry) and not read_through_via(entry))
             or (_descriptor(entry).get('write_strategy') == 'mocked' and name not in local))
    )


def _store(value: Any) -> str:
    return ' '.join(str(value).split()).casefold() if isinstance(value, str) else ''


def reads_back(writer: dict[str, Any], target: dict[str, Any]) -> bool:
    """`target` reads what `writer` writes: it writes nothing (a read tool), or its `reads_from` names the very store the
    writer's `writes_to` names. Store names are prose, so only the same store reads as covered — never a guess."""
    if not writes(target):
        return True
    return bool(_store(_descriptor(target).get('reads_from'))) and _store(_descriptor(target).get('reads_from')) == _store(_descriptor(writer).get('writes_to'))


def writer_edges(config: dict[str, Any]) -> list[Edge]:
    """Every declared edge out of a write tool: to a declared tool other than itself, or `target: null`. An edge naming
    a tool the config does not declare declares nothing."""
    tools = _tools(config)
    graph = config.get('tool_graph') if isinstance(config, dict) else None
    edges: list[Edge] = []
    for item in graph if isinstance(graph, list) else []:
        if not isinstance(item, dict) or 'target' not in item:
            continue
        writer, reader = str(item.get('source') or ''), item.get('target')
        if writer not in tools or not writes(tools[writer]):
            continue
        if reader is None or (isinstance(reader, str) and reader in tools and reader != writer):
            edges.append(Edge(writer, reader, str(item.get('shared_entity') or '')))
    return edges


def seam_readers(config: dict[str, Any], writer: str, entity_types: list[str]) -> frozenset[str]:
    """The OTHER mocked tools a declared reader of `writer`'s write of one of `entity_types` reads through: for each
    `tool_graph` edge out of it whose `shared_entity` is one of them and whose target :func:`reads_back`, the target when it
    is `mode: mock`, else each mock naming it in `serves:`. The writer itself is never one: a call log cannot tell its read
    from another write."""
    tools = _tools(config)
    targets = {e.reader for e in writer_edges(config) if e.writer == writer and e.reader and e.entity in entity_types
               and reads_back(tools[writer], tools[e.reader])}
    found = {t for t in targets if mode(tools[t]) == 'mock'} | {
        name for name, entry in tools.items() if mode(entry) == 'mock' and isinstance(entry.get('serves'), list)
        and targets & {str(s).strip() for s in entry['serves'] if isinstance(s, str)}}
    return frozenset(found - {writer})


def _looked(side: str, through_is_real: bool) -> str:
    """A `read_through` reader (`BOTH`) read as the real store when `through_is_real`: an overlay is no cure for a split."""
    return REAL_STORE if through_is_real and side == BOTH else side


def mode_splits(config: dict[str, Any], *, through_is_real: bool = False) -> list[tuple[Edge, str, str]]:
    """(edge, where the write lands, where the read looks) for each declared reader on the other side of the mock."""
    tools = _tools(config)
    served, local = served_by_seam(config), set(local_store_writers(config))
    splits = []
    for edge in writer_edges(config):
        landed = write_side(tools[edge.writer])
        if edge.reader is None or (landed == REAL_STORE and edge.writer in local
                                   and confirmed_store(config, _descriptor(tools[edge.reader]).get('reads_from'))):
            continue  # a real write into a confirmed store, read back from it: both meet in that one store
        looked = _looked(read_side(tools[edge.reader], served=edge.reader in served), through_is_real)
        if landed != BOTH and looked not in (landed, BOTH):
            splits.append((edge, landed, looked))
    return splits


def undeclared_writers(config: dict[str, Any]) -> dict[str, list[str]]:
    """The mocked write tools with no declared READER and no `target: null` -> the writers their edges do name (empty when
    they name none): an edge to another writer declares no read-back (:func:`reads_back`)."""
    tools = _tools(config)
    edges = writer_edges(config)
    declared = {e.writer for e in edges if e.reader is None or reads_back(tools[e.writer], tools[e.reader])}
    return {
        name: sorted({str(e.reader) for e in edges if e.writer == name and e.reader is not None})
        for name, entry in sorted(tools.items()) if mode(entry) == 'mock' and writes(entry) and name not in declared
    }


def real_readers(config: dict[str, Any], writer: str, entity_types: list[str]) -> list[str]:
    """The tools declared to read what `writer` writes — an edge from it, or an edge whose `shared_entity` is one of
    `entity_types` — that read the real store. Env-gen's `unread_writes` names the types no MOCKED tool reads; a real
    reader of one is the split, decided from the world's typing and the scan's declaration together."""
    tools = _tools(config)
    graph = config.get('tool_graph') if isinstance(config, dict) else None
    found = {
        str(item['target']) for item in (graph if isinstance(graph, list) else [])
        if isinstance(item, dict) and isinstance(item.get('target'), str) and item['target'] in tools
        and (item.get('source') == writer or str(item.get('shared_entity') or '') in entity_types)
    }
    served = served_by_seam(config)
    return sorted(name for name in found if name != writer and read_side(tools[name], served=name in served) == REAL_STORE)


#: C12 seams whose mock stands in for a store the agent's own code reads through (its client object, its rows).
_STORE_SEAMS = frozenset({'client', 'entity'})


def seam(entry: dict[str, Any]) -> str | None:
    """C12 `tools.<name>.seam`: where the mock cuts in (`transport | client | entity | tool_body`); None when undeclared."""
    value = entry.get('seam')
    return value if isinstance(value, str) else None


def store_splits(config: dict[str, Any], *, through_is_real: bool = False) -> list[tuple[str, str, list[str]]]:
    """(writer, store, real readers) for each write landing in the mock world whose store a read on the other side of the
    mock reaches, whatever `tool_graph` says: a tool whose `reads_from` names the store and reads the real one, or, for a
    `client` / `entity` seam (a store the agent's own code also reads through), no tool that reads it through the mock
    world at all (a mocked reader, a served real one, or a real one that declares `read_through`) — found by the store's
    name, or declared by a `tool_graph` edge from the writer (store names are prose; the edge is the declaration).
    A `target: null` edge declares nothing here: it says no TOOL reads the write back, not that the store is unread."""
    tools = _tools(config)
    served = served_by_seam(config)
    splits = []
    for name, entry in sorted(tools.items()):
        if not writes(entry) or write_side(entry) != MOCK_WORLD:
            continue
        readers = {n: _looked(side, through_is_real) for n, side in store_readers(config, name).items()}
        real = sorted(n for n, side in readers.items() if n != name and side == REAL_STORE)
        declared = {_looked(read_side(tools[e.reader], served=e.reader in served), through_is_real) for e in writer_edges(config)
                    if e.writer == name and e.reader and reads_back(entry, tools[e.reader])}
        if real or (seam(entry) in _STORE_SEAMS and not {MOCK_WORLD, BOTH} & (set(readers.values()) | declared)):
            splits.append((name, str(_descriptor(entry).get('writes_to')), real))
    return splits


def store_readers(config: dict[str, Any], writer: str) -> dict[str, str]:
    """Every tool whose `reads_from` names the very store `writer`'s `writes_to` names (itself included) -> its read side."""
    tools = _tools(config)
    served = served_by_seam(config)
    store = _store(_descriptor(tools.get(writer, {})).get('writes_to'))
    return {n: read_side(e, served=n in served) for n, e in tools.items() if store and _store(_descriptor(e).get('reads_from')) == store}


#: The world owner whose store is the harness's own: kept beside the agent's process and returned to the same state before
#: every conversation by the harness's `reset()` (the runner holds an `agent` world without one). A write into it is a
#: write into a per-conversation local store, never a live write.
LOCAL_STORE_OWNER = 'agent'


def local_store_types(config: dict[str, Any]) -> frozenset[str] | None:
    """The entity types the declared per-conversation local store holds: `world_state.owned_types` beside
    `world_state.owner: agent`, or None (every type) when that owner declares no `owned_types`. An empty set when no
    such store is declared: another owner, an `owned_types` that is not a list of names (`verify_config` warns), or
    `shared_world_ok: true` (the operator kept that world shared across conversations, so nothing resets it)."""
    world_state = config.get('world_state') if isinstance(config, dict) else None
    if not isinstance(world_state, dict) or str(world_state.get('owner') or '').strip().lower() != LOCAL_STORE_OWNER:
        return frozenset()
    if world_state.get('shared_world_ok') is True:
        return frozenset()
    owned = world_state.get('owned_types')
    if owned is None:
        return None
    if not isinstance(owned, list) or not all(isinstance(t, str) and t.strip() for t in owned):
        return frozenset()
    return frozenset(t.strip() for t in owned)


def written_types(config: dict[str, Any], writer: str) -> frozenset[str]:
    """The entity types `writer` declares it writes: its `post_conditions[].entity_type` and the `shared_entity` of every
    `tool_graph` edge out of it."""
    post = _descriptor(_tools(config).get(writer, {})).get('post_conditions')
    graph = config.get('tool_graph') if isinstance(config, dict) else None
    types = [c.get('entity_type') for c in post if isinstance(c, dict)] if isinstance(post, list) else []
    types += [e.get('shared_entity') for e in graph if isinstance(e, dict) and e.get('source') == writer] if isinstance(graph, list) else []
    return frozenset(t.strip() for t in types if isinstance(t, str) and t.strip())


def local_store_writers(config: dict[str, Any]) -> list[str]:
    """The write tools whose every declared write lands in the per-conversation local store (:func:`local_store_types`):
    each type they write (:func:`written_types`, never empty) is one that store holds, AND their `writes_to` names a
    confirmed store (:func:`confirmed_store`), so one confirmation never covers another store's types. Such a write has
    no effect outside the run, so it is not a live write: it needs no grant, and the product's own write runs against
    that store, with `world_snapshot()` observing it. Any other writer is not one: its store is mocked by default."""
    held = local_store_types(config)
    if held is not None and not held:
        return []
    return sorted(
        name for name, entry in _tools(config).items()
        if writes(entry) and (types := written_types(config, name)) and (held is None or types <= held)
        and confirmed_store(config, _descriptor(entry).get('writes_to'))
    )


#: `world_state.confirmation[].answer`: the store is one the harness creates per conversation, or a disposable copy the
#: user confirmed may be written and wiped before every conversation (:data:`STORE_ANSWERS`: its reads AND writes run
#: real); or the agent's own bundled read-only files (`agent_bundled`: files shipped with the agent, its reads run real,
#: never a write). Nothing else makes a store's access real.
STORE_ANSWERS = ('harness_created', 'disposable_copy')
CONFIRMATION_ANSWERS = (*STORE_ANSWERS, 'agent_bundled')
#: How a `harness_created` target says where the store lives: in the harness's process, a folder it makes, its run dir.
HARNESS_CREATED_PREFIXES = ('in-process:', 'mkdtemp:', 'run-dir:')
#: How an `agent_bundled` target names the shipped files: `bundled: <path>`, a path, never a connection string.
BUNDLED_PREFIX = 'bundled:'


def target_identifier(target: str) -> str:
    """The part of a confirmed `target` a tool's `reads_from` / `writes_to` prose must contain: casefolded, its
    harness-created prefix dropped."""
    text = _store(target)
    for prefix in (*HARNESS_CREATED_PREFIXES, BUNDLED_PREFIX):
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return text


def confirmation_errors(config: dict[str, Any]) -> list[str]:
    """Why each `world_state.confirmation` entry confirms nothing (`verify_config` FAILs them); empty when all hold."""
    world_state = config.get('world_state') if isinstance(config, dict) else None
    block = world_state.get('confirmation') if isinstance(world_state, dict) else None
    if block is None:
        return []
    if not isinstance(block, list):
        return [f'`world_state.confirmation` is a {type(block).__name__}, not a list of `{{answer, target}}` entries']
    errors = []
    for i, entry in enumerate(block):
        answer = entry.get('answer') if isinstance(entry, dict) else None
        target = entry.get('target') if isinstance(entry, dict) else None
        if answer == agent_fakes.ANSWER:  # routes one endpoint to the agent's own double; confirms no store (agent_fakes.py)
            errors += agent_fakes.shape_errors(i, entry, config)
        elif answer not in CONFIRMATION_ANSWERS:
            errors.append(f'entry {i}: `answer` is {answer!r}, not one of {", ".join((*CONFIRMATION_ANSWERS, agent_fakes.ANSWER))}')
        elif not isinstance(target, str) or not target_identifier(target):
            errors.append(f'entry {i}: `target` names no store')
        elif answer == 'harness_created' and (not _store(target).startswith(HARNESS_CREATED_PREFIXES) or '://' in target):
            errors.append(f'entry {i}: a `harness_created` target is `in-process: <object>`, `mkdtemp: <prefix>` or '
                          f'`run-dir: <path>`, never a connection string ({target!r})')
        elif (answer == 'agent_bundled') != _store(target).startswith(BUNDLED_PREFIX) or (
                answer == 'agent_bundled' and ('://' in target or len(target_identifier(target).split()) != 1
                                               or '/' not in target_identifier(target).strip('/'))):
            errors.append(f'entry {i}: an `agent_bundled` target, and only one, is `bundled: <path>` (one path the agent ships, '
                          f'never a connection string) ({target!r})')
    return errors


#: The ask hook's verbatim record of every answered question (`hooks/ask_answered.py` ASK_ANSWERS), in `<workdir>/artifacts`.
ASK_ANSWERS = '.ask_answers.jsonl'
#: A store question's answers ("Yes — run it live" / "No — simulate it", plural and "None — simulate all" too), and the stem
#: every store question carries.
_LIVE_YES = re.compile(r'^\W*yes\W+run\b.*\blive\b', re.IGNORECASE)
_SIMULATE_NO = re.compile(r'^\W*(?:no|none)\W+simulate\b', re.IGNORECASE)
#: The only Yes that backs a store: one that runs every store the question named live, and nothing more. A partial Yes
#: ("run some live", "run them live except …", a picked store) backs none; that store's own question decides.
_LIVE_ONE = re.compile(r'^\W*yes\W+run\s+(?:it|them)\s+live\W*$', re.IGNORECASE)
_STORE_QUESTION = re.compile(r'disposable cop', re.IGNORECASE)
#: The option label a question marks as its recommendation. A store question recommends simulating, always: a Yes picked
#: where live was the recommended option is the question's steer, not the user's confirmation, so it backs nothing.
_RECOMMENDED = re.compile(r'\(recommended\)', re.IGNORECASE)
_CODE_SPAN = re.compile(r'`([^`]+)`')


def recorded_answers(workdir: Path) -> list[dict[str, Any]]:
    """The `{question, answer}` rows the ask hook recorded for ``workdir``, oldest first; `[]` when none."""
    try:
        text = (workdir / 'artifacts' / ASK_ANSWERS).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get('question'), str) and isinstance(row.get('answer'), str):
            rows.append(row)
    return rows


def unbacked_disposable_copies(config: dict[str, Any], answers: list[dict[str, Any]]) -> list[str]:
    """The `disposable_copy` targets no recorded user answer backs. ``answers`` are the ask hook's verbatim rows, oldest
    first. Only a store question counts (its stem, answered Yes-run-live or No-simulate) that names the target as a whole
    code span; the latest such answer decides, and only a whole Yes ("run it/them live", :data:`_LIVE_ONE`) backs the
    entry, never one the question marked (Recommended). A partial Yes ("run some live"), a map approval, a blocker's
    answer or a question about another store backs nothing; a later No or partial Yes retires an earlier Yes."""
    world_state = config.get('world_state') if isinstance(config, dict) else None
    block = world_state.get('confirmation') if isinstance(world_state, dict) else None
    unbacked = []
    for entry in block if isinstance(block, list) else ():
        if not isinstance(entry, dict) or entry.get('answer') != 'disposable_copy' or not isinstance(entry.get('target'), str):
            continue
        ident = target_identifier(entry['target'])
        store_answers = [
            row['answer'] for row in answers
            if _STORE_QUESTION.search(row['question']) and (_LIVE_YES.search(row['answer']) or _SIMULATE_NO.search(row['answer']))
            and ident in {_store(span) for span in _CODE_SPAN.findall(row['question'])}
        ]
        if not (ident and store_answers and _LIVE_ONE.search(store_answers[-1]) and not _RECOMMENDED.search(store_answers[-1])):
            unbacked.append(entry['target'])
    return unbacked


def confirmed_targets(config: dict[str, Any]) -> dict[str, str]:
    """Each valid `world_state.confirmation` entry's :func:`target_identifier` -> its answer, an `agent_fake` entry aside
    (it routes an endpoint, never confirms a store). None when any entry is invalid: a malformed block confirms nothing."""
    world_state = config.get('world_state') if isinstance(config, dict) else None
    block = world_state.get('confirmation') if isinstance(world_state, dict) else None
    if not isinstance(block, list) or confirmation_errors(config):
        return {}
    return {target_identifier(e['target']): e['answer'] for e in block if e['answer'] != agent_fakes.ANSWER}


def confirmed_store(config: dict[str, Any], prose: Any, *, read: bool = False) -> bool:
    """`prose` (a `reads_from` / `writes_to`) names a confirmed store: it contains a confirmed target's identifier. An
    `agent_bundled` target counts only for a `read`, and only when the `reads_from` IS that path, or names nothing beside
    it but the harness's in-process store (:func:`bundled_read`):
    shipped files are read real, and a write to them is a live write."""
    text = _store(prose)
    answers = CONFIRMATION_ANSWERS if read else STORE_ANSWERS
    return bool(text) and (any(
        text in (ident, f'{BUNDLED_PREFIX} {ident}') if answer == 'agent_bundled' else ident in text
        for ident, answer in confirmed_targets(config).items() if answer in answers) or (read and bundled_read(config, prose)))


def declared_write_only(config: dict[str, Any], writer: str) -> bool:
    """`writer` declares `target: null`: nothing reads back what it writes."""
    return any(edge.writer == writer and edge.reader is None for edge in writer_edges(config))


def _names_store(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() not in ('', 'none', 'null')


def unconfirmed_real_readers(config: dict[str, Any], corpus: frozenset[str]) -> list[str]:
    """The `mode: real` tools that read a store outside the mock world (an unset `read_strategy` reads the real store, a
    `read_through` reads it too) whose `reads_from` names no confirmed store, `corpus` tools aside (they stay on the corpus
    route): a record store's reads are mocked unless the user confirmed it. The agent's own bundled files
    (`agent_bundled`) are read real."""
    tools, served = _tools(config), served_by_seam(config)
    return sorted(
        name for name, entry in tools.items()
        if mode(entry) == 'real' and name not in corpus and _names_store(reads := _descriptor(entry).get('reads_from'))
        and read_side(entry, served=name in served) != MOCK_WORLD and not confirmed_store(config, reads, read=True)
    )


def store_tools(config: dict[str, Any]) -> list[str]:
    """The tools that name a store: a mocked one that reads or writes one, or a real one that reads one other than the
    agent's own bundled files (a write-free `agent_bundled` reader reaches no endpoint) or the stores the harness creates
    (a real tool whose reads, and writes when it writes, are all :func:`harness_only` reaches no endpoint)."""
    return sorted(
        name for name, entry in _tools(config).items()
        if (mode(entry) == 'mock' and (_names_store(_descriptor(entry).get('reads_from')) or writes(entry)))
        or (mode(entry) == 'real' and _names_store(reads := _descriptor(entry).get('reads_from'))
            and (writes(entry) or not _bundled_only(config, reads))
            and not (harness_only(config, reads) and (not writes(entry) or harness_only(config, _descriptor(entry).get('writes_to')))))
    )


def _bundled_only(config: dict[str, Any], reads: Any) -> bool:
    """`reads` is a reader of the agent's own bundled files (:func:`bundled_read`): it reaches no endpoint."""
    return bundled_read(config, reads)


def _part_answers(config: dict[str, Any], prose: Any) -> list[str | None]:
    """The confirmation answer each `;`-separated part of `prose` (a `reads_from` / `writes_to`) names: `agent_bundled` when
    the part IS a bundled path, `harness_created` when it names a harness-created target, else None."""
    targets = confirmed_targets(config)

    def answer(part: str) -> str | None:
        part = part.strip()
        bundled = [i for i, a in targets.items() if a == 'agent_bundled' and part in (i, f'{BUNDLED_PREFIX} {i}')]
        return 'agent_bundled' if bundled else next((a for i, a in targets.items() if a == 'harness_created' and i in part), None)
    return [answer(part) for part in _store(prose).split(';') if part.strip()]


def bundled_read(config: dict[str, Any], reads: Any) -> bool:
    """`reads` (a `reads_from`) touches only the agent's own bundled files and the harness's in-process store: each of its
    `;`-separated parts IS an `agent_bundled` path, or names a `harness_created` target, and at least one is bundled."""
    answers = _part_answers(config, reads)
    return bool(answers) and None not in answers and 'agent_bundled' in answers


def harness_only(config: dict[str, Any], prose: Any) -> bool:
    """`prose` (a `reads_from` / `writes_to`) names only stores the harness creates: each of its `;`-separated parts IS a
    `harness_created` target (in-process, a folder it makes, its run dir), with or without its prefix, and none is a
    connection string. A part that only contains a target may name an endpoint beside it."""
    harness = [i for i, a in confirmed_targets(config).items() if a == 'harness_created']
    parts = [part.strip() for part in _store(prose).split(';') if part.strip()]
    return bool(parts) and '://' not in _store(prose) and all(
        any(part == i or part in (f'{prefix} {i}' for prefix in HARNESS_CREATED_PREFIXES) for i in harness) for part in parts)


def corpus_tools(workdir: Path, topology: dict[str, Any] | None) -> frozenset[str]:
    """The retrieval tools on the corpus route: `.knowledge_bases` tools, the map's `grounding.surfaces` (a surface's first
    word is its tool) and the names of its `rag` entries."""
    try:
        bases = json.loads((workdir / '.knowledge_bases').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        bases = None
    grounding = topology.get('grounding') if isinstance(topology, dict) else None
    surfaces = grounding.get('surfaces') if isinstance(grounding, dict) else None
    rag = topology.get('rag') if isinstance(topology, dict) else None
    names = {str(b['tool']) for b in bases if isinstance(b, dict) and b.get('tool')} if isinstance(bases, list) else set()
    names |= {str(x).split()[0] for x in surfaces if str(x).strip()} if isinstance(surfaces, list) else set()
    names |= {str(r['name']) for r in rag if isinstance(r, dict) and r.get('name')} if isinstance(rag, list) else set()
    return frozenset(names)


def unconfirmed_store_tools(config: dict[str, Any], corpus: frozenset[str]) -> list[str]:
    """:func:`store_tools`, `corpus` aside, whose `reads_from` or `writes_to` names a store no confirmation covers (an
    `agent_bundled` one covers a read only): the stores the run simulates."""
    tools = _tools(config)

    def uncovered(name: str) -> bool:
        descriptor = _descriptor(tools[name])
        reads = _names_store(descriptor.get('reads_from')) and not confirmed_store(config, descriptor.get('reads_from'), read=True)
        return reads or (writes(tools[name]) and not confirmed_store(config, descriptor.get('writes_to')))
    return [name for name in store_tools(config) if name not in corpus and uncovered(name)]


def _path_parts(value: Any) -> tuple[str, ...]:
    return tuple(p for p in _store(value).replace('\\', '/').split('/') if p not in ('', '.', '..'))


def _corpus_sources(topology: dict[str, Any] | None) -> list[tuple[str, ...]]:
    """The folders the map types as corpus sources (`grounding.sources`): documents the agent reads, so its data."""
    grounding = topology.get('grounding') if isinstance(topology, dict) else None
    sources = grounding.get('sources') if isinstance(grounding, dict) else None
    return [parts for parts in map(_path_parts, sources) if parts] if isinstance(sources, list) else []


def _in_source(parts: tuple[str, ...], source: tuple[str, ...]) -> bool:
    """`parts` is the `source` folder or a path inside it, whatever base it is written from (its components run in it)."""
    return any(parts[i:i + len(source)] == source for i in range(len(parts) - len(source) + 1))


def bundled_data_targets(config: dict[str, Any], topology: dict[str, Any] | None) -> list[str]:
    """The `agent_bundled` targets that are, sit inside or hold a corpus source: never files the agent ships."""
    sources = _corpus_sources(topology)
    return sorted(
        ident for ident, answer in confirmed_targets(config).items() if answer == 'agent_bundled'
        and any(_in_source(parts := _path_parts(ident), s) or s[:len(parts)] == parts for s in sources))


def corpus_source_readers(config: dict[str, Any], topology: dict[str, Any] | None, corpus: frozenset[str]) -> list[str]:
    """The `mode: real` tools, `corpus` aside, whose `reads_from` names a path in a corpus source and that read it for
    real: they read the agent's data, which no confirmation beside it (an in-process store's included) makes real."""
    sources, served = _corpus_sources(topology), served_by_seam(config)

    def tokens(prose: Any) -> list[tuple[str, ...]]:
        return [_path_parts(t.strip('.,:()\'"`')) for t in _store(prose).replace(';', ' ').replace(',', ' ').split()]
    return sorted(
        name for name, entry in _tools(config).items()
        if mode(entry) == 'real' and name not in corpus and read_side(entry, served=name in served) != MOCK_WORLD
        and any(_in_source(t, s) for t in tokens(_descriptor(entry).get('reads_from')) for s in sources))


def corpus_endpoint_env(topology: dict[str, Any] | None, corpus: frozenset[str]) -> frozenset[str]:
    """The endpoint variables the map's `grounding.endpoint_env` types as the corpus index the retrieval tools read
    (a search index, a vector store): read-only and on the corpus route, so never pinned to the mock. Empty without a
    corpus tool."""
    grounding = topology.get('grounding') if isinstance(topology, dict) else None
    names = grounding.get('endpoint_env') if isinstance(grounding, dict) else None
    return frozenset(str(n).strip() for n in names if str(n).strip()) if corpus and isinstance(names, list) else frozenset()


def endpoint_tools(topology: dict[str, Any] | None, corpus: frozenset[str]) -> dict[str, frozenset[str]]:
    """The map's `grounding.endpoint_tools` {corpus endpoint variable: the tools that read through it}, for the variables
    :func:`corpus_endpoint_env` names and listed as names only; any other key or value types nothing."""
    grounding = topology.get('grounding') if isinstance(topology, dict) else None
    block = grounding.get('endpoint_tools') if isinstance(grounding, dict) else None
    variables = corpus_endpoint_env(topology, corpus)
    return {str(var).strip(): frozenset(str(t).strip() for t in names if isinstance(t, str) and t.strip())
            for var, names in block.items() if str(var).strip() in variables and isinstance(names, list)} if isinstance(block, dict) else {}


def unnamed_live_reads(config: dict[str, Any], topology: dict[str, Any] | None, corpus: frozenset[str],
                       answers: list[dict[str, Any]]) -> dict[str, list[str]]:
    """{corpus endpoint variable: the live corpus reads its store question leaves unnamed}. A `grounding.endpoint_env`
    variable a store question names (a whole word) is that store's endpoint too. The latest such question decides: its
    store runs live only on a plain "Yes — run it/them live" (never one marked Recommended) to a question that names a
    backed `disposable_copy` target as a code span; any other answer (No, None, a store label, "run some live", free text)
    leaves it simulated, while every corpus tool that reads for real still reaches it, so that question names each of
    them as a code span: reference content may stay live beside a simulated store, told. Those are the live
    corpus reads :func:`endpoint_tools` types on that variable plus those it types on none; a variable it does not list
    demands every live corpus read."""
    live = _live_corpus_reads(config, corpus)
    typed = endpoint_tools(topology, corpus)
    attributed = frozenset().union(*typed.values())
    unbacked = {target_identifier(t) for t in unbacked_disposable_copies(config, answers)}
    backed = {i for i, a in confirmed_targets(config).items() if a == 'disposable_copy' and i not in unbacked}
    unnamed = {}
    for var in sorted(corpus_endpoint_env(topology, corpus)):
        rows = [row for row in answers if _STORE_QUESTION.search(row['question'])
                and re.search(rf'(?<![\w-]){re.escape(var)}(?![\w-])', row['question'])]
        if not rows:
            continue
        question, answer = rows[-1]['question'], rows[-1]['answer']
        spans = {_store(span) for span in _CODE_SPAN.findall(question)}
        if _LIVE_ONE.search(answer) and backed & spans:  # a Yes marked (Recommended) backs no target
            continue
        named = {span.strip() for span in _CODE_SPAN.findall(question)}
        reads = [n for n in live if n in typed[var] or n not in attributed] if var in typed else live
        if missing := [n for n in reads if n not in named]:
            unnamed[var] = missing
    return unnamed


def _live_corpus_reads(config: dict[str, Any], corpus: frozenset[str]) -> list[str]:
    """The corpus tools on the config that read for real (a seam's `serves:` aside: their corpus read never goes through it)."""
    tools = _tools(config)
    return [n for n in sorted(corpus) if n in tools and read_side(tools[n], served=False) != MOCK_WORLD]


def index_over_simulated(config: dict[str, Any], topology: dict[str, Any] | None, corpus: frozenset[str],
                         workdir: Path) -> dict[str, str]:
    """{tool: why it is part of a simulated store, not reference content}. The map's `grounding.records_of` types what each
    retrieval surface returns: the `world_state.entity_types` type whose rows its hits are, or null when no world type holds
    it (reference content: a playbook, regulations, runbooks). When the run simulates a store (:func:`unconfirmed_store_tools`
    non-empty), each live corpus read must be typed there, and one typed with a declared world type is an index over records
    each scenario seeds: part of that store, mocked with it. A `.knowledge_bases` tool the config mocks keeps
    a corpus scenario generation plants questions from, which its seeded answers never hold."""
    tools = _tools(config)
    grounding = topology.get('grounding') if isinstance(topology, dict) else None
    records_of = grounding.get('records_of') if isinstance(grounding, dict) else None
    records_of = records_of if isinstance(records_of, dict) else {}
    world_state = config.get('world_state') if isinstance(config, dict) else None
    types = world_state.get('entity_types') if isinstance(world_state, dict) else None
    types = set(types) if isinstance(types, dict) else set()
    found: dict[str, str] = {}
    if unconfirmed_store_tools(config, corpus):
        for name in _live_corpus_reads(config, corpus):
            value = records_of.get(name, ...)
            if value is ...:
                found[name] = 'undeclared'
            elif value is not None and not isinstance(value, str):
                found[name] = ('types its hits as one entity type or null, not a list or mapping: a surface returning two row '
                               'kinds is typed by the records it returns, so name the main one or split the surface')
            elif value is not None and value not in types:
                found[name] = f'`{value}` is no declared `world_state.entity_types` type'
            elif value is not None:
                found[name] = f'records of `{value}`'
    try:
        bases = json.loads((workdir / '.knowledge_bases').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        bases = None
    for base in bases if isinstance(bases, list) else ():
        name = str(base.get('tool') or '') if isinstance(base, dict) else ''
        if name in tools and mode(tools[name]) == 'mock':
            found.setdefault(name, 'mocked, yet `.knowledge_bases` keeps its corpus')
    return found
