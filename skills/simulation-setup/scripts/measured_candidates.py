"""Measured facts a scan's own inputs already settle: closed URL sets, URLs outside them, topics the corpus never covers.

`scaffold_rule_facts.py --profile` proposes these as typed facts, and `lint_prd.py` reports one the PRD never
types, so both read the same function and cannot drift. Everything here is a mechanical sweep over files, never a
reading: a closed set is the distinct URL values of one structured field, a conflict is a URL an instruction file
names that no such field holds, an absence is a topic the instructions call out of scope that no corpus document
mentions. The files are the ones `artifacts/data_profile.md` names (a path-shaped token that resolves under the
agent's root), so the sweep covers what the scan profiled and nothing else.

Stdlib + pyyaml; runs under the python3 3.9 floor.
"""

from __future__ import annotations

import ast
import csv
import functools
import gzip
import itertools
import json
import re
import sys
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

try:
    import yaml
except ImportError:  # the caller reports a missing PyYAML; there is nothing to sweep without it
    yaml = None  # type: ignore[assignment]

#: A closed set of URLs larger than this is a corpus, not a list to type.
MAX_CLOSED_VALUES = 12
#: The topics of one absence fact, at most: a longer list is a document, not a probe.
MAX_ABSENCE_TERMS = 8

_PATH_TOKEN = re.compile(r'(?<![\w/.-])(?P<path>(?:[\w.-]+/)*[\w.-]+\.(?:ya?ml|json|ndjson|jsonl|csv|md|txt|rst)(?:\.gz)?)(?![\w/-])')
_URL = re.compile(r"https?://[^\s<>)\]\"'`,;]+")
_OUT_OF_SCOPE = re.compile(r'out of scope|outside (?:of )?(?:these|the|its)|not (?:covered|supported)', re.IGNORECASE)
_STRUCTURED = ('.yaml', '.yml', '.json', '.csv')
_PROSE = ('.md', '.txt', '.rst')


@dataclass(frozen=True)
class Candidate:
    """One measured fact the inputs settle. ``kind`` is the fact's predicate; ``words`` and ``values`` are what a PRD
    fact must mention to be taken as typing it."""

    kind: str  # closed_set | conflicts_across_sources | absent_from_source
    subject: str
    evidence: str
    source: str  # path:LINE, or a directory for an absence
    terms: tuple[str, ...] = ()
    check_kind: str = ''  # existence | absence | '' (no check)
    values: tuple[str, ...] = field(default=())


def input_paths(profile_text: str, root: Path | None = None) -> list[str]:
    """The files ``data_profile.md`` names, in first-mention order; with ``root``, only those that exist under it."""
    named = dict.fromkeys(m.group('path').removeprefix('./') for m in _PATH_TOKEN.finditer(profile_text))
    found = [p for p in named if root is None or _read(root, p) is not None]
    if root is None:
        return found
    # A profile names some of an agent's data and counts the rest: the structured files that sit directly in a
    # directory above a named one (below the root) are inputs of the same scan.
    for rel in list(found):
        for parent in Path(rel).parents:
            if str(parent) in ('', '.'):
                continue
            for sibling in sorted((root / parent).glob('*')):
                if sibling.is_file() and sibling.suffix.lower() in _STRUCTURED:
                    found.append(str(sibling.relative_to(root)))
    return list(dict.fromkeys(found))


def _read(root: Path, rel: str) -> str | None:
    path = (root / rel).resolve()
    if not path.is_file() or not path.is_relative_to(root.resolve()):
        return None
    try:
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None


def _records(text: str, suffix: str) -> list[tuple[str, list[dict]]]:
    """``(list name, records)`` for every list of mappings in a structured file.

    A file the parser cannot take (a CSV cell past the csv module's field limit, a structure deeper than the parser's
    recursion allows, malformed text) holds no records: the sweep is a courtesy, never the reason a lint run fails."""
    if suffix == '.csv':
        try:
            rows = list(csv.DictReader(text.splitlines()))
        except csv.Error:
            return []
        return [('rows', rows)] if rows else []
    if yaml is None:
        return []
    try:
        data = json.loads(text) if suffix == '.json' else yaml.safe_load(text)
    except (ValueError, RecursionError, yaml.YAMLError):
        return []
    if isinstance(data, list):
        return [('records', [r for r in data if isinstance(r, dict)])]
    if isinstance(data, dict):
        return [(str(k), [r for r in v if isinstance(r, dict)]) for k, v in data.items() if isinstance(v, list) and any(isinstance(r, dict) for r in v)]
    return []


def _first_line(text: str, needle: str) -> int:
    for number, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return number
    return 1


def closed_url_sets(root: Path, paths: list[str]) -> list[Candidate]:
    """Each structured field whose every non-null value is a URL: its distinct values are a closed set."""
    found: list[Candidate] = []
    for rel in paths:
        if not rel.lower().endswith(_STRUCTURED):
            continue
        text = _read(root, rel)
        if text is None:
            continue
        for name, records in _records(text, Path(rel).suffix.lower()):
            fields = sorted({key for record in records for key in record})
            for key in fields:
                values = [str(r[key]).strip() for r in records if r.get(key) not in (None, '')]
                if not values or not all(re.fullmatch(_URL.pattern, v) for v in values):
                    continue
                distinct = list(dict.fromkeys(values))
                if len(distinct) > MAX_CLOSED_VALUES:
                    continue
                subject = f'{name}.{key}'
                found.append(
                    Candidate(
                        'closed_set', subject,
                        f'{len(distinct)} {key} value(s) across the {len(values)} {name} entries that carry one: ' + ', '.join(distinct),
                        f'{rel}:{_first_line(text, distinct[0])}', terms=tuple(distinct), check_kind='existence', values=tuple(distinct),
                    )
                )
    return found


