"""Export the org's published Knowledge articles as the Markdown snapshot the agent's corpus is built from.

An Agentforce agent answers from articles that live in the org, and the corpus ingest reads files.
This is the bridge, and it is a READ: it describes `Knowledge__kav` for its rich-text fields, queries
the latest Online version of each article, converts each body from HTML to Markdown and writes

    <workdir>/knowledge_snapshot/<UrlName>.md      title as `#`, a `Summary:` line, the body
    <workdir>/knowledge_snapshot.INDEX.json        per article: ids, version, UrlName, publish date, sha256

The index sits BESIDE the folder, not in it: the folder is a corpus root, so anything in it is hashed
by the scan contract and ingested as a document.

Scope: when the bundle binds a Data Library (`knowledge: rag_feature_config_id`), the library is read
through the Connect API (`einstein/data-libraries/{id}`). A Knowledge library narrows the snapshot to
the articles it holds: the article versions its own search index chunked, when exactly one index can
be it, else its `knowledgeConfig` rule applied to the Online articles. A library built from files or
web pages gets no article snapshot. Anything unread keeps every Online article, with a typed finding
saying why and who owns it.

Audience: the org's record of the agent (`BotDefinition.Type`, else the bundle's `agent_template`)
names who the agent answers. An article not known to be published to a channel that audience reads
is left out unless `plurai.config.yaml` says `knowledge_out_of_audience: include`.

The folder is recorded in `grounding.sources` (and in the bench manifest's `sources.corpus_roots` when
it exists), and each file's sha256 is stamped into `.corpus_scope`, so a later build can prove the
corpus it ingested is this snapshot (`--verify`).

Org access is the operator's own `sf` CLI login: `sf org display --json` hands over the instance URL
and a session token, which stays inside this process and is never printed or written. Nothing here
writes to the org. Stdlib-only; the setup skill copies it into the workdir beside
`rewire_agentforce.py`, which it imports.

Exit: 0 snapshot written (or verified) · 1 the org or the bundle could not be read, or `--verify`
found a difference · 3 no snapshot, and the typed finding printed says why.
"""

from __future__ import annotations

import argparse
import enum
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:  # below 3.11: the same str-valued enum, interpolating as its value (`f'{member}'`), as enum.StrEnum does

    class StrEnum(str, enum.Enum):
        def __str__(self) -> str:
            return str(self.value)

        def __format__(self, spec: str) -> str:
            return format(str(self.value), spec)

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True  # the import below would leave a __pycache__ in the customer's workdir

import rewire_agentforce as rewire  # noqa: E402 — the copied sibling, found beside this file

ARTICLE_OBJECT = 'Knowledge__kav'
ONLINE = "PublishStatus='Online' AND IsLatestVersion=true"
#: The standard columns each article row carries, besides its rich-text body fields.
ARTICLE_COLUMNS = (
    'Id', 'KnowledgeArticleId', 'ArticleNumber', 'VersionNumber', 'Title', 'UrlName', 'Summary', 'Language',
    'FirstPublishedDate', 'LastPublishedDate', 'RecordTypeId',
)
LIBRARY_KEY = 'rag_feature_config_id'
CORPUS_SCOPE = '.corpus_scope'
TOPOLOGY = 'artifacts/agent_topology.json'
#: The `.corpus_scope` keys this script owns. A re-export replaces its own lines and keeps every other.
STAMP_KEYS = ('snapshot:', 'snapshot_sha256:')
EXIT_NO_SNAPSHOT = 3


CONFIG = 'plurai.config.yaml'
#: The `plurai.config.yaml` key holding the customer's answer on articles outside the agent's audience.
OUT_OF_AUDIENCE_KEY = 'knowledge_out_of_audience'


class Finding(StrEnum):
    """What a snapshot could not be, or left out, typed. Who owns each one travels beside it (`Owner`)."""

    UNSCOPED = 'knowledge.snapshot_unscoped'
    SOURCE_NOT_ARTICLES = 'knowledge.source_not_articles'
    VISIBILITY_FILTERED = 'knowledge.snapshot_visibility_filtered'
    VISIBILITY_UNREADABLE = 'knowledge.visibility_unreadable'
    NOT_ENABLED = 'knowledge.not_enabled'
    NO_BODY_FIELD = 'knowledge.no_body_field'
    NO_ONLINE_ARTICLES = 'knowledge.no_online_articles'


class Owner(StrEnum):
    """Who a finding is about. `agent`: the org's or the bundle's configuration, recorded as a
    `potential` entry in `agent_findings.md`. `instrument`: a limit of this reader, relayed beside the
    snapshot line and never filed against the agent."""

    AGENT = 'agent'
    INSTRUMENT = 'instrument'


class ExportError(Exception):
    """The org or the bundle could not be read; the message says what to fix."""


class NotFound(ExportError):
    """The org answered 404 for a path."""


# --- the org ------------------------------------------------------------------------------------

OrgGet = Callable[[str], Any]
"""GET a REST path under `/services/data/v<api>/` (or a full `/services/...` path) and return its JSON."""


@dataclass(frozen=True)
class RestOrg:
    instance_url: str
    api_version: str
    token: str = field(repr=False)

    def get(self, path: str) -> Any:
        url = self.instance_url.rstrip('/') + (path if path.startswith('/services/') else f'/services/data/v{self.api_version}/{path}')
        request = urllib.request.Request(url, headers={'Authorization': f'Bearer {self.token}', 'Accept': 'application/json'})  # noqa: S310 — the org's own https URL
        try:
            with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
                return json.load(response)
        except urllib.error.HTTPError as error:
            code = _error_code(error)
            if error.code == 404 or code in ('NOT_FOUND', 'INVALID_TYPE'):
                raise NotFound(f'{path.split("?")[0]} ({code or error.code})') from None
            raise ExportError(f'the org answered {error.code} ({code or "no error code"}) for {path.split("?")[0]}') from None
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise ExportError(f'the org could not be read at {path.split("?")[0]} ({type(error).__name__})') from None


def _error_code(error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read() or b'null')
    except ValueError:
        return ''
    first = body[0] if isinstance(body, list) and body else body
    return str(first.get('errorCode') or '') if isinstance(first, dict) else ''


def connect(org: str | None) -> RestOrg:
    """The operator's `sf` login for `org` (the CLI's default target org when None). The token never leaves this process."""
    command = ['sf', 'org', 'display', '--json', *(['--target-org', org] if org else [])]
    named = org or "the sf CLI's default org"
    try:
        done = subprocess.run(command, capture_output=True, text=True, **rewire.utf8_child_text(), timeout=180, check=False)  # noqa: S603 — fixed argv, no shell
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ExportError(f'could not run the sf CLI ({type(error).__name__}); install it and log in to {named}') from None
    try:
        payload = json.loads(done.stdout)
    except ValueError:
        payload = {}
    result = payload.get('result') if isinstance(payload, dict) else None
    if done.returncode != 0 or not isinstance(result, dict) or not result.get('accessToken') or not result.get('instanceUrl'):
        reason = payload.get('name') if isinstance(payload, dict) else ''
        raise ExportError(f'the sf CLI has no usable login for {named}{f" ({reason})" if reason else ""}; run `sf org login web --alias <alias>` and re-run')
    return RestOrg(instance_url=str(result['instanceUrl']), api_version=str(result.get('apiVersion') or rewire.API_VERSION), token=str(result['accessToken']))


def json_object(body: Any, what: str) -> dict[str, Any]:
    """`body` when the org answered a JSON object; the typed error otherwise, never an AttributeError."""
    if not isinstance(body, dict):
        raise ExportError(f'the org answered {what} with a JSON {type(body).__name__} where an object was expected')
    return body


def query_all(get: OrgGet, soql: str) -> list[dict[str, Any]]:
    """Every row of `soql`, following `nextRecordsUrl` until the org says it is done."""
    what = f'a query on {soql.split(" FROM ", 1)[-1].split()[0]}'
    page = json_object(get('query?q=' + urllib.parse.quote(soql)), what)
    rows = [r for r in page.get('records') or [] if isinstance(r, dict)]
    while not page.get('done', True) and page.get('nextRecordsUrl'):
        page = json_object(get(page['nextRecordsUrl']), what)
        rows += [r for r in page.get('records') or [] if isinstance(r, dict)]
    return rows


