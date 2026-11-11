#!/usr/bin/env python3
"""Record one agent finding's TYPED fields beside `agent_findings.md`, so no reader parses them from prose.

    record_finding.py <agent_findings.md> --id agent.<name> --status <status> --found-during <stage> --title "<title>"
                      [--severity <s>] [--subject complication:<name>|policy:<name>]... [--root-cause <id>]
                      [--run <run folder> [--scenario <id>]... [--evidence <item>:<turn>:<quote>]...] [--cause test_setup]
    record_finding.py <agent_findings.md> --id agent.<name> --fold-into agent.<other>   # one defect: shown as the other
    record_finding.py <agent_findings.md> --backfill     # rows for the sections whose status reads unambiguously
    record_finding.py <agent_findings.md> --check        # name every section no row covers

The markdown section stays the human's evidence (a defect `scored_findings.py attribute` files gets its section
rendered from its typed fields, :func:`render_section`); this writes the fields a script decides on — the
status tier, the stage that found it, its title and the run it was seen in — as one JSON row per
finding in `agent_findings.jsonl` next to it, keyed by `--id` (the section's `finding id`).
Recording an id again updates its row, so a status change is the same call. `prune_agent_findings.py`
decides a section by its row when one exists, and falls back to reading the section only for a
finding no row covers. The overview page reads the rows too, and prefers them over the prose.

Row: `{id, status, found_during, title, severity?, subjects?, root_cause?, source?: {run, scenarios?, recorded_at}, evidence?,
run_stale?, cause?}`.
Nothing is defaulted: a value the writer did not give is absent, never guessed. `--scenario` (repeatable,
needs `--run`) names the scenarios of that run the finding was read off, so `artifact_deps.py` dates the
finding by those scenarios' verdicts alone; `recorded_at` is when the row was written, so a later rescore
or re-run of a cited scenario marks the finding as based on the previous verdicts.

`--evidence '<item>:<turn_idx>:<quote>'` (repeatable, needs `--run`) is CHECKED before anything is written:
`<item>` is a verdict item of `runs/<run>/` as `red_cells.py list` names it (`<scenario>`,
`<scenario>/stability_retry`, `<scenario>/trial_<k>`; `trial_<k>/<scenario>` reads the same), and the quote
must appear, whitespace-normalized, in one of the agent's replies at that turn of its `transcript.json`. A
miss exits 2 and prints what that turn actually says. Stored as `evidence: [{run, item, turn_idx, quote}]`;
a call with `--run` restates it (none given drops the old run's evidence). A `confirmed` row recorded with
`--run` must carry at least one.

A `confirmed` record (and a `--backfill` row) whose section still states `why it might be intended:` lands `potential`:
the section's status reads `potential — may be intended`, and a WARN says how to confirm it. Exit 0. A value with no
letter or digit (a `—` placeholder) states no reason and demotes nothing. A `confirmed` record of a section whose status
still reads potential rewrites that section confirmed (`severity if real:` → `severity:`), so the md and the row agree.

`--subject` (repeatable) names what the finding is ABOUT in the suite: `complication:<name>` (a typical
complication, the user move) or `policy:<name>` (the eval that grades it). A call that passes it replaces the
row's `subjects`; one that does not keeps them. A `potential` finding's subjects are what
`record_target_rulings.py` will not rule out without an internal overrule, and a confirmed one's `policy:`
subject is what `red_cells.py attribution` reads to file that policy's reds as the agent's.

`--root-cause <id>` (`[a-z][a-z0-9_]*`) names the ONE agent defect behind the finding: findings one agent change would
fix together share it, and the customer reads them as one (`scored_findings.py`). A typed key the writer states, never a
text-similarity guess. A call that passes it replaces it; one that does not keeps it.

`--fold-into agent.<other>` (with `--id` only) records that the finding IS another one's defect — the same act, typed by
the writer: it sets the row's `root_cause` to the other's (`scored_findings.root_cause`), and that of every row that
already shared the folded row's own, so the page shows them as one issue with every quote kept. Nothing else of the row
changes (its status, evidence and subjects stay). It refuses (exit 2) a missing row, a missing or retired target, and
the row itself. A different act that the other finding's fix would also remove is not a fold.

`--cause test_setup` types a finding whose cause is in plurai_sim — the harness, a mock, the seeded data — not in the
agent: the call MOVES its section out of `agent_findings.md` into `test_setup_findings.md` beside it, and its row into
`test_setup_findings.jsonl` (both internal, never on a customer surface), so no customer reads a test-setup remedy as an
agent finding. `cause` is otherwise absent: every finding in `agent_findings.*` is the agent's.

`run_stale: true` is set by `open_epoch.py` on a confirmed row it KEPT across a resume of a handed-off build: its
evidence is the earlier run's until this build's final audit reproduces it. A call with `--run` re-anchors the
row and clears it.

`--backfill` writes a row only for a section whose `status:` field is exactly one of the statuses and
whose `finding id` is present; it fills `title` on an existing row that lacks one and changes nothing
else. `--check` changes nothing. Both list every section they could not cover.

Exit codes: 0 recorded / every section covered · 2 a value outside its closed set, evidence the run's
transcript does not hold, or the store could not be read or written · 3 (`--backfill`, `--check`) at
least one section is listed as not covered.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# `prune_agent_findings` (read lazily below) is a sibling; this file is run as a script and imported by path alike.
sys.path.insert(0, str(Path(__file__).resolve().parent))

STATUSES = ('confirmed', 'potential', 'retired')
#: `--cause`: where the defect lives when it is not the agent. A `test_setup` finding is ours and leaves the customer's file.
CAUSES = ('test_setup',)
TEST_SETUP_MD = 'test_setup_findings.md'
STAGES = ('scan', 'suite build', 'quality loop', 'run', 'fixes')
#: The run stage (`runs_index.RUN_STAGES`, as `runs/index.json` records it) a `--run` folder's finding was found in. Only
#: these two are read: a `--found-during` of `quality loop` or `run` is corrected to the named run's own stage, and every
#: other stage (`scan`, `suite build`, `fixes`) stands as the caller gave it.
RUN_FOUND_DURING = {'qualification': 'quality loop', 'final': 'run'}
SEVERITIES = ('high', 'medium', 'low')
#: What a `--subject` may name: a typical complication (the user move) or a policy (the eval that grades it).
SUBJECT_KINDS = ('complication', 'policy')
_SUBJECT = re.compile(r'^(?P<kind>[a-z]+):(?P<name>[A-Za-z0-9_][\w.-]*)$')
_ROOT_CAUSE = re.compile(r'^[a-z][a-z0-9_]*$')
#: The field a section says why its behaviour may be the owner's choice in: a section that states one is a question for
#: the owner, so it is recorded `potential` whatever status it is given (:func:`land_intended`).
INTENDED_FIELD = 'why it might be intended'
#: A stated reason holds at least one letter or digit, in any script: a `—` / `-` / `…` placeholder states none.
_REASON = re.compile(r'[^\W_]')
MAY_BE_INTENDED = 'potential — may be intended'

#: A field line of a findings section — `- **status:** confirmed — reproduced…` — in the markdown
#: variance the authoring templates produce (bold or not, colon inside or outside the emphasis).
_FIELD = re.compile(r'^[ \t]*(?:[-*+]|\d+[.)])?[ \t]*[*_`]{0,2}[ \t]*(?P<key>[a-z][a-z ]*?)[ \t]*[*_`]{0,2}[ \t]*:[ \t*_]*(?P<value>.*)$', re.I)
#: A run folder as the templates cite it: `runs/<folder>` (an optional `· set <id>` follows).
_RUN = re.compile(r'\bruns/(?P<folder>[A-Za-z0-9][\w.-]*)')
_FOLDER = re.compile(r'^[A-Za-z0-9][\w.-]*$')


def store_path(findings_md: Path) -> Path:
    """The typed store for a findings file: the same name, `.jsonl`."""
    return findings_md.with_suffix('.jsonl')


def read_rows(store: Path) -> dict[str, dict]:
    """`{id: row}`; a missing store is `{}`. A malformed line raises ValueError — never skipped."""
    if not store.exists():
        return {}
    rows: dict[str, dict] = {}
    for n, line in enumerate(store.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not isinstance(row.get('id'), str):
            raise ValueError(f'{store}:{n} is not a finding row')
        rows[row['id']] = row
    return rows


def write_rows(store: Path, rows: dict[str, dict]) -> None:
    tmp = store.with_name(f'.{store.name}.tmp')
    tmp.write_text(''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n' for row in rows.values()), encoding='utf-8')
    tmp.replace(store)


def route_test_setup(findings: Path, fid: str, rows: dict[str, dict]) -> bool:
    """Move finding `fid` — its section and its row — from `findings` / its store to the internal test-setup pair beside
    them. True when a section moved; the row moves either way (the section may not be written yet)."""
    import prune_agent_findings  # noqa: PLC0415

    internal = findings.with_name(TEST_SETUP_MD)
    internal_rows = read_rows(store_path(internal))
    internal_rows[fid] = {**internal_rows.get(fid, {}), **rows.pop(fid)}
    write_rows(store_path(internal), internal_rows)
    if not findings.is_file():
        return False
    preamble, *sections = prune_agent_findings._split_sections(findings.read_text(encoding='utf-8'))
    moving = [sec for sec in sections if (m := prune_agent_findings._FINDING_ID.search(sec)) and m.group(1) == fid]
    if not moving:
        return False
    head = internal.read_text(encoding='utf-8') if internal.is_file() else '# Test-setup findings — causes in plurai_sim, not the agent (internal)\n\n'
    internal.write_text(head.rstrip('\n') + '\n\n' + ''.join(moving).strip('\n') + '\n', encoding='utf-8')
    tmp = findings.with_name(f'.{findings.name}.tmp')
    tmp.write_text(preamble + ''.join(sec for sec in sections if sec not in moving), encoding='utf-8')
    tmp.replace(findings)
    return True


def run_folder(value: str) -> str | None:
    """The bare run folder name, from `qual-2` or `runs/qual-2`; None for anything else (a path is refused)."""
    folder = value.strip()
    if folder.startswith('runs/'):
        folder = folder[len('runs/'):]
    return folder if _FOLDER.match(folder) else None


def _fields(section: str) -> dict[str, str]:
    """`{field key: value}` for a section's field lines; the first occurrence of a key wins."""
    fields: dict[str, str] = {}
    for line in section.splitlines()[1:]:
        match = _FIELD.match(line)
        if match:
            fields.setdefault(match.group('key').strip().lower(), match.group('value').strip())
    return fields


