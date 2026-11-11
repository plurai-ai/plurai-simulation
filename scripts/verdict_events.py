#!/usr/bin/env python3
"""Hand one verdict change to the SDK's single writer of a conversation's verdicts: `plurai verdicts <verb>`.

The plugin decides WHAT changes (a re-score's result, an audit's corrections); the SDK owns the file: its
completeness check, its history and its bytes. No plugin script writes a conversation's verdicts itself.

    plurai verdicts apply-rescore     --item-dir <item dir> --result <score_run result.json> --history-len <N>
    plurai verdicts apply-correction  --item-dir <item dir> --event <correction event.json>   (ONE unit per call)
    plurai verdicts import-legacy     --item-dir <item dir> --doc <converted document.json>

Every apply is built on the file as it is NOW: ``send`` reads the item's current ``history`` length through
``verdicts_file`` at the moment of the call — `--history-len` for a rescore, the event's ``history_len`` for a
correction — so a run of corrections on one item is a run of calls, each against the file the previous one left. What
keeps a stale correction out is its ``old`` state: the writer refuses it unless the unit is in that state now.

The runner is the one `resolve_plurai.py` chooses, required to offer the `verdicts` verb, so an SDK that predates
it is refused by the resolver with the upgrade line rather than failing on an unknown subcommand.

THE SCORES. Every write the SDK applies is followed by ``plurai scores refresh --run-dir <the item's run dir>``
(:func:`refresh_scores`): the SDK recomputes the run's ``scores`` block (``run_summary.json`` → ``scores``, schema
:data:`SCORES_SCHEMA`) from the verdicts as they now stand, so no reader ever holds a score the verdicts no longer
say. The plugin never computes a score: ``build_run_json``, ``release_verdict`` and the hand-off read that block.
"""

from __future__ import annotations

import functools
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

try:
    from _stdio import utf8_child_text
except ImportError:  # a copy run outside the plugin decodes its children as the platform does
    def utf8_child_text() -> dict:
        return {}

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _client_handshake  # noqa: E402 - the plugin's text for a version refusal

#: The SDK verb group, required of the resolved runner.
REQUIRE = 'verdicts'
#: The SDK verb group that recomputes the run's scores block, and the block's schema tag.
SCORES = 'scores'
SCORES_SCHEMA = 'scores-v1'
#: An attempt folder's second sample, and a listed trial's folder under the run dir.
RETRY_DIRNAME = 'stability_retry'
TRIAL_DIR = re.compile(r'trial_[0-9]+')
#: The run's summary, which holds the scores block.
RUN_SUMMARY = 'run_summary.json'
#: Each verb and the flag its payload file is passed with.
VERB_FLAGS = {'apply-rescore': '--result', 'apply-correction': '--event', 'import-legacy': '--doc'}


class WriterUnavailable(RuntimeError):
    """No installed `plurai` offers the `verdicts` verb (the resolver's stderr says why)."""


@functools.cache
def _resolved(verb: str) -> str:
    """The resolved `plurai` path that offers ``verb``; :class:`WriterUnavailable` when there is none."""
    import resolve_plurai  # noqa: PLC0415 - sibling script; only a send or a refresh needs it

    path, code = resolve_plurai.resolve((*resolve_plurai._DEFAULT_REQUIRED, verb))  # noqa: SLF001 - the default probe, plus the verb
    if path is None:
        raise WriterUnavailable(f'no installed `plurai` offers `{verb}` (resolve_plurai exit {code}); upgrade the SDK in the client venv')
    return path


def runner() -> str:
    """The resolved `plurai` path that offers the verdicts verb."""
    return _resolved(REQUIRE)


def history_len(item_dir: Path) -> int:
    """The length of the item's current verdicts ``history`` (``verdicts_file.read``; a missing file raises its error)."""
    import verdicts_file  # noqa: PLC0415 - sibling script: the one reader of a conversation's verdicts

    return len(verdicts_file.history(verdicts_file.read(item_dir)))


def argv(verb: str, item_dir: Path, payload: Mapping[str, Any], payload_path: Path) -> Tuple[list, Mapping[str, Any]]:
    """``(the writer's argv after the runner, the payload as sent)``: the history length read NOW for an apply."""
    args = [REQUIRE, verb, '--item-dir', str(item_dir), VERB_FLAGS[verb], str(payload_path)]
    if verb == 'apply-rescore':
        args += ['--history-len', str(history_len(item_dir))]
    elif verb == 'apply-correction':
        payload = {**payload, 'history_len': history_len(item_dir)}
    return args, payload


def _run(command: list) -> Tuple[int, str]:
    """Run the writer; ``(exit code, its message)``. The one subprocess call (tests replace it with a fake writer).

    An upgrade-required exit raises :class:`_client_handshake.VersionRefused` with the plugin's refusal text: the SDK's own
    words never reach a reader, and a refusal is not a writer outcome a caller can wrap in prose of its own."""
    proc = subprocess.run(command, capture_output=True, text=True, **utf8_child_text(), check=False)  # noqa: S603 - the resolved, verified SDK runner
    _client_handshake.raise_if_refused(proc)
    return proc.returncode, (proc.stderr or proc.stdout).strip()