def body_fields(describe: dict[str, Any]) -> list[tuple[str, str]]:
    """`(api name, label)` of each rich-text field, in the object's field order. A formula field can be
    `htmlFormatted` too, so the rich-text type is what decides, not the flag."""
    return [
        (str(f['name']), str(f.get('label') or f['name']))
        for f in describe.get('fields') or []
        if isinstance(f, dict) and f.get('type') == 'textarea' and f.get('extraTypeInfo') == 'richtextarea'
    ]


# --- scope --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LibraryRef:
    """The bundle's `rag_feature_config_id`, split per Agent Script's value shape `<KIND>_<record id>`
    (a bare record id has no kind). Only `record_id` is something the org can resolve; a value of
    neither shape has an empty `record_id` and is never sent to the org."""

    raw: str
    kind: str
    record_id: str


_LIBRARY_REF = re.compile(r'^(?:(?P<kind>[A-Za-z][A-Za-z0-9]*)_)?(?P<id>[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?)$')


def parse_library_ref(raw: str) -> LibraryRef | None:
    """The binding as a typed fact; None when the bundle binds nothing."""
    raw = raw.strip().strip('"\'')
    if not raw:
        return None
    match = _LIBRARY_REF.match(raw)
    return LibraryRef(raw=raw, kind=match['kind'] or '', record_id=match['id']) if match else LibraryRef(raw=raw, kind='', record_id='')


@dataclass(frozen=True)
class Library:
    """A bound Data Library as the org describes it (`einstein/data-libraries/{id}`)."""

    id: str
    name: str
    developer_name: str
    source_type: str
    """The org's own `sourceType` (`KNOWLEDGE`, ...), verbatim."""
    status: str
    knowledge_config: dict[str, Any] | None = None
    """`groundingSource.knowledgeConfig` for a Knowledge library: the rule that picks its articles."""


class MembershipProof(StrEnum):
    """Which read established what the library holds."""

    INDEX_CHUNKS = 'index_chunks'  # the library's own search index: the article versions it chunked
    LIBRARY_RULE = 'library_rule'  # the library's `knowledgeConfig` applied to the Online articles
    NONE = 'none'


@dataclass(frozen=True)
class IndexedArticle:
    """What the library knows about one article it holds."""

    version_id: str
    """The article VERSION id (`ka0…`) the index keys on."""
    chunk_count: int | None
    """The index's chunks of the library's content fields for it; None when membership came from the rule."""


class Unenumerated(StrEnum):
    """Why no read established which articles a Knowledge library holds."""

    CATEGORY_RULE = 'data_category_rule'  # it selects by data category, which no read here applies
    UNKNOWN_RULE_KEYS = 'unknown_rule_keys'  # its rule carries settings this export does not know
    NO_RULE = 'no_rule'  # the library read carries no article rule
    READS_REFUSED = 'reads_refused'  # this login can read neither its index nor the articles its rule picks


UNENUMERATED_WHY: dict[Unenumerated, str] = {
    Unenumerated.CATEGORY_RULE: 'it selects articles by data category, which this export cannot apply',
    Unenumerated.UNKNOWN_RULE_KEYS: 'its article rule carries settings this export does not know',
    Unenumerated.NO_RULE: 'the library carries no article rule',
    Unenumerated.READS_REFUSED: 'this login can read neither its search index nor the articles its rule picks',
}


@dataclass(frozen=True)
class LibraryIndex:
    """What a proven read says a library holds: its source kind (`articles`, `files` or `web`) and, for an
    articles library, the articles it holds, keyed by `KnowledgeArticleId` (the export's filter key)."""

    source: str
    article_ids: frozenset[str] | None = None
    articles: dict[str, IndexedArticle] = field(default_factory=dict)
    membership_proof: MembershipProof = MembershipProof.NONE
    index_facts: dict[str, Any] = field(default_factory=dict)
    unenumerated: Unenumerated | None = None
    """Set exactly when `article_ids` is None for an articles library: why its articles are unknown."""


class Narrowing(StrEnum):
    """Whether the snapshot is the bound library's own articles, typed for the index."""

    APPLIED = 'applied'  # narrowed to what the library holds
    NOT_SUPPORTED = 'not_supported'  # the library resolves, and no read established what it holds
    NO_LIBRARY = 'no_library'  # nothing is bound, or the bound id names no library this login reads
    REFUSED = 'refused'  # the library is built from something other than articles: no article snapshot


@dataclass(frozen=True)
class Scope:
    library: Library | None
    reason: str
    """`data_library` (narrowed to what it holds), `source_not_articles` (the library is built from
    files or web pages, so no article snapshot), or why every Online article was taken: `unbound` (no
    id in the bundle), `unresolved` (the id names no library this login reads), `unenumerated` (the
    library resolves, and no read established what it holds)."""
    ref: LibraryRef | None = None
    index: LibraryIndex | None = None

    @property
    def narrows(self) -> bool:
        return self.reason == 'data_library'

    @property
    def narrowing(self) -> Narrowing:
        return {
            'data_library': Narrowing.APPLIED,
            'unenumerated': Narrowing.NOT_SUPPORTED,
            'source_not_articles': Narrowing.REFUSED,
        }.get(self.reason, Narrowing.NO_LIBRARY)

    def described(self) -> str:
        if self.library is None:
            why = 'no Data Library is bound' if self.reason == 'unbound' else 'the bound Data Library id names no library this login can read'
            return f'every Online article in the org ({why})'
        named = f'the Data Library {self.library.name} ({self.library.id})'
        if self.reason == 'source_not_articles' and self.index is not None:
            return f'{named}, built from {self.index.source}, not Knowledge articles'
        if self.narrows:
            return named
        why = f': {UNENUMERATED_WHY[self.index.unenumerated]}' if self.index is not None and self.index.unenumerated is not None else ''
        return f'every Online article in the org ({named} resolves; which articles it holds could not be read{why})'


#: Who owns an unnarrowed or refused snapshot, per scope reason, and the one remedy its finding names.
#: Only an empty binding is the agent's configuration; every other reason is this reader's own limit.
SCOPE_REMEDY: dict[str, tuple[Owner, str]] = {
    'unbound': (Owner.AGENT, "bind the agent's knowledge action to the Data Library it should answer from, then re-run the export"),
    'unresolved': (
        Owner.INSTRUMENT,
        'nothing to change in the agent: this login cannot read the bound library, so the snapshot is every Online article; '
        'if the library holds only some articles, scenarios may ask about articles the agent cannot find',
    ),
    'unenumerated': (
        Owner.INSTRUMENT,
        'nothing to change in the agent: which articles the Data Library holds could not be read, so the snapshot is every Online article; '
        'if the library is built from files or web pages instead, build the corpus from those with /build-rag-corpus',
    ),
    'source_not_articles': (Owner.INSTRUMENT, "build the corpus from the library's own files or pages with /build-rag-corpus"),
}


def _no_articles_owner(scope: Scope, remedy: str) -> tuple[Owner, str]:
    """An org with nothing to export is the agent's configuration only when no library is bound: a bound
    library that could not be read may be built from files or web pages, where no article is expected."""
    if scope.reason == 'unbound':
        return Owner.AGENT, remedy
    return Owner.INSTRUMENT, f'if the bound library is built from files or web pages, build the corpus from those with /build-rag-corpus; otherwise {remedy}'


_SALESFORCE_ID = re.compile(r'^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$')
LIBRARIES = 'einstein/data-libraries'

# Evaluated at runtime, so `Optional`: `|` on types is 3.10+.
Resolver = Callable[[OrgGet, str], Optional[Library]]
IndexReader = Callable[[OrgGet, Library], Optional[LibraryIndex]]


