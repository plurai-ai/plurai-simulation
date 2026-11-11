#!/usr/bin/env python3
"""Copy each tool's `descriptor.description` from `plurai.config.yaml` onto `agent_topology.json`.

The report page shows a short line on what each tool does beside its calls. That line is the config's own
`tools.<name>.descriptor.description`, copied by code — never written a second time. For every `tools[]` and `rag[]`
entry of the map whose name is a `tools:` entry in the config, the map's `description` becomes the first sentence of
that text, at most `LIMIT` characters (a longer sentence is cut on a word boundary and ends in `…`). A leading tag
(`WRITE.`) is skipped; an abbreviation's period (`incl.`, `e.g.`) and an open bracket never end the sentence.

The copy is customer text: the page guard reads `agent_topology.json` inside the filled page, so each line is checked
the way the guard reads it there (`customer_text_guard.text_violations('report_html', …)`). A line the guard flags,
and a tool with no description in the config, get NO `description` — never a stale one. Every run recomputes every
entry from the config, so a re-scan refreshes the field. `render_topology.py` carries the key through a re-render.

Usage:
    record_tool_descriptions.py --config <plurai.config.yaml> --topology <agent_topology.json>

Prints one `descriptions: N of M tools` line, plus one `WARN` line per kind of gap (missing in the config, flagged by
the guard). A gap is never blocking. Exit codes: 0 written (or already current) · 2 an input is unreadable · 4 the map
could not be written.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import customer_text_guard  # noqa: E402 - the page's own customer-text vocabulary

#: The longest description the map carries.
LIMIT = 90
#: The map keys whose entries can be tools the config describes (a typed `rag` entry is a tool too).
SURFACES = ('tools', 'rag')
#: Where the filled page embeds the map: the guard reads the description under this path.
PAGE_PATH = 'artifacts/agent_topology.json'
#: A candidate sentence end: `.`, `!` or `?`, whitespace, then a capital, a digit, a quote or an opening bracket.
_SENTENCE_END = re.compile(r'[.!?]\s+(?=[A-Z0-9"\'(\[])')
#: A leading tag such as `WRITE.` or `READ-ONLY.`: one short all-caps word and a period, then whitespace or the end.
_LEADING_TAG = re.compile(r'\A[A-Z][A-Z_-]{1,19}\.(?:\s+|\Z)')
#: Words a period follows without ending the sentence (compared lower-case, without their last period).
_ABBREVIATIONS = frozenset({'incl', 'e.g', 'i.e', 'etc', 'approx', 'vs', 'no', 'ex', 'esp'})
_BRACKETS = {'(': ')', '[': ']'}


def _ends_sentence(text: str, end: int) -> bool:
    """Whether the punctuation at `text[end]` ends a sentence: not an abbreviation's or a single letter's period, and
    no `(` or `[` left open before it."""
    if text[end] == '.':
        word = (text[:end].split() or [''])[-1].lstrip('("\'[')
        if word.lower() in _ABBREVIATIONS or re.search(r'(?:^|[^A-Za-z])[A-Za-z]$', word):
            return False
    for opener, closer in _BRACKETS.items():
        depth = 0
        for char in text[:end]:
            depth = depth + 1 if char == opener else max(0, depth - 1) if char == closer else depth
        if depth:
            return False
    return True


def first_sentence(text: str) -> str:
    """The first sentence of `text`'s first paragraph, whitespace collapsed, at most `LIMIT` characters.

    A leading tag (`WRITE.`) is skipped, a period after an abbreviation (`incl.`, `e.g.`) or a single letter ends
    nothing, and a sentence never ends inside an open `(` or `[`. A tag with nothing after it gives `''`."""
    body = _LEADING_TAG.sub('', text.strip(), count=1)
    paragraph = ' '.join(re.split(r'\n\s*\n', body.strip(), maxsplit=1)[0].split())
    ends = (m.start() for m in _SENTENCE_END.finditer(paragraph) if _ends_sentence(paragraph, m.start()))
    end = next(ends, None)
    sentence = paragraph if end is None else paragraph[: end + 1]
    if len(sentence) <= LIMIT:
        return sentence
    cut = sentence[: LIMIT - 1]
    if ' ' in cut:
        cut = cut[: cut.rindex(' ')]
    return cut.rstrip(' ,;:-') + '…'


def guard_hits(text: str) -> list[str]:
    """The guard's term ids for `text` read as the filled page reads the map's prose."""
    files = {PAGE_PATH: {'tools': [{'description': text}]}}
    payload = json.dumps({'files': files}, ensure_ascii=False).replace('</', '<\\/')
    page = f'<html><script type="application/json" id="plurai-report-data">{payload}</script></html>\n'
    return sorted({v.term for v in customer_text_guard.text_violations('report_html', page)})


def config_descriptions(config: object) -> dict[str, str]:
    """`{tool name: descriptor.description}` for every config tool that has a non-empty one."""
    tools = config.get('tools') if isinstance(config, dict) else None
    if isinstance(tools, list):
        tools = {t.get('name'): t for t in tools if isinstance(t, dict)}
    out = {}
    for name, entry in (tools or {}).items():
        descriptor = entry.get('descriptor') if isinstance(entry, dict) else None
        text = descriptor.get('description') if isinstance(descriptor, dict) else None
        if isinstance(name, str) and isinstance(text, str) and text.strip():
            out[name] = text
    return out


def stamp(topology: dict, descriptions: dict[str, str]) -> tuple[dict, list[str], dict[str, list[str]]]:
    """A copy of `topology` with every surface's `description` recomputed from `descriptions`.

    Returns the copy, the `tools[]` names the config describes none for, and `{name: guard term ids}` for each line the
    guard flagged. A `rag[]` entry the config does not describe is left without one quietly: a knowledge base's map name
    is a label, not a tool name."""
    out = dict(topology)
    missing: list[str] = []
    flagged: dict[str, list[str]] = {}
    for key in SURFACES:
        items = topology.get(key)
        if not isinstance(items, list):
            continue
        stamped = []
        for item in items:
            if not isinstance(item, dict):
                stamped.append(item)
                continue
            entry = {k: v for k, v in item.items() if k != 'description'}
            name = entry.get('name')
            text = descriptions.get(name) if isinstance(name, str) else None
            if text is None:
                if key == 'tools':
                    missing.append(str(name))
            else:
                line = first_sentence(text)
                try:
                    hits = guard_hits(line)
                except Exception as exc:  # noqa: BLE001 - a line the guard cannot read is left out, never a crash
                    hits = [f'unchecked: {type(exc).__name__}']
                if hits:
                    flagged[str(name)] = hits
                elif line:
                    entry['description'] = line
            stamped.append(entry)
        out[key] = stamped
    return out, missing, flagged


def write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
        os.chmod(tmp, path.stat().st_mode & 0o777)  # the map keeps its own permissions, not mkstemp's 0600
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--config', required=True, type=Path)
    ap.add_argument('--topology', required=True, type=Path)
    args = ap.parse_args(argv)
    try:
        original = args.topology.read_text(encoding='utf-8')
        topology = json.loads(original)
        config = yaml.safe_load(args.config.read_text(encoding='utf-8')) or {}
        if not isinstance(topology, dict):
            raise ValueError(f'{args.topology} is not a JSON object')
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f'record_tool_descriptions: {exc}; {args.topology} is UNCHANGED.', file=sys.stderr)
        return 2
    updated, missing, flagged = stamp(topology, config_descriptions(config))
    text = json.dumps(updated, indent=2, ensure_ascii=False) + '\n'
    if text != original:
        try:
            write_atomic(args.topology, text)
        except OSError as exc:
            print(f'record_tool_descriptions: cannot write {args.topology} ({exc}).', file=sys.stderr)
            return 4
    tools = [t for t in updated.get('tools') or [] if isinstance(t, dict)]
    print(f'descriptions: {sum(1 for t in tools if "description" in t)} of {len(tools)} tools')
    if missing:
        print(f'WARN no description for {", ".join(missing)}: plurai.config.yaml has no `descriptor.description` for '
              'them, so the report shows none', file=sys.stderr)
    for name, hits in flagged.items():
        print(f'WARN {name}: the customer-text check did not pass its description ({", ".join(hits)}), so the report '
              'shows none — reword `descriptor.description` in plurai.config.yaml', file=sys.stderr)
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
