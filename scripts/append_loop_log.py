#!/usr/bin/env python3
"""Append one typed entry to the quality loop's `loop_log.md`.

The loop log has two readers and both are load-bearing:

* **The next round.** Every critic pass arrives with no memory of the last one, so a
  re-reported WRONG FACT and an UNFIXED DEFECT look identical — both are "the critic said
  it again". The written adjudication is what tells them apart.
* **The adoption precondition.** `/simulation-generate-suite` Step 3b may only pass
  `adopt_from_scenario_set_id` when no SCENARIO-scope input edit happened after the
  qualification batch. That is a fact about the round, not a memory: an `input-edit` entry
  here is what decides it, and an unlogged edit makes the backend refuse the adoption on its
  own. `--adoption-test` DECIDES it from the parsed entries — see `adoption_verdict`.

Written as a script rather than left as an instruction because the instruction did not
survive contact with a real loop — two full builds produced no log at all. Stdlib only (the
backend `--policy` check reads the eval set through `build_run_json`, which needs pyyaml).

Usage:
    append_loop_log.py --kind input-edit --scope scenario \
        --finding "restock rule contradicted the data" \
        --sections "§5 Closed sets" --regens "scenarios+evals"
    append_loop_log.py --adoption-test --donor-set a9fa01e07dff4920a4f5f359baf143b0
    append_loop_log.py --kind adjudication --finding "critic said declared_closed_set=true" \
        --read "source_registry.yaml:declared_closed_set" --actual "false"
    append_loop_log.py --kind job --outcome refused-publish --finding "eval extend <id>"
    append_loop_log.py --kind regeneration --flag-class content \
        --defect unsatisfiable_inputs --finding "slot 2 targets a null catalog field"
    append_loop_log.py --kind eval-round --lane eval --finding "batch 2 rescored, 3 cells flipped"
    append_loop_log.py --kind incomplete-triage --cause latency \
        --finding "s04 landed incomplete at turn 8 of 14 — the agent is sound, just slow"

Three rules the parser ENFORCES and this block used to state nowhere the caller reads:

* `--outcome` belongs to `--kind job` and to nothing else — it is REQUIRED there (a terminal
  outcome is a class: `done` | `failed` | `refused-publish`) and REFUSED everywhere else. A
  novel outcome with no kind of its own was getting written as nothing at all; downstream, an
  unlogged refusal is indistinguishable from a job that never ran.
* `--cause` belongs to `--kind incomplete-triage` the same way, and is REQUIRED there:
  `agent` | `simulation` | `latency`. A scenario the time budget cut has exactly one of those
  behind it, they route to three different owners, and a triage that names none is a note.
* `--verdict` belongs to `--kind warning-triage` the same way, and is REQUIRED there:
  `fixed` | `regenerated` | `flag-wrong` | `accepted` | `ruled`. A demoted warning obliges nobody, so the
  record of what you DECIDED is the entire artifact — "I read it" is not a verdict.
* `--defect` takes a CITATION, not prose: a backend flag exactly as it is emitted
  (`impossible_success_criteria`, `unsatisfiable_inputs` — lower snake_case, at least one
  underscore) or a critic finding as `finding:<id>`. `coverage`, `anything` and
  `the batch was short` are all rejected, which is the point: free text there would let any
  re-draw wear a defect class. Required on `--flag-class content|infra`.
* `--scope` belongs to `--kind input-edit` and is REQUIRED there:
  `scenario` | `eval` | `both`. It names which generation lane the edited FILE feeds, and it
  is the ONE field the adoption test branches on — `--lane` and `--regens` cannot stand in for
  it (both are optional, `--regens` is free prose, and neither says which file moved).
* `--surface` belongs to `--kind blocking-unresolved` and is REQUIRED there:
  `eval` | `scenario` | `harness` | `env` | `backend` | `plugin` | `sdk` (`env`: the mock or the world it serves).
* `--policy <check>` (repeatable, once per check) belongs to `--surface eval`, `backend`, `plugin` and `sdk`:
  the check(s) the block sits on. REQUIRED on eval and on backend (or `--affects-no-check` there). An eval
  entry caps the rung only while the final run still carries a wrong or unchecked verdict on that eval
  (`lane_pin.py anchor` re-checks it), and an entry that names no eval cannot be re-checked. A backend
  entry on a check only the backend judges, which the final run's audit proves wrong, caps the rung at
  `qualified` as a named risk (`release_verdict.blocking_split`); any other stays the floor. A plugin /
  SDK entry whose named checks the final run re-checked clean caps it at `qualified` as an internal
  named risk; one that names no check stays the floor, so the writer warns.
  A backend `--policy` that `policies_config.yaml` does not type as judged by the backend alone is
  REFUSED (`release_verdict._server_judged`); an unreadable eval set writes it with a warning.
* `--affects-no-check` belongs to `--surface backend` alone, in place of `--policy`: a crashed job that
  leaves no check unmeasured. Written as `Affects no check: yes`; the entry stays the floor.

`eval-edit` is accepted as a spelling of `eval-round` — same entry, same reader.

(The regeneration entry's STAGE is derived from disk, never passed — see `_derive_stage`.)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

HEADER = '# Loop log\n\n_Loop-internal. Never customer-facing._\n'

# `re-steer` is read by Step 3b the same way `input-edit` is: the deliverable must be
# generated with the steering the VALIDATED sample was, and "carry the re-steered values"
# was a memory instruction that a batch-relative string then survived into the final run.
#
# `critic` is read by Step 3b as a PRECONDITION: the final batch may only be generated once a
# critic has judged the set it is about to be built from. Scoring is not judging, so the entry
# must name the set (`--note "set <scenario_set_id>"`) — an entry for a superseded set proves
# nothing about the current one.
#
# `job` is the terminal outcome of a backend job, and `--outcome` is a CLASS rather than free
# text on purpose: a novel outcome with no kind of its own gets written as nothing at all (a
# refused publish left no trace in a whole build), and downstream an unlogged refusal is
# indistinguishable from a job that never ran.
#
# `regeneration` is written BEFORE a batch is re-drawn, and it is the flow's only mechanical
# check on why. Typing the reason is what lets the log attach the finding a coverage re-draw at
# qualification carries; the decision stays the plugin's.
#
# `scenario-round`, `harness-fix`, `rejoin` and `parked` name the events a round has when the two
# lanes overlap. `parked` is the load-bearing one: a finding whose slot is about to be regenerated
# is neither fixed nor outstanding, and without a record of that the next critic reads it as
# unfixed and the round re-buys it.
#
# `incomplete-triage` is the record for a scenario that ran out of the per-scenario TIME budget
# and landed `incomplete`. Three causes, and the round OWES exactly one of them (`--cause`, which
# this script requires): the AGENT burned the clock, the SIMULATION burned it, or the agent is
# sound and genuinely slow. They route to three different places and only the first two are
# anyone's defect, so an untriaged incomplete is the shape that gets re-litigated every round —
# each critic sees a short dialog, each round pays for the same look, and none of them can see
# that the last one already decided it.
#
# `warning-triage` is the same shape one step earlier: a generation warning that carries a
# verdict-shaped decision and, unlike a flag that blocks, obliges nobody. `audit_flicker_demoted`
# says it in its own text — "read it and decide whether the scenario is still fit" — so a scenario
# can ship carrying one that was raised twice, and pay a regeneration when a later critic pass
# re-derives it. The verdict is what the log can refuse; "the flag is
# wrong, keep the scenario" is a complete and cheap one.
#
# `claim-refuted` is the arbitration record, and it is a DIFFERENT event from `adjudication`.
# An adjudication settles a critic's wrong FACT by reading the artifact. This settles a
# disagreement between two passes about a FIX: the fixes applied since the last critic ride the
# next critic's prompt as claims, and a claim the new pass reverses is disposed of here rather
# than entering the round as a fresh finding. Without the record, a reversal is indistinguishable
# from a new defect — which is how the same edit was applied at 20:52 and reverted at 21:14 with
# 32 minutes and two correct critics between them.
#
# `red-cell-audit` is the scoped audit of the red cells, a different event from a `critic` pass,
# and `blocking-unresolved` is one landing block left unrepaired (`--surface` required), so a
# reader counts both instead of reading them out of prose.
#
# `regression` records that a confirmed agent issue was written to a folder under
# `plurai_sim/regressions/`. The folder is its own status and no ledger tracks it, which is what
# makes the log entry worth writing: the folder says an issue is covered and says nothing about
# which run, which scenario or which turn it came from, and the next build re-authors everything
# around the pin. The entry is where a later reader learns what was recorded and from where,
# without opening a pin's provenance block.
# `trace-scenario` is the same record for a folder under `plurai_sim/trace_scenarios/`: a
# production conversation cut before a trace failure, written from a trace-analysis brief,
# carried by a final batch, or retired when a regression cut at the seed boundary replays its
# conversation. It is a kind of its own because it carries no blame — a regression says an agent
# issue was confirmed, a trace scenario says only that production showed a failure.
#
# `fix-applied` records that `/simulation-fixes` applied a fix the user chose from `fixes.md`. The plan
# cannot say it: its `Regression:` lines read the same before and after the choice, and the choice
# itself is recorded only on harnesses with an ask tool. The `/simulate` closing reads it to name
# the next command: a fix this run applied is scored next, an unapplied plan is chosen from.
KINDS = (
    'input-edit', 're-steer', 'adjudication', 'critic', 'env-fix', 'eval-round', 'run', 'note',
    'job', 'regeneration', 'incomplete-triage', 'warning-triage',
    'scenario-round', 'harness-fix', 'rejoin', 'parked', 'claim-refuted',
    'red-cell-audit', 'blocking-unresolved', 'regression', 'trace-scenario', 'fix-applied',
)

#: Spellings that MEAN a listed kind. Accepted rather than refused because the cost is
#: asymmetric and this script exists because entries were not being written: a rejected
#: `--kind` exits 2 and the round's event goes unrecorded, while an alias costs one dict
#: lookup. Normalised at parse time, so nothing downstream ever sees the alias — a second
#: spelling reaching `_derive_stage` or the Step-3b grep would be a gate that reads one name
#: and a log that carries two.
ALIASES = {'eval-edit': 'eval-round'}


def _canonical(kind: str) -> str:
    """The listed kind an alias stands for; a listed kind unchanged."""
    return ALIASES.get(kind, kind)


#: Which lane an entry belongs to. Rendered INLINE on the kind line, never as a bullet — see
#: `_entry`, which keeps the NOTE directly below the kind line for the same reason.
LANES = ('eval', 'scenario', 'harness', 'joint')

#: Every terminal state a backend job can reach. Closed: a state that needs a new member is a
#: change to this list, not an entry under `note` that no reader greps for.
JOB_OUTCOMES = ('done', 'failed', 'refused-publish')

#: WHO burned the scenario's time budget. Closed and exhaustive by construction — the clock was
#: spent by the agent, by the suite, or by neither (the work legitimately takes that long) — and
#: each routes somewhere different: `agent` to `$ART/agent_findings.md` (a finding, never a
#: blocker), `simulation` to the suite's own fix list this round, `latency` to nothing at all
#: except the record. `latency` is the one worth typing: it is a legal, non-blocking outcome, and
#: with no name of its own it gets logged as a defect that the next round then tries to fix.
INCOMPLETE_CAUSES = ('agent', 'simulation', 'latency')

#: What a `warning-triage` DECIDED. `fixed` and `regenerated` say the defect was real and paid
#: for; `flag-wrong` and `accepted` say the scenario ships as-is and WHY — a refuted flag versus a
#: real defect judged tolerable are different states, and a later critic re-raising the item needs
#: to know which one it is arguing with. `ruled` says the flag was RIGHT about a scenario-TARGET
#: clause the agent provably cannot observe, and the clause now travels as a `target_rulings` row
#: (`record_target_rulings.py`) — a structural fact, never dismissed as flicker. Read by nothing but
#: a human and the next critic; the value is that the round cannot record "I looked" without
#: recording what it concluded.
WARNING_VERDICTS = ('fixed', 'regenerated', 'flag-wrong', 'accepted', 'ruled')

#: What a regeneration is answering, in the suite flow's own branch vocabulary. `content` and
#: `infra` each name a defect the last draw hit; `coverage` names none — it is a request for
#: MORE, which is what the final batch is for.
FLAG_CLASSES = ('content', 'infra', 'coverage')

#: Which generation lane the edited INPUT FILE feeds — the axis `adoption_verdict` branches on.
#: It is about the FILE, not about what the round chose to re-run: Step 3b's condition (a) names
#: `simulation_prd.md` / `scenario_variables.yaml` / `scenario_state_schema.yaml` / schemas /
#: briefs, and an edit to one of those forfeits scenario adoption whether or not the round paid a
#: regeneration for it. `eval_intent.yaml` is an input and is NOT on that list, so an edit to it
#: is `eval` scope and forfeits nothing.
#:
#: TYPED because the untyped fields could not carry this. `--lane` is optional (a whole box's
#: input-edit entries carried none) and `--regens` is free prose ("none beyond the final count=7
#: batch already planned"), so a parser reading either has to guess — and the guess is a gate.
SCOPES = ('scenario', 'eval', 'both')

#: Which surface an unresolved landing block sits on — what a rebuild has to repair. `env` is the mock and the world
#: it serves: a floor surface of its own (`release_verdict.RECHECKS` has no row for it), never filed as tooling.
SURFACES = ('eval', 'scenario', 'harness', 'env', 'backend', 'plugin', 'sdk')
#: The surfaces `--policy` belongs to: those the final run re-checks (`release_verdict.RECHECKS`).
POLICY_SURFACES = ('eval', 'backend', 'plugin', 'sdk')

#: The scopes that FORFEIT scenario adoption. `both` forfeits because it includes `scenario`.
FORFEITING_SCOPES = ('scenario', 'both')

#: What `--defect` must LOOK like: a backend flag (`impossible_success_criteria`) or an explicit
#: `finding:<id>` escape for a critic finding. Not a curated list — one kept here would go stale
#: against the backend and start refusing real flags — but a SHAPE, which is what separates a typed
#: flag from prose. It proves the citation is a reference, not that the reference resolves;
#: `coverage`, `anything` and `the batch was short` are all rejected, which is the point.
_DEFECT_TOKEN = re.compile(r'^(?:finding:\S+|[a-z][a-z0-9]*(?:_[a-z0-9]+)+)$')

#: A regression folder as `/record-regression` names it. Matched anywhere in the finding text, so
#: the entry stays a sentence rather than becoming a field.
_REGRESSION_FOLDER = re.compile(r'\bregression_[a-z0-9]+(?:_[a-z0-9]+)*\b')

#: A trace scenario folder as `trace_scenarios.py write` names it, matched the same way.
_TRACE_SCENARIO_FOLDER = re.compile(r'\btrace_(?!scenarios\b)[a-z0-9]+(?:_[a-z0-9]+)*\b')

#: One-shot token proving THIS re-draw was logged. `hooks/regen_gate.py` requires it before
#: `generate_scenarios_start` may re-draw, and consumes it — the same bearer-token shape
#: `ask_guard` uses for a sanctioned question. Without it the refusal below is only a refusal to
#: write a line, which is not a gate on anything.
REGEN_TOKEN = '.regen_logged'


def _resolved_workdir() -> Path | None:
    """The agent workdir `hooks/_workdir` resolves, when it EXISTS on disk — else None.

    Both callers below need exactly this, and both need the existence test: that resolver
    returns `<cwd>/plurai_sim` for a directory with no agent in it, so treating a resolution as
    a location would write into an arbitrary repo. `hooks/` is not a package, hence the shim.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
    try:
        from _workdir import resolve_workdir  # noqa: PLC0415 - hooks/ is only on sys.path here
    except Exception:
        return None
    resolved = resolve_workdir()
    return resolved if resolved is not None and resolved.is_dir() else None