def resolve_library(get: OrgGet, library_id: str) -> Library | None:
    """The Data Library `library_id` names, read through the Connect API; None when this login reads none.

    A library is not an sObject: its key prefix is in no sObject list and SOQL, describe and the UI API
    all refuse its type, so the Connect read is the one that describes it."""
    if not _SALESFORCE_ID.match(library_id):
        return None
    try:
        body = get(f'{LIBRARIES}/{library_id}')
    except ExportError:  # NotFound included: a 403 is as much this login's limit as a 404
        return None
    if not isinstance(body, dict) or not body.get('sourceType'):
        return None
    grounding = body.get('groundingSource')
    config = grounding.get('knowledgeConfig') if isinstance(grounding, dict) else None
    return Library(
        id=library_id,
        name=str(body.get('masterLabel') or body.get('developerName') or library_id),
        developer_name=str(body.get('developerName') or ''),
        source_type=str(body['sourceType']),
        status=str(body.get('status') or ''),
        knowledge_config=config if isinstance(config, dict) else None,
    )


#: The org's `sourceType` -> the source kind a snapshot is decided on. Only `KNOWLEDGE` has been read
#: from a live org; the others are the platform's other library kinds, matched by prefix. A value
#: matching none is never taken for articles: the library stays `unenumerated`.
SOURCE_KINDS: tuple[tuple[str, str], ...] = (('KNOWLEDGE', 'articles'), ('FILE', 'files'), ('UDMO', 'files'), ('WEB', 'web'))
#: The Data Cloud object Knowledge article versions are indexed from; a search index whose primary
#: object is this one indexes articles.
ARTICLE_DMO = 'KnowledgeArticleVersion__dlm'
_DMO_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]*__dlm$')
_ID_BATCH = 100


def _source_kind(source_type: str) -> str | None:
    upper = source_type.upper()
    return next((kind for prefix, kind in SOURCE_KINDS if upper == prefix or upper.startswith(prefix)), None)


def _dmo_field(field_name: str) -> str:
    """A core field's name in its Data Cloud copy: `Body__c` -> `Body_c__c`, `Title` -> `Title`."""
    return f'{field_name[:-3]}_c__c' if field_name.endswith('__c') else field_name


def _article_ids_of_versions(get: OrgGet, version_ids: set[str], *, public_only: bool) -> dict[str, str]:
    """`{version id: KnowledgeArticleId}` for each article version the org still has (and, for a library
    restricted to public articles, still publishes to the public knowledge base)."""
    ids = sorted(v for v in version_ids if _SALESFORCE_ID.match(v))
    mapping: dict[str, str] = {}
    for start in range(0, len(ids), _ID_BATCH):
        quoted = ', '.join(f"'{v}'" for v in ids[start : start + _ID_BATCH])
        for row in query_all(get, f'SELECT Id, KnowledgeArticleId FROM {ARTICLE_OBJECT} WHERE Id IN ({quoted}){" AND IsVisibleInPkb = true" if public_only else ""}'):
            if row.get('Id') and row.get('KnowledgeArticleId'):
                mapping[str(row['Id'])] = str(row['KnowledgeArticleId'])
    return mapping


def _index_chunks(get: OrgGet, library: Library) -> LibraryIndex | None:
    """Membership from the library's own search index, when exactly one index can be it.

    No read links a library to its search index. The link is taken only when it is unambiguous: the
    bound library is READY and is the org's one Knowledge library, and exactly one ready index is built
    over the article object. Anything else is None, and the rule read decides."""
    listed = json_object(get(LIBRARIES), 'the Data Library list').get('libraries') or []
    knowledge = [lib for lib in listed if isinstance(lib, dict) and _source_kind(str(lib.get('sourceType') or '')) == 'articles']
    if len(knowledge) != 1 or knowledge[0].get('libraryId') != library.id or library.status != 'READY':
        return None
    indexes = [
        row
        for row in query_all(get, 'SELECT SemanticSearchApiName, PrimaryDmo, ChunkDmo, RuntimeStatus FROM DataSemanticSearch')
        if row.get('PrimaryDmo') == ARTICLE_DMO
    ]
    if len(indexes) != 1 or indexes[0].get('RuntimeStatus') != 'READY' or not _DMO_NAME.match(str(indexes[0].get('ChunkDmo') or '')):
        return None
    chunk_dmo = str(indexes[0]['ChunkDmo'])
    chunks = query_all(get, f'SELECT SourceRecordId__c, DataSource__c FROM {chunk_dmo}')
    content = {_dmo_field(f) for f in (library.knowledge_config or {}).get('contentFields') or []}
    per_version: dict[str, int] = {}
    for chunk in chunks:
        version = str(chunk.get('SourceRecordId__c') or '')
        if version:
            per_version[version] = per_version.get(version, 0) + (chunk.get('DataSource__c') in content)
    public_only = bool((library.knowledge_config or {}).get('isRestrictToPublicArticle'))
    article_of = _article_ids_of_versions(get, set(per_version), public_only=public_only)
    articles = {article_of[v]: IndexedArticle(version_id=v, chunk_count=n if content else None) for v, n in per_version.items() if v in article_of}
    return LibraryIndex(
        source='articles',
        article_ids=frozenset(articles),
        articles=articles,
        membership_proof=MembershipProof.INDEX_CHUNKS,
        index_facts={
            'search_index': indexes[0].get('SemanticSearchApiName'),
            'chunk_dmo': chunk_dmo,
            'content_fields': sorted(content),
            'restrict_to_public': public_only,
            'chunks': len(chunks),
            'versions_not_taken': len(set(per_version) - set(article_of)),
        },
    )


#: The `knowledgeConfig` settings the rule read knows. Any other key could narrow the library in a way
#: this export would not apply, so a rule carrying one is not applied at all.
KNOWN_RULE_KEYS = frozenset(
    {'contentFields', 'dataCategorySelectionIds', 'isDataCategoryRuleEnabled', 'isRestrictToPublicArticle', 'primaryIndexField1', 'primaryIndexField2'}
)


def _unread(why: Unenumerated, **facts: Any) -> LibraryIndex:
    return LibraryIndex(source='articles', unenumerated=why, index_facts=facts)


def _library_rule(get: OrgGet, library: Library) -> LibraryIndex:
    """Membership from the library's `knowledgeConfig`, applied to the Online articles. A rule this
    export cannot apply whole (a data-category rule, or a setting it does not know) is typed as such,
    never applied in part."""
    config = library.knowledge_config
    if config is None:
        return _unread(Unenumerated.NO_RULE)
    unknown = sorted(set(config) - KNOWN_RULE_KEYS)
    if unknown:
        return _unread(Unenumerated.UNKNOWN_RULE_KEYS, keys=unknown)
    if config.get('isDataCategoryRuleEnabled') or config.get('dataCategorySelectionIds'):
        return _unread(Unenumerated.CATEGORY_RULE)
    where = ONLINE + (' AND IsVisibleInPkb = true' if config.get('isRestrictToPublicArticle') else '')
    rows = query_all(get, f'SELECT Id, KnowledgeArticleId FROM {ARTICLE_OBJECT} WHERE {where}')
    articles = {str(r['KnowledgeArticleId']): IndexedArticle(version_id=str(r['Id']), chunk_count=None) for r in rows if r.get('KnowledgeArticleId') and r.get('Id')}
    return LibraryIndex(
        source='articles',
        article_ids=frozenset(articles),
        articles=articles,
        membership_proof=MembershipProof.LIBRARY_RULE,
        index_facts={'restrict_to_public': bool(config.get('isRestrictToPublicArticle')), 'content_fields': list(config.get('contentFields') or [])},
    )


def read_library_index(get: OrgGet, library: Library) -> LibraryIndex | None:
    """What a resolved library holds: its source kind, then its articles from the index's own chunks,
    else from its rule. A read this login may not make falls to the next one. None only for a source
    kind no table names; an articles library whose articles stay unknown says why (`unenumerated`)."""
    kind = _source_kind(library.source_type)
    if kind is None:
        return None
    if kind != 'articles':
        return LibraryIndex(source=kind)
    for read in (_index_chunks, _library_rule):
        try:
            index = read(get, library)
        except ExportError:  # a Data Cloud object this login cannot read, or a 403: the next read decides
            continue
        if index is not None:
            return index
    return _unread(Unenumerated.READS_REFUSED)