def instruction_urls(root: Path, sources: list[str]) -> list[tuple[str, str, int]]:
    """``(url, path, line)`` for every concrete URL the instruction files name (a ``<placeholder>`` host is a template)."""
    out: list[tuple[str, str, int]] = []
    for rel in sources:
        text = _read(root, rel)
        for number, line in enumerate((text or '').splitlines(), 1):
            for url in _URL.findall(line):
                if '<' not in url and '{' not in url:
                    out.append((url.rstrip('.'), rel, number))
    return out


def url_conflicts(root: Path, sources: list[str], closed: list[Candidate]) -> list[Candidate]:
    """A URL an instruction file names that no closed URL set holds, while at least one such set exists."""
    held = {v for c in closed for v in c.values}
    if not held:
        return []
    names = ', '.join(c.subject for c in closed)
    seen: dict[str, Candidate] = {}
    for url, rel, line in instruction_urls(root, sources):
        if url in held or url in seen:
            continue
        host = urlsplit(url).netloc
        seen[url] = Candidate(
            'conflicts_across_sources', f'{host}.url',
            f'{rel}:{line} names {url}, which no value of {names} equals; those sets hold {len(held)} URL(s)',
            f'{rel}:{line}', terms=(url,), values=(url,),
        )
    return list(seen.values())


def _topic_items(sentence: str) -> list[str]:
    """The topics an out-of-scope sentence lists after the colon that follows its phrase (a colon before a space, so
    ``https://`` is not one): split on commas, ``and`` and ``or``; ``any ...`` and long clauses are dropped."""
    for start in _OUT_OF_SCOPE.finditer(sentence):
        colon = re.search(r':\s', sentence[start.end():])
        if not colon:
            continue
        tail = re.sub(r'\*+|`', '', sentence[start.end() + colon.end():]).rstrip('. ')
        items: list[str] = []
        for chunk in re.split(r',|\band\b|\bor\b', tail):
            item = chunk.strip(' .;')
            if not item or item.lower().startswith(('any ', 'etc', 'but ', 'you ', 'that ')) or len(item.split()) > 3:
                continue
            items.append(item)
        if items:
            return list(dict.fromkeys(items))
    return []


def out_of_scope_topics(root: Path, sources: list[str]) -> list[tuple[str, str, int]]:
    """``(topic, path, line)`` for each topic listed in an out-of-scope sentence of an instruction file."""
    found: list[tuple[str, str, int]] = []
    for rel in sources:
        for number, line in enumerate((_read(root, rel) or '').splitlines(), 1):
            if _OUT_OF_SCOPE.search(line) and ':' in line:
                found += [(t, rel, number) for t in _topic_items(line)]
    return found


def uncovered_topics(root: Path, sources: list[str], paths: list[str]) -> list[Candidate]:
    """Out-of-scope topics no corpus document mentions, as one absence candidate."""
    named = [p for p in paths if p.lower().endswith(_PROSE) and p not in sources]
    # A profile names some documents and counts the rest ("the four guides"): the siblings of a named one are corpus too.
    docs = list(dict.fromkeys([*named, *(str(q.relative_to(root)) for p in named for q in sorted((root / p).parent.glob('*' + Path(p).suffix)) if q.is_file())]))
    docs = [p for p in docs if p not in sources]
    corpus = {p: (_read(root, p) or '').lower() for p in docs}
    if not corpus:
        return []
    topics = out_of_scope_topics(root, sources)
    absent = [t for t in dict.fromkeys(t for t, _, _ in topics) if not any(re.search(rf'\b{re.escape(t.lower())}\b', body) for body in corpus.values())]
    if not absent:
        return []
    absent = absent[:MAX_ABSENCE_TERMS]
    first = next((rel, number) for t, rel, number in topics if t in absent)
    parents = Counter(str(Path(p).parent) for p in docs)
    folder = parents.most_common(1)[0][0]
    folder = '' if folder == '.' else folder.rstrip('/') + '/'
    return [
        Candidate(
            'absent_from_source', 'corpus.out_of_scope_topics',
            f'{", ".join(absent)} occur in none of the {len(corpus)} corpus document(s); {first[0]}:{first[1]} lists them as out of scope',
            folder, terms=tuple(absent), check_kind='absence',
        )
    ]


def finding_text(c: Candidate, scaffold: str = 'scaffold_rule_facts.py --profile') -> str:
    """The open finding for a candidate no PRD fact types: what the sweep found, and the two ways to close it."""
    what = {
        'closed_set': f'`{c.subject}` is a closed set of {len(c.values)} URL value(s) in {c.source}, and no `closed_set` fact types it',
        'conflicts_across_sources': f'{c.evidence}, and no `conflicts_across_sources` fact states it',
        'absent_from_source': f'{c.evidence}, and no `absent_from_source` fact types the absence',
    }[c.kind]
    return f'measured facts: {what} — add the fact (`{scaffold}` proposes it) or accept this finding with the reason it does not apply.'


def candidates(root: Path, profile_text: str, sources: list[str]) -> list[Candidate]:
    """Every measured fact ``profile_text``'s inputs settle, closed sets first."""
    paths = input_paths(profile_text, root)
    closed = closed_url_sets(root, paths)
    return [*closed, *url_conflicts(root, sources, closed), *uncovered_topics(root, sources, paths)]


def _words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r'[A-Za-z0-9_]{3,}', text)}


def is_typed(candidate: Candidate, facts: list) -> bool:
    """Whether a PRD fact types ``candidate``: a fact of its predicate that mentions one of its values (URLs) or
    shares a word with one of its terms (topics), in its subject, evidence or check terms."""
    for fact in facts:
        if not isinstance(fact, dict) or str(fact.get('predicate')) != candidate.kind:
            continue
        check = fact.get('check') if isinstance(fact.get('check'), dict) else {}
        blob = f'{fact.get("subject", "")} {fact.get("evidence", "")} {" ".join(map(str, check.get("terms") or []))}'
        if candidate.kind == 'absent_from_source':
            if _words(' '.join(candidate.terms)) & _words(blob):
                return True
        elif candidate.kind == 'closed_set':
            if candidate.subject.rsplit('.', 1)[-1] in blob or any(v in blob for v in candidate.values):
                return True
        elif any(v in blob or urlsplit(v).netloc in blob for v in candidate.values):
            return True
    return False


