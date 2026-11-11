#!/usr/bin/env python3
"""Apply one `score_run` result to a conversation's verdicts, through the SDK's one writer (`plurai verdicts apply-rescore`).

    apply_rescore.py <result.json|-> --out-dir "$OUT" --scenario <item path under $OUT> \
        [--pin "$WORKDIR"] [--set-corpus-ids a,b] [--art "$ART"]

`--scenario` is the item's directory under the run: `<scenario>`, `trial_<k>/<scenario>` or `<scenario>/stability_retry`.
The writer replaces the evals the result re-judged, keeps the rest, and appends the change to the file's history.
This script decides only whether the result may be applied, and REFUSES (nothing is sent) rather than apply part of one:

  - a SUMMARY-shaped result (truncated `reasoning`, `turns` collapsed to `turns_summary`, `findings` dropped) — re-call
    the one scenario with `summary=false`;
  - a cell with no `reasoning`, or an absent `cells` / `not_scored` / `changed` (absent is not empty);
  - a result scored with no `baseline_cells`: score_run must be given the item's current cells (:func:`baseline_cells`),
    or it carries nothing forward and diffs against nothing;
  - an audit correction the backend re-applied (`audit_corrections`) that the run's own red-cell audit holds no quote
    for: each one reaches the history with the audit row's verdict, quote and reason.

REMOVED EVALS. An eval the set no longer has leaves the item's verdicts through a `changed` row with `new: null`. The
backend writes that row only for an eval it was given a baseline cell of, so an eval the item recorded with no cell (not
applicable here) gets it from this script: only with `--pin`, only when the result neither scored nor listed it, and only
when the workdir's synced `policies_config.yaml` no longer names it (:func:`removed_without_cell`).

PIN. `--pin` names the workdir (or the pin file) holding `.eval_lane_pin.json`; the result's own `gt_revision` supplies
the current grading revision. The staleness reasons `lane_pin` weights `REFUSING` refuse (exit 5), because they void the
BEHAVIOUR record the verdicts describe; which ones is asked of `lane_pin.WEIGHTS`, never listed again here. Today:
`harness-fingerprint-changed`, `scenario-set-moved` and `run-superseded`. The rest WARN and still apply
(`gt-revision-moved`, `eval-set-moved`, `eval-revision-moved`, `corpus-moved`, `corpus-unknown`, `set-adopted`,
`set-relinked`). `corpus-moved` needs `--set-corpus-ids` to compare the SET's corpus. See
`${CLAUDE_PLUGIN_ROOT}/docs/lane-split.md`.

OVERRULES ARE INTERNAL. A cell an applied overrule restored is applied as its effective verdict alone (no
`overruled_findings`, none of the `gate_findings` rows it lifted, no `gate_authority`); the result's `applied` overrule
outcomes are recorded in the plugin's ledger, `$ART/.gate_overrules.jsonl` (`--art`, else `<--pin workdir>/artifacts`).

EXIT CODES
    0  applied
    2  usage error, unreadable input, or an applied overrule the ledger could not record
    3  a required field is missing — the message NAMES it
    4  the result is summary-shaped
    5  the eval lane's pin is stale in a way that voids the behaviour record
    6  the writer refused the result, or no installed SDK offers `plurai verdicts`
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _client_handshake  # noqa: E402 - the plugin's refusal exit
import _confine  # noqa: E402
import red_cell_audit  # noqa: E402 - the run's audit, as it was uploaded
import verdict_events  # noqa: E402 - the one route to the SDK writer

# The marker `summary=true` appends to every truncated judge prose. Its presence is proof
# the result came from the wrong mode — cheaper and more honest than guessing from length.
_TRUNCATION_MARKER = '[truncated — full text via summary=False]'

#: The internal overrule record a result carries beside its cells; it goes to the ledger, never to the verdicts.
_GATE_AUTHORITY = 'gate_authority'
#: The audit row fields a backend-applied correction carries into the history.
_AUDIT_EVIDENCE = ('quote', 'quote_turn', 'quote_file', 'reason')

_EXIT_USAGE = 2
_EXIT_MISSING_FIELD = 3
_EXIT_SUMMARY_SHAPED = 4
_EXIT_PIN_STALE = 5
_EXIT_WRITER = 6


def _pin_refuses(reason: str) -> bool:
    """Does this staleness reason void the BEHAVIOUR record, or only the judgement of it?

    ASKED OF `lane_pin.WEIGHTS`, which is where the weight lives, never answered from a second
    hand-written tuple here: a projection cannot fall out of step with the table it projects.

    Only ever reached with a reason `pin_staleness` produced, so `lane_pin` imported: the guarded
    import there is what turns a broken sibling into "no staleness", and there is nothing to
    classify in that case.
    """
    import lane_pin  # noqa: PLC0415 - sibling script, imported for its weight table

    return lane_pin.weight(reason) == lane_pin.REFUSING


#: The key the backend stamps on a cell an applied overrule restored (the cell builders for an
#: effective and a withdrawn cell): the fingerprints it lifted. The SDK strips the same key at its persistence boundary
#: (`plurai.orchestrator.client.take_gate_authority`).
OVERRULED_FINDINGS_KEY = 'overruled_findings'
_APPLIED = 'applied'


def effective_cell(cell: dict[str, Any]) -> dict[str, Any]:
    """`cell` as its effective verdict alone: no `overruled_findings`, and none of the `gate_findings` rows it
    lifted (a finding left beside the verdict it no longer describes is the overrule by another name). Pure."""
    lifted = cell.get(OVERRULED_FINDINGS_KEY)
    out = {k: v for k, v in cell.items() if k != OVERRULED_FINDINGS_KEY}
    rows = out.get('gate_findings')
    if isinstance(lifted, list) and isinstance(rows, list):
        fingerprints = {fp for fp in lifted if isinstance(fp, str)}
        kept = [row for row in rows if not (isinstance(row, dict) and row.get('fingerprint') in fingerprints)]
        if kept:
            out['gate_findings'] = kept
        else:
            del out['gate_findings']
    return out


def effective_cells(cells: dict[str, Any]) -> dict[str, Any]:
    """`{policy: cell}` through :func:`effective_cell`; a non-dict cell passes through. Pure."""
    return {name: effective_cell(cell) if isinstance(cell, dict) else cell for name, cell in cells.items()}


def applied_overrules(result: dict[str, Any]) -> list[dict[str, Any]]:
    """The result's `applied` overrule outcomes (`gate_authority.overrules`), each `{overrule, outcome, finding}`."""
    block = result.get('gate_authority')
    outcomes = block.get('overrules') if isinstance(block, dict) else None
    return [o for o in outcomes or () if isinstance(o, dict) and o.get('outcome') == _APPLIED and isinstance(o.get('overrule'), dict)]


