#!/usr/bin/env python3
"""`$WORKDIR/.eval_lane_pin.json` — the eval lane's coordinate frame.

WHY. `score_run` replays a SAVED run against the CURRENT eval set. That is a real verification
tier — judge tokens only, no agent re-run — but it verifies against a frame, and the frame can
move underneath it without a word: the scenario set gets regenerated, the harness gets edited,
the grading ground truth gets revised. The replay keeps returning verdicts, and they keep
looking current, because nothing in the result says which world they describe.

So the lane pins its frame at each upload and checks it before each replay. The pin is the
answer to "what are these saved artifacts a recording OF":

    {scenario_set_id, eval_set_id, run_upload_id, gt_revision, eval_revision,
     harness_fingerprint, corpus_ids, corpus_id, backend_build, out_dir, pinned_at}

FRESH vs STALE. `check` compares the pin against what is on disk NOW (and, for the grading
revision, against what the last `score_run` reported) and lists EVERY coordinate that moved,
headlined by the one that costs the most. Each axis declares its own weight in `REASONS`
(`REFUSING` = the recording is void, `WARN` = only the judgement of it moved), and every reader
that refuses projects that table instead of keeping a list — a warn-only axis inserted ahead of a
refusing one otherwise answers on its behalf, which is how a rebuilt corpus came to mask a dead
harness. The axes, coarsest first:

    scenario-set-moved            .scenario_set_id is not the set these artifacts came from
    corpus-moved                  the corpus coordinate moved since the sets (and the
                                  recording) stood on it. The coordinate is the SET of corpus
                                  ids the workdir attaches, read from all three marker shapes
                                  (`.knowledge_bases`, `.corpus_ids`, `.corpus_id`) by
                                  `corpus_coordinate.py`. Two references: it is UNEQUAL to the
                                  one pinned at the upload (a rebuild, or a knowledge base
                                  deleted), or it no longer contains every id the set's own
                                  detail records (`get_artifact_meta(<set>).detail.corpus_ids`,
                                  passed as --set-corpus-ids) — a set built on the old corpus
                                  keeps driving the critic while the ground truth moved under it
    eval-set-moved                .eval_set_id was replaced, not edited in place
    harness-fingerprint-changed   the harness that produced the transcripts is gone
    gt-revision-moved             the grading ground truth was revised since the upload
    eval-revision-moved           the eval set was edited IN PLACE since the upload (same
                                  .eval_set_id, different rubrics — the run's `eval_revision`
                                  from run_summary.json vs get_artifact_meta's detail.eval_revision)
    backend-build-moved           the BACKEND that generated and judged was redeployed between
                                  the pin and this check (`backend_identity().build_id`)

Two answers are neither: `corpus-unknown` and `backend-unknown`. Those two axes are the ones whose
reference or current value arrives from OUTSIDE the pin (`--set-corpus-ids`, `--backend-build`), so
"the caller asked and the side it needs cannot be read" is a real state — and with no third answer
it was reported as FRESH, which asserts the agreement nothing checked. They WARN and do not refuse
(`check` still exits 0, the sidecar is still written): nothing was measured to have moved. The backend's
twin types the same state `unknown` on `ScoreRunResult.corpus_coordinate`, "so that unknown is never
read as agreement".

Three more stalenesses have no on-disk tell at all, and all are declared rather than detected:

    run-superseded   a fresh RUN supersedes the saved artifacts while every coordinate above
                     still matches (an env fix changes no marker and no harness file);
                     `lane_pin.py stale` records it
    set-adopted      the qualification scenarios were ADOPTED into the final set and the eval
                     set relinked to it: the frame's set ids move while the recording behind
                     them does not; `lane_pin.py adopt` records it
    set-relinked     the final set was generated FRESH (adoption refused) and the eval set
                     relinked to it: the ids move AND the recording shares no scenario with the
                     set they now name; `lane_pin.py relink` records it

`adopt` and `relink` exist because leaving the pin alone was worse in both directions. Left
as-is it named a superseded set and `check` reported `scenario-set-moved` — a regeneration
anomaly — for what is the DESIGNED end state of the relink (observed live: a shipped suite whose
pin still named the qualification set). Re-pinned without the declaration every coordinate would
match, the frame would read FRESH, and the lane would write sidecars from a replay of transcripts
the relinked set never produced. They are two verbs because they record two different facts: an
adopted set still contains the recorded scenarios (the recorded scenarios are still in it), a
fresh one contains none of them — and one verb labelling both `set-adopted` told the operator an
adoption had happened when the loop had refused it.

The pin belongs to the PIN's own artifacts. `rescore.json` sidecars are written only while it is
fresh; a landing re-run deletes them and marks the pin stale; a rejoin rewrites it and re-opens
writing.

Usage:
    lane_pin.py write --scenario-set-id <id> --eval-set-id <id> --run-upload-id <id> \
        [--gt-revision N] [--eval-revision <hex>] [--harness-fingerprint <hex>] [--corpus-id <id>[,<id>]] \
        [--backend-build <build_id>] [--out-dir "$OUT"]
    lane_pin.py check [--gt-revision N] [--eval-revision <hex>] [--set-corpus-ids a,b] [--backend-build <build_id>]
                      [--refusing-only]                              # 0 = FRESH, 3 = STALE, 2 = unreadable
    lane_pin.py stale [--reason run-superseded]
    lane_pin.py adopt  [--scenario-set-id <id>] [--eval-set-id <id>]   # defaults: the markers
    lane_pin.py relink [--scenario-set-id <id>] [--eval-set-id <id>]   # same, for a FRESH final set
    lane_pin.py scope                                                  # the qualification-scope block, derived

EXIT CODES (`check`)
    0  FRESH — the frame still holds; a replay verifies against the world it claims to
    3  STALE — printed as `STALE <reason>`; the reason names what moved
    2  no pin, or one that cannot be read — the lane has no frame, which is not freshness

Exit 3 says something MOVED, not what it costs: the headline reason's WEIGHT says that, and a
caller that stops the lane on every 3 stops it on a warn-only axis too. `backend-build-moved` is
the one that makes this visible — a backend redeploy is routine inside a build and voids no
recording — so a reader acting on the exit code should read the headline and ask `weight()` (or
project `REFUSING_REASONS`, as the sidecar writer does) before treating a 3 as "the recording is
gone". Every axis is still reported, whatever the caller decides.

`--refusing-only` is that projection for a caller that branches on the exit code: 3 only when a
REFUSING reason is reported, 0 with a `MOVED (judgement only)` headline when every one weighs WARN.
Each axis line then carries its weight.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import client_pin  # noqa: E402 - sibling script: the client pin's filename and shape are owned there
import corpus_coordinate  # noqa: E402 - sibling script: all three corpus marker shapes are read there, not here
import deliverables  # noqa: E402 - sibling script: which run is THE scored run is read there
import harness_fingerprint  # noqa: E402 - sibling script: the workdir cascade and the fingerprint live there
import release_verdict  # noqa: E402 - sibling script: the rung and the report banner read one verdict there
from release_verdict import NOT_QUALIFIED, QUALIFIED, VALIDATED  # noqa: E402

PIN = '.eval_lane_pin.json'

#: What a staleness reason COSTS a reader, and there are only two answers.
#:
#: REFUSING — the recording itself is void. The transcripts are of a harness, or of scenarios,
#:            that no longer exist, so nothing derived from them is evidence about the current
#:            suite. A reader that publishes over it (a `rescore.json` sidecar, a `run.json`)
#:            must refuse instead.
#: WARN     — the recording still stands; what moved is the JUDGEMENT of it (the grading
#:            revision, the rubrics, the knowledge base under them). Say so and go on.
REFUSING = 'REFUSING'
WARN = 'WARN'

#: The drift `check` detects by reading the current state → its WEIGHT. Ordered coarsest-first,
#: which is the order `check` reports them in, so the reason names the reason to act rather than a
#: symptom of it.
#:
#: THE WEIGHT LIVES ON THE AXIS, and this table has NO DEFAULT — `weight()` raises on a reason it
#: does not carry. That is the whole point of the mapping. While this was a bare tuple every
#: reader kept its own hand-written list of which reasons refuse, and `staleness()` returned the
#: FIRST moved axis in tuple order: the night `corpus-moved` was inserted (WARN) ahead of
#: `harness-fingerprint-changed` (REFUSING), a rebuilt corpus started MASKING a dead harness, and
#: `write_rescore_sidecar.py` (now `apply_rescore.py`) published a sidecar over transcripts of a `simulate.py` that no
#: longer existed — exit 0 where the same frame with the corpus id left alone exits 5. A new axis
#: can no longer be added without stating what it costs.
REASONS: dict[str, str] = {
    'scenario-set-moved': REFUSING,
    'corpus-moved': WARN,
    'harness-fingerprint-changed': REFUSING,
    'gt-revision-moved': WARN,
    'eval-set-moved': WARN,
    'eval-revision-moved': WARN,
    # LAST because coarseness orders by "which reason best explains the others", and a redeploy
    # explains none of them: it moves no marker, no fingerprint and no revision. WARN because
    # the recording still stands — what changed is the machinery that generated and judged it.
    'backend-build-moved': WARN,
    # LAST for the same reason, and WARN for the same reason: a source patch moves no marker and
    # no revision, and the recording still stands — what changed is the code that judged it.
    'backend-source-moved': WARN,
}

#: The stalenesses that have no on-disk tell — declared by `stale` / `adopt` / `relink`, never
#: inferred. A CLOSED vocabulary: every reader branches on these strings, so a reason typed
#: freehand at a call site would be one no reader knows. Weighted on the same axis as the
#: detected ones, for the same reason: `run-superseded` is declared BEFORE a full re-run, so the
#: artifacts a sidecar would sit beside are the new run's — the recording moved, not the judgement.
DECLARED = 'run-superseded'
ADOPTED = 'set-adopted'
RELINKED = 'set-relinked'
DECLARED_REASONS: dict[str, str] = {DECLARED: REFUSING, ADOPTED: WARN, RELINKED: WARN}

#: The `set_lineage` vocabulary — which verb re-pointed the ids, spelled as the verb names so
#: `_repoint` records them and `licensed_anchor` weighs them against the same words.
LINEAGE_ADOPT = 'adopt'
LINEAGE_RELINK = 'relink'
SET_LINEAGES = (LINEAGE_ADOPT, LINEAGE_RELINK)

#: The corpus axis had something to compare against and one SIDE could not be read. Not drift and
#: not freshness — the third answer, which this frame did not have. Every other axis can honestly
#: say nothing when a side is unknown, because its reference IS the pin: if the pin recorded no
#: `gt_revision`, no comparison was ever asked for. The corpus axis is the one with a reference
#: that arrives from OUTSIDE the pin (`--set-corpus-ids`, the set's own `detail.corpus_ids`), so
#: "the caller asked and the workdir cannot answer" is a real state, and reporting it as FRESH
#: told the operator their recording stands on the corpus the set does. The backend's twin
#: (`ScoreRunResult.corpus_coordinate`) types the same state `unknown` "so that unknown is never
#: read as agreement" and warns. WARN-weight, like it: `check` still exits 0 and the sidecar is
#: still written — nothing was measured to have moved.
UNKNOWN_CORPUS = 'corpus-unknown'

#: The caller ASKED for a backend comparison (it passed `--backend-build`) and one side is not a
#: build. Same third answer as `corpus-unknown`, at the same weight — but the trigger differs, so
#: do not read the two as one rule: the corpus axis asks whether a REFERENCE exists, this one asks
#: whether the caller asked at all (see `_backend_unknown`; both sides unknown still counts).
#:
#: Here "not a build" is not a rare state but the DEFAULT one: this script is stdlib-only and makes
#: no network call, so the build arrives as a CLI argument, and a failed identity fetch, a 401, or a
#: backend that predates `build_id` all hand it `''`. Compared as a value, `''` is unequal to every
#: real build, so the frame would report `backend-build-moved` on every check for the rest of the
#: build — the exact failure `cmd_write` already refuses for an empty `scenario_set_id`. `write`
#: normalizes an empty (or unsubstituted-placeholder) build to `unknown`; both sides read it as
#: "nobody looked".
UNKNOWN_BACKEND = 'backend-unknown'

#: The same third answer for the SOURCE axis. It is a separate reason and not a
#: widening of the one above because the two axes answer different questions — "was the image
#: redeployed" and "was the code under it patched" — and an arc can know one and not the other:
#: boxes can report the same `build_id` while serving different graders.
#:
#: WITHOUT it the axis was inert for the whole rollout window and said nothing about being inert. A
#: pin written before `backend_source` existed carries `unknown`; checked against a patched box it
#: printed `FRESH` exit 0 with no tell, because `moved_coordinates`' "an unknown side is not drift"
#: branch is silent by design and `cannot_tell` only ever took `backend_build`. The skills tell the
#: model `'' if you have none — same rule` for both flags, and it was NOT the same rule: `''`
#: yielded `backend-unknown` on one axis and silence on the other. Same rule now.
UNKNOWN_BACKEND_SOURCE = 'backend-source-unknown'

#: What an unrecorded coordinate reads as ON DISK. A word, not `null`, and not the empty string:
#: the pin is read by eye as often as by code, and `backend_build: null` says "this version has no
#: such axis" while `unknown` says "the axis exists and nobody could answer it".
UNKNOWN = 'unknown'

#: The pin DECLARES a staleness whose reason this version has no weight for. The pin is a file on
#: disk that outlives the code that wrote it (`_pinned_corpus` reads two generations of the corpus
#: field for exactly that reason), so `stale_reason` is untrusted INPUT, not a literal from a call
#: site — a pin written by a newer plugin, or edited by hand, carries a word this vocabulary does
#: not have. Handing it straight to `heaviest` fired `weight()`'s deliberate KeyError, which
#: guards against a NEW AXIS landing in the CODE without a cost, at a reader instead: `check` died
#: with a traceback and exit 1 where it had reported `STALE` and exit 3 — losing the refusing
#: harness axis that had moved alongside it — and `write_rescore_sidecar` died where it had
#: refused with exit 5. REFUSING, never WARN: something declared this frame stale for a reason
#: this version cannot read, and the lane's standing rule is that unknown is never read as
#: agreement.
UNKNOWN_DECLARED = 'declared-reason-unrecognized'

#: EVERY reason this frame can report, detected or declared, with its weight. The one table the
#: whole lane asks; `weight()` is how it is asked.
WEIGHTS: dict[str, str] = {
    **REASONS, **DECLARED_REASONS,
    UNKNOWN_CORPUS: WARN, UNKNOWN_BACKEND: WARN, UNKNOWN_BACKEND_SOURCE: WARN, UNKNOWN_DECLARED: REFUSING,
}


def weight(reason: str) -> str:
    """`REFUSING` or `WARN` for one staleness reason. RAISES on a reason with no declared weight.

    No default, deliberately. A default would let a new axis land silently at whichever weight the
    default happened to be — which is how a WARN-only `corpus-moved` came to sit ahead of the
    refusing harness axis and mask it. An unweighted reason is a vocabulary bug, and it stops the
    reader that would otherwise act on it wrongly.

    That makes this a rule about CODE, so a reason that arrives as DATA is normalized before it
    gets here — `declared_staleness` maps a `stale_reason` off a pin this version does not know
    onto `UNKNOWN_DECLARED`. Never relax the raise to cover that case: the raise is the guard, and
    the boundary is where an untrusted string stops being one.
    """
    try:
        return WEIGHTS[reason]
    except KeyError:
        raise KeyError(
            f'{reason!r} has no declared weight in lane_pin.WEIGHTS. Every staleness reason must say whether it '
            f'voids the RECORDING ({REFUSING}) or only the judgement of it ({WARN}); readers project this table '
            'rather than keeping their own lists.'
        ) from None


#: The reasons that void the BEHAVIOUR record — the projection every refusing reader takes,
#: in place of a second (and third) hand-written list. In table order.
REFUSING_REASONS: tuple[str, ...] = tuple(name for name, cost in WEIGHTS.items() if cost == REFUSING)

#: The same projection narrowed to the axes `moved_coordinates` can DETECT, for the readers that
#: only ever consult on-disk drift (`build_run_json.pinned_run_staleness` — a declared staleness
#: has no coordinate for it to compare).
REFUSING_DETECTED_REASONS: tuple[str, ...] = tuple(name for name, cost in REASONS.items() if cost == REFUSING)

_EXIT_UNREADABLE = 2
_EXIT_STALE = 3


def _workdir(explicit: str | None) -> Path:
    """`--workdir`, then `$WORKDIR`, then the shared resolver cascade — the one in harness_fingerprint."""
    return harness_fingerprint.resolve_workdir(explicit)


def _marker(workdir: Path, name: str) -> str | None:
    try:
        value = (workdir / name).read_text(encoding='utf-8').strip()
    except OSError:
        return None
    return value or None


def _current_fingerprint(workdir: Path) -> str:
    """Recompute it, never read the stored marker.

    The marker is written by the same step that writes the pin, so comparing the two compares a
    value with itself — it would agree across exactly the harness edit this check exists to catch.

    RAISES (OSError) when a harness file cannot be read. Swallowing that into "unknown" made an
    uncomputable hash read as "did not move" — and the aggregator then built run.json over a
    recording nobody could verify. Unknown is for a value nobody LOOKED for; a value that could
    not be read is the caller's refusal to make.
    """
    return str(harness_fingerprint.fingerprint(workdir, harness_fingerprint.detect_lang(workdir))['fingerprint'])


def _read_json(path: Path) -> tuple[dict | None, str | None]:
    """`(document, why-not)` — exactly one of them is None.

    THREE readers in this module describe a file they could not read, and each spelled its own
    exception ladder. The split that matters is the same everywhere: ABSENT is a fact about the
    flow (nothing wrote it yet) and every other failure is a fact about the file. Callers keep
    their own wording; this keeps the ladder in one place.
    """
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None, None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, f'{type(exc).__name__}: {exc}'
    return (doc, None) if isinstance(doc, dict) else (None, 'it does not read as a JSON object')


def read_pin(workdir: Path) -> dict | None:
    """The pin as a mapping, or None when nothing readable is there.

    Through `_read_json`, which exists to keep this ladder in one place — and which this reader
    skipped. It caught `OSError` and `JSONDecodeError` but not `UnicodeDecodeError`, so a pin whose
    bytes are not UTF-8 killed `anchor` with a traceback instead of landing it on the floor rung:
    the one shape where the verb refuses is the one where it could not refuse at all.
    """
    return _read_json(workdir / PIN)[0]


def _pinned_corpus(pin: dict) -> frozenset[str] | None:
    """The corpus coordinate the RECORDING stood on, or None when the pin records none.

    Two shapes because pins outlive the code that wrote them: `corpus_ids` is the coordinate this
    version pins (a list, because a multi-knowledge-base agent stands on several corpora at once),
    and a bare `corpus_id` string is what a pin written before that carries. An older pin is read,
    never re-interpreted — it named exactly one corpus and that is what it is compared as.
    """
    recorded = pin.get('corpus_ids')
    if isinstance(recorded, list):
        ids = {str(value).strip() for value in recorded if str(value).strip()}
        return frozenset(ids) if ids else None
    single = pin.get('corpus_id')
    return frozenset({str(single).strip()}) if isinstance(single, str) and single.strip() else None


def build_stamp(value: object) -> str | None:
    """One backend build coordinate, normalized: the build, or `None` when this side is not one.

    THE VALUE ARRIVES AS A CLI ARGUMENT, so it arrives however the caller's substitution went.
    Three shapes are all "could not answer" and none of them is a build:

    * `''` — the identity fetch failed, answered 401, or this backend predates `build_id`. The
      documented invocation reads it with a command substitution, which yields `''` on every one
      of those, exactly as `$(cat .scenario_set_id)` does for a missing marker.
    * `unknown` — read back off a pin `write` normalized, or handed straight through by a caller
      that had already normalized it.
    * `<build_id>` — an UNSUBSTITUTED placeholder. Every MCP-derived argument in this plugin is
      written as `<…>` for the model to replace, and `assert-backend-identity.sh` carries the same
      guard for the same argument after one shipped through unsubstituted: compared as a value it
      is unequal to every real build, which is a manufactured red on a coordinate nobody read.
    * `{…}` — the whole `backend_identity()` OBJECT, where this wants one field of it. Refused by
      SHAPE, exactly as `assert-backend-identity.sh` selects its comparison mode: the object
      carries `started_at`, which moves on every backend restart, and `instance_id`, which moves when
      the box is replaced (and, off E2B, on every restart), so pinning it would report
      `backend-build-moved` on a box that merely bounced.

    The `unknown` compare is case-insensitive because the pin is documented as hand-editable and
    read by eye: a hand-typed `Unknown` is the same statement, and comparing it as a build would
    manufacture the drift this function exists to prevent.

    ONE answer for all of them — `None` — which is what keeps the comparison in
    `moved_coordinates`' existing "an unknown side is not drift" branch instead of adding a second
    rule beside it. WHETHER THE CALLER ASKED is a different question and is not answered here: it
    is `backend_build is None` at the raw argument, in `_backend_unknown`, so a reader that
    predates the flag (`apply_rescore.py`, `build_run_json.py`) is told nothing at all
    rather than `backend-unknown`. The two sites that WRITE the coordinate spell the on-disk word
    themselves (`… or UNKNOWN`): a pin says `unknown`, a comparison says nothing.
    """
    text = '' if value is None else str(value).strip()
    if not text or text.casefold() == UNKNOWN or (text.startswith('<') and text.endswith('>')) or text.startswith('{'):
        return None
    return text


def _backend_unknown(pin: dict, backend_build: str | None) -> str | None:
    """The sentence for a backend comparison that was ASKED FOR and could not be made, else None.

    Asked for = the caller passed `--backend-build` at all. Could not be made = either side is not
    a build. Both sides unknown still counts: the caller asked, and being told nothing is what the
    third answer exists for — `corpus-unknown`'s "nothing to compare is NOT unknown" carve-out is
    about a reference the caller never supplied, which here is the `None` case above.

    Reported rather than swallowed because the whole point of the axis is evidence integrity: an
    arc that cannot name the build it ran against cannot tell a redeploy from a held-constant box.
    """
    return _backend_axis_unknown(
        pin, backend_build, field='backend_build', noun='backend image build',
        absent='a failed identity fetch, a 401, or a backend predating `build_id`', question='the backend was REDEPLOYED',
    )


def _backend_source_unknown(pin: dict, backend_source: str | None) -> str | None:
    """The same sentence for the SOURCE axis — the one a per-file patch moves.

    ONE rule for both flags, which is what the skills already tell the model they are. Written as
    its own function rather than folded into the one above because the QUESTION differs: a build
    comparison answers "was it redeployed", a source comparison "was the code under it patched",
    and an operator handed the wrong noun looks in the wrong place.
    """
    return _backend_axis_unknown(
        pin, backend_source, field='backend_source', noun='backend source',
        absent='a failed identity fetch, a 401, or a backend predating `source_id`', question='the source was PATCHED',
    )


def _backend_axis_unknown(pin: dict, value: str | None, *, field: str, noun: str, absent: str, question: str) -> str | None:
    """The shared body of the two sentences above. One producer per question, one rule for both."""
    if value is None:
        return None
    pinned, current = build_stamp(pin.get(field)), build_stamp(value)
    if pinned is not None and current is not None:
        return None
    reason = (f'the pin records no {noun} (written before the axis existed, or the identity fetch failed at pin time)'
              if pinned is None else f'the pin records {noun} {pinned}')
    reading = f'this check was handed none ({absent})' if current is None else f'this check was handed {current}'
    return f'{reason} and {reading}, so whether {question} under this recording cannot be checked'


def _corpus_axis(pin: dict, workdir: Path, set_corpus_ids: list[str] | None) -> tuple[object, object]:
    """`(reference, current)` for the corpus coordinate — the pinned corpora, or the SET's own
    corpus ids when the caller passed them and one of them is no longer attached here.

    Two references for one axis because they catch two different rebuilds, and they are compared
    by two different rules because the two sides MEAN two different things:

    * **the pin** — both sides are this workdir's own coordinate, recorded at two times, so drift
      is set INEQUALITY. A subset test would call `{A,B}` → `{A}` fresh, and that is a knowledge
      base being DELETED out from under the recording.
    * **the set's `detail.corpus_ids`** — the backend records "the corpus id(s) its SHIPPED scenarios
      were grounded on … Not the attached knowledge bases: a two-KB agent whose scenarios all hit
      one base stands on one corpus". So it is a SUBSET of what a workdir attaches by design, and
      inequality here would report drift on every check a multi-KB customer ever ran. The question
      it can answer is containment: does the workdir still attach every corpus the set stands on.

    Unknown on either side is not drift; that is `_corpus_unknown`'s answer, not this one's.
    """
    current = corpus_coordinate.corpus_coordinate(workdir)
    pinned = _pinned_corpus(pin)
    # Rendered through the shared reader's own renderer, so an EMPTY coordinate reads `(none)` on
    # both halves rather than as the blank string one of them used to print after `current `.
    if pinned is not None and current is not None and pinned != current:
        return corpus_coordinate.render(pinned), corpus_coordinate.render(current)
    if set_corpus_ids and current is not None and not set(set_corpus_ids) <= current:
        # The set's ids keep the order the CALLER passed them in — they are that caller's list,
        # echoed back so it can be matched against what it sent, not this workdir's coordinate.
        return ','.join(set_corpus_ids), corpus_coordinate.render(current)
    return None, None


def _corpus_unknown(pin: dict, workdir: Path, set_corpus_ids: list[str] | None) -> str | None:
    """The sentence for a corpus comparison that was ASKED FOR and could not be made, else None.

    Asked for = there is a reference: the pin recorded a coordinate, or the caller passed the
    set's own ids. Could not be made = no corpus marker shape in the workdir is readable (see
    `corpus_coordinate`; an agent with `.knowledge_bases` and no `.corpus_id` is answerable). Both together are the backend's `corpus_coordinate: 'unknown'` — one side
    records no readable coordinate — and the reason it is typed there is the reason it is
    reported here: with no third answer, the axis answered FRESH, which asserts the agreement it
    could not check.

    Nothing to compare is NOT unknown. A pin with no corpus and no `--set-corpus-ids` never asked
    a question, and an agent with no corpus at all would otherwise be told on every check that its
    frame cannot be verified.
    """
    reading = corpus_coordinate.read_coordinate(workdir)
    if reading.ids is not None:
        return None
    cannot = (f'{", ".join(reading.unreadable)} cannot be parsed' if reading.unreadable
              else f'the workdir has none of {", ".join(corpus_coordinate.MARKERS)}')
    if (pinned := _pinned_corpus(pin)) is not None:
        return (f'the pin records corpus {",".join(sorted(pinned))} and {cannot}, so whether the '
                'corpus was rebuilt under this recording cannot be checked')
    if set_corpus_ids:
        return (f'the set stands on {",".join(set_corpus_ids)} and the pin records no corpus while {cannot}, so '
                'whether this recording stands on the same knowledge base cannot be checked')
    return None


def cannot_tell(pin: dict, workdir: Path, set_corpus_ids: list[str] | None,
                backend_build: str | None, backend_source: str | None = None) -> dict[str, str]:
    """`{reason: why}` for every comparison that was ASKED FOR and could not be made.

    ONE producer for every third answer — the tuple below is the whole list, so a new axis is one
    entry here rather than a branch in `_reported_reasons` and a second branch in `cmd_check`.
    (`backend-source-unknown` was the axis that proved the point by NOT being added: it shipped
    with a `moved_coordinates` entry and no third answer, so it was silently inert.) None of these
    is drift and none is freshness: they ride both outcomes and change the exit code of neither.

    `backend_source` defaults to None so a caller that predates the flag is told nothing at all
    rather than `backend-source-unknown` — the same rule its sibling has.
    """
    answers = ((UNKNOWN_CORPUS, _corpus_unknown(pin, workdir, set_corpus_ids)),
               (UNKNOWN_BACKEND, _backend_unknown(pin, backend_build)),
               (UNKNOWN_BACKEND_SOURCE, _backend_source_unknown(pin, backend_source)))
    return {reason: why for reason, why in answers if why is not None}


def moved_coordinates(pin: dict, workdir: Path, gt_revision: int | None, eval_revision: str | None = None,
                      set_corpus_ids: list[str] | None = None, backend_build: str | None = None,
                      backend_source: str | None = None) -> dict[str, tuple[object, object]]:
    """EVERY detected coordinate that moved — `{reason: (pinned, current)}`, in `REASONS` order.

    All of them, not the first: a reader acting on ONE axis (the run aggregator refuses on the
    harness axis alone) must not be blinded by a coarser axis that happens to sort earlier — a
    regenerated scenario set would otherwise hide the fact that the harness is gone too. Both
    values ride along so the refusal can NAME them rather than restate the reason.
    """
    checks = {
        'scenario-set-moved': (pin.get('scenario_set_id'), _marker(workdir, '.scenario_set_id')),
        'corpus-moved': _corpus_axis(pin, workdir, set_corpus_ids),
        'harness-fingerprint-changed': (pin.get('harness_fingerprint'), _current_fingerprint(workdir)),
        'gt-revision-moved': (pin.get('gt_revision'), gt_revision),
        'eval-set-moved': (pin.get('eval_set_id'), _marker(workdir, '.eval_set_id')),
        # The judge-side axis: an in-place rubric edit keeps .eval_set_id and moves this.
        'eval-revision-moved': (pin.get('eval_revision'), eval_revision),
        # The BACKEND axis. Both sides go through `build_stamp`, so `''`, `unknown` and
        # an unsubstituted `<build_id>` all read as None here and land in the "an unknown side is
        # not drift" branch below rather than as a comparison against a value nobody read.
        'backend-build-moved': (build_stamp(pin.get('backend_build')), build_stamp(backend_build)),
        # The axis a source patch actually moves. Same weight and the same "an unknown side is
        # not drift" rule as its sibling above — see the pin's own `backend_source` comment.
        'backend-source-moved': (build_stamp(pin.get('backend_source')), build_stamp(backend_source)),
    }
    moved: dict[str, tuple[object, object]] = {}
    for reason in REASONS:
        pinned, current = checks[reason]
        # An UNKNOWN current value is not drift. The pin cannot tell "it did not move" from "I
        # could not look", and a check that guessed either way would be worse than silent: it
        # would either refuse every replay on an older backend that reports no revision, or
        # bless one whose scenario set was deleted out from under it.
        if pinned is None or current is None:
            continue
        if pinned != current:
            moved[reason] = (pinned, current)
    return moved


def heaviest(reasons: list[str]) -> str | None:
    """The reason a single-answer reader must act on: the highest-WEIGHT one, then the coarsest.

    Callers that collapse the frame to one string (`check`'s headline, `staleness()`) used to take
    the first in `REASONS` order, which is an ordering by COARSENESS — a statement about which
    reason best explains the others, not about what any of them costs. The two came apart the
    moment a warn-only axis was inserted ahead of a refusing one: `corpus-moved` sorts before
    `harness-fingerprint-changed`, so a rebuilt corpus answered for a dead harness and the sidecar
    writer, which refuses only on the reasons it is told, wrote the file. Weight first, coarseness
    within a weight, keeps both readings.

    Both orderings are read off `WEIGHTS`, not off the argument, so the answer does not depend on
    the order a caller happened to collect its reasons in — the property the tuple version quietly
    relied on and the one a new call site would get wrong.

    `moved_coordinates` is unchanged and still returns EVERY axis: this is what a reader that can
    only hold one answer should hold, not a replacement for reporting all of them.
    """
    order = list(WEIGHTS)
    return min(reasons, key=lambda reason: ((weight(reason) != REFUSING), order.index(reason)), default=None)


def declared_staleness(pin: dict) -> tuple[str, str] | None:
    """`(weighable reason, what the file literally says)` for a pin that DECLARES one, else None.

    The weighable half is one of `DECLARED_REASONS`, or `UNKNOWN_DECLARED` when the file carries a
    word this version has no weight for — see `UNKNOWN_DECLARED` for why that is normalized HERE,
    at the boundary where untrusted disk content enters, rather than by giving `weight()` a
    default. The literal half rides along so the report can still name what the pin says: an
    operator told `declared-reason-unrecognized` and nothing else cannot go and look.
    """
    if not pin.get('stale'):
        return None
    recorded = str(pin.get('stale_reason') or DECLARED)
    return (recorded if recorded in DECLARED_REASONS else UNKNOWN_DECLARED), recorded


def _reported_reasons(pin: dict, workdir: Path, gt_revision: int | None, eval_revision: str | None,
                      set_corpus_ids: list[str] | None, backend_build: str | None = None,
                      backend_source: str | None = None) -> list[str]:
    """Every reason this frame reports, detected then declared then cannot-tell.

    The order is the tie-break WITHIN a weight, and it is the standing precedence: DETECTED
    reasons are decided before a declared one, because a declared `run-superseded` is written by
    the loop before every full re-run — short-circuiting on it made a changed harness or a
    regenerated set unreachable from that moment on. The two cannot-tells are last and weakest: a
    measured move and a declaration both outrank "cannot tell".
    """
    reasons = list(moved_coordinates(pin, workdir, gt_revision, eval_revision, set_corpus_ids, backend_build, backend_source))
    if (declared := declared_staleness(pin)) is not None:
        reasons.append(declared[0])
    reasons.extend(cannot_tell(pin, workdir, set_corpus_ids, backend_build, backend_source))
    return reasons


def staleness(pin: dict, workdir: Path, gt_revision: int | None, eval_revision: str | None = None,
              set_corpus_ids: list[str] | None = None, backend_build: str | None = None,
              backend_source: str | None = None) -> str | None:
    """The reason this frame stopped holding — the HEAVIEST one — or None while it still holds.

    Heaviest, not first: see `heaviest`. A reader with one answer to act on must be handed the
    reason that costs the most, or a warn-only axis that happens to sort earlier answers on behalf
    of a refusing one and the interlock cannot fire.

    `set_corpus_ids` is THE SAME argument `check` takes and for the same reason: without it the
    corpus axis can only compare the marker against the pin, so the half that catches a set
    standing on a corpus the workdir has left — the half the reason was added for — is dead in
    every caller that does not pass it. `apply_rescore.py --pin` was such a caller while
    its own header advertised `corpus-moved` as a reason it handles.

    `backend_build` is the same shape of argument and the same rule: absent, the backend axis is
    not asked and reports nothing at all — never `backend-unknown` at a caller that predates it.
    """
    return heaviest(_reported_reasons(pin, workdir, gt_revision, eval_revision, set_corpus_ids, backend_build, backend_source))


def cmd_write(args: argparse.Namespace) -> int:
    workdir = _workdir(args.workdir)
    # argparse's `required=True` accepts an EMPTY string, and the documented invocation reads each
    # id with `$(cat "$WORKDIR/.scenario_set_id")` — which yields '' whenever the marker is missing
    # (a bundle run, a named-env mismatch, a refused resolver). An empty pinned id then compares
    # unequal to every real one, so the frame reports `scenario-set-moved` on EVERY check for the
    # rest of the build, citing a regeneration that never happened and stopping the eval lane cold.
    missing = [name for name in ('scenario_set_id', 'eval_set_id', 'run_upload_id') if not str(getattr(args, name) or '').strip()]
    if missing:
        print(
            f'lane_pin write: refusing to pin an EMPTY {", ".join(missing)} — the marker file it was read from is '
            'probably absent. A pin with a blank id fails every later check as "moved", which stops the eval lane '
            'for the whole build on a regeneration that never happened.',
            file=sys.stderr,
        )
        return _EXIT_UNREADABLE
    fingerprint = args.harness_fingerprint or _current_fingerprint(workdir)
    # The corpora the recording stood on, read through the ONE shared reader so a
    # multi-knowledge-base agent (`.knowledge_bases`, or a per-tenant `.corpus_ids`) pins a real
    # coordinate instead of the `.corpus_id` it does not have. `--corpus-id` still overrides, and
    # an agent with no corpus pins None — which is unknown, never drift.
    declared = [c.strip() for c in (args.corpus_id or '').split(',') if c.strip()]
    coordinate = frozenset(declared) if declared else corpus_coordinate.corpus_coordinate(workdir)
    pin = {
        'scenario_set_id': args.scenario_set_id,
        'eval_set_id': args.eval_set_id,
        'run_upload_id': args.run_upload_id,
        'gt_revision': args.gt_revision,
        'eval_revision': args.eval_revision or None,
        'harness_fingerprint': fingerprint,
        'corpus_ids': sorted(coordinate) if coordinate else None,
        # KEPT beside `corpus_ids` for the readers that predate it (and for an operator reading
        # the file by eye), and written ONLY for a single-corpus agent — the same rule
        # `/build-rag-corpus` writes the `.corpus_id` marker under. Filling it with one of several
        # ids would name a corpus this recording did not stand on alone.
        'corpus_id': (sorted(coordinate)[0] if coordinate and len(coordinate) == 1 else None),
        # THE BACKEND IMAGE BUILD (`backend_identity().build_id`) — normalized, never the raw
        # argument. Written for every pin, `unknown` included, on the same rule as its neighbours:
        # "the backend did not move", "it did" and "nobody could read it" are three states, and an
        # absent key makes the third read like the first.
        'backend_build': build_stamp(args.backend_build) or UNKNOWN,
        # WHAT THE BACKEND WAS ACTUALLY RUNNING. `build_id` is a label baked into
        # the image and a per-file source patch does not move it: boxes can report one commit while
        # serving a patched grader under a bind-mount, so `backend-build-moved` would answer "held
        # constant" across two genuinely different graders.
        # Same normalization, same `unknown`, same three states.
        'backend_source': build_stamp(args.backend_source) or UNKNOWN,
        # WHICH PROCESS. Recorded and never COMPARED as drift — an instance id changes on every
        # restart by design, so an axis on it would report a redeploy on a box that merely
        # bounced (`build_stamp` refuses the whole identity object for exactly this reason). It
        # is here so a later reader can say "that measurement came from a different process",
        # which is the question `assert-backend-identity.sh` answers live and nothing on disk
        # could answer afterwards.
        'backend_instance': build_stamp(args.backend_instance) or UNKNOWN,
        'out_dir': args.out_dir or os.environ.get('OUT'),
        'pinned_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    _rewrite(workdir, pin)
    print(f'pinned {workdir / PIN} — scenario {pin["scenario_set_id"]} · eval {pin["eval_set_id"]} · upload {pin["run_upload_id"]}')
    if pin['backend_build'] == UNKNOWN:
        # LOUD, because the normalization is what stops a blank build from reporting drift on every
        # later check — and a silent normalization would leave the operator believing the arc is
        # pinned to a backend when the axis is dark for the rest of the build.
        print(
            f'note: this pin records backend_build {UNKNOWN} — pass --backend-build with '
            "backend_identity()'s `build_id` so a redeploy between qualification and the final set is "
            'reported as backend-build-moved. Until then that axis answers nothing, in either direction.',
            file=sys.stderr,
        )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    workdir = _workdir(args.workdir)
    pin = read_pin(workdir)
    if pin is None:
        print(
            f'UNREADABLE — no usable {PIN} in {workdir}. The lane has no frame, which is not the '
            'same as a fresh one: write the pin at the upload before replaying against it.',
            file=sys.stderr,
        )
        return _EXIT_UNREADABLE
    # An EMPTY current revision (an older backend reports none; the skill substitutes '') is unknown,
    # not drift — the same normalization `write` applies.
    set_corpus_ids = [c.strip() for c in (args.set_corpus_ids or '').split(',') if c.strip()] or None
    try:
        moved = moved_coordinates(pin, workdir, args.gt_revision, args.eval_revision or None, set_corpus_ids, args.backend_build, args.backend_source)
    except OSError as exc:
        print(f'UNREADABLE — the harness fingerprint cannot be computed ({exc}); a frame that cannot be checked is not fresh.', file=sys.stderr)
        return _EXIT_UNREADABLE
    declared = declared_staleness(pin)
    # A comparison that was asked for and could not be made is neither drift nor freshness. It
    # rides with BOTH outcomes and changes neither: nothing was measured to have moved, so the
    # exit code is unchanged and the replay goes ahead — but the operator is no longer told the
    # corpus coordinate agrees when nothing could check it. See `UNKNOWN_CORPUS`.
    for reason, why in cannot_tell(pin, workdir, set_corpus_ids, args.backend_build, args.backend_source).items():
        print(f'{reason} — {why}', file=sys.stderr)
    if not moved and declared is None:
        print(f'FRESH — pinned {pin.get("pinned_at")} · upload {pin.get("run_upload_id")}')
        return 0
    # EVERY moved axis, with both values — not the first alone. A reader acting on ONE axis (the
    # critic step stops on the harness axis) was blinded whenever a coarser axis sorted before it:
    # a regenerated scenario set hid that the harness was gone too.
    #
    # The corpus axis is the one whose reference is not always the pin's: with `--set-corpus-ids`
    # it compares against the SET's own ids, and labelling those "pinned" stated the recording's
    # corpus as the set's and the workdir's as the stray — the finding, inverted.
    #
    # WHICH reference it used is asked of the AXIS, not inferred from the value: the two can carry
    # the same string (a set standing on exactly the pinned corpus), and comparing values would
    # then label the pin's own reference as the set's. Re-reading the axis without the set ids
    # answers it exactly — silence there means only the set half fired.
    from_the_set = 'corpus-moved' in moved and _corpus_axis(pin, workdir, None) == (None, None)
    # The HEADLINE is the heaviest reason, not the coarsest: a warn-only axis that sorts earlier
    # (`corpus-moved`, inserted ahead of `harness-fingerprint-changed`) answered for a refusing one
    # here too, and a reader that keys on the first line read a rebuilt corpus where the harness
    # was gone. Every axis is still listed below it, in coarseness order.
    headline = heaviest([*moved, *([declared[0]] if declared is not None else [])])
    # `heaviest` puts a REFUSING reason first whenever one is reported, so the headline's weight IS
    # the frame's: the weight table stays the one owner of what a move costs.
    judgement_only = args.refusing_only and weight(headline) == WARN

    def _weighed(reason: str) -> str:
        return f' [{weight(reason)}]' if args.refusing_only else ''

    lines = [f'MOVED (judgement only) {headline}' if judgement_only else f'STALE {headline}']
    for reason, (reference, current) in moved.items():
        label = "the set's corpus_ids" if reason == 'corpus-moved' and from_the_set else 'pinned'
        lines.append(f'  {reason}: {label} {reference} · current {current}{_weighed(reason)}')
    if declared is not None:
        reason, recorded = declared
        # The literal is printed whenever it is not the reason itself, which is the only case that
        # needs it: an operator told the frame is stale for a reason this version cannot weigh has
        # to be told what the file actually says, or there is nothing to go and look at.
        lines.append(f'  {reason}: declared' + (f' as {recorded!r}' if recorded != reason else '') + _weighed(reason))
    print('\n'.join(lines), file=sys.stderr)
    return 0 if judgement_only else _EXIT_STALE


def _rewrite(workdir: Path, pin: dict) -> None:
    """Replace the pin atomically — a half-written frame reads as valid JSON to the next replay.

    THE one writer: `write`, `stale`, `adopt` and `relink` all land here, so the tmp+replace is
    stated once and a verb added later cannot get it wrong by omission.
    """
    path = workdir / PIN
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(pin, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    tmp.replace(path)


def _repoint(args: argparse.Namespace, reason: str) -> int:
    """Move the frame's set ids onto the relinked set and declare WHY the recording is stale.

    One body for `adopt` and `relink`: the mechanics are identical (re-point two ids, declare the
    frame stale), only the fact being recorded differs, and it is the verb that carries it.
    """
    verb = args.cmd
    # The lineage comes off the REASON constant, not off `args.cmd`. The reader weighs this word
    # against a closed set, so the writer must spell it from the same place: a subcommand rename
    # would otherwise record a word that set does not contain, which is the unknown-lineage state.
    lineage = LINEAGE_RELINK if reason == RELINKED else LINEAGE_ADOPT
    workdir = _workdir(args.workdir)
    pin = read_pin(workdir)
    if pin is None:
        # `read_pin` returns None for THREE states — absent, unreadable, and not-a-mapping — and
        # only the first is a no-op. Telling an operator "write the pin at the next upload" while a
        # corrupt one naming the QUALIFICATION set sits on disk is the reassuring reading of a
        # frame nobody can check, which is what `check` refuses to give (it exits UNREADABLE here).
        if (workdir / PIN).exists():
            print(
                f'{workdir / PIN} exists but does not read as a pin — refusing to {verb} over an unreadable frame. '
                'Delete it or write a fresh pin at the next upload.',
                file=sys.stderr,
            )
            return _EXIT_UNREADABLE
        print(
            f'no {PIN} in {workdir} — nothing to {verb}. A frame that does not exist cannot follow the '
            'relink; write the pin at the next upload.',
        )
        return 0
    scenario_set_id = args.scenario_set_id or _marker(workdir, '.scenario_set_id')
    eval_set_id = args.eval_set_id or _marker(workdir, '.eval_set_id')
    if not scenario_set_id or not eval_set_id:
        print(
            f'lane_pin {verb}: refusing to re-point the frame at an EMPTY set id — pass it explicitly, or '
            'write the marker first. A blank id fails every later check as "moved", citing a regeneration '
            'that never happened.',
            file=sys.stderr,
        )
        return _EXIT_UNREADABLE
    run_upload = getattr(args, 'run_upload', None)
    if run_upload is not None and not run_upload.strip():
        print(f'lane_pin {verb}: refusing to record an EMPTY --run-upload — the id it was read from is probably absent.', file=sys.stderr)
        return _EXIT_UNREADABLE
    fingerprint = None
    if run_upload:
        # The recording moves to the final run, so its harness coordinate moves with it, recomputed
        # as `write` does. Carried forward, the qualification fingerprint reads as a harness that is
        # gone under a run the current one produced.
        try:
            fingerprint = _current_fingerprint(workdir)
        except OSError as exc:
            print(f'lane_pin {verb}: refusing to record the run — the harness fingerprint cannot be computed ({exc}).', file=sys.stderr)
            return _EXIT_UNREADABLE
    pin['scenario_set_id'] = scenario_set_id
    pin['eval_set_id'] = eval_set_id
    if run_upload:
        # THE FINAL POPULATION'S OWN RUN: the recording now describes the shipped set, so nothing
        # is declared stale, and `anchor` reads this run's final-stage diagnostics.
        for key in ('stale', 'stale_reason'):
            pin.pop(key, None)
        pin.update({
            'run_upload_id': run_upload.strip(),
            'harness_fingerprint': fingerprint,
            'final_run_upload_id': run_upload.strip(),
            'out_dir': args.out_dir or os.environ.get('OUT'),
            'eval_revision': args.eval_revision or None,
            'gt_revision': None,
            'set_lineage': lineage,
            'pinned_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        })
        _rewrite(workdir, pin)
        print(f'{verb}ed — scenario {scenario_set_id} · eval {eval_set_id} · recording: the final-population run {pin["run_upload_id"]}')
        return 0
    # A re-point with no run names a set nothing has run yet, whatever an earlier call recorded.
    pin.pop('final_run_upload_id', None)
    # The recording did NOT move with the ids: `run_upload_id` / `out_dir` still name the
    # qualification artifacts. Declaring the staleness is what keeps the two facts apart — without
    # it every coordinate matches, the frame reads FRESH, and the lane writes sidecars from a
    # replay of transcripts the relinked set never produced.
    pin['stale'] = True
    # A staleness already declared is not replaced. `run-superseded` is the coarser fact — the
    # recording itself was overtaken — and overwriting it with a relink reason would name the
    # smaller reason to act on.
    #
    # The warnings are not the only consumer. `stale_reason` is load-bearing for a CUSTOMER-FACING
    # claim because `licensed_anchor` reads it, and a reason kept from an earlier declaration
    # would license the wrong rung. That is why the lineage below is its own field rather than a
    # change to this rule: the rule is still right for the operator's question.
    kept = 'stale_reason' in pin
    pin.setdefault('stale_reason', reason)
    # WHICH VERB RE-POINTED THE IDS, recorded UNCONDITIONALLY and never via setdefault. The
    # staleness above cannot carry this: the repair loop declares `run-superseded` on every rescore
    # round, so by the time `relink` runs the reason is usually already taken and `setdefault` keeps
    # it — which left the only tell that adoption was REFUSED invisible to every later reader.
    # Measured before this line existed: write -> stale(run-superseded) -> relink -> anchor printed
    # `Suite: validated` with "the gated set was adopted", over a pin recording a relink.
    pin['set_lineage'] = lineage
    _rewrite(workdir, pin)
    # The reason RECORDED, never the one this verb wanted: `setdefault` keeps a prior declaration,
    # and printing the wanted one told the operator `set-relinked` while the file said
    # `run-superseded`.
    recorded = pin['stale_reason']
    how = f'kept the earlier {recorded}' if kept else f'declared {recorded}'
    print(f'{verb}ed — scenario {scenario_set_id} · eval {eval_set_id} · STALE ({how}) until a run of the relinked set is uploaded')
    return 0


def cmd_adopt(args: argparse.Namespace) -> int:
    return _repoint(args, ADOPTED)


def cmd_relink(args: argparse.Namespace) -> int:
    return _repoint(args, RELINKED)


#: What `build_id` actually identifies, said once, where the claim is made. The backend's
#: `current_build_stamp()` stamps the IMAGE; a box running a source overlay reports the image build while executing
#: patched source, so this coordinate BOUNDS the claim rather than proving the code — and a scope
#: block that printed the build without saying so would be stronger than the coordinate supports.
_BUILD_CAVEAT = ('`build_id` is the backend IMAGE stamp: a box running a source overlay reports the image build while '
                 'executing patched source, so this names the deployment, not the lines that ran.')


def _artifacts(workdir: Path, explicit: str | None) -> Path:
    """`--artifacts-dir`, then `$ART`, then `<workdir>/artifacts`.

    The same three answers `append_loop_log._artifacts_dir` gives in the normal layout, reached
    through THIS module's already-resolved workdir rather than by calling it: that function
    self-resolves a fourth way and exits 2 when it cannot, which is right for a writer that must
    not lose an entry and wrong for a report that has a workdir in hand.
    """
    return Path(explicit) if explicit else Path(os.environ.get('ART') or (workdir / 'artifacts'))


def _client_pin(workdir: Path) -> str:
    """`plugin <sha> · SDK <sha>` off `.client_pin.json`, or why it cannot be named.

    The FILE, through that module's own constant — not its `check`, which RESOLVES the installed
    client and shells out to git: a report line must not be able to fail, hang, or change its
    answer with the working tree. `read_pin` there collapses absent and unreadable into one
    `None`, and those are the two states this line exists to keep apart, so the read is `_read_json`
    and only the path constant is borrowed. Rendered in the same shape `client_pin` prints.
    """
    path = workdir / client_pin.PIN
    doc, why_not = _read_json(path)
    if why_not is not None:
        # NOT armed and armed-but-unreadable are different facts with different owners, and
        # collapsing them told an operator "no pin" about a pin that exists — the reassuring
        # reading of a record nobody can check.
        return f'UNREADABLE — {path} ({why_not})'
    if doc is None:
        return 'none armed — the plugin + SDK commits these runs were made with were not recorded'
    plugin, sdk = str(doc.get('plugin_commit') or UNKNOWN), str(doc.get('sdk_commit') or UNKNOWN)
    return f'plugin {plugin} · sdk {sdk}'


def _loop_passes(art: Path, run_dir: Path | None) -> tuple[str, str, str, str, list[str]]:
    """`(passes, critic verdict, red-cell audit, blocking — unresolved, limitations)` derived from
    the loop log, never asserted. Each is read off its own typed kind: a red-cell audit is not a
    critic pass, and an unresolved landing block is counted, never read out of prose.

    The blocking count is the LIVE one (`release_verdict.blocking_split` against `run_dir`, the scored run): an eval
    entry the final run re-checked no longer counts, and the line then says how many were recorded at landing.

    The loop log is the round's own record and the only place a pass leaves a trace, so both
    numbers come off it. Every way it can fail to answer becomes a LIMITATION line rather than a
    zero: `0 critic passes` and "the log could not be read" are different rounds, and only the
    first is a fact about the suite.

    The census counts the kinds the log ACTUALLY CARRIES rather than a list of kinds this file
    considers a pass. That list was `('run', 'critic')` and nothing in the plugin writes
    `--kind run` — so every real loop rendered `0 run`, a fact about the vocabulary printed as a
    fact about the round, in the one artifact whose entire purpose is honest scope. A closed set
    that nobody writes to reports zero forever; a census reports what happened.
    """
    limits: list[str] = []
    log = art / 'loop_log.md'
    try:
        import append_loop_log  # noqa: PLC0415 - sibling script, imported for its ONE log parser
    except ImportError as exc:
        return UNKNOWN, UNKNOWN, UNKNOWN, UNKNOWN, [f'the loop log could not be parsed ({exc}), so passes used and the critic verdict are unread.']
    if not log.is_file():
        return UNKNOWN, UNKNOWN, UNKNOWN, UNKNOWN, [f'no {log} — this loop recorded no passes, so how many ran and what the critic said cannot be derived.']
    # READ IT HERE, even though `parse_entries` reads it again. That parser answers `[]` for a log
    # it cannot read — right for its own callers (a forfeit is the safe direction there), and
    # exactly wrong for a REPORT: a permission error, a truncated write or a non-UTF-8 byte would
    # render as the flat claim `0 run · 0 critic` about a round that ran three of each.
    try:
        text = log.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        return UNKNOWN, UNKNOWN, UNKNOWN, UNKNOWN, [f'{log} exists and could not be read ({type(exc).__name__}: {exc}), so passes used and the critic verdict are unread — NOT zero.']
    entries = append_loop_log.parse_entries(log)
    if text.strip() and not entries:
        # Content this version cannot parse is not an empty round either — the log outlives the
        # writer's format, and `parse_entries` drops what it does not recognize line by line.
        return UNKNOWN, UNKNOWN, UNKNOWN, UNKNOWN, [f'{log} has content but no entry this version can parse, so passes used and the critic verdict are unread — NOT zero.']
    census: dict[str, int] = {}
    for entry in entries:
        census[entry.kind] = census.get(entry.kind, 0) + 1
    critics = [e for e in entries if e.kind == 'critic']
    audits = [e for e in entries if e.kind == 'red-cell-audit']
    blocks = [e for e in entries if e.kind == 'blocking-unresolved']
    live, released = release_verdict.blocking_split(entries, run_dir)
    # A log written before the typed kind carries its landing blocks as prose notes: a count of 0
    # there would read as "none", so it is unknown.
    untyped = [e for e in entries if e.kind == 'note' and 'BLOCKING — UNRESOLVED' in e.finding]
    if not critics:
        # The ONE absence worth a limitation: the flow writes a `critic` entry at every gate pass
        # (analyze-simulation), so none means the suite was never independently judged — or the
        # judgement was not recorded, which for a report is the same amount of evidence.
        limits.append('no `critic` entry in the loop log: this suite was never independently judged, or the judgement was not recorded.')
    return (
        ' · '.join(f'{count} {kind}' for kind, count in sorted(census.items(), key=lambda kv: (-kv[1], kv[0]))) or 'no entry recorded',
        critics[-1].finding if critics else UNKNOWN,
        audits[-1].finding if audits else 'none recorded',
        (f'{UNKNOWN} (older log: {len(untyped)} untyped BLOCKING — UNRESOLVED note(s))' if untyped and not blocks else
         _blocking_line(live, released)),
        limits,
    )


def _scored_run_dir(workdir: Path) -> Path | None:
    """The scored run's dir (`deliverables.Disk.scored_run`), or None when no run is scored."""
    scored = deliverables.Disk(workdir).scored_run()
    return workdir / deliverables.RUNS / scored if scored else None


def _is_audited_final(workdir: Path, run_dir: Path | None) -> bool:
    """Whether `run_dir` is the index's scored run: the last AUDITED final (`runs_index.build_index`, rebuilt from the run
    folders, nothing written). `Disk.scored_run` falls back to `.last_run` with no index, which can name a calibration run."""
    if run_dir is None:
        return False
    import runs_index  # noqa: PLC0415 - sibling script, stdlib only

    try:
        with contextlib.redirect_stderr(io.StringIO()):  # the index's notes are for `runs_index.py write`
            return runs_index.build_index(workdir, {})['scored_run'] == run_dir.name
    except Exception:  # noqa: BLE001 - an odd run folder never stops the hand-off's anchor: no final Agent line
        return False


def _run_json_stage(run_dir: Path | None) -> str | None:
    """The `stage` a run's `run.json` records, else None."""
    doc, _ = _read_json(run_dir / 'run.json') if run_dir is not None else (None, None)
    return doc['stage'] if doc is not None and isinstance(doc.get('stage'), str) else None


def _loop_entries(art: Path) -> list:
    """The loop log's parsed entries (`append_loop_log.parse_entries`), `[]` when the parser cannot be loaded."""
    try:
        import append_loop_log  # noqa: PLC0415 - sibling script, imported for its ONE log parser
    except ImportError:
        return []
    return append_loop_log.parse_entries(art / 'loop_log.md')


def _blocking_line(live: list, released: list) -> str:
    """`<live count>[ (<their surfaces>; remedy <their remedies>)]` (`release_verdict.remedies`: what the hand-off's Next
    line reads), plus how many were recorded at landing once the final run released any."""
    surfaces = ', '.join(sorted({e.surface or UNKNOWN for e, _, _ in live}))
    held = f'{surfaces}; remedy {release_verdict.remedies(live)}'
    if not released:
        return f'{len(live)}' + (f' ({held})' if live else '')
    checked = f'{len(live) + len(released)} recorded at landing; '
    checked += ('' if not live else f'{len(released)} ') + 're-checked on the final run: no longer affects the results'
    return f'{len(live)} (' + (f'{held}; ' if live else '') + checked + ')'


def _scored_eval_set_state(pin: dict) -> str | None:
    """What the RUN behind this pin was launched against — `eval_set_state` from its summary.

    The pin records which eval-set REVISION graded the run, and that number cannot say whether the
    bytes behind it were an open edit session's mid-edit draft: the backend's finalize stamps a content
    digest either way. The SDK's pre-flight records the answer beside the artifacts
    (`run_summary.json`'s `eval_set_state`) and nothing read it — so a suite qualified
    entirely against a draft rendered a scope block that read clean.

    Absent key, absent file, no `out_dir`: None. This is one more coordinate, not a gate, and a
    run summary written by an older SDK simply has nothing to say here.
    """
    out_dir = pin.get('out_dir')
    if not isinstance(out_dir, str) or not out_dir.strip():
        return None
    summary = Path(out_dir) / 'run_summary.json'
    doc, why_not = _read_json(summary)
    if why_not is not None:
        return f'{summary} could not be read ({why_not}), so which eval-set bytes this run was scored against is unread.'
    record = doc.get('eval_set_state') if doc is not None else None
    if not isinstance(record, dict) or not record:
        return None
    if record.get('state') == 'draft':
        return (f'the run behind this pin was launched against a DRAFT eval set (session '
                f'{record.get("edit_session_id") or UNKNOWN}): the eval_revision above is a digest of that session\'s '
                'mid-edit bytes, not of a committed set.')
    if record.get('state') is None:
        return ('the run behind this pin could not establish whether its eval set was a committed one or an open '
                "session's draft (the backend reported no state, or the pre-flight could not reach it).")
    return None


def _recording_run_driver(pin: dict) -> str | None:
    """A limitation line when the recording behind this pin has gates no person is on record answering.

    The other half of `run_summary.json`'s lesson one function up: a row nothing reads is a row
    that dies. `build_run_json.py` stamps `run.json`'s `run_driver` block on a headless run, and
    THIS is what surfaces it — a headless round's defaulted decisions and its unmet prerequisites
    belong in the block that says what the verdicts are evidence ABOUT, not only in the run dir.

    Absent key, no `out_dir`, an interactive run nobody had to answer for: None. Nothing to bound.
    An INTERACTIVE recording whose gate was asked and never answered is not that state — the block
    is there, and its `asks_unanswered` list is the same limitation reached the other way.

    A run.json that could NOT BE READ is not that state, and `why_not` is exactly the reason —
    computed one line down and, before this, discarded. Returning None for it gave an unreadable
    recording the same answer as a human-attended one, on the very block whose job is to say what
    the verdicts are evidence about.
    """
    out_dir = pin.get('out_dir')
    if not isinstance(out_dir, str) or not out_dir.strip():
        return None
    doc, why_not = _read_json(Path(out_dir) / 'run.json')
    if why_not is not None:
        return (f"the recording behind this pin has no readable run.json ({why_not}), so whether a person "
                'answered this round\'s gates is not settled in this frame.')
    block = doc.get('run_driver') if isinstance(doc, dict) else None
    if not isinstance(block, dict) or not block.get('mode'):
        return None  # an interactive run says nothing here, and neither does an older run.json
    pending = block.get('input_required')
    pending = [p for p in pending if isinstance(p, dict)] if isinstance(pending, list) else []
    applied = block.get('defaults_applied')
    applied = [a for a in applied if isinstance(a, dict)] if isinstance(applied, list) else []
    # The gates a person WAS asked about and never answered — the interactive shape of the same
    # limitation: what those gates decided is not attributable to anyone in this frame either.
    unasked = block.get('asks_unanswered')
    unasked = [a for a in unasked if isinstance(a, dict)] if isinstance(unasked, list) else []
    # Gates reached with no run driver's answer whose own phase then landed: the default or stop they
    # replaced is no longer on the record, so they are named by what was applied, never as flow-answered.
    resolved = block.get('gates_resolved')
    resolved = [r for r in resolved if isinstance(r, dict)] if isinstance(resolved, list) else []
    # Stops whose config key was present when the gate was reached again: the writer of the key is not attested.
    settled = block.get('stops_settled_by_config')
    settled = [s for s in settled if isinstance(s, dict)] if isinstance(settled, list) else []
    partial = block.get('record') == 'partial'
    gaps = block.get('unreadable')
    gaps = [g for g in gaps if isinstance(g, dict)] if isinstance(gaps, list) else []
    head = (f'the recording behind this pin ran with run_driver={block["mode"]} (declared by '
            f'{block.get("declared_by") or UNKNOWN}):')
    if not applied and not pending and not unasked and not partial and not resolved and not settled:
        # SCOPE and EVIDENCE, never the universal sentence. The hook's findings are swept on every
        # arm and this block is built inside the RUN phase, so an empty record means "no gate
        # finding reached THIS phase's record" — not "every gate this round had was answered by
        # the flow". Rendering the second from the first attests to windows nothing read.
        return (f'{head} no gate findings were recorded for this phase (findings read from: '
                f'{Path(out_dir) / "run.json"}), so the gates the earlier phases of this round reached — and '
                'what they answered — are not settled in this frame.')
    line = head
    if applied or pending:
        # The HEADLESS kinds only: every gate such a round reached was the flow's. An interactive
        # block carrying only `asks_unanswered` cannot say that — a person may have answered
        # every other gate of the round, and that record is not projected here.
        line += ' the gates on this record were answered by the flow, not by a person.'
    if applied:
        # NAMED, not counted. "some defaults fired" is not a coordinate a critic can check; the
        # gate and the answer it took are.
        took = ', '.join(f'{a.get("gate") or UNKNOWN}={a.get("default") or UNKNOWN}' for a in applied)
        line += f' {len(applied)} did so here ({took}).'
    if pending:
        gates = ', '.join(str(p.get('gate') or UNKNOWN) for p in pending)
        line += (f' {len(pending)} gate(s) had no default and were left unanswered ({gates}), so what they '
                 'would have decided is not settled anywhere in this frame.')
    if unasked:
        # NAMED, like the other two, and claiming no more than the hook saw: the question went out
        # and nothing came back. Whether the flow then applied a default, stopped, or took an
        # answer from the chat is not recorded — a prerequisite gate has no default to apply.
        gates = ', '.join(str(a.get('gate') or UNKNOWN) for a in unasked)
        line += (f' {len(unasked)} gate(s) were asked and no answer came back ({gates}), so what was '
                 'decided at them is not attributable to a person in this frame.')
    if resolved:
        took = ', '.join(f'{r.get("gate") or UNKNOWN}={r.get("applied") or UNKNOWN}' for r in resolved)
        line += (f' {len(resolved)} gate(s) reached with no run driver answer were then decided by their own landed '
                 f'phase ({took}).')
    if settled:
        gates = ', '.join(str(s.get('gate') or UNKNOWN) for s in settled)
        line += (f' {len(settled)} stop(s) ({gates}) were settled by a config key present when the gate was reached '
                 'again; nothing attests who wrote it, so they are not recorded as the run driver\'s answer.')
    if partial:
        # A record read only in part: its unreadable lines are named, never folded into "no gate reached".
        where = ', '.join(f'{g.get("base") or UNKNOWN} line {g.get("line") or "(whole file)"}' for g in gaps) or UNKNOWN
        line += (f' The run driver record is PARTIAL — it could not be read at {where} — so gates beyond those '
                 'named here are not settled in this frame.')
    return line


def cmd_scope(args: argparse.Namespace) -> int:
    """Render the ONE qualification-scope block: what these verdicts are evidence ABOUT.

    Every coordinate the arc has is split across four files — `.client_pin.json` (which client),
    `.eval_lane_pin.json` (which sets, which recording, which backend build, which rubrics),
    `loop_log.md` (how many passes, what the critic said) and the run's own `run_summary.json`
    (whether the eval set was committed) — and a report that names none of them reads as a claim
    about the current world.

    DERIVED, never dictated. Every line is read off disk, and each thing that could NOT be read —
    or that HAS MOVED since the pin — becomes a limitation line. A scope block whose limitations
    are hand-written is a scope block that omits the ones nobody remembered.
    """
    workdir = _workdir(args.workdir)
    pin = read_pin(workdir)
    if pin is None:
        # STDOUT, and still exit 2. The documented invocation redirects stdout into
        # `$ART/qualification_scope.md`, so a refusal printed only to stderr left a ZERO-BYTE
        # artifact — silence exactly where the bound could not be established, and the previous
        # good block destroyed by the redirect. `read_pin` returns None for three states and only
        # one of them is benign, so say which (the same split `_repoint` refuses to collapse).
        exists = (workdir / PIN).exists()
        print('\n'.join([
            '### Qualification scope',
            f'- NO READABLE FRAME — {"the pin exists and does not read as one" if exists else "there is no pin"} in {workdir}.',
            '- limitations:',
            '  - these verdicts are NOT scoped to a frame: nothing records which sets, which recording, which client or '
            'which backend build they describe. ' + ('Delete the unreadable pin or write a fresh one at the next upload.'
                                                     if exists else 'Write the pin at the upload.'),
        ]))
        return _EXIT_UNREADABLE
    passes, critic, audit, blocking, limits = _loop_passes(_artifacts(workdir, args.artifacts_dir), _scored_run_dir(workdir))
    build = build_stamp(pin.get('backend_build')) or UNKNOWN
    revision = str(pin.get('eval_revision') or UNKNOWN)
    limits.append(_BUILD_CAVEAT if build != UNKNOWN else
                  'no backend image build was recorded on the pin, so nothing here says which backend generated and judged this recording.')
    if revision == UNKNOWN:
        limits.append('no `eval_revision` was recorded, so an in-place rubric edit since the pin cannot be told from none.')
    if _pinned_corpus(pin) is None:
        limits.append('no corpus coordinate was recorded, so a rebuilt knowledge base under this recording cannot be told from none.')
    if (draft_note := _scored_eval_set_state(pin)) is not None:
        limits.append(draft_note)
    if (run_driver_note := _recording_run_driver(pin)) is not None:
        limits.append(run_driver_note)
    if (declared := declared_staleness(pin)) is not None:
        # THE SPLIT THE TWO HALVES OF THE BLOCK ARE IN. `adopt` / `relink` re-point the ids at the
        # FINAL set and leave every recording coordinate — build, eval_revision, upload,
        # fingerprint, and the critic verdict that judged it — describing the QUALIFICATION pass.
        # Printed without this, the build reads as the build behind the set named two lines above,
        # which is the strongest claim in the block and the one the arc cannot make.
        limits.append(
            f'the frame is DECLARED stale ({declared[1]}): the sets named above are the FINAL ones, while the recording '
            'below them — its build, its eval_revision, its upload, and the critic verdict — is the qualification pass. '
            'By design (the gate runs on the qualification sample), no critic pass judged the final set.'
        )
    # DETECTED drift too, not only the declared kind. The pin's stored coordinates are what this
    # block prints, and printing them while the workdir has moved off them states the frame as
    # current — the very claim the block exists to bound. Every moved axis is a limitation,
    # whatever its weight: a refusing one voids the recording, a warn one moves the judgement of
    # it, and a report that mentions neither is the one that reads clean.
    try:
        for reason, (pinned, current) in moved_coordinates(pin, workdir, None).items():
            limits.append(f'{reason} SINCE the pin ({weight(reason).lower()}): pinned {pinned} · current {current}.')
    except OSError as exc:
        limits.append(f'the harness fingerprint could not be recomputed ({exc}), so whether the harness still stands was not checked.')
    lines = [
        '### Qualification scope',
        f'- client pin: {_client_pin(workdir)}',
        f'- sets now named: scenario {pin.get("scenario_set_id") or UNKNOWN} · eval {pin.get("eval_set_id") or UNKNOWN}',
        # Everything below describes the RECORDING, which after an adopt/relink is not a run of the
        # sets above. Two labels rather than one list, so the reader cannot attach the build to the
        # final set by proximity — see the declared-staleness limitation.
        f'- the recording behind them: run upload {pin.get("run_upload_id") or UNKNOWN} · pinned {pin.get("pinned_at") or UNKNOWN}',
        f'- backend image build (`build_id`) of that recording: {build}',
        f'- eval_revision of that recording: {revision}',
        f'- passes used: {passes}',
        f'- critic verdict: {critic}',
        f'- red-cell audit: {audit}',
        f'- blocking — unresolved: {blocking}',
        '- limitations:',
        *(f'  - {limit}' for limit in limits),
    ]
    print('\n'.join(lines))
    return 0


# ── the licensed hand-off anchor ─────────────────────────────────────────────────────────────
#
# The suite ladder's rungs were MODEL-AUTHORED: no script computed the hand-off line, and
# `report_guard` validated its SHAPE only (that it is an anchor at the head of a line) while
# reading no audit. So the rung was whatever the session remembered, and `qualified` — the lowest
# rung a LANDED suite can claim — is ALSO the normal landing state: "landed with accepted gaps on
# a GATED set" and "nothing gated this population at all" rendered byte-identical, so a suite
# could ship the second as the first.
#
# Computed here, from two facts already on disk: whether the frame declares `set-relinked` (the
# final set was generated FRESH, adoption refused, so no critic pass judged this population) and
# the final coverage audit's `release_gate` — the backend's own coverage gate.
# A relinked set whose own run was recorded (`relink --run-upload`) reads that run's final-stage
# diagnostics instead: the audit's gate read the qualification recording, not this population.

#: THE OPEN PRODUCT DECISION, deliberately ONE CONSTANT. A relinked branch is a
#: different claim from an adopted one — its population was never gated — and today both render
#: `Suite: qualified`. Whether that deserves a rung of its own is a product call, not this script's:
#:
#:   * a fourth rung (the value below) — the reader sees that nothing judged this set;
#:   * set this to `QUALIFIED` — the two branches collapse back to today's text, and the
#:     computation still runs, still refuses a model-authored line, and still names the branch in
#:     `suite_anchor.json` for anything that wants to read it.
#:
#: Nothing else in this file branches on the choice. It is not quite free on the TEST side, and
#: that cost was measured rather than assumed: setting this to `QUALIFIED` also retires
#: `test_a_relinked_branch_does_not_render_as_the_adopted_one`, whose assertion IS the distinction
#: the collapse removes. Leave that test as it stands until the decision is taken —
#: `test_the_rung_choice_is_one_constant` is written to stay green either way.
RELINKED_RUNG = 'Suite: qualified (ungated set)'

#: EVERY rung `licensed_anchor` can return. The single place that enumerates its own range, so the
#: tests that reconcile it against `hooks/anchors.json` and against the recipe's ladder derive the
#: set instead of hand-listing it — a hand-list stays green for a rung added as an inline literal,
#: which is the same inert-mechanism shape this file exists to close.
RUNGS: tuple[str, ...] = (VALIDATED, QUALIFIED, NOT_QUALIFIED, RELINKED_RUNG)


def blocking_unresolved(art: Path, run_dir: Path | None) -> int | None:
    """How many typed `blocking-unresolved` entries the loop log records that still affect the results of `run_dir`, the
    scored run (`release_verdict.blocking_split`) — `None` when it cannot say.

    Read off :func:`_loop_passes`, the ONE loop-log reader, so the anchor and the scope block count the same entries.
    """
    head = _loop_passes(art, run_dir)[3].split(' ', 1)[0]
    return int(head) if head.isdigit() else None


def _relinked(pin: dict) -> bool:
    """Whether `pin` records a relink (a FRESH final set, adoption refused): read off `set_lineage`, else the declared
    staleness for a pin written before that field or carrying a lineage word this version does not know."""
    lineage = pin.get('set_lineage')
    declared_relink = (declared_staleness(pin) or (None, ''))[0] == RELINKED
    # A word this version can weigh answers; anything else falls through to the declaration, which
    # is the same precedence `_reported_reasons` uses and never reads unknown as agreement.
    return lineage == LINEAGE_RELINK if lineage in SET_LINEAGES else declared_relink


#: The `why` of a relinked set whose final run was scored on disk but never uploaded (`final_run_not_uploaded`).
FINAL_RUN_NOT_UPLOADED_WHY = (
    "the final run was scored but never uploaded, so Plurai's checks of that run did not run; "
    'the rung is read from the final coverage audit of the shipped set'
)


def licensed_anchor(pin: dict | None, *, release_gate: dict | None, blocking_unresolved: int | None,
                    moved: dict[str, tuple[object, object]] | None, final_run_not_uploaded: bool) -> tuple[str, str]:
    """`(the one anchor this state licenses, why)`.

    `blocking_unresolved` (:func:`blocking_unresolved`) is REQUIRED: a positive count caps the anchor at
    `not yet qualified`, because the hand-off's Next line already sends that suite back to a repair and a
    suite landed with a blocking defect that still corrupts the final scores is not qualified. The count is the
    LIVE one: an eval entry the final run re-checked (`release_verdict.blocking_split`) caps at `qualified` instead,
    through `validation_holds`. `None` (no readable log) does not cap:
    the release gate stays the authority there. A coverage block reaches this count only through
    `release_gate.block_causes` — the EFFECTIVE gate, after the overrules recorded on the eval set — never
    through the raw `guard_coverage_gate` beside it (deliverable.md), so a cause an overrule released is not
    re-imposed here.

    THREE THINGS CAN BE UNKNOWN HERE AND NONE MAY READ AS THE PERMISSIVE ANSWER.

    `release_gate is None` means the final audit carried no release gate bound at the final stage
    (see :func:`read_audit`), which is NOT the same as a clear one: an unread gate has judged
    nothing, and claiming `validated` off it would be the model-authored guess this verb replaces.
    It lands on the floor rung and says so.

    `pin is None` is the same shape and used to be the opposite: absent, unreadable and
    not-a-mapping all collapsed to "not relinked", so a CORRUPT pin beside a non-blocking audit
    printed `Suite: validated` at exit 0 without a word about the pin — while `cmd_check` and
    `_repoint` REFUSE on that identical state.

    `moved` (:func:`moved_coordinates` of the pin, REQUIRED) is the detected drift, and `None` when it
    could not be computed. A REFUSING axis voids the recording every rung reads from, so it caps the
    anchor at the floor, as an uncheckable frame does; the scope block names the same axes.

    The RELINK is read off `set_lineage`, recorded unconditionally by `_repoint`, with the declared
    staleness as the fallback for pins written before that field existed (:func:`_relinked`).

    `final_run_not_uploaded` (REQUIRED, computed by `cmd_anchor`) says the relinked set's own final run is scored on
    disk with no upload recorded. It changes only the relinked branch's `why`, never the rung.

    A `set_lineage` word this version does not know is the FOURTH unknown, and it used to be the
    one exception to the rule above: any truthy string that was not exactly `relink` read as
    "adopted" AND suppressed the fallback, so a pin declaring `set-relinked` beside a lineage of
    `graft` — or `Relink`, a case slip — printed `Suite: validated`. An unrecognized word is not
    evidence of adoption; it is no evidence at all, and the declared staleness beside it still is.
    """
    if pin is None:
        return NOT_QUALIFIED, 'the lane pin could not be read, so which set this hand-off ships cannot be established'
    if moved is None:
        return NOT_QUALIFIED, 'the harness fingerprint could not be recomputed, so whether the recorded run is of this harness cannot be checked'
    if void := [f'{reason} (pinned {pinned} · current {current})' for reason, (pinned, current) in moved.items() if weight(reason) == REFUSING]:
        return NOT_QUALIFIED, f'the recording behind the gate no longer stands: {"; ".join(void)}'
    relinked = _relinked(pin)
    # A FRESH set is gated only by its own run: `release_gate` is then that run's final-stage
    # diagnostics (see `cmd_anchor`), and the ungated rung is left for a run that never happened.
    final_run = relinked and bool(pin.get('final_run_upload_id'))
    source = "the final-population run's diagnostics" if final_run else 'the final coverage audit'
    if release_gate is None:
        return NOT_QUALIFIED, f'{source} carries no release gate bound at the final stage, so no gate has judged this population'
    # Keyed on the loop log, whose coverage entries are written from the effective `block_causes` only.
    if blocking_unresolved:
        return NOT_QUALIFIED, f'the loop log records {blocking_unresolved} BLOCKING — UNRESOLVED defect(s), so the suite landed with a known blocking defect'
    if relinked and not final_run:
        if final_run_not_uploaded:
            return RELINKED_RUNG, FINAL_RUN_NOT_UPLOADED_WHY
        return RELINKED_RUNG, (
            'the final set was generated FRESH (adoption refused), so the gate ran on the qualification sample and '
            'no critic pass judged the population being shipped'
        )
    anchor = release_verdict.rung(release_gate)
    older = release_gate.get(release_verdict.OLDER_GATE)
    if older:
        gate_name = release_verdict.OLDER_GATE_NAMES.get(older, older)
        rerun = (
            f're-run `suite_diagnostics` on that run at the final stage, save it over artifacts/{FINAL_DIAGNOSTICS}'
            if final_run
            else "re-run `eval_coverage_audit` with `run_stage: 'final'` on the shipped sets, save it over artifacts/coverage_audit_final.json"
        )
        if release_gate['blocking']:
            return anchor, (
                f'not yet qualified under the older gate (no release gate recorded): {source} predates the release gate, '
                f'and its {gate_name} is BLOCKING, so the suite has to be rebuilt; to read the release gate instead, {rerun}, '
                f'and re-run `lane_pin.py anchor`'
            )
        return anchor, (
            f'qualified under the older gate (no release gate recorded): {source} predates the release gate, so its '
            f'{gate_name} is read, and it is not blocking. `validated` needs the release gate; to read one, {rerun}, and re-run `lane_pin.py anchor`'
        )
    untyped = '' if release_verdict.typed(release_gate) else ' (an untyped verdict from an older backend, read from its blocking bit alone)'
    if release_gate['blocking']:
        # A typed gate names its own reasons, each by the head its line opens with — so a block held by
        # neither half (an unaccepted catastrophic risk, an incomplete run) is never told as a missing coverage.
        holding = [head for reason in release_gate.get('reasons') or [] if isinstance(reason, str) and (head := reason.split(':', 1)[0].strip())] or [
            name
            for name, holds in (
                ('the coverage gate', release_gate.get('coverage_status') in ('advisory', 'block')),
            )
            if holds
        ]
        return anchor, f'the release gate of {source} is BLOCKING ({" and ".join(holding) or "no coverage was computed"}){untyped}'
    named = f', names {len(release_verdict.gate_risks(release_gate))} risk(s)' if anchor == QUALIFIED else ''
    return anchor, f'the release gate of {source} is not blocking{named}{untyped} and ' + ('it read the set being shipped' if final_run else 'the gated set was adopted')


def _audit_round(path: Path) -> tuple[int, float, str]:
    """Sort key putting the audit the flow wrote LAST at the end.

    By round NUMBER, because `sorted()` on the name is lexicographic and this glob's names are not
    zero-padded: it put `…_final10.json` before `…_final2.json`, so the tenth round lost to the
    second, and any lettered suffix (`…_final_rerun.json`) beat every numbered one. Reading a stale
    audit is only half of it — the digest then binds the guard to a file the rung was not computed
    from. mtime breaks ties for names that carry no round number at all.
    """
    stem = path.name.removeprefix('coverage_audit_final').removesuffix('.json')
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (int(stem) if stem.isdigit() else 1, mtime, path.name)


def audit_candidates(workdir: Path) -> list[Path]:
    """Every final coverage audit on disk, the newest ROUND last."""
    return sorted((workdir / 'artifacts').glob('coverage_audit_final*.json'), key=_audit_round)


def read_audit(path: Path | None) -> tuple[dict | None, str | None]:
    """`(release_gate, sha256)` from ONE read of the audit.

    Both halves off the same bytes. Two reads could disagree — the verdict from content A and the
    digest from content B — and the record would then bind the guard to bytes the rung was NOT
    computed from, which is the one thing the digest exists to make impossible.

    The verdict is the audit's `release_gate` — and only when it is bound at `stage: final` with a boolean `blocking`; anything else is `None`.
    `None` for the verdict is honest and lands on the floor rung: an unread gate has judged nothing.
    """
    report, digest = _load_report(path)
    if report is None:
        return None, digest
    return _final_gate(report), digest


def _load_report(path: Path | None) -> tuple[dict | None, str | None]:
    """`(report, sha256)` from ONE read of a saved backend report — see :func:`read_audit`."""
    if path is None:
        return None, None
    try:
        raw = path.read_bytes()
    except OSError:
        return None, None
    digest = hashlib.sha256(raw).hexdigest()
    try:
        doc = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return None, digest
    if not isinstance(doc, dict):
        return None, digest
    # Both shapes, because both land on disk: the bare report, and the job envelope `mcp_call.py`
    # writes, which carries the report under `detail`.
    return (doc['detail'] if isinstance(doc.get('detail'), dict) else doc), digest


def _final_gate(report: dict) -> dict | None:
    """The report's release verdict, only when bound at `stage: final` with a boolean `blocking`.

    Read by `release_verdict.report_gate` — the reader the report banner uses too, so the rung and the
    banner never read one artifact two ways. An audit written before `release_gate` existed is read
    off its older gate, which the rung caps at `qualified` rather than reading it as no gate at all."""
    gate = release_verdict.report_gate(report)
    if not isinstance(gate, dict) or gate.get('stage') != 'final' or not isinstance(gate.get('blocking'), bool):
        return None
    return gate


#: Where the fresh final set's own run leaves its final-stage `suite_diagnostics` job envelope.
FINAL_DIAGNOSTICS = 'suite_diagnostics_final.json'

#: The named risk of a relinked set whose final run was scored but never uploaded (`cmd_anchor`). Recorded in
#: `suite_anchor.json` `named_risks`, never in the gate, so it moves no rung.
FINAL_RUN_NOT_UPLOADED = 'final_run_not_uploaded'


def _run_scenario_set_ids(run_dir: Path | None) -> set[str]:
    """The scenario sets a run's conversations were judged against: each top-level item's `verdicts.json`
    `judged_against.scenario_set_id`. Items that cannot be read say nothing."""
    if run_dir is None or not run_dir.is_dir():
        return set()
    import verdicts_file  # noqa: PLC0415 - sibling script, the one verdicts reader

    ids: set[str] = set()
    for item in sorted(run_dir.iterdir()):
        if not verdicts_file.exists(item):
            continue
        try:
            set_id = verdicts_file.judged_against(verdicts_file.read(item)).get('scenario_set_id')
        except verdicts_file.VerdictsError:
            continue
        if isinstance(set_id, str) and set_id:
            ids.add(set_id)
    return ids


def final_run_not_uploaded(pin: dict | None, run_dir: Path | None, stage: str | None) -> bool:
    """Whether a relinked set's own final run is scored on disk but no upload of it was recorded.

    `final_run_upload_id` is written only by `relink --run-upload`, so outside a relink a missing id is normal. The
    run must be of the shipped set (every readable `verdicts.json` judged against `pin['scenario_set_id']`): a final
    run of an earlier set may well have been uploaded, for that set.
    """
    if pin is None or stage != 'final' or not _relinked(pin) or pin.get('final_run_upload_id'):
        return False
    set_id = pin.get('scenario_set_id')
    return isinstance(set_id, str) and bool(set_id) and _run_scenario_set_ids(run_dir) == {set_id}


def read_diagnostics(path: Path | None, pin: dict) -> tuple[dict | None, str | None]:
    """`(release_gate, sha256)` of the final-population run's diagnostics, from ONE read.

    Bound to the run the pin records (`final_run_upload_id`) and to the pinned set: a report on
    any other upload or set is not a reading of the population being shipped, and is `None`.
    """
    report, digest = _load_report(path)
    if report is None or report.get('run_upload_id') != pin.get('final_run_upload_id'):
        return None, digest
    if report.get('scenario_set_id') not in (None, pin.get('scenario_set_id')):
        return None, digest
    return _final_gate(report), digest


def cmd_anchor(args: argparse.Namespace) -> int:
    """Compute the licensed hand-off anchor and record it for `report_guard` to require verbatim.

    Recorded as well as printed, on the same terms as `bench_manifest verify`'s run-integrity
    sentence: a rule that compares the hand-off against a sentence a SCRIPT printed cannot be
    satisfied by a correct-sounding invention, and cannot fail a correct hand-off.
    """
    workdir = _workdir(args.workdir)
    # RESOLVED, because the record is read back by a different process with a different cwd: the
    # hooks resolve from the SESSION's cwd while the skills resolve from the Bash tool's, and a
    # relative `--workdir` recorded a relative audit path that the guard then failed to open —
    # silently dropping the rule rather than enforcing it.
    audit = Path(args.audit).resolve() if args.audit else None
    # The final audit, by the names the flow writes — the newest ROUND wins: an additive round
    # writes `…_final2.json` beside `…_final.json`, and reading the earlier one reports gaps that
    # round had already closed.
    candidates = audit_candidates(workdir)
    if audit is None:
        audit = candidates[-1].resolve() if candidates else None
    # READ ONCE, and that applies to BOTH inputs. The audit used to be opened twice here — once for
    # the verdict, once for the digest — so the record could bind bytes the rung was not computed
    # from, which is the failure this record exists to prevent.
    gate, digest = read_audit(audit)
    #
    # NOT `read_pin(workdir) or {}`. That collapse would let an unreadable pin fall through to
    # "not relinked", and `licensed_anchor` distinguishes
    # `None` (nothing could be read) from `{}` (a pin that says nothing), and only the first may
    # refuse. Keep the None; `declared_staleness` is the thing that wants a mapping, so it gets one.
    pin = read_pin(workdir)
    if pin is None:
        print(f'  the lane pin at {workdir / PIN} could not be read — the rung falls to the floor', file=sys.stderr)
    declared = declared_staleness(pin or {})
    # A FRESH set whose own run was recorded is gated by THAT run's final-stage diagnostics, and the
    # record binds those bytes instead, so the guard sees the report the rung was read from.
    if pin is not None and pin.get('final_run_upload_id'):
        audit = (Path(args.diagnostics) if args.diagnostics else workdir / 'artifacts' / FINAL_DIAGNOSTICS).resolve()
        gate, digest = read_diagnostics(audit, pin)
    # The runner's own named risks (a shared world) ride the scored run's summary, not the backend's report.
    scored = deliverables.Disk(workdir).scored_run()
    risks = release_verdict.run_risks(workdir / deliverables.RUNS / scored) if scored else []
    if gate is not None and risks:
        gate = {**gate, release_verdict.RUN_RISKS: risks}
    gate = release_verdict.with_loop_risks(gate, workdir / deliverables.RUNS / scored) if scored else gate
    run_dir = _scored_run_dir(workdir)
    blocks = blocking_unresolved(_artifacts(workdir, None), run_dir)
    live, released = release_verdict.blocking_split(_loop_entries(_artifacts(workdir, None)), run_dir)
    # The same detected drift `scope` lists as limitations, so the rung cannot license a recording the
    # scope block beside it calls void.
    try:
        moved = moved_coordinates(pin, workdir, None) if pin is not None else {}
    except OSError:
        moved = None
    # Which run the Agent line reads (below), read here too: a relinked set's final run on disk with no upload recorded.
    audited = _is_audited_final(workdir, run_dir)
    stage = 'final' if audited else _run_json_stage(run_dir)
    not_uploaded = final_run_not_uploaded(pin, run_dir, stage)
    anchor, why = licensed_anchor(pin, release_gate=gate, blocking_unresolved=blocks, moved=moved, final_run_not_uploaded=not_uploaded)
    # What the workdir still says against `validated` (`release_verdict.validation_holds`): a measurement that did not
    # hold, a critic older than the last harness/env edit, no client pin over the loop. A hold only ever lowers the rung.
    holds = release_verdict.validation_holds(workdir, run_dir, client_pin_required=True)
    anchor, held = release_verdict.capped_rung(anchor, holds)
    if held:
        why = f'{why}; not validated: {"; ".join(held)}'
    # The rung is the HAND-OFF's, so a build that has not handed off (`bench_manifest.open_build`: its phase record
    # ends before `report landed`) licenses the floor, whatever its gate says: its suite is still the build's draft.
    from bench_manifest import _suite_state, open_build  # noqa: PLC0415 - the phase record's one reader
    doc, _unreadable = _suite_state(workdir)
    open_last = open_build(doc['phases']) if doc is not None else None
    if open_last is not None:
        anchor, why = NOT_QUALIFIED, (f'the suite build has not handed off (its record ends on '
                                      f'{open_last.get("phase")} {open_last.get("status")})')
    # THE AGENT, rated apart from the suite: the scored run's scores block (the SDK's), said as its own line. Never
    # folded into the rung — the rung rates the suite, this rates the agent the suite scored. Only the audited final
    # run's line is the agent's verdict; a run before it (the qualification run, when no final run is audited yet) is
    # labelled as that run's, and a final run not yet audited says no Agent line.
    scores = release_verdict.run_scores(run_dir) if audited or stage != 'final' else None
    agent_line = release_verdict.agent_line(scores, release_verdict.policy_display_names(workdir), stage=stage)
    agent = (scores or {}).get('agent')
    # The summary the Agent line was read off, bound by digest: a re-run into the same run rescores it, and the guard
    # (`report_guard._recorded_anchor`) then stops requiring a line the scores no longer say.
    summary = run_dir / 'run_summary.json' if agent_line and run_dir is not None else None
    try:
        summary_sha = hashlib.sha256(summary.read_bytes()).hexdigest() if summary is not None else None
    except OSError:
        summary_sha = None
    record = {
        'anchor': anchor,
        'why': why,
        # False until the build hands off: `simulate_plan._handed_off` then reads this file as no proof of a finished suite.
        'handed_off': open_last is None,
        'audit': str(audit) if audit else None,
        # THE DIGEST OF THE AUDIT THIS ANCHOR WAS COMPUTED FROM — the binding
        # `_recorded_integrity_line` has and this file's first version dropped. It catches the audit
        # being REWRITTEN; on its own it cannot catch the audit being SUPERSEDED, because an
        # additive round leaves the file this digest covers untouched and lands a new one beside it.
        'audit_sha256': digest,
        # …which is what this covers: the SELECTION, not just the bytes. A newer round appearing
        # makes the record stale even though its digest still matches, and the guard then compels
        # the rung the superseded audit licensed instead of the one the set has now earned.
        'audit_names': sorted(p.name for p in candidates),
        'release_gate_blocking': gate['blocking'] if gate is not None else None,
        # The LIVE count, and why each still caps (the customer `<why>` names these); the entries the final run re-checked.
        'blocking_unresolved': blocks,
        'blocking_live': [why for _, why, _ in live],
        # What each live entry still needs, beside its why (`release_verdict.blocking_split`: rebuild | eval:<checks> | ours).
        'blocking_remedies': [remedy for _, _, remedy in live],
        'blocking_released': [why for _, why in released],
        # Every risk the anchor names, each by its `kind`: the gate's (the `why`'s "names N risk(s)") and the released entries
        # that stay a named risk on the suite (an audit-proven false red of a check only the backend judges, said in the
        # `why`'s "not validated" clause).
        'named_risks': [*(release_verdict.gate_risks(gate) if gate is not None else []),
                        *release_verdict.named_risks(released, release_verdict.policy_display_names(workdir)),
                        *([{'kind': FINAL_RUN_NOT_UPLOADED, 'display_name': 'final run not uploaded', 'run': run_dir.name if run_dir else None,
                            'at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] if not_uploaded else [])],
        'validation_holds': [hold for hold, _ in holds],
        # False on an older backend's gate: read from its blocking bit alone, never guessed into the typed reading.
        'release_verdict_typed': release_verdict.typed(gate) if gate is not None else None,
        'declared_staleness': declared[1] if declared else None,
        'set_lineage': (pin or {}).get('set_lineage'),
        # Whether the pin could be read AT ALL, stated rather than inferred: an unreadable pin and a
        # healthy pin that declares nothing write byte-identical nulls for the two fields above, and
        # the next reader keying on `set_lineage is None` would get the permissive default back.
        'pin_readable': pin is not None,
        # The Agent line beside the rung (`release_verdict.agent_line`), and the verdict it says — null without a run, or off an unaudited final.
        'agent': agent.get('verdict') if isinstance(agent, dict) else None,
        'agent_line': agent_line,
        'agent_summary': str(summary) if summary_sha else None,
        'agent_summary_sha256': summary_sha,
    }
    # THE RUNG FIRST. The record write is the step that can fail — an unwritable `artifacts/`, a
    # sentinel workdir, a full disk — and writing first meant every one of those killed the verb
    # with a traceback and printed no rung at all, in exactly the state where the guard also stops
    # guarding. The caller gets the answer even when it cannot be recorded.
    print(anchor)
    if agent_line:
        print(agent_line)  # the hand-off's second line, verbatim
    print(f'  ({why})', file=sys.stderr)
    # A warning, never a block: a named risk the hand-off would show by its id although the eval set names the check.
    for policy in release_verdict.id_labelled_risks(record['named_risks'], release_verdict.policy_display_names(workdir)):
        print(f'  warning: the named risk on `{policy}` is labelled by its id although policies_config.yaml gives it a display name',
              file=sys.stderr)
    for _, released_why in released:
        print(f'  (a blocking entry recorded at landing was {released_why})', file=sys.stderr)
    if not_uploaded:
        print("  warning: the final run was scored but never uploaded, so Plurai's checks of that run did not run", file=sys.stderr)
    out = workdir / 'artifacts' / 'suite_anchor.json'
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    except OSError as exc:
        print(f'  the anchor could NOT be recorded at {out} ({exc}) — the guard has no rule to enforce', file=sys.stderr)
        return _EXIT_UNREADABLE
    return 0


def cmd_stale(args: argparse.Namespace) -> int:
    workdir = _workdir(args.workdir)
    pin = read_pin(workdir)
    if pin is None:
        # A SEMANTIC NO-OP, not a failure. The skills call `stale` UNCONDITIONALLY before a
        # re-run — that is the whole point of declaring a staleness that has no on-disk tell —
        # and on the first run through the flow no pin exists yet. "Mark the nonexistent frame
        # stale" is already true, so exiting non-zero made the correct instruction look like a
        # broken step and sent operators looking for a pin they were not supposed to have.
        #
        # `check` still exits UNREADABLE on the same state, and rightly: there the caller is
        # asking a question about a frame, and "there is no frame" is not "the frame holds".
        print(f'no {PIN} in {workdir} — nothing to mark stale (a frame that does not exist cannot be current).')
        return 0
    pin['stale'] = True
    pin['stale_reason'] = args.reason
    _rewrite(workdir, pin)
    print(f'marked STALE ({args.reason}) — {workdir / PIN}')
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workdir', help='defaults to $WORKDIR, else the resolver cascade')
    sub = parser.add_subparsers(dest='cmd', required=True)

    write = sub.add_parser('write', help='pin the frame these run artifacts were recorded in')
    write.add_argument('--scenario-set-id', required=True)
    write.add_argument('--eval-set-id', required=True)
    write.add_argument('--run-upload-id', required=True)
    write.add_argument('--gt-revision', type=int, default=None, help='from the score_run result (0 is real, not absent)')
    write.add_argument('--eval-revision', default=None, help="the run's eval_revision from run_summary.json (the runner copies it off finalize)")
    write.add_argument('--harness-fingerprint', default=None, help='defaults to recomputing it here')
    write.add_argument('--corpus-id', default=None,
                       help='the corpus (or comma-joined corpora) the recording stood on; defaults to the workdir '
                            "coordinate — `corpus_coordinate.py`'s union of .knowledge_bases, .corpus_ids and .corpus_id")
    write.add_argument('--backend-build', default=None,
                       help="the backend IMAGE build these artifacts were generated and judged by — backend_identity()'s "
                            '`build_id`. An empty or unreadable value records `unknown`, which never reports drift')
    write.add_argument('--backend-source', default=None,
                       help="the backend's SOURCE stamp (backend_identity()'s `source_id`) — the field a per-file source "
                            'patch moves, which `build_id` does not. Empty or unreadable records `unknown`')
    write.add_argument('--backend-instance', default=None,
                       help="the backend BOX (backend_identity()'s `instance_id`). Recorded, never compared as drift: "
                            'on E2B it is the sandbox id and moves only when the box is replaced; '
                            'off E2B it is a per-process uuid')
    write.add_argument('--out-dir', default=None, help='the run dir these artifacts live in; defaults to $OUT')
    write.set_defaults(func=cmd_write)

    check = sub.add_parser('check', help='does the pinned frame still hold?')
    check.add_argument('--gt-revision', type=int, default=None, help='the revision the last score_run reported')
    check.add_argument('--eval-revision', default=None, help="the eval set's CURRENT revision (get_artifact_meta(eval_set_id).detail.eval_revision)")
    check.add_argument('--set-corpus-ids', default=None,
                       help='comma-joined corpus ids the SET stands on (get_artifact_meta(<set>).detail.corpus_ids); one of '
                            'them that the workdir no longer attaches is corpus-moved')
    check.add_argument('--backend-build', default=None,
                       help="the backend's CURRENT image build (backend_identity().build_id). Omit it and the axis is not "
                            'asked at all; pass an empty or unreadable value and the answer is backend-unknown, never drift')
    check.add_argument('--backend-source', default=None,
                       help="the backend's CURRENT source stamp (backend_identity().source_id). Omit it and the axis is "
                            'not asked; an empty or unreadable value answers backend-unknown, never drift')
    check.add_argument('--refusing-only', action='store_true',
                       help='exit 3 only when a REFUSING axis moved; exit 0 (MOVED, judgement only) when every moved axis weighs WARN')
    check.set_defaults(func=cmd_check)

    scope = sub.add_parser('scope', help='render the qualification-scope block from the pins on disk')
    scope.add_argument('--artifacts-dir', default=None, help='defaults to $ART, else $WORKDIR/artifacts')
    scope.set_defaults(func=cmd_scope)

    anchor = sub.add_parser('anchor', help='compute the one hand-off anchor this state licenses, and record it')
    anchor.add_argument('--audit', default=None, help='the final coverage audit JSON; defaults to the newest artifacts/coverage_audit_final*.json')
    anchor.add_argument('--diagnostics', default=None,
                        help=f"a relinked set's final-population run diagnostics (job envelope); defaults to artifacts/{FINAL_DIAGNOSTICS}")
    anchor.set_defaults(func=cmd_anchor)
    stale = sub.add_parser('stale', help='declare a staleness that has no on-disk tell')
    stale.add_argument('--reason', default=DECLARED, choices=DECLARED_REASONS)
    stale.set_defaults(func=cmd_stale)

    for verb, func, blurb in (
        ('adopt', cmd_adopt, 'the final set ADOPTED the qualification scenarios'),
        ('relink', cmd_relink, 'the final set was generated FRESH (adoption refused)'),
    ):
        repoint = sub.add_parser(verb, help=f're-point the frame at the relinked set — {blurb} — declared stale until it is run')
        repoint.add_argument('--scenario-set-id', default=None, help='defaults to $WORKDIR/.scenario_set_id')
        repoint.add_argument('--eval-set-id', default=None, help='defaults to $WORKDIR/.eval_set_id')
        if verb == 'relink':
            repoint.add_argument('--run-upload', default=None,
                                 help="the fresh final set's OWN run upload: the recording now describes the shipped set")
            repoint.add_argument('--out-dir', default=None, help='with --run-upload: that run dir; defaults to $OUT')
            repoint.add_argument('--eval-revision', default=None, help="with --run-upload: that run's eval_revision (run_summary.json)")
        repoint.set_defaults(func=func)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
