#!/usr/bin/env python3
"""Survey a trace artifact's record shapes, and report EVERY gap in a draft adapter at once.

Two modes, one script.

SURVEY (no ``--adapter``) is the discovery half: it groups an artifact's records into distinct
SHAPES and, for each, reports the key skeleton with value types plus the *low-cardinality* scalar
fields that can serve as discriminators in a ``when:``. A value is printed only when it looks like
a label — short, single-line, a few words at most — and never when it looks like prose or varies
about as often as records do. That boundary is the point rather than a nicety: this report is what
the authoring model reads, and the design it serves keeps message content away from a model
entirely, so a withheld value is named by path and left unquoted. It also nominates ``order:`` and ``id:``
candidates, because both have a testable definition (strictly increasing / present-and-distinct)
that is cheaper to check than to guess.

COVERAGE (with ``--adapter``) is the authoring loop's inner check. ``plurai convert-trace`` leaves
a record no rule accounts for out of the conversation, and counts it in the pin's provenance and a
finding — right for a converter meeting format drift, and too late for an author, because a turn
among those records is already missing from the pin. It also refuses a thread on the FIRST field
that does not resolve, so a twelve-shape artifact costs twelve round trips through a conversion.
This mode runs the executor's own per-record steps and COLLECTS every gap instead, so a draft
adapter's gaps arrive in a single report.

It is a reporting wrapper, not a second executor. Every judgment — does this record match this
rule, does this field resolve, is this ordinal sequence legal — is made by importing
``plurai.trace_adapter``'s own functions and calling them. Nothing about the mapping language is
reimplemented here, because a second matcher that disagreed with the first would report "covered"
for a record the converter then refuses, which is worse than no report at all.

Either mode takes one file or a DIRECTORY of them, and a directory reports every file in name
order under one exit code — the corpus pass the authoring loop runs before shipping an adapter,
and the same shape `plurai convert-trace --export` accepts.

Exit codes: 0 clean, 1 gaps found, 2 an artifact or the environment could not be read. Over a
directory the code is the worst of the files.

    python3 survey_artifact.py session.jsonl --container jsonl
    python3 survey_artifact.py session.jsonl --adapter plurai_sim/plurai_adapters/claude_code.yaml
    python3 survey_artifact.py traces/ --adapter plurai_sim/plurai_adapters/claude_code.yaml
"""

from __future__ import annotations

import argparse
import inspect
import io
import itertools
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# Values above this many distinct observations stop being a discriminator and start being content.
DEFAULT_MAX_DISTINCT = 8
# A scalar longer than this is content even if the artifact only ever recorded one of it.
MAX_LITERAL_CHARS = 60
# Words above this many make a value prose rather than a label. Length alone does not separate the
# two: a short sentence of thinking fits inside any character budget generous enough to hold a
# uuid or a hyphenated enum, and printing it would put transcript text in front of the model this
# whole design keeps it away from. Word count separates them cleanly — labels are one or two
# tokens, and prose is not.
MAX_LITERAL_WORDS = 3
# Paths deeper than this are noise for an author who is writing top-level `when:` matchers.
MAX_DEPTH = 4
UNACCOUNTED_INDICES_SHOWN = 10
# What a directory holds that this script can read: the json and line-delimited containers, each
# also gzipped, as the converter enumerates a directory. Matched on the lower-cased name.
ARTIFACT_SUFFIXES = ('.json', '.jsonl', '.ndjson')
GZIP_SUFFIX = '.gz'
GZIP_MAGIC = b'\x1f\x8b'
# Stands in for the uuid5 the executor mints when a rule declares no `id:`. Any non-blank string
# reaches the same canonical verdict, and minting the real one would couple this pre-flight to
# the executor's id namespace for a value no report ever prints.
MINTED_ID_STANDIN = 'minted-by-the-executor'
# Same idea for the unresolved-field list, which on a badly-pathed adapter is one line per record.
FIELD_PROBLEMS_SHOWN = 20


class SurveyError(Exception):
    """A condition the caller has to fix — a missing file, an unreadable artifact, a stale SDK."""


# --------------------------------------------------------------------------- #
# Reading the artifact                                                        #
# --------------------------------------------------------------------------- #


def read_text(path: Path) -> str:
    """The artifact's text through the SDK's own source read (gzip, byte-order marks), the one the
    converter uses. Survey mode also runs on an interpreter without the SDK; there it reads plain
    UTF-8 and refuses a gzipped artifact by name."""
    try:
        from plurai.trace_convert import TraceConversionError, read_source_text  # pyright: ignore[reportMissingImports]
    except ImportError:
        try:
            raw = path.read_bytes()
        except OSError as e:
            raise SurveyError(f'{path}: could not be read ({e}).') from e
        if raw[:2] == GZIP_MAGIC:
            raise SurveyError(f'{path}: is gzip-compressed, and only the plurai SDK reads it. Run this with the client venv\'s python.')
        try:
            return io.TextIOWrapper(io.BytesIO(raw), encoding='utf-8').read()  # universal newlines, as read_text
        except UnicodeDecodeError as e:
            raise SurveyError(f'{path}: could not be read ({e}).') from e
    try:
        return read_source_text(path)
    except TraceConversionError as e:
        raise SurveyError(str(e)) from e


