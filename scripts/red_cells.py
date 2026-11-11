#!/usr/bin/env python3
"""Every red verdict unit of a local run under the backend's item names, and red-cell audit rows as overrules.

    red_cells.py list      --run-dir "$OUT" --greens <n> --workdir "$WORKDIR" [--split-dir <dir>] [--patterns <file>] [--out <file>]
    red_cells.py patterns  --run-dir "$OUT" [--audit <rows.json>]… [--out <file>]
    red_cells.py overrules --run-dir "$OUT" [--audit <rows.json>]… (--run-upload-id <id> | --refusal <file> [--refusal …])
                           [--overrule-unevaluated "<reason>"] [--out <file>]
    red_cells.py uncovered --run-dir "$OUT" [--audit <rows.json>]… --refusal <file> [--out <file>]
    red_cells.py attribution --report <saved suite_diagnostics envelope> --findings "$ART/agent_findings.md"
                           [--run-dir <final run> | --audit <rows.json>…] [--out <file>]
    red_cells.py passes    --run-dir "$OUT" --policy <p> [--policy …] [--n 2] [--split-dir <dir>] [--out <file>]
    red_cells.py labels    --run-dir "$OUT" [--audit <rows.json>]… [--pass-review <rows.json> …]
                           --run-upload-id <id> --stage qualification|final --eval-set-id <id> --out <file>

ITEMS. The local run dir and the backend name a trial differently: on disk trial ``k`` (k >= 2) is
``$OUT/trial_<k>/<scenario>/``, while the uploaded item, and every ``eval_edit.*`` finding's ``scenario``,
is ``<scenario>/trial_<k>``. Trial 1 is ``$OUT/<scenario>/`` and item ``<scenario>``; a stability retry
is ``$OUT/<scenario>/stability_retry/`` and item ``<scenario>/stability_retry``. :func:`run_items` is the
one walk from the first naming to the second; a glob of ``*/trial_*/`` over this layout finds no trial.

UNITS. A verdict UNIT is what the backend's edit regression guard protects and fingerprints: one entry of a
turn-based cell's ``turns`` stream (``turn_idx`` = that entry's own turn), or the whole cell of a
scenario-scoped policy (``turn_idx`` = null, whatever turns its reasoning cites). A unit is red when it is a
conclusive violation (``violated`` and ``status == 'success'``). Verdicts are the CURRENT ones: the item's
``verdicts.json`` (``scripts/verdicts_file.py``), never the transcript, which holds none.

``list`` groups the red units by ``(scenario, policy, turn_idx)`` with every trial's unit side by side,
each carrying its OWN reply, so one audit read covers a unit across trials while each trial still gets
its own verdict row. A ruling on one trial says nothing about another: every trial is a separate reply (a FINAL
run's corrections carry it only to a sibling whose reply holds the same quote: ``verdict_corrections`` SIBLINGS).

GREENS (``--greens <n>``, required with ``--workdir``: :data:`GREENS_REQUIRED`). A red-only audit can only clear reds,
so a false pass stands. ``list`` also lists at most ``n`` conclusive PASS units, each marked ``sampled``, in the same groups, deterministic
(``sha256(<run dir name>|<item>|<turn>)`` order): ``doubt`` — a pass its judge was unsure of (:func:`in_doubt`: a split
self-consistency vote or low confidence), one whose (scenario, policy) has a red trial first; ``split`` — per (scenario,
policy) with a red trial, ONE pass unit of another trial, a doubtful one first, then at a red turn; and ``stakes`` —
:data:`STAKES_PER_POLICY` pass units per policy, judge or code check, a :data:`STAKES_RULES` rule names in
``<workdir>/policies_config.yaml``. The first unit of each stakes policy with no red in the run is drawn before the
rounds; each round then draws :data:`GREEN_DRAW`, cut at ``n``. A sampled green's row is
``CONFIRMED PASS`` / ``FALSE PASS`` / ``UNDECIDABLE``; a FALSE PASS quotes the violating reply.

KNOWN PATTERNS. The suite's final audit is the official run's prior: ``patterns`` writes the rulings of a run still
standing (one decided verdict of :data:`PATTERN_VERDICTS`, the unit still as ruled, its quote verified), grouped by
``(policy, verdict, cause, eval_revision)`` with up to :data:`PATTERN_REASONS` reasons and :data:`PATTERN_EXAMPLES`
quoted examples. ``list --patterns`` attaches to each group the patterns of its policy at the eval revision its items
were scored at (index ``known_patterns``: ``{attached, stale}``). A pattern is a hint, never a row: the audit still
rules and quotes this run's own reply, and only its rows reach ``labels``, ``overrules`` or a correction.

AUDIT ROWS (the run's ``red_cell_audit.json``, ``red_cell_audit.py``: every command reads it unless ``--audit`` names
row files, and says ``audit missing`` when the run has none): ``item``, ``policy``, ``turn`` (an int, a list of ints,
or null), ``verdict``, and optionally ``defect_surface`` / ``quote`` / ``quote_turn`` / ``reason`` / ``clause`` /
``owner_clauses`` (EVERY rubric clause label a TRUE / FALSE RED rests on) / ``code_evidence`` (the product-code facts it
cites, ``red_cell_audit.CODE_EVIDENCE``) / ``policy_fingerprint`` (the judged cell's, stamped by ``save``) / ``thread_id``
(the judged attempt's, stamped by ``save``).
``item`` is the backend item name (``trial_<k>/<scenario>`` is read as ``<scenario>/trial_<k>``). A PIPELINE item (its
transcript has no ``turns``; the agent's whole output is ``pipeline_io.outputs``) has one reply, read at turn 0:
:func:`whole_run_replies` is that reply and :func:`evidence_turn` the turn a row's quote is checked at. Verdicts on a
recorded red: ``TRUE RED`` / ``FALSE RED`` / ``UNDECIDABLE``; on a recorded verdict the draft no longer reaches
(``eval_edit.verdict_dropped``): ``IN SCOPE`` (the rule still grades that turn) / ``OUT OF SCOPE``. A FALSE RED's
``defect_surface`` is its cause, one of :data:`CAUSES`; ``quote`` is the complying reply, verbatim, from the unit's
turn (``quote_turn`` names it on a scenario-scoped cell), or from a file the agent wrote (``quote_file``: its path in
the item's world, or the ``name`` of the SDK's file row, whose ``text_preview`` is the text a quote is checked in;
:func:`quote_miss`). An overrule needs neither: the gate's fingerprint is its evidence.

LABELS (prompt-cause policies only: a policy with at least one ``prompt`` FALSE RED or FALSE PASS). ``passes`` samples ``--n``
conclusive pass units per named policy — those whose ``sha256(<run dir name>|<item>|<turn>)`` sorts first, so a
re-read of the run draws the same ones — grouped like ``list``, for a pass review whose rows are
``{item, policy, turn, verdict: CONFIRMED PASS | FALSE PASS | UNDECIDABLE, quote, quote_turn?, reason}``.
``labels`` writes the ``upload_eval_labels`` arguments ``{eval_set_id, rows}``: one row per unit an audit or
pass-review row of such a policy rules, ``{policy, eval_revision, run_upload_id, item, turn, judge_verdict,
label, cause, quote, quote_turn, reason, stage}``; ``eval_revision`` is that of the item's current verdicts. A
row is skipped, with its reason on stdout, when its unit is not red (audit) or not a pass (review) now, a FALSE RED
names no cause from :data:`CAUSES`, a FALSE RED / FALSE PASS names a cause that is not the judge's error
(:data:`NOT_JUDGE_CAUSES`), or a decided row's quote is absent or not in that turn's reply.

``overrules`` emits one ``{gate_id, fingerprint, reason}`` per unit a row releases: ``eval_edit.lost_red``
for a FALSE RED red unit, ``eval_edit.verdict_dropped`` for an OUT OF SCOPE recorded pass. A row naming
several turns is split into one unit per turn. It REFUSES a unit any row ruled ``TRUE RED``,
``IN SCOPE`` or ``UNDECIDABLE``: that verdict stands until the audit itself changes. The fingerprint is
``gate_ledger.fingerprint(gate_id, policy, {run_upload_id, scenario: <item>, policy, turn_idx})`` —
the backend's own evidence — over the newest finalized run linked to the eval set (``--run-upload-id``,
or read off a refusal's findings with ``--refusal``). COMPATIBILITY ONLY: the guard no longer holds a commit and
answers an overrule ``not_holding``, so no skill step calls ``overrules`` or ``uncovered``; ``guard_decide.py``
decides on the committed result's ``guard`` report.

``uncovered`` reads a refused ``end_edit_session`` result (``--refusal``: the result JSON, or a list of
its ``gate_authority.findings``) and lists, grouped like ``list``, the held units no audit row covers —
the only units a post-refusal audit reads. It also names held units the audit ruled true
(``held_by_ruling``: repair or revert the edit) and released units still held (``released_but_held``:
resend the FULL overrule list).

UNEVALUATED. An ``eval_edit.unevaluated`` finding is a recorded verdict the draft reached none on, even
after the backend's in-call retry: its judge did not answer (``observed.now`` ``error`` or ``…sample_error``,
``cause: judge``), or its re-score could not run at all (``now`` = ``unscored (<why>)``, ``cause: unscored``).
Nothing was measured, so no audit row speaks to it and none releases it. Both commands list these units
apart (``unevaluated``). ``overrules`` emits one for each only on
``--overrule-unevaluated "<reason>"`` (at least 20 characters, the same for every unit), taken from every
``--refusal`` given, so a unit overruled on an earlier try stays in the list; the fingerprint is
``gate_ledger.fingerprint('eval_edit.unevaluated', policy, <the same unit evidence>)``.

ATTRIBUTION. An ``attribution.*`` finding of ``suite_diagnostics`` files a red against the eval or the environment
(``suite_defect``: "not counted against your agent"). ``attribution`` emits the overrule that files it back as the
agent's — ``{gate_id, fingerprint, reason}``, the served fingerprint — deterministically, for every holding one whose
policy (``evidence.policy``, else ``subject``) is the ``policy:`` subject of a CONFIRMED row of
``agent_findings.jsonl`` that is not ``run_stale``, or that a ``--audit`` row ruled ``TRUE RED``. A finding scoped
to one item (``evidence.item``) is the agent's only on a ``TRUE RED`` of that item and policy: a finding elsewhere
says nothing about what the simulated tools withheld there. It prints ``{overrules, lifted, kept, new}``; ``new``
counts the overrules the report's own job had not applied, so 0 means the saved envelope already reads them.

Exit codes: 0 ok · 2 usage or unreadable input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'skills' / 'simulation-run' / 'scripts'))

import record_finding  # noqa: E402 - sibling script: the findings store and its subject grammar
import red_cell_audit  # noqa: E402 - sibling script: the one audit file name and shape (C5)
from build_run_json import _AGENT_STATE_TOOL_TRAIL_KEY as TOOL_TRAIL_KEY  # noqa: E402 - the trail key the run.json reader reads
from build_run_json import eval_set_bodies, policy_veto_capability  # noqa: E402 - after the sys.path lines it depends on
from gate_ledger import OVERRULE_REASON_MIN_CHARS, fingerprint  # noqa: E402
import verdicts_file  # noqa: E402 - the one reader of an item's verdicts

LOST_RED = 'eval_edit.lost_red'
VERDICT_DROPPED = 'eval_edit.verdict_dropped'
UNEVALUATED = 'eval_edit.unevaluated'
GUARD_GATES = (LOST_RED, VERDICT_DROPPED, UNEVALUATED)

#: Why ``list`` refuses to run without ``--greens <n>`` (n >= 1) and ``--workdir``.
GREENS_REQUIRED = ('an audit of reds alone can only clear reds, so a false pass after a repair is never caught; '
                   'every audit list samples greens (the skills pass --greens 24 --workdir "$WORKDIR")')

RETRY_DIRNAME = 'stability_retry'
_TRIAL_DIR = re.compile(r'trial_(\d+)')
_LOCAL_TRIAL_ITEM = re.compile(r'trial_(\d+)/([^/]+)')

TRUE_RED, FALSE_RED, UNDECIDABLE = 'TRUE RED', 'FALSE RED', 'UNDECIDABLE'
IN_SCOPE, OUT_OF_SCOPE = 'IN SCOPE', 'OUT OF SCOPE'
CONFIRMED_PASS, FALSE_PASS = 'CONFIRMED PASS', 'FALSE PASS'
#: The verdicts that release a unit, with the gate whose finding they release.
RELEASES = {FALSE_RED: LOST_RED, OUT_OF_SCOPE: VERDICT_DROPPED}
#: The verdicts that keep a unit held: an overrule for it is refused.
KEEPS = (TRUE_RED, IN_SCOPE, UNDECIDABLE)
PASS_VERDICTS = (CONFIRMED_PASS, FALSE_PASS, UNDECIDABLE)
#: A sampled green's ruling rides the same audit file as the reds'.
VERDICTS = (TRUE_RED, FALSE_RED, UNDECIDABLE, IN_SCOPE, OUT_OF_SCOPE, CONFIRMED_PASS, FALSE_PASS)

#: A FALSE RED's cause (its ``defect_surface``). ``harness`` is the eval's own evidence wiring, ``type`` the wrong
#: kind of eval, ``prompt`` a right rule and evidence the judge misapplies, ``scenario`` a wrong judge-only ground truth.
CAUSES = ('rubric', 'harness', 'type', 'prompt', 'target_clause', 'scan_fact', 'scenario', 'env')
PROMPT = 'prompt'
STAGES = red_cell_audit.STAGES
#: Audit / pass-review verdict → label (the backend's ``EvalLabelRow.label``).
#: The FALSE RED / FALSE PASS causes that are not the judge's error (the scenario, the harness, the mocked world or a scan
#: fact): never uploaded as a label (``eval_rounds.FLAG_CAUSES`` are the ones that are).
NOT_JUDGE_CAUSES = ('scenario', 'harness', 'env', 'scan_fact')
LABELS = {TRUE_RED: 'true_red', FALSE_RED: 'false_red', CONFIRMED_PASS: 'confirmed_pass', FALSE_PASS: 'false_pass', UNDECIDABLE: 'undecidable'}

#: ``(item, policy, turn_idx)`` — ``turn_idx`` None for a scenario-scoped cell.
Unit = Tuple[str, str, Optional[int]]


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _readable(d: Path) -> bool:
    """An item with a conversation to read: its transcript, with its verdicts.json beside it. A transcript with no
    verdicts.json raises (``verdicts_file.read`` names the converter): no item is read without its verdicts. An attempt
    nothing graded is still a conversation (its replies are evidence); it simply holds no cells."""
    return (d / 'transcript.json').is_file() and isinstance(verdicts_file.read(d), dict)


def run_items(out: Path) -> List[Tuple[str, Path]]:
    """``[(backend item name, local dir)]`` for every scenario folder of the run, every trial and retry included."""
    items: List[Tuple[str, Path]] = []
    for d in sorted(p for p in out.iterdir() if p.is_dir()):
        trial = _TRIAL_DIR.fullmatch(d.name)
        if trial and int(trial.group(1)) >= 2:
            items.extend((f'{s.name}/{d.name}', s) for s in sorted(p for p in d.iterdir() if p.is_dir()) if _readable(s))
            continue
        if _readable(d):
            items.append((d.name, d))
        if _readable(d / RETRY_DIRNAME):
            items.append((f'{d.name}/{RETRY_DIRNAME}', d / RETRY_DIRNAME))
    return sorted(items)


def item_scenario(item: str) -> str:
    return item.partition('/')[0]


def normalize_item(item: str) -> str:
    """The backend item name for an audit row's ``item``: a local ``trial_<k>/<scenario>`` reads as ``<scenario>/trial_<k>``."""
    item = item.strip().strip('/')
    local = _LOCAL_TRIAL_ITEM.fullmatch(item)
    if local:
        item = f'{local.group(2)}/trial_{local.group(1)}'
    return item[: -len('/trial_1')] if item.endswith('/trial_1') else item


