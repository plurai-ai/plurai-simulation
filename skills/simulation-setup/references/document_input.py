# This file belongs to you. Plurai generated it from your agent as a starting point;
# review it, change it and keep it with your code.
"""Reader for the documents a bench hands the agent. Copy it beside `simulate.py` as `_documents.py`.

A document the user HANDS OVER reaches a dialog harness in one shape, on whichever turn she hands it over (the
opener included), and never as an `agent_context` key or a `seed_state` value:

* a `file` content part: `{'type': 'file', 'file': {'filename': '<field>.<ext>', 'file_data': 'data:<mime>;base64,<...>'}}`
  — the filename keeps the document's own extension, the URI its media type, for every format (text, csv,
  json, pdf, image, office). `turn_documents` returns it as `Document(filename, bytes, media_type)`.
* an `image_url` content part: `{'type': 'image_url', 'image_url': {'url': 'data:...'}}` (the bare string
  `'image_url': 'data:...'` is the same part)

`document_bytes` also reads a `data:` string (an `invoke_pipeline` input) and a dict attachment
(`{'content_type': ..., 'data_base64': ...}`) written by an older generated harness.

A served document too large to inline is folded to a `data:` URI by the SDK before the agent sees it,
so a `plurai:///documents/...` reference that reaches this module means the fold did not happen.

Every function either returns bytes or raises `UnreadableDocument`: a harness that skips a document it
cannot decode grades the agent on a question it was never shown.

An agent that reads the files a user names, not an upload, gets them by path: `plant` writes them into the
scenario's `inputs/` and the harness names each path in the message (`turn_text` is what the user said).
`world_snapshot()` reports those files with `planted_rows`, each beside the hash it had when handed over.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote_to_bytes


class UnreadableDocument(ValueError):
    """The value is not a document this reader can decode."""


#: Each planted file's path -> the sha256 of the bytes handed over. The file is planted after the run's first
#: world snapshot, so this is the only baseline that shows the agent changed or removed it. `reset()` clears it.
PLANTED: dict[Path, str] = {}


class Document(NamedTuple):
    filename: str
    data: bytes
    media_type: str | None


def _is_data_uri(value: str) -> bool:
    return value[:5].casefold() == 'data:'


def _data_uri(uri: str) -> tuple[bytes, str | None]:
    header, separator, body = uri.partition(',')
    if not separator:
        raise UnreadableDocument(f'a data: URI without a payload: {uri[:60]!r}')
    media_type, *flags = header[len('data:') :].split(';')
    try:
        data = base64.b64decode(body, validate=True) if 'base64' in (f.strip().casefold() for f in flags) else unquote_to_bytes(body)
    except (binascii.Error, ValueError) as exc:
        raise UnreadableDocument(f'the document payload is not valid base64: {exc}') from exc
    return data, media_type or None


def document_bytes(value: object) -> tuple[bytes, str | None]:
    """The raw bytes of one document and its media type (`None` when the value does not name one)."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value), None
    if isinstance(value, str):
        if _is_data_uri(value):
            return _data_uri(value)
        if value.startswith('plurai:///'):
            raise UnreadableDocument(f'{value} is an unfolded document reference: the SDK should have replaced it with a data: URI')
        raise UnreadableDocument(f'expected a data: URI, got a string that is not one: {value[:60]!r}')
    if isinstance(value, Mapping):
        kind = value.get('type')
        if kind == 'file' and isinstance(value.get('file'), Mapping):
            return document_bytes(value['file'].get('file_data'))
        if kind == 'image_url':
            spec = value.get('image_url')
            return document_bytes(spec.get('url') if isinstance(spec, Mapping) else spec)
        if (payload := value.get('data_base64') or value.get('data')) is not None:
            data, media_type = _attachment_payload(payload)
            return data, value.get('content_type') or media_type
    raise UnreadableDocument(f'expected a document (a file or image_url part, a data: URI or bytes), got {type(value).__name__}')


def _attachment_payload(payload: object) -> tuple[bytes, str | None]:
    """The `data_base64` / `data` value of a dict attachment: a data: URI, bytes, or bare base64."""
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload), None
    if isinstance(payload, str) and _is_data_uri(payload):
        return _data_uri(payload)
    try:
        return base64.b64decode(str(payload), validate=True), None
    except (binascii.Error, ValueError) as exc:
        raise UnreadableDocument(f'the document payload is not valid base64: {exc}') from exc


def turn_documents(content: object) -> list[Document]:
    """The documents one user turn carries: its `file` and `image_url` parts, in order. A string turn has none."""
    if not isinstance(content, list):
        return []
    documents: list[Document] = []
    for part in content:
        if not isinstance(part, Mapping) or part.get('type') not in ('file', 'image_url'):
            continue
        data, media_type = document_bytes(part)
        filename = (part['file'].get('filename') or '') if part['type'] == 'file' else ''
        documents.append(Document(filename, data, media_type))
    return documents


def turn_text(content: object) -> str:
    """What the user SAID on one turn: the string, or its `text` parts joined."""
    if isinstance(content, str):
        return content
    parts = content if isinstance(content, list) else []
    return '\n'.join(str(p.get('text') or '') for p in parts if isinstance(p, Mapping) and p.get('type') == 'text')


def plant(documents: list[Document], directory: Path) -> list[Path]:
    """Write each document into ``directory`` (the scenario's `inputs/`) and return the paths, for an agent that
    reads the files a user names. A document with no filename is named by its position and media type."""
    directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, document in enumerate(documents, 1):
        name = Path(document.filename).name or f'document_{index}{mimetypes.guess_extension(document.media_type or "") or ""}'
        target = directory / name
        target.write_bytes(document.data)
        PLANTED[target.resolve()] = hashlib.sha256(document.data).hexdigest()
        paths.append(target)
    return paths


def planted_rows(directory: Path) -> dict[str, dict[str, object]]:
    """The `world_snapshot()` rows of the files `plant` wrote into ``directory``, keyed `input_file:<name>`: the
    `plurai.file_row` plus `planted_sha256` (a different `sha256` means the agent changed the file), or `missing: True`
    in place of the file row when the file is gone."""
    from plurai import file_row

    rows: dict[str, dict[str, object]] = {}
    for path, digest in PLANTED.items():
        if path.parent == directory.resolve():
            gone = {'_entity_type': 'input_file', 'name': path.name, 'missing': True}
            row = file_row(path, entity_type='input_file', preview=False) if path.is_file() else gone
            rows[f'input_file:{path.name}'] = {**row, 'planted_sha256': digest}
    return rows