def read_records(path: Path, container: str, records_path: str | None) -> list[Any]:
    """The artifact's records, read the way the executor reads them."""
    text = read_text(path)

    if container == 'jsonl':
        root: Any = []
        # `split('\n')`, not `splitlines()` — matching the executor, which does the same so that a
        # message containing U+2028 is not split into two unparseable fragments.
        for number, raw_line in enumerate(text.split('\n'), start=1):
            line = raw_line.removesuffix('\r')
            if not line.strip():
                continue
            try:
                root.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SurveyError(f'{path.name} line {number}: not parseable JSON ({e}). Is this really jsonl?') from e
    else:
        try:
            root = json.loads(text)
        except json.JSONDecodeError as e:
            raise SurveyError(f'{path.name}: not parseable JSON ({e}). If it is one object per line, pass --container jsonl.') from e

    if records_path:
        found = resolve(root, records_path)
        if len(found) != 1:
            raise SurveyError(f'{path.name}: records path {records_path!r} matched {len(found)} values; it must name exactly one list.')
        root = found[0]
    if not isinstance(root, list):
        raise SurveyError(
            f'{path.name}: the records are {type(root).__name__}, not a list. '
            f'{"Point --records at the list." if not records_path else "That path does not hold a list."}'
        )
    return root


def resolve(value: Any, path: str) -> list[Any]:
    """A minimal stand-in for the executor's resolver, used only before an adapter exists.

    Survey mode runs with no adapter and therefore no SDK contract to honour; coverage mode uses
    the executor's own :func:`_resolve_path` instead. Kept to dict keys and ``*`` — enough to reach
    a `records:` list, and deliberately not enough to be mistaken for the mapping language.
    """
    current = [value]
    for segment in path.split('.'):
        nxt: list[Any] = []
        for item in current:
            if segment == '*' and isinstance(item, list):
                nxt.extend(item)
            elif isinstance(item, dict) and segment in item:
                nxt.append(item[segment])
        if not nxt:
            return []
        current = nxt
    return current


def _inner_name(path: Path) -> str:
    """The lower-cased name without a trailing ``.gz``: compression never changes the reading."""
    name = path.name.lower()
    return name.removesuffix(GZIP_SUFFIX)


def guess_container(path: Path) -> str:
    return 'jsonl' if Path(_inner_name(path)).suffix in {'.jsonl', '.ndjson', '.log'} else 'json'


def is_artifact_name(path: Path) -> bool:
    """Whether a directory entry is an export: one of :data:`ARTIFACT_SUFFIXES`, gzipped or not."""
    inner = _inner_name(path)
    return any(inner.endswith(suffix) and inner != suffix for suffix in ARTIFACT_SUFFIXES)


# --------------------------------------------------------------------------- #
# Shapes                                                                      #
# --------------------------------------------------------------------------- #


def shape_key(record: Any) -> str:
    """A record's top-level key set — the same identity the executor's refusal message prints."""
    if not isinstance(record, dict):
        return f'<{type(record).__name__}>'
    return '{' + ', '.join(sorted(record)) + '}'


def walk(record: Any, prefix: str = '', depth: int = 0) -> list[tuple[str, Any]]:
    """Every (dotted path, leaf value) in a record, with ``*`` standing in for a list index."""
    if depth >= MAX_DEPTH:
        return [(prefix, record)] if prefix else []
    if isinstance(record, dict):
        out: list[tuple[str, Any]] = []
        for key in sorted(record):
            out.extend(walk(record[key], f'{prefix}.{key}' if prefix else key, depth + 1))
        return out
    if isinstance(record, list):
        if not record:
            return [(prefix, [])]
        out = []
        for item in record:
            out.extend(walk(item, f'{prefix}.*' if prefix else '*', depth + 1))
        return out
    return [(prefix, record)]


def type_name(value: Any) -> str:
    if value is None:
        return 'null'
    return {bool: 'bool', int: 'int', float: 'float', str: 'str', list: 'list', dict: 'object'}.get(type(value), type(value).__name__)


def is_literal_candidate(value: Any) -> bool:
    """Whether a value is a label worth printing, as opposed to conversation content.

    This is the survey's disclosure boundary, so it errs toward printing nothing. A value passes
    only by looking like an identifier or an enum: short, single-line, and at most a few words.
    Everything else is named by path and withheld by value — the author still learns the field is
    there, without the transcript arriving in the session that is supposed to never see it.
    """
    if isinstance(value, (bool, int, float)) or value is None:
        return True
    return (
        isinstance(value, str)
        and len(value) <= MAX_LITERAL_CHARS
        and '\n' not in value
        and len(value.split()) <= MAX_LITERAL_WORDS
    )