def _first_word(value: str) -> str:
    match = re.match(r'[*_`\s]*([A-Za-z]+)', value)
    return match.group(1).lower() if match else ''


def _stage(value: str) -> str | None:
    """The stage a `found during:` value names, matched as a whole-word prefix; None when it names none."""
    text = re.sub(r'^[*_`\s]+', '', value).lower()
    return next((stage for stage in STAGES if re.match(re.escape(stage) + r'\b', text)), None)


def section_row(section: str) -> tuple[dict | None, str]:
    """`(row, '')` for a section whose status reads unambiguously, else `(None, why)`."""
    import prune_agent_findings  # noqa: PLC0415 - it imports this module; imported lazily to keep that one-way at load

    heading = prune_agent_findings._heading_of(section)
    found = prune_agent_findings._FINDING_ID.search(section)
    if not found:
        return None, 'no `finding id` line'
    fields = _fields(section)
    status = _first_word(fields.get('status', ''))
    if status not in STATUSES:
        return None, f'status {fields.get("status", "")!r} is not one of {", ".join(STATUSES)}' if 'status' in fields else 'no `status` line'
    row: dict = {'id': found.group(1), 'status': status, 'title': re.sub(r'^\d+[.)]?\s+', '', heading)}
    if 'found during' in fields:
        stage = _stage(fields['found during'])
        if stage is None:
            return None, f'`found during` {fields["found during"]!r} is not one of {", ".join(STAGES)}'
        row['found_during'] = stage
    severity = _first_word(fields.get('severity', '') or fields.get('severity if real', ''))
    if severity in SEVERITIES:
        row['severity'] = severity
    folders = {m.group('folder') for m in _RUN.finditer(fields.get('run', ''))}
    if len(folders) == 1:
        row['source'] = {'run': folders.pop()}
    return row, ''


