#!/usr/bin/env python3
"""assert_additive_extend.py — prove an EXTEND actually only ADDED.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/assert_additive_extend.py" <pre.yaml> <new.yaml>
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/assert_additive_extend.py" <pre.yaml> <new.yaml> \
        <pre-obligations.yaml> <new-obligations.yaml>

WHY THIS EXISTS. Extend mode freezes the source set's policies byte-for-byte and authors only
for what the newly linked suite leaves ungraded. The backend refuses a modified baseline at
publish — but "the backend says it refused" and "the file on my disk is additive" are two
different claims, and only the second is the one the customer's mirror is about to become. A
changed or missing policy here is a BACKEND DEFECT, not something to work around: stop, do not
write `.eval_set_id`, and report it.

YAML-EQUAL, not byte-equal: the extension re-serializes the whole document, so key order and
quoting legitimately move while the POLICY must not. Comparison is on the parsed bodies.

THE LEDGER IS THE SECOND THING AN EXTEND PRODUCES. The typed coverage ledger — the rows of
`out/obligations.yaml`, served as each set's `detail.obligations` — is re-authored against the
FINAL population. An extension that added no policy but rebuilt those rows is not a no-op:
adopting it costs no check and gains the accounting. Pass both ledgers (a YAML/JSON file holding
either the `{obligations: [...], reachability: [...]}` document or the bare `detail.obligations`
list) and a ledger-only change is its OWN outcome (exit 5). Omit them and exit 4 stands. The
table in `generation_trace.md` is rendered from these rows and is never read here.

NORMALISATION IS NOT A BROKEN FREEZE. The backend's executable-set filter runs AFTER the
merge that restores the frozen baseline, and it repairs bodies in place — a `|`-block
expression scalar collapsed by `normalize_expression_text`, a `turn_based` policy re-scoped to
`end_to_end` because it grades end-to-end. Those repairs are what make the set RUNNABLE, and the
`/edit-evals` commit gate deliberately skips the warn-tier checks that would have caught them
earlier, so the policy a repair session just landed is exactly the one the next extension
normalises. Byte-compared, that is indistinguishable from the backend breaking its freeze — and
exit 3 says "BACKEND DEFECT, STOP", which stalls the loop on a routine repair. So a change
CONFINED to those two shapes gets its own verdict (exit 6) naming what moved.

STREAMS. stdout is a one-line verdict plus the added names. Everything else goes to stderr.

EXIT CODES
  0  additive — every pre-existing policy is YAML-equal AND at least one policy was added
  6  additive, but the backend NORMALISED one or more frozen policies (expression whitespace,
     turn→end_to_end scope) — see above. No check was removed and no rubric rewritten; adopt,
     and fix it at the source with /edit-evals if the two sets must stay identical.
  5  additive, nothing added, but the typed COVERAGE LEDGER was re-authored. Adopt the new set: the
     policies are byte-for-byte the ones you have and the accounting now describes the suite
     that ships. Only reachable when both ledgers are supplied.
  4  additive but EMPTY — nothing was added and nothing else moved either. Not a defect, but not
     a success: the suite's gap is still ungraded, and moving `.eval_set_id` would point it at a
     copy of the old set.
  3  NOT additive — a policy changed or was removed (names on stderr). Backend defect: STOP.
  2  a file is missing / unreadable / not a policy mapping
 64  usage error

NONE of these codes may be produced by anything else in the caller's chain. `curl` uses 3 and 5
for its own failures and `plurai fetch policies` exits 3 on a draft (and 13 on a credit refusal, with the kind's
sentence on stderr: the error posture's credit stop, never an adoption), so the SKILL keeps every
fetch OUT of the `&&` chain this script ends: a proxy-resolution failure exiting 5 would otherwise
read as "adopt the new eval set" with neither this script nor `verify_policies.py` having run.

Stdlib + pyyaml (the same dependency `verify_policies.py` already requires).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - the skill runs this with the project venv
    print(
        'assert_additive_extend: pyyaml is not importable by this interpreter '
        f'({sys.executable}). Run it with the project venv, as verify_policies.py is run.',
        file=sys.stderr,
    )
    raise SystemExit(2) from None


def _load(path: Path, label: str) -> dict:
    try:
        parsed = yaml.safe_load(path.read_text(encoding='utf-8'))
    except OSError as exc:
        print(f'assert_additive_extend: cannot read the {label} set at {path}: {exc}', file=sys.stderr)
        raise SystemExit(2) from exc
    except yaml.YAMLError as exc:
        print(f'assert_additive_extend: the {label} set at {path} is not parseable YAML: {exc}', file=sys.stderr)
        raise SystemExit(2) from exc
    if not isinstance(parsed, dict) or not parsed:
        print(f'assert_additive_extend: the {label} set at {path} is not a non-empty policy mapping.', file=sys.stderr)
        raise SystemExit(2)
    return parsed


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _ledger(path: Path, label: str) -> str | None:
    """One typed coverage ledger in canonical form — or ``None`` when it is not usable evidence.

    Unreadable is NOT a difference: a missing file compared against a present one would read as
    "the ledger was re-authored" and license the marker move on the strength of a failed fetch,
    which is the one direction that adopts an unverified set. So each of these is UNKNOWN:

    * an OSError / decode error, or ``ValueError`` (``read_text`` on a path holding a NUL byte);
    * an EMPTY file, or one that is not parseable YAML/JSON;
    * a document that carries no typed obligation rows — a non-empty list of mappings, each with an
      ``obligation_id`` — whether at ``obligations:`` or as the bare list ``detail.obligations`` is.

    Row and reachability ORDER is not content: both lists are compared sorted.
    """
    try:
        raw = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print(f'assert_additive_extend: cannot read the {label} coverage ledger at {path}: {exc} — treating the ledger as UNKNOWN.', file=sys.stderr)
        return None
    if not raw.strip():
        print(f'assert_additive_extend: the {label} coverage ledger at {path} is EMPTY — treating it as UNKNOWN, never as a difference.', file=sys.stderr)
        return None
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        print(f'assert_additive_extend: the {label} coverage ledger at {path} is not parseable: {exc} — treating it as UNKNOWN.', file=sys.stderr)
        return None
    rows, reach = (loaded.get('obligations'), loaded.get('reachability')) if isinstance(loaded, dict) else (loaded, None)
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) and row.get('obligation_id') for row in rows):
        print(
            f'assert_additive_extend: the {label} file at {path} carries no typed obligation rows, so it is not a '
            f'coverage ledger — treating it as UNKNOWN.',
            file=sys.stderr,
        )
        return None
    reachability = reach if isinstance(reach, list) else []
    return _canonical({'obligations': sorted(map(_canonical, rows)), 'reachability': sorted(map(_canonical, reachability))})


#: The ONE scope value the backend's filter re-scopes a mis-scoped policy TO. A move in the other
#: direction is a narrowing of what the policy grades and is NOT normalisation.
_NORMALISED_SCOPE = 'end_to_end'


def _normalisation_only(before: object, after: object) -> bool:
    """Is the difference between two policy bodies CONFINED to the backend's own repairs?

    Two, and exactly two (see the module docstring): the expression re-serialized with different
    whitespace, and a turn-scoped policy re-scoped to ``end_to_end``. Every other key must be
    equal, so a rewritten rubric, a moved threshold or an added guard is still exit 3.
    """
    if not isinstance(before, dict) or not isinstance(after, dict) or set(before) | {'evaluation_scope'} != set(after) | {'evaluation_scope'}:
        return False
    for key in set(before) | set(after):
        if before.get(key) == after.get(key):
            continue
        if key == 'expression' and ' '.join(str(before.get(key)).split()) == ' '.join(str(after.get(key)).split()):
            continue
        if key == 'evaluation_scope' and after.get(key) == _NORMALISED_SCOPE:
            continue
        return False
    return True


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 5):
        print('usage: assert_additive_extend.py <pre-extend.yaml> <new.yaml> [<pre-obligations.yaml> <new-obligations.yaml>]', file=sys.stderr)
        return 64
    pre = _load(Path(argv[1]), 'pre-extend')
    new = _load(Path(argv[2]), 'new')

    removed = sorted(name for name in pre if name not in new)
    moved = [name for name in pre if name in new and new[name] != pre[name]]
    # SPLIT BEFORE JUDGING. A frozen policy the backend NORMALISED is a routine repair the
    # freeze does not cover, and calling it a backend defect stalls the loop on it.
    repaired = {name for name in moved if _normalisation_only(pre[name], new[name])}
    normalised = sorted(repaired)
    changed = sorted(set(moved) - repaired)
    added = sorted(name for name in new if name not in pre)

    if removed or changed:
        # Both classes named separately: they have the same remedy but not the same story, and
        # an operator reporting this needs to say which one the backend did.
        if removed:
            print(f'NOT ADDITIVE — {len(removed)} pre-existing policy(ies) are GONE from the new set: {removed}', file=sys.stderr)
        if changed:
            print(f'NOT ADDITIVE — {len(changed)} pre-existing policy(ies) CHANGED: {changed}', file=sys.stderr)
        print(
            'Extend mode freezes the source set byte-for-byte, so this is a BACKEND DEFECT. Do NOT write the new id to '
            '.eval_set_id — the customer would lose checks they have today. Keep the pre-extend set and report it.',
            file=sys.stderr,
        )
        if normalised:
            print(f'(also NORMALISED, and not part of the refusal: {normalised})', file=sys.stderr)
        print('additive=false')
        return 3

    if normalised:
        # Its OWN verdict, ahead of both the added and the empty arms: the operator has to know a
        # frozen body moved, and the loop must not stop for it. Named, so "which ones and what
        # moved" is answerable without diffing two YAML files by hand.
        print(
            f'assert_additive_extend: the backend NORMALISED {len(normalised)} frozen policy(ies) {normalised} — the '
            f'executable-set filter runs after the freeze and repairs a multi-line expression scalar or a turn-scoped '
            f'policy that grades end-to-end. No check was removed and no rubric rewritten, so this is NOT the freeze '
            f'breaking. Adopt the new set; fix it at the source by editing the evals if the two sets must stay identical.',
            file=sys.stderr,
        )
        print(f'additive=true added={len(added)} normalised={len(normalised)}: {", ".join(normalised)}')
        return 6

    if not added:
        # The LEDGER half, before the no-op verdict: an extension that found nothing to author
        # can still have re-authored the typed rows against the final population.
        if len(argv) == 5:
            pre_ledger, new_ledger = _ledger(Path(argv[3]), 'pre-extend'), _ledger(Path(argv[4]), 'new')
            if pre_ledger is not None and new_ledger is not None and pre_ledger != new_ledger:
                print(
                    'assert_additive_extend: the extension added no policy but RE-AUTHORED the typed coverage ledger. Every '
                    'pre-existing policy is YAML-equal, so adopting the new set changes no check and replaces an '
                    'accounting written for a superseded population.',
                    file=sys.stderr,
                )
                print('additive=true added=0 ledger=rebuilt')
                return 5
        # Exit 4, not 0: the caller dispatches on the code, and "the extension ran and changed
        # nothing" must not read as "the extension covered the gap".
        print(
            'assert_additive_extend: the extension added NO policy. The set is unchanged, so the linked suite\'s '
            'ungraded targets are STILL ungraded — do not move .eval_set_id, and read the extend job\'s warnings for why.',
            file=sys.stderr,
        )
        print('additive=true added=0')
        return 4
    print(f'additive=true added={len(added)}: {", ".join(added)}')
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main(sys.argv))
