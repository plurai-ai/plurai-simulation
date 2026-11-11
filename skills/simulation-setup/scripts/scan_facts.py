#!/usr/bin/env python3
"""Project the PRD's `## Agent facts` into the document the backend's scan-contract check verifies.

WHY A PROJECTION AND NOT A NEW FILE. The scan already writes typed facts — one `## Agent facts`
block in `simulation_prd.md`, closed `kind`/`predicate` vocabulary, every entry carrying its
`source`. What those entries lack is the payload a checker can EXECUTE: which literals decide
the claim, over which units. So the sweep an author performs by hand (`agent_facts.md`: "sweep
every universal negative before it ships") is recorded as prose and never re-run. The optional
`check:` block on a fact is that sweep, written down as data; this script hands it to the
contract. A second facts file authored beside the PRD would be a second place for the same
claim to live, and the two would disagree within a scan.

WHAT IT WILL NOT DO. It never infers a `check:` from `evidence` prose. A sentence naming four
absent product areas is not a term list — reading one out of it would put terms the author never
swept into a check that then reports a verdict in their name. A fact with no `check:` is
projected as nothing and REPORTED as uncovered, because a projection that quietly drops facts
makes an all-clear over two checks look like an all-clear over ten.

Usage:
    scan_facts.py <simulation_prd.md> --out <facts.yaml>

ONE MALFORMED FACT NEVER REFUSES THE PRD. A `check:` this script would not compile, and two facts
sharing a `subject` and a `predicate` (`lint_prd.duplicate_facts`), are NAMED on stderr and
forwarded VERBATIM: the contract checker owns the per-fact drop, files each one it cannot read as
an advisory item, and decides the rest. A coerced or skipped fact would be a claim the author
never made, or one the report never names.

Exit 0 — the document was written (possibly with zero facts; the count and every named entry are
         on stderr).
Exit 2 — the `## Agent facts` block is not parseable YAML or carries no `facts:` list, or the PRD
         cannot be read. Nothing in it can be projected, so nothing is written. Under
         `--refuse-malformed`, also any malformed or repeated fact.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))

import _confine  # noqa: E402 - after the sys.path line it depends on

#: The kinds the scan contract can DECIDE against a corpus (the backend's scan contract).
#: Behavioural claims are deliberately absent: they are not decidable by reading a corpus, and a
#: fact declaring one would be stamped `unverified_at_scan` at the far end anyway.
CHECK_KINDS = ('absence', 'existence', 'count')
CHECK_MATCHES = ('any', 'all')
#: The contract's own severity vocabulary. Closed HERE, where a typo can be named against the
#: fact that carries it — passed through as a free string it reaches the report as an unranked
#: value and a check the author declared blocking renders as an advisory.
CHECK_SEVERITIES = ('critical', 'high', 'medium', 'low')

#: Every key a `check:` block may carry. Closed, so a typo (`term:`, `scopes:`) is NAMED here, where
#: the author reads it. The block is still forwarded verbatim: the checker drops a fact carrying an
#: unknown key, a multi-term check with no `match`, or a boolean `expected_count`, with an advisory
#: item, rather than deciding it over a scope or a count the author never declared. A checker that
#: predates that drop would DECIDE such a fact, so `scan_contract.sh` passes `--refuse-malformed`
#: to it instead.
CHECK_KEYS = frozenset({'kind', 'terms', 'match', 'scope', 'field', 'expected_count', 'severity', 'probe_ask'})

#: The predicate a `detector:` block is legal on — ONE spelling with `lint_prd.CLOSED_SET_PREDICATE`
#: and with the backend's `PREDICATE_CLOSED_SET`.
CLOSED_SET_PREDICATE = 'closed_set'

#: `origin` decides WHOSE claim loses when the corpus disagrees. A fact read off the corpus that
#: the corpus contradicts is a mis-transcription; one asserted by the PRD is outranked by the
#: corpus. The `corpus:` prefix is the agent-facts vocabulary's own token for the first case.
_CORPUS_SOURCE_PREFIX = 'corpus:'


def _load_lint_prd() -> Any:
    """The facts-block reader, from the module that already owns it.

    `lint_prd` and this script both address the same span of the same document; a second parser
    for `## Agent facts` would be a second answer to "where does the block end".
    """
    path = Path(__file__).resolve().parent / 'lint_prd.py'
    spec = importlib.util.spec_from_file_location('lint_prd', path)
    if spec is None or spec.loader is None:  # pragma: no cover - a broken checkout, not a state to handle
        raise RuntimeError(f'cannot load {path}')
    module = importlib.util.module_from_spec(spec)
    # In `sys.modules` BEFORE the exec: `dataclasses` resolves a class's owning module by name at
    # decoration time, so a module executed outside the table raises AttributeError from inside
    # `dataclasses` the day the loaded script gains a dataclass — here, in a customer's session.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _detector_payload(entry: dict) -> dict:
    """`{'detector': ...}` when the fact declares one, `{}` otherwise — projected, never inferred.

    The absence of the block is a THIRD state, distinct from `required: false`: nobody said. The
    report files it as advisory rather than as a choice the customer made, and inventing a default
    here would erase exactly that distinction.

    Shape is NOT re-validated here beyond keeping the payload's own TYPES honest:
    `lint_prd._detector_key_findings` reports the authoring defects and the backend's
    `load_scan_facts` refuses a malformed block outright, so a third full checker would be a third
    opinion about the same three keys.

    `lint_prd` does NOT run first, despite what this said — `scan_contract.sh` invokes this
    projector directly and the lint is a separate step of the setup skill. So anything projected
    in a shape the backend cannot read has to reach the backend AS THAT SHAPE, not coerced into a
    readable-looking one: a coerced value is a claim the author never made.
    """
    detector = entry.get('detector')
    if not isinstance(detector, dict) or str(entry.get('predicate') or '').strip() != CLOSED_SET_PREDICATE:
        return {}
    # A LIST, checked. `negatives: "AB-9999"` — a scalar where a list belongs, an ordinary YAML slip
    # — iterates into `['A','B','-','9','9','9','9']`, and the backend then accepts seven literals
    # and hard-blocks the scan with `positive_control … missed=['A','B','-']` and a remedy about
    # adding members. Projecting nothing is what makes the backend say "you declared negatives that
    # are not a list" instead of blaming the ladder for the author's typo.
    negatives = detector.get('negatives')
    payload: dict = {'required': detector.get('required')}
    if negatives is not None:
        # Absent stays ABSENT rather than becoming `negatives: null`: "the author wrote none" and
        # "the author wrote null" are the same fact, and only one of them is a thing they typed.
        payload['negatives'] = [str(n) for n in negatives] if isinstance(negatives, list) else negatives
    if isinstance(candidate := detector.get('candidate'), dict):
        payload['candidate'] = {
            'rung': str(candidate.get('rung') or ''),
            'pattern': str(candidate.get('pattern') or ''),
            'source': str(candidate.get('source') or ''),
        }
    return {'detector': payload}


def _origin_of(entry: dict) -> str:
    """The arbitration class the fact's `source` collapses to.

    Case-insensitive: one capital letter would otherwise flip which side loses when the corpus
    disagrees, silently and in the safer-looking direction. One reader, so the checked and the
    asserted projections cannot disagree about the same `source:` line.
    """
    return 'corpus' if str(entry.get('source') or '').lower().startswith(_CORPUS_SOURCE_PREFIX) else 'prd'


def _forwarded(entry: dict, check: Any) -> dict:
    """A fact whose `check:` this script would not compile, carried VERBATIM for the checker to judge.

    The identity fields are projected as for any fact; the `check:` block rides NESTED and
    untouched — the checker reads a nested `check` over the row, so the malformed key reaches it
    AS THAT SHAPE and its drop names the fact. No `id` when a half is missing: the checker derives
    a stable one from what the fact says rather than this script inventing `?`.
    """
    subject = str(entry.get('subject') or '').strip()
    predicate = str(entry.get('predicate') or '').strip()
    return {
        **({'id': f'{subject}.{predicate}'} if subject and predicate else {}),
        'claim': f'{subject} {predicate}: {entry.get("evidence") or ""}'.strip(),
        'origin': _origin_of(entry),
        'source': str(entry.get('source') or ''),
        'subject': subject,
        'predicate': predicate,
        **_detector_payload(entry),
        'check': check,
    }


def _project_one(entry: dict, *, where: str, project_asserted: bool = True) -> tuple[dict | None, list[str]]:
    """One agent fact -> one scan-contract fact, or `(None, [])` when it cannot be addressed.

    A `check:` it would not compile comes back FORWARDED (:func:`_forwarded`) beside the findings
    that name why, never dropped.

    A fact with NO `check:` is still projected, as `kind: asserted` — the contract stamps those
    "asserted, unverified: no static route to check this" and makes its PROCEED provisional over
    them. Dropping them instead is what made a report over 8 of 15 declared facts render exactly
    like a report over all 15, so the loop built on premises nothing had tested and found the
    defect four hours later, after generation.

    ``project_asserted=False`` restores that drop, and exists for ONE reason: a checker that
    predates the kind reports `unknown kind 'asserted'` as UNRESOLVABLE, which its report files as
    FIX-FIRST — so a new plugin against an older backend would hard-stop a PRD that is fine, one
    blocking item per check-less fact. Losing the provisional signal is a worse report; inventing
    blocking findings is a wrong one. `scan_contract.sh` probes the checker and decides.
    """
    check = entry.get('check')
    subject = str(entry.get('subject') or '').strip()
    predicate = str(entry.get('predicate') or '').strip()
    if check is None:
        # No new refusal on a document that used to pass: without both halves there is no id to
        # report a verdict under, so it stays uncovered exactly as before. `lint_prd.py` already
        # names a fact missing either.
        if not subject or not predicate or not project_asserted:
            return None, []
        return {
            'id': f'{subject}.{predicate}',
            'kind': 'asserted',
            'claim': f'{subject} {predicate}: {entry.get("evidence") or ""}'.strip(),
            'origin': _origin_of(entry),
            'source': str(entry.get('source') or ''),
            'subject': subject,
            # The PREDICATE, carried verbatim. The contract's closed-class gate finds its facts by
            # it, and deriving it back out of the composed id would be a second spelling of the
            # join — one that breaks the day a subject contains a dot, which every subject does.
            'predicate': predicate,
            **_detector_payload(entry),
        }, []
    if not isinstance(check, dict):
        return _forwarded(entry, check), [f'{where} ({subject or "?"}): `check:` must be a mapping, got {type(check).__name__}.']

    findings: list[str] = []
    # The id and the claim are BUILT from these, so a missing one used to render a verdict under
    # the literal name `?` — a finding the author cannot trace back to anything they wrote.
    if not subject or not predicate:
        findings.append(f'{where}: a fact carrying a `check:` needs both `subject` and `predicate` — they become the id the contract reports its verdict under.')
    if unknown := sorted(set(check) - CHECK_KEYS):
        findings.append(f'{where} ({subject}): `check:` carries unknown key(s) {", ".join(unknown)}; the closed set is {", ".join(sorted(CHECK_KEYS))}.')

    kind = str(check.get('kind') or '').strip()
    if kind not in CHECK_KINDS:
        findings.append(f'{where} ({subject}): `check.kind` is {kind or "missing"!r}; it must be one of {", ".join(CHECK_KINDS)}.')

    terms = check.get('terms') or []
    if not isinstance(terms, list) or not terms or any(not str(t).strip() for t in terms):
        findings.append(f'{where} ({subject}): `check.terms` must be a non-empty list of non-blank literals — the words the sweep actually looked for.')
        terms = []

    match = str(check.get('match') or 'any')
    if match not in CHECK_MATCHES:
        # Never defaulted from a typo: anything not exactly `all` would mean `any` — and which of
        # the two is the WEAKER predicate flips with the kind (`any` is stronger for an absence,
        # weaker for an existence), so there is no safe fallback to pick.
        findings.append(f'{where} ({subject}): `check.match` must be `any` or `all`, got {match!r}.')
    elif 'match' not in check and isinstance(terms, list) and len(terms) > 1:
        # …and never defaulted from an OMISSION either, once there is more than one term. The
        # default `any` verifies an "all three exist" claim on one of the three, and reports that
        # verdict under the author's own sentence.
        findings.append(f'{where} ({subject}): `check.match` is required with {len(terms)} terms — write `any` (one term is enough) or `all` (every term, in the same unit).')

    expected_count = check.get('expected_count')
    # `isinstance(True, int)` is True in Python, so `expected_count: yes` used to compile to
    # "exactly 1" — a count over a number the author never stated.
    if isinstance(expected_count, bool):
        findings.append(f'{where} ({subject}): `check.expected_count` is a boolean; it must be an integer.')
        expected_count = None
    if kind == 'count' and not isinstance(expected_count, int):
        findings.append(f'{where} ({subject}): `check.kind: count` needs an integer `expected_count` — how many units in scope must match.')
    if kind != 'count' and expected_count is not None:
        findings.append(f'{where} ({subject}): `check.expected_count` is only read for `kind: count`; on `{kind}` it is ignored.')

    severity = check.get('severity')
    if severity is not None and str(severity) not in CHECK_SEVERITIES:
        findings.append(f'{where} ({subject}): `check.severity` is {severity!r}; the closed set is {", ".join(CHECK_SEVERITIES)}.')

    scope = check.get('scope') or []
    if not isinstance(scope, list) or any(not str(s).strip() for s in scope):
        findings.append(f'{where} ({subject}): `check.scope` must be a list of non-blank globs over corpus unit ids (omit it to mean the whole corpus).')
        scope = []

    if findings:
        return _forwarded(entry, check), findings

    projected = {
        'id': f'{subject}.{predicate}',
        'kind': kind,
        'claim': f'{subject} {predicate}: {entry.get("evidence") or ""}'.strip(),
        'origin': _origin_of(entry),
        # Carried VERBATIM beside the arbitration class it collapses to: `origin` decides who
        # loses when the corpus disagrees, and the citation is what the contract resolves against
        # the scanned tree. Dropping it is why a PRD could cite a line eleven off and read clean.
        'source': str(entry.get('source') or ''),
        'subject': subject,
        'terms': [str(t) for t in terms],
        'match': match,
        'predicate': predicate,
        **_detector_payload(entry),
    }
    if scope:
        projected['scope'] = [str(s) for s in scope]
    if check.get('field'):
        projected['field'] = str(check['field'])
    if kind == 'count':
        projected['expected_count'] = int(expected_count)  # type: ignore[arg-type]
    if check.get('severity'):
        projected['severity'] = str(check['severity'])
    if check.get('probe_ask'):
        projected['probe_ask'] = str(check['probe_ask'])
    return projected, []


def project(prd_text: str, *, project_asserted: bool = True) -> tuple[list[dict], list[str], list[str]]:
    """`(facts, uncovered_subjects, findings)` for one PRD — `findings` names every malformed entry, and an unreadable block."""
    facts, uncovered, findings, duplicates, unreadable = _project(prd_text, project_asserted=project_asserted)
    return facts, uncovered, [*([unreadable] if unreadable else []), *duplicates, *findings]


def _project(prd_text: str, *, project_asserted: bool) -> tuple[list[dict], list[str], list[str], list[str], str | None]:
    """`(facts, uncovered_subjects, check_findings, duplicate_findings, unreadable)`.

    `uncovered_subjects` is returned rather than logged so the caller can print it: the count of
    facts NOT projected is the honest denominator of whatever the contract goes on to report. The
    two finding kinds stay apart so the named line can say WHICH it is — a PRD with no `check:` at
    all was once reported as "declared check(s) do not compile". Neither refuses anything: the
    entries they name are in `facts`, forwarded. `unreadable` is the one refusal — a block nothing
    in which can be projected.
    """
    lint = _load_lint_prd()
    block = lint._facts_block(lint._without_appendix(prd_text))
    if block is None:
        return [], [], [], [], None
    try:
        loaded = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        return [], [], [], [], f'the "## Agent facts" block is not parseable YAML ({exc}); nothing in it can be projected.'
    entries = loaded.get('facts') if isinstance(loaded, dict) else None
    if not isinstance(entries, list):
        return [], [], [], [], 'the "## Agent facts" block must be a mapping with a `facts:` list (see references/agent_facts.md).'

    # A repeated `subject.predicate` renders two verdicts under one id — and renaming one to
    # `<id>.2` reports a verdict under an id the author never wrote, so both are forwarded as
    # written and the pair is NAMED. The rule is the LINT's (one owner, so the two cannot disagree
    # about it); it runs over every entry, projected or not.
    duplicates = lint.duplicate_facts(entries)
    facts: list[dict] = []
    uncovered: list[str] = []
    findings: list[str] = []
    for i, entry in enumerate(entries, 1):
        where = f'facts[{i}]'
        if not isinstance(entry, dict):
            # Named and NOT forwarded: the checker refuses a document holding a non-mapping entry
            # outright, so carrying it would cost every other fact its verdict.
            findings.append(f'{where} is not a mapping; it is not forwarded.')
            continue
        projected, entry_findings = _project_one(entry, where=where, project_asserted=project_asserted)
        findings.extend(entry_findings)
        if projected is None:
            uncovered.append(f'{entry.get("subject") or "?"} / {entry.get("predicate") or "?"}')
            continue
        if projected.get('kind') == 'asserted':
            # Projected AND named. The contract now carries it as asserted-unverified, and this
            # line is still the honest denominator: the count of claims nothing tested.
            uncovered.append(f'{entry.get("subject") or "?"} / {entry.get("predicate") or "?"}')
        facts.append(projected)
    return facts, uncovered, findings, duplicates, None


def _sweep_stale(out: Path, why: str) -> None:
    """Remove a previous run's projection, and SAY it was removed.

    Every caller is a refusal, and a refusal that leaves the old document behind is laundered
    into a verdict — so the delete and the announcement travel together.
    """
    if out.is_file():
        out.unlink()
        print(f'scan_facts: removed a STALE {out} — {why}', file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('prd', help='path to simulation_prd.md')
    ap.add_argument('--out', required=True, help='where to write the projected facts YAML')
    ap.add_argument(
        '--no-asserted',
        action='store_true',
        help="DROP facts with no `check:` instead of projecting them as `kind: asserted`. For a checker that predates the kind, "
        'which files an unknown kind as a BLOCKING finding — see `_project_one`. They are still named on stderr.',
    )
    ap.add_argument(
        '--refuse-malformed',
        action='store_true',
        help='Write NOTHING and exit 2 when any declared check does not compile or two facts share an id, instead of '
        'forwarding them. For a checker that predates the per-fact drop: it would decide an unknown key over the whole '
        'corpus, a multi-term check as `any`, and `expected_count: true` as 1.',
    )
    _confine.add_flag(ap)
    args = ap.parse_args(argv)

    out = Path(args.out)
    if why := _confine.refusal(out, allow_outside=args.allow_outside_workdir):
        # Refused before anything is read or removed: the sweep below deletes an existing `out`.
        print(f'scan_facts: {why}', file=sys.stderr)
        return 2
    try:
        text = Path(args.prd).read_text(encoding='utf-8')
    except OSError as exc:
        print(f'scan_facts: cannot read {args.prd} ({exc}).', file=sys.stderr)
        _sweep_stale(out, 'it describes a PRD this run never read.')
        return 2

    facts, uncovered, findings, duplicates, unreadable = _project(text, project_asserted=not args.no_asserted)
    if unreadable:
        print(f'scan_facts: NOTHING was written:\n  - {unreadable}', file=sys.stderr)
        # SWEEP the previous scan's projection. Leaving it is how a refusal turns into a silent
        # pass: the contract runs next, reads the stale document, and reports a verdict over
        # facts this PRD no longer states.
        _sweep_stale(out, 'it projects facts from a block this PRD no longer carries readably.')
        return 2

    if args.refuse_malformed and (findings or duplicates):
        # Each refusal kind is COUNTED UNDER ITS OWN NAME: this line is what an operator reads first.
        causes = [f'{len(duplicates)} fact(s) share a subject and a predicate with an earlier fact'] if duplicates else []
        causes += [f'{len(findings)} declared check(s) do not compile'] if findings else []
        print(f'scan_facts: {" and ".join(causes)} — NOTHING was written:', file=sys.stderr)
        for finding in (*duplicates, *findings):
            print(f'  - {finding}', file=sys.stderr)
        _sweep_stale(out, 'it projects checks this PRD no longer compiles.')
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump({'facts': facts}, sort_keys=False, allow_unicode=True), encoding='utf-8')

    # An asserted fact is in BOTH lists — projected, and named as untested — so a plain sum counts
    # it twice and the denominator the SKILL tells the model to RELAY comes out inflated. A
    # forwarded fact (no top-level `kind`) is in the denominator and not among the executable ones.
    asserted = sum(1 for fact in facts if fact.get('kind') == 'asserted')
    executable = sum(1 for fact in facts if fact.get('kind') not in (None, 'asserted'))
    total = len(facts) + len(uncovered) - asserted
    print(f'scan_facts: {executable} of {total} typed fact(s) carry an executable `check:` -> {out}', file=sys.stderr)
    if findings or duplicates:
        # Each kind is COUNTED UNDER ITS OWN NAME, and every entry is named: the checker drops what
        # it cannot read with an advisory item, and this is where the author sees why.
        causes = [f'{len(duplicates)} fact(s) share a subject and a predicate with an earlier fact'] if duplicates else []
        causes += [f'{len(findings)} declared check(s) do not compile'] if findings else []
        print(f'scan_facts: {" and ".join(causes)} — forwarded as written for the contract to judge:', file=sys.stderr)
        for finding in (*duplicates, *findings):
            print(f'  - {finding}', file=sys.stderr)
    if uncovered:
        # Named, not counted. "8 uncovered" tells nobody which claims the contract did not test.
        print('scan_facts: NOT verified against the corpus (no `check:` declared):', file=sys.stderr)
        for subject in uncovered:
            print(f'  - {subject}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
