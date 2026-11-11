#!/usr/bin/env python3
"""end_session_outcome.py — read an `end_edit_session` result WITHOUT losing the refusal.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/end_session_outcome.py" <result.json|-> [--scope-rows <rows.json | run dir>]…
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/end_session_outcome.py" <revert_edited_policies result.json|->

WHY THIS EXISTS. A REFUSED finalize returns `reason` as the LAST JSON field, after a
nine-name list and five nulls, with `promoted_yaml: null` and `policies_url: null` — and it
keeps the session OPEN by design. Truncate that result anywhere (a transcript cap, a
`head -c 700`) and every field that survives looks like a clean close, while the one field
saying nothing was promoted is gone. Two live sessions were read that way, reported as
"clean-closed", and left holding their eval set until the idle timeout.

So the DISPOSITION is printed first, always, and never truncated:
    outcome=<committed|refused|closed_unchanged|closed_failed|not_found|promoted|running>
    session_still_open=<true|false>          (only when the field is present)
    reason=<the WHOLE reason, however long>
    warnings=<one per line, verbatim>
    gate_findings=<n> (<k> holding)          (only when the result carries `gate_authority`)
      finding: <gate_id> <authority>[ holding| released] <fingerprint> — <remedy>
    overrules=<n>
      overrule: <gate_id> <fingerprint> → <outcome>
    guard=<status> run=<id> dropped_verdicts=<n> lost_reds=<n> silenced_true_reds=<n> unevaluated=<n>
      <kind>: <policy> <item> turn=<t> now=<now> audit=<ruling|none>   (only when `guard` is an object)
    decision: revert=<policies|none> intended=<n> named_risks=<n>      (`guard_decide.py`, on a commit)
      revert_edited_policies <the tool's arguments as JSON>
then the revision trail —
    eval_revision=<the set's revision after this commit>
    policies_changed=<n>: <policy> (<modified|added|removed>), …   (every policy whose fingerprint moved)
then the routine facts (turn_count / final_policy_names / promoted_yaml SIZE). The promoted
YAML BODY is never printed: it is ~30-65 KB, it belongs on disk via `policies_url`, and
pushing it through a reader's context is what the size cap exists to prevent.

THE REVISION TRAIL. Every commit's loop-log line names the revision it committed and every policy it moved
(`policies_changed`, from the backend's fingerprints — never the editor's own account), and a
`revert_edited_policies` result read through this script logs its own line the same way: `reverted=` /
`skipped=` / `eval_revision=` / `policies_changed=`, exit 0 when it reverted something, 3 when it reverted nothing.
So the revision every later re-score is stamped with is one the log names.

OLDER BACKENDS. `outcome` is a typed field a backend may not have. Absent, the disposition
is derived the way the skills always derived it: a truthy `reason` means REFUSED, anything
else means promoted. A backend that HAS the field always wins — the derivation cannot see
the difference between a refusal and a `closed_failed`.

EXIT CODES (callers dispatch on the exit code, as they do on `plurai stash-doc`)
  0  the edits landed (`committed`, or an older backend with no reason)
  2  usage error, unreadable input, or a body that is not JSON
  3  anything else — refused, closed_unchanged, closed_failed, not_found
  4  `running` — the finalize is still running: `outcome=running` and `finalize_id=` are printed, nothing is logged
     or decided. Poll the id, then call end_edit_session once more. Never a commit and never a failure.

Stdlib-only, like every other plugin-shipped runtime script.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guard_decide  # noqa: E402 - sibling script: the plugin's keep/revert decision on the guard report
from _stdio import utf8_child_text  # noqa: E402 - sibling script: UTF-8 children on Windows

#: Dispositions that mean the edits ARE on the backend. Everything else exits 3 — including
#: `closed_unchanged`, which is not a failure but is also not a promote, and a caller that
#: syncs its local mirror on it would overwrite nothing while reporting an edit landed.
_PROMOTED = frozenset({'committed', 'promoted'})

#: Printed when the backend carries no `outcome` field at all, so a reader can tell a DERIVED
#: disposition from a typed one — the derivation cannot distinguish refused from closed_failed.
_DERIVED_NOTE = '(derived from `reason`: this backend does not report a typed `outcome`)'


def _usage(msg: str) -> int:
    print(f'usage: end_session_outcome.py <result.json|-> [--scope-rows <rows.json | run dir>]…\n{msg}', file=sys.stderr)
    return 2


#: `outcome` → the loop log's terminal CLASS. A refusal promoted nothing, which is the class
#: the log had no way to record: an unlogged refusal is indistinguishable downstream from a
#: session that never ran, and the next round re-derives it from scratch.
_OUTCOME_CLASS = {
    'committed': 'done',
    'promoted': 'done',
    'refused': 'refused-publish',
    'closed_failed': 'failed',
    'closed_unchanged': 'done',
    'not_found': 'failed',
}


def _changed(body: dict) -> str:
    """`policies_changed` as one line: `<n>: <policy> (<change>), …` — `unreported` from a backend that sends none."""
    changed = body.get('policies_changed')
    if not isinstance(changed, list):
        return 'unreported'
    named = [f'{c.get("policy")} ({c.get("change")})' for c in changed if isinstance(c, dict)]
    return f'{len(named)}' + (f': {", ".join(named)}' if named else '')


def _trail(body: dict) -> str:
    return f'eval revision {body.get("eval_revision") or "unknown"}; policies changed {_changed(body)}'


def _log_outcome(outcome: str, reason: object, trail: str = '') -> None:
    """Record the session's terminal outcome in the loop log, from the reader that OBSERVED it.

    Here rather than in the skill for the reason `hooks/pending_register.py` gives: a note the
    model is asked to write is an assertion it can skip, and a refused publish left no trace at
    all across a whole build. This reader runs on EVERY `end_edit_session` — the skill cannot
    branch on the outcome without it — so the record rides the work.

    Best-effort: never changes the exit code, never prints, and `append_loop_log.py` refuses
    rather than creating an artifacts tree in an arbitrary repo.
    """
    import subprocess  # noqa: PLC0415 - only on the logging path, never on the read path

    detail = ' — '.join(x for x in (str(reason or '').strip()[:200], trail) if x)
    try:
        subprocess.run(
            [
                sys.executable, str(Path(__file__).with_name('append_loop_log.py')),
                '--kind', 'job', '--outcome', _OUTCOME_CLASS.get(outcome, 'failed'),
                '--finding', f'edit session ended: {outcome}',
                *(['--note', detail] if detail else []),
            ],
            capture_output=True, timeout=15, check=False,
        )
    except Exception:  # noqa: BLE001 - bookkeeping must never break the reader
        pass


def _guard_lines(body: dict, scope_rows: list[dict]) -> list[str]:
    """The eval regression guard's advisory report and the plugin's decision on it (`guard_decide.py`). The guard never
    refuses: what it moved shipped, and the decision says which policies to revert. Internal, never customer text."""
    guard = body.get('guard')
    if not isinstance(guard, dict):
        return []
    lists = (('silenced_true_reds', 'silenced_true_red'), *guard_decide.KEPT_LISTS)
    counts = ' '.join(f'{key}={len(guard.get(key) or [])}' for key, _ in lists)
    lines = [f'guard={guard.get("status", "")} run={guard.get("run_upload_id") or "none"} {counts}']
    for key, kind in lists:
        for i in guard.get(key) or []:
            if isinstance(i, dict):
                lines.append(f'  {kind}: {i.get("policy")} {i.get("item")} turn={i.get("turn")} now={i.get("now")} audit={i.get("audit_ruling") or "none"}')
    if body.get('outcome') in _PROMOTED:
        d = guard_decide.decide(body, scope_rows)
        lines.append(f'decision: revert={",".join(d["revert"]) or "none"} intended={len(d["intended"])} named_risks={len(d["named_risks"])}')
        if d['revert_call']:
            lines.append(f'  revert_edited_policies {json.dumps(d["revert_call"])}')
    return lines


def _record_guard(body: dict, scope_sources: list[str]) -> None:
    """Record a committed session's guard decision in `$ART/eval_loop.json` (`eval_rounds.py guard`), from the reader
    that observed it. Best-effort, like :func:`_log_outcome`: never changes the exit code, never prints."""
    import subprocess  # noqa: PLC0415 - only on the recording path

    try:
        subprocess.run([sys.executable, str(Path(__file__).with_name('eval_rounds.py')), 'guard', '--result', '-',
                        *(arg for src in scope_sources for arg in ('--scope-rows', src))],
                       input=json.dumps(body), capture_output=True, text=True, **utf8_child_text(), timeout=15, check=False)
    except Exception:  # noqa: BLE001 - bookkeeping must never break the reader
        pass


def _released_fingerprint(answer: dict) -> object:
    """The finding an overrule answer lifted: its `finding.fingerprint`, else the overrule's own."""
    finding = answer.get('finding') if isinstance(answer.get('finding'), dict) else {}
    sent = answer.get('overrule') if isinstance(answer.get('overrule'), dict) else {}
    return finding.get('fingerprint') or sent.get('fingerprint')


