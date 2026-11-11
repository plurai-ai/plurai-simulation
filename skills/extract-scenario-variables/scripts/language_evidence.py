#!/usr/bin/env python3
"""The languages an agent SERVES, from typed evidence — and the `language:` mix that follows from it.

`scenario_variables.yaml`'s `language` is the only channel that puts a language on the scenario set:
unset authors every scenario blind, and a blind set comes out in one language. This reads what the
agent's own files DECLARE and proposes the declaration; it never guesses a language from a market,
a name or a place.

Evidence, each with a citation:

    locale    a UI translation catalogue: a child of an i18n directory (`locales/he.json`, `i18n/fr/`),
              a gettext `<tag>/LC_MESSAGES/`, an Apple `<tag>.lproj/`. CITED, never PROPOSED: a
              catalogue says which languages the UI strings were translated into (a translation
              platform fills dozens), not which languages the agent's users converse in.
    setting   a language setting with literal values (`SUPPORTED_LANGUAGES = ['en', 'he']`,
              `locales: [en, fr]`, `defaultLocale: 'he'`)
    prd       a language the PRD names (the closed ISO 639-1 name set, matched by exact form)

A multilingual system prompt is the one kind a script cannot read; the skill adds that language by
hand, citing the prompt. The PROPOSAL: no evidence of a language other than English is `null` (never
invent one). Otherwise the dominant language — a declared default setting, else English — takes 0.6
and the rest share 0.4, the weights summing to exactly 1; a single language is the bare code. Only
setting and PRD evidence proposes a language; a catalogue-only language is listed, not weighted.

Usage:
    python language_evidence.py <workdir> [--json]

Exit 0 always (it reports); 2 when the workdir does not exist.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

#: ISO 639-1, keyed by the language NAME as prose spells it — the closed set `verify.py` matches
#: archetype prose against (it derives `_ISO_639_LANGUAGE_NAMES` from these keys).
NAME_TO_CODE: Dict[str, str] = {
    'Abkhazian': 'ab', 'Afar': 'aa', 'Afrikaans': 'af', 'Akan': 'ak', 'Albanian': 'sq', 'Amharic': 'am', 'Arabic': 'ar',
    'Aragonese': 'an', 'Armenian': 'hy', 'Assamese': 'as', 'Avaric': 'av', 'Avestan': 'ae', 'Aymara': 'ay',
    'Azerbaijani': 'az', 'Bambara': 'bm', 'Bashkir': 'ba', 'Basque': 'eu', 'Belarusian': 'be', 'Bengali': 'bn',
    'Bihari': 'bh', 'Bislama': 'bi', 'Bosnian': 'bs', 'Breton': 'br', 'Bulgarian': 'bg', 'Burmese': 'my', 'Catalan': 'ca',
    'Chamorro': 'ch', 'Chechen': 'ce', 'Chichewa': 'ny', 'Chinese': 'zh', 'Chuvash': 'cv', 'Cornish': 'kw', 'Corsican': 'co',
    'Cree': 'cr', 'Croatian': 'hr', 'Czech': 'cs', 'Danish': 'da', 'Divehi': 'dv', 'Dutch': 'nl', 'Dzongkha': 'dz',
    'English': 'en', 'Esperanto': 'eo', 'Estonian': 'et', 'Ewe': 'ee', 'Faroese': 'fo', 'Fijian': 'fj', 'Finnish': 'fi',
    'French': 'fr', 'Fulah': 'ff', 'Gaelic': 'gd', 'Galician': 'gl', 'Ganda': 'lg', 'Georgian': 'ka', 'German': 'de',
    'Greek': 'el', 'Guarani': 'gn', 'Gujarati': 'gu', 'Haitian': 'ht', 'Hausa': 'ha', 'Hebrew': 'he', 'Herero': 'hz',
    'Hindi': 'hi', 'Hungarian': 'hu', 'Icelandic': 'is', 'Ido': 'io', 'Igbo': 'ig', 'Indonesian': 'id', 'Interlingua': 'ia',
    'Interlingue': 'ie', 'Inuktitut': 'iu', 'Inupiaq': 'ik', 'Irish': 'ga', 'Italian': 'it', 'Japanese': 'ja',
    'Javanese': 'jv', 'Kannada': 'kn', 'Kanuri': 'kr', 'Kashmiri': 'ks', 'Kazakh': 'kk', 'Khmer': 'km', 'Kikuyu': 'ki',
    'Kinyarwanda': 'rw', 'Kirghiz': 'ky', 'Komi': 'kv', 'Kongo': 'kg', 'Korean': 'ko', 'Kurdish': 'ku', 'Lao': 'lo',
    'Latvian': 'lv', 'Limburgan': 'li', 'Lingala': 'ln', 'Lithuanian': 'lt', 'Luxembourgish': 'lb', 'Macedonian': 'mk',
    'Malagasy': 'mg', 'Malay': 'ms', 'Malayalam': 'ml', 'Maltese': 'mt', 'Manx': 'gv', 'Maori': 'mi', 'Marathi': 'mr',
    'Marshallese': 'mh', 'Mongolian': 'mn', 'Nauru': 'na', 'Navajo': 'nv', 'Ndonga': 'ng', 'Nepali': 'ne',
    'Norwegian': 'no', 'Occitan': 'oc', 'Ojibwa': 'oj', 'Oriya': 'or', 'Oromo': 'om', 'Ossetian': 'os', 'Pali': 'pi',
    'Pashto': 'ps', 'Persian': 'fa', 'Polish': 'pl', 'Portuguese': 'pt', 'Punjabi': 'pa', 'Quechua': 'qu',
    'Romanian': 'ro', 'Romansh': 'rm', 'Rundi': 'rn', 'Russian': 'ru', 'Samoan': 'sm', 'Sango': 'sg', 'Sanskrit': 'sa',
    'Sardinian': 'sc', 'Serbian': 'sr', 'Shona': 'sn', 'Sindhi': 'sd', 'Sinhala': 'si', 'Slovak': 'sk', 'Slovenian': 'sl',
    'Somali': 'so', 'Sotho': 'st', 'Spanish': 'es', 'Sundanese': 'su', 'Swahili': 'sw', 'Swati': 'ss', 'Swedish': 'sv',
    'Tagalog': 'tl', 'Tahitian': 'ty', 'Tajik': 'tg', 'Tamil': 'ta', 'Tatar': 'tt', 'Telugu': 'te', 'Thai': 'th',
    'Tibetan': 'bo', 'Tigrinya': 'ti', 'Tsonga': 'ts', 'Tswana': 'tn', 'Turkish': 'tr', 'Turkmen': 'tk', 'Twi': 'tw',
    'Uighur': 'ug', 'Ukrainian': 'uk', 'Urdu': 'ur', 'Uzbek': 'uz', 'Venda': 've', 'Vietnamese': 'vi', 'Volapuk': 'vo',
    'Walloon': 'wa', 'Welsh': 'cy', 'Wolof': 'wo', 'Xhosa': 'xh', 'Yiddish': 'yi', 'Yoruba': 'yo', 'Zhuang': 'za',
    'Zulu': 'zu',
    # Everyday exonyms an author is at least as likely to write as the ISO name — each names a
    # language ALREADY in the table under another spelling.
    'Cantonese': 'zh', 'Castilian': 'es', 'Farsi': 'fa', 'Filipino': 'tl', 'Flemish': 'nl', 'Mandarin': 'zh',
}
#: ISO 639-1 codes whose name is not in the prose set (multi-word, or a collision `verify.py` records).
_UNNAMED_CODES = frozenset({'fy', 'ho', 'ii', 'kj', 'kl', 'la', 'lu', 'nb', 'nd', 'nn', 'nr', 'se', 'to'})
CODES = frozenset(NAME_TO_CODE.values()) | _UNNAMED_CODES
#: Deprecated codes still found in locale trees, mapped onto the modern tag.
_ALIASES = {'iw': 'he', 'in': 'id', 'ji': 'yi'}

ENGLISH = 'en'
DOMINANT_WEIGHT = 0.6
#: The citation prefix of a UI translation catalogue — evidence that is cited but never proposes a language.
CATALOGUE = 'locale '

#: Directories whose children are one catalogue per language.
#: Not `lang` / `languages`: in a tool that handles PROGRAMMING languages those hold `ts/`, `go/`, … — and `ts` is Tsonga.
_LOCALE_DIRS = frozenset({'locales', 'locale', 'i18n', 'l10n', 'translations', 'intl'})
#: A locale tag: a language code, optionally a region or script subtag (`he`, `pt-BR`, `zh_Hant`).
_TAG = re.compile(r'^([A-Za-z]{2})(?:[-_](?:[A-Za-z]{2}|[A-Za-z]{4}|\d{3}))?$')
_CATALOGUE_SUFFIXES = ('.json', '.yaml', '.yml', '.po', '.mo', '.ts', '.js', '.mjs', '.properties', '.arb', '.ftl', '.xliff', '.xlf', '.toml', '.py')
#: Never the agent's own declarations: dependencies, build output, our own workdir.
_SKIP_DIRS = frozenset({'.git', 'node_modules', '.venv', 'venv', 'env', '__pycache__', 'site-packages', 'dist', 'build',
                        '.next', '.nuxt', 'coverage', '.tox', '.mypy_cache', '.pytest_cache', 'plurai_sim', 'vendor'})
_SETTING_SUFFIXES = frozenset({'.py', '.ts', '.tsx', '.js', '.mjs', '.cjs', '.json', '.yaml', '.yml', '.toml', '.env', '.ini', '.cfg'})
_MAX_FILES = 20000
_MAX_BYTES = 256 * 1024
#: A language setting with literal values. `default…` names the dominant language.
_SETTING = re.compile(
    r'(?P<key>\b(?:(?:supported|available|allowed|enabled)[_-]?)?(?:languages|locales|langs)\b'
    r'|\bdefault[_-]?(?:language|locale|lang)\b|\bLANGUAGE_CODE\b)["\']?\s*[:=]',
    re.IGNORECASE,
)
_QUOTED_TAG = re.compile(r'["\']([A-Za-z]{2}(?:[-_][A-Za-z0-9]{2,4})?)["\']')
#: An UNQUOTED value is read only when it is nothing but tags (`[en, he]`, `he`): a prose line after
#: `languages:` is full of two-letter English words that are also codes (`it`, `is`, `he`, `no`).
_BARE_VALUE = re.compile(r'\[?\s*[A-Za-z]{2}(?:[-_][A-Za-z0-9]{2,4})?(?:\s*,\s*[A-Za-z]{2}(?:[-_][A-Za-z0-9]{2,4})?)*\s*,?\s*\]?')
_BARE_TAG = re.compile(r'[A-Za-z]{2}(?:[-_][A-Za-z0-9]{2,4})?')
_YAML_ITEM = re.compile(r'^\s*-\s*["\']?([A-Za-z]{2}(?:[-_][A-Za-z0-9]{2,4})?)["\']?\s*$')
_WORD = re.compile(r'[^\W\d_]+', re.UNICODE)

Evidence = Dict[str, List[str]]
Proposal = Union[str, Dict[str, float], None]


def code_of(tag: str) -> Optional[str]:
    """The ISO 639-1 code a locale tag names, or None when it names none."""
    match = _TAG.match(tag)
    if not match:
        return None
    code = match.group(1).lower()
    code = _ALIASES.get(code, code)
    return code if code in CODES else None


def _note(evidence: Evidence, code: str, citation: str) -> None:
    cites = evidence.setdefault(code, [])
    if citation not in cites:
        cites.append(citation)


def _files(root: Path):
    seen = 0
    for here, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith('.'))
        base = Path(here)
        yield base, dirs, sorted(names)
        seen += len(names)
        if seen > _MAX_FILES:
            return


def _locale_evidence(root: Path, evidence: Evidence) -> None:
    for base, dirs, names in _files(root):
        rel = base.relative_to(root)
        if base.name.lower() in _LOCALE_DIRS:
            for child in dirs + [n for n in names if n.lower().endswith(_CATALOGUE_SUFFIXES)]:
                stem = child if child in dirs else child.rsplit('.', 1)[0]
                if (code := code_of(stem)) is not None:
                    _note(evidence, code, f'{CATALOGUE}{(rel / child).as_posix()}')
        if base.name == 'LC_MESSAGES' and (code := code_of(base.parent.name)) is not None:
            _note(evidence, code, f'{CATALOGUE}{rel.as_posix()}')
        for child in dirs:
            if child.endswith('.lproj') and (code := code_of(child[: -len('.lproj')])) is not None:
                _note(evidence, code, f'{CATALOGUE}{(rel / child).as_posix()}')


def _setting_evidence(root: Path, evidence: Evidence) -> List[str]:
    """Record every language a setting declares; return the default(s) a `default…` setting names."""
    defaults: List[str] = []
    for base, _dirs, names in _files(root):
        for name in names:
            path = base / name
            if path.suffix.lower() not in _SETTING_SUFFIXES and not name.startswith('.env'):
                continue
            try:
                if path.stat().st_size > _MAX_BYTES:
                    continue
                text = path.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError):
                continue
            for match in _SETTING.finditer(text):
                tags = _value_tags(text[match.end():match.end() + 400])
                line = text.count('\n', 0, match.start()) + 1
                is_default = match.group('key').lower().startswith(('default', 'language_code'))
                for tag in tags[:1] if is_default else tags:
                    if (code := code_of(tag)) is not None:
                        _note(evidence, code, f'setting {path.relative_to(root).as_posix()}:{line} ({match.group("key")})')
                        if is_default and code not in defaults:
                            defaults.append(code)
    return defaults


def _value_tags(after: str) -> List[str]:
    """The tags one setting's value carries: a quoted literal (bracketed across lines, or the rest of
    the line), a bare list or single tag, or a YAML block list on the lines below."""
    opened = after.lstrip()[:1] in '[({'
    value = after[: _close(after)] if opened else after.split('\n', 1)[0]
    if quoted := _QUOTED_TAG.findall(value):
        return quoted
    bare = value.split('#', 1)[0].strip()
    if bare:
        return _BARE_TAG.findall(bare) if _BARE_VALUE.fullmatch(bare) else []
    items = []
    for line in after.split('\n')[1:]:
        item = _YAML_ITEM.match(line)
        if not item:
            break
        items.append(item.group(1))
    return items


def _close(text: str) -> int:
    depth = 0
    for i, ch in enumerate(text):
        if ch in '[({':
            depth += 1
        elif ch in '])}':
            depth -= 1
            if depth <= 0:
                return i + 1
    return len(text)


def _prd_evidence(prd: Path, evidence: Evidence) -> None:
    try:
        text = prd.read_text(encoding='utf-8')
    except OSError:
        return
    for token in _WORD.findall(text):
        if token in NAME_TO_CODE:
            _note(evidence, NAME_TO_CODE[token], f'prd names {token}')


def agent_root(workdir: Path) -> Path:
    """The agent's directory: the one holding `plurai_sim/` (named envs included)."""
    parts = workdir.resolve().parts
    return Path(*parts[: parts.index('plurai_sim')]) if 'plurai_sim' in parts else workdir.resolve()