# ── World checks: data invariants every real row holds ─────────────────────────────────────────────────────────────────
#
# A world check (`world_state.entity_types.<type>.checks`, enforced by the backend on every seeded world) is a fact about
# the agent's real rows. The sweep below proposes one only when it holds on EVERY row of a list of mappings in one of the
# agent's structured data files, and `world_check_problems` re-measures a declared one on the file it cites, so a check
# is never stated from memory. Three kinds:
#
# * a field fixed while a boolean field holds a value (`when: {taxIncluded: true}`, `then: {'lines[].taxAlloc': 0}`),
#   and different on some row where it does not — two values that never coexist;
# * a field whose every value is an id of a list in ANOTHER file (`{in: [...]}`, the members copied from that list): a
#   reference into data the rows' own file does not hold, such as a catalog. A reference inside one file is between
#   the world's own rows, which a `references:` field states. A longer id list is the world's own rows too, unless its
#   file is in a reference store the scan recorded (:func:`reference_roots`), which it never seeds;
# * a number computed from the row (`{expr: ...}`): the sum of an item field over a row list (or of a product of two),
#   or a sum and difference of up to MAX_TERMS other numbers.
#
# `source` cites the rows: `<path>` for a file that is one list, `<path>#<list>` for a file that maps names to lists,
# with ` (members: <path>#<list>.<field>)` after it when an `in` set is copied from another file.

#: A list with fewer rows than this proves no invariant.
MIN_CHECK_ROWS = 20
#: Rows a condition must hold on before what it fixes is a fact.
MIN_CONDITION_ROWS = 5
#: Values that would break a conditional check if the field ignored the condition (its rate of other values where the
#: condition does not hold, times the values read where it does), at least: fewer, and chance explains the absence.
MIN_EXPECTED_BREAKS = 5
#: An id list longer than this is the world's own rows, not a reference list to copy.
MAX_REFERENCE_MEMBERS = 200
#: The ceiling for an id list in a declared reference store (a file under a root the scan recorded as content the agent
#: reads live: `.scan_corpus_roots`, `artifacts/corpus_roots.json`). Such a list is no world's own rows, so the cap above
#: does not apply; this one does because the backend shows an `in` set in full to the scenario author.
MAX_REFERENCE_STORE_MEMBERS = 2000
#: Terms of a computed number, at most.
MAX_TERMS = 4
#: Rows the computed-number search screens a candidate on before it is measured on every row.
_SCREEN_ROWS = 64
#: The most decompressed bytes one data file is read to.
MAX_DATA_BYTES = 64 << 20
#: Absolute tolerance of a numeric comparison (the backend's default: float noise, never money).
CHECK_TOLERANCE = 1e-9

_DATA_SUFFIXES = ('.json', '.yaml', '.yml', '.csv', '.ndjson', '.jsonl')
_EXPR_FUNCTIONS = {'sum': sum, 'min': min, 'max': max, 'abs': abs, 'round': round, 'len': len}
_SOURCE = re.compile(r'^(?P<path>[^\s#()]+?)(?:#(?P<list>[^\s()]+))?(?:\s+\(members:\s*(?P<mpath>[^\s#()]+)#(?P<mlist>[^\s()]*?)\.(?P<mfield>[^\s.()]+)\))?$')
_PRD = re.compile(r'simulation_prd\.md', re.IGNORECASE)


@dataclass(frozen=True)
class WorldCheck:
    """One proposed check on the rows of ``rows`` (``<path>`` or ``<path>#<list>``)."""

    rows: str
    count: int
    check: dict


@functools.lru_cache(maxsize=8)
def data_lists(root: Path, rel: str) -> dict[str, list[dict]]:
    """``{list name: rows}`` for every list of mappings a structured data file holds; ``''`` names a file that is one list.

    JSON, YAML, CSV, NDJSON/JSONL, each optionally gzipped. A file that is missing, outside ``root``, larger than
    MAX_DATA_BYTES decompressed, or unparseable holds no lists. Cached: read-only for its callers."""
    path = (root / rel).resolve()
    name = path.name.lower()
    inner = name[:-3] if name.endswith('.gz') else name
    if not inner.endswith(_DATA_SUFFIXES) or not path.is_file() or not path.is_relative_to(root.resolve()):
        return {}
    try:
        with (gzip.open(path, 'rb') if name.endswith('.gz') else path.open('rb')) as fh:
            raw = fh.read(MAX_DATA_BYTES + 1)
    except (OSError, EOFError):
        return {}
    if len(raw) > MAX_DATA_BYTES:
        return {}
    text = raw.decode('utf-8', errors='replace')
    try:
        if inner.endswith('.csv'):
            data: object = list(csv.DictReader(text.splitlines()))
        elif inner.endswith(('.ndjson', '.jsonl')):
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
        elif inner.endswith('.json'):
            data = json.loads(text)
        else:
            data = yaml.safe_load(text) if yaml is not None else None
    except (ValueError, RecursionError, csv.Error, *(() if yaml is None else (yaml.YAMLError,))):
        return {}
    if isinstance(data, list):
        rows = [r for r in data if isinstance(r, dict)]
        return {'': rows} if rows else {}
    if isinstance(data, dict):
        return {str(k): [r for r in v if isinstance(r, dict)] for k, v in data.items() if isinstance(v, list) and v and all(isinstance(r, dict) for r in v)}
    return {}