def profile(records: list[Any], max_distinct: int) -> dict[str, dict[str, Any]]:
    """Per-path type counts, whether the path is label-safe, and its values where few enough.

    ``label_safe`` and ``values`` answer two different questions, and collapsing them was a real
    defect: a path goes unprintable either because it held something prose-shaped OR because it
    held too many distinct labels, and an IDENTIFIER is the second of those on every artifact big
    enough to matter. Judged by ``values is None`` alone, a uuid field was indistinguishable from a
    transcript — so a source that records perfectly good ids was told to mint its own, on exactly
    the artifacts where minting is most obviously wrong. ``label_safe`` is cardinality-blind: it
    says only that nothing at this path has ever looked like conversation.
    """
    types: dict[str, Counter] = defaultdict(Counter)
    values: dict[str, Counter] = defaultdict(Counter)
    printable: dict[str, bool] = defaultdict(lambda: True)
    label_safe: dict[str, bool] = defaultdict(lambda: True)
    present: Counter = Counter()

    for record in records:
        seen_paths = set()
        for path, value in walk(record):
            types[path][type_name(value)] += 1
            seen_paths.add(path)
            if not is_literal_candidate(value):
                label_safe[path] = False
                printable[path] = False
            elif printable[path]:
                values[path][json.dumps(value)] += 1
                if len(values[path]) > max_distinct:
                    printable[path] = False
        for path in seen_paths:
            present[path] += 1

    return {
        path: {
            'types': dict(counts),
            'present_in': present[path],
            'label_safe': label_safe[path],
            'values': dict(values[path]) if printable[path] and values[path] else None,
        }
        for path, counts in sorted(types.items())
    }


def order_candidates(records: list[Any], fields: dict[str, dict[str, Any]]) -> list[tuple[str, int]]:
    """Paths that are strictly increasing wherever they resolve, with how often they resolve.

    Presence is reported, not required. The executor's ordering rule applies to the messages an
    adapter PROJECTS, not to every record in the file, and this survey runs before any adapter
    exists to say which records are discarded — so a timestamp on 11 of 13 records is a legal
    ``order:`` exactly when the other two are discards. That is the author's call to make, and
    they can only make it if the ratio is on the page.
    """
    out = []
    for path, info in fields.items():
        # A `*` path names a block INSIDE a record, and a record's position is a property of the
        # record. Nominating one would offer the author a path the executor then refuses for
        # selecting more than one value per message.
        if '*' in path or set(info['types']) - {'int', 'float', 'str'} or len(set(info['types'])) > 1:
            continue
        # The profile's type set describes LEAVES; a path can still resolve to the list those
        # leaves came from (`message.content` is a string on one record and a block list on the
        # next). Re-check what actually resolved rather than trusting the leaf types.
        flat = [found[0] for record in records if len(found := resolve(record, path)) == 1 and isinstance(found[0], (int, float, str)) and not isinstance(found[0], bool)]
        if len(flat) < 2:
            continue
        try:
            if all(a < b for a, b in itertools.pairwise(flat)):  # pyright: ignore[reportOperatorIssue]
                out.append((path, len(flat)))
        except TypeError:
            continue
    return out


def id_candidates(records: list[Any], fields: dict[str, dict[str, Any]]) -> list[tuple[str, int]]:
    """Paths holding a distinct, non-blank string wherever they resolve, with how often they do.

    Same reasoning as :func:`order_candidates`: an id is required on the messages a rule projects,
    so a path present on a subset is a candidate for a rule that matches that subset.
    """
    out = []
    for path, info in fields.items():
        # Label-safe, NOT low-cardinality: a good id is high-cardinality by definition (one per
        # record), so testing printability here would reject every id on a large artifact and
        # accept only the fields too repetitive to be one. What must be excluded is prose, which
        # `label_safe` names directly — nominating a transcript as an id would contradict this
        # survey's own promise never to show the author conversation text.
        if '*' in path or set(info['types']) != {'str'} or not info['label_safe']:
            continue
        flat = [found[0] for record in records if len(found := resolve(record, path)) == 1 and isinstance(found[0], str)]
        if len(flat) < 2 or any(not value.strip() for value in flat):
            continue
        if len(set(flat)) == len(flat):
            out.append((path, len(flat)))
    return out


def group_shapes(records: list[Any], indices: list[int], max_distinct: int) -> list[dict[str, Any]]:
    """The records at ``indices``, grouped by top-level key set and profiled."""
    buckets: dict[str, list[int]] = defaultdict(list)
    for index in indices:
        buckets[shape_key(records[index])].append(index)
    return [
        {
            'shape': shape,
            'count': len(members),
            'indices': members,
            'fields': profile([records[i] for i in members], max_distinct),
        }
        for shape, members in sorted(buckets.items(), key=lambda item: -len(item[1]))
    ]