def _artifacts_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    art = os.environ.get('ART')
    if art:
        return Path(art)
    workdir = os.environ.get('WORKDIR')
    if workdir:
        return Path(workdir) / 'artifacts'
    # SELF-RESOLVE, like every other artifacts writer. The harness resets the shell between
    # tool calls, so a snippet that forgot to re-source resolve-workdir.sh reaches here with a
    # clean environment — and this script's whole reason for existing is that the loop log was
    # not being written. Refusing over a missing export is the same lost entry by another name.
    resolved = _resolved_workdir()
    if resolved is not None:
        return resolved / 'artifacts'
    print(
        'ERROR: no artifacts directory. Source scripts/resolve-workdir.sh (which exports '
        '$ART), or pass --artifacts-dir explicitly.',
        file=sys.stderr,
    )
    raise SystemExit(2)


def _entry(args: argparse.Namespace, stage: str | None, *, gate_finding: str | None) -> str:
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    lane = f' · lane `{args.lane}`' if args.lane else ''
    # Collapse whitespace: a newline inside --finding would split the kind line in two, and the
    # `-A1` reader below would then return the finding's second line instead of the note.
    finding = ' '.join(str(args.finding).split())
    parts = [f'- **{stamp}** · `{args.kind}`{lane} — {finding}']
    # THE NOTE COMES FIRST, directly below the kind line, whatever else the entry carries.
    #
    # Step 3b's critic precondition greps the kind and reads `-A1` for the set id, so anything
    # rendered between the two makes a gate that passes today count 0 tomorrow — and it fails
    # OPEN, so no test necessarily catches it. `--lane` stays inline for exactly this;
    # hoisting the note generalizes that from "no NEW field may sit above it" to an invariant
    # that holds for every kind and every combination of fields, including ones nobody has
    # written yet. Position carries no meaning here, so this costs nothing.
    if args.note:
        parts.append(f'  - {args.note}')
    # Each remaining field answers a question the next round actually asks; omitted ones
    # simply do not render, so a short entry stays short.
    if args.scope:
        parts.append(f'  - Scope: `{args.scope}`')
    if args.surface:
        parts.append(f'  - Surface: `{args.surface}`')
    if args.policy:
        parts.append('  - Policy: ' + ', '.join(f'`{" ".join(str(p).split())}`' for p in args.policy))
    if getattr(args, 'affects_no_check', False):
        parts.append('  - Affects no check: yes')
    if args.sections:
        parts.append(f'  - Sections touched: {args.sections}')
    if args.read:
        parts.append(f'  - Read: `{args.read}`')
    if args.actual:
        parts.append(f'  - Artifact actually says: {args.actual}')
    if args.regens:
        parts.append(f'  - Regenerations paid: {args.regens}')
    if args.outcome:
        parts.append(f'  - Outcome: `{args.outcome}`')
    if args.cause:
        parts.append(f'  - Cause: `{args.cause}`')
    if args.verdict:
        parts.append(f'  - Verdict: `{args.verdict}`')
    if args.flag_class:
        parts.append(f'  - Flag class: `{args.flag_class}` · stage: `{stage}`')
    if args.defect:
        parts.append(f'  - Repairs: {args.defect}')
    if gate_finding:
        parts.append(f'  - Gate finding: {gate_finding}')
    return '\n'.join(parts) + '\n'