def _scalar(value: object) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_values(row: object, path: str) -> list[object]:
    """Every value ``path`` (``a``, ``a.b``, ``a[].b``) names on ``row``; an absent step reads None, ``a[]`` over no list reads nothing."""
    found: list[object] = [row]
    for segment in path.split('.'):
        name, iterate = (segment[:-2], True) if segment.endswith('[]') else (segment, False)
        step: list[object] = []
        for current in found:
            value = current.get(name) if isinstance(current, dict) else None
            if not iterate:
                step.append(value)
            elif isinstance(value, list):
                step.extend(value)
        found = step
    return found


def same_value(a: object, b: object, tolerance: float = CHECK_TOLERANCE) -> bool:
    """Equality as the backend compares a seeded value: a bool equals only a bool, numbers within ``tolerance``."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if _number(a) and _number(b):
        return abs(float(a) - float(b)) <= tolerance  # type: ignore[arg-type]
    return a == b


def _key(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _row_paths(rows: list[dict]) -> list[str]:
    """The scalar paths of a list: each top-level field, and ``f[].g`` for each field of the mappings a row-list field holds."""
    paths: dict[str, None] = {}
    for row in rows:
        for name, value in row.items():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        paths.update((f'{name}[].{k}', None) for k, v in item.items() if _scalar(v))
            elif _scalar(value):
                paths[str(name)] = None
    return [p for p in paths if all(_scalar(v) for r in rows for v in check_values(r, p))]


def _conditional_checks(rows: list[dict], paths: list[str]) -> list[dict]:
    """``when {flag: v}`` → ``then {path: k}``: ``path`` is ``k`` on every row where the boolean ``flag`` is ``v`` and
    differs often enough where it is not that chance cannot explain it (MIN_EXPECTED_BREAKS). Of a check and its
    contrapositive over two booleans, only the first is kept."""
    flags = [p for p in paths if '[]' not in p and all(isinstance(r.get(p), bool) for r in rows)]
    found: list[dict] = []
    seen: set[str] = set()
    for flag in flags:
        for v in (True, False):
            inside = [r for r in rows if r[flag] is v]
            outside = [r for r in rows if r[flag] is not v]
            if len(inside) < MIN_CONDITION_ROWS or not outside:
                continue
            for path in paths:
                if path == flag:
                    continue
                values = [x for r in inside for x in check_values(r, path)]
                if not values or not all(same_value(x, values[0]) for x in values):
                    continue
                rest = [x for r in outside for x in check_values(r, path)]
                differ = sum(1 for x in rest if not same_value(x, values[0]))
                if not differ or differ * len(values) < MIN_EXPECTED_BREAKS * len(rest):
                    continue  # fixed on every row, or too rarely different for its absence here to mean anything
                fixed = values[0]
                if path in flags and isinstance(fixed, bool) and _key([path, not fixed, flag, not v]) in seen:
                    continue
                seen.add(_key([flag, v, path, fixed]))
                found.append({'when': {flag: v}, 'then': {path: fixed}})
    return found


def _id_fields(rows: list[dict], limit: int) -> list[str]:
    """Top-level fields every row carries as a distinct id (:func:`_is_id`): the list's ids, for a list of at most ``limit`` rows."""
    if not 2 <= len(rows) <= limit:
        return []
    return [
        name
        for name in rows[0]
        if all(_is_id(r.get(name)) for r in rows) and len({_key(r[name]) for r in rows}) == len(rows)
    ]


def _is_id(value: object) -> bool:
    """A string without whitespace and with a non-digit: a counter (1..n) is no id another file can be said to cite."""
    return isinstance(value, str) and not any(c.isspace() for c in value) and not value.isdigit() and bool(value)


def _reference_checks(rows: list[dict], paths: list[str], here: str, ids: dict[str, dict[str, object]]) -> list[tuple[dict, str]]:
    """``{path: {in: <ids>}}`` for each path whose every value is an id of a list in another file than ``here``;
    ``ids`` maps ``<path>#<list>.<field>`` to that list's ids."""
    found: list[tuple[dict, str]] = []
    for path in paths:
        values = [x for r in rows for x in check_values(r, path)]
        if not values or any(x is None or isinstance(x, bool) for x in values):
            continue
        keys = {_key(x) for x in values}
        if len(keys) < 2:
            continue
        for members, by_key in ids.items():
            if Path(members.split('#', 1)[0]).as_posix() != Path(here).as_posix() and keys <= by_key.keys():
                found.append(({'then': {path: {'in': sorted(by_key.values(), key=_key)}}}, members))
    return found


def _sum_expr(list_field: str, items: tuple[str, ...]) -> str:
    return f'sum({" * ".join(f"l[{i!r}]" for i in items)} for l in {list_field})'