def survey(artifact: Path, container: str | None, records_path: str | None, max_distinct: int) -> dict[str, Any]:
    """The survey-mode report for ``artifact``: its shapes, its constants, its nominations.

    The counterpart to :func:`coverage`, and it exists for the same reason that one does —
    so the mode is a function with a return value rather than a block inside ``main``, which
    leaves ``main`` as argument wiring and lets the report be built without going through the
    CLI. ``container`` may be ``None``, in which case it is guessed from the suffix; the
    guess is reported back in the dict because the author has to know which one was used.

    Raises :class:`SurveyError` for the conditions the caller is expected to fix — same
    contract as the rest of the module, and ``main`` renders it.
    """
    container = container or guess_container(artifact)
    records = read_records(artifact, container, records_path)
    if not records:
        raise SurveyError(f'{artifact.name}: holds no records.')
    fields = profile(records, max_distinct)
    constant, freeform = uninformative(fields, len(records))
    return {
        'artifact': str(artifact),
        'container': container,
        'records_path': records_path,
        'record_count': len(records),
        'shapes': group_shapes(records, list(range(len(records))), max_distinct),
        'constant_fields': constant,
        'freeform_fields': sorted(freeform),
        'order_candidates': order_candidates(records, fields),
        'id_candidates': id_candidates(records, fields),
    }


# --------------------------------------------------------------------------- #
# Coverage — the executor's own steps, collected rather than raised            #
# --------------------------------------------------------------------------- #


def load_executor():
    """The executor's internals, or a refusal naming what to install.

    Importing private names is a deliberate coupling: this script's whole value is that it agrees
    with the converter exactly, and the only way to guarantee that is to run the converter's code.
    A mismatch surfaces here as an ImportError with a fix, never as a coverage report that lies.
    """
    # Resolved from the CLIENT's venv at runtime, never from this plugin — the plugin deliberately
    # takes no package dependency on the SDK. Unresolvable here by design; the except below is the
    # supported path, not a fallback.
    try:
        import yaml  # pyright: ignore[reportMissingModuleSource]
        from plurai import trace_adapter, trace_convert  # pyright: ignore[reportMissingImports]
    except ImportError as e:
        raise SurveyError(
            f'coverage mode needs the plurai SDK and pyyaml importable by this interpreter ({e}). Run it with the client venv\'s '
            'python, or survey without --adapter.'
        ) from e
    needed = ('AdapterDocument', 'AdapterError', '_artifact_root', '_records', '_matches', '_extract', '_tool_calls', '_resolve_path', '_ordering_violation')
    missing = [name for name in needed if not hasattr(trace_adapter, name)]
    # Conversion has two halves. The adapter decides where in the artifact each field is; the
    # canonical projection decides whether the pin schema can hold what came back. A coverage
    # pass that ran only the first half calls an adapter ready while `plurai convert-trace`
    # still refuses it — a blank id, a content shape the pin cannot carry, a `tool` message with
    # no call to answer are all invisible to the adapter's own extraction.
    missing += [f'trace_convert.{name}' for name in ('canonical_message', 'TraceConversionError') if not hasattr(trace_convert, name)]
    if missing:
        raise SurveyError(
            f'this plurai build\'s converter is missing {", ".join(missing)} — it moved and this pre-flight has not '
            'caught up. Skip --adapter and let `plurai convert-trace` report the gaps one at a time.'
        )
    # A content field may select several values (one message's content list); every other field
    # names one. An executor without that distinction would call a multi-block content a gap the
    # converter this plugin pairs with carries verbatim.
    if 'many' not in inspect.signature(trace_adapter._extract).parameters:
        raise SurveyError(
            'this plurai build predates the converter that carries a multi-value content field as a list and keeps '
            'unrecognised records out of the pin with a count. Upgrade the SDK in the client venv, then re-run.'
        )
    return trace_adapter, trace_convert, yaml


def load_document(adapter_path: Path, adapter, yaml):
    """The adapter, validated by the SDK's own model — a bad document fails here, not mid-report."""
    try:
        raw = yaml.safe_load(adapter_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError) as e:
        raise SurveyError(f'{adapter_path}: could not be read ({e}).') from e
    except yaml.YAMLError as e:
        raise SurveyError(f'{adapter_path}: is not parseable YAML ({e}).') from e
    if not isinstance(raw, dict):
        raise SurveyError(f'{adapter_path}: must be a YAML mapping, got {type(raw).__name__}.')
    try:
        document = adapter.AdapterDocument.model_validate(raw)
    except Exception as e:  # pydantic ValidationError, without importing pydantic to name it
        raise SurveyError(f'{adapter_path}: is not a valid adapter — {e}') from e
    if document.format != adapter_path.stem:
        raise SurveyError(f'{adapter_path}: declares format {document.format!r} but is filed as {adapter_path.stem!r}. The stem and the declaration must agree.')
    return document


def block_speaks(block: Any) -> bool:
    """Does one content block carry anything beyond its own type tag?"""
    if isinstance(block, str):
        return bool(block.strip())
    if isinstance(block, dict):
        return any(str(value).strip() for key, value in block.items() if key != 'type')
    return block is not None


