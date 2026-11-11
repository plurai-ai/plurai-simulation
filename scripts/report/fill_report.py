#!/usr/bin/env python3
"""Fill a built simulation report with a workdir's plurai_sim files.

    python fill_report.py <report.html> <plurai_sim dir> <out.html>

The page describes itself: its <html> element carries the report version, the hash of the JSON
Schema map it embeds and the payload limits, and the schema map is keyed by each file's path
under plurai_sim (`*` marks a folder wildcard). This script obeys the page: every file a pattern
matches is cut down to the fields its schema declares, strings are scrubbed of what the Plugin or
SDK put there (machine paths, the backend and mock hosts, credentials in a URL, anything that
looks like a key or token; a Plugin warning keeps its own host, since it names the host IT must
allow), and the result lands in the page's data element — the placeholder on
a fresh page, the previous payload on a page filled before. A machine path is cut to its file name
wherever a home or system folder sits in it, under another folder too (/data/home/…,
/System/Volumes/Data/Users/…); the agent's own sandbox, /mnt/user-data/ and /mnt/skills/, is what
the Agent did rather than a path on our machines and ships as written.

A file that will not parse, or whose top-level shape is not what its schema declares, is dropped
and named, never fatal; so is every YAML file when PyYAML is not installed. Compatibility is per
file, never per workdir: the Plugin version artifacts/verification.json names rides along in the
envelope for the page to show, and a workdir from another Plugin minor, or one scanned before
verification.json exists, fills like any other. The page's data-report-plugin attribute is the
version it was built against; comparing that to the vendored pin is the Plugin's --check, not this
script's. YAML dates and anything else JSON cannot carry land as strings. The report is the same
whatever the workdir's anonymization posture: plurai.config.yaml `anonymize:` governs only the
uploads that build the Environment, and this script never reads it. A Scenario's seeded world
(initial_world_state.yaml, else the copy a transcript was injected with) ships slim, each entity cut
to its kind, the human key the page names it by (case or order number, name, subject, first and last
name, title), its status and the fields some shipped Attempt's final_world_state.json changed; that
final world ships each entity as its kind, key, status and the fields that differ from the seed, and
each turn's world delta each record it wrote as its kind, key and status, so the page can tell what
the agent added, changed and removed. A chunk of the knowledge base ships as its document name,
index, text and heading path (metadata.heading_path, the only metadata kept); a chunk_idx written as
a numeral string lands as a number, and metadata written as a Python literal (a dict's repr) is read
with ast.literal_eval, never evaluated, and dropped when it is not one. rag_corpus/relationships.json
ships each link as its two ends and its type. grounding_ledger.yaml ships its tool entries alone: the
agent facts and entities go, and of each evidence record only its origin, tier, verdict, value, observed
calls and types and its units remain, every trace id in units replaced by the withheld marker so only
their count ships (evidence and part hashes, windows, sources and citations go). The undeclared list keeps each name, tier and observation. The trace brief's
anomalies_noise.md ships as its anomaly headings (key, occurrences and conversations) alone, each followed
by the names of the Agent's tools (agent_topology.json) its conditions and description name; every quote,
example, location and trace id goes. The cuts run before the schema; within a kept ledger value or
observation, the schema decides which fields ship.
Every trial's transcript of every Run the page shows ships; qualification Runs never do. Every
verdicts.json ships whole, its history left out. Beside each transcript that ships, its
tool_events.json ships slim: only the tool calls (state updates go), each as its turn, tool, status,
args and response, a response given as a JSON string parsed, and every value whole but what is
certainly binary: a base64 data: URI anywhere in a string, or a whole string of base64 (no spaces,
line-wrap newlines allowed; 256 characters, or 64 under a key named for it such as base64 or
file_data) that decodes to a known file signature, to bytes that are not UTF-8 text, or, for a data
URI, to what its header names an image, audio, video or PDF. Each becomes
{"$withheld": "binary", kind, bytes} with the mime the data URI or the signature states; a string
holding a data URI among text becomes {"$text": [text, marker, text]}. Each conversation that
carried any is recorded as a tool_binaries cut. Args and responses are hardened like world data
(each seeded, final and per-turn world: keys named for a credential withheld, email addresses, IPs
and bare hosts replaced), and every URL in them is replaced too, which world data's are not.
Over the soft limit, transcripts are cut in a fixed order and each cut is recorded, the tool calls
beside a transcript always going before it: earlier Runs' tool calls, then earlier Runs' later
trials, then earlier Runs whole, then the scored Run's later trials' tool calls but the first that
did not pass, then those later trials, then every remaining tool call file, then the scored Run's
passing conversations, then attachments, then the text of the knowledge base's chunks (a passages
cut); only past the hard ceiling does the script refuse.

Exit 0 with the page written. Exit 2 when a precondition fails (Python below the floor, usage,
template not a report page or its data-report-schema attribute off its embedded schema, out path
unwritable). Exit 3 when the payload is over the hard ceiling after every cut. Reasons go to
stderr, one line each; stdout is exactly one
JSON line: {version, kept, dropped, cut, misread, misread_count, bytes, filled_at}. `misread` names the
first 50 of: values the page reads as unknown (`coerced`: under an `x-caught` node of its schema),
values the page rejects with their row or file (`rejected`: a wrong type, a value outside an enum,
a required key absent), and fields it relies on that a file lacks (`missing`: `x-relied-on`). The
page shows the reader none of this, so the Plugin acts on it here.

Python 3.9 or newer, standard library; PyYAML for the YAML inputs.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PYTHON_FLOOR = (3, 9)
DATA_ID = 'plurai-report-data'
SCHEMA_ID = 'plurai-report-schema'
ATTRS = {
    'version': 'data-report-version',
    'plugin': 'data-report-plugin',
    'schema_hash': 'data-report-schema',
    'soft': 'data-report-payload-soft',
    'max': 'data-report-payload-max',
}

HTML_TAG = re.compile(r'<html\b[^>]*>')
ATTR = re.compile(r'([a-z-]+)="([^"]*)"')
TRANSCRIPT = 'artifacts/runs/*/*/transcript.json'
TRIAL_TRANSCRIPT = 'artifacts/runs/*/trial_*/*/transcript.json'
JUDGED = ('scored', 'goal_undecided')
CONTEXT = 'artifacts/scenarios/*/agent_context.yaml'
RUN = 'artifacts/runs/*/run.json'
SUMMARY = 'artifacts/runs/*/run_summary.json'
RUNS_INDEX = 'artifacts/runs/index.json'
SUITE = 'artifacts/suite.json'
LEDGER = 'grounding_ledger.yaml'
LEDGER_TOOL = 'tool:'
EVIDENCE_KEPT = ('origin', 'tier', 'verdict', 'value', 'observed')
UNDECLARED_KEPT = ('name', 'tier', 'observed')
ANOMALIES = 'artifacts/trace_brief/anomalies_noise.md'
TOPOLOGY = 'artifacts/agent_topology.json'
ANOMALY = re.compile(r'^### `[^`]+` — \d+ occurrence\(s\) across \d+ unit\(s\)$')
ANOMALY_FIELD = re.compile(r'^- `([^`]+)`')
ANOMALY_NAMING = ('conditions', 'description')
PROGRESS = 'artifacts/flow_progress.json'
RUN_WARNINGS = 'artifacts/run_warnings.json'
HELD_HOST = '\ue000'
WORLD = 'artifacts/runs/*/*/final_world_state.json'
TRIAL_WORLD = 'artifacts/runs/*/trial_*/*/final_world_state.json'
TOOL_EVENTS = 'artifacts/runs/*/*/tool_events.json'
TRIAL_TOOL_EVENTS = 'artifacts/runs/*/trial_*/*/tool_events.json'
ENTITY_STRING = 240
ENTITY_KEYS = 24
ENTITY_ITEMS = 12
ENTITY_DEPTH = 3
BASE64 = re.compile(r'(?:[A-Za-z0-9+/_-]+\r?\n)*[A-Za-z0-9+/_-]+={0,2}(?:\r?\n)?')
BASE64_FLOOR = 256
KEYED_BASE64_FLOOR = 64
BINARY_KEYS = ('base64', 'b64', 'data_base64', 'base64_data', 'file_data', 'filedata', 'file_base64', 'image_data', 'imagedata', 'image_base64', 'audio_data', 'audiodata', 'content_base64')
HEX = re.compile(r'[0-9A-Fa-f]+\s*')
DATA_URI = re.compile(r'(?<![\w.+-])data:([\w.+-]+/[\w.+-]+)?(?:;[\w.+-]+=[^;,\s"\'<>]*)*;base64,((?:[A-Za-z0-9+/_-]|\r?\n)+={0,2})', re.I)
CONTROL = re.compile(r'[\x00-\x08\x0b\x0e-\x1f\x7f]')
SIGNATURES = (
    (b'\xff\xd8\xff', 'image/jpeg'),
    (b'\x89PNG\r\n\x1a\n', 'image/png'),
    (b'GIF87a', 'image/gif'),
    (b'GIF89a', 'image/gif'),
    (b'%PDF-', 'application/pdf'),
    (b'PK\x03\x04', 'application/zip'),
    (b'ID3', 'audio/mpeg'),
    (b'fLaC', 'audio/flac'),
)
RIFF = {b'WEBP': 'image/webp', b'WAVE': 'audio/wav'}
URL = re.compile(r'(?i)\b[a-z][a-z0-9+.-]*://[^\s"\'<>()\[\]]+')
SIDECAR = 'report.data.js'
UPDATE_HOOK = '__reportUpdate'
KEY_WORD = re.compile(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+')
SENSITIVE_WORDS = frozenset({'key', 'keys', 'apikey', 'token', 'tokens', 'secret', 'secrets', 'password', 'passwd', 'otp', 'auth', 'authorization', 'credential', 'credentials'})
EMAIL = re.compile(r'(?i)\b[\w.%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}\b')
IP = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
BARE_HOST = re.compile(r'(?i)(?<![\w@.-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:com|net|org|io|ai|co|dev|app|cloud|uk|us|eu|de|fr|il|info|biz|me)(?![\w.-])')
SCENARIO_WORLD = 'artifacts/scenarios/*/initial_world_state.yaml'
RECORD_KEYS = ('_entity_type', 'casenumber', 'ordernumber', 'name', 'subject', 'firstname', 'lastname', 'title', 'status')
CHUNKS = 'rag_corpus/chunks.json'
NUMERAL = re.compile(r'^\d+$')
RELIED_ON = 'x-relied-on'
CAUGHT = 'x-caught'
MISREAD_CAP = 50
VERIFICATION = 'artifacts/verification.json'

MACHINE_PATH = re.compile(r'(?:(?P<sandbox>/mnt/(?:user-data|skills)/)|(?:(?<![\w.-])(?:/[\w.-]+)*)?(?:/Users/[\w.-]+(?: [\w.-]+){1,2}/|/(?:Users|home|private|var|tmp|opt|root|mnt|workspace|srv)/)|(?<![\w/~])~/|[A-Za-z]:\\)[^\s\'"()\[\]`*<>]*')
MOCK_URL = re.compile(r'https?://[^\s/"\']+/mock/[0-9a-f]{32}/?')
BACKEND_URL = re.compile(r'https?://[^\s/"\']*\bplurai\.[a-z]+(?::\d+)?')
BARE_BACKEND = re.compile(r'(?<![\w.-])(?:[a-z0-9-]+\.)+plurai\.uk(?![\w-]|\.[\w-])', re.I)
BEARER = re.compile(r'\bBearer\s+[A-Za-z0-9._~+/=-]{8,}')
SECRET_PREFIX = re.compile(r'\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b')
AWS_KEY = re.compile(r'\bAKIA[0-9A-Z]{16}\b')
TOKEN = re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|xox[abposr]-[A-Za-z0-9-]{10,}|eyJ[\w-]{8,}\.eyJ[\w-]{8,}\.[\w-]{8,})')
USERINFO = re.compile(r'\b([a-z][a-z0-9+.-]*://)[^\s/:@\'"]+:[^\s/@\'"]+@')
KEY_ASSIGNMENT = re.compile(r'(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password|authorization)\b(\s*[:=]\s*)["\']?((?=[^\s"\',;]*\d)[A-Za-z0-9_./+=-]{16,})')
REDACTED = '[redacted]'
WITHHELD = '[withheld]'
QUALIFICATION = re.compile(r'(?:^|[-_])qual')
MEMBER_SUFFIXES = ('-repeat', '-verify')
DIGITS = re.compile(r'(\d+)')


class Refusal(Exception):
    def __init__(self, reason: str, code: int = 2) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


class Unreadable(Exception):
    pass


def check_python(version: tuple[int, ...]) -> None:
    if tuple(version[:2]) < PYTHON_FLOOR:
        raise Refusal(f'fill_report.py needs Python {".".join(map(str, PYTHON_FLOOR))} or newer; this is {".".join(map(str, version[:3]))}')


def matches(pattern: str, path: str) -> bool:
    return re.fullmatch(re.escape(pattern).replace(r'\*', '[^/]+'), path) is not None


def element(report: str, element_id: str) -> re.Match[str] | None:
    return re.search(rf'(<script\b[^>]*\bid="{re.escape(element_id)}"[^>]*>)(.*?)(</script>)', report, re.S)


def page_of(report: str) -> dict[str, Any]:
    tag = HTML_TAG.search(report)
    attrs = dict(ATTR.findall(tag.group(0))) if tag else {}
    schema = element(report, SCHEMA_ID)
    data = element(report, DATA_ID)
    if schema is None or data is None or any(attrs.get(name) is None for name in ATTRS.values()):
        raise Refusal('template is not a simulation report page: missing its data element, schema element or data-report attributes')
    schema_json = schema.group(2)
    if hashlib.sha256(schema_json.encode('utf-8')).hexdigest() != attrs[ATTRS['schema_hash']]:
        raise Refusal('template schema hash does not match the schema it embeds')
    return {
        'version': attrs[ATTRS['version']],
        'plugin': attrs[ATTRS['plugin']],
        'schema_hash': attrs[ATTRS['schema_hash']],
        'soft': int(attrs[ATTRS['soft']]),
        'max': int(attrs[ATTRS['max']]),
        'schemas': json.loads(schema_json),
        'data': data,
    }


def project(value: Any, schema: dict[str, Any]) -> Any:
    branches = schema.get('anyOf') or schema.get('oneOf')
    if isinstance(branches, list):
        fitting = [branch for branch in branches if isinstance(branch, dict) and fits(value, branch)]
        if not fitting:
            return value
        return max((project(value, branch) for branch in fitting), key=retained)
    if isinstance(value, dict) and 'properties' in schema:
        return {key: project(item, schema['properties'][key]) for key, item in value.items() if key in schema['properties']}
    if isinstance(value, dict) and isinstance(schema.get('additionalProperties'), dict):
        return {key: project(item, schema['additionalProperties']) for key, item in value.items()}
    if isinstance(value, list) and isinstance(schema.get('items'), dict):
        return [project(item, schema['items']) for item in value]
    return value


def joined(at: str, key: str) -> str:
    return key if at == '' else f'{at}.{key}'


def violations(value: Any, schema: dict[str, Any], at: str = '') -> list[dict[str, str]]:
    found = breaches(value, schema, at)
    if found and schema.get(CAUGHT):
        return [{'kind': 'coerced', 'field': at, 'value': value if isinstance(value, str) else json.dumps(json_safe(value))}]
    return found


def breaches(value: Any, schema: dict[str, Any], at: str) -> list[dict[str, str]]:
    rejected = [{'kind': 'rejected', 'field': at, 'value': value if isinstance(value, str) else json.dumps(json_safe(value))[:200]}]
    branches = schema.get('anyOf') or schema.get('oneOf')
    if isinstance(branches, list):
        fitting = [branch for branch in branches if isinstance(branch, dict) and fits(value, branch)]
        return min((violations(value, branch, at) for branch in fitting), key=len) if fitting else rejected
    if not fits(value, schema):
        return rejected
    if isinstance(schema.get('enum'), list) and value not in schema['enum']:
        return rejected
    if isinstance(value, dict) and isinstance(schema.get('properties'), dict):
        absent = [{'kind': 'rejected', 'field': joined(at, key)} for key in schema.get('required', []) if key not in value and not schema['properties'].get(key, {}).get(CAUGHT)]
        return [*absent, *(found for key, item in value.items() if key in schema['properties'] for found in violations(item, schema['properties'][key], joined(at, key)))]
    if isinstance(value, dict) and isinstance(schema.get('additionalProperties'), dict):
        return [found for key, item in value.items() for found in violations(item, schema['additionalProperties'], joined(at, key))]
    if isinstance(value, list) and isinstance(schema.get('items'), dict):
        return [found for index, item in enumerate(value) for found in violations(item, schema['items'], f'{at}[{index}]')]
    return []


def misread_in(value: Any, schema: dict[str, Any]) -> list[dict[str, str]]:
    relied = schema.get(RELIED_ON) if isinstance(schema.get(RELIED_ON), list) else []
    missing = [{'kind': 'missing', 'field': field} for field in relied if isinstance(value, dict) and value.get(field) is None]
    return [*missing, *violations(value, schema)]


def retained(value: Any) -> int:
    if isinstance(value, dict):
        return len(value) + sum(retained(item) for item in value.values())
    if isinstance(value, list):
        return sum(retained(item) for item in value)
    return 0


TYPES = {'object': dict, 'array': list, 'string': str, 'boolean': bool, 'number': (int, float), 'integer': int, 'null': type(None)}


def fits(value: Any, schema: dict[str, Any]) -> bool:
    declared = schema.get('type')
    if declared is None:
        return True
    names = declared if isinstance(declared, list) else [declared]
    return any(isinstance(value, TYPES[name]) and not (name == 'number' and isinstance(value, bool)) for name in names if name in TYPES)


def shareable(path: str) -> str:
    parts = re.split(r'[\\/]', path)
    if 'plurai_sim' in parts:
        return '/'.join(parts[parts.index('plurai_sim'):])
    return parts[-1] or parts[-2]


def bare_host(host: str) -> re.Pattern[str]:
    return re.compile(rf'(?<![\w.-]){re.escape(host)}(?![\w-]|\.[\w-])', re.I)


def scrubber(hosts: list[str], backends: list[str]):
    exact = sorted({host.rstrip('/') for host in hosts if host}, key=len, reverse=True)
    hostnames = {urlsplit(backend).hostname for backend in backends}
    bare = [bare_host(name) for name in sorted({name for name in hostnames if name}, key=len, reverse=True)]

    def scrub(text: str) -> str:
        text = USERINFO.sub(rf'\1{REDACTED}@', text)
        text = MOCK_URL.sub('[mock]', text)
        for host in exact:
            text = text.replace(host, '[backend]')
        text = BACKEND_URL.sub('[backend]', text)
        for host in bare:
            text = host.sub('[backend]', text)
        text = BARE_BACKEND.sub('[backend]', text)
        text = MACHINE_PATH.sub(lambda m: m.group(0) if m['sandbox'] else shareable(m.group(0)), text)
        text = BEARER.sub(f'Bearer {REDACTED}', text)
        text = SECRET_PREFIX.sub(REDACTED, text)
        text = AWS_KEY.sub(REDACTED, text)
        text = TOKEN.sub(REDACTED, text)
        return KEY_ASSIGNMENT.sub(lambda m: f'{m.group(1)}{m.group(2)}{REDACTED}', text)

    return scrub


def scrub_warnings(value: Any, scrub) -> Any:
    if not isinstance(value, dict) or not isinstance(value.get('warnings'), list):
        return scrub_all(value, scrub)
    return {**scrub_all(value, scrub), 'warnings': [scrub_warning(warning, scrub) for warning in value['warnings']]}


def scrub_warning(warning: Any, scrub) -> Any:
    host = warning.get('host') if isinstance(warning, dict) else None
    if not isinstance(host, str) or host == '':
        return scrub_all(warning, scrub)
    held = {key: item.replace(host, HELD_HOST) if isinstance(item, str) else item for key, item in warning.items()}
    return {key: item.replace(HELD_HOST, host) if isinstance(item, str) else item for key, item in scrub_all(held, scrub).items()}


def scrub_all(value: Any, scrub) -> Any:
    if isinstance(value, dict):
        return {key: scrub_all(item, scrub) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_all(item, scrub) for item in value]
    if isinstance(value, str):
        return scrub(value)
    return value


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key if isinstance(key, str) else json_safe(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


PLAIN_INT = re.compile(r'^[-+]?(?:0|[1-9][0-9_]*)$')
INT_TAG = 'tag:yaml.org,2002:int'


def written_numbers(yaml: Any) -> Any:
    class Loader(yaml.SafeLoader):
        pass

    Loader.yaml_implicit_resolvers = {
        first: [(tag, pattern) for tag, pattern in resolvers if tag != INT_TAG]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    Loader.add_implicit_resolver(INT_TAG, PLAIN_INT, list('-+0123456789'))
    return Loader


def read(source: Path) -> Any:
    text = source.read_text(encoding='utf-8')
    if source.suffix in ('.md', '.jsonl', '.txt'):
        return text
    if source.suffix in ('.yaml', '.yml'):
        try:
            import yaml  # noqa: PLC0415 - only YAML inputs need it; the Plugin's interpreter carries it
        except ImportError as error:
            raise Unreadable('PyYAML is not installed; YAML inputs are skipped') from error
        return json_safe(yaml.load(text, Loader=written_numbers(yaml)))
    return json.loads(text)


def count_of(value: Any) -> Any:
    return len(value) if isinstance(value, list) else value


def count_egress(run: Any) -> None:
    integrity = run.get('run_integrity') if isinstance(run, dict) else None
    egress = integrity.get('egress') if isinstance(integrity, dict) else None
    if not isinstance(egress, dict):
        return
    for key in ('declared_hosts', 'undeclared_hosts'):
        if key in egress:
            egress[key] = count_of(egress[key])
    runtime = egress.get('runtime')
    if isinstance(runtime, dict):
        for key in ('observed_hosts', 'blocked'):
            if key in runtime:
                runtime[key] = count_of(runtime[key])


def harden_text(text: str) -> str:
    return BARE_HOST.sub('[host]', IP.sub('[ip]', EMAIL.sub('[email]', text)))


def unwrapped(response: Any) -> Any:
    for _ in range(2):
        if not isinstance(response, str) or response.lstrip()[:1] not in ('{', '[', '"'):
            return response
        try:
            response = json.loads(response)
        except ValueError:
            return response
    return response


def nested(value: Any) -> bool:
    return isinstance(value, (dict, list))


def capped(value: Any, depth: int = ENTITY_DEPTH) -> Any:
    if isinstance(value, dict):
        return {key: capped(item, depth - 1) for key, item in list(value.items())[:ENTITY_KEYS] if depth > 1 or not nested(item)}
    if isinstance(value, list):
        return [capped(item, depth - 1) for item in value[:ENTITY_ITEMS] if depth > 1 or not nested(item)]
    if isinstance(value, str):
        return value[:ENTITY_STRING]
    return value


def decoded(body: str) -> bytes | None:
    body = body.replace('\r', '').replace('\n', '')
    if ('-' in body or '_' in body) and ('+' in body or '/' in body):
        return None
    body = body.replace('-', '+').replace('_', '/').rstrip('=')
    if len(body) % 4 == 1:
        return None
    try:
        return base64.b64decode(body + '=' * (-len(body) % 4), validate=True)
    except ValueError:
        return None


def sniffed(raw: bytes) -> str | None:
    if raw[:4] == b'RIFF' and raw[8:12] in RIFF:
        return RIFF[raw[8:12]]
    return next((mime for signature, mime in SIGNATURES if raw.startswith(signature)), None)


def is_text(raw: bytes) -> bool:
    try:
        return CONTROL.search(raw.decode('utf-8')) is None
    except UnicodeDecodeError:
        return False


def kind_of(mime: str | None) -> str:
    if mime is None:
        return 'file'
    if mime == 'application/pdf':
        return 'pdf'
    family = mime.split('/', 1)[0]
    return family if family in ('image', 'audio', 'video') else 'file'


def binary_marker(raw: bytes, stated: str | None = None) -> dict[str, Any] | None:
    mime = stated or sniffed(raw)
    if sniffed(raw) is None and kind_of(mime) == 'file' and is_text(raw):
        return None
    return {'$withheld': 'binary', 'kind': kind_of(mime), **({} if mime is None else {'mime': mime}), 'bytes': len(raw)}


def bare_binary(text: str, key: str) -> dict[str, Any] | None:
    floor = KEYED_BASE64_FLOOR if key.lower() in BINARY_KEYS else BASE64_FLOOR
    if len(text) < floor or not BASE64.fullmatch(text) or HEX.fullmatch(text):
        return None
    if not all(any(test(char) for char in text) for test in (str.isdigit, str.isupper, str.islower)):
        return None
    raw = decoded(text)
    return None if raw is None else binary_marker(raw)


def spliced(text: str) -> Any:
    parts: list[Any] = []
    at = 0
    for uri in DATA_URI.finditer(text):
        raw = decoded(uri.group(2))
        marker = None if raw is None else binary_marker(raw, uri.group(1).lower() if uri.group(1) else None)
        if marker is None:
            continue
        parts += [text[at:uri.start()], marker]
        at = uri.end()
    if not parts:
        return text
    parts = [part for part in [*parts, text[at:]] if part != '']
    return parts[0] if len(parts) == 1 else {'$text': parts}


def withhold_binary(value: Any, key: str = '') -> Any:
    if isinstance(value, dict):
        return {name: withhold_binary(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [withhold_binary(item, key) for item in value]
    if not isinstance(value, str):
        return value
    marker = bare_binary(value, key)
    if marker is not None:
        return marker
    parsed = unwrapped(value)
    if isinstance(parsed, str):
        return spliced(value)
    kept = withhold_binary(parsed, key)
    return value if kept == parsed else kept


def read_calls(events: Any) -> bool:
    if not isinstance(events, dict) or not isinstance(events.get('events'), list):
        return False
    whole = [
        {**event, 'args': event.get('args'), 'response': unwrapped(event.get('response'))}
        for event in events['events'] if isinstance(event, dict) and event.get('type', 'tool_call') == 'tool_call'
    ]
    events['events'] = [{**event, 'args': withhold_binary(event['args']), 'response': withhold_binary(event['response'])} for event in whole]
    return events['events'] != whole


def harden_calls(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: (WITHHELD if sensitive(key) and item is not None else harden_calls(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [harden_calls(item) for item in value]
    if isinstance(value, str):
        return harden_text(URL.sub('[url]', value))
    return value


def sensitive(key: str) -> bool:
    return any(word.lower() in SENSITIVE_WORDS for word in KEY_WORD.findall(key))


def harden(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: (WITHHELD if sensitive(key) and item is not None else harden(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [harden(item) for item in value]
    if isinstance(value, str):
        return harden_text(value)
    return value


def entities_of(world: Any) -> dict[str, Any] | None:
    return world['entities'] if isinstance(world, dict) and isinstance(world.get('entities'), dict) else None


def injected_world(transcript: Any) -> dict[str, Any] | None:
    inputs = transcript.get('injected_inputs') if isinstance(transcript, dict) else None
    return entities_of(inputs.get('initial_world_state')) if isinstance(inputs, dict) else None


def changed_fields(seed: dict[str, Any], entity_id: str, entity: Any) -> set[str]:
    prior = seed.get(entity_id)
    if not isinstance(prior, dict) or not isinstance(entity, dict):
        return set()
    return {key for key, value in entity.items() if key not in prior or prior[key] != value}


Worlds = tuple[dict[str, dict[str, Any]], dict[str, dict[str, set[str]]]]


def world_records(raw: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    found: list[tuple[str, str, Any]] = []
    for path, value in sorted(raw.items()):
        if matches(SCENARIO_WORLD, path):
            found.append((path, 'seeded', entities_of(value)))
        if matches(WORLD, path) or matches(TRIAL_WORLD, path):
            found.append((path, 'final', entities_of(value)))
        if not (matches(TRANSCRIPT, path) or matches(TRIAL_TRANSCRIPT, path)) or not isinstance(value, dict):
            continue
        found.append((path, 'injected', injected_world(value)))
        for turn in value.get('turns') or []:
            world = turn.get('turn_world') if isinstance(turn, dict) else None
            delta = world.get('delta') if isinstance(world, dict) else None
            if not isinstance(delta, dict):
                continue
            removed = delta.get('removed') if isinstance(delta.get('removed'), list) else []
            found.append((path, 'upserted', delta.get('upserted')))
            found.append((path, 'removed', dict.fromkeys(item for item in removed if isinstance(item, str))))
    return [(path, source, records) for path, source, records in found if isinstance(records, dict)]


def worlds_of(raw: dict[str, Any]) -> Worlds:
    records = world_records(raw)
    injected: dict[str, dict[str, Any]] = {}
    for path, source, entities in records:
        if source == 'injected':
            injected.setdefault(path.split('/')[-2], entities)
    seeds = {**injected, **{path.split('/')[-2]: entities for path, source, entities in records if source == 'seeded'}}
    changes: dict[str, dict[str, set[str]]] = {}
    for path, source, final in records:
        scenario = path.split('/')[-2]
        if source != 'final' or scenario not in seeds:
            continue
        for entity_id, entity in final.items():
            changes.setdefault(scenario, {}).setdefault(entity_id, set()).update(changed_fields(seeds[scenario], entity_id, entity))
    return seeds, changes


def slimmed(entity: Any, fields: set[str]) -> Any:
    if not isinstance(entity, dict):
        return entity
    return {key: value if isinstance(value, str) else capped(value) for key, value in entity.items() if key.lower() in RECORD_KEYS or key in fields}


def seeded_of(world: Any, changed: dict[str, set[str]]) -> Any:
    entities = entities_of(world)
    if entities is None:
        return world
    return {**world, 'entities': {entity_id: slimmed(entity, changed.get(entity_id, set())) for entity_id, entity in entities.items()}}


def final_of(world: Any, seed: dict[str, Any]) -> Any:
    entities = entities_of(world)
    if entities is None:
        return world
    return {**world, 'entities': {entity_id: slimmed(entity, changed_fields(seed, entity_id, entity)) for entity_id, entity in entities.items()}}


def delta_of(turn_world: Any) -> Any:
    delta = turn_world.get('delta') if isinstance(turn_world, dict) else None
    if not isinstance(delta, dict) or not isinstance(delta.get('upserted'), dict):
        return turn_world
    return {**turn_world, 'delta': {**delta, 'upserted': {entity_id: slimmed(entity, set()) for entity_id, entity in delta['upserted'].items()}}}


def world_of(pattern: str, path: str, projected: Any, worlds: Worlds) -> Any:
    seeds, changes = worlds
    scenario = path.split('/')[-2] if '/' in path else path
    if pattern == SCENARIO_WORLD:
        return harden(seeded_of(projected, changes.get(scenario, {})))
    if pattern in (WORLD, TRIAL_WORLD):
        return harden(final_of(projected, seeds.get(scenario, {})))
    if pattern in (TOOL_EVENTS, TRIAL_TOOL_EVENTS):
        return harden_calls(projected)
    if pattern not in (TRANSCRIPT, TRIAL_TRANSCRIPT) or not isinstance(projected, dict):
        return projected
    inputs = projected.get('injected_inputs')
    if isinstance(inputs, dict) and inputs.get('initial_world_state') is not None:
        inputs['initial_world_state'] = harden(seeded_of(inputs['initial_world_state'], changes.get(scenario, {})))
    for turn in projected.get('turns') or []:
        if isinstance(turn, dict) and turn.get('turn_world') is not None:
            turn['turn_world'] = harden(delta_of(turn['turn_world']))
    return projected


def metadata_of(metadata: Any) -> Any:
    if isinstance(metadata, str):
        try:
            metadata = ast.literal_eval(metadata.strip())
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            return None
    return json_safe(metadata) if isinstance(metadata, dict) else None


def read_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    index = chunk.get('chunk_idx')
    read = {**chunk, 'chunk_idx': int(index) if isinstance(index, str) and NUMERAL.match(index.strip()) else index}
    if 'metadata' in chunk:
        read['metadata'] = metadata_of(chunk['metadata'])
    return read


def read_chunks(chunks: Any) -> Any:
    if not isinstance(chunks, list):
        return chunks
    return [read_chunk(chunk) if isinstance(chunk, dict) else chunk for chunk in chunks]


def read_evidence(evidence: Any) -> Any:
    if not isinstance(evidence, dict):
        return evidence
    kept = {key: evidence[key] for key in EVIDENCE_KEPT if key in evidence}
    units = evidence.get('units')
    if isinstance(units, list):
        kept['units'] = [WITHHELD for _ in units]
    return kept


def read_ledger(ledger: Any) -> Any:
    if not isinstance(ledger, dict):
        return ledger
    entries = ledger.get('entries')
    undeclared = ledger.get('undeclared')
    kept: dict[str, Any] = {'schema_version': ledger.get('schema_version')}
    if isinstance(entries, list):
        kept['entries'] = [
            {**{key: entry[key] for key in ('key', 'display', 'tier', 'value') if key in entry},
             'evidence': [read_evidence(evidence) for evidence in entry.get('evidence') or []]}
            for entry in entries
            if isinstance(entry, dict) and str(entry.get('key', '')).startswith(LEDGER_TOOL)
        ]
    if isinstance(undeclared, list):
        kept['undeclared'] = [
            {key: item[key] for key in UNDECLARED_KEPT if key in item}
            for item in undeclared
            if isinstance(item, dict) and str(item.get('name', '')).startswith(LEDGER_TOOL)
        ]
    return kept


def tool_names(topology: Any) -> list[str]:
    tools = topology.get('tools') if isinstance(topology, dict) else None
    return list(dict.fromkeys(tool['name'] for tool in tools or [] if isinstance(tool, dict) and isinstance(tool.get('name'), str)))


def named_in(text: str, tools: list[str]) -> list[str]:
    return [tool for tool in tools if re.search(rf'(?<![\w-])(?<!\w\.){re.escape(tool)}(?![\w-])(?!\.\w)', text)]


def read_anomalies(text: Any, tools: list[str]) -> Any:
    if not isinstance(text, str):
        return text
    blocks: list[tuple[str, list[str]]] = []
    field: str | None = None
    for line in text.splitlines():
        if ANOMALY.match(line):
            blocks.append((line, []))
            field = None
            continue
        at = ANOMALY_FIELD.match(line)
        if at:
            field = at.group(1)
            continue
        if blocks and field in ANOMALY_NAMING and line.startswith('  - '):
            named = blocks[-1][1]
            named.extend(tool for tool in named_in(line, tools) if tool not in named)
    return ''.join(
        f'{head}\n' + (f'- `tools`: {", ".join(f"`{name}`" for name in named)}\n' if named else '')
        for head, named in blocks
    )


def run_live(progress: Any) -> bool:
    stages = progress.get('stages') if isinstance(progress, dict) else None
    return any(isinstance(stage, dict) and stage.get('id') == 'run' and stage.get('state') in ('running', 'halted') for stage in stages or [])


def playing_run(workdir: Path, stages: dict[str, Any] | None) -> tuple[str | None, list[dict[str, str]]]:
    runs = workdir / 'artifacts' / 'runs'
    if not runs.is_dir():
        return None, []
    candidates: list[tuple[float, float, str]] = []
    for folder in runs.iterdir():
        if not folder.is_dir() or (folder / 'run.json').is_file():
            continue
        if stages is None and QUALIFICATION.search(folder.name):
            continue
        if stages is not None and stages.get(folder.name, {}).get('stage') == 'qualification':
            continue
        transcripts = [path.stat().st_mtime for path in folder.glob('*/transcript.json')]
        if transcripts:
            candidates.append((max(transcripts), folder.stat().st_mtime, folder.name))
    if not candidates:
        return None, []
    candidates.sort(reverse=True)
    newest = candidates[0][2]
    dropped = [{'path': f'artifacts/runs/{name}/', 'reason': f'a second Run without run.json while Run plays; only {newest} ships'} for _, _, name in candidates[1:]]
    return newest, dropped


def hosts_of(raw: dict[str, Any]) -> list[str]:
    hosts: list[str] = []
    for path, value in raw.items():
        metadata = value.get('metadata') if path.endswith('/transcript.json') and isinstance(value, dict) else None
        if isinstance(metadata, dict):
            hosts += [metadata.get(key) for key in ('mock_service_url', 'server_url') if isinstance(metadata.get(key), str)]
    return hosts


def backends_of(raw: dict[str, Any]) -> list[str]:
    metadata = [value.get('metadata') for path, value in raw.items() if path.endswith('/transcript.json') and isinstance(value, dict)]
    return [entry['server_url'] for entry in metadata if isinstance(entry, dict) and isinstance(entry.get('server_url'), str)]


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def payload_bytes(files: dict[str, Any]) -> int:
    return len(json.dumps(files, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def natural(folder: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.casefold() for part in DIGITS.split(folder)]


def stages_of(index: Any) -> dict[str, Any] | None:
    if not isinstance(index, dict) or not isinstance(index.get('runs'), list):
        return None
    return {row['name']: row for row in index['runs'] if isinstance(row, dict) and isinstance(row.get('name'), str)}


def qualification(path: str, stages: dict[str, Any] | None, playing: str | None = None, headless: set[str] = frozenset()) -> bool:
    parts = path.split('/')
    if parts[:2] != ['artifacts', 'runs'] or len(parts) < 4:
        return False
    if playing is not None and parts[2] == playing:
        return False
    if playing is not None and parts[2] in headless:
        return True
    if stages is not None:
        return stages.get(parts[2], {}).get('stage') in (None, 'qualification')
    base = next((parts[2][:-len(suffix)] for suffix in MEMBER_SUFFIXES if parts[2].endswith(suffix)), parts[2])
    return QUALIFICATION.search(base) is not None


def instant(stamp: Any) -> float | None:
    if not isinstance(stamp, str):
        return None
    try:
        moment = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp()


def of_suite(run: Any, suite: Any) -> bool:
    if not isinstance(suite, dict) or not isinstance(run, dict):
        return True
    ids = {row.get('id') for row in suite.get('scenarios', []) if isinstance(row, dict)}
    rows = [row for row in run.get('scenarios', []) if isinstance(row, dict)]
    evals = suite.get('eval_set_id')
    return bool(rows) and all(row.get('id') in ids for row in rows) and (evals is None or run.get('eval_set_id') == evals)


def latest_run(files: dict[str, Any]) -> str | None:
    folders = sorted({path.split('/')[2] for path in files if matches(RUN, path)}, key=natural)
    index = files.get(RUNS_INDEX)
    scored = index.get('scored_run') if isinstance(index, dict) else None
    if scored in folders:
        return scored
    stages = stages_of(index)
    if stages is None:
        heads = [folder for folder in folders if not folder.endswith(MEMBER_SUFFIXES)]
    else:
        heads = [folder for folder in folders if stages.get(folder, {}).get('stage') == 'final']
    candidates = [folder for folder in heads if of_suite(files.get(f'artifacts/runs/{folder}/run.json'), files.get(SUITE))] or heads
    if not candidates:
        return None

    def order(folder: str) -> tuple[bool, float, int]:
        timed = files.get(SUMMARY.replace('*', folder)) if stages is None else stages.get(folder)
        at = instant(timed.get('written_at')) if isinstance(timed, dict) else None
        return (at is not None, at or 0.0, candidates.index(folder))

    return max(candidates, key=order)


def scores_of(files: dict[str, Any], folder: str) -> dict[str, Any]:
    run = files.get(f'artifacts/runs/{folder}/run.json')
    scores = run.get('scores') if isinstance(run, dict) else None
    scenarios = scores.get('scenarios') if isinstance(scores, dict) else None
    return scenarios if isinstance(scenarios, dict) else {}


def conversations_of(scenario: Any) -> list[dict[str, Any]]:
    rows = scenario.get('conversations') if isinstance(scenario, dict) else None
    return sorted((row for row in rows or [] if isinstance(row, dict) and isinstance(row.get('trial'), int)), key=lambda row: row['trial'])


def attempt_of(verdicts: Any) -> str:
    evals = verdicts.get('evals') if isinstance(verdicts, dict) else None
    states = [entry.get('state') for entry in (evals or {}).values() if isinstance(entry, dict)]
    status = verdicts.get('status') if isinstance(verdicts, dict) else None
    value = status.get('value') if isinstance(status, dict) else None
    if 'fail' in states:
        return 'failed'
    if value == 'agent_error':
        return 'crashed'
    return 'passed' if value in JUDGED and 'pass' in states else 'undecided'


def trial_dir_of(row: dict[str, Any]) -> str | None:
    if row['trial'] == 1:
        return ''
    trial_dir = row.get('trial_dir')
    return trial_dir if isinstance(trial_dir, str) and trial_dir else None


def attempts_of(files: dict[str, Any], folder: str, scenario: str, scored: Any) -> list[tuple[str | None, str]]:
    attempts = []
    for row in conversations_of(scored):
        trial_dir = trial_dir_of(row)
        verdicts = None if trial_dir is None else files.get('/'.join(part for part in ('artifacts/runs', folder, trial_dir, scenario, 'verdicts.json') if part))
        attempts.append((trial_dir, attempt_of(verdicts)))
    return attempts


def later_trial_transcripts(files: dict[str, Any]) -> set[str]:
    latest = latest_run(files)
    if latest is None:
        return set()
    shown: set[str] = set()
    for scenario, scored in scores_of(files, latest).items():
        attempts = attempts_of(files, latest, scenario, scored)
        if not attempts or attempts[0] != ('', 'passed'):
            continue
        trial_dir = next((trial_dir for trial_dir, result in attempts[1:] if result != 'passed'), None)
        if trial_dir:
            shown.add(f'artifacts/runs/{latest}/{trial_dir}/{scenario}/transcript.json')
    return shown


def passing_scenarios(files: dict[str, Any], folder: str) -> list[str]:
    passed = []
    for scenario, scored in scores_of(files, folder).items():
        results = [result for _, result in attempts_of(files, folder, scenario, scored)]
        if 'passed' in results and 'failed' not in results and 'crashed' not in results:
            passed.append(scenario)
    return passed


def conversation_of(path: str) -> str:
    return path[len('artifacts/runs/'):path.rindex('/')]


def calls_beside(transcript: str) -> str:
    return f'{transcript[:transcript.rindex("/")]}/tool_events.json'


def file_parts(transcript: Any) -> list[dict[str, Any]]:
    turns = transcript.get('turns') if isinstance(transcript, dict) else None
    return [
        part['file']
        for turn in turns or [] if isinstance(turn, dict)
        for part in turn.get('user_messages') or [] if isinstance(part, dict) and isinstance(part.get('file'), dict)
    ]


def strip_files(transcript: Any) -> bool:
    carried = [part for part in file_parts(transcript) if 'file_data' in part]
    for part in carried:
        del part['file_data']
    return carried != []


def read_context(path: str, transcript: Any, scored: str | None) -> None:
    if isinstance(transcript, dict) and not (matches(TRANSCRIPT, path) and path.split('/')[2] == scored):
        transcript.pop('injected_inputs', None)
        strip_files(transcript)


def degrade(files: dict[str, Any], soft: int, playing: str | None = None) -> list[dict[str, Any]]:
    cut: list[dict[str, Any]] = []
    if payload_bytes(files) <= soft:
        return cut
    latest = latest_run(files)
    shown = later_trial_transcripts(files) | {path for path in files if matches(TRIAL_TRANSCRIPT, path) and path.split('/')[2] == playing}
    shown_calls = {calls_beside(path) for path in shown}

    def drop(kind: str, paths: list[str], items: list[str] | None = None) -> bool:
        for path in paths:
            del files[path]
        if paths:
            cut.append({'kind': kind, 'items': sorted(items if items is not None else [conversation_of(path) for path in paths])})
        return payload_bytes(files) <= soft

    def calls(trial: bool | None = None) -> list[str]:
        return [path for path in files if (trial is not False and matches(TRIAL_TOOL_EVENTS, path)) or (trial is not True and matches(TOOL_EVENTS, path))]

    older_calls = [path for path in calls() if path.split('/')[2] not in (latest, playing)]
    if drop('older_tool_calls', older_calls):
        return cut
    older_trials = [path for path in files if matches(TRIAL_TRANSCRIPT, path) and path.split('/')[2] not in (latest, playing)]
    if drop('older_trials', older_trials):
        return cut
    older = [path for path in files if matches(TRANSCRIPT, path) and path.split('/')[2] not in (latest, playing)]
    if drop('older_transcripts', older, sorted({path.split('/')[2] for path in older})):
        return cut
    later_calls = [path for path in calls(trial=True) if path not in shown_calls]
    if drop('later_trial_tool_calls', later_calls):
        return cut
    later = [path for path in files if matches(TRIAL_TRANSCRIPT, path) and path not in shown]
    if drop('later_trials', later):
        return cut
    if drop('tool_calls', calls()):
        return cut
    if latest is not None:
        passing = [scenario for scenario in passing_scenarios(files, latest) if f'artifacts/runs/{latest}/{scenario}/transcript.json' in files]
        for scenario in passing:
            files[f'artifacts/runs/{latest}/{scenario}/transcript.json']['turns'] = []
        if passing:
            cut.append({'kind': 'passing_turns', 'items': passing})
            if payload_bytes(files) <= soft:
                return cut
    attachments = [path for path in files if matches(CONTEXT, path)]
    for path in attachments:
        del files[path]
    attachments += [path for path in files if (matches(TRANSCRIPT, path) or matches(TRIAL_TRANSCRIPT, path)) and strip_files(files[path])]
    if attachments:
        cut.append({'kind': 'attachments', 'items': sorted(attachments)})
        if payload_bytes(files) <= soft:
            return cut
    passages = [chunk for chunk in files.get(CHUNKS) or [] if isinstance(chunk, dict) and 'text' in chunk]
    for chunk in passages:
        del chunk['text']
    if passages:
        cut.append({'kind': 'passages', 'items': [CHUNKS]})
    return cut


def type_name(value: Any) -> str:
    if isinstance(value, bool):
        return 'boolean'
    return next((name for name, kinds in TYPES.items() if name != 'number' and isinstance(value, kinds)), 'number')


def misfit(value: Any, schema: dict[str, Any]) -> str | None:
    if fits(value, schema):
        return None
    declared = schema['type']
    expected = ' or '.join(declared) if isinstance(declared, list) else declared
    return f'the page reads this file as {expected}; it holds {type_name(value)}'


def fill(report: str, workdir: Path) -> tuple[str, dict[str, Any]]:
    page = page_of(report)
    raw: dict[str, Any] = {}
    newest: tuple[float, str] | None = None
    try:
        stages = stages_of(read(workdir / RUNS_INDEX)) if (workdir / RUNS_INDEX).is_file() else None
    except Exception:  # noqa: BLE001 - an unreadable index is dropped below like any file; runs then fall back to their names
        stages = None
    try:
        live = run_live(read(workdir / PROGRESS)) if (workdir / PROGRESS).is_file() else False
    except Exception:  # noqa: BLE001 - an unreadable Progress is dropped below like any file; no Run plays without one
        live = False
    playing, dropped = (playing_run(workdir, stages) if live else (None, []))
    headless = {folder.name for folder in (workdir / 'artifacts' / 'runs').glob('*/') if not (folder / 'run.json').is_file()}
    for pattern in page['schemas']:
        for source in sorted(workdir.glob(pattern)) if '*' in pattern else [workdir / pattern]:
            if not source.is_file():
                continue
            path = source.relative_to(workdir).as_posix()
            if qualification(path, stages, playing, headless):
                continue
            modified = source.stat().st_mtime
            if newest is None or modified > newest[0]:
                newest = (modified, path)
            try:
                raw[path] = read(source)
            except (Unreadable, ValueError, UnicodeDecodeError) as error:
                dropped.append({'path': path, 'reason': str(error).splitlines()[0] if str(error) else type(error).__name__})
            except Exception as error:  # noqa: BLE001 - PyYAML raises its own hierarchy; one bad file never costs the page
                dropped.append({'path': path, 'reason': f'{type(error).__name__}: {str(error).splitlines()[0] if str(error) else ""}'.rstrip(': ')})
    scored = latest_run(raw)
    scrub = scrubber(hosts_of(raw), backends_of(raw))
    files: dict[str, Any] = {}
    binaries: list[str] = []
    misread: list[dict[str, str]] = []
    worlds = worlds_of(raw)
    for path, value in raw.items():
        pattern = next(p for p in page['schemas'] if matches(p, path))
        reason = misfit(value, page['schemas'][pattern])
        if reason is not None:
            dropped.append({'path': path, 'reason': reason})
            continue
        if pattern == RUN:
            count_egress(value)
        if pattern == CHUNKS:
            value = read_chunks(value)
        if pattern == LEDGER:
            value = read_ledger(value)
        if pattern == ANOMALIES:
            value = read_anomalies(value, tool_names(raw.get(TOPOLOGY)))
        if pattern in (TOOL_EVENTS, TRIAL_TOOL_EVENTS) and read_calls(value):
            binaries.append(path)
        projected = project(value, page['schemas'][pattern])
        misread.extend({'path': path, **entry} for entry in misread_in(projected, page['schemas'][pattern]))
        if pattern in (TRANSCRIPT, TRIAL_TRANSCRIPT):
            read_context(path, projected, scored)
        projected = world_of(pattern, path, projected, worlds)
        files[path] = scrub_warnings(projected, scrub) if pattern == RUN_WARNINGS else scrub_all(projected, scrub)
    cut = degrade(files, page['soft'], playing)
    carried = sorted(conversation_of(path) for path in binaries if path in files)
    if carried:
        cut.append({'kind': 'tool_binaries', 'items': carried})
    size = payload_bytes(files)
    if size > page['max']:
        raise Refusal(f'payload is {size} bytes after every cut; the page admits {page["max"]}', 3)
    filled_at = iso(datetime.now(tz=timezone.utc).timestamp())
    verification = files.get(VERIFICATION)
    written_by = verification.get('plugin_version') if isinstance(verification, dict) else None
    plugin_version = written_by if isinstance(written_by, str) and written_by != '' else None
    envelope = {
        'version': page['version'],
        'schema_hash': page['schema_hash'],
        'inputs_newest': None if newest is None else {'path': newest[1], 'at': iso(newest[0])},
        'plugin_version': plugin_version,
        'dropped': dropped,
        'cut': cut,
        'files': files,
    }
    stamp = hashlib.sha256(serialized(envelope).encode('utf-8')).hexdigest()
    envelope = {**envelope, 'filled_at': filled_at, 'stamp': stamp}
    text = serialized(envelope)
    data = page['data']
    filled = report[:data.start()] + data.group(1) + text + data.group(3) + report[data.end():]
    sidecar = f'window.{UPDATE_HOOK}({json.dumps(stamp)}, {serialized(text)});\n'
    summary = {
        'version': page['version'],
        'kept': sorted(files),
        'dropped': dropped,
        'cut': cut,
        'misread': misread[:MISREAD_CAP],
        'misread_count': len(misread),
        'bytes': size,
        'filled_at': filled_at,
        'stamp': stamp,
    }
    return filled, summary, sidecar


def serialized(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('\ufffd', '\\ufffd')


def write_atomically(out: Path, text: str) -> None:
    mode = out.stat().st_mode & 0o777 if out.is_file() else 0o644
    handle, temporary = tempfile.mkstemp(dir=out.parent, prefix=f'.{out.name}.', suffix='.tmp')
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(text.encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, out)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def fill_page(argv: list[str]) -> None:
    if len(argv) != 4:
        raise Refusal(__doc__.strip())
    template, workdir, out = Path(argv[1]), Path(argv[2]), Path(argv[3])
    if not template.is_file():
        raise Refusal(f'template not found: {template}')
    if not workdir.is_dir():
        raise Refusal(f'workdir not found: {workdir}')
    filled, summary, sidecar = fill(template.read_text(encoding='utf-8'), workdir)
    try:
        write_atomically(out, filled)
        write_atomically(out.parent / SIDECAR, sidecar)
    except OSError as error:
        raise Refusal(f'cannot write {out}: {error.strerror}') from error
    print(json.dumps(summary, ensure_ascii=False))


def main(argv: list[str]) -> int:
    try:
        check_python(tuple(sys.version_info))
        fill_page(argv)
    except Refusal as refusal:
        print(refusal.reason, file=sys.stderr)
        return refusal.code
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