#: One rendered kind line: `- **<stamp>** · `<kind>`[ · lane `<lane>`] — <finding>`. The shape
#: `_entry` writes, read back. Kept beside it so the two move together.
_KIND_LINE = re.compile(
    r'^- \*\*(?P<stamp>[^*]+)\*\* · `(?P<kind>[^`]+)`(?: · lane `(?P<lane>[^`]+)`)? — (?P<finding>.*)$'
)


#: The typed field bullets `_entry` renders, exactly. CLOSED, and read back as a closed set: the
#: free `--note` bullet is rendered ABOVE these and is arbitrary operator prose, so an open
#: `<Capitalized words>:` pattern lets a note invent any field it likes. `parse_entries` also
#: takes the LAST match rather than the first, because the note always renders first — between
#: them, a note reading `Scope: eval` can neither create the field nor shadow the real one.
#: (Reproduced before both guards existed: `--scope scenario --note "Scope: eval"` made the
#: adoption test read the entry as eval-scope and drop a forfeit it had earned.)
_FIELD_KEYS = (
    'Scope', 'Surface', 'Policy', 'Sections touched', 'Read', 'Artifact actually says', 'Regenerations paid',
    'Outcome', 'Cause', 'Verdict', 'Flag class', 'Repairs', 'Gate finding', 'Affects no check',
)