def speaks(message: dict[str, Any]) -> bool:
    """Does this projected message carry anything a reader could hear — text, or a tool call?

    An agent turn whose ``content`` is ``''`` because the turn WAS the tool call is ordinary and
    says plenty, which is why a call counts. A message that says neither is a blank turn, and a
    whole transcript of them is a pin that replays as silence.
    """
    if message.get('tool_calls'):
        return True
    content = message.get('content')
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(block_speaks(block) for block in content)
    return content is not None


def coverage(artifact: Path, adapter_path: Path, max_distinct: int) -> dict[str, Any]:
    """Every record's fate under this adapter, with every refusal collected instead of raised."""
    adapter, convert, yaml = load_executor()
    document = load_document(adapter_path, adapter, yaml)

    try:
        root = adapter._artifact_root(document, artifact)
        records = adapter._records(document, root, source=artifact.name)
    except adapter.AdapterError as e:
        raise SurveyError(str(e)) from e

    unaccounted: list[int] = []
    discard_hits = Counter()
    rule_hits = Counter()
    field_problems: list[str] = []
    silent = 0
    ordinals: list[Any] = []
    ordinal_resolved = 0

    # `provenance:` and `agent_state:` are read ONCE from the artifact root, not per record — so a
    # bad path there survives every record passing and refuses at the very end of a conversion, the
    # most expensive moment to learn it. Same call the executor makes, same `field=` labels.
    for key, spec in document.provenance.items():
        try:
            adapter._extract(root, spec, where=artifact.name, field=f'provenance.{key}', many=False)
        except adapter.AdapterError as e:
            field_problems.append(str(e))
    if document.agent_state is not None:
        # `agent_state:` is a bare path, not a FieldSpec, so it has no `_extract` to borrow. This is
        # the one condition the report restates, and it restates the executor's line for line.
        found = adapter._resolve_path(root, document.agent_state)
        if len(found) != 1 or not isinstance(found[0], dict):
            got = type(found[0]).__name__ if len(found) == 1 else f'{len(found)} values'
            field_problems.append(f'{artifact.name}: agent_state path {document.agent_state!r} holds {got}, not a single object.')

    for index, record in enumerate(records):
        at = f'{artifact.name} record[{index}]'
        matched_discard = next((i for i, rule in enumerate(document.discard) if adapter._matches(record, rule.when)), None)
        if matched_discard is not None:
            discard_hits[matched_discard] += 1
            continue

        matched = next((i for i, rule in enumerate(document.messages) if adapter._matches(record, rule.when)), None)
        if matched is None:
            unaccounted.append(index)
            continue
        rule_hits[matched] += 1
        rule = document.messages[matched]

        # Every field the executor would extract, each failure recorded rather than raised. A rule
        # that matches and then cannot read its own content is a different defect from a missing
        # rule, and an author who only ever sees the first one fixes them one conversion at a time.
        # The extracted values are ASSEMBLED, not just probed, because the message is what the
        # canonical projection below judges. `many` is the executor's: content alone may select
        # several values, which become the message's content list.
        message: dict[str, Any] = {'type': rule.speak, 'id': MINTED_ID_STANDIN}
        readable = True
        for field, spec in (('content', rule.content), ('id', rule.id), ('tool_call_id', rule.tool_call_id)):
            if spec is None:
                continue
            try:
                message[field] = adapter._extract(record, spec, where=at, field=field, many=field == 'content')
            except adapter.AdapterError as e:
                field_problems.append(str(e))
                readable = False
        if rule.tool_calls is not None:
            try:
                message['tool_calls'] = adapter._tool_calls(record, rule.tool_calls, where=at)[0]
            except adapter.AdapterError as e:
                field_problems.append(str(e))
                readable = False
        if readable:
            silent += int(not speaks(message))
            try:
                convert.canonical_message(message, where=at)
            except convert.TraceConversionError as e:
                field_problems.append(str(e))

        if document.order is not None:
            found = adapter._resolve_path(record, document.order)
            if len(found) > 1:
                field_problems.append(f'{at}: order path {document.order!r} selects {len(found)} values, and a message has one position or none.')
                ordinals.append(None)
            else:
                # A key present but holding null is NOT a resolved ordinal. `_resolve_path` returns
                # `[None]` for it — truthy — so counting `bool(found)` would report it as resolved
                # and then contradict the ordering check, which counts the same record as having no
                # ordinal. One report must not state both.
                ordinal = found[0] if found else None
                ordinals.append(ordinal)
                ordinal_resolved += int(ordinal is not None)

    ordering = None
    if document.order is not None:
        ordering = adapter._ordering_violation(ordinals, source=artifact.name, path=document.order)

    # Profiled over every record, NOT over the unaccounted ones: "constant" has to mean constant
    # across the artifact. Judged against the records that still need a rule, the most useful field
    # of all — the one they all share, which is the `when:` the author is about to write — would be
    # constant within that group and get suppressed as uninformative.
    constant, freeform = uninformative(profile(records, max_distinct), len(records))
    return {
        'artifact': str(artifact),
        'adapter': str(adapter_path),
        'format': document.format,
        'record_count': len(records),
        'discarded': sum(discard_hits.values()),
        'projected': sum(rule_hits.values()),
        'silent_message_count': silent,
        'unaccounted_count': len(unaccounted),
        'unaccounted_shapes': group_shapes(records, unaccounted, max_distinct),
        'constant_fields': constant,
        'freeform_fields': sorted(freeform),
        'dead_discard_rules': [
            {'index': i, 'because': rule.because} for i, rule in enumerate(document.discard) if discard_hits[i] == 0
        ],
        'dead_message_rules': [
            {'index': i, 'speak': rule.speak, 'when': rule.when} for i, rule in enumerate(document.messages) if rule_hits[i] == 0
        ],
        'rule_hits': {str(i): rule_hits[i] for i in range(len(document.messages))},
        'field_problems': field_problems,
        'order_path': document.order,
        'ordinal_resolved_count': ordinal_resolved if document.order else None,
        'ordering_violation': ordering,
    }