def _computed_checks(rows: list[dict]) -> list[dict]:
    """``{field: {expr}}`` for a number that is, on every row, the sum of an item field (or a product of two) over a
    row list, or a sum and difference of up to MAX_TERMS other numbers and item sums. A number that varies is a target;
    of the rearrangements of one identity, the one with the fewest subtractions is kept."""
    names = [n for n in rows[0] if str(n).isidentifier() and all(_number(r.get(n)) for r in rows)]
    names = [n for n in names if len({_key(r[n]) for r in rows}) > 1]
    sums: dict[str, list[float]] = {}
    for list_field in [n for n in rows[0] if str(n).isidentifier() and all(isinstance(r.get(n), list) for r in rows)]:
        items = [r[list_field] for r in rows]
        if not any(items) or not all(isinstance(i, dict) for its in items for i in its):
            continue
        every = [i for its in items for i in its]
        numeric = sorted(k for k in {k for i in every for k in i} if all(_number(i.get(k)) for i in every))
        combos = [(k,) for k in numeric] + [(a, b) for n, a in enumerate(numeric) for b in numeric[n + 1 :]]
        for combo in combos:
            column = [float(sum(_product(i, combo) for i in its)) for its in items]
            if len(set(column)) > 1:
                sums[_sum_expr(list_field, combo)] = column
    columns = {n: [float(r[n]) for r in rows] for n in names}
    found: list[dict] = []
    for target in names:  # a number that IS one item sum
        expr = next((e for e, column in sums.items() if all(same_value(a, b) for a, b in zip(columns[target], column))), None)
        if expr is not None:
            found.append({'then': {target: {'expr': expr}}})
    single = {e: c for e, c in sums.items() if '*' not in e and not any(all(same_value(a, b) for a, b in zip(columns[n], c)) for n in names)}
    terms = {**columns, **single}
    explained: set[str] = set()
    accepted: list[set[str]] = []
    for size in range(2, MAX_TERMS + 1):
        hits: list[tuple[int, int, str, tuple[tuple[str, int], ...]]] = []
        for target in [n for n in names if n not in explained]:
            others = [t for t in terms if t != target]
            for chosen in itertools.combinations(others, size):
                for signs in itertools.product((1, -1), repeat=size):
                    if all(_holds_sum(columns[target], terms, chosen, signs, i) for i in range(min(_SCREEN_ROWS, len(rows)))) and all(
                        _holds_sum(columns[target], terms, chosen, signs, i) for i in range(len(rows))
                    ):
                        hits.append((list(signs).count(-1), -names.index(target), target, tuple(zip(chosen, signs))))
        for _neg, _pos, target, parts in sorted(hits):
            group = {target, *(t for t, _ in parts)}
            if target in explained or group in accepted:
                continue
            found.append({'then': {target: {'expr': ' '.join(f'{"-" if s < 0 else "+"} {t}' for t, s in parts).removeprefix('+ ')}}})
            explained.add(target)
            accepted.append(group)
    return found


def _holds_sum(target: list[float], terms: dict[str, list[float]], chosen: tuple[str, ...], signs: tuple[int, ...], i: int) -> bool:
    return same_value(target[i], sum(s * terms[t][i] for s, t in zip(signs, chosen)))


def _product(item: dict, combo: tuple[str, ...]) -> float:
    out = 1.0
    for key in combo:
        out *= float(item[key])
    return out


def reference_roots(root: Path, workdir: Path | None = None) -> list[Path] | None:
    """The reference stores the scan recorded for the agent at ``root``: every line of ``<workdir>/.scan_corpus_roots``
    and every root of a part of ``<workdir>/artifacts/corpus_roots.json`` (``workdir`` defaults to ``root/plurai_sim``),
    resolved. None when neither file exists, so no store is classified; ``[]`` when they exist and name nothing that
    resolves.

    A root is an absolute path. One outside ``root`` (a bench copied or moved after the scan, the original kept or
    gone) resolves to the longest tail of it that exists under ``root``; one with no such tail is kept when it exists
    and dropped when it does not. An unreadable ``corpus_roots.json`` counts as absent."""
    workdir = root / 'plurai_sim' if workdir is None else workdir
    lines: list[str] | None = None
    try:
        lines = (workdir / '.scan_corpus_roots').read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        pass
    try:
        partition = json.loads((workdir / 'artifacts' / 'corpus_roots.json').read_text(encoding='utf-8', errors='replace'))
    except (OSError, ValueError):
        partition = None
    if isinstance(partition, dict):
        parts = partition.get('parts') if isinstance(partition, dict) else None
        lines = [*(lines or []), *(
            r for part in (parts.values() if isinstance(parts, dict) else ()) if isinstance(part, dict) and isinstance(part.get('roots'), list)
            for r in part['roots'] if isinstance(r, str)
        )]
    if lines is None:
        return None
    resolved = (_resolve_root(root, line.strip()) for line in dict.fromkeys(lines) if line.strip())
    return list(dict.fromkeys(p for p in resolved if p is not None))


def _resolve_root(root: Path, line: str) -> Path | None:
    given = Path(line) if Path(line).is_absolute() else root / line
    base = root.resolve()
    if given.exists() and given.resolve().is_relative_to(base):
        return given.resolve()
    parts = Path(line).parts
    for i in range(1, len(parts)):
        tail = root.joinpath(*parts[i:]).resolve()
        if tail.exists() and tail.is_relative_to(base):
            return tail
    return given.resolve() if given.exists() else None


def _in_store(root: Path, rel: str, stores: list[Path]) -> bool:
    path = (root / rel).resolve()
    return any(path == store or path.is_relative_to(store) for store in stores)


#: The world-type key that names the file (and list) a type's rows are copied from when the agent only reads them: a
#: catalog, a code table the run simulates as a world type. The scan declares it; nothing infers it.
REFERENCE_ROWS_KEY = 'reference_rows'


def reference_tables(config: object) -> dict[tuple[str, str], str]:
    """``{(path, list): type}`` for every ``world_state.entity_types.<type>.reference_rows: <path>[#<list>]``: the
    list a simulated type's rows are copied from (``''`` names a file that is one list). A reference table is a store
    scoped to that one list, so its ids are an ``in`` set for the OTHER types that hold them."""
    world_state = config.get('world_state') if isinstance(config, dict) else None
    bodies = world_state.get('entity_types') if isinstance(world_state, dict) else None
    found: dict[tuple[str, str], str] = {}
    for name, body in (bodies.items() if isinstance(bodies, dict) else ()):
        value = body.get(REFERENCE_ROWS_KEY) if isinstance(body, dict) else None
        if isinstance(value, str) and value.strip():
            path, _, list_name = value.strip().replace('\\', '/').partition('#')
            found[(Path(path.removeprefix('./')).as_posix(), list_name)] = str(name)
    return found


def _in_table(rel: str, name: str, tables: Collection[tuple[str, str]]) -> bool:
    return (Path(rel).as_posix(), name) in tables


def _members_list(members: str) -> tuple[str, str]:
    """``(path, list)`` of a ``<path>#<list>.<field>`` members key."""
    path, _, rest = members.partition('#')
    return path, rest.rpartition('.')[0]


def _swept_lists(root: Path, paths: list[str]) -> dict[tuple[str, str], list[dict]]:
    return {(rel, name): rows for rel in dict.fromkeys(paths) for name, rows in data_lists(root, rel).items()}