def decide_scope(get: OrgGet, ref: LibraryRef | None, *, resolver: Resolver, index_reader: IndexReader) -> Scope:
    """The scope rule. Both reads are named at every call: a defaulted reader is one a caller drops."""
    if ref is None:
        return Scope(library=None, reason='unbound')
    library = resolver(get, ref.record_id) if ref.record_id else None
    if library is None:
        return Scope(library=None, reason='unresolved', ref=ref)
    index = index_reader(get, library)
    if index is None:
        return Scope(library=library, reason='unenumerated', ref=ref)
    if index.source != 'articles':
        return Scope(library=library, reason='source_not_articles', ref=ref, index=index)
    if index.article_ids is None:
        return Scope(library=library, reason='unenumerated', ref=ref, index=index)
    return Scope(library=library, reason='data_library', ref=ref, index=index)


# --- the audience -------------------------------------------------------------------------------


class Audience(StrEnum):
    EXTERNAL = 'external'  # customers or partners, through a site, portal or messaging channel
    INTERNAL = 'internal'  # the org's own users, in the Salesforce app


#: The org's own agent type (`BotDefinition.Type`, a closed platform picklist) and who that agent
#: answers. A type not listed here (`Bot`, `AgentforceOrchestrator`) counts as EXTERNAL.
AUDIENCE_BY_AGENT_TYPE: dict[str, Audience] = {'ExternalCopilot': Audience.EXTERNAL, 'InternalCopilot': Audience.INTERNAL}
#: The offline fallback, when the org's agent record cannot be read: an agent template's family (the
#: `config.agent_template` value before `__`). A template not listed here, or none, counts as EXTERNAL:
#: the conservative direction for what leaves the org.
AUDIENCE_BY_TEMPLATE_FAMILY: dict[str, Audience] = {
    'SvcCopilotTmpl': Audience.EXTERNAL,
    'EmployeeCopilot': Audience.INTERNAL,
}
#: `Knowledge__kav`'s channel flags: the internal app, the public knowledge base, the customer portal, the partner portal.
VISIBILITY_COLUMNS: dict[str, str] = {'app': 'IsVisibleInApp', 'pkb': 'IsVisibleInPkb', 'csp': 'IsVisibleInCsp', 'prm': 'IsVisibleInPrm'}
#: The channels each audience reads an article through.
AUDIENCE_CHANNELS: dict[Audience, tuple[str, ...]] = {Audience.EXTERNAL: ('pkb', 'csp', 'prm'), Audience.INTERNAL: ('app',)}


@dataclass(frozen=True)
class AgentAudience:
    audience: Audience
    decided_by: str
    """`agent_type` (the org's agent record), `template` (the bundle's template family) or `default`."""
    agent_type: str | None = None


_DEVELOPER_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')


def agent_type(get: OrgGet, developer_name: str) -> str | None:
    """The agent's `BotDefinition.Type` as the org records it; None when it cannot be read."""
    if not _DEVELOPER_NAME.match(developer_name):
        return None
    try:
        rows = query_all(get, f"SELECT Type FROM BotDefinition WHERE DeveloperName = '{developer_name}'")
    except ExportError:
        return None
    types = {str(r['Type']) for r in rows if r.get('Type')}
    return types.pop() if len(types) == 1 else None


def audience_of(org_type: str | None, template: str) -> AgentAudience:
    """Who the agent answers: the org's agent type first, the bundle's template family when the org's
    record is unreadable, EXTERNAL when neither names one."""
    if org_type is not None:
        audience = AUDIENCE_BY_AGENT_TYPE.get(org_type)
        return AgentAudience(audience or Audience.EXTERNAL, 'agent_type' if audience else 'default', org_type)
    audience = AUDIENCE_BY_TEMPLATE_FAMILY.get(template.split('__', 1)[0] if template else '')
    return AgentAudience(audience, 'template') if audience is not None else AgentAudience(Audience.EXTERNAL, 'default')


#: The key as YAML reads it at the top level: optionally quoted, with or without space before the colon.
_OUT_OF_AUDIENCE_LINE = re.compile(rf'^(["\']?){OUT_OF_AUDIENCE_KEY}\1\s*:(?P<value>.*)$')


def out_of_audience_decision(workdir: Path) -> str:
    """`include` or `exclude` as `plurai.config.yaml` records the customer's answer, else `undecided`. A
    key set twice is refused: which line is the answer is not something to guess."""
    path = workdir / CONFIG
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return 'undecided'
    values = [match['value'] for match in map(_OUT_OF_AUDIENCE_LINE.match, lines) if match]
    if len(values) > 1:
        raise ExportError(f'{path} sets `{OUT_OF_AUDIENCE_KEY}` {len(values)} times; keep the one line that is the customer\'s answer and re-run')
    if not values:
        return 'undecided'
    value = values[0].split('#', 1)[0].strip().strip('"\'').lower()
    return value if value in ('include', 'exclude') else 'undecided'


@dataclass(frozen=True)
class BundleFacts:
    library: LibraryRef | None
    agent_template: str
    developer_name: str


def bundle_facts(project: Path, bundle_dir: Path | None) -> BundleFacts:
    """The facts the export reads off the bundle: the knowledge binding, the agent template and the agent's name."""
    bundle_dir = bundle_dir or rewire._default_bundle_dir(rewire.DxProject.locate(project))  # noqa: SLF001 — the sibling's one resolver
    bundle = rewire.parse_bundle(rewire.sole_agent_file(bundle_dir).read_text(encoding='utf-8'))
    raw, _ = rewire.knowledge_values(bundle).get(LIBRARY_KEY, ('', 0))
    return BundleFacts(library=parse_library_ref(raw), agent_template=_config_value(bundle.lines, 'agent_template'), developer_name=bundle.developer_name)


def _config_value(lines: list[str], key: str) -> str:
    """A scalar under the top-level `config:` block, unquoted; '' when absent."""
    for i, line in enumerate(lines):
        if rewire._indent(line) == 0 and line.strip() == 'config:':  # noqa: SLF001
            for entry in lines[i + 1 : rewire._block_end(lines, i, 0)]:  # noqa: SLF001
                if rewire._indent(entry) == rewire.VARIABLE_INDENT and entry.strip().split(':', 1)[0] == key:  # noqa: SLF001
                    return entry.split(':', 1)[1].strip().strip('"\'')
            break
    return ''


# --- HTML to Markdown ---------------------------------------------------------------------------

_INLINE_MARK = {'strong': '**', 'b': '**', 'em': '*', 'i': '*', 'code': '`'}
_BLOCKS = frozenset({'p', 'div', 'section', 'article', 'header', 'footer', 'main', 'aside', 'figure', 'figcaption', 'address', 'dl', 'dt', 'dd'})
_SKIPPED = frozenset({'script', 'style', 'head', 'title'})