# --------------------------------------------------------------------------- #
# Rendering                                                                   #
# --------------------------------------------------------------------------- #


SCALAR_TYPES = frozenset({'str', 'int', 'float', 'bool', 'null'})


def uninformative(fields: dict[str, dict[str, Any]], total: int) -> tuple[dict[str, str], frozenset[str]]:
    """Scalar paths that cannot discriminate, split by why: constant, or free-form.

    Both are judged against the WHOLE artifact, never against a subset. A value constant across
    every record separates no record from any other, and a free-form string (`uuid`, message text)
    has about as many values as records; neither narrows a ``when:``, and printing both against
    every shape buries the handful of fields that do. Judged against a subset the same test would
    invert: a field constant across just the records that still need a rule is the exact literal
    the author has to write, and hiding it would send them to the raw trace to find what this
    survey exists to tell them.

    Constants keep their value — one short token, and printing it is the difference between naming
    a path and handing over the matcher. Free-form paths are named without values, which is the
    promise never to print conversation content.

    Objects and lists stay visible even though they have no printable value — they are STRUCTURE,
    and an author reaching for `args:` or `where:` needs to see that a path holds a block list.
    """
    constant: dict[str, str] = {}
    freeform: set[str] = set()
    for path, info in fields.items():
        if set(info['types']) - SCALAR_TYPES:
            continue
        if not info['label_safe']:
            freeform.add(path)
        elif len(info['values'] or ()) == 1 and info['present_in'] == total:
            # Constant AND universal. A single value on a path only SOME records carry is the
            # opposite of uninformative — `queue: "fulfilment"` present on one record kind and
            # absent everywhere else is the cleanest matcher in the artifact. Suppressing it as a
            # "constant" would hide the discriminator and print a sentence that is false about it.
            constant[path] = next(iter(info['values'] or ()))
    return constant, frozenset(freeform)


def render_fields(
    fields: dict[str, dict[str, Any]],
    total: int,
    constant: dict[str, str],
    freeform: frozenset[str],
) -> list[str]:
    indent = '    '
    lines = []
    for path, info in fields.items():
        if path in constant or path in freeform:
            continue
        types = '|'.join(sorted(info['types']))
        presence = '' if info['present_in'] == total else f'  [{info["present_in"]}/{total}]'
        line = f'{indent}{path:<44} {types}{presence}'
        if info['values']:
            shown = ', '.join(f'{value}×{count}' for value, count in sorted(info['values'].items(), key=lambda kv: -kv[1]))
            line += f'\n{indent}    values: {shown}'
        lines.append(line)
    present = frozenset(fields)
    if same := sorted(path for path in constant if path in present):
        shown = ', '.join(f'{path}={constant[path]}' for path in same)
        lines.append(f'{indent}(same on every record in the artifact, so it narrows nothing: {shown})')
    if varied := sorted(freeform & present):
        lines.append(f'{indent}(free-form, about as many values as records — not matchable: {", ".join(varied)})')
    return lines


def render_survey(report: dict[str, Any]) -> str:
    out = [
        f'ARTIFACT  {report["artifact"]}',
        f'container: {report["container"]}   records: {report["records_path"] or "<the document root>"}   count: {report["record_count"]}',
        '',
        f'{len(report["shapes"])} distinct record shape(s). Each needs a message rule or an explicit discard — the executor',
        'leaves any record neither accounts for out of the conversation.',
        '',
    ]
    constant = report['constant_fields']
    freeform = frozenset(report['freeform_fields'])
    total = report['record_count']
    for number, shape in enumerate(report['shapes'], start=1):
        out.append(f'── shape {number}: {shape["count"]} record(s) — {shape["shape"]}')
        out.append(f'   first at index {shape["indices"][0]}')
        out.extend(render_fields(shape['fields'], shape['count'], constant, freeform))
        out.append('')
    if report['order_candidates']:
        out.append('order: strictly increasing wherever it resolves — legal only if every record MISSING it is a discard:')
        for path, resolved in report['order_candidates']:
            out.append(f'       {path}  (resolves on {resolved}/{total})')
    else:
        out.append('order: no field is strictly increasing — omit `order:` rather than declaring a partial or tying one.')
        out.append('       Order is then the record list\'s own, which is usually what the source already means.')
    if report['id_candidates']:
        out.append('id:    distinct and non-blank wherever it resolves — usable on a rule that matches those records:')
        for path, resolved in report['id_candidates']:
            out.append(f'       {path}  (resolves on {resolved}/{total})')
    else:
        out.append('id:    no field qualifies — omit `id:` and let the executor mint uuid5 ids, which it counts in provenance.')
    return '\n'.join(out)