#: One rendered FIELD bullet under a kind line: `  - <Key>: <value>`. `_entry` writes exactly
#: these, so the two move together — a field rendered in some other shape is a field no gate reads.
_FIELD_LINE = re.compile(r'^\s{2,}- (?P<key>' + '|'.join(_FIELD_KEYS) + r'): (?P<value>.*)$')

#: The stamp `_entry` writes, read back.
_STAMP_FORMAT = '%Y-%m-%d %H:%M UTC'


@dataclass
class Entry:
    """One parsed log entry: its kind line plus the typed bullets under it.

    Not frozen: `parse_entries` fills `fields` as it walks the bullets below the kind line.
    """

    stamp: str
    kind: str
    lane: str
    finding: str
    fields: dict[str, str]

    @property
    def when(self) -> datetime | None:
        """The entry's UTC minute, or None when the stamp is not the shape `_entry` writes."""
        try:
            return datetime.strptime(self.stamp.strip(), _STAMP_FORMAT).replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    @property
    def scope(self) -> str:
        """The typed `--scope`, or `''` when the entry predates the flag or omits it."""
        return self.fields.get('Scope', '').strip().strip('`')

    @property
    def surface(self) -> str:
        """The typed `--surface` of a `blocking-unresolved` entry, or `''`."""
        return self.fields.get('Surface', '').strip().strip('`')

    @property
    def policies(self) -> tuple[str, ...]:
        """The typed `--policy` checks of a `--surface eval` / `backend` / `plugin` / `sdk` entry, or `()` (every entry
        written before the flag)."""
        return tuple(re.findall(r'`([^`]+)`', self.fields.get('Policy', '')))

    @property
    def affects_no_check(self) -> bool:
        """Whether a backend entry was typed `--affects-no-check` (a crashed job that leaves no check unmeasured). Never
        beside a `--policy` (the writer refuses both), so a note shaped like the bullet cannot make a named entry read so."""
        return not self.policies and self.fields.get('Affects no check', '').strip() == 'yes'


def parse_entries(path: Path) -> list[Entry]:
    """Every entry in the log, oldest first, with its field bullets attached.

    Unparseable lines are skipped rather than guessed at — an operator's own prose in the log
    is not an entry. A field bullet before any kind line has no entry to belong to and is
    likewise dropped.
    """
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        # A log this cannot read is a log with no entries, which forfeits at every caller. A
        # traceback would exit non-zero too, but it prints no verdict — and the skill hands
        # "any non-zero -> forfeited" to a reader that is looking at stdout for the reason.
        return []
    out: list[Entry] = []
    for line in text.splitlines():
        head = _KIND_LINE.match(line)
        if head:
            out.append(Entry(head.group('stamp'), _canonical(head.group('kind')), head.group('lane') or '', head.group('finding'), {}))
            continue
        field = _FIELD_LINE.match(line)
        if field and out:
            # LAST wins: `_entry` renders the free note FIRST, so the typed bullet always
            # comes after any note that happens to look like one.
            out[-1].fields[field.group('key')] = field.group('value')
    return out