def _sections(findings: Path) -> list[str]:
    import prune_agent_findings  # noqa: PLC0415

    return prune_agent_findings._split_sections(findings.read_text(encoding='utf-8'))[1:]


def check(findings: Path, rows: dict[str, dict]) -> list[str]:
    """One line per section no row covers: its heading, and its id when it has one."""
    import prune_agent_findings  # noqa: PLC0415

    missing = []
    for section in _sections(findings):
        found = prune_agent_findings._FINDING_ID.search(section)
        if not found or found.group(1) not in rows:
            ident = f' (`{found.group(1)}`)' if found else ' (no `finding id` line)'
            missing.append(f'## {prune_agent_findings._heading_of(section)}{ident}')
    return missing


def _section_of(findings: Path, fid: str) -> tuple[str, list[str], int] | None:
    """`(preamble, sections, index of fid's section)`, None when the file or the section is missing."""
    import prune_agent_findings  # noqa: PLC0415

    if not findings.is_file():
        return None
    preamble, *sections = prune_agent_findings._split_sections(findings.read_text(encoding='utf-8'))
    at = next((n for n, s in enumerate(sections) if (m := prune_agent_findings._FINDING_ID.search(s)) and m.group(1) == fid), None)
    return None if at is None else (preamble, sections, at)