def record_applied_overrules(art: Path, outcomes: list[dict[str, Any]]) -> int:
    """Append each applied outcome to the plugin's ledger once (a line already carrying the same gate id,
    fingerprint and reason is not repeated); the number appended. Raises :class:`Refused` (exit 2)
    when one cannot be recorded: an applied outcome that reaches no ledger is lost."""
    import gate_ledger  # noqa: PLC0415 - sibling script, the one writer of the ledger

    path = gate_ledger.ledger_path(art)
    try:
        seen = {(line.overrule.gate_id, line.overrule.fingerprint, line.overrule.reason) for line in gate_ledger.read_ledger(path)}
        added = 0
        for outcome in outcomes:
            raw = outcome['overrule']
            overrule = gate_ledger.Overrule(gate_id=str(raw.get('gate_id') or ''), fingerprint=str(raw.get('fingerprint') or ''), reason=str(raw.get('reason') or ''))
            if (overrule.gate_id, overrule.fingerprint, overrule.reason) in seen:
                continue
            finding = outcome.get('finding') if isinstance(outcome.get('finding'), dict) else None
            gate_ledger.append_overrule(
                path, overrule, finding=finding, at=gate_ledger._now(), plugin_version=gate_ledger._plugin_version(),  # noqa: SLF001 - the ledger's own stamps
                session=gate_ledger.session_id(),
            )
            seen.add((overrule.gate_id, overrule.fingerprint, overrule.reason))
            added += 1
    except (gate_ledger.OverruleRefused, gate_ledger.LedgerError, OSError) as exc:
        raise Refused(_EXIT_USAGE, f'an applied overrule could not be recorded in {path} ({type(exc).__name__}: {exc}); nothing was written.') from exc
    return added


def _ledger_art(args: argparse.Namespace) -> Path | None:
    """`--art`, else the `artifacts/` of the `--pin` workdir; `None` when neither names one."""
    if args.art:
        return Path(args.art)
    if args.pin:
        pin = Path(args.pin)
        return (pin.parent if pin.is_file() else pin) / 'artifacts'
    return None