def is_clean(report: dict[str, Any]) -> bool:
    """Does this coverage report describe an adapter that is ready to convert?

    One definition, read by both the rendered verdict and the exit code. Two copies of the
    expression is the divergence that matters most on a gate: the half a human reads and the
    half CI reads would disagree, and the human-visible half would look right.

    A non-empty projection is one of the conditions, not a precondition left to some other
    check. An adapter whose ``discard:`` is one notch too broad swallows every record, and the
    three problem counts are then all zero for the emptiest possible reason — nothing was left
    to be unaccounted, to fail a field, or to sit out of order. `plurai convert-trace` refuses
    that adapter for an empty ``messages`` list, which is precisely the trip this gate exists
    to catch first.

    A projection of nothing but SILENT messages is the same defect wearing the shape of a
    success. Every count is zero, every record became a message, and each of those messages is
    blank — the pin converts, uploads and generates, and the resumed run replays a conversation
    in which nobody said anything. The converter accepts it, so this is the last gate that can
    see it.
    """
    return (
        bool(report['projected'])
        and report['silent_message_count'] < report['projected']
        and not report['unaccounted_count']
        and not report['field_problems']
        and not report['ordering_violation']
    )


def render_coverage(report: dict[str, Any]) -> str:
    clean = is_clean(report)
    out = [
        f'ARTIFACT  {report["artifact"]}',
        f'ADAPTER   {report["adapter"]}  (format: {report["format"]})',
        '',
        (
            f'{report["record_count"]} records → {report["projected"]} projected, '
            f'{report["discarded"]} discarded, {report["unaccounted_count"]} UNACCOUNTED'
        ),
        '',
    ]
    # The verdict goes above the detail, not under it. What follows a clean run is commentary on a
    # working adapter, and a reader who meets it first reads a passing adapter as a failing one.
    if clean:
        out.append('CLEAN — every record is accounted for and every declared field resolves. Run `plurai convert-trace`.')
        out.append('')
    if not report['projected']:
        # Without this the run is a bare refusal: the three problem sections below are all empty
        # in exactly this case, so the report would print counts and stop, and the author would
        # be left to infer the verdict from an exit code.
        out.append('NOTHING PROJECTED — no record became a message, so this adapter converts to an empty transcript and')
        out.append('`plurai convert-trace` refuses it. Widen the message rules, or narrow the `discard:` rule that is')
        out.append('swallowing the conversation.')
        out.append('')
    if report['projected'] and report['silent_message_count'] == report['projected']:
        out.append(f'EVERY MESSAGE IS BLANK — all {report["projected"]} projected message(s) carry neither text nor a tool call, so')
        out.append('this adapter converts to a transcript of empty turns. `plurai convert-trace` accepts that pin, generation')
        out.append('accepts it, and the resumed run replays a conversation in which nobody said anything. The usual cause is')
        out.append("a `missing: ''` on `content:` standing in for a path that never resolves — take it off, and the report")
        out.append('above will name the path.')
        out.append('')
    if report['unaccounted_shapes']:
        out.append(f'UNACCOUNTED — {len(report["unaccounted_shapes"])} shape(s) no rule matches. `plurai convert-trace` leaves these')
        out.append('records out of the conversation and only counts them, so a turn among them is missing from every pin.')
        out.append('Every one of them needs a message rule or an explicit discard:')
        out.append('')
        constant = report['constant_fields']
        freeform = frozenset(report['freeform_fields'])
        for shape in report['unaccounted_shapes']:
            indices = shape['indices'][:UNACCOUNTED_INDICES_SHOWN]
            more = '' if len(shape['indices']) <= UNACCOUNTED_INDICES_SHOWN else f', … (+{len(shape["indices"]) - UNACCOUNTED_INDICES_SHOWN})'
            out.append(f'── {shape["count"]} record(s) at [{", ".join(str(i) for i in indices)}{more}] — {shape["shape"]}')
            out.extend(render_fields(shape['fields'], shape['count'], constant, freeform))
            out.append('')
    if report['field_problems']:
        out.append(f'FIELDS THAT DO NOT RESOLVE — {len(report["field_problems"])}. The rule matched and then could not read what it')
        out.append('declared. Fix the path. `missing:` is for a field the source legitimately omits, and on `content:` it')
        out.append('substitutes silence for the turn rather than reporting the gap:')
        for problem in report['field_problems'][:FIELD_PROBLEMS_SHOWN]:
            out.append(f'    {problem}')
        if len(report['field_problems']) > FIELD_PROBLEMS_SHOWN:
            out.append(f'    … (+{len(report["field_problems"]) - FIELD_PROBLEMS_SHOWN} more)')
        out.append('')
    if report['dead_message_rules']:
        out.append('RULES THAT NEVER MATCHED on THIS artifact. Two very different causes, and only you can tell them')
        out.append('apart: an earlier, broader rule shadows this one (rules are first-match-wins, so they go narrowest')
        out.append('first) — a real defect; or this artifact simply holds no such record, which is ordinary in a')
        out.append('heterogeneous corpus and no reason to delete a rule. Check the shadowing reading against a second')
        out.append('artifact before changing anything:')
        for rule in report['dead_message_rules']:
            out.append(f'    messages[{rule["index"]}] speak: {rule["speak"]}  when: {json.dumps(rule["when"])}')
        out.append('')
    if report['dead_discard_rules']:
        out.append('DISCARD RULES THAT NEVER MATCHED — harmless, but each one claims this artifact holds something it does not:')
        for rule in report['dead_discard_rules']:
            out.append(f'    discard[{rule["index"]}]: {rule["because"]}')
        out.append('')
    if report['order_path']:
        out.append(f'order: {report["order_path"]!r} resolved on {report["ordinal_resolved_count"]} of {report["record_count"]} records.')
        if report['ordering_violation']:
            out.append(f'    VIOLATION: {report["ordering_violation"]}')
    return '\n'.join(out)