class _Markdown(HTMLParser):
    """Rich-text HTML to Markdown blocks: headings, paragraphs, lists (nested, ordered), tables, links,
    bold/italic/code, block quotes, preformatted text and rules. Unknown tags pass their text through."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, Any]] = []
        self.inline: list[str] = []
        self.heading = 0
        self.lists: list[dict[str, Any]] = []  # {'ordered', 'n', 'indent', 'emitted', 'cell'}
        self.list_lines: list[str] = []
        self.cell_lead = ''  # text pending in a table cell when a list starts directly inside it
        self.cell_items: list[str] = []  # that list's items, flattened for the cell's single line
        self.cell_has_list = False  # a list already flattened into this cell: the next one joins it with '; ' too
        # a stack, not a single slot: a <table> nested inside a cell (a layout/comparison table pasted from
        # Word/Docs) is real content, and each level needs its own {'rows', 'row', 'row_header', 'in_row',
        # 'prefix'} so the outer table's state survives the inner one opening and closing.
        self.tables: list[dict[str, Any]] = []
        self.links: list[str] = []
        self.quote = 0
        self.pre = 0
        self.pre_start = 0  # where the open <pre>'s own text starts in `inline`: a cell's or item's lead-in is not code
        self.skip = 0
        self.fresh_mark: int | None = None  # the index of an emphasis mark no text has followed yet

    # inline text
    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if not self.pre:
            data = re.sub(r'\s+', ' ', data)
            if self.fresh_mark is not None and self.fresh_mark == len(self.inline) - 1 and data.startswith(' '):
                self.inline.insert(self.fresh_mark, ' ')  # `<b> x</b>`: the space goes outside the mark
                data = data.lstrip(' ')
        if data:
            self.fresh_mark = None
        self.inline.append(data)

    def _take(self) -> str:
        text = ''.join(self.inline)
        self.inline = []
        return text if self.pre else re.sub(r'[ \t]*\n[ \t]*', '\n', re.sub(r'[ \t]+', ' ', text)).strip()

    def _flush(self) -> None:
        if self.tables or self.lists:
            return
        text = self._take()
        if text:
            prefix = '> ' * self.quote
            self.blocks.append(('p', '\n'.join(prefix + line for line in text.split('\n'))))

    # lists
    def _emit_item(self, continuation: bool = False) -> None:
        level = self.lists[-1]
        text = self._take().replace('\n', ' ')
        if not text:
            return
        if level['cell']:  # a table cell is one markdown line: flatten the item instead of bulleting it
            self.cell_items.append(text)
        else:
            marker = f'{level["n"]}. ' if level['ordered'] else '- '
            indent = ' ' * level['indent']
            self.list_lines.append(f'{indent}{" " * len(marker) if continuation else marker}{text}')
        level['emitted'] = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self._flush()
            self.heading = int(tag[1])
        elif tag in _BLOCKS:
            if self.lists or self.tables:
                self.inline.append(' ')
            else:
                self._flush()
        elif tag in ('ul', 'ol'):
            # cell mode is decided by whether a table cell is open RIGHT NOW, not by what the enclosing
            # list (if any) already settled on — an outer document-level list can still be on the stack
            # while we're deep inside one of its `<li>`'s tables, and a list starting in that table's own
            # cell must flatten into the cell, not bullet into the outer list.
            cell = bool(self.tables)
            indent = 0
            if cell:
                if self.lists and self.lists[-1]['cell']:
                    self._emit_item()  # continuing an already-open cell list: flush this item's pending text
                elif self.cell_has_list:
                    # a sibling list already flattened earlier in this same cell: treat its rendered text
                    # as just another item, so every list sharing the cell joins with the same '; '
                    lead = self._take()
                    self.cell_lead, self.cell_items = '', ([lead] if lead else [])
                else:
                    self.cell_lead, self.cell_items = self._take(), []  # salvage the cell's lead-in text
            elif self.lists:
                self._emit_item()  # a nested list (or a table, earlier) can't interrupt an item mid-text either
                parent = self.lists[-1]
                indent = parent['indent'] + (len(f'{parent["n"]}. ') if parent['ordered'] else 2)
            else:
                self._flush()
            self.lists.append({'ordered': tag == 'ol', 'n': 0, 'indent': indent, 'emitted': False, 'cell': cell})
        elif tag == 'li' and self.lists:
            self._emit_item()  # text directly under <ul>/<ol>, never wrapped in a prior <li>: not dropped
            self.lists[-1]['n'] += 1
            self.lists[-1]['emitted'] = False
        elif tag == 'table':
            if self.tables:
                # nested: whatever's pending belongs to the OUTER cell this table lives in, not to a
                # paragraph or list item about to close — carry it forward and reattach once this table
                # flattens back into that same cell (below, at `</table>`).
                prefix = self._take()
            else:
                prefix = ''
                if self.lists:
                    self._emit_item()  # a table can't interrupt list construction; salvage the lead-in text first
                else:
                    self._flush()
            self.tables.append({'rows': [], 'row': [], 'row_header': True, 'in_row': False, 'prefix': prefix, 'caption': ''})
        elif tag == 'caption' and self.tables:
            stray = self._take()  # <caption> is a <table>'s first child; anything pending isn't its own
            if stray:
                self.tables[-1]['rows'].append(([stray], False))
        elif tag == 'tr' and self.tables:
            ctx = self.tables[-1]
            stray = self._take()
            if stray:  # text directly under <table>, never wrapped in a <tr>: its own cell, not dropped
                ctx['row'].append(stray)
            if ctx['row']:  # a td/th accumulated with no <tr> around it at all: commit it before resetting
                ctx['rows'].append((ctx['row'], ctx['row_header']))
            ctx['row'], ctx['row_header'], ctx['in_row'] = [], True, True
        elif tag in ('td', 'th') and self.tables:
            ctx = self.tables[-1]
            stray = self._take()
            if stray:  # text directly under <tr>, never wrapped in a <td>/<th>: its own cell, not dropped
                ctx['row'].append(stray)
            ctx['row_header'] = ctx['row_header'] and tag == 'th'
            self.cell_has_list = False  # a new cell: the next list to start in it is its first, not a sibling
        elif tag in _INLINE_MARK:
            self.inline.append(_INLINE_MARK[tag])
            self.fresh_mark = len(self.inline) - 1
        elif tag == 'a':
            self.links.append(dict(attrs).get('href') or '')
            self.inline.append('[')
        elif tag == 'br':
            self.inline.append(' ' if self.lists or self.tables else '\n')
        elif tag == 'img':
            alt = (dict(attrs).get('alt') or '').strip()
            if alt:
                self.inline.append(alt)
        elif tag == 'hr':
            self._flush()
            self.blocks.append(('hr', None))
        elif tag == 'blockquote':
            self._flush()
            self.quote += 1
        elif tag == 'pre':
            self._flush()
            if not self.pre:
                self.pre_start = len(self.inline)
            self.pre += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6') and self.heading:
            text = self._take().replace('\n', ' ')
            if text:
                self.blocks.append(('h', (self.heading, text)))
            self.heading = 0
        elif tag in _BLOCKS and not (self.lists or self.tables):
            self._flush()
        elif tag == 'li' and self.lists:
            self._emit_item(continuation=self.lists[-1]['emitted'])
        elif tag in ('ul', 'ol') and self.lists:
            if self.inline and not self.lists[-1]['emitted']:
                self._emit_item()
            level = self.lists.pop()
            # a cell list is done once we've popped back OUT of every cell-mode level — not necessarily
            # out of every list: an outer document-level list can still be open around the table this
            # cell lives in (Defect A), and it must stay untouched, waiting for its own `</ul>`/`</ol>`.
            still_in_cell = self.lists and self.lists[-1]['cell']
            if level['cell'] and not still_in_cell:
                joined = '; '.join(self.cell_items)
                text = f'{self.cell_lead} {joined}' if self.cell_lead and joined else self.cell_lead or joined
                if text:
                    self.inline.append(text)
                self.cell_lead, self.cell_items = '', []
                self.cell_has_list = True
            elif not self.lists:
                self.blocks.append(('list', self.list_lines))
                self.list_lines = []
        elif tag == 'caption' and self.tables:
            text = self._take()
            if text:
                ctx = self.tables[-1]
                ctx['caption'] = f"{ctx['caption']} {text}".strip() if ctx['caption'] else text
        elif tag in ('td', 'th') and self.tables:
            self.tables[-1]['row'].append(self._take())  # raw: escaped once, at `_table_lines`, on render
        elif tag == 'tr' and self.tables:
            ctx = self.tables[-1]
            if ctx['row']:
                ctx['rows'].append((ctx['row'], ctx['row_header']))
            ctx['row'] = []
            ctx['in_row'] = False
        elif tag == 'table' and self.tables:
            ctx = self.tables.pop()
            stray = self._take()
            if stray:  # trailing text under an open <tr> (its own cell) or directly under <table> (its own row)
                ctx['row'].append(stray) if ctx['in_row'] else ctx['rows'].append(([stray], False))
            if ctx['row']:  # a </td>/</th> closed with no </tr> ever committing it: still don't lose it
                ctx['rows'].append((ctx['row'], ctx['row_header']))
            if self.tables:
                # nested: this table IS the cell's content, joined onto whatever preceded it (its own
                # lead-in text and <caption>, if any) — a table can't nest inside a markdown table cell,
                # so it flattens to the cell's one line instead.
                flat = '; '.join(' / '.join(cells) for cells, _ in ctx['rows'])
                lead = ' '.join(part for part in (ctx['prefix'], ctx['caption']) if part)
                combined = f'{lead} {flat}'.strip()
                if combined:
                    self.inline.append(combined)
            else:
                if ctx['caption']:  # a line above the table, not a fake header row inside it
                    self.blocks.append(('p', ctx['caption']))
                if ctx['rows']:
                    self.blocks.append(('table', ctx['rows']))
        elif tag in _INLINE_MARK:
            mark = _INLINE_MARK[tag]
            if self.fresh_mark is not None and self.fresh_mark == len(self.inline) - 1 and self.inline[-1] == mark:
                self.inline.pop()  # an empty emphasis renders as nothing
                self.fresh_mark = None
            else:
                trailing = ''
                while self.inline and self.inline[-1].endswith(' '):
                    trailing = ' '
                    self.inline[-1] = self.inline[-1].rstrip(' ')
                    if not self.inline[-1]:
                        self.inline.pop()
                self.inline.append(mark + trailing)
        elif tag == 'a' and self.links:
            href = self.links.pop()
            self.inline.append(f']({href})' if href else ']')
        elif tag == 'blockquote':
            self._flush()
            self.quote = max(0, self.quote - 1)
        elif tag == 'pre':
            lead, self.inline = self.inline[: self.pre_start], self.inline[self.pre_start :]
            text = self._take()
            self.inline = lead
            self.pre = max(0, self.pre - 1)
            if text.strip():
                if self.lists or self.tables:  # a fenced block can't sit inside a list item or table cell
                    self.inline.append(_code_span(text.strip()))
                else:
                    self.blocks.append(('pre', text.strip('\n')))

    def close(self) -> None:
        super().close()
        while self.lists:
            self.handle_endtag('ol' if self.lists[-1]['ordered'] else 'ul')
        while self.tables:
            self.handle_endtag('table')
        self._flush()


def _code_span(text: str) -> str:
    """`text` as one CommonMark inline code span: a fence one backtick longer than the longest backtick run
    inside it, padded with a space when `text` starts or ends with a backtick so the fence can't absorb it.
    Not table escaping — a `|` in it is still escaped by `_escape_cell` when its cell renders."""
    fence = '`' * (max(map(len, re.findall('`+', text)), default=0) + 1)
    pad = ' ' if text.startswith('`') or text.endswith('`') else ''
    return f'{fence}{pad}{text}{pad}{fence}'


def _escape_cell(text: str) -> str:
    """A markdown table cell is one output line: collapse a newline to a space, then escape the two
    characters that would otherwise corrupt the row — the backslash FIRST, so escaping the pipe right
    after can't be misread as already-escaped. This is the ONLY place cell text is escaped: content
    stays raw everywhere it's captured, flattened (a nested table or list into its cell) or salvaged
    (stray text with no <tr>/<td> wrapper), so nesting never compounds the escaping."""
    return text.replace('\n', ' ').replace('\\', '\\\\').replace('|', '\\|')


def _table_lines(rows: list[tuple[list[str], bool]]) -> list[str]:
    width = max(len(cells) for cells, _ in rows)
    padded = [[_escape_cell(c) for c in cells] + [''] * (width - len(cells)) for cells, _ in rows]
    lines = [f'| {" | ".join(padded[0])} |', '|' + '---|' * width]
    return lines + [f'| {" | ".join(cells)} |' for cells in padded[1:]]


def html_to_markdown(html: str, *, top: int = 2) -> str:
    """`html` as Markdown blocks. Headings are shifted so the highest one in the body is level `top`
    (the article title owns `#`); the rest keep their relative depth."""
    parser = _Markdown()
    parser.feed(html or '')
    parser.close()
    levels = [payload[0] for kind, payload in parser.blocks if kind == 'h']
    shift = top - min(levels) if levels else 0
    out = []
    for kind, payload in parser.blocks:
        if kind == 'h':
            out.append(f'{"#" * max(1, min(6, payload[0] + shift))} {payload[1]}')
        elif kind == 'p':
            out.append(payload)
        elif kind == 'list':
            out.append('\n'.join(payload))
        elif kind == 'table':
            out.append('\n'.join(_table_lines(payload)))
        elif kind == 'pre':
            out.append(f'```\n{payload}\n```')
        elif kind == 'hr':
            out.append('---')
    return '\n\n'.join(block for block in out if block)


def article_markdown(record: dict[str, Any], fields: list[tuple[str, str]]) -> str:
    """One article: its title as `#`, its summary as a `Summary:` line, then its body."""
    parts = [f'# {" ".join(str(record.get("Title") or record.get("UrlName") or "").split())}']
    summary = ' '.join(str(record.get('Summary') or '').split())
    if summary:
        parts.append(f'Summary: {summary}')
    for name, label in fields:
        body = html_to_markdown(str(record.get(name) or ''), top=3 if len(fields) > 1 else 2)
        if body:
            parts.extend([f'## {label}', body] if len(fields) > 1 else [body])
    return '\n\n'.join(parts) + '\n'


# --- the snapshot -------------------------------------------------------------------------------


def _file_name(record: dict[str, Any], taken: set[str]) -> str:
    stem = re.sub(r'[^A-Za-z0-9._-]+', '-', str(record.get('UrlName') or record.get('ArticleNumber') or record.get('Id') or 'article')).strip('-.') or 'article'
    name = f'{stem}.md'
    if name in taken:  # one UrlName per language: a translation is its own file
        name = f'{stem}.{re.sub(r"[^A-Za-z0-9_-]+", "-", str(record.get("Language") or record.get("Id")))}.md'
    taken.add(name)
    return name


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Export:
    """What one export decided and wrote; the CLI prints it and the tests read it."""

    scope: Scope | None
    articles: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, str]] = field(default_factory=list)
    body_fields: list[tuple[str, str]] = field(default_factory=list)
    audience: dict[str, Any] | None = None

    @property
    def written(self) -> bool:
        return bool(self.articles)


def _finding(code: Finding, owner: Owner, title: str, severity: str, evidence: str, remedy: str) -> dict[str, str]:
    return {'id': str(code), 'owner': str(owner), 'title': title, 'severity': severity, 'evidence': evidence, 'how_to_confirm': remedy}


def _membership(scope: Scope, article_id: str) -> dict[str, Any]:
    """`in_library` (true, or `unknown` when no read established what the library holds; a non-member is
    never exported) and the chunks the library's index holds for the article."""
    indexed = scope.index.articles.get(article_id) if scope.narrows and scope.index is not None else None
    return {'in_library': True if scope.narrows else 'unknown', 'indexed_chunks': indexed.chunk_count if indexed else None}