class Refused(Exception):
    """A refusal, carrying the exit code the caller should exit with."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _load_json(path: str) -> Any:
    """Read a JSON document from a path, or from stdin when the path is `-`."""
    try:
        text = sys.stdin.read() if path == '-' else Path(path).read_text(encoding='utf-8')
    except OSError as exc:
        raise Refused(_EXIT_USAGE, f'cannot read {path}: {exc}') from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise Refused(_EXIT_USAGE, f'{path} is not valid JSON: {exc}') from exc


def extract_cells(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The rescore's own verdicts — `cells`, with every cell's `reasoning` intact.

    Rejects a summary-shaped result outright (exit 4): the verdicts keep each cell's reasoning,
    findings and per-turn stream, which summary mode truncates or drops.
    """
    cells = result.get('cells')
    if not isinstance(cells, dict) or not cells:
        raise Refused(
            _EXIT_MISSING_FIELD,
            "required field `cells` is missing (or empty) from the score_run result — nothing to apply. "
            'A result with no cells scored nothing; say so in the round report.',
        )
    summary_shaped = sorted(
        name for name, cell in cells.items()
        if isinstance(cell, dict) and ('turns_summary' in cell or _TRUNCATION_MARKER in str(cell.get('reasoning', '')))
    )
    if summary_shaped:
        raise Refused(
            _EXIT_SUMMARY_SHAPED,
            f'this result is summary-shaped (cells {summary_shaped} carry `turns_summary` and/or truncated '
            '`reasoning`), so it cannot satisfy `cells`: summary mode truncates reasoning, drops `findings`, '
            'and collapses the per-turn `turns` array. Re-call score_run for this ONE scenario with '
            'summary=false.',
        )
    missing_reasoning = sorted(
        name for name, cell in cells.items()
        if not isinstance(cell, dict) or not str(cell.get('reasoning', '')).strip()
    )
    if missing_reasoning:
        raise Refused(
            _EXIT_MISSING_FIELD,
            f'cells {missing_reasoning} carry no `reasoning`, which every reader of the verdicts needs. Report the gap '
            "in the round's write-up rather than applying a partial result.",
        )
    return cells


def extract_not_scored(result: dict[str, Any]) -> dict[str, str]:
    """`not_scored` — policy name -> why this call did NOT judge it.

    An ABSENT key is not an empty one: treating the backend's silence as absolution is the
    coverage lie the field exists to prevent, so an absent key is a refusal, not a `{}`.
    """
    not_scored = result.get('not_scored')
    if not_scored is None:
        raise Refused(
            _EXIT_MISSING_FIELD,
            'required field `not_scored` is absent from the score_run result. An absent `not_scored` is NOT an '
            'empty one — a clean `cells` with unstated coverage reads as a clean SET. Re-fetch the full result envelope.',
        )
    if not isinstance(not_scored, dict):
        raise Refused(
            _EXIT_MISSING_FIELD,
            f'`not_scored` must be a policy-name -> reason mapping, got {type(not_scored).__name__}.',
        )
    return not_scored


def extract_not_scored_kinds(result: dict[str, Any]) -> dict[str, str] | None:
    """`not_scored_kinds` — the TYPED twin of `not_scored`, or None on a backend without one.

    A SIBLING top-level map (the backend's `ScoreRunResult.not_scored_kinds`): same keys as
    `not_scored`, values from a closed vocabulary (`guard_skipped`, `turn_skipped_every_turn`,
    `no_agent_turns`, `turn_refused`, `guard_raised`) that the writer branches on.

    Absent is NOT a refusal, unlike the required fields: a backend that predates the typed map is
    a supported backend. Present in any other shape is.
    """
    kinds = result.get('not_scored_kinds')
    if kinds is None:
        return None
    if not isinstance(kinds, dict):
        raise Refused(
            _EXIT_MISSING_FIELD,
            f'`not_scored_kinds` must be a policy-name -> kind mapping, got {type(kinds).__name__}.',
        )
    return {str(name): str(kind) for name, kind in kinds.items()}