def _member_ids(
    root: Path, lists: dict[tuple[str, str], list[dict]], stores: list[Path], tables: Collection[tuple[str, str]] = ()
) -> dict[str, dict[str, object]]:
    """``{<path>#<list>.<field>: ids}`` for every id field of a list: at most MAX_REFERENCE_MEMBERS rows, or
    MAX_REFERENCE_STORE_MEMBERS for a list in a reference store under ``stores`` or a declared reference table."""
    in_store = {rel: _in_store(root, rel, stores) for rel, _ in lists}
    return {
        f'{rel}#{name}.{field_}': {_key(r[field_]): r[field_] for r in rows}
        for (rel, name), rows in lists.items()
        for field_ in _id_fields(rows, MAX_REFERENCE_STORE_MEMBERS if in_store[rel] or _in_table(rel, name, tables) else MAX_REFERENCE_MEMBERS)
    }


def _where(rel: str, name: str) -> str:
    return f'{rel}#{name}' if name else rel


def world_check_candidates(
    root: Path, paths: list[str], reference_roots: list[Path] | None = None, tables: Collection[tuple[str, str]] = ()
) -> list[WorldCheck]:
    """Every check that holds on every row of a list of mappings in ``paths`` (files under ``root``), list by list.

    ``reference_roots`` are the agent's reference stores (:func:`reference_roots`) and ``tables`` its declared reference
    tables (:func:`reference_tables`): an id list in a file under a store, or a table's list, is copied as an ``in``
    set up to MAX_REFERENCE_STORE_MEMBERS rows, any other up to MAX_REFERENCE_MEMBERS."""
    lists = _swept_lists(root, paths)
    ids = _member_ids(root, lists, list(reference_roots or ()), tables)
    found: list[WorldCheck] = []
    for (rel, name), rows in lists.items():
        if len(rows) < MIN_CHECK_ROWS:
            continue
        where = _where(rel, name)
        row_paths = _row_paths(rows)
        checks = [{**c, 'source': where} for c in _conditional_checks(rows, row_paths)]
        checks += [{**c, 'source': f'{where} (members: {members})'} for c, members in _reference_checks(rows, row_paths, rel, ids)]
        checks += [{**c, 'source': where} for c in _computed_checks(rows)]
        found.extend(WorldCheck(where, len(rows), c) for c in checks)
    return found


def reference_store_checks(
    root: Path, paths: list[str], stores: list[Path], *, above: int = 0, tables: Collection[tuple[str, str]] = ()
) -> list[WorldCheck]:
    """The ``in`` checks of :func:`world_check_candidates` whose members are an id list of more than ``above`` rows in a
    reference store under ``stores`` or a declared reference table: the reference sweep alone, without the conditional
    and computed searches."""
    lists = _swept_lists(root, paths)
    ids = {
        m: v
        for m, v in _member_ids(root, lists, stores, tables).items()
        if len(v) > above and (_in_store(root, _members_list(m)[0], stores) or _in_table(*_members_list(m), tables))
    }
    found: list[WorldCheck] = []
    for (rel, name), rows in lists.items():
        if len(rows) < MIN_CHECK_ROWS or not ids:
            continue
        where = _where(rel, name)
        found.extend(
            WorldCheck(where, len(rows), {**c, 'source': f'{where} (members: {members})'}) for c, members in _reference_checks(rows, _row_paths(rows), rel, ids)
        )
    return found


def members_list(source: str) -> tuple[str, str] | None:
    """``(path, list)`` of the list an ``in`` set's ``(members: <path>#<list>.<field>)`` cites in a check's ``source``."""
    cited = _SOURCE.match(source.strip())
    return (Path(cited['mpath']).as_posix(), cited['mlist'] or '') if cited and cited['mpath'] else None


def members_path(source: str) -> str | None:
    """The file an ``in`` set's ``(members: <path>#<list>.<field>)`` cites in a check's ``source``, else None."""
    cited = _SOURCE.match(source.strip())
    return cited['mpath'] if cited and cited['mpath'] else None


def render_checks(found: list[WorldCheck]) -> str:
    """The checks as YAML to copy, one block per list of rows, each headed by the list it was measured on."""
    by_rows: dict[tuple[str, int], list[dict]] = {}
    for c in found:
        by_rows.setdefault((c.rows, c.count), []).append(c.check)
    blocks = [
        f'# {rows}: {count} rows. Copy each check verbatim under world_state.entity_types.<the type these rows are>.checks\n'
        + yaml.safe_dump(checks, sort_keys=False, allow_unicode=True, width=10_000, default_flow_style=None).rstrip()
        for (rows, count), checks in by_rows.items()
    ]
    return '\n'.join(blocks)


_EXPR_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.Name, ast.Load, ast.Store, ast.Constant,
    ast.Subscript, ast.Slice, ast.Call, ast.GeneratorExp, ast.ListComp, ast.comprehension, ast.List, ast.Tuple,
    ast.operator, ast.unaryop, ast.cmpop, ast.boolop, ast.expr_context,
)


def eval_expr(expr: str, row: dict) -> object:
    """``expr`` over ``row``'s fields: arithmetic, comparisons, subscripts, comprehensions and sum/min/max/abs/round/len,
    nothing else (no attribute, no other call) — the subset of the backend's simpleeval sandbox a computed check uses."""
    tree = expr_tree(expr)
    scope: dict = {**{str(k): v for k, v in row.items()}, **_EXPR_FUNCTIONS, '__builtins__': {}}
    return eval(compile(tree, '<check>', 'eval'), scope)  # noqa: S307 - expr_tree admits no attribute, import or other call