def _gate_authority_lines(block: object) -> list[str]:
    """The result's `gate_authority` block, whole: each finding in `scripts/gate_ledger.py`'s `finding:`
    form plus the holding count, then what each overrule did. A COMMITTED result can carry advisory
    findings too, so they print on every outcome. Internal: the plugin decides on them per
    `docs/gate-findings.md`; none of this is customer text."""
    if not isinstance(block, dict):
        return []
    findings = [f for f in block.get('findings') or [] if isinstance(f, dict)]
    overrules = [o for o in block.get('overrules') or [] if isinstance(o, dict)]
    # `holds` is the CATALOGUE flag, true on a finding an applied overrule of this call released too: what
    # still holds is a holding finding no applied overrule names.
    released = {_released_fingerprint(o) for o in overrules if o.get('outcome') == 'applied'}
    holding = sum(1 for f in findings if f.get('holds') and f.get('fingerprint') not in released)
    lines = [f'gate_findings={len(findings)} ({holding} holding)']
    for f in findings:
        mark = (' holding' if f.get('fingerprint') not in released else ' released') if f.get('holds') else ''
        lines.append(f'  finding: {f.get("gate_id", "")} {f.get("authority", "")}{mark} {f.get("fingerprint", "")} — {f.get("remedy", "")}')
    lines.append(f'overrules={len(overrules)}')
    for o in overrules:
        sent = o.get('overrule') if isinstance(o.get('overrule'), dict) else {}
        lines.append(f'  overrule: {sent.get("gate_id", "")} {sent.get("fingerprint", "")} → {o.get("outcome", "")}')
    return lines