def extract_changed(result: dict[str, Any]) -> list[dict[str, Any]]:
    """`changed` — the verdict flips vs the baseline, `[]` when nothing moved.

    Absent is again distinct from empty: `[]` is the backend saying "nothing flipped",
    which is a real (and often the desired) result; a missing key means the diff was never
    reported, and "confirm the intended cells flipped and nothing else regressed" then has
    nothing to check against.
    """
    changed = result.get('changed')
    if changed is None:
        raise Refused(
            _EXIT_MISSING_FIELD,
            'required field `changed` is absent from the score_run result. `[]` (nothing flipped) is a valid '
            'answer; a missing key is not — it leaves the round with no way to confirm the intended cells '
            'flipped and nothing else regressed. Re-fetch the full result envelope.',
        )
    if not isinstance(changed, list):
        raise Refused(
            _EXIT_MISSING_FIELD,
            f'`changed` must be a list of {{policy_name, old, new}} entries, got {type(changed).__name__}.',
        )
    return changed


def baseline_cells(item_dir: Path) -> dict[str, Any]:
    """The `baseline_cells` every `score_run` of an item is given: its CURRENT cells, read from its verdicts. They are what
    `rescore='changed'` carries forward unjudged and what `changed` diffs against."""
    import verdicts_file  # noqa: PLC0415 - the one reader of a conversation's verdicts; only a caller that scores needs it

    evals = verdicts_file.read(Path(item_dir)).get('evals') or {}
    return {name: entry['cell'] for name, entry in evals.items() if isinstance(entry, dict) and isinstance(entry.get('cell'), dict)}


def _mirror_names(pin_arg: str) -> set[str] | None:
    """The policy names of the eval set's synced mirror (`policies_config.yaml` in the `--pin` workdir), or None when it
    cannot be read (then no removal is inferred)."""
    path = Path(pin_arg)
    workdir = path.parent if path.is_file() else path
    try:
        import yaml  # noqa: PLC0415 - the one PyYAML read; the caller runs under $PLURAI_PY

        loaded = yaml.safe_load((workdir / 'policies_config.yaml').read_text(encoding='utf-8'))
    except Exception as exc:  # noqa: BLE001 - no mirror, no inference: the writer then decides as before
        print(f'apply_rescore: WARNING — the policies mirror in {workdir} could not be read ({type(exc).__name__}); an eval the '
              'set removed with no recorded cell is not dropped from the verdicts.', file=sys.stderr)
        return None
    return {str(name) for name in loaded} if isinstance(loaded, dict) else None


def removed_without_cell(item_dir: Path, result: dict[str, Any], pin_arg: str | None) -> list[dict[str, Any]]:
    """`changed` removal rows (`{policy_name, old: None, new: None}`) for each eval the item recorded WITHOUT a cell that the
    result neither scored nor listed in `not_scored` and the synced mirror no longer names: the set removed it. Such an eval
    is never in `baseline_cells` (:func:`baseline_cells`), so the backend cannot report it, and the writer would refuse the
    whole result as partial. An eval with a cell is never inferred: the backend reports that removal itself."""
    if not pin_arg:
        return []
    import verdicts_file  # noqa: PLC0415 - the one reader of a conversation's verdicts

    try:
        evals = verdicts_file.read(Path(item_dir)).get('evals') or {}
    except verdicts_file.VerdictsError:
        return []
    listed = {str(n) for n in result.get('cells') or {}} | {str(n) for n in result.get('not_scored') or {}}
    reported = {str(c.get('policy_name')) for c in result.get('changed') or () if isinstance(c, dict)}
    candidates = sorted(n for n, e in evals.items() if isinstance(e, dict) and e.get('cell') is None and n not in listed | reported)
    if not candidates:
        return []
    names = _mirror_names(pin_arg)
    if names is None:
        return []
    return [{'policy_name': name, 'old': None, 'new': None} for name in candidates if name not in names]


def extract_baseline(result: dict[str, Any]) -> None:
    """Refuse a result scored without `baseline_cells` (score_run echoes the map it diffed against)."""
    echoed = result.get('baseline_cells')
    if not isinstance(echoed, dict) or not echoed:
        raise Refused(
            _EXIT_MISSING_FIELD,
            'the score_run result echoes no `baseline_cells`: it was scored without the item\'s current cells, so it carried '
            'nothing forward and its `changed` diffs against nothing. Re-score with baseline_cells = the item\'s current '
            'cells (`rescore_run.py` passes them).',
        )


def _evidence_rows(run_dir: Path) -> dict[tuple[Any, Any, Any], dict[str, Any]]:
    """``{(item, policy, turn_idx): row}`` — the run's FALSE RED rows exactly as they were uploaded (one per unit)."""
    rows, _notes = red_cell_audit.backend_rows(red_cell_audit.rows(run_dir), run_dir)
    return {(row['item'], row['policy'], row['turn_idx']): row for row in rows if row.get('verdict') == 'FALSE_RED'}