def list_entries(path: Path, kind: str) -> list[str]:
    """`['<stamp> — <finding>']` for every entry of ``kind``, oldest first.

    WHY A READER LIVES HERE. `/simulation-generate-suite` 3b.9 has to check every steering demand
    against the delivered set, and its first step is "list the demands you actually made — the
    RECORD, not your memory of the round". A step that says "read them out of $ART/loop_log.md"
    is still a memory exercise the moment the log is long: both boxes silently dropped a demand.
    One writer, one reader, one line format — a demand that was logged cannot be forgotten here.

    The finding only: the note lines below it are the round's commentary, and the demand is what
    the checklist verifies.
    """
    return [f'{e.stamp} — {e.finding}' for e in parse_entries(path) if e.kind == kind]


def adoption_verdict(entries: list[Entry], donor_set_id: str) -> tuple[bool, str, list[str]]:
    """Step 3b condition (a), decided: `(forfeited, headline, reasons)`.

    CONDITION (a) ONLY. Adoption also needs (b) no critic finding whose fix-locus is a scenario in
    that batch and (c) the gate's `solvable` verdict, and the backend refuses on its own
    content-hash bar besides. A caller that reads "not forfeited" as "adopt" has skipped two
    conditions and a backend gate; the printed verdict says so in as many words.

    THE PREDICATE THIS REPLACES was `grep -c -- '`input-edit`' loop_log.md` — a COUNT, over the
    whole log. On the build that motivated this it returned 4, of which one PRE-dated the
    qualification job and one was `eval` scope; the log then recorded "4 input-edit entries after
    the qualification batch" as its evidence. Two things are wrong with a count and both are here:
    it has no reference time, and it has no scope.

    * **Reference time** — the EARLIEST-STAMPED `job` entry whose finding names ``donor_set_id``.
      Earliest BY STAMP, not by position: a hand-edited or merged log is not guaranteed to be in
      chronological order, and taking a later job (a re-score, a `suite_diagnostics`
      re-read of the same set) would exempt every edit in between — the whole window the loop
      edits in. Same-minute ties count as AFTER: a minute-resolution stamp cannot order two
      events inside it, and the fail-closed reading is the one that cannot license an adoption.
      A donor-naming `job` entry whose stamp does not parse FORFEITS outright rather than being
      skipped over: skipping it promotes a later job to reference, which is the same fail-open by
      another route — and an unparseable input-edit stamp already forfeits, so the two agree.
    * **Scope** — the typed `--scope`. Missing or unrecognised counts as scenario scope and
      FORFEITS, which is the only safe default: an entry written before the flag existed, or by a
      caller that worked around it, says nothing about which file moved.

    No `job` entry naming the donor set ⇒ no reference time ⇒ forfeited, for the same reason.

    KNOWN WINDOW, stated rather than papered over: `poll_job.sh` writes the `job` entry on the
    TERMINAL state, so the stamp is when generation FINISHED. An input edit made while the job
    was still running pre-dates it and does not forfeit here, even though the batch was built
    from the pre-edit inputs. The backend's own input-hash bar refuses that adoption anyway,
    which is why this is condition (a) of three and not the whole gate.
    """
    dated: list[tuple[datetime, Entry]] = []
    for entry in entries:
        if entry.kind != 'job' or donor_set_id not in entry.finding:
            continue
        if entry.when is None:
            return True, (
                f'FORFEITED (fail-closed): a `job` entry naming donor set {donor_set_id} has an unparseable stamp '
                f'({entry.stamp!r}), so the reference time cannot be trusted. Skipping it would promote a LATER job '
                f'to reference and exempt every edit in between.'
            ), []
        dated.append((entry.when, entry))
    if not dated:
        return True, (
            f'FORFEITED (fail-closed): no `job` entry in the log names donor set {donor_set_id}, so there is no '
            f'reference time to date the input edits against.'
        ), []
    reference_when, reference_entry = min(dated, key=lambda pair: pair[0])

    reasons: list[str] = []
    for entry in entries:
        if entry.kind != 'input-edit':
            continue
        when = entry.when
        if when is None:
            reasons.append(f'{entry.stamp} — unparseable stamp, counted as AFTER the batch (fail-closed): {entry.finding[:90]}')
            continue
        if when < reference_when:
            continue
        if entry.scope in SCOPES and entry.scope not in FORFEITING_SCOPES:
            continue
        why = f'scope `{entry.scope}`' if entry.scope in SCOPES else f'NO typed scope ({entry.scope or "absent"}) — counted as `scenario` (fail-closed)'
        reasons.append(f'{entry.stamp} — {why}: {entry.finding[:90]}')

    if reasons:
        return True, f'FORFEITED: {len(reasons)} input-edit entr(ies) are not exempt against the batch at {reference_entry.stamp} (see each reason).', reasons
    return False, f'CONDITION (a) HOLDS: no scenario-scope input edit after the batch at {reference_entry.stamp}.', []