def expr_tree(expr: str) -> ast.Expression:
    """The parsed ``expr``; ValueError when it does not parse or steps outside the allowed subset."""
    try:
        tree = ast.parse(expr, mode='eval')
    except SyntaxError as exc:
        raise ValueError(f'`{expr}` does not parse ({exc.msg})') from exc
    for node in ast.walk(tree):
        if not isinstance(node, _EXPR_NODES) and type(node).__name__ != 'Index':  # ast.Index: a subscript below 3.9
            raise ValueError(f'`{type(node).__name__}` is not allowed in a computed check')
        if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or node.func.id not in _EXPR_FUNCTIONS or node.keywords):
            raise ValueError('a computed check calls only sum, min, max, abs, round and len')
        if isinstance(node, ast.Pow):
            raise ValueError('`**` is not allowed in a computed check')
        if isinstance(node, ast.Name) and node.id.startswith('__'):
            raise ValueError(f'`{node.id}` is not a row field')
    return tree


def rule_breaks(value: object, rule: object, row: dict) -> str | None:
    """Why ``value`` breaks ``rule`` (a literal, ``{in: [...]}`` or ``{expr, tolerance}``), or None when it holds."""
    if isinstance(rule, dict) and set(rule) == {'in'} and isinstance(rule['in'], list):
        return None if any(same_value(value, m) for m in rule['in']) else f'{value!r} is not in the set'
    if isinstance(rule, dict) and 'expr' in rule and set(rule) <= {'expr', 'tolerance'}:
        try:
            expected = eval_expr(str(rule['expr']), row)
        except Exception as exc:  # noqa: BLE001 - a row the expression cannot evaluate on breaks the check
            return f'`{rule["expr"]}` does not evaluate ({type(exc).__name__}: {exc})'
        tolerance = rule.get('tolerance')
        try:
            ok = same_value(value, expected, CHECK_TOLERANCE if tolerance is None else float(tolerance))
        except (TypeError, ValueError):
            return f'{value!r} cannot be compared with {expected!r}'
        return None if ok else f'{value!r} != {expected!r}'
    if isinstance(rule, dict):
        return f'{rule!r} is not a rule (a literal, {{in: [...]}} or {{expr: ..., tolerance: n}})'
    return None if same_value(value, rule) else f'{value!r} != {rule!r}'


_CHECK_PATH = re.compile(r'^[^.\[\]\s]+(\[\])?(\.[^.\[\]\s]+(\[\])?)*$')


def check_defect(check: object) -> str | None:
    """Why ``check`` is not a declaration the backend enforces (its ``world_checks.declaration_defect``), or None."""
    if not isinstance(check, dict):
        return 'a check is a mapping'
    if set(check) - {'when', 'then', 'source'}:
        return 'a check carries `when`, `then` and `source`, nothing else'
    source = check.get('source')
    if not isinstance(source, str) or not source.strip():
        return 'it cites no `source`: a check is measured on a data file of the agent and cites it'
    if _PRD.search(source):
        return f'`source: {source}` cites the PRD: a check is measured on the agent\'s own data, never on prose'
    when, then = check.get('when') or {}, check.get('then')
    if not isinstance(when, dict) or not isinstance(then, dict) or not then:
        return '`then` is a non-empty mapping of path -> rule, and `when` (optional) a mapping'
    for side, block in (('when', when), ('then', then)):
        for path, rule in block.items():
            if not isinstance(path, str) or not _CHECK_PATH.match(path):
                return f'`{side}` path {path!r} is not `a`, `a.b` or `a[].b`'
            if side == 'when' and '[]' in path:
                return f'`when` path {path!r} iterates a list; a condition reads one value'
            if not isinstance(rule, dict):
                continue
            if set(rule) == {'in'}:
                if not isinstance(rule['in'], list):
                    return f'`{side}.{path}`: `in` is a list of members'
            elif 'expr' in rule and set(rule) <= {'expr', 'tolerance'} and side == 'then' and '[]' not in path:
                tolerance = rule.get('tolerance')
                if tolerance is not None and (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or tolerance < 0):
                    return f'`{side}.{path}`: `tolerance` is a non-negative number'
                try:
                    expr_tree(str(rule['expr']))
                except ValueError as exc:
                    return f'`{side}.{path}`: {exc}'
            else:
                return f'`{side}.{path}`: a rule is a literal, {{in: [...]}}, or (on a `then` path reading one value) {{expr: ..., tolerance: n}}'
    return None


def _derived_members(root: Path, rel: str, name: str, field_: str) -> dict[str, object] | None:
    rows = data_lists(root, rel).get(name)
    if not rows or any(field_ not in r for r in rows):
        return None
    return {_key(r[field_]): r[field_] for r in rows}


def world_check_problems(check: object, roots: list[Path]) -> list[str]:
    """Every problem :func:`world_check_outcome` finds, refusals then stale-set ones; ``[]`` when the check stands."""
    refused, stale = world_check_outcome(check, roots)
    return [*refused, *stale]