def audited_corrections(result: dict[str, Any], run_dir: Path) -> list[dict[str, Any]]:
    """`audit_corrections` — each unit the backend set to the run audit's FALSE RED ruling — with that row's evidence
    (``verdict``, ``quote``, ``quote_turn``, ``quote_file``, ``reason``), so the history keeps why the verdict moved.

    The backend applies the audit it was uploaded and returns only the ruling's cause; the quote and reason live in the
    run's own audit file. A correction the audit holds no quote for is refused (exit 3).
    """
    corrections = [c for c in result.get('audit_corrections') or () if isinstance(c, dict)]
    if not corrections:
        return []
    try:
        rows = _evidence_rows(run_dir)
    except (red_cell_audit.AuditMissing, ValueError) as exc:
        raise Refused(_EXIT_MISSING_FIELD, f'the result carries {len(corrections)} backend-applied audit correction(s), and the run\'s '
                                           f'audit cannot be read for their quote and reason ({exc}).') from exc
    out = []
    for correction in corrections:
        unit = (correction.get('item'), correction.get('policy'), correction.get('turn_idx'))
        row = rows.get(unit) or rows.get((*unit[:2], None))
        if row is None or not str(row.get('quote') or '').strip():
            raise Refused(_EXIT_MISSING_FIELD, f'`audit_corrections` names {unit}, and the run\'s audit holds no FALSE RED quote for it: '
                                               're-upload the audit this run was scored with, then re-score.')
        reason = ' '.join(str(row.get('reason') or row.get('clause') or '').split())
        out.append({**correction, 'verdict': 'FALSE_RED', **{k: row[k] for k in _AUDIT_EVIDENCE if row.get(k) is not None}, 'reason': reason})
    return out