def _is_revert(body: dict) -> bool:
    """A `revert_edited_policies` result: `reverted` / `skipped` and no `outcome` (an end result always has one)."""
    return 'outcome' not in body and 'reverted' in body and 'eval_set_id' in body


def _revert(body: dict) -> int:
    """Print a revert result and log it: the revert is a commit of its own, at its own revision."""
    reverted = [str(p) for p in body.get('reverted') or []]
    skipped = [f'{s.get("policy")} ({s.get("reason")})' for s in body.get('skipped') or [] if isinstance(s, dict)]
    print('\n'.join([
        f'reverted={", ".join(reverted) or "none"}',
        f'skipped={"; ".join(skipped) or "none"}',
        f'eval_revision={body.get("eval_revision") or ""}',
        f'policies_changed={_changed(body)}',
    ]))
    import subprocess  # noqa: PLC0415 - only on the logging path

    try:
        subprocess.run(
            [sys.executable, str(Path(__file__).with_name('append_loop_log.py')), '--kind', 'job', '--outcome', 'done' if reverted else 'failed',
             '--finding', f'policies reverted (session {body.get("session_id")}): {", ".join(reverted) or "none"}', '--note', _trail(body)],
            capture_output=True, timeout=15, check=False,
        )
    except Exception:  # noqa: BLE001 - bookkeeping must never break the reader
        pass
    return 0 if reverted else 3