def world_check_outcome(check: object, roots: list[Path]) -> tuple[list[str], list[str]]:
    """``(refused, stale)``: a declared world check re-measured on the data file its ``source`` cites.

    Refused: a check the backend would not enforce (:func:`check_defect`, the PRD cited included); a ``source`` naming
    no data file under ``roots``; an ``in`` set read off the cited rows themselves; a literal or computed rule any cited
    row breaks. Stale (a membership miss, the remedy being to refresh the set from its store): an ``in`` set that is not
    exactly the ``(members: ...)`` field of a list in another file, or that a cited row's value falls outside."""
    defect = check_defect(check)
    if defect is not None:
        return [defect], []
    assert isinstance(check, dict)
    source = str(check['source'])
    cited = _SOURCE.match(source.strip())
    root = next((r for r in roots if cited and data_lists(r, cited['path'])), None)
    if cited is None or root is None:
        return [f'`source: {source}` names no data file this verifier can re-measure (`<path>`, `<path>#<list>`, '
                'optionally ` (members: <path>#<list>.<field>)`; JSON, YAML, CSV or NDJSON, optionally gzipped)'], []
    lists = data_lists(root, cited['path'])
    rows = lists.get(cited['list'] or '')
    if rows is None:
        return [f'`{cited["path"]}` holds no list `{cited["list"] or ""}`; its lists are {", ".join(repr(n) for n in lists)}'], []
    when, then = check.get('when') or {}, check['then']
    problems: list[str] = []
    stale: list[str] = []
    for path, rule in then.items():
        if not (isinstance(rule, dict) and 'in' in rule):
            continue
        where = f'{cited["mpath"]}#{cited["mlist"]}.{cited["mfield"]}'
        if not cited['mpath'] or cited['mpath'] == cited['path']:
            problems.append(f'`{path}`: an `in` set cites its members in ANOTHER file, ` (members: <path>#<list>.<field>)` — a set read off the rows themselves is a sample, not a reference list')
            continue
        mroot = next((r for r in roots if data_lists(r, cited['mpath'])), None)
        derived = _derived_members(mroot, cited['mpath'], cited['mlist'], cited['mfield']) if mroot else None
        if derived is None:
            stale.append(f'`{path}`: its members cite `{where}`, which no row list carries')
        elif set(derived) != {_key(m) for m in rule['in']}:
            stale.append(_set_diff(path, derived, {_key(m) for m in rule['in']}, where))
    breaks: dict[bool, list[str]] = {True: [], False: []}  # keyed by: the broken rule is an `in` set
    for i, row in enumerate(rows):
        if any(rule_breaks(v, rule, row) is not None for path, rule in when.items() for v in check_values(row, str(path))):
            continue
        for path, rule in then.items():
            membership = isinstance(rule, dict) and 'in' in rule
            breaks[membership].extend(f'row {i} `{path}`: {why}' for v in check_values(row, str(path)) if (why := rule_breaks(v, rule, row)))
    rows_of = cited.group(0).split(' (')[0]
    for membership, found in breaks.items():
        if found:
            (stale if membership else problems).append(
                f'{len(found)} value(s) on the {len(rows)} real row(s) of `{rows_of}` break it ({"; ".join(found[:3])})'
            )
    return problems, stale


def _set_diff(path: str, derived: dict[str, object], members: set[str], where: str) -> str:
    missing, extra = sorted(set(derived) - members), sorted(members - set(derived))
    shown = '; '.join(part for part in (f'missing {", ".join(missing[:3])}' if missing else '', f'not in the file {", ".join(extra[:3])}' if extra else '') if part)
    return f'`{path}`: its members are not the {len(derived)} of `{where}` ({shown}); copy the set from the file'


def main(argv: list[str] | None = None) -> int:
    """Print the world checks the agent's real rows hold, as YAML to copy under `world_state.entity_types.<type>.checks`."""
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('root', type=Path, help="the agent's root; every path is relative to it")
    parser.add_argument('--profile', type=Path, help='plurai_sim/artifacts/data_profile.md (under the root): the files it names are swept')
    parser.add_argument('--data', action='append', default=[], help="a data file of the agent's real rows (seed output, fixture); repeatable")
    parser.add_argument(
        '--workdir', type=Path,
        help="the workdir holding .scan_corpus_roots and artifacts/corpus_roots.json (default: the profile's "
        '<workdir>/artifacts/ parent, else <root>/plurai_sim)',
    )
    parser.add_argument(
        '--reference-roots', nargs='+', action='extend', default=[],
        help='a file or directory the agent reads live as reference content (a catalog, a code table), beside the ones '
        'plurai_sim/.scan_corpus_roots and plurai_sim/artifacts/corpus_roots.json record; an id list under one is copied '
        f'as an `in` set up to {MAX_REFERENCE_STORE_MEMBERS} rows',
    )
    args = parser.parse_args(argv)
    if yaml is None:
        print('measured_candidates: PyYAML is required', file=sys.stderr)
        return 3
    root = args.root.resolve()
    profile = args.profile if args.profile is None or args.profile.is_absolute() or not (root / args.profile).is_file() else root / args.profile
    named = input_paths(profile.read_text(encoding='utf-8', errors='replace'), root) if profile else []
    workdir = args.workdir.resolve() if args.workdir else (profile.resolve().parent.parent if profile and profile.parent.name == 'artifacts' else None)
    recorded = reference_roots(root, workdir)
    given = [p for p in (_resolve_root(root, r) for r in args.reference_roots) if p is not None]
    for outside in (p for p in given if not p.is_relative_to(root)):
        print(f'# measured_candidates: {outside} is outside the agent root {root}; the sweep reads files under the root only', file=sys.stderr)
    stores = list(dict.fromkeys([*(recorded or []), *given]))
    config_path = (workdir or root / 'plurai_sim') / 'plurai.config.yaml'
    try:
        tables = reference_tables(yaml.safe_load(config_path.read_text(encoding='utf-8', errors='replace')))
    except (OSError, yaml.YAMLError):
        tables = {}
    if recorded is None and not given and not tables:
        print(f'# measured_candidates: no reference store is recorded (no plurai_sim/.scan_corpus_roots or artifacts/corpus_roots.json) '
              f'and no world type declares `{REFERENCE_ROWS_KEY}:`, so no id list above {MAX_REFERENCE_MEMBERS} rows is proposed as an '
              '`in` set; name one with --reference-roots', file=sys.stderr)
    else:
        print(f'# measured_candidates: reference stores: {", ".join(str(s) for s in stores) or "none resolves under the root"}; reference tables: '
              f'{", ".join(f"{_where(rel, name)} ({kind})" for (rel, name), kind in tables.items()) or "none"}', file=sys.stderr)
    paths = [*named, *(d.removeprefix('./') for d in args.data), *(rel for rel, _ in tables)]
    found = world_check_candidates(root, paths, stores, tables)
    if found:
        print(render_checks(found))
    if not found:
        print('# no world check holds on every real row of these files')
    return 0


if __name__ == '__main__':
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