def intended(findings: Path, fid: str) -> str | None:
    """The :data:`INTENDED_FIELD` of `fid`'s section when it states a reason (:data:`_REASON`): a `confirmed` record of
    it lands `potential` (:func:`land_intended` then says so in the section). A placeholder with no letter or digit
    (`—`) is no reason, so it demotes nothing. Read only."""
    found = _section_of(findings, fid)
    why = _fields(found[1][found[2]]).get(INTENDED_FIELD, '') if found else ''
    return why if _REASON.search(why) else None


def land_intended(findings: Path, fid: str, why: str) -> str:
    """Rewrite `fid`'s section status to :data:`MAY_BE_INTENDED` (the page reads the tier off it), after its row is
    written `potential`; the warning, which says how to confirm it."""
    found = _section_of(findings, fid)
    if found:
        preamble, sections, n = found
        lines = sections[n].splitlines(keepends=True)
        at = next((i for i, line in enumerate(lines[1:], 1) if (f := _FIELD.match(line)) and f.group('key').strip().lower() == 'status'), None)
        if at:
            lines[at] = f'- **status:** {MAY_BE_INTENDED}\n'
        else:
            lines.insert(1, f'- **status:** {MAY_BE_INTENDED}\n')
        sections[n] = ''.join(lines)
        tmp = findings.with_name(f'.{findings.name}.tmp')
        tmp.write_text(preamble + ''.join(sections), encoding='utf-8')
        tmp.replace(findings)
    return (f'WARN: {fid} recorded as potential: its section says why it might be intended ({why}). To confirm it, drop that '
            'line and record it again.')


def land_confirmed(findings: Path, fid: str) -> None:
    """After a `confirmed` row is written: rewrite `fid`'s section confirmed when its status still reads potential (the
    page parser's own tier read), by the scored run's promotion (`scored_findings._promote`: `severity if real:` →
    `severity:`, the potential-only fields dropped), so the page reads the tier the row holds. Only from potential: a
    retired or withdrawn section stays as it is. A section already written confirmed that states only `severity if real:`
    gets that one line renamed `severity:` (the page parser reads `severity:` alone), and nothing else."""
    import prune_agent_findings  # noqa: PLC0415
    import scored_findings  # noqa: PLC0415 - it imports this module at load

    found = _section_of(findings, fid)
    if not found:
        return
    preamble, sections, n = found
    fields = _fields(sections[n])
    if prune_agent_findings._POTENTIAL.search(sections[n]):  # noqa: SLF001 - the page parser's tier read
        sections[n] = scored_findings._promote(sections[n])  # noqa: SLF001 - THE potential → confirmed rewrite
    elif _first_word(fields.get('status', '')) == 'confirmed' and 'severity if real' in fields and 'severity' not in fields:
        lines = sections[n].splitlines(keepends=True)
        at = next(i for i, line in enumerate(lines[1:], 1) if (f := _FIELD.match(line)) and f.group('key').strip().lower() == 'severity if real')
        lines[at] = f'- **severity:** {fields["severity if real"]}\n'
        sections[n] = ''.join(lines)
    else:
        return
    tmp = findings.with_name(f'.{findings.name}.tmp')
    tmp.write_text(preamble + ''.join(sections), encoding='utf-8')
    tmp.replace(findings)


def backfill(findings: Path, rows: dict[str, dict], landed: list[tuple[str, str]]) -> list[str]:
    """Add rows for unambiguous sections (and a missing `title` on existing rows); the rest, listed. A confirmed section
    that states why it might be intended lands potential: its `(id, why)` goes on `landed` (:func:`land_intended`
    once the rows are written)."""
    import prune_agent_findings  # noqa: PLC0415

    listed = []
    for section in _sections(findings):
        row, why = section_row(section)
        if row is None:
            listed.append(f'## {prune_agent_findings._heading_of(section)} — {why}')
        elif row['id'] in rows:
            rows[row['id']].setdefault('title', row['title'])
        else:
            why = intended(findings, row['id']) if row['status'] == 'confirmed' else None
            if why:
                row['status'] = 'potential'
                landed.append((row['id'], why))
            rows[row['id']] = row
    return listed