def collect(workdir: Path) -> tuple:
    """`(evidence, defaults)` for the agent that owns `workdir`."""
    evidence: Evidence = {}
    root = agent_root(workdir)
    _locale_evidence(root, evidence)
    defaults = _setting_evidence(root, evidence)
    _prd_evidence(workdir / 'simulation_prd.md', evidence)
    return evidence, defaults


def served(evidence: Evidence) -> List[str]:
    """The codes a setting or the PRD declares — a UI translation catalogue alone never makes one."""
    return [code for code, cites in evidence.items() if any(not cite.startswith(CATALOGUE) for cite in cites)]


def propose(evidence: Evidence, defaults: List[str]) -> Proposal:
    """The `language:` value the evidence supports; None when it shows no language but English."""
    dominant = defaults[0] if len(defaults) == 1 else ENGLISH
    others = sorted(code for code in served(evidence) if code != dominant)
    if dominant == ENGLISH and not others:
        return None
    if not others:
        return dominant
    share = round((1 - DOMINANT_WEIGHT) / len(others), 2)
    # The rounding remainder goes to the dominant language, so the weights sum to exactly 1.
    return {dominant: round(1 - share * len(others), 2), **{code: share for code in others}}


def render(proposal: Proposal) -> str:
    """The YAML value — JSON, which is YAML, so `no` (Norwegian) is never read as a boolean."""
    return 'null' if proposal is None else json.dumps(proposal, ensure_ascii=False)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('workdir', help='the plurai_sim workdir (the agent root is the directory above it)')
    parser.add_argument('--json', action='store_true', help='print {language, evidence, defaults} as JSON')
    args = parser.parse_args(argv)
    workdir = Path(args.workdir)
    if not workdir.is_dir():
        print(f'ERROR: {workdir} is not a directory', file=sys.stderr)
        return 2
    evidence, defaults = collect(workdir)
    proposal = propose(evidence, defaults)
    if args.json:
        print(json.dumps({'language': proposal, 'evidence': evidence, 'defaults': defaults}, ensure_ascii=False))
        return 0
    print(f'language: {render(proposal)}')
    if proposal is None:
        print('  no evidence the agent serves a language other than English — leave `language` null')
    proposed = set(served(evidence))
    for code, cites in evidence.items():
        tag = '' if code in proposed else ' [UI catalogue only — not a served language]'
        print(f'  {code}{tag}: {"; ".join(cites[:5])}' + (f' (+{len(cites) - 5} more)' if len(cites) > 5 else ''))
    return 0


if __name__ == '__main__':
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