# --------------------------------------------------------------------------- #


def artifacts_of(path: Path) -> list[Path]:
    """The files one invocation reads: the named file, or a directory's exports in name order.

    A directory is the ordinary ask, not a mistake to refuse: an adapter is a claim about a
    FORMAT, and one artifact can only ever evidence the shapes that artifact happens to hold.
    `plurai convert-trace --export` takes a directory for the same reason, so refusing one here
    would make the pre-flight the only step in the lane that cannot see the corpus it gates.
    """
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise SurveyError(f'{path}: no such file or directory.')
    found = sorted(child for child in path.iterdir() if child.is_file() and is_artifact_name(child))
    if not found:
        raise SurveyError(
            f'{path}: a directory holding no {" / ".join(ARTIFACT_SUFFIXES)} file, gzipped or not. Point this at one export, or at a directory of them.'
        )
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('artifact', type=Path, help='the source artifact to read, or a directory of them (never uploaded anywhere)')
    parser.add_argument('--adapter', type=Path, help='a draft adapter YAML; switches to coverage mode')
    parser.add_argument('--container', choices=('json', 'jsonl'), help='survey mode only; inferred from the suffix when omitted')
    parser.add_argument('--records', help='survey mode only; dotted path to the list of records inside a json document')
    parser.add_argument('--max-distinct', type=int, default=DEFAULT_MAX_DISTINCT, help=f'print literal values for fields with at most this many distinct ones (default {DEFAULT_MAX_DISTINCT})')
    parser.add_argument('--json', action='store_true', dest='as_json', help='emit the report as JSON — one object for a file, a list of them for a directory')
    args = parser.parse_args(argv)

    try:
        if args.adapter is not None and (args.container or args.records):
            raise SurveyError('--container / --records are survey-mode flags; in coverage mode the adapter declares both.')
        artifacts = artifacts_of(args.artifact)
    except SurveyError as e:
        print(f'survey_artifact: {e}', file=sys.stderr)
        return 2

    reports: list[dict[str, Any]] = []
    rendered: list[str] = []
    # The WORST outcome across the corpus, not the last one. An author who ran twelve files and
    # got exit 0 because the twelfth happened to be clean would ship on the strength of one file.
    status = 0
    for artifact in artifacts:
        try:
            if args.adapter is not None:
                report = coverage(artifact, args.adapter, args.max_distinct)
                text = render_coverage(report)
                status = max(status, 0 if is_clean(report) else 1)
            else:
                report = survey(artifact, args.container, args.records, args.max_distinct)
                text = render_survey(report)
        except SurveyError as e:
            # A refusal goes to stderr and NOWHERE else: stdout carries reports, so a reader
            # parsing it — `--json` most of all — never has to tell a report from an apology.
            # One unreadable file also does not end the pass, because over a corpus the
            # remaining files are still worth reading.
            print(f'survey_artifact: {e}', file=sys.stderr)
            status = 2
            continue
        reports.append(report)
        rendered.append(text)

    if not rendered:
        return status
    if args.as_json:
        # The shape follows what was ASKED for, not how many files a directory turned out to
        # hold: a caller that named one file never has to unwrap a list, and a caller that named
        # a directory never has to handle two shapes because it happened to contain one export.
        print(json.dumps(reports[0] if args.artifact.is_file() else reports, indent=2, default=str))
    else:
        print(('\n' + '─' * 78 + '\n\n').join(rendered))
    return status


if __name__ == '__main__':
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