def _replies(turn: dict) -> list[str]:
    """The agent's own replies at one transcript turn: `agent_message` and every assistant message of the turn
    (`agent_messages` and `turn_messages`), mid-turn ones included. A content-block list contributes its text blocks."""
    texts = [turn.get('agent_message')]
    for m in [*(turn.get('agent_messages') or []), *(turn.get('turn_messages') or [])]:
        if isinstance(m, dict) and (m.get('role') == 'assistant' or m.get('type') == 'ai'):
            content = m.get('content')
            texts += content if isinstance(content, list) else [content]
    texts = [t.get('text') if isinstance(t, dict) and t.get('type') == 'text' else t for t in texts]
    return [t for t in texts if isinstance(t, str) and t.strip()]


def check_evidence(run_dir: Path, values: list[str]) -> tuple[list[dict], str]:
    """`(entries, '')` when every `<item>:<turn>:<quote>` is in that turn's agent reply, else `([], why)`."""
    import red_cells  # noqa: PLC0415 - the one item walk; only an --evidence call needs it

    if not run_dir.is_dir():
        return [], f'there is no runs/{run_dir.name}/ beside the findings file'
    items = dict(red_cells.run_items(run_dir))
    entries = []
    for value in values:
        item, _, rest = value.partition(':')
        turn, _, quote = rest.partition(':')
        quote = quote.strip()
        if not item.strip() or not turn.strip().isdigit() or not quote:
            return [], f'--evidence {value!r} is not "<item>:<turn_idx>:<verbatim quote>"'
        key = red_cells.normalize_item(item)
        if '..' in key.split('/') or key not in items:
            return [], f'--evidence item {item!r} is not an item of runs/{run_dir.name}/ — its items: {", ".join(sorted(items)) or "none"}'
        turn_idx = int(turn)
        try:
            transcript = json.loads((items[key] / 'transcript.json').read_text(encoding='utf-8'))
            turns = transcript.get('turns') or []
        except (OSError, ValueError, AttributeError) as exc:
            return [], f'runs/{run_dir.name}/{key} has no readable transcript.json ({type(exc).__name__})'
        if red_cells.is_pipeline(transcript):
            # A pipeline run has no turns: its whole output is one reply, quoted at turn 0 and nowhere else.
            if turn_idx != red_cells.PIPELINE_TURN:
                return [], (f'runs/{run_dir.name}/{key} is a pipeline run (no turns): its whole output is one reply, '
                            f'quoted at turn {red_cells.PIPELINE_TURN}; got turn {turn_idx}')
            replies = red_cells.whole_run_replies(transcript)
        else:
            found = next((t for t in turns if isinstance(t, dict) and t.get('turn_idx') == turn_idx), None)
            if found is None:
                return [], f'runs/{run_dir.name}/{key} has no turn {turn_idx} (turns {len(turns)})'
            replies = _replies(found)
        if not any(' '.join(quote.split()) in ' '.join(reply.split()) for reply in replies):
            said = '\n---\n'.join(replies) or '(no agent reply at this turn)'
            return [], f'the quote is not in the agent\'s reply at runs/{run_dir.name}/{key} turn {turn_idx}; that turn says:\n{said}'
        entries.append({'run': run_dir.name, 'item': key, 'turn_idx': turn_idx, 'quote': quote})
    return entries, ''


def parse_subject(value: str) -> str | None:
    """`<kind>:<name>` normalized (whitespace stripped), or None when it is not one of `SUBJECT_KINDS`."""
    match = _SUBJECT.match(value.strip())
    return f"{match.group('kind')}:{match.group('name')}" if match and match.group('kind') in SUBJECT_KINDS else None


def target_graders_beside(findings: Path) -> list[str]:
    """`final_bundle.target_graders` of the workdir's `policies_config.yaml` (beside the findings file or one level up).

    Empty when no config is readable: the `--subject` guard it feeds refuses (exit 2) a goal judge it can read, and a
    missing or unreadable config is never a block.
    """
    for folder in (findings.parent, findings.parent.parent):
        path = folder / 'policies_config.yaml'
        if not path.is_file():
            continue
        try:
            import yaml  # noqa: PLC0415 - only this guard reads YAML

            import final_bundle  # noqa: PLC0415

            return final_bundle.target_graders(yaml.safe_load(path.read_text(encoding='utf-8')))
        except Exception:  # noqa: BLE001 - an unreadable config leaves the guard silent
            return []
    return []