def rescore_payload(result: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """The result the writer is handed: every field checked, cells at their effective verdict, the backend's audit
    corrections with their evidence, and no internal overrule record."""
    cells = effective_cells(extract_cells(result))
    extract_not_scored(result)
    extract_not_scored_kinds(result)
    extract_changed(result)
    extract_baseline(result)
    payload = {k: v for k, v in result.items() if k != _GATE_AUTHORITY}
    payload['cells'] = cells
    if result.get('audit_corrections'):
        payload['audit_corrections'] = audited_corrections(result, run_dir)
    return payload


def pin_staleness(pin_arg: str, result: dict[str, Any], set_corpus_ids: list[str] | None = None) -> str | None:
    """The reason the eval lane's pinned frame has moved, or None while it holds.

    `--pin` takes the WORKDIR (the usual call) or the pin file itself. The result's own
    `gt_revision` is the current grading revision — this result IS the latest scoring, so no
    second source is needed. An unresolvable pin returns None rather than a reason: a missing
    frame is the caller's problem to notice, and inventing a staleness reason for it would
    report the wrong thing.

    `set_corpus_ids` is the set's own `detail.corpus_ids` (`--set-corpus-ids`), and it is what
    makes `corpus-moved` reachable from here at all: without it the axis can only compare the
    workdir marker against the pin, so a set left standing on a corpus the workdir has since
    replaced — the shape the reason was added for — is invisible, while this file's own header
    lists `corpus-moved` among the reasons it warns on. Absent, the axis reports what it can and
    `corpus-unknown` says when it could not look.
    """
    path = Path(pin_arg)
    workdir = path.parent if path.is_file() else path
    try:
        import lane_pin  # noqa: PLC0415 - sibling script, imported for its comparison
    except Exception:
        return None
    pin = lane_pin.read_pin(workdir)
    if pin is None:
        return None
    revision = result.get('gt_revision')
    try:
        return lane_pin.staleness(pin, workdir, revision if isinstance(revision, int) else None,
                                  set_corpus_ids=set_corpus_ids)
    except OSError as exc:
        # A frame that cannot be checked is not a fresh one: the harness axis is the refusing one.
        raise Refused(
            _EXIT_PIN_STALE,
            f'the eval lane\'s pin cannot be checked — the harness fingerprint could not be computed ({exc}). '
            'Fix the harness file it names and re-check; nothing is applied against an unverifiable frame.',
        ) from exc


def resolve_item_dir(args: argparse.Namespace) -> Path:
    """`--out-dir`/`--scenario`: the item's directory (the writer refuses one that holds no verdicts)."""
    if not (args.out_dir and args.scenario):
        raise Refused(_EXIT_USAGE, 'give --out-dir <run dir> and --scenario <the item\'s directory under it>.')
    chosen = Path(args.out_dir) / args.scenario
    if why := _confine.refusal(chosen, allow_outside=getattr(args, 'allow_outside_workdir', False)):
        raise Refused(_EXIT_USAGE, why)
    return chosen


def main(argv: list[str] | None = None) -> int:
    return _client_handshake.refusal_main(lambda: _main(argv))


def _main(argv: list[str] | None) -> int:
    parser = argparse.ArgumentParser(prog='apply_rescore.py', formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description=__doc__.split('\n\n', 1)[0])
    parser.add_argument('result', help="path to the score_run result JSON, or '-' to read it from stdin")
    parser.add_argument('--out-dir', default=None, help='the run dir ($OUT)')
    parser.add_argument('--scenario', default=None, help="the item's directory under the run (a trial's: trial_<k>/<scenario>)")
    parser.add_argument('--pin', default=None, help='the workdir (or pin file) holding .eval_lane_pin.json — REFUSES (exit 5) when the '
                                                    'pinned frame moved in a way that voids the behaviour record, warns otherwise')
    parser.add_argument('--set-corpus-ids', default=None, help="comma-joined corpus ids the SCENARIO SET stands on "
                        '(get_artifact_meta(<set>).detail.corpus_ids) — the same argument `lane_pin.py check` takes')
    parser.add_argument('--art', default=None, help="the workdir's artifacts/ directory ($ART), whose .gate_overrules.jsonl records the "
                                                    "result's applied overrules; defaults to <--pin workdir>/artifacts")
    _confine.add_flag(parser)
    args = parser.parse_args(argv)

    try:
        # First, so a path the confinement refuses is refused before any input is read.
        item_dir = resolve_item_dir(args)
        result = _load_json(args.result)
        if not isinstance(result, dict):
            raise Refused(_EXIT_USAGE, f'the score_run result must be a JSON object, got {type(result).__name__}.')
        # BEFORE the field checks: a replay against a world that is gone is not made applicable by being complete.
        set_corpus_ids = [c.strip() for c in (args.set_corpus_ids or '').split(',') if c.strip()] or None
        if set_corpus_ids and not args.pin:
            print('apply_rescore: --set-corpus-ids was given without --pin, so the corpus coordinate was NOT '
                  'compared — the ids are read through the pinned frame. Pass --pin "$WORKDIR" as well.', file=sys.stderr)
        stale = pin_staleness(args.pin, result, set_corpus_ids) if args.pin else None
        if stale is not None and _pin_refuses(stale):
            raise Refused(_EXIT_PIN_STALE, f'the eval lane\'s pin is STALE ({stale}), which voids the BEHAVIOUR these verdicts describe. '
                                           'Re-run, re-upload and re-pin (the rejoin does all three), then re-score.')
        if stale:
            print(f'apply_rescore: WARNING — the eval lane\'s pin is STALE ({stale}). The saved behaviour still stands; the '
                  'judgement of it has moved. Weigh that in the round and re-pin at the next upload.', file=sys.stderr)
        payload = rescore_payload(result, Path(args.out_dir))
        removed = removed_without_cell(item_dir, result, args.pin)
        payload['changed'] = [*payload['changed'], *removed]
        applied = applied_overrules(result)
        if applied:
            art = _ledger_art(args)
            if art is None:
                raise Refused(_EXIT_USAGE, f'the result carries {len(applied)} applied overrule(s), recorded only in the plugin ledger: '
                                           'pass --art "$ART" (or --pin "$WORKDIR"). Nothing was applied.')
            record_applied_overrules(art, applied)
        try:
            code, message = verdict_events.send('apply-rescore', item_dir, payload)
        except verdict_events.WriterUnavailable as exc:
            raise Refused(_EXIT_WRITER, str(exc)) from exc
        if code != 0:
            raise Refused(_EXIT_WRITER, f'the verdicts writer refused the result (exit {code}): {message}')
    except Refused as exc:
        print(f'apply_rescore: {exc}', file=sys.stderr)
        return exc.code
    print(f'applied to {item_dir} — {len(payload["cells"])} cell(s), {len(payload["not_scored"])} not_scored, '
          f'{len(payload["changed"]) - len(removed)} changed, {len(removed)} removed from the set without a recorded cell, '
          f'{len(payload.get("audit_corrections") or ())} audit correction(s)')
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