#: ``Run.cells`` source tag: every current verdict comes from the item's ``verdicts.json``.
VERDICTS_SOURCE = 'verdicts'


def current_cells(d: Path) -> Tuple[Dict[str, Any], str]:
    """``({policy: cell}, 'verdicts')`` — the current verdicts of one item, off its ``verdicts.json``."""
    return verdicts_file.cells(verdicts_file.read(d)), VERDICTS_SOURCE


def _is_turn(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def units(cell: Any) -> List[Tuple[Optional[int], Mapping[str, Any]]]:
    """The verdict units of one cell: its ``turns`` stream entries, or the cell itself when it has none. A cell the score
    reads as red whose stream holds no red turn (every turn set aside or N/A) is also its own unit, ``(None, cell)``,
    AFTER the stream (so :meth:`Run.scoped` still reads it turn-based): the audit rules every red the score counts."""
    if not isinstance(cell, Mapping):
        return []
    turns = cell.get('turns')
    stream = [t for t in turns if isinstance(t, Mapping)] if isinstance(turns, list) else []
    if stream:
        found: List[Tuple[Optional[int], Mapping[str, Any]]] = [(t['turn_idx'], t) for t in stream if _is_turn(t.get('turn_idx'))]
        return found + [(None, cell)] if is_red(cell) and not any(is_red(t) for _, t in found) else found
    return [(None, cell)]


def is_red(unit: Mapping[str, Any]) -> bool:
    return bool(unit.get('violated')) and unit.get('status') == 'success'


def is_pass(unit: Mapping[str, Any]) -> bool:
    return not unit.get('violated') and unit.get('status') == 'success'


#: The one turn a pipeline item's evidence is read at: its whole output is one reply.
PIPELINE_TURN = 0


def is_pipeline(transcript: Any) -> bool:
    """A pipeline transcript: no dialog ``turns``, and the agent's output under ``pipeline_io.outputs``."""
    if not isinstance(transcript, Mapping) or transcript.get('turns'):
        return False
    io = transcript.get('pipeline_io')
    return isinstance(io, Mapping) and io.get('outputs') is not None


def turn_idxs(transcript: Any) -> List[int]:
    """Every turn a quote may sit at: :data:`PIPELINE_TURN` on a pipeline item, else each dialog turn's ``turn_idx``."""
    if is_pipeline(transcript):
        return [PIPELINE_TURN]
    turns = transcript.get('turns') if isinstance(transcript, Mapping) else None
    return sorted(t['turn_idx'] for t in turns or [] if isinstance(t, Mapping) and _is_turn(t.get('turn_idx')))


def whole_run_replies(transcript: Any) -> List[str]:
    """A pipeline transcript's agent reply: every non-blank string leaf of ``pipeline_io.outputs``; ``[]`` for a dialog.

    Only what the agent produced: ``injected_inputs`` is what it was handed, and so is each trail row's ``result``
    (``outputs.tool_trail[].result``, the tool's answer); the row's ``args`` are the agent's own words."""
    if not is_pipeline(transcript):
        return []
    outputs = transcript['pipeline_io']['outputs']
    trail = outputs.get(TOOL_TRAIL_KEY) if isinstance(outputs, Mapping) else None
    if isinstance(trail, list):
        outputs = {**outputs, TOOL_TRAIL_KEY: [{k: v for k, v in row.items() if k != 'result'} if isinstance(row, Mapping) else row
                                              for row in trail]}
    return string_leaves(outputs)


def string_leaves(value: Any) -> List[str]:
    """Every non-blank string in ``value``, in order: ``value`` itself, or the strings of a mapping or list at any depth."""
    leaves: List[str] = []
    stack: List[Any] = [value]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            if value.strip():
                leaves.append(value)
        elif isinstance(value, Mapping):
            stack.extend(reversed(list(value.values())))
        elif isinstance(value, list):
            stack.extend(reversed(value))
    return leaves


#: An audit row's FILE anchor: the path of a file the agent wrote (a report, a brief) whose content holds the row's
#: ``quote``. A cell graded on a written file has no reply turn that holds its evidence; the file is that evidence.
QUOTE_FILE = 'quote_file'


#: The SDK's file row (``plurai.file_rows.file_row``): ``name`` + ``text_preview``, cut at the SDK's preview limit and
#: then marked with this key, ``{'shown_chars': int, 'total_chars': int | None}``.
PREVIEW_TRUNCATED = '_preview_truncated'


def world_entities(item_dir: Optional[Path], transcript: Any) -> List[Any]:
    """Each turn's ``turn_world.delta.upserted`` entity, then each ``final_world_state.json`` entity."""
    found: List[Any] = []
    for turn in (transcript.get('turns') or []) if isinstance(transcript, Mapping) else []:
        delta = ((turn.get('turn_world') or {}).get('delta') or {}) if isinstance(turn, Mapping) else {}
        upserted = delta.get('upserted') if isinstance(delta, Mapping) else None
        found.extend(upserted.values() if isinstance(upserted, Mapping) else [])
    world = _read_json(item_dir / 'final_world_state.json') if item_dir is not None else None
    entities = world.get('entities') if isinstance(world, Mapping) else None
    found.extend(entities.values() if isinstance(entities, Mapping) else [])
    return found


def _file_text(entity: Any) -> Optional[Tuple[str, str]]:
    """``(key, text)`` of a file entity: a string ``path`` + ``content``, or the SDK file row's ``name`` + ``text_preview``."""
    if not isinstance(entity, Mapping):
        return None
    if isinstance(entity.get('path'), str) and isinstance(entity.get('content'), str):
        return entity['path'], entity['content']
    if isinstance(entity.get('name'), str) and isinstance(entity.get('text_preview'), str):
        return entity['name'], entity['text_preview']
    return None


def written_files(item_dir: Optional[Path], transcript: Any) -> Dict[str, List[str]]:
    """``{path: [content, …]}`` — every version of every file the item's world holds (:func:`world_entities`): an
    entity carrying a string ``path`` and ``content``, or an SDK file row's ``name`` and ``text_preview`` (keyed by
    ``name``)."""
    files: Dict[str, List[str]] = {}
    for entity in world_entities(item_dir, transcript):
        found = _file_text(entity)
        if found is not None:
            versions = files.setdefault(found[0], [])
            if found[1] not in versions:
                versions.append(found[1])
    return files


def cut_previews(item_dir: Optional[Path], transcript: Any) -> Dict[str, Mapping[str, Any]]:
    """``{name: _preview_truncated}`` — the SDK file rows whose ``text_preview`` the SDK cut."""
    return {
        entity['name']: entity[PREVIEW_TRUNCATED]
        for entity in world_entities(item_dir, transcript)
        if isinstance(entity, Mapping) and isinstance(entity.get('name'), str) and isinstance(entity.get(PREVIEW_TRUNCATED), Mapping)
    }


def file_key(files: Mapping[str, Any], path: str) -> Optional[str]:
    """The key of :func:`written_files` an audit row's ``quote_file`` names: the path itself, else its file name (an SDK
    file row is keyed by ``name``); None when neither is held."""
    if path in files:
        return path
    name = Path(path).name
    return name if name in files else None


def holds_quote(texts: Iterable[str], quote: str) -> bool:
    needle = ' '.join(quote.split())
    return any(needle in ' '.join(text.split()) for text in texts)


def clip(text: str, limit: int = 400) -> str:
    """``text`` on one line, cut to ``limit`` characters with an ellipsis."""
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _trail(transcript: Any, turn_idx: int) -> List[Mapping[str, Any]]:
    """The tool calls the agent made at ``turn_idx``: the dialog turn's ``agent_state`` trail, a pipeline's output trail."""
    if is_pipeline(transcript):
        outputs = transcript['pipeline_io']['outputs']
        trail = outputs.get(TOOL_TRAIL_KEY) if isinstance(outputs, Mapping) and turn_idx == PIPELINE_TURN else None
    else:
        turn = next((t for t in (transcript.get('turns') or []) if isinstance(t, Mapping) and t.get('turn_idx') == turn_idx), None)
        state = turn.get('agent_state') if isinstance(turn, Mapping) else None
        trail = state.get(TOOL_TRAIL_KEY) if isinstance(state, Mapping) else None
    return [call for call in trail or [] if isinstance(call, Mapping)]


def _holds_value(value: Any, cited: Any) -> bool:
    """``cited`` is ``value`` or a value nested in it (structural equality: key order and spacing aside)."""
    if value == cited:
        return True
    nested = value.values() if isinstance(value, Mapping) else value if isinstance(value, list) else ()
    return any(_holds_value(v, cited) for v in nested)


def cited_call(transcript: Any, turn_idx: int, quote: str) -> Optional[Mapping[str, Any]]:
    """The tool call at ``turn_idx`` an audit ``quote`` cites, else None: a WRITE the agent attempted is evidence no reply
    holds. A quote cites a call by its ``call_id``, or as the tool's name followed by its arguments as JSON — the whole
    ``args`` or one value nested in them (``create_x {"customer": …}`` for ``args = {"data": {"customer": …}}``). The
    args are the agent's own words; a call's ``result`` (the tool's answer) is never matched."""
    text = quote.strip()
    name, _, rest = text.partition(' ')
    try:
        cited = json.loads(rest) if rest.strip() else None
    except ValueError:
        cited = None
    for call in _trail(transcript, turn_idx):
        call_id = call.get('call_id')
        if isinstance(call_id, str) and call_id and call_id in text:
            return call
        if cited is not None and name == call.get('tool') and _holds_value(call.get('args'), cited):
            return call
    return None


class Run:
    """One local run dir, read once: its items, their current cells, their transcripts."""

    def __init__(self, out: Path) -> None:
        if not out.is_dir():
            raise ValueError(f'{out} is not a run directory')
        self.out = out
        self.dirs = dict(run_items(out))
        self._cells: Dict[str, Tuple[Dict[str, Any], str]] = {}
        self._transcripts: Dict[str, Any] = {}

    def transcript(self, item: str) -> Mapping[str, Any]:
        if item not in self._transcripts:
            d = self.dirs.get(item)
            doc = _read_json(d / 'transcript.json') if d is not None else None
            self._transcripts[item] = doc if isinstance(doc, Mapping) else {}
        return self._transcripts[item]

    def pipeline(self, item: str) -> bool:
        return is_pipeline(self.transcript(item))

    def thread(self, item: str) -> Optional[str]:
        """The item's current attempt (its transcript's ``metadata.thread_id``), None when it names none."""
        return red_cell_audit.thread_of(self.transcript(item)) if item in self.dirs else None

    def files(self, item: str) -> Dict[str, List[str]]:
        """The files the item's agent wrote (:func:`written_files`)."""
        return written_files(self.dirs.get(item), self.transcript(item))

    def cells(self, item: str) -> Tuple[Dict[str, Any], str]:
        if item not in self._cells:
            self._cells[item] = current_cells(self.dirs[item]) if item in self.dirs else ({}, 'absent')
        return self._cells[item]

    def revision(self, item: str) -> Optional[str]:
        """The eval set revision the item's CURRENT verdicts were scored at (``verdicts.json`` ``judged_against``)."""
        d = self.dirs.get(item)
        if d is None:
            return None
        revision = verdicts_file.judged_against(verdicts_file.read(d)).get('eval_revision')
        return revision if isinstance(revision, str) and revision else None

    def unit(self, item: str, policy: str, turn_idx: Optional[int]) -> Optional[Mapping[str, Any]]:
        return dict(units(self.cells(item)[0].get(policy))).get(turn_idx)

    def scoped(self, item: str, policy: str) -> Optional[bool]:
        """True for a scenario-scoped cell, False for a turn-based one, None when the item has no such cell."""
        found = units(self.cells(item)[0].get(policy))
        return None if not found else found[0][0] is None

    def red_units(self) -> List[Unit]:
        return [
            (item, policy, turn_idx)
            for item in sorted(self.dirs)
            for policy, cell in sorted(self.cells(item)[0].items())
            for turn_idx, unit in units(cell)
            if is_red(unit)
        ]

    def view(self, unit: Unit) -> Dict[str, Any]:
        """One unit as an auditor reads it: its own reply, the verdict's reasoning and quotes."""
        item, policy, turn_idx = unit
        d = self.dirs.get(item)
        cells, source = self.cells(item)
        cell = cells.get(policy) if isinstance(cells.get(policy), Mapping) else {}
        verdict = self.unit(item, policy, turn_idx) or {}
        row: Dict[str, Any] = {
            'item': item,
            'transcript': str((d / 'transcript.json').relative_to(self.out)) if d else None,
            'verdicts_from': source,
            'status': verdict.get('status'),
            'violated': verdict.get('violated'),
            'reasoning': verdict.get('reasoning') or cell.get('reasoning'),
            'quotes': [f.get('quote') for f in (verdict.get('findings') or cell.get('findings') or []) if isinstance(f, Mapping) and f.get('quote')],
        }
        # A pass the backend SET from an earlier audit's FALSE RED ruling of this same cell revision: ``rescored`` is what
        # the judges said. Shown so an auditor never reads the corrected verdict as the judges' own.
        correction = verdict.get('audit_correction') or cell.get('audit_correction')
        if isinstance(correction, Mapping):
            row['audit_correction'] = dict(correction)
        transcript = self.transcript(item)
        if is_pipeline(transcript):
            # The agent's whole output, whole: the auditor quotes it verbatim at turn PIPELINE_TURN.
            row['pipeline_outputs'] = transcript['pipeline_io']['outputs']
            row['quote_turn'] = PIPELINE_TURN
        elif turn_idx is not None:
            turns = transcript.get('turns') or []
            turn = next((t for t in turns if isinstance(t, Mapping) and t.get('turn_idx') == turn_idx), None)
            if turn is not None:
                row['user_message'] = turn.get('user_message')
                row['agent_message'] = turn.get('agent_message')
        return row


def evidence_turn(run: Run, item: str, row: Mapping[str, Any], turn_idx: Optional[int]) -> Optional[int]:
    """The turn a row's quote is checked at: the unit's own turn, else the row's ``quote_turn``, else
    :data:`PIPELINE_TURN` on a pipeline item; None when the row names none."""
    if turn_idx is not None:
        return turn_idx
    quote_turn = row.get('quote_turn')
    if _is_turn(quote_turn):
        return quote_turn
    return PIPELINE_TURN if run.pipeline(item) else None


def recorded_tool_answers(run: Run, item: str, turn_idx: Optional[int]) -> List[Any]:
    """The tool answers the run recorded at ``turn_idx`` (at every turn: None), as recorded: each ``tool_events.json``
    event's ``response`` of that turn and each trail call's ``result`` there."""
    d = run.dirs.get(item)
    events = (_read_json(d / 'tool_events.json') or {}) if d is not None else {}
    events = events.get('events') if isinstance(events, Mapping) else None
    answers = [e['response'] for e in events or [] if isinstance(e, Mapping) and turn_idx in (None, e.get('turn')) and e.get('response') is not None]
    transcript = run.transcript(item)
    turns = turn_idxs(transcript) if turn_idx is None else [turn_idx]
    return answers + [c['result'] for turn in turns for c in _trail(transcript, turn) if isinstance(c.get('result'), str)]


def tool_answers(run: Run, item: str, turn_idx: int) -> List[str]:
    """:func:`recorded_tool_answers` at ``turn_idx`` as text: a response that is not text is JSON-dumped."""
    return [a if isinstance(a, str) else json.dumps(a, ensure_ascii=False) for a in recorded_tool_answers(run, item, turn_idx)]


def judge_cited_tool_answer(run: Run, item: str, policy: str, turn_idx: Optional[int], turn: int, quote: str) -> bool:
    """A FALSE RED's tool-data anchor: the red unit's OWN judge finding at ``turn`` cites a quote ``quote`` contains, and
    ``quote`` is verbatim in a tool answer the run recorded at that turn (:func:`tool_answers`). The auditor then refutes
    the very tool evidence the red rests on — a missed action's premise, with no agent sentence to quote."""
    unit = run.unit(item, policy, turn_idx) or {}
    cited = [str(f.get('quote') or '') for f in unit.get('findings') or [] if isinstance(f, Mapping) and f.get('turn_idx') == turn]
    return any(c.strip() and ' '.join(c.split()) in ' '.join(quote.split()) for c in cited) and holds_quote(tool_answers(run, item, turn), quote)


def quote_miss(run: Run, item: str, row: Mapping[str, Any], turn_idx: Optional[int], *, tool_data: bool = False) -> Optional[str]:
    """None when the row's ``quote`` is verbatim in its anchor — the file :data:`QUOTE_FILE` names, else the reply at
    :func:`evidence_turn`, or a tool call the agent made there (:func:`cited_call`), or (``tool_data``: a FALSE RED only)
    the tool answer its judge cited (:func:`judge_cited_tool_answer`) — else why it is not."""
    quote = str(row.get('quote') or '').strip()
    if not quote:
        return 'no quote: a correction is never made without the reply that shows it'
    path = str(row.get(QUOTE_FILE) or '').strip()
    if path:
        files = run.files(item)
        key = file_key(files, path)
        if key is None:
            return f'{item} wrote no file {path!r}; the files its world holds: {", ".join(sorted(files)) or "none"}'
        if holds_quote(files[key], quote):
            return None
        cut = cut_previews(run.dirs.get(item), run.transcript(item)).get(key)
        if cut is not None:
            total = cut.get('total_chars')
            return (f'the quote is not in the preview of {key} the run recorded: it shows the first {cut.get("shown_chars")} of '
                    f'{total if total is not None else "an unknown number of"} characters; quote from the shown part')
        return f'the quote is not in {path} as {item} wrote it'
    turn = evidence_turn(run, item, row, turn_idx)
    if turn is None:
        return f'{row.get("policy")} is scenario-scoped on {item}: the row needs quote_turn, the turn its quote is from, or {QUOTE_FILE}, the file it is in'
    _, miss = record_finding.check_evidence(run.out, [f'{item}:{turn}:{quote}'])
    if miss and cited_call(run.transcript(item), turn, quote) is not None:
        return None  # the evidence is a tool call the agent made at that turn (:func:`cited_call`)
    if miss and tool_data:
        if judge_cited_tool_answer(run, item, str(row.get('policy')), turn_idx, turn, quote):
            return None
        miss = f'{miss}\n(nor does it hold the judge\'s own finding quote at turn {turn} verbatim, in a tool answer recorded there)'
    return miss or None


def grouped(run: Run, found: Iterable[Unit], extra: Optional[Mapping[Unit, Mapping[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Units grouped by ``(scenario, policy, turn_idx)``, every trial's unit side by side."""
    groups: Dict[Tuple[str, str, Optional[int]], List[Dict[str, Any]]] = {}
    for unit in sorted(set(found), key=_unit_key):
        item, policy, turn_idx = unit
        view = run.view(unit)
        view.update((extra or {}).get(unit) or {})
        groups.setdefault((item_scenario(item), policy, turn_idx), []).append(view)
    return [
        {'scenario': scenario, 'policy': policy, 'turn_idx': turn_idx, 'units': rows}
        for (scenario, policy, turn_idx), rows in sorted(groups.items(), key=lambda kv: _unit_key(kv[0]))
    ]


def _unit_key(unit: Sequence[Any]) -> Tuple[str, str, int]:
    return (unit[0], unit[1], -1 if unit[2] is None else unit[2])


# ---------------------------------------------------------------------------
# the audit
# ---------------------------------------------------------------------------


def read_rows(paths: Sequence[Path]) -> List[Dict[str, Any]]:
    """The rows of each file: a run's typed audit (``red_cell_audit.SCHEMA``) or a JSON list of rows."""
    rows: List[Dict[str, Any]] = []
    for path in paths:
        rows.extend(red_cell_audit.unwrap(_read_json(path), path))
    return rows


def audit_rows(run: Run, paths: Sequence[Path]) -> List[Dict[str, Any]]:
    """``paths``' rows when any is given, else the run's own typed audit (``red_cell_audit.path``) —
    ``red_cell_audit.AuditMissing`` (``audit missing: <path>``) when it has none."""
    return read_rows(paths) if paths else red_cell_audit.rows(run.out)


def superseded(row: Mapping[str, Any], current: Optional[str]) -> bool:
    """Whether audit ``row`` ruled an earlier attempt of its item: it names a ``thread_id`` and the item's current attempt
    (``current``, :meth:`Run.thread`) is another — the item was re-run since. Unknown on either side reads as current."""
    thread = row.get(red_cell_audit.THREAD_ID)
    return isinstance(thread, str) and bool(thread.strip()) and current is not None and thread.strip() != current


def current_rows(run: Run, rows: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """``rows`` less every row of an earlier attempt of its item (:func:`superseded`): the rulings of the run's conversations."""
    return [r for r in rows if not (isinstance(r.get('item'), str) and superseded(r, run.thread(normalize_item(r['item']))))]


def _turns(value: Any) -> Optional[List[Optional[int]]]:
    """A row's ``turn`` as a list of turns; ``[None]`` for null; None when it is neither."""
    if value is None:
        return [None]
    if _is_turn(value):
        return [value]
    if isinstance(value, list) and value and all(_is_turn(t) for t in value):
        return list(value)
    return None


def _row_turns(run: Run, item: str, policy: str, turn: Any) -> Tuple[Optional[List[Optional[int]]], str]:
    """``(the verdict units' turns a row names, '')``, or ``(None, why)`` when it names none of this run's units."""
    scoped = run.scoped(item, policy)
    turns = _turns(turn)
    if scoped is None:
        return None, f'{item} has no {policy} cell in this run'
    if turns is None:
        return None, f'turn {turn!r} is not an int, a list of ints, or null'
    if scoped:
        return [None], ''
    if turns == [None] and run.unit(item, policy, None) is not None:
        return [None], ''  # a red cell with no red turn is its own unit (:func:`units`)
    if None in turns:
        return None, f'{policy} is turn-based on {item}: the row must name the verdict unit\'s turn'
    return turns, ''


def ruled_units(run: Run, rows: Sequence[Mapping[str, Any]]) -> Tuple[Dict[Unit, List[Tuple[str, int]]], List[Dict[str, Any]]]:
    """``({unit: [(verdict, row index)]}, skipped rows)`` — each row split into the verdict units it rules. A FALSE RED /
    OUT OF SCOPE / FALSE PASS row stamped with another ``policy_fingerprint`` than its cell's rules nothing (skipped), nor
    does a row of an earlier attempt of its item (:func:`superseded`)."""
    ruled: Dict[Unit, List[Tuple[str, int]]] = {}
    skipped: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        verdict = _verdict(row)
        policy, item = row.get('policy'), row.get('item')
        if not policy or not isinstance(item, str):
            continue  # a claim row or an ENV row: no policy unit to rule
        if verdict not in VERDICTS:
            skipped.append({'row': index, 'why': f'verdict {row.get("verdict")!r} is none of {", ".join(VERDICTS)}'})
            continue
        item = normalize_item(item)
        if superseded(row, run.thread(item)):
            skipped.append({'row': index, 'why': f'ruled on an earlier attempt (thread {row[red_cell_audit.THREAD_ID]}); the item was re-run since'})
            continue
        turns, why = _row_turns(run, item, str(policy), row.get('turn'))
        if turns is None:
            skipped.append({'row': index, 'why': why})
            continue
        # A correction judged ONE revision of the cell: never carried onto a re-score of an edited policy (the backend's rule).
        stamped = row.get(red_cell_audit.POLICY_FINGERPRINT)
        judged = run.cells(item)[0][str(policy)].get(red_cell_audit.POLICY_FINGERPRINT)
        if verdict in (*RELEASES, FALSE_PASS) and stamped and judged and stamped != judged:
            skipped.append({'row': index, 'why': f'ruled at {red_cell_audit.POLICY_FINGERPRINT} {stamped}; the cell was re-judged since ({judged})'})
            continue
        for turn_idx in turns:
            ruled.setdefault((item, str(policy), turn_idx), []).append((verdict, index))
    return ruled, skipped


def _reason(row: Mapping[str, Any], verdict: str, unit: Unit) -> str:
    item, policy, turn_idx = unit
    where = f'{item} {policy}' + ('' if turn_idx is None else f' T{turn_idx}')
    surface = f' ({row["defect_surface"]})' if row.get('defect_surface') else ''
    why = ' '.join(str(row.get('reason') or row.get('clause') or '').split())
    return clip(f'red-cell audit {verdict}{surface} on {where}' + (f': {why}' if why else ''))


def build_overrules(run: Run, rows: Sequence[Mapping[str, Any]], run_upload_id: str) -> Dict[str, Any]:
    ruled, skipped = ruled_units(run, rows)
    overrules: List[Dict[str, str]] = []
    emitted: List[Dict[str, Any]] = []
    refused: List[Dict[str, Any]] = []
    kept: List[Dict[str, Any]] = []
    for unit in sorted(ruled, key=_unit_key):
        item, policy, turn_idx = unit
        verdicts = ruled[unit]
        names = {v for v, _ in verdicts}
        where = {'item': item, 'policy': policy, 'turn_idx': turn_idx}
        keeping = sorted(names & set(KEEPS))
        releasing = sorted(names & set(RELEASES))
        if keeping:
            if releasing:
                refused.append({**where, 'why': f'the audit also ruled this unit {" and ".join(keeping)}; that ruling stands until the audit changes it'})
            else:
                kept.append({**where, 'verdicts': keeping})
            continue
        if not releasing:
            continue  # a sampled green's ruling: no held red to release
        if len(releasing) > 1:
            refused.append({**where, 'why': f'rows rule this unit both {" and ".join(releasing)}'})
            continue
        verdict = releasing[0]
        recorded = run.unit(item, policy, turn_idx) or {}
        if verdict == FALSE_RED and not is_red(recorded):
            skipped.append({**where, 'why': 'FALSE RED on a unit the run did not record red: there is no red to release'})
            continue
        if verdict == OUT_OF_SCOPE and not is_pass(recorded):
            skipped.append({**where, 'why': 'OUT OF SCOPE on a unit the run did not record a pass: no dropped verdict to release'})
            continue
        gate_id = RELEASES[verdict]
        fp = fingerprint(gate_id, policy, {'run_upload_id': run_upload_id, 'scenario': item, 'policy': policy, 'turn_idx': turn_idx})
        row = rows[next(i for v, i in verdicts if v == verdict)]
        overrules.append({'gate_id': gate_id, 'fingerprint': fp, 'reason': _reason(row, verdict, unit)})
        emitted.append({**where, 'gate_id': gate_id, 'fingerprint': fp})
    return {'run_upload_id': run_upload_id, 'overrules': overrules, 'units': emitted, 'refused': refused, 'kept': kept, 'skipped': skipped}


# ---------------------------------------------------------------------------
# labels — prompt-cause policies only
# ---------------------------------------------------------------------------


def audit_true_reds(run: Run, rows: Sequence[Mapping[str, Any]]) -> Dict[str, set[str]]:
    """``{policy: scenarios}`` this run's audit ruled ``TRUE RED`` — only a row on an item of THIS run whose cell is still
    red there now, so another run's ruling (or one on a cell a correction cleared) never counts."""
    out: Dict[str, set[str]] = {}
    for row in rows:
        if _verdict(row) != TRUE_RED or not row.get('policy') or not row.get('item'):
            continue
        item, policy = normalize_item(str(row['item'])), str(row['policy'])
        if item in run.dirs and any(is_red(unit) for _, unit in units(run.cells(item)[0].get(policy))):
            out.setdefault(policy, set()).add(item_scenario(item))
    return out


def _verdict(row: Mapping[str, Any]) -> str:
    return red_cell_audit.verdict_of(dict(row))


def _cause(row: Mapping[str, Any]) -> str:
    return str(row.get('defect_surface') or '').strip().lower()


def prompt_policies(audit: Sequence[Mapping[str, Any]]) -> List[str]:
    """The policies with at least one ``prompt`` FALSE RED (or a sampled green's ``prompt`` FALSE PASS): the only
    ones that get pass samples and labels."""
    return sorted({str(r['policy']) for r in audit if r.get('policy') and _verdict(r) in (FALSE_RED, FALSE_PASS) and _cause(r) == PROMPT})


def _pass_digest(run: Run, unit: Unit) -> str:
    item, _, turn_idx = unit
    return hashlib.sha256(f'{run.out.name}|{item}|{"" if turn_idx is None else turn_idx}'.encode()).hexdigest()


def sample_passes(run: Run, policies: Sequence[str], n: int) -> List[Unit]:
    """Per policy, the ``n`` conclusive pass units whose digest sorts first — the same sample on every read of a run."""
    found: List[Unit] = []
    for policy in sorted(set(policies)):
        passes = [(item, policy, t) for item in sorted(run.dirs) for t, unit in units(run.cells(item)[0].get(policy)) if is_pass(unit)]
        found += sorted(passes, key=lambda u: _pass_digest(run, u))[:n]
    return found


# ---------------------------------------------------------------------------
# the green sample — an audit reads passes too
# ---------------------------------------------------------------------------

DOUBT, SPLIT, STAKES = 'doubt', 'split', 'stakes'
#: Pass units per stakes policy.
STAKES_PER_POLICY = 2
#: A judge's pass is in doubt when its self-consistency samples split (``agreement`` < 1) or its ``confidence`` is
#: under this; a code check reports neither below it.
DOUBT_CONFIDENCE = 0.95
#: One round of the green draw: a doubtful pass is the likeliest false one, so it takes half the sample.
GREEN_DRAW = (DOUBT, SPLIT, DOUBT, STAKES)
#: What makes a policy's passes worth reading: its red fails the scenario, or its declared severity says so.
STAKES_RULES: Tuple[Tuple[str, Callable[[Mapping[str, Any]], bool]], ...] = (
    ('veto', lambda body: policy_veto_capability(dict(body)) is True),
    ('severity', lambda body: str(body.get('severity') or '').strip().lower() in ('high', 'critical')),
)


def stakes_policies(bodies: Mapping[str, Any]) -> List[str]:
    """The eval set's policies, judge or code check, any :data:`STAKES_RULES` rule names."""
    return sorted(name for name, body in bodies.items() if isinstance(body, Mapping) and any(rule(body) for _, rule in STAKES_RULES))


def in_doubt(unit: Mapping[str, Any]) -> bool:
    """A pass its own judge was unsure of (:data:`DOUBT_CONFIDENCE`)."""
    agreement, confidence = unit.get('agreement'), unit.get('confidence')
    return (isinstance(agreement, (int, float)) and agreement < 1) or (isinstance(confidence, (int, float)) and confidence < DOUBT_CONFIDENCE)


def sample_greens(run: Run, stakes: Sequence[str], cap: int) -> Dict[Unit, str]:
    """``{unit: DOUBT | SPLIT | STAKES}`` — at most ``cap`` conclusive pass units, the same on every read of a run (GREENS)."""
    reds: Dict[Tuple[str, str], List[Unit]] = {}
    for unit in run.red_units():
        reds.setdefault((item_scenario(unit[0]), unit[1]), []).append(unit)
    found = {(item, policy, t): in_doubt(u) for item in sorted(run.dirs) for policy, cell in sorted(run.cells(item)[0].items()) for t, u in units(cell) if is_pass(u)}
    passes = list(found)
    split: List[Unit] = []
    for (scenario, policy), red in sorted(reds.items()):
        red_items, red_turns = {u[0] for u in red}, {u[2] for u in red}
        siblings = [u for u in passes if u[1] == policy and item_scenario(u[0]) == scenario and red_items - {u[0]}]
        if siblings:
            split.append(min(siblings, key=lambda u: (not found[u], u[2] not in red_turns, _pass_digest(run, u))))
    red_policies = {policy for _, policy in reds}
    staked: List[Unit] = []
    unread: List[Unit] = []  # the first unit of each stakes policy with no red: never left unread under the cap
    for policy in sorted(set(stakes)):
        mine = sorted((u for u in passes if u[1] == policy and u not in split), key=lambda u: _pass_digest(run, u))[:STAKES_PER_POLICY]
        staked += mine
        unread += mine[:1] if policy not in red_policies else []
    queues = {
        DOUBT: iter(sorted((u for u in passes if found[u]), key=lambda u: ((item_scenario(u[0]), u[1]) not in reds, _pass_digest(run, u)))),
        SPLIT: iter(sorted(split, key=lambda u: _pass_digest(run, u))),
        STAKES: iter(sorted(staked, key=lambda u: _pass_digest(run, u))),
    }
    sample: Dict[Unit, str] = {unit: STAKES for unit in sorted(unread, key=lambda u: _pass_digest(run, u))[: max(cap, 0)]}
    # Each round draws by GREEN_DRAW, so no class starves another under the cap; a unit an earlier draw took is skipped.
    while queues and len(sample) < cap:
        for why in GREEN_DRAW:
            unit = next((u for u in queues[why] if u not in sample), None) if why in queues and len(sample) < cap else None
            if unit is None:
                queues.pop(why, None)
            else:
                sample[unit] = why
    return sample


# ---------------------------------------------------------------------------
# known patterns — the suite's final rulings, a hint to the official run's audit
# ---------------------------------------------------------------------------

#: The decided rulings a pattern carries.
PATTERN_VERDICTS = (TRUE_RED, FALSE_RED, FALSE_PASS)
#: Per pattern: distinct reasons and example replies kept.
PATTERN_REASONS, PATTERN_EXAMPLES = 3, 2


def known_patterns(run: Run, rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """``{run, patterns}`` — the rulings still standing on this run (KNOWN PATTERNS), grouped by
    ``(policy, verdict, cause, eval_revision)``."""
    ruled, _ = ruled_units(run, rows)
    found: Dict[Tuple[str, str, Optional[str], str], Dict[str, Any]] = {}
    for unit in sorted(ruled, key=_unit_key):
        item, policy, turn_idx = unit
        names = {v for v, _ in ruled[unit]}
        if len(names) != 1 or not names <= set(PATTERN_VERDICTS):
            continue  # undecided, or ruled two ways
        (verdict,) = names
        recorded = run.unit(item, policy, turn_idx) or {}
        if not (is_pass(recorded) if verdict == FALSE_PASS else is_red(recorded)):
            continue  # no longer standing: a repair or a correction already moved it
        row = rows[ruled[unit][-1][1]]
        quote, at, revision = str(row.get('quote') or '').strip(), evidence_turn(run, item, row, turn_idx), run.revision(item)
        if not quote or at is None or not revision or record_finding.check_evidence(run.out, [f'{item}:{at}:{quote}'])[1]:
            continue
        cause = _cause(row) if verdict != TRUE_RED and _cause(row) in CAUSES else None
        pattern = found.setdefault((policy, verdict, cause, revision), {
            'policy': policy, 'verdict': verdict, 'cause': cause, 'eval_revision': revision, 'units': 0, 'reasons': [], 'examples': []})
        pattern['units'] += 1
        reason = ' '.join(str(row.get('reason') or row.get('clause') or '').split())
        if reason and reason not in pattern['reasons'] and len(pattern['reasons']) < PATTERN_REASONS:
            pattern['reasons'].append(reason)
        if len(pattern['examples']) < PATTERN_EXAMPLES:
            pattern['examples'].append({'item': item, 'turn': at, 'quote': quote})
    return {'run': run.out.name, 'patterns': [found[k] for k in sorted(found, key=lambda k: (k[0], k[1], k[2] or '', k[3]))]}


def attach_patterns(run: Run, groups: Sequence[Dict[str, Any]], patterns: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    """Each group gets ``known_patterns``: the patterns of its policy at the eval revision one of its items was scored at.
    ``{attached, stale}``: groups that got one, and patterns no item of this run was scored at."""
    revisions = {run.revision(item) for item in run.dirs}
    attached = 0
    for group in groups:
        at = {run.revision(u['item']) for u in group['units']}
        matching = [dict(p) for p in patterns if p.get('policy') == group['policy'] and p.get('eval_revision') in at]
        if matching:
            group['known_patterns'] = matching
            attached += 1
    return {'attached': attached, 'stale': sum(1 for p in patterns if p.get('eval_revision') not in revisions)}


def build_labels(run: Run, audit: Sequence[Mapping[str, Any]], reviews: Sequence[Mapping[str, Any]], run_upload_id: str, stage: str) -> Dict[str, Any]:
    """``{policies, rows, skipped}`` — one label row per verdict unit an audit or pass-review row of a prompt-cause
    policy rules. A FALSE RED needs a cause in :data:`CAUSES`; a decided row needs its verbatim reply ``quote`` (at the
    unit's turn, or ``quote_turn`` on a scenario-scoped cell), checked against the transcript. A later row wins its unit."""
    policies = prompt_policies(audit)
    labels: Dict[Unit, Dict[str, Any]] = {}
    skipped: List[Dict[str, Any]] = []
    sources = [('audit', i, r, (TRUE_RED, FALSE_RED, UNDECIDABLE, CONFIRMED_PASS, FALSE_PASS)) for i, r in enumerate(audit)]
    sources += [('pass-review', i, r, PASS_VERDICTS) for i, r in enumerate(reviews)]
    for source, index, row, allowed in sources:
        policy, item, verdict = row.get('policy'), row.get('item'), _verdict(row)
        if str(policy) not in policies or not isinstance(item, str) or verdict in (IN_SCOPE, OUT_OF_SCOPE):
            continue
        if superseded(row, run.thread(normalize_item(item))):
            continue  # it labelled an earlier attempt, not this run's conversation
        where = {'source': source, 'row': index, 'item': item, 'policy': policy}
        if verdict not in allowed:
            skipped.append({**where, 'why': f'verdict {row.get("verdict")!r} is none of {", ".join(allowed)}'})
            continue
        item = normalize_item(item)
        turns, why = _row_turns(run, item, str(policy), row.get('turn'))
        if turns is None:
            skipped.append({**where, 'why': why})
            continue
        cause = _cause(row) if verdict in (FALSE_RED, FALSE_PASS) else None
        if verdict == FALSE_RED and cause not in CAUSES:
            skipped.append({**where, 'why': f'a FALSE RED names its defect_surface, one of {", ".join(CAUSES)}; got {row.get("defect_surface")!r}'})
            continue
        cause = cause if cause in CAUSES else None
        if verdict in (FALSE_RED, FALSE_PASS) and cause in NOT_JUDGE_CAUSES:
            for turn_idx in turns:  # a later row wins its unit: an earlier row's label of it goes too
                labels.pop((item, str(policy), turn_idx), None)
            skipped.append({**where, 'why': f'a {cause} cause is not a judge error: it stays in the audit, never in the labels'})
            continue
        quote = str(row.get('quote') or '').strip()
        revision = run.revision(item)
        for turn_idx in turns:
            unit_where = {**where, 'item': item, 'turn_idx': turn_idx}
            recorded = run.unit(item, str(policy), turn_idx) or {}
            # A pass-review row, or an audit row ruling a sampled green, labels a pass; every other audit row a red.
            on_pass = source == 'pass-review' or verdict in (CONFIRMED_PASS, FALSE_PASS) or (verdict == UNDECIDABLE and is_pass(recorded))
            if not (is_pass(recorded) if on_pass else is_red(recorded)):
                skipped.append({**unit_where, 'why': f'the unit is not {"a pass" if on_pass else "red"} in this run\'s current verdicts'})
                continue
            at = evidence_turn(run, item, row, turn_idx)
            quote_turn = at if turn_idx is None else None
            if verdict != UNDECIDABLE:
                if not quote or at is None:
                    why = 'a decided row quotes the agent\'s reply verbatim (`quote`, and `quote_turn` on a scenario-scoped cell)'
                    skipped.append({**unit_where, 'why': why})
                    continue
                _, why = record_finding.check_evidence(run.out, [f'{item}:{at}:{quote}'])
                if why:
                    skipped.append({**unit_where, 'why': why})
                    continue
            if not revision:
                skipped.append({**unit_where, 'why': 'the item\'s current verdicts carry no eval_revision'})
                continue
            labels[(item, str(policy), turn_idx)] = {
                'policy': str(policy), 'eval_revision': revision, 'run_upload_id': run_upload_id, 'item': item, 'turn': turn_idx,
                'judge_verdict': 'pass' if on_pass else 'violated', 'label': LABELS[verdict], 'cause': cause,
                'quote': quote, 'quote_turn': quote_turn, 'reason': ' '.join(str(row.get('reason') or '').split()), 'stage': stage,
            }
    return {'policies': policies, 'rows': [labels[u] for u in sorted(labels, key=_unit_key)], 'skipped': skipped}


# ---------------------------------------------------------------------------
# a refusal
# ---------------------------------------------------------------------------


def guard_findings(doc: Any, *, held_only: bool = True) -> List[Mapping[str, Any]]:
    """The guard findings of a refused ``end_edit_session`` result (or of a bare findings list): by default only
    the ones it still holds on.

    The result lists every finding, the ones an overrule of that call lifted included; ``gate_authority.overrules``
    says which were ``applied``.
    """
    findings, lifted = doc, set()
    if isinstance(doc, Mapping):
        authority = doc.get('gate_authority') if isinstance(doc.get('gate_authority'), Mapping) else doc
        findings = authority.get('findings')
        lifted = {
            _lifted(o) for o in authority.get('overrules') or [] if isinstance(o, Mapping) and o.get('outcome') == 'applied'
        }
    if not isinstance(findings, list):
        raise ValueError('the refusal carries no gate_authority.findings list')
    return [
        f for f in findings
        if isinstance(f, Mapping) and f.get('gate_id') in GUARD_GATES and f.get('holds', True) and isinstance(f.get('evidence'), Mapping)
        and not (held_only and f.get('fingerprint') in lifted)
    ]


def _lifted(answer: Mapping[str, Any]) -> Any:
    """The finding an applied overrule released: its ``finding.fingerprint``, else the overrule's own."""
    finding = answer.get('finding') if isinstance(answer.get('finding'), Mapping) else {}
    sent = answer.get('overrule') if isinstance(answer.get('overrule'), Mapping) else {}
    return finding.get('fingerprint') or sent.get('fingerprint')


def held_findings(doc: Any) -> List[Mapping[str, Any]]:
    return guard_findings(doc)


def unevaluated_cause(now: Any) -> str:
    """``unscored`` when the unit's re-score could not run at all, ``judge`` when its judge did not answer."""
    return 'unscored' if str(now or '').startswith('unscored') else 'judge'


def _unevaluated_row(finding: Mapping[str, Any]) -> Dict[str, Any]:
    item, policy, turn_idx = _finding_unit(finding)
    now = (finding.get('observed') or {}).get('now')
    return {'item': item, 'policy': policy, 'turn_idx': turn_idx, 'fingerprint': finding.get('fingerprint'), 'now': now, 'cause': unevaluated_cause(now)}


def unevaluated_overrules(findings: Sequence[Mapping[str, Any]], reason: str) -> Tuple[List[Dict[str, str]], List[Dict[str, Any]]]:
    """``(overrules, refused)`` — one ``eval_edit.unevaluated`` overrule per unit, on the operator's ``reason``.

    The fingerprint is recomputed from the unit evidence; a served fingerprint that differs is refused rather than copied.
    """
    reason = ' '.join(reason.split())
    if len(reason) < OVERRULE_REASON_MIN_CHARS:
        raise ValueError(
            f'--overrule-unevaluated needs a reason of at least {OVERRULE_REASON_MIN_CHARS} characters saying what you verified '
            f'(e.g. "judge unavailable after two re-judges"); got {reason!r}'
        )
    overrules: List[Dict[str, str]] = []
    refused: List[Dict[str, Any]] = []
    seen = set()
    for finding in findings:
        if finding.get('gate_id') != UNEVALUATED:
            continue
        ev = finding['evidence']
        item, policy, turn_idx = _finding_unit(finding)
        fp = fingerprint(UNEVALUATED, policy, {'run_upload_id': str(ev.get('run_upload_id')), 'scenario': item, 'policy': policy, 'turn_idx': turn_idx})
        if fp in seen:
            continue
        seen.add(fp)
        if finding.get('fingerprint') not in (None, fp):
            refused.append({**_unevaluated_row(finding), 'why': f'the served fingerprint differs from the unit evidence\'s ({fp})'})
            continue
        overrules.append({'gate_id': UNEVALUATED, 'fingerprint': fp, 'reason': reason})
    return overrules, refused


def _finding_unit(finding: Mapping[str, Any]) -> Unit:
    ev = finding['evidence']
    return (str(ev.get('scenario')), str(ev.get('policy')), ev.get('turn_idx'))


def refusal_run_upload_id(findings: Sequence[Mapping[str, Any]]) -> Optional[str]:
    ids = {str(f['evidence'].get('run_upload_id')) for f in findings if f['evidence'].get('run_upload_id')}
    if len(ids) > 1:
        raise ValueError(f'the refusal names more than one run_upload_id ({", ".join(sorted(ids))})')
    return next(iter(ids), None)


def uncovered(run: Run, rows: Sequence[Mapping[str, Any]], findings: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    ruled, skipped = ruled_units(run, rows)
    open_units: Dict[Unit, Dict[str, Any]] = {}
    held_by_ruling: List[Dict[str, Any]] = []
    released_but_held: List[Dict[str, Any]] = []
    unevaluated: List[Dict[str, Any]] = []
    for finding in findings:
        if finding.get('gate_id') == UNEVALUATED:
            unevaluated.append(_unevaluated_row(finding))
            continue
        unit = _finding_unit(finding)
        where = {'item': unit[0], 'policy': unit[1], 'turn_idx': unit[2], 'gate_id': finding.get('gate_id'), 'fingerprint': finding.get('fingerprint')}
        names = {v for v, _ in ruled.get(unit, [])}
        if not names:
            open_units[unit] = {'gate_id': finding.get('gate_id'), 'fingerprint': finding.get('fingerprint'), 'observed': finding.get('observed')}
        elif names & set(RELEASES) and not names & set(KEEPS):
            released_but_held.append(where)
        else:
            held_by_ruling.append({**where, 'verdicts': sorted(names)})
    return {
        'run_upload_id': refusal_run_upload_id(findings),
        'held': len(findings),
        'uncovered': grouped(run, open_units, open_units),
        'held_by_ruling': held_by_ruling,
        'released_but_held': released_but_held,
        'unevaluated': unevaluated,
        'skipped': skipped,
    }


# ---------------------------------------------------------------------------
# attribution
# ---------------------------------------------------------------------------

ATTRIBUTION_PREFIX = 'attribution.'


def _authority(report: Any) -> Mapping[str, Any]:
    """``gate_authority`` of a saved ``job_status`` envelope (its ``detail``), of a bare detail, or the block itself."""
    doc = report.get('detail') if isinstance(report, Mapping) and isinstance(report.get('detail'), Mapping) else report
    if not isinstance(doc, Mapping):
        raise ValueError('the report is not a suite_diagnostics envelope')
    authority = doc.get('gate_authority') if isinstance(doc.get('gate_authority'), Mapping) else doc
    if not isinstance(authority.get('findings'), list):
        raise ValueError('the report carries no gate_authority.findings list')
    return authority


def attribution_overrules(report: Any, finding_rows: Mapping[str, Mapping[str, Any]], audit: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """``{overrules, lifted, kept, new}`` — see ATTRIBUTION in the module docstring."""
    authority = _authority(report)
    applied = {_lifted(o) for o in authority.get('overrules') or [] if isinstance(o, Mapping) and o.get('outcome') == 'applied'}
    confirmed: Dict[str, str] = {}
    for fid, row in sorted(finding_rows.items()):
        if row.get('status') == 'confirmed' and row.get('run_stale') is not True:
            for subject in record_finding.subjects_of(dict(row)):
                if subject.startswith('policy:'):
                    confirmed.setdefault(subject[len('policy:'):], fid)
    true_reds: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    for row in audit:
        if _verdict(row) == TRUE_RED and row.get('policy') and row.get('item'):
            true_reds.setdefault((normalize_item(str(row['item'])), str(row['policy'])), row)
    by_policy = {policy: (item, row) for (item, policy), row in sorted(true_reds.items())}
    overrules: List[Dict[str, str]] = []
    lifted: List[Dict[str, Any]] = []
    kept: List[Dict[str, Any]] = []
    for f in authority['findings']:
        if not (isinstance(f, Mapping) and str(f.get('gate_id') or '').startswith(ATTRIBUTION_PREFIX) and f.get('holds', True) and f.get('fingerprint')):
            continue
        evidence = f.get('evidence') if isinstance(f.get('evidence'), Mapping) else {}
        policy = str(evidence.get('policy') or f.get('subject') or '')
        item = normalize_item(str(evidence['item'])) if evidence.get('item') else None
        where = {'gate_id': f['gate_id'], 'policy': policy, **({'item': item} if item else {}), 'fingerprint': f['fingerprint']}
        reason = None
        if item is not None:
            row = true_reds.get((item, policy))
            if row is not None:
                reason = f'final red-cell audit ruled {item} {policy} TRUE RED: {row.get("quote") or row.get("reason") or ""}'
        elif policy in confirmed:
            reason = f'the agent\'s own red: confirmed agent finding {confirmed[policy]} names {policy} (policy:{policy}), so its reds count against the agent'
        elif policy in by_policy:
            item_of, row = by_policy[policy]
            reason = f'final red-cell audit ruled {policy} TRUE RED on {item_of}: {row.get("quote") or row.get("reason") or ""}'
        if reason is None:
            kept.append(where)
            continue
        overrules.append({'gate_id': str(f['gate_id']), 'fingerprint': str(f['fingerprint']), 'reason': clip(reason)})
        lifted.append({**where, 'already_applied': f['fingerprint'] in applied})
    return {'overrules': overrules, 'lifted': lifted, 'kept': kept, 'new': sum(1 for x in lifted if not x['already_applied'])}


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------


def _emit(doc: Any, out: Optional[str]) -> None:
    text = json.dumps(doc, indent=1, ensure_ascii=False)
    if out:
        Path(out).write_text(text + '\n', encoding='utf-8')
    else:
        print(text)


def _emit_groups(run: Run, found: Sequence[Unit], index: Dict[str, Any], split_dir: Optional[str], out: Optional[str],
                 extra: Optional[Mapping[Unit, Mapping[str, Any]]] = None, patterns: Optional[Sequence[Mapping[str, Any]]] = None) -> None:
    """``found`` grouped like ``list``; with ``split_dir``, one ``<scenario>.json`` each and an index of the files."""
    groups = grouped(run, found, extra)
    if patterns is not None:
        index['known_patterns'] = attach_patterns(run, groups, patterns)
    if not split_dir:
        _emit({**index, 'groups': groups}, out)
        return
    split = Path(split_dir)
    split.mkdir(parents=True, exist_ok=True)
    by_scenario: Dict[str, List[Dict[str, Any]]] = {}
    for group in groups:
        by_scenario.setdefault(group['scenario'], []).append(group)
    for scenario, rows in by_scenario.items():
        _emit({**index, 'scenario': scenario, 'groups': rows}, str(split / f'{scenario}.json'))
    _emit({**index, 'files': sorted(f'{scenario}.json' for scenario in by_scenario)}, out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n', 1)[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('list', 'overrules', 'uncovered'):
        p = sub.add_parser(name)
        p.add_argument('--run-dir', required=True, help='the local run dir ($OUT)')
        p.add_argument('--out', help='write the JSON here instead of stdout')
        if name == 'list':
            p.add_argument('--split-dir', help="also write one <scenario>.json per scenario here, so an audit reads one scenario's trials at a time")
            p.add_argument('--greens', type=int, help=f'also list at most this many sampled pass units (GREENS); required, >= 1: {GREENS_REQUIRED}')
            p.add_argument('--workdir', help='the dir holding policies_config.yaml, which names the stakes policies --greens samples; required')
            p.add_argument('--patterns', help="the suite's audit_patterns.json: attach its patterns at this run's eval revision (KNOWN PATTERNS); absent → none")
        else:
            p.add_argument('--audit', action='append', default=[], help="a red-cell audit rows file (repeatable); default: the run's own audit")
            p.add_argument('--refusal', action='append', help="a refused end_edit_session result (JSON), or a list of its findings; "
                           "overrules takes every refusal of the session (repeatable), uncovered reads the last")
        if name == 'overrules':
            p.add_argument('--run-upload-id', help='the newest finalized run linked to the eval set (else read off --refusal)')
            p.add_argument('--overrule-unevaluated', metavar='REASON',
                           help='also overrule every eval_edit.unevaluated unit the refusals name, with this reason (>= 20 characters)')
    p = sub.add_parser('patterns')
    p.add_argument('--run-dir', required=True, help='the local run dir ($OUT)')
    p.add_argument('--audit', action='append', default=[], help="this run's red-cell audit rows (repeatable); default: the run's own audit")
    p.add_argument('--out', help='write the JSON here instead of stdout')
    p = sub.add_parser('passes')
    p.add_argument('--run-dir', required=True, help='the local run dir ($OUT)')
    p.add_argument('--policy', action='append', required=True, help='a prompt-cause policy to sample (repeatable)')
    p.add_argument('--n', type=int, default=2, help='pass units per policy (default 2)')
    p.add_argument('--split-dir', help="also write one <scenario>.json per scenario here")
    p.add_argument('--out', help='write the JSON here instead of stdout')
    p = sub.add_parser('labels')
    p.add_argument('--run-dir', required=True, help='the local run dir ($OUT)')
    p.add_argument('--audit', action='append', default=[], help="a red-cell audit rows file (repeatable; a later row wins its unit); default: the run's own audit")
    p.add_argument('--pass-review', action='append', default=[], help='a pass-review rows file (repeatable)')
    p.add_argument('--run-upload-id', required=True, help="the run's finalized upload")
    p.add_argument('--stage', required=True, choices=STAGES)
    p.add_argument('--eval-set-id', required=True)
    p.add_argument('--out', required=True, help='the upload_eval_labels arguments file {eval_set_id, rows}')
    p = sub.add_parser('attribution')
    p.add_argument('--report', required=True, help='the saved suite_diagnostics job_status envelope')
    p.add_argument('--findings', required=True, help='agent_findings.md (its typed rows are read beside it)')
    p.add_argument('--audit', action='append', default=[], help='a final red-cell audit rows file (repeatable)')
    p.add_argument('--run-dir', help="the final run dir: its own audit, when no --audit is given")
    p.add_argument('--out', help='write the JSON here instead of stdout')
    args = ap.parse_args(argv)
    if args.cmd == 'list' and (args.greens is None or args.greens < 1 or not args.workdir):
        sub.choices['list'].error(f'list needs --greens <n> (n >= 1) and --workdir "$WORKDIR": {GREENS_REQUIRED}')
    if args.cmd == 'attribution':
        try:
            report = _read_json(Path(args.report))
            if report is None:
                raise ValueError(f'{args.report} is not readable JSON')
            audit = read_rows([Path(a) for a in args.audit])
            if not args.audit and args.run_dir:
                try:
                    audit = red_cell_audit.rows(Path(args.run_dir))
                except red_cell_audit.AuditMissing as exc:
                    print(f'red_cells: {exc} — no TRUE RED files a red back as the agent\'s; the confirmed findings still do', file=sys.stderr)
            if args.run_dir and Path(args.run_dir).is_dir():
                audit = current_rows(Run(Path(args.run_dir)), audit)  # a red ruled under a repaired world never overrules the env
            result = attribution_overrules(report, record_finding.read_rows(record_finding.store_path(Path(args.findings))), audit)
        except ValueError as exc:
            print(f'red_cells: {exc}', file=sys.stderr)
            return 2
        _emit(result, args.out)
        return 0
    try:
        run = Run(Path(args.run_dir))
        if args.cmd == 'list':
            found = run.red_units()
            index: Dict[str, Any] = {'run_dir': str(run.out), 'items': sorted(run.dirs), 'red_units': len(found)}
            bodies = eval_set_bodies(args.workdir)
            stakes = stakes_policies(bodies) if bodies is not None else []
            greens = {unit: {'sampled': why} for unit, why in sample_greens(run, stakes, args.greens).items()}
            index.update(green_units=len(greens), stakes_policies=stakes if bodies is not None else None)
            patterns = None
            if args.patterns:
                doc = _read_json(Path(args.patterns)) if Path(args.patterns).is_file() else {'patterns': []}
                if not isinstance(doc, Mapping) or not isinstance(doc.get('patterns'), list):
                    raise ValueError(f'{args.patterns} is not a red_cells.py patterns file')
                patterns = [p for p in doc['patterns'] if isinstance(p, Mapping)]
            _emit_groups(run, [*found, *greens], index, args.split_dir, args.out, greens, patterns)
            return 0
        if args.cmd == 'patterns':
            _emit(known_patterns(run, audit_rows(run, [Path(p) for p in args.audit])), args.out)
            return 0
        if args.cmd == 'passes':
            if args.n < 1:
                raise ValueError('--n must be at least 1')
            found = sample_passes(run, args.policy, args.n)
            _emit_groups(run, found, {'run_dir': str(run.out), 'policies': sorted(set(args.policy)), 'pass_units': len(found)}, args.split_dir, args.out)
            return 0
        rows = audit_rows(run, [Path(p) for p in args.audit])
        if args.cmd == 'labels':
            result = build_labels(run, rows, read_rows([Path(p) for p in args.pass_review]), args.run_upload_id, args.stage)
            _emit({'eval_set_id': args.eval_set_id, 'rows': result['rows']}, args.out)
            _emit({'policies': result['policies'], 'rows': len(result['rows']), 'skipped': result['skipped']}, None)
            return 0
        refusals = [_read_json(Path(p)) for p in args.refusal or []]
        if args.cmd == 'uncovered':
            if not refusals:
                raise ValueError('uncovered needs --refusal')
            _emit(uncovered(run, rows, held_findings(refusals[-1])), args.out)
            return 0
        every = [f for doc in refusals for f in guard_findings(doc, held_only=False)]
        run_upload_id = args.run_upload_id or (refusal_run_upload_id(every) if every else None)
        if not run_upload_id:
            raise ValueError('overrules needs --run-upload-id, or a --refusal whose findings name it')
        result = build_overrules(run, rows, run_upload_id)
        named = [f for f in every if f.get('gate_id') == UNEVALUATED]
        result['unevaluated'] = [_unevaluated_row(f) for f in named]
        if args.overrule_unevaluated is not None:
            if not refusals:
                raise ValueError('--overrule-unevaluated needs the --refusal(s) that named the units')
            extra, refused = unevaluated_overrules(named, args.overrule_unevaluated)
            result['overrules'] += extra
            result['refused'] += refused
        _emit(result, args.out)
        for row in result['refused']:
            print(f'red_cells: refused {row["item"]} {row["policy"]} T{row["turn_idx"]} — {row["why"]}', file=sys.stderr)
        return 0
    except ValueError as exc:
        print(f'red_cells: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