def main(argv: list[str]) -> int:
    args, scope_sources, rest = [], [], iter(argv[1:])
    for arg in rest:
        if arg == '--scope-rows':
            scope_sources.append(next(rest, ''))
        else:
            args.append(arg)
    if len(args) != 1 or '' in scope_sources:
        return _usage(f'expected exactly one result argument, got {len(args)}.')
    src = args[0]
    try:
        scope_rows = guard_decide.load_scope_rows(scope_sources)
    except (OSError, ValueError) as exc:
        return _usage(f'could not read --scope-rows: {exc}')
    try:
        raw = sys.stdin.read() if src == '-' else Path(src).read_text(encoding='utf-8')
    except OSError as exc:
        return _usage(f'could not read {src!r}: {exc}')
    try:
        body = json.loads(raw)
    except ValueError as exc:
        return _usage(f'{src!r} is not JSON: {exc}. Write the MCP result to a file with json.dump and pass that file.')
    if not isinstance(body, dict):
        return _usage(f'{src!r} parsed to a {type(body).__name__}, not an end_edit_session result object.')
    if _is_revert(body):
        return _revert(body)

    typed = body.get('outcome')
    reason = body.get('reason')
    if typed == 'running':
        # NOT an outcome of the session: the finalize is still running server-side. Nothing is logged or decided —
        # the loop log would file it as a failure, and the guard decision reads a report that does not exist yet.
        print('\n'.join([
            'outcome=running',
            f'finalize_id={body.get("finalize_id") or ""}',
            f'reason={reason if reason is not None else ""}',
        ]))
        return 4
    if isinstance(typed, str) and typed:
        outcome = typed
        derived = False
    else:
        # The pre-`outcome` shape: a truthy reason was the ONLY refusal signal the skills had.
        outcome = 'refused' if reason else 'promoted'
        derived = True

    lines = [f'outcome={outcome}' + (f'  {_DERIVED_NOTE}' if derived else '')]
    if 'session_still_open' in body:
        lines.append(f'session_still_open={"true" if body.get("session_still_open") else "false"}')
    elif outcome == 'refused':
        # An older backend refuses WITHOUT saying so; the fact is still true and the caller
        # still has to act on it, so state it rather than letting the absence read as "closed".
        lines.append('session_still_open=true  (assumed: a refusal keeps the session open; this backend does not report the field)')
    # WHOLE, never elided: this is the field truncation deleted, and shortening it here
    # would reintroduce the defect inside the tool written to prevent it.
    lines.append(f'reason={reason if reason is not None else ""}')
    warnings = body.get('warnings') or []
    if isinstance(warnings, list) and warnings:
        lines.append('warnings=')
        lines.extend(f'  - {w}' for w in warnings)
    else:
        lines.append('warnings=')
    lines.extend(_gate_authority_lines(body.get('gate_authority')))
    lines.extend(_guard_lines(body, scope_rows))
    if outcome in _PROMOTED:
        lines.append(f'eval_revision={body.get("eval_revision") or ""}')
        lines.append(f'policies_changed={_changed(body)}')

    lines.append(f'turn_count={body.get("turn_count", "")}')
    names = body.get('final_policy_names') or []
    if isinstance(names, list):
        lines.append(f'final_policy_names={len(names)}: {", ".join(str(n) for n in names)}')
    promoted = body.get('promoted_yaml')
    if isinstance(promoted, str):
        # SIZE only. The body belongs on disk via policies_url; printing it here would push
        # 30-65 KB through the context the inline cap exists to protect.
        lines.append(f'promoted_yaml: {len(promoted.encode("utf-8"))} bytes (body withheld — fetch it from policies_url)')
    else:
        lines.append('promoted_yaml: absent')
        if body.get('promoted_yaml_omitted_reason'):
            lines.append(f'promoted_yaml_omitted_reason={body["promoted_yaml_omitted_reason"]}')
    if body.get('policies_url'):
        lines.append(f'policies_url={body["policies_url"]}')

    print('\n'.join(lines))
    _log_outcome(outcome, reason, _trail(body) if outcome in _PROMOTED else '')
    if isinstance(body.get('guard'), dict) and outcome in _PROMOTED:
        _record_guard(body, scope_sources)
    return 0 if outcome in _PROMOTED else 3


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main(sys.argv))
