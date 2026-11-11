#!/usr/bin/env python3
"""Validate the `--domain` JSON a corpus ingest carries — the typed `instruction_chunks` selectors above all.

WHY. A RAG corpus extracted from an agent's data source carries the agent's INSTRUCTIONS beside
its knowledge — a flow file's persona, conduct rules and scripted lines sit in the same records
as its facts. The backend types every chunk as `knowledge` or `instruction`, and a chunk no
selector matches is knowledge: a knowledge question anchored on a rule ("what is your
readiness-check rule?") is a false red by construction, because a rule is something the agent
OBEYS, not something a user can ask it to recite. The manifest's `domain.instruction_chunks` is
the client's typed declaration of where the instructions are, and the backend validates each
selector over the whole upload and REFUSES an invalid one with a warning, never a failed build.
A refused selector is silent knowledge: the rules it was meant to mark stay knowledge and the
walk anchors on them. So the shape is checked HERE, before the ingest, where a typo is cheap.

TWO TIERS. **FINDINGS** are shapes the BACKEND refuses, and they move the exit code. **NOTES** are
shapes this script would rather you did not write — readable, defensible, and accepted by the
backend exactly as they are — and they never do. The skill gates the ingest on exit 0, so a check
that refuses a key the backend reads would weaken a working selector, and its instruction chunks
would ship typed as knowledge. An unknown domain key, a non-bool `semantic_enrichment`, a
non-string `entity_type`, an unknown selector key, a `key_path` on a non-metadata selector and a
missing `reason` are therefore notes: the backend coerces or ignores each of them.

The bare-string `domain_keywords` refusal is a finding: the backend does
`list(domain.get('domain_keywords', []) or [])`, so a string does not fail — it explodes into one
keyword per CHARACTER, and the steering it was meant to give the graph builder becomes noise no
warning names. Accepted-and-destroyed is not accepted.

The vocabularies are READ, not retyped: `plurai_contracts.contract('chunk_role_selectors', …)`
takes them from the vendored `GET /client-contracts` payload when the backend serves that block, and
falls back to the literals below when it does not, saying so.

The rules mirror the backend's role-selector validation (without the population checks,
which need the chunks): a selector is `{source, regex, key_path?, reason?}`; `source` is one of
`metadata` / `name` / `page_content`; a `metadata` selector needs `key_path` (a dot path into the
chunk metadata) and its `regex` is optional; the other two need a `regex`; a regex must compile.

A `source: name` selector is also valid when it matches no document, and then it types nothing. With
`--extract-shape` the check reads the `per_document` map the extract-only run prints (document name ->
chunk count) and fails a name selector whose regex matches none of those names. The names are matched
as printed there, with `re.search`. Without the flag the check stays shape-only.

A document with a `domain` key is the `{domain, questions}` file `plurai ingest-corpus --domain-file` embeds in the corpus
part; the mapping it holds is what is checked, and `questions` must be a list of non-empty strings.

Usage:
    verify_domain.py <domain.json | corpus_domain.json | -> [--extract-shape <extract.json>]
    # exit 0 clean (notes may print) · 1 findings · 2 unreadable
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

#: The fallback literals the contract read below takes when the vendored payload does not carry the
#: block (an older backend, a box on a pinned digest). With the block present the literals are not
#: consulted — and `plurai_contracts.report_fallbacks()` says which.
_LOCAL_VOCABULARY = {
    # Where a selector reads.
    'selector_sources': ['metadata', 'name', 'page_content'],
    # Every key a selector may carry. NOT a refusal: the backend has no unknown-key check, so an extra
    # key is a NOTE here (see `check_selectors`).
    'selector_keys': ['source', 'regex', 'key_path', 'reason'],
    # The domain keys the backend's graph builder and corpus job read: `task_description`,
    # `domain_keywords` and `semantic_enrichment` steer the graph builder; the corpus job reads
    # `entity_type` and `instruction_chunks` beside them. Derived from what the backend reads, not
    # from what this script happens to check.
    'domain_keys': ['task_description', 'semantic_enrichment', 'entity_type', 'instruction_chunks', 'domain_keywords'],
}

#: The vendored contract block this vocabulary comes from, when the backend serves one. Named here
#: rather than inline so the hand-off is greppable: until `GET /client-contracts` carries
#: `chunk_role_selectors`, every read below takes the fallback and reports it.
CONTRACT_BLOCK = 'chunk_role_selectors'


def _vocabulary() -> dict[str, list[str]]:
    """The selector / domain vocabulary, READ from the vendored payload where it exists.

    A hand-written mirror of a backend allowlist drifts, so the mirror is derived. `plurai_contracts` is imported lazily and defensively: this script is
    run standalone by a SKILL snippet, and a customer copying it out of the plugin must still get
    the check rather than an ImportError.
    """
    local = {key: list(value) for key, value in _LOCAL_VOCABULARY.items()}
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
        from plurai_contracts import contract  # noqa: PLC0415 - repo script, optional at runtime
    except ImportError:
        return local
    served = contract(CONTRACT_BLOCK, default=_LOCAL_VOCABULARY)
    if not isinstance(served, dict):
        return local
    # Per KEY, not per block: a served block that carries only some of the vocabulary leaves the
    # rest on this consumer's literal rather than emptying it.
    return {key: list(served.get(key) or fallback) for key, fallback in local.items()}


_VOCABULARY = _vocabulary()

SELECTOR_SOURCES = tuple(_VOCABULARY['selector_sources'])
SELECTOR_KEYS = frozenset(_VOCABULARY['selector_keys'])
DOMAIN_KEYS = frozenset(_VOCABULARY['domain_keys'])


USAGE = 'usage: verify_domain.py <domain.json | -> [--extract-shape <extract.json>]'


class Report(NamedTuple):
    """What the check found, split by WHO refuses it.

    `findings` are shapes the backend will not accept; they move the exit code, and the SKILL gates
    the ingest on it. `notes` are shapes the backend accepts as they are — reported so an author can
    tighten them, never so a model deletes a working selector to get to exit 0.
    """

    findings: list[str]
    notes: list[str]


def check_selectors(raw: object) -> Report:
    """`instruction_chunks`; an absent key is a valid declaration of "none".

    Mirrors the backend's role-selector validation. Three of its checks are NOT there — an unknown
    key, a `key_path` on a non-metadata selector, and a missing `reason` — and each is a note.
    """
    if raw is None:
        return Report([], [])
    if not isinstance(raw, list):
        return Report([f'instruction_chunks must be a list of selectors, got {type(raw).__name__}.'], [])
    findings: list[str] = []
    notes: list[str] = []
    for i, spec in enumerate(raw):
        where = f'instruction_chunks[{i}]'
        if not isinstance(spec, dict):
            findings.append(f'{where} must be a mapping, got {type(spec).__name__}.')
            continue
        if unknown := sorted(set(spec) - SELECTOR_KEYS):
            # NOTE: `validate_role_selectors` reads the four keys it knows and ignores the rest —
            # it has no unknown-key check. An extra key is inert, not a build failure.
            notes.append(f'{where} carries key(s) the backend does not read: {", ".join(unknown)}; it reads '
                         f'{", ".join(sorted(SELECTOR_KEYS))}. Harmless, but probably a typo for one of them.')
        source = str(spec.get('source') or '').strip()
        if source not in SELECTOR_SOURCES:
            findings.append(f'{where}: source must be one of {", ".join(SELECTOR_SOURCES)}, got {source!r}.')
            continue
        key_path = str(spec.get('key_path') or '').strip()
        regex = str(spec.get('regex') or '').strip()
        if source == 'metadata' and not key_path:
            findings.append(f'{where}: a metadata selector needs `key_path` (a dot path into the chunk metadata, e.g. `kind`).')
        if source != 'metadata' and key_path:
            # NOTE: the backend constructs `RoleSelector(key_path=…)` and simply never reads it on
            # a non-metadata source. Dead, not invalid.
            notes.append(f'{where}: `key_path` is read only by a metadata selector; on `{source}` the backend ignores it.')
        if source != 'metadata' and not regex:
            findings.append(f'{where}: a `{source}` selector needs a `regex` — with none it matches nothing.')
        if regex:
            try:
                re.compile(regex)
            except re.error as exc:
                findings.append(f'{where}: regex {regex!r} does not compile ({exc}).')
        if not str(spec.get('reason') or '').strip():
            # NOTE: `reason` defaults to '' in `RoleSelector` and the backend never requires it. It
            # is worth writing — it is what a refusal warning names — but it is not a shape check.
            notes.append(f'{where}: no `reason` — the build warning that names a refused selector will have nothing to say '
                         'about why it was declared. Worth adding; the backend does not require it.')
    return Report(findings, notes)


def check_domain(doc: object) -> Report:
    """The whole `--domain` document, split into what the backend refuses and what it accepts."""
    if not isinstance(doc, dict):
        return Report([f'the domain document must be a JSON object, got {type(doc).__name__}.'], [])
    findings: list[str] = []
    notes: list[str] = []
    if unknown := sorted(set(doc) - DOMAIN_KEYS):
        # NOTE: the backend picks the keys it reads out of this mapping and never validates its
        # shape, so an unknown key is inert. Refusing it made a customer delete a field.
        notes.append(f'domain key(s) the backend does not read: {", ".join(unknown)} — it reads '
                     f'{", ".join(sorted(DOMAIN_KEYS))} and ignores the rest. Harmless; check for a typo.')
    if not str(doc.get('task_description') or '').strip():
        findings.append('task_description is required — it becomes the knowledge base\'s label.')
    if 'semantic_enrichment' in doc and not isinstance(doc['semantic_enrichment'], bool):
        enrichment = doc['semantic_enrichment']
        if isinstance(enrichment, str) and enrichment.strip():
            # A FINDING, not a note, and for the same reason `domain_keywords` is one: the backend
            # accepts this and destroys what it meant. `builder.py:925` coerces with `bool(...)`,
            # for which EVERY non-empty string is True — so `"false"`, `"no"`, `"off"`, and their
            # spelling in any other language, all turn enrichment ON. The author wrote a value the
            # backend reads as its opposite, which no note can make safe.
            #
            # The rule is the TYPE, not a vocabulary of falsy words: enumerating spellings would be
            # the same defect one language later, and the type already answers it for all of them.
            findings.append(f'semantic_enrichment is the string {enrichment!r}, not true/false. The backend coerces '
                            'with `bool(...)`, for which every non-empty string is TRUE — so this turns enrichment ON '
                            'whatever the word says. Write an unquoted true or false.')
        else:
            # Everything else (a number, a list) is coerced without inverting an intent: `bool(0)`
            # is False and `bool(1)` is True, which is what an author writing 0/1 meant.
            notes.append(f'semantic_enrichment is {enrichment!r}, not true/false. The backend coerces it with '
                         '`bool(...)`.')
    if 'entity_type' in doc and not (isinstance(doc['entity_type'], str) and str(doc['entity_type']).strip()):
        # NOTE: `builder.py` coerces with `str(...).strip()`, so a number becomes its digits and a
        # blank becomes '' (which it already treats as "not declared").
        notes.append(f'entity_type is {doc["entity_type"]!r}, not a non-empty string. The backend coerces it with '
                     '`str(...).strip()`, so it will be typed as whatever that produces.')
    keywords = doc.get('domain_keywords')
    if keywords is not None and not (isinstance(keywords, list) and all(isinstance(k, str) and k.strip() for k in keywords)):
        # A FINDING, and the one over-strictness that earns it: the backend does
        # `list(domain.get('domain_keywords', []) or [])`, so a bare string does not fail — it
        # explodes into one keyword per CHARACTER, and the steering it was meant to give the graph builder
        # becomes noise no warning names. Accepted-and-destroyed is not accepted.
        findings.append(f'domain_keywords must be a list of non-empty strings, got {type(keywords).__name__}'
                        + (' (a bare string becomes one keyword per character in the backend)' if isinstance(keywords, str) else '') + '.')
    selectors = check_selectors(doc.get('instruction_chunks'))
    return Report([*findings, *selectors.findings], [*notes, *selectors.notes])


def check_extract_shape(doc: object, shape: object) -> Report:
    """Each `source: name` selector of `doc` against the documents the local extraction kept.

    `shape` is the extract-only stdout; its `per_document` map keys are the document names. A
    selector with no regex or one that does not compile is already a finding of `check_selectors`,
    so it is skipped here. Selectors on metadata or page content cannot be judged from file names.
    """
    per_document = shape.get('per_document') if isinstance(shape, dict) else None
    if not isinstance(per_document, dict):
        note = ('the extract shape carries no `per_document` map (an older SDK prints none), so no name '
                'selector was checked against the extracted documents.')
        return Report([], [note])
    if not per_document:
        return Report([], ['the extract shape lists no documents, so no name selector was checked against them.'])
    selectors = doc.get('instruction_chunks') if isinstance(doc, dict) else None
    if not isinstance(selectors, list):
        return Report([], [])
    names = sorted(str(name) for name in per_document)
    findings: list[str] = []
    for i, spec in enumerate(selectors):
        if not isinstance(spec, dict) or str(spec.get('source') or '').strip() != 'name':
            continue
        regex = str(spec.get('regex') or '').strip()
        try:
            matched = bool(regex) and any(re.search(regex, name) for name in names)
        except re.error:
            continue
        if regex and not matched:
            shown = ', '.join(names[:8]) + (f', ... ({len(names)} in all)' if len(names) > 8 else '')
            findings.append(f'instruction_chunks[{i}]: the name selector {regex!r} matches none of the {len(names)} '
                            f'extracted documents ({shown}), so it types no chunk as an instruction. Fix the regex '
                            'against those names, or drop the selector if no source file holds instructions.')
    return Report(findings, [])


#: The top-level keys of the file `plurai ingest-corpus --domain-file` reads: the domain mapping and the customer's own questions.
WRAPPER_KEYS = ('domain', 'questions')


def unwrap_corpus_domain(doc: object) -> tuple[object, Report]:
    """The domain mapping inside a `{domain, questions}` file, and what is wrong with the file around it.

    A document with a `domain` key is the file the SDK embeds in the corpus part, so the check reads the mapping
    it holds and judges `questions` (a list of non-empty strings) and any other top-level key. A document with no
    `domain` key is the bare mapping and passes through unchanged.
    """
    if not isinstance(doc, dict) or 'domain' not in doc:
        return doc, Report([], [])
    findings = [f'{key!r} is not a key of the corpus domain file ({" and ".join(WRAPPER_KEYS)} only)' for key in doc if key not in WRAPPER_KEYS]
    questions = doc.get('questions', [])
    if not isinstance(questions, list) or not all(isinstance(q, str) and q.strip() for q in questions):
        findings.append('questions must be a list of non-empty strings')
    return doc['domain'], Report(findings, [])


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    shape_path = ''
    if '--extract-shape' in args:
        at = args.index('--extract-shape')
        if at + 1 >= len(args):
            print(USAGE, file=sys.stderr)
            return 2
        shape_path = args[at + 1]
        del args[at:at + 2]
    if len(args) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        text = sys.stdin.read() if args[0] == '-' else Path(args[0]).read_text(encoding='utf-8')
        doc = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        print(f'verify_domain: cannot read the domain document ({exc})', file=sys.stderr)
        return 2
    doc, wrapper = unwrap_corpus_domain(doc)
    report = check_domain(doc)
    report = Report([*wrapper.findings, *report.findings], [*wrapper.notes, *report.notes])
    if shape_path:
        try:
            shape = json.loads(Path(shape_path).read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            print(f'verify_domain: cannot read the extract shape ({exc})', file=sys.stderr)
            return 2
        matched = check_extract_shape(doc, shape)
        report = Report([*report.findings, *matched.findings], [*report.notes, *matched.notes])
    for finding in report.findings:
        print(f'  ⚠ {finding}', file=sys.stderr)
    for note in report.notes:
        # NOTES are printed after the findings and marked differently, because they are read by a
        # model that was told to fix what this script reports. One that cannot tell them apart
        # weakens a working selector to reach exit 0, and its instruction chunks then ship typed
        # as knowledge — the outcome the selector existed to prevent.
        print(f'  · note (the backend accepts this): {note}', file=sys.stderr)
    selectors = doc.get('instruction_chunks') if isinstance(doc, dict) else None
    declared = len(selectors) if isinstance(selectors, list) else 0
    if report.findings:
        print(f'{len(report.findings)} domain finding(s) — fix them before the ingest; a refused selector leaves its '
              f'instruction chunks typed as knowledge.'
              + (f' ({len(report.notes)} note(s) above are NOT findings — the backend accepts them; do not "fix" them.)'
                 if report.notes else ''))
        return 1
    print(f'✓ domain: {declared} instruction-chunk selector(s) declared'
          + ('' if declared else ' — every chunk will be typed knowledge')
          + (f' · {len(report.notes)} note(s), none of them blocking' if report.notes else ''))
    return 0


if __name__ == '__main__':
    try:
        sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
        from _stdio import utf8_stdio
    except ImportError:  # copied out of the plugin: no scripts/ beside it, so the streams stay as Python opened them
        def utf8_stdio() -> None:
            pass
    utf8_stdio()
    sys.exit(main())