def render_section(row: dict, *, number: int, checks: list[str], evidence: list[str], trigger: str | None, why: str | None) -> str:
    """The `agent_findings.md` section of a row `scored_findings.py attribute` files, from typed fields alone: the
    title, the checks that failed on it (the most-linked first: the planner's probe), the agent's quoted replies and the
    reader's `why` (as `why it is the agent:`, the line the regression planner and `/simulation-fixes` read; `attribute`
    warns when it trips the customer-text guard, as it does for the title) are what the customer reads; the reader's
    `trigger` goes on an internal field (`customer_text_guard` `internal_fields`) and the linked scenarios on `seen in:`
    (the planner's candidates; `scored_findings.customer_copy` drops it from the page)."""
    lines = [f'## {number}. {row["title"]}', '- **status:** confirmed']
    if row.get('severity'):
        lines.append(f'- **severity:** {row["severity"]}')
    lines += [f'- **finding id:** `{row["id"]}`', f'- **found during:** {row["found_during"]}']
    if checks:
        lines.append(f'- **probe:** {", ".join(f"`{c}`" for c in checks)} (eval policy)')
    if trigger:
        lines.append(f'- **trigger:** {trigger}')
    source = row.get('source') or {}
    if source.get('run'):
        lines.append(f'- **run:** runs/{source["run"]}')
    if source.get('scenarios'):
        lines.append(f'- **seen in:** {", ".join(source["scenarios"])}')
    if evidence:
        lines.append(f'- **evidence:** {evidence[0]}')
        lines += [f'  {more}' for more in evidence[1:]]
    if why:
        lines.append(f'- **why it is the agent:** {why}')
    return '\n'.join(lines) + '\n'


def subjects_of(row: dict) -> list[str]:
    """The row's `subjects`, each one `parse_subject` reads; anything else is dropped, never guessed."""
    raw = row.get('subjects')
    return [s for s in (parse_subject(v) for v in raw if isinstance(v, str)) if s] if isinstance(raw, list) else []