def _visibility(row: dict[str, Any], read: list[str]) -> dict[str, bool]:
    return {channel: row.get(VISIBILITY_COLUMNS[channel]) is True for channel in read}


def export(get: OrgGet, workdir: Path, bundle: BundleFacts, *, resolver: Resolver, index_reader: IndexReader) -> Export:
    """Read the org, write the snapshot and its index. Every outcome short of an unreadable org is typed."""
    decision = out_of_audience_decision(workdir)  # first: a config that cannot be read as one answer stops before any org read
    scope = decide_scope(get, bundle.library, resolver=resolver, index_reader=index_reader)
    if scope.reason == 'source_not_articles':
        owner, remedy = SCOPE_REMEDY[scope.reason]
        result = Export(
            scope=scope,
            findings=[
                _finding(
                    Finding.SOURCE_NOT_ARTICLES,
                    owner,
                    "The agent's library is not built from Knowledge articles",
                    'medium',
                    f'the knowledge action searches {scope.described()}, so an article snapshot would be an unrelated corpus',
                    remedy,
                )
            ],
        )
        return _record(workdir, result)
    try:
        describe = json_object(get(f'sobjects/{ARTICLE_OBJECT}/describe'), f'the {ARTICLE_OBJECT} describe')
    except NotFound:
        owner, remedy = _no_articles_owner(scope, 'check that Lightning Knowledge is enabled and that this login can read articles')
        finding = _finding(
            Finding.NOT_ENABLED,
            owner,
            'The org has no Knowledge articles object',
            'high',
            f'`{ARTICLE_OBJECT}` is not readable in the org, so there is no article to export',
            remedy,
        )
        return _record(workdir, Export(scope=scope, findings=[finding]))
    fields = body_fields(describe)
    if not fields:
        owner, remedy = _no_articles_owner(scope, 'check which field the article layout stores the body in')
        finding = _finding(
            Finding.NO_BODY_FIELD,
            owner,
            'The knowledge articles have no rich-text body field',
            'medium',
            f'`{ARTICLE_OBJECT}` declares no rich-text field, so an article carries a title and a summary and nothing to answer from',
            remedy,
        )
        return _record(workdir, Export(scope=scope, findings=[finding]))
    present = {str(f['name']) for f in describe.get('fields') or [] if isinstance(f, dict)}
    who = audience_of(agent_type(get, bundle.developer_name), bundle.agent_template)
    audience = who.audience
    read = [channel for channel, column in VISIBILITY_COLUMNS.items() if column in present]
    readable = any(channel in read for channel in AUDIENCE_CHANNELS[audience])
    columns = [c for c in ARTICLE_COLUMNS if c in present] + [VISIBILITY_COLUMNS[c] for c in read] + [name for name, _ in fields]
    rows = query_all(get, f'SELECT {", ".join(columns)} FROM {ARTICLE_OBJECT} WHERE {ONLINE} ORDER BY UrlName')
    if scope.narrows and scope.index is not None and scope.index.article_ids is not None:
        rows = [r for r in rows if r.get('KnowledgeArticleId') in scope.index.article_ids]
    if scope.library is not None and (scope.library.knowledge_config or {}).get('isRestrictToPublicArticle'):
        # A setting the library states is applied whatever else about it could not be read: taking
        # every Online article never takes one the library itself would not.
        rows = [r for r in rows if r.get(VISIBILITY_COLUMNS['pkb']) is True]
    result = Export(scope=scope, body_fields=fields)
    if not rows:
        owner, remedy = _no_articles_owner(scope, 'publish the articles the agent should answer from, then re-run the export')
        evidence = f'no `{ARTICLE_OBJECT}` row is Online in {scope.described()}'
        result.findings.append(_finding(Finding.NO_ONLINE_ARTICLES, owner, 'The knowledge search has no published article to find', 'high', evidence, remedy))
        return _record(workdir, result)
    # An article whose audience channels cannot be read is not known to be in the audience: it waits
    # on the same answer as one known to be outside it.
    in_audience = [readable and any(_visibility(r, read).get(c) for c in AUDIENCE_CHANNELS[audience]) for r in rows]
    outside = [r for r, inside in zip(rows, in_audience) if not inside]
    if outside and decision != 'include':
        rows = [r for r, inside in zip(rows, in_audience) if inside]
    result.audience = {
        'audience': str(audience),
        'agent_type': who.agent_type,
        'agent_template': bundle.agent_template or None,
        'decided_by': who.decided_by,
        'channels': list(AUDIENCE_CHANNELS[audience]),
        'visibility_read': read,
        'out_of_audience': len(outside),
        'decision': decision,
        'excluded': [] if decision == 'include' else [{'article_id': r.get('KnowledgeArticleId'), 'version_id': r.get('Id')} for r in outside],
    }
    if not readable and decision != 'include':
        result.findings.append(
            _finding(
                Finding.VISIBILITY_UNREADABLE,
                Owner.INSTRUMENT,
                "Which channels each article is published to could not be read",
                'medium',
                f'`{ARTICLE_OBJECT}` lists none of the {audience} audience\'s channel flags, so none of the {len(outside)} Online article(s) is known to be '
                'published to it, and none was exported',
                f"check that this login can read the articles' channel visibility and re-run the export, or `{OUT_OF_AUDIENCE_KEY}: include` in {CONFIG} "
                'exports them on the next run',
            )
        )
    elif outside and decision != 'include':
        result.findings.append(
            _finding(
                Finding.VISIBILITY_FILTERED,
                Owner.INSTRUMENT,
                "Articles outside the agent's audience were left out of the snapshot",
                'low',
                f'{len(outside)} Online article(s) are published to none of the channels the {audience} audience reads ({", ".join(AUDIENCE_CHANNELS[audience])})',
                f'nothing to change in the agent; `{OUT_OF_AUDIENCE_KEY}: include` in {CONFIG} exports them on the next run',
            )
        )
    if not rows:
        return _record(workdir, result)
    folder = workdir / rewire.KNOWLEDGE_SNAPSHOT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    taken: set[str] = set()
    for row in rows:
        name = _file_name(row, taken)
        data = article_markdown(row, fields).encode('utf-8')
        (folder / name).write_bytes(data)
        result.articles.append(
            {
                'file': name,
                'article_id': row.get('KnowledgeArticleId'),
                'version_id': row.get('Id'),
                'article_number': row.get('ArticleNumber'),
                'version_number': row.get('VersionNumber'),
                'url_name': row.get('UrlName'),
                'language': row.get('Language'),
                'title': row.get('Title'),
                'summary': row.get('Summary'),
                'first_published_date': row.get('FirstPublishedDate'),
                'last_published_date': row.get('LastPublishedDate'),
                'article_type': row.get('RecordTypeId') or 'unknown',
                'visibility': _visibility(row, read),
                **_membership(scope, str(row.get('KnowledgeArticleId') or '')),
                'bytes': len(data),
                'sha256': _sha256(data),
            }
        )
    if not scope.narrows:
        owner, remedy = SCOPE_REMEDY[scope.reason]
        result.findings.insert(
            0,
            _finding(
                Finding.UNSCOPED,
                owner,
                "The knowledge snapshot is every published article, not the agent's bound library",
                'medium',
                f"the snapshot holds {len(rows)} article(s): {scope.described()}; the agent's live search may cover a different set",
                remedy,
            ),
        )
    return _record(workdir, result)