def _validate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Reject an entry that would record less than its kind promises. Exits 2, like every other
    refusal here — a half-typed entry is the blind spot the typed kinds exist to close."""
    if args.kind == 'job' and not args.outcome:
        parser.error(f'--kind job requires --outcome {{{",".join(JOB_OUTCOMES)}}} — every terminal outcome is a class.')
    if args.outcome and args.kind != 'job':
        parser.error('--outcome belongs to --kind job.')
    # Same shape as `--outcome`, for the same reason: a triage that names no cause is not a
    # triage. The three causes route to three different places and only one of them is the
    # agent's, so an entry that records "this one was cut" and stops leaves the next round
    # exactly where this one started.
    if args.kind == 'incomplete-triage' and not args.cause:
        parser.error(
            f'--kind incomplete-triage requires --cause {{{",".join(INCOMPLETE_CAUSES)}}} — '
            'the agent burned the clock, the suite burned it, or the work is genuinely that '
            'slow. Those are three different owners; an untriaged incomplete is re-litigated '
            'every round.'
        )
    if args.cause and args.kind != 'incomplete-triage':
        parser.error('--cause belongs to --kind incomplete-triage.')
    # Same trade as --cause: an entry that records "I read the warning" and stops is the state
    # this kind exists to end, because the next critic cannot tell it from nobody having looked.
    if args.kind == 'warning-triage' and not args.verdict:
        parser.error(
            f'--kind warning-triage requires --verdict {{{",".join(WARNING_VERDICTS)}}} — a demoted '
            'or non-blocking warning obliges nobody, so the RECORD of what you decided is the '
            'whole artifact. "flag-wrong" is a complete verdict; "I read it" is not.'
        )
    if args.verdict and args.kind != 'warning-triage':
        parser.error('--verdict belongs to --kind warning-triage.')
    # THE ADOPTION AXIS, and the reason it is required rather than inferred. Step 3b used to
    # decide the forfeit with `grep -c input-edit` — a count with no reference time and no scope —
    # and the untyped fields cannot repair it: `--lane` is optional and `--regens` is prose. An
    # entry with no scope is a forfeit the round did not necessarily earn, so the flag is the
    # cheaper end of the trade: one word here, or a regenerated deliverable there.
    if args.kind == 'input-edit' and not args.scope:
        parser.error(
            f'--kind input-edit requires --scope {{{",".join(SCOPES)}}} — which generation lane the edited FILE '
            'feeds. `simulation_prd.md` / `scenario_variables.yaml` / `scenario_state_schema.yaml` / schemas / '
            'briefs are `scenario` (they forfeit Step 3b adoption); `eval_intent.yaml` is `eval` (it does not); '
            'an edit spanning both is `both`. Untyped, the adoption test must assume `scenario` and forfeit.'
        )
    if args.scope and args.kind != 'input-edit':
        parser.error('--scope belongs to --kind input-edit.')
    if args.kind == 'blocking-unresolved' and not args.surface:
        parser.error(f'--kind blocking-unresolved requires --surface {{{",".join(SURFACES)}}} — the surface the block sits on.')
    if args.surface and args.kind != 'blocking-unresolved':
        parser.error('--surface belongs to --kind blocking-unresolved.')
    # The eval is what the final-run re-check reads (`release_verdict.blocking_split`); in prose it is unreadable.
    if args.surface == 'eval' and not args.policy:
        parser.error('--surface eval requires --policy <eval> (repeatable) — the eval(s) the block sits on, so the '
                     'final run can re-check it.')
    if args.policy and args.surface not in POLICY_SURFACES:
        parser.error(f'--policy belongs to --surface {", ".join(POLICY_SURFACES)}.')
    if args.affects_no_check and args.surface != 'backend':
        parser.error('--affects-no-check belongs to --surface backend.')
    if args.affects_no_check and args.policy:
        parser.error('--affects-no-check and --policy are exclusive: an entry either names the checks it affects or '
                     'states it affects none.')
    # A backend entry that names no check was a check in prose (RT66 holmesgpt): typed, or typed as affecting none.
    if args.surface == 'backend' and not args.policy and not args.affects_no_check:
        parser.error('--surface backend requires --policy <check> (repeatable) — the check(s) the block sits on — or '
                     '--affects-no-check for a crashed job that leaves no check unmeasured.')
    # The folder is the only record a regression has, and nothing machine-reads it. An entry that
    # says one was recorded without naming which folder therefore points at nothing — a reader
    # holding a directory of slugs cannot match it back to the issue, the run or the cut.
    if args.kind == 'regression' and not _REGRESSION_FOLDER.search(args.finding or ''):
        parser.error(
            '--kind regression requires --finding to name the folder it wrote '
            '(regression_<issue_slug>) — the folder is the whole record and no ledger indexes it, '
            'so an entry that does not name one refers to nothing.'
        )
    if args.kind == 'trace-scenario' and not _TRACE_SCENARIO_FOLDER.search(args.finding or ''):
        parser.error(
            '--kind trace-scenario requires --finding to name the folder it concerns '
            '(trace_<failure_slug>) — the folder is the whole record and no ledger indexes it, '
            'so an entry that does not name one refers to nothing.'
        )
    if args.kind != 'regeneration':
        if args.flag_class or args.defect:
            parser.error('--flag-class / --defect belong to --kind regeneration.')
        return
    if not args.flag_class:
        parser.error(
            f'--kind regeneration requires --flag-class {{{",".join(FLAG_CLASSES)}}} — '
            'a re-draw with no stated class is a re-draw with no stated reason.'
        )
    if args.flag_class != 'coverage':
        if not args.defect:
            parser.error(
                '--kind regeneration --flag-class content|infra requires --defect <the flag or finding '
                'id this re-draw repairs> — the class says a defect was hit; the citation says which.'
            )
        if not _DEFECT_TOKEN.match(args.defect.strip()):
            parser.error(
                f'--defect {args.defect!r} is prose, not a citation. Give the backend flag as it is '
                'emitted (`impossible_success_criteria`, `unsatisfiable_inputs`) or a critic finding '
                'as `finding:<id>`. Free text here would let any re-draw wear a defect class.'
            )


def _stage_finding(args: argparse.Namespace, stage: str) -> str | None:
    """The coverage finding, decided on the DERIVED stage — evidence for the caller, never a refusal.

    The entry is still written and the token still minted: whether the re-draw is worth a batch is
    the plugin's decision, and this states what the log can see for it.
    """
    if args.flag_class == 'coverage' and stage == 'qualification':
        return (
            'coverage re-draw at QUALIFICATION — no critic has judged the current scenario set yet, and a '
            'qualification batch validates the machinery on ~3 scenarios; it is not a coverage sweep and '
            'cannot cover a flow graph by arithmetic. Breadth belongs to the final batch.'
        )
    return None


def _derive_stage(art: Path) -> str:
    """`qualification` until a critic has judged the CURRENT scenario set, `final` after.

    DERIVED, never declared. The stage decides whether a coverage re-draw carries a finding, so an
    actor allowed to name its own stage can name its way straight past it — the asymmetry the
    critic gate already avoids by reading `.scenario_set_id` off disk. This reads the same two
    facts that gate does: Step 3b's precondition IS a `critic` entry naming the current set, so
    the presence of one is exactly what separates "still validating the machinery" from
    "building the deliverable".

    Unreadable either way → `qualification`, the stricter branch: an unreadable set id must
    never be the thing that silences the finding.
    """
    try:
        set_id = (art.parent / '.scenario_set_id').read_text(encoding='utf-8').strip()
        lines = (art / 'loop_log.md').read_text(encoding='utf-8').splitlines()
    except OSError:
        return 'qualification'
    if not set_id:
        return 'qualification'
    for i, line in enumerate(lines):
        # The set id sits on a note line BELOW the kind — the same `-A1` shape Step 3b greps for.
        if '`critic`' in line and any(set_id in note for note in lines[i : i + 3]):
            return 'final'
    return 'qualification'


def _write_regen_token(art: Path) -> None:
    """Drop the one-shot token the PreToolUse gate consumes, in every place it looks.

    Both `$WORKDIR` (= `art.parent` in the normal layout) and the independently resolved
    workdir, because an explicit `--artifacts-dir` can put the two in different places and a
    token the gate cannot find would fail CLOSED on a legitimate re-draw.
    """
    targets = {art.parent}
    try:
        resolved = _resolved_workdir()
    except Exception:  # noqa: BLE001 - the token is best-effort in EVERY direction
        resolved = None
    if resolved is not None:
        targets.add(resolved)
    for target in targets:
        try:
            (target / REGEN_TOKEN).write_text('regeneration logged\n', encoding='utf-8')
        except OSError:
            continue


#: The shortest `--donor-set` the reference lookup will accept. Set ids are 32 hex characters; a
#: prefix this long is still unambiguous in a loop log, and anything shorter is a substring match
#: waiting to land on the wrong job.
_MIN_DONOR_SET_ID = 8

#: `--adoption-test` when condition (a) forfeits. Distinct from argparse's own 2 so the caller can
#: tell "the log says no" from "you called it wrong" — both are non-zero, and both mean do not adopt.
ADOPTION_FORFEITED = 4


def _print_adoption_verdict(path: Path, donor_set_id: str) -> int:
    """Render `adoption_verdict` for a human AND for the loop log, and return the exit code."""
    forfeited, headline, reasons = adoption_verdict(parse_entries(path), donor_set_id)
    print(headline)
    for reason in reasons:
        print(f'  · {reason}')
    # The acceptance criterion is that the LOG says which entries triggered the refusal, so the
    # sentence is emitted ready to paste rather than left for the round to compose from the list
    # above — which is the recall step this whole subcommand exists to remove.
    if forfeited:
        cited = '; '.join(r.split(' — ', 1)[0] for r in reasons) or 'no dated qualification job entry'
        print(f'\n  --note "adoption forfeited by condition (a); triggering input-edit entries: {cited}"')
    print(
        '\nCondition (a) ONLY. Adoption still needs (b) no critic finding whose fix-locus is a scenario in this '
        "batch and (c) the gate's `solvable` verdict — and the backend refuses on its own input-hash bar besides."
    )
    return ADOPTION_FORFEITED if forfeited else 0


def _client_judged(workdir: Path, policies: list[str]) -> list[str] | None:
    """`["`P` (<type>)", …]` for each named check NOT judged by the backend alone, by the read side's own predicate
    (`release_verdict._server_judged`) over the eval set beside the artifacts; None when that could not be decided."""
    here = Path(__file__).resolve().parent
    sys.path[:0] = [p for p in (str(here), str(here.parent / 'skills' / 'simulation-run' / 'scripts')) if p not in sys.path]
    try:
        import release_verdict  # noqa: PLC0415 - the ONE owner of who judges a check
        from build_run_json import eval_set_bodies  # noqa: PLC0415 - the one reader of policies_config.yaml

        bodies = eval_set_bodies(str(workdir))
        if bodies is None:
            return None
        def said(p: str) -> str:
            return 'not in the eval set' if p not in bodies else f'type {bodies[p].get("type")}' if isinstance(bodies[p], dict) else 'no body'

        return [f'`{p}` ({said(p)})' for p in policies if not release_verdict._server_judged(bodies.get(p))]
    except Exception:  # never lose the entry over the check: the read side still keeps an unreadable set live
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--kind', choices=(*KINDS, *ALIASES), help='what happened')
    parser.add_argument('--list', dest='list_kind', choices=(*KINDS, *ALIASES),
                        help='READ mode: print every entry of this kind (one `<stamp> — <finding>` per line) and '
                             'exit. Nothing is written. `--list re-steer` is what 3b.9\'s post-batch checklist runs '
                             'to enumerate the demands it must verify against the delivered set.')
    parser.add_argument('--adoption-test', action='store_true',
                        help="READ mode: decide Step 3b's adoption condition (a) from the log and exit. Nothing is "
                             'written. Needs --donor-set. Exit 0 = condition (a) holds; ANY non-zero = forfeited, so '
                             'do not pass adopt_from_scenario_set_id.')
    parser.add_argument('--donor-set', help='--adoption-test: the qualification scenario_set_id adoption would draw from')
    parser.add_argument('--lane', choices=LANES, help='which lane did it — rendered inline on the kind line')
    parser.add_argument('--scope', choices=SCOPES,
                        help='input-edit ONLY (and required there): which generation lane the edited FILE feeds. The '
                             'axis --adoption-test branches on; untyped entries forfeit adoption by default.')
    parser.add_argument('--surface', choices=SURFACES, help='blocking-unresolved ONLY (and required there): the surface the block sits on')
    parser.add_argument('--policy', action='append',
                        help='--surface eval / backend (required there) or plugin / sdk (repeatable, once per check): the '
                             'check the block sits on')
    parser.add_argument('--affects-no-check', action='store_true',
                        help='--surface backend ONLY, in place of --policy: a crashed job that leaves no check unmeasured')
    parser.add_argument('--finding', help='the trigger, in one line')
    parser.add_argument('--sections', help='input-edit: which sections were edited')
    parser.add_argument('--regens', help='input-edit: which regenerations this bought')
    parser.add_argument('--read', help='adjudication: the exact path + field read')
    parser.add_argument('--actual', help='adjudication: what the artifact actually says')
    parser.add_argument('--outcome', choices=JOB_OUTCOMES,
                        help='job ONLY (and required there): the terminal outcome')
    parser.add_argument('--cause', choices=INCOMPLETE_CAUSES,
                        help='incomplete-triage ONLY (and required there): who burned the scenario time budget')
    parser.add_argument('--verdict', choices=WARNING_VERDICTS,
                        help='warning-triage ONLY (and required there): what you decided about the warning')
    parser.add_argument('--flag-class', choices=FLAG_CLASSES, help='regeneration: what the re-draw answers')
    parser.add_argument('--defect',
                        help='regeneration: a CITATION for a content|infra re-draw — the backend flag as emitted '
                             '(lower_snake_case, e.g. unsatisfiable_inputs) or finding:<id>. Prose is refused.')
    parser.add_argument('--note', help='anything else worth one line')
    parser.add_argument('--artifacts-dir', help='defaults to $ART, else $WORKDIR/artifacts')
    args = parser.parse_args()

    # READ modes, decided before anything else: they write nothing, so none of the append-side
    # requirements apply to them. `--kind` and `--finding` stay required for the APPEND path — an
    # entry without them is the half-typed record the typed kinds exist to refuse.
    if args.adoption_test:
        if args.kind or args.finding or args.list_kind:
            parser.error('--adoption-test is a READ mode: it takes no --kind/--finding/--list.')
        donor = (args.donor_set or '').strip()
        # Length-checked, not just non-empty. The reference lookup is a substring match over the
        # `job` findings, so a short value matches an arbitrary job and the verdict is then
        # rendered against it — indistinguishable, in the output, from a real one. Whitespace-only
        # used to pass the bare truthiness test and matched EVERY job entry.
        if len(donor) < _MIN_DONOR_SET_ID:
            parser.error(
                f'--adoption-test needs --donor-set <the qualification scenario_set_id adoption would draw from> — '
                f'at least {_MIN_DONOR_SET_ID} characters. It is matched against the `job` entries, so a short value '
                f'matches an arbitrary batch and the verdict reads exactly like a real one.'
            )
        return _print_adoption_verdict(_artifacts_dir(args.artifacts_dir) / 'loop_log.md', donor)

    if args.list_kind:
        if args.kind or args.finding:
            parser.error('--list is a READ mode: it takes no --kind/--finding (the kind to list is --list\'s value).')
        wanted = _canonical(args.list_kind)
        entries = list_entries(_artifacts_dir(args.artifacts_dir) / 'loop_log.md', wanted)
        for entry in entries:
            print(entry)
        if not entries:
            print(f'append_loop_log: no `{wanted}` entries in loop_log.md. That is a FACT about the round — if you '
                  'made demands and none are here, they were never recorded, and nothing downstream can verify them.',
                  file=sys.stderr)
        return 0

    if not args.kind or not args.finding:
        parser.error('--kind and --finding are required (or use --list <kind> to read).')
    args.kind = _canonical(args.kind)
    _validate(args, parser)

    art = _artifacts_dir(args.artifacts_dir)
    if args.surface in ('plugin', 'sdk') and not args.policy:
        print(f'append_loop_log: warning: this `--surface {args.surface}` entry names no check, so it caps the suite at not '
              'yet qualified until it names the checks it affects (`--policy <check>`, once per check).', file=sys.stderr)
    if args.surface == 'backend' and args.policy:
        foreign = _client_judged(art.parent, args.policy)
        if foreign is None:
            print(f'append_loop_log: warning: the eval set beside {art} could not be read, so whether '
                  f'{", ".join(args.policy)} is judged by the backend alone was not checked — written as given.', file=sys.stderr)
        elif foreign:
            parser.error(
                f'{", ".join(foreign)}: not judged by the backend alone, so a `--surface backend --policy` entry on it '
                'can never be re-checked. If its own verdicts are what is wrong, file `--surface eval --policy <it>` '
                '(the final run\'s audit re-checks it); if backend machinery keeps it from measuring, file '
                '`--surface eval --policy <it>` too: the final run\'s re-check keeps it live while any of its verdicts '
                'is missing or wrong.'
            )
    stage = None
    gate_finding = None
    if args.kind == 'regeneration':
        stage = _derive_stage(art)
        gate_finding = _stage_finding(args, stage)
    art.mkdir(parents=True, exist_ok=True)
    path = art / 'loop_log.md'
    existing = path.read_text(encoding='utf-8') if path.is_file() else ''
    if not existing.strip():
        existing = HEADER
    if not existing.endswith('\n'):
        existing += '\n'
    path.write_text(existing + _entry(args, stage, gate_finding=gate_finding), encoding='utf-8')
    if stage is not None:
        _write_regen_token(art)
    # A stage is derived for `regeneration` and for nothing else, so its absence is not a hole
    # in the line — there is no stage to state.
    derived = f' ({stage})' if stage is not None else ''
    lane = f' (lane {args.lane})' if args.lane else ''
    print(f'✓ loop_log.md ← {args.kind}{derived}{lane}')
    if gate_finding:
        doctrine = Path(__file__).resolve().parent.parent / 'docs' / 'gate-findings.md'
        print(f'append_loop_log: finding: {gate_finding} Logged with the entry; whether this re-draw is worth a '
              f'batch is yours to decide ({doctrine}).', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.path.append(str(Path(__file__).resolve().parent))  # poll_job.sh runs this under `python3 -I`
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