def _record(args: argparse.Namespace) -> int:
    if not args.id or not args.status or not args.found_during or args.title is None:
        print('record_finding: --id, --status, --found-during and --title are required to record a row', file=sys.stderr)
        return 2
    if not args.id.startswith('agent.') or not args.id[len('agent.'):]:
        print(f'record_finding: --id must be `agent.<name>`, got {args.id!r}', file=sys.stderr)
        return 2
    title = ' '.join(args.title.split())
    if not title:
        print('record_finding: --title is empty; pass the section heading\'s title', file=sys.stderr)
        return 2
    folder = None
    if args.run is not None:
        folder = run_folder(args.run)
        if folder is None:
            print(f'record_finding: --run takes a run folder name (e.g. `qual-2`), got {args.run!r}', file=sys.stderr)
            return 2
    subjects = [parse_subject(v) for v in args.subject or []]
    if None in subjects:
        bad = [v for v, parsed in zip(args.subject, subjects) if parsed is None]
        print(f'record_finding: --subject takes `complication:<name>` or `policy:<name>`; got {", ".join(map(repr, bad))}', file=sys.stderr)
        return 2
    goal_judges = sorted(set(target_graders_beside(args.findings)) & {s[len('policy:'):] for s in subjects if s.startswith('policy:')})
    if goal_judges:
        print(f'record_finding: `{"`, `".join(goal_judges)}` grades only its own scenario\'s target, so it can never reproduce this finding in '
              'another scenario. Name the check that grades the behaviour itself.', file=sys.stderr)
        return 2
    if args.root_cause is not None and not _ROOT_CAUSE.match(args.root_cause.strip()):
        print(f'record_finding: --root-cause takes one snake_case id (e.g. `claims_unperformed_action`), got {args.root_cause!r}', file=sys.stderr)
        return 2
    scenarios = [s.strip() for s in args.scenario or []]
    if scenarios and folder is None:
        print('record_finding: --scenario names scenarios OF a run; pass --run <run folder> with it', file=sys.stderr)
        return 2
    bad = [s for s in scenarios if not _FOLDER.match(s)]
    if bad:
        print(f'record_finding: --scenario takes a scenario folder name, never a path; got {", ".join(map(repr, bad))}', file=sys.stderr)
        return 2
    evidence: list[dict] = []
    if args.evidence and folder is None:
        print('record_finding: --evidence quotes a reply OF a run; pass --run <run folder> with it', file=sys.stderr)
        return 2
    if args.status == 'confirmed' and folder and not args.evidence:
        print('record_finding: a confirmed finding recorded with --run needs --evidence "<item>:<turn_idx>:<verbatim quote>" '
              '— the agent reply that shows it, from that run\'s transcript', file=sys.stderr)
        return 2
    if args.evidence:
        evidence, why = check_evidence(args.findings.parent / 'runs' / folder, args.evidence)
        if why:
            print(f'record_finding: {why}', file=sys.stderr)
            return 2
    store = store_path(args.findings)
    try:
        rows = read_rows(store)
        why = intended(args.findings, args.id) if args.status == 'confirmed' else None
        status = 'potential' if why else args.status
        args.found_during = found_during_of(args.findings, folder, args.found_during)
        row = {**rows.get(args.id, {}), 'id': args.id, 'status': status, 'found_during': args.found_during, 'title': title}
        if args.severity:
            row['severity'] = args.severity
        if subjects:
            row['subjects'] = sorted(set(subjects))
        if args.root_cause is not None:
            row['root_cause'] = args.root_cause.strip()
        if args.cause:
            row['cause'] = args.cause
        if folder:
            row.pop('run_stale', None)
            row['source'] = {'run': folder, **({'scenarios': sorted(set(scenarios))} if scenarios else {}),
                             'recorded_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}
            row.pop('evidence', None)
            if evidence:
                row['evidence'] = evidence
        rows[args.id] = row
        moved = route_test_setup(args.findings, args.id, rows) if args.cause == 'test_setup' else None
        write_rows(store, rows)
        warn = land_intended(args.findings, args.id, why) if why else None
        if status == 'confirmed':
            land_confirmed(args.findings, args.id)
    except (OSError, ValueError) as exc:
        print(f'record_finding: {store} could not be updated ({type(exc).__name__}: {exc})', file=sys.stderr)
        return 2
    where = '' if moved is None else f' → {TEST_SETUP_MD} (internal; ' + ('section moved)' if moved else 'no section of that id to move yet)')
    if warn:
        print(f'record_finding: {warn}', file=sys.stderr)
    print(f'record_finding: {args.id} = {status} ({args.found_during}){where}')
    return 0


def fold_into(findings: Path, fid: str, into: str) -> str:
    """Record finding ``fid`` as finding ``into``'s defect: ``fid``'s ``root_cause``, and every row's that shared ``fid``'s,
    becomes ``into``'s (``scored_findings.root_cause``). The line said; ValueError on a refusal (a missing row, a missing or
    retired target, the row itself, a potential and a confirmed one: the page groups by tier, so such a fold would merge
    nothing), OSError when the store cannot be read or written."""
    import scored_findings  # noqa: PLC0415 - it imports this module at load: THE root-cause reading

    store = store_path(findings)
    rows = read_rows(store)
    if fid == into:
        raise ValueError(f'{fid} cannot be folded into itself')
    if fid not in rows:
        raise ValueError(f'{fid} has no row in {store.name}')
    if into not in rows or rows[into].get('status') == 'retired':
        raise ValueError(f'{into} is {"retired" if into in rows else f"no row of {store.name}"}: nothing to fold into')
    if (rows[fid].get('status') == 'potential') != (rows[into].get('status') == 'potential'):
        raise ValueError(f'{fid} is {rows[fid].get("status")} and {into} is {rows[into].get("status")}: '
                         'a potential and a confirmed finding are never one issue, so nothing is folded')
    cause, was = scored_findings.root_cause(into, rows[into]), scored_findings.root_cause(fid, rows[fid])
    if cause == was:
        return f'{fid} is already {into}\'s defect (`{cause}`)'
    moved = [other for other, row in rows.items() if other != fid and scored_findings.root_cause(other, row) == was]
    for other in (fid, *moved):
        rows[other] = {**rows[other], 'root_cause': cause}
    write_rows(store, rows)
    return f'{fid} folded into {into} (`{cause}`)' + (f'; {", ".join(moved)} with it' if moved else '')


def found_during_of(findings: Path, folder: str | None, given: str) -> str:
    """`given`, unless it is `quality loop` or `run` and `folder`'s stage in `runs/index.json` (or its run.json, by the
    index's own rule) says the other: then that one, with a WARNING on stderr. Fails open to `given`."""
    if folder is None or given not in RUN_FOUND_DURING.values():
        return given
    try:
        import runs_index  # noqa: PLC0415 - sibling script: the index's own stage rule

        runs = findings.parent / 'runs'
        run = runs_index._read_json(runs / folder / 'run.json')  # noqa: SLF001
        recorded = runs_index._previous(runs / runs_index.INDEX).get(folder, {})  # noqa: SLF001
        stage, _source = runs_index._stage(folder, run if isinstance(run, dict) else {}, recorded, {})  # noqa: SLF001
    except Exception:  # noqa: BLE001 - a label correction never blocks recording
        return given
    derived = RUN_FOUND_DURING.get(stage or '')
    if derived is None or derived == given:
        return given
    print(f'WARNING: record_finding: --run {folder} is a {stage} run, so the finding is recorded found during {derived!r}, not {given!r}',
          file=sys.stderr)
    return derived


def _sweep(args: argparse.Namespace) -> int:
    store = store_path(args.findings)
    try:
        rows = read_rows(store)
        if not args.findings.exists():
            return 0
        landed: list[tuple[str, str]] = []
        listed = backfill(args.findings, rows, landed) if args.backfill else check(args.findings, rows)
        if args.backfill:
            write_rows(store, rows)
            for fid, why in landed:
                print(f'record_finding: {land_intended(args.findings, fid, why)}', file=sys.stderr)
    except (OSError, ValueError) as exc:
        print(f'record_finding: {args.findings} could not be read ({type(exc).__name__}: {exc})', file=sys.stderr)
        return 2
    if not listed:
        return 0
    head = 'sections left without a row (record each with --id/--status/--found-during/--title)' if args.backfill else 'sections with no row'
    print(f'record_finding: {len(listed)} {head}:')
    print('\n'.join(f'  {line}' for line in listed))
    return 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('findings', type=Path, help='the agent_findings.md this row belongs to')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--backfill', action='store_true', help='write rows for the sections whose status reads unambiguously')
    mode.add_argument('--check', action='store_true', help='list every section no row covers (exit 3 when any)')
    parser.add_argument('--id', help='the section\'s finding id, e.g. agent.refund_window_overstated')
    parser.add_argument('--status', choices=STATUSES)
    parser.add_argument('--found-during', choices=STAGES)
    parser.add_argument('--title', help='the section heading\'s title, without its number')
    parser.add_argument('--severity', choices=SEVERITIES)
    parser.add_argument('--subject', action='append', help='complication:<name> or policy:<name> the finding is about (repeatable)')
    parser.add_argument('--root-cause', help='the one agent defect behind the finding (snake_case id), shared by findings one fix closes')
    parser.add_argument('--run', help='the run folder the finding was seen in (`runs/<folder>`)')
    parser.add_argument('--scenario', action='append', help='a scenario of that run the finding was read off (repeatable)')
    parser.add_argument('--evidence', action='append', help='<item>:<turn_idx>:<verbatim quote> of that run\'s agent reply, checked (repeatable)')
    parser.add_argument('--cause', choices=CAUSES, help='test_setup: the cause is in plurai_sim (harness, mock, seed) — moved to the internal file')
    parser.add_argument('--fold-into', metavar='AGENT_ID', help='with --id only: the finding is this other finding\'s defect (the same act)')
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    rest = (args.status, args.found_during, args.title, args.severity, args.subject, args.root_cause, args.run, args.scenario, args.evidence, args.cause)
    if args.backfill or args.check:
        if args.id is not None or args.fold_into is not None or any(v is not None for v in rest):
            print('record_finding: --backfill / --check take the findings file only', file=sys.stderr)
            return 2
        return _sweep(args)
    if args.fold_into is not None:
        if not args.id or any(v is not None for v in rest):
            print('record_finding: --fold-into takes --id and the finding it folds into, nothing else', file=sys.stderr)
            return 2
        try:
            said = fold_into(args.findings, args.id, args.fold_into)
        except (OSError, ValueError) as exc:
            print(f'record_finding: {exc}', file=sys.stderr)
            return 2
        print(f'record_finding: {said}')
        return 0
    return _record(args)


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