def _record(workdir: Path, result: Export) -> Export:
    """Write the index, drop files a previous export left, and record (or retract) the folder as a corpus source."""
    folder = workdir / rewire.KNOWLEDGE_SNAPSHOT_DIR
    keep = {a['file'] for a in result.articles}
    for stale in sorted(folder.glob('*.md')) if folder.is_dir() else ():
        if stale.name not in keep:
            stale.unlink()
    scope = result.scope
    index = {
        'object': ARTICLE_OBJECT,
        'filter': ONLINE,
        'body_fields': [name for name, _ in result.body_fields],
        'source': {
            'scope': scope.reason if scope else None,
            'binding': {'raw': scope.ref.raw, 'kind': scope.ref.kind, 'record_id': scope.ref.record_id} if scope and scope.ref else None,
            'library': _library_record(scope.library, scope.index) if scope and scope.library else None,
            'narrowing': str(scope.narrowing) if scope else None,
            'described': scope.described() if scope else 'no article source',
        },
        'audience': result.audience,
        'snapshot_dir': rewire.KNOWLEDGE_SNAPSHOT_DIR,
        'exported_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'articles': result.articles,
        'findings': result.findings,
    }
    (workdir / rewire.KNOWLEDGE_SNAPSHOT_INDEX).write_text(json.dumps(index, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    source = f'{workdir.name}/{rewire.KNOWLEDGE_SNAPSHOT_DIR}'
    record_source(workdir, source, present=result.written)
    stamp_corpus_scope(workdir, result)
    return result


def _library_record(library: Library, index: LibraryIndex | None) -> dict[str, Any]:
    ids = sorted(index.article_ids) if index is not None and index.article_ids is not None else None
    return {
        'id': library.id,
        'name': library.name,
        'developer_name': library.developer_name,
        'source_type': library.source_type,
        'status': library.status,
        'source': index.source if index else None,
        'membership_proof': str(index.membership_proof) if index else str(MembershipProof.NONE),
        'index_facts': dict(index.index_facts) if index else {},
        'article_ids': ids,
    }


def retract(workdir: Path) -> None:
    """No snapshot stays recorded when this export could not finish: an earlier one may hold articles
    the customer's current answer keeps in the org, and the scan continues without one."""
    if not (workdir / rewire.KNOWLEDGE_SNAPSHOT_INDEX).is_file() and not (workdir / rewire.KNOWLEDGE_SNAPSHOT_DIR).is_dir():
        return
    try:
        _record(workdir, Export(scope=None))
    except (OSError, ValueError) as error:
        print(f'export-knowledge: the earlier snapshot could not be taken back ({type(error).__name__}); delete {workdir / rewire.KNOWLEDGE_SNAPSHOT_DIR}', file=sys.stderr)


def record_source(workdir: Path, source: str, *, present: bool) -> None:
    """The folder in `grounding.sources` on the map and `sources.corpus_roots` in the bench manifest — or
    out of both when there is no snapshot. Paths are relative to the workdir's parent, as the manifest resolves them."""

    def edit(listed: list[str]) -> list[str]:
        kept = [s for s in listed if s != source]
        return [*kept, source] if present else kept

    topology_path = workdir / TOPOLOGY
    if topology_path.is_file():
        topology = json.loads(topology_path.read_text(encoding='utf-8'))
        grounding = topology.get('grounding')
        if isinstance(grounding, dict):
            sources = edit([str(s) for s in grounding.get('sources') or []])
            if sources:
                grounding['sources'] = sources
            else:
                grounding.pop('sources', None)
            topology_path.write_text(json.dumps(topology, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    manifest_path = workdir / rewire.MANIFEST
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if isinstance(manifest.get('sources'), dict):
            manifest['sources']['corpus_roots'] = edit([str(s) for s in manifest['sources'].get('corpus_roots') or []])
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def stamp_corpus_scope(workdir: Path, result: Export) -> None:
    """Replace this script's lines in `.corpus_scope`, keeping every other line: one `snapshot:` line and
    one `snapshot_sha256: <file> <sha256>` per article."""
    path = workdir / CORPUS_SCOPE
    kept = [line for line in (path.read_text(encoding='utf-8').splitlines() if path.is_file() else []) if not line.startswith(STAMP_KEYS)]
    if result.written and result.scope is not None:
        day = time.strftime('%Y-%m-%d', time.gmtime())
        excluded = len((result.audience or {}).get('excluded') or [])
        left_out = f'; {excluded} article(s) outside the agent\'s audience left out' if excluded else ''
        kept.append(f'snapshot: {workdir.name}/{rewire.KNOWLEDGE_SNAPSHOT_DIR} — {len(result.articles)} article(s), {result.scope.described()}{left_out} ({day})')
        kept += [f'snapshot_sha256: {a["file"]} {a["sha256"]}' for a in result.articles]
    if kept:
        path.write_text('\n'.join(kept) + '\n', encoding='utf-8')
    elif path.is_file():
        path.unlink()


def verify(workdir: Path) -> tuple[int, list[str]]:
    """Is the snapshot on disk the one the export stamped — and, once the scan contract hashed it, the one it hashed?"""
    scope_path = workdir / CORPUS_SCOPE
    stamped = dict(
        line.removeprefix('snapshot_sha256:').split()[:2]
        for line in (scope_path.read_text(encoding='utf-8').splitlines() if scope_path.is_file() else [])
        if line.startswith('snapshot_sha256:') and len(line.split()) >= 3
    )
    if not stamped:
        return 1, [f'no snapshot stamp in {scope_path}; run the export first']
    folder = workdir / rewire.KNOWLEDGE_SNAPSHOT_DIR
    on_disk = {p.name: _sha256(p.read_bytes()) for p in sorted(folder.glob('*.md'))} if folder.is_dir() else {}
    problems = [f'{name}: stamped, not on disk' for name in sorted(set(stamped) - set(on_disk))]
    problems += [f'{name}: on disk, not stamped' for name in sorted(set(on_disk) - set(stamped))]
    problems += [f'{name}: changed since the export' for name in sorted(set(stamped) & set(on_disk)) if stamped[name] != on_disk[name]]
    lines = []
    contract_path = workdir / 'artifacts' / 'scan_contract.json'
    if contract_path.is_file():
        inventory = {
            str(f['path']): str(f['sha256'])
            for f in (json.loads(contract_path.read_text(encoding='utf-8')).get('source_inventory') or {}).get('files') or []
            if isinstance(f, dict)
        }
        hashed = {name: inventory.get(name) or inventory.get(f'{rewire.KNOWLEDGE_SNAPSHOT_DIR}/{name}') for name in stamped}
        if any(hashed.values()):
            problems += [f'{name}: the scan contract hashed a different file' for name, sha in hashed.items() if sha != stamped[name]]
        else:
            lines.append('the scan contract has not hashed the snapshot yet')
    if problems:
        return 1, [f'the knowledge snapshot is not the one the export stamped ({len(problems)}): re-run the export, never edit it', *problems]
    return 0, [f'the knowledge snapshot equals the export: {len(stamped)} file(s), every sha256 as stamped', *lines]


# --- CLI ----------------------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--project-dir', type=Path, default=None, help='the Agentforce project root (default: the working directory)')
    parser.add_argument('--bundle-dir', type=Path, default=None, help='the retrieved AiAuthoringBundle directory')
    parser.add_argument('--org', default=os.environ.get('SF_ORG_ALIAS') or None, help="the org alias (default: SF_ORG_ALIAS, else the sf CLI's default org)")
    parser.add_argument('--verify', action='store_true', help='check the snapshot on disk against its .corpus_scope stamp and exit, reading no org')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    project = rewire._resolve_agent_root((args.project_dir or Path.cwd()).resolve())  # noqa: SLF001
    workdir = rewire._resolve_workdir(project)  # noqa: SLF001
    if args.verify:
        code, lines = verify(workdir)
        for line in lines:
            print(line, file=sys.stderr if code else sys.stdout)
        return code
    try:
        facts = bundle_facts(project, args.bundle_dir)
        org = connect(args.org)
        workdir.mkdir(parents=True, exist_ok=True)
        result = export(org.get, workdir, facts, resolver=resolve_library, index_reader=read_library_index)
    except (ExportError, rewire.RewireError, OSError, ValueError) as error:
        print(f'export-knowledge: {error}', file=sys.stderr)
        retract(workdir)
        return 1
    if result.written and result.scope is not None:
        print(f'knowledge snapshot: {len(result.articles)} article(s) from {result.scope.described()} -> {workdir.name}/{rewire.KNOWLEDGE_SNAPSHOT_DIR}')
        print(f'recorded: grounding.sources; {CORPUS_SCOPE} stamped with {len(result.articles)} sha256; index {rewire.KNOWLEDGE_SNAPSHOT_INDEX}')
    else:
        print('knowledge snapshot: none written')
    if result.audience is not None:
        print(audience_line(result.audience))
    for f in result.findings:
        print(f'finding: {f["id"]} ({f["severity"]}, owner: {f["owner"]}) — {f["title"]}: {f["evidence"]}; how to confirm: {f["how_to_confirm"]}')
    return 0 if result.written else EXIT_NO_SNAPSHOT


def audience_line(audience: dict[str, Any]) -> str:
    """The one line the setup skill reads to decide whether to ask: it ends `<key>: undecided` exactly
    when articles outside the audience exist and the customer has not answered yet."""
    if audience['decided_by'] == 'agent_type':
        why = f'agent type {audience["agent_type"]}'
    elif audience['decided_by'] == 'template':
        why = f'agent template {audience["agent_template"]}'
    elif audience.get('agent_type'):
        why = f'agent type {audience["agent_type"]}, which names no audience, so external'
    else:
        why = 'no known agent type or template, so external'
    head = f'knowledge audience: {audience["audience"]} ({why})'
    count = audience['out_of_audience']
    if not count:
        return f'{head}; every Online article is published to it'
    readable = any(channel in audience['visibility_read'] for channel in audience['channels'])
    which = 'published only outside it' if readable else 'not known to be published to it (channel visibility unreadable)'
    taken = 'exported' if audience['decision'] == 'include' else 'not exported'
    return f'{head}; {count} article(s) {which} {taken} ({OUT_OF_AUDIENCE_KEY}: {audience["decision"]})'


if __name__ == '__main__':
    rewire.utf8_stdio()
    raise SystemExit(main())