def send(verb: str, item_dir: Path, payload: Mapping[str, Any]) -> Tuple[int, str]:
    """``(exit code, the writer's message)`` of one ``plurai verdicts <verb>`` on ``item_dir``. The payload travels as a
    temporary file (a result can exceed argv). A write the SDK applied is followed by :func:`refresh_scores` on the item's
    run dir; a refresh that fails is said on stderr and leaves the write's own exit code alone (the write landed, and
    ``build_run_json`` refreshes the block again before anything reads it). A version refusal of either call raises
    :class:`_client_handshake.VersionRefused`."""
    with tempfile.TemporaryDirectory(prefix='plurai-verdicts-') as tmp:
        doc = Path(tmp) / 'payload.json'
        try:
            args, payload = argv(verb, item_dir, payload, doc)
        except ValueError as exc:  # verdicts_file.VerdictsError: no current file to build on — what the writer refuses (3)
            return 3, str(exc)
        doc.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        code, message = _run([runner(), *args])
    if code == 0:
        run_dir, _ = item_home(Path(item_dir))
        _, why = refresh_scores(run_dir)
        if why:
            print(f'verdict_events: the scores of {run_dir} were not refreshed after this write ({why})', file=sys.stderr)
    return code, message


# ---------------------------------------------------------------------------------------------------------
# The scores block: `plurai scores refresh`
# ---------------------------------------------------------------------------------------------------------


def scores_home(trial_dir: Path) -> Tuple[Path, str]:
    """``(the run dir the SDK scores, the trial folder under it)`` for a trial's dir: ``$OUT`` itself is ``($OUT, '')``;
    ``$OUT/trial_<k>`` is ``($OUT, 'trial_<k>')`` when ``$OUT`` holds the run summary, else a run dir of its own."""
    d = Path(trial_dir)
    if TRIAL_DIR.fullmatch(d.name) and (d.parent / RUN_SUMMARY).is_file():
        return d.parent, d.name
    return d, ''


def item_home(item_dir: Path) -> Tuple[Path, str]:
    """:func:`scores_home` of an attempt folder's trial: ``<trial dir>/<item>`` or its ``stability_retry``."""
    d = Path(item_dir)
    return scores_home((d.parent if d.name == RETRY_DIRNAME else d).parent)


def scores_runner() -> str:
    """The resolved `plurai` that offers `scores`."""
    return _resolved(SCORES)


def _scores_call(command: list) -> Tuple[int, str, str]:
    """Run ``plurai scores refresh``; ``(exit code, stdout, stderr)``. The one subprocess call of a refresh (tests replace
    it with the helper's fake, which answers a block the test hands it — never one it computes).

    An upgrade-required exit raises :class:`_client_handshake.VersionRefused` with the plugin's refusal text."""
    proc = subprocess.run(command, capture_output=True, text=True, **utf8_child_text(), check=False)  # noqa: S603 - the resolved, verified SDK runner
    _client_handshake.raise_if_refused(proc)
    return proc.returncode, proc.stdout, proc.stderr.strip()


def refresh_scores(run_dir: Path) -> Tuple[Optional[Dict[str, Any]], str]:
    """``(the scores block, '')`` after ``plurai scores refresh --run-dir <run_dir>`` wrote it into the run's summary, or
    ``(None, why)``: no SDK offers the verb, the SDK refused (exit 2: no readable ``run_summary.json``), or it printed
    no block of :data:`SCORES_SCHEMA`. A version refusal raises :class:`_client_handshake.VersionRefused`."""
    try:
        command = [scores_runner(), SCORES, 'refresh', '--run-dir', str(run_dir)]
    except WriterUnavailable as exc:
        return None, str(exc)
    code, stdout, stderr = _scores_call(command)
    if code != 0:
        return None, f'`plurai scores refresh` exit {code}: {stderr or "no reason given"}'
    try:
        block = json.loads(stdout)
    except ValueError:
        return None, '`plurai scores refresh` printed no JSON block'
    if not isinstance(block, dict) or block.get('schema') != SCORES_SCHEMA:
        return None, f'`plurai scores refresh` printed a block of schema {block.get("schema") if isinstance(block, dict) else None!r}, not {SCORES_SCHEMA}'
    return block, ''


def conversation(block: Optional[Mapping[str, Any]], scenario: str, trial_dir: str) -> Optional[Dict[str, Any]]:
    """The block's entry for one attempt — ``scenario`` in trial folder ``trial_dir`` (``''``: trial 1) — or None."""
    entry = ((block or {}).get('scenarios') or {}).get(scenario)
    for row in (entry or {}).get('conversations') or ():
        if isinstance(row, dict) and row.get('trial_dir') == trial_dir:
            return row
    return None
