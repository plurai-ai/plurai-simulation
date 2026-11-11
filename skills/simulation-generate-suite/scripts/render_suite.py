#!/usr/bin/env python3
"""Render a test-suite spec (scenarios + evals + environment) into the report
artifacts behind `/simulation-generate-suite`.

The skill (local Claude) builds ONE `suite.json` from data the backend returns
(scenario-set meta, per-scenario yaml, the dimension graph, policies_config.yaml)
and this script lays it out deterministically — same split as the topology
renderer: Claude does the judgement/derivation, the script does the pixels.

    python render_suite.py suite.json --artifacts-dir plurai_sim/artifacts

Writes (all under --artifacts-dir, which is gitignored):
    scenarios.md
    evals.md
    charts/scenarios_coverage.svg
    charts/scenarios_complexity.svg
    charts/environment.svg
    scenarios/<page>.md        (one per scenario; <page> is the id SANITISED to a single
                                safe path segment — see page_name)
    suite.json                 (canonical copy)

Every section degrades gracefully: anything the spec omits (e.g. trace-derived
value ranges / failure-mode rates, not available until the traces sync exists)
is simply left out — never shown as empty or placeholder.

The render is AUTHORITATIVE for what it owns: after writing, it prunes the
per-scenario pages and charts a PREVIOUS spec produced and this one does not, so
a re-render of a changed suite can't leave the report linking scenarios that are
no longer in it. Nothing outside those renderer-owned names is ever touched, and
a hand-edited line is not preserved — the fix for a wrong line is a correct
render, not a manual patch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

# ── palette (GitHub dark — same as the topology renderer) ─────────────────────
BG = "#0d1117"
PANEL = "#161b22"
STROKE = "#30363d"
TRACK = "#1b2230"
TEXT = "#e6edf3"
MUTED = "#6e7681"
LABEL = "#9aa5b1"
BLUE = "#4493f8"
CYAN = "#39c5cf"
PURPLE = "#bc8cff"
GOLD = "#e3b341"
ORANGE = "#f0883e"
RED = "#ff7b72"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
SANS = "-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"

ACCENTS = {"blue": BLUE, "cyan": CYAN, "purple": PURPLE, "gold": GOLD, "orange": ORANGE, "red": RED}


def _svg(w: int, h: int, body: str, *, rx: int = 0) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="{SANS}">'
        f'<rect width="{w}" height="{h}" rx="{rx}" fill="{BG}"/>{body}</svg>'
    )


# ── warning channel ───────────────────────────────────────────────────────────
# stderr reaches the AGENT (the Bash tool result carries it) and then dies with the turn: the
# human never sees it, and the report skeleton this feeds forbids gap narration, so nothing
# downstream can carry it either. Every warning is therefore also mirrored into an artifact —
# artifacts are the only thing that survives to a reader. Kept module-level rather than threaded
# through every renderer because the emit sites are scattered and a dropped thread is exactly the
# silent-warning failure this whole change is about.
_NOTES: list[str] = []


def _warn(message: str) -> None:
    """Emit an operator-facing warning to stderr AND record it for `render_notes.txt`."""
    _NOTES.append(message)
    print(message, file=sys.stderr)


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _complexity_accent(bits: float, hi: float) -> str:
    if bits >= max(6.0, hi * 0.85):
        return PURPLE
    if bits >= hi * 0.55:
        return BLUE
    return CYAN


def _is_number(value) -> bool:
    """A real number, never a bool. `True` is an `int` in Python, and a card typed `verified: true`
    took the numeric branch and rendered `verified 1%`."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _coverage_split(card: dict) -> tuple[float, float] | None:
    """`(verified, declared)` when the card carries BOTH typed numbers, else None.

    Both, or neither: a card carrying only `verified` would render a split whose other half the
    reader has to infer, and inferring it from the headline is what the split exists to stop.
    """
    verified, declared = card.get("verified"), card.get("declared")
    if verified is None and declared is None:
        return None  # the ordinary case: an older backend publishes no typed pair
    if not all(_is_number(v) for v in (verified, declared)):
        # Either half absent, or either half not a number ("22", True). Warned in EVERY unusable
        # shape, not only the None-asymmetry: a mistyped pair silently rendered no split and said
        # nothing at all, which is indistinguishable from a backend that never published one.
        _warn(
            f'render_suite: coverage card {card.get("label", "?")!r} carries an unusable '
            f"verified/declared pair ({verified!r}, {declared!r}) — both must be numbers in the "
            "card's unit, so no split is rendered"
        )
        return None
    # The sub-line SPLITS the headline, so the halves must add up to it. The backend makes
    # `covered` / `covered_unverified` / `uncovered` mutually exclusive — `covered` is VERIFIED
    # ONLY — so a card whose value was built from `covered` alone renders its own verified number
    # as the headline and leaves the `+Y% declared` dangling under a total that excludes it.
    # Warned, not refused: the split is still the more honest of the two renderings.
    value = card.get("value")
    if _is_number(value) and abs((verified + declared) - value) > 1:
        _warn(
            f'render_suite: coverage card {card.get("label", "?")!r} has value {value} but '
            f"verified {verified} + declared {declared} = {verified + declared} — the sub-line splits the "
            "headline, so the headline must be verified+declared (see the coverage schema in SKILL.md)"
        )
    return float(verified), float(declared)


def _credit_note(card: dict) -> str | None:
    """The AUDITOR-ONLY / advisory-reach annotation for a flow-coverage card, or None — validated, never rendered.

    The verified/declared split above says how much of the headline the audit measured. It does
    NOT say how the measured half was decided: a flow can be credited on a single sampled coherence
    auditor's attestation against a lexical scan that found no signal for it. The auditor
    is right to decide — a scenario can walk a flow without naming it — but an auditor-only
    credit is softer evidence than a corroborated one, and folding it into the strongest band
    without a marker would hide the difference. Both counts are SUBSETS of verified, not a fourth band; they qualify the
    number rather than subtracting from it.

    THREE-VALUED, on both keys, because the failure this renders is exactly a null read as a
    zero. `auditor_only: null` means the advisory produced no verdict to split on — it is not
    "no auditor-only credits". `advisory_blind: null` means the advisory's reach is unknown, so
    an `auditor_only: 0` beside it is not a clean sweep: the advisory FAILS OPEN, and a set
    nothing scanned publishes `[]` and reads as fully scanned. The key being ABSENT is the
    fourth case and the only silent one — an older backend publishes no split at all, and
    inventing an unknown-state line for every such suite would put the marker on renders that
    never had the question.

    `advisory_blind: 0` is deliberately silent: every verified flow was scanned, which is what
    makes the auditor-only count trustworthy and needs no line of its own.
    """
    has_auditor, has_blind = "auditor_only" in card, "advisory_blind" in card
    if not has_auditor and not has_blind:
        return None  # the ordinary case: an older backend publishes no split
    label = card.get("label", "?")
    bits: list[str] = []
    if has_auditor:
        auditor = card["auditor_only"]
        if auditor is None:
            bits.append("auditor-only split NOT computed")
        elif _is_number(auditor):
            bits.append(f"{auditor:g} auditor-only — the lexical advisory found no signal" if auditor else "0 auditor-only")
        else:
            _warn(
                f"render_suite: coverage card {label!r} carries an unusable `auditor_only` "
                f"({card['auditor_only']!r}) — it must be a flow COUNT, or null when no split ran, so no "
                "auditor-only marker is rendered"
            )
            return None
    if has_blind:
        blind = card["advisory_blind"]
        if blind is None:
            bits.append("advisory reach UNKNOWN")
        elif not _is_number(blind):
            _warn(
                f"render_suite: coverage card {label!r} carries an unusable `advisory_blind` "
                f"({card['advisory_blind']!r}) — it must be a flow COUNT, or null when the advisory's reach is "
                "unknown, so no auditor-only marker is rendered"
            )
            return None
        elif blind:
            bits.append(f"{blind:g} unreadable by the advisory")
    return ", ".join(bits) or None


#: The coverage chart renders THREE cards. The canvas WIDTH is computed from the count
#: (`pad * 2 + n * cw + (n - 1) * gap` in `render_coverage_svg`), so a fourth card is DRAWN, not
#: clipped — it does not run off the SVG, it makes the SVG bigger: 886px for three, 1182px for
#: four, against the 854-860px every other chart in this file emits. That is the real constraint
#: and it is about the REPORT, not the canvas: this chart sits in a column of same-width charts,
#: and one a third wider is scaled down beside them, taking its 40px figures with it. So three is
#: a layout decision; what WAS an oversight is dropping the rest in silence.
_MAX_COVERAGE_CARDS = 3


def _visible_cards(cards: list) -> list:
    """The first three cards, and a WARNING naming any the render throws away.

    A backend that starts publishing a fourth coverage card renders it nowhere and says nothing:
    the report looks complete, and the number nobody sees is indistinguishable from a number the
    backend never computed. Every other unusable coverage shape in this file already warns —
    a mistyped verified/declared pair, an unusable `auditor_only`, a `coverage` block with no
    cards.
    """
    kept, dropped = list(cards)[:_MAX_COVERAGE_CARDS], list(cards)[_MAX_COVERAGE_CARDS:]
    if dropped:
        labels = ", ".join(repr(c.get("label", "?")) if isinstance(c, dict) else repr(c) for c in dropped)
        message = (
            f"render_suite: coverage carries {len(cards)} cards and only the first {_MAX_COVERAGE_CARDS} are "
            f"rendered — DROPPED: {labels}. The canvas grows with the count, so a fourth is not clipped; it "
            "is dropped to keep this chart the same width as the report's other charts. Reorder "
            "`coverage.cards` to put the three that matter first, or say the dropped number in the round's "
            "notes; an unrendered card reads as one the backend never computed."
        )
        # Both coverage surfaces truncate, and one warning per report is the finding — two
        # copies of the same sentence is noise the reader learns to scroll past.
        if message not in _NOTES:
            _warn(message)
    return kept


def _dots(n: int, accent: str) -> str:
    return f'<tspan fill="{accent}">' + "●" * max(0, min(5, int(n))) + "</tspan>"


def _rating(n) -> str:
    """A 1–5 complexity rating shown with its denominator, e.g. '●●○○○ (2/5)'."""
    n = max(0, min(5, int(n or 0)))
    return "●" * n + "○" * (5 - n) + f" ({n}/5)"


# ── SVG: coverage cards ───────────────────────────────────────────────────────
def _with_unit(value, unit) -> str:
    """`29%` for a percent card (the default unit), `29 of 68` for a count card: a word unit is spaced off the number."""
    unit = "%" if unit is None else str(unit).strip()
    return f"{value}{unit}" if unit in ("%", "") else f"{value} {unit}"


def render_coverage_svg(cards: list[dict]) -> str:
    cards = _visible_cards(cards)
    if not cards:
        return _svg(854, 40, "")
    cw, gap, pad = 262, 34, 16
    w = pad * 2 + len(cards) * cw + (len(cards) - 1) * gap
    # The auditor-only marker is validated (a malformed one is noted in render_notes.txt) and never
    # drawn: how the audit credited a flow is loop material, not the operator's chart.
    for c in cards:
        if isinstance(c, dict):
            _credit_note(c)
    h = 132
    p = []
    for i, c in enumerate(cards):
        x = pad + i * (cw + gap)
        accent = ACCENTS.get(c.get("accent", "blue"), BLUE)
        val = _with_unit(c["value"], c.get("unit"))
        p.append(f'<rect x="{x}" y="16" width="{cw}" height="100" rx="14" fill="{PANEL}" stroke="{STROKE}"/>')
        p.append(f'<circle cx="{x + 26}" cy="42" r="3.5" fill="{accent}"/>')
        p.append(f'<text x="{x + 26}" y="80" font-size="40" font-weight="600" font-family="{MONO}" fill="{TEXT}">{escape(val)}</text>')
        label = f'<text x="{x + 28}" y="102" font-size="12.5" fill="{LABEL}">{escape(c["label"])}'
        # The VERIFIED / DECLARED split, when the backend typed one. The headline number counts
        # flows the audit could not verify as covered (it fails open by design), so a single
        # figure over-claims — a live set printed `uncovered: []` over three declared credits.
        # Rendered ONLY from the two typed numbers: a percentage can never be manufactured from
        # the legacy credit map, because that path has no numbers to divide.
        split = _coverage_split(c)
        if split is not None:
            verified, declared = split
            label += (f'<tspan x="{x + 28}" dy="15" font-size="11" fill="{MUTED}">'
                      f'verified {_with_unit(f"{verified:g}", c.get("unit"))} (+{_with_unit(f"{declared:g}", c.get("unit"))} declared)</tspan>')
        p.append(label + "</text>")
    return _svg(w, h, "".join(p))


# ── SVG: scenario complexity (ranked bars) ────────────────────────────────────
def render_complexity_svg(scenarios: list[dict], subtitle: str) -> str:
    rows = [s for s in scenarios if s.get("complexity") is not None]
    rows.sort(key=lambda s: s["complexity"], reverse=True)
    if not rows:
        return _svg(860, 60, "")
    hi = max(s["complexity"] for s in rows)
    unit = next((s.get("complexity_unit") for s in rows if s.get("complexity_unit")), "")
    top, rh, bx, bw = 56, 34, 320, 360
    w, h = 860, top + len(rows) * rh + 12
    p = [
        f'<text x="26" y="30" font-size="13" font-weight="700" fill="{TEXT}" letter-spacing="0.04em">SCENARIO COMPLEXITY</text>',
        f'<text x="200" y="30" font-size="11.5" fill="{MUTED}">{escape(subtitle)}</text>',
    ]
    for i, s in enumerate(rows):
        y = top + i * rh
        val = s["complexity"]
        # `if hi > 0`, not `if hi`: a suite where every scenario walked a single flow has
        # hi == 0.0 bits — a legitimate value that is FALSY, which silently rendered every
        # bar at zero width and read as a broken chart rather than a flat one.
        frac = val / hi if hi > 0 else 0
        accent = s.get("accent") and ACCENTS.get(s["accent"]) or _complexity_accent(val, hi)
        p.append(f'<text x="306" y="{y + 13}" text-anchor="end" font-size="11.5" fill="{LABEL}">{escape(_trunc(_title(s), 34))}</text>')
        p.append(f'<rect x="{bx}" y="{y}" width="{bw}" height="16" rx="4" fill="{TRACK}"/>')
        p.append(f'<rect x="{bx}" y="{y}" width="{max(6, bw * frac):.0f}" height="16" rx="4" fill="{accent}"/>')
        vtxt = f'{val:g}'
        p.append(f'<text x="{bx + bw + 10}" y="{y + 13}" font-size="11.5" font-weight="700" fill="{TEXT}" font-family="{MONO}">{escape(vtxt)}</text>')
    if unit:
        p.append(f'<text x="{bx + bw + 10}" y="{top - 26}" font-size="10.5" fill="{MUTED}">{escape(unit)}</text>')
    return _svg(w, h, "".join(p))


# ── SVG: environment (world-state schema; + optional bounds/noise) ─────────────
def render_environment_svg(env: dict) -> str:
    schema = env.get("schema") or []
    bounds = env.get("bounds") or []
    noise = env.get("noise") or []
    w = 860
    p = []
    y = 0

    def section_title(title, sub, yy):
        p.append(f'<text x="26" y="{yy}" font-size="13" font-weight="700" fill="{TEXT}" letter-spacing="0.04em">{escape(title)}</text>')
        if sub:
            p.append(f'<text x="{26 + 11 * len(title) + 12}" y="{yy}" font-size="11.5" fill="{MUTED}">{escape(sub)}</text>')

    # schema (always available)
    if schema:
        y = 34
        section_title("WORLD-STATE SCHEMA", "record types + fields each scenario's starting data is drawn from", y)
        for ent in schema:
            y += 30
            card = ent.get("cardinality")
            p.append(f'<text x="30" y="{y}" font-size="13" font-weight="700" font-family="{MONO}" fill="{PURPLE}">{escape(ent["entity"])}</text>')
            if card:
                p.append(f'<text x="{30 + 10 * len(ent["entity"]) + 14}" y="{y}" font-size="11" fill="{MUTED}">{escape(card)}</text>')
            for f in ent.get("fields", []):
                y += 26
                p.append(f'<text x="46" y="{y}" font-size="12.5" font-family="{MONO}" fill="{BLUE}">{escape(f["name"])}</text>')
                p.append(f'<text x="250" y="{y}" font-size="12" font-family="{MONO}" fill="{MUTED}">{escape(f.get("type", ""))}</text>')
                extra = f.get("range") or f.get("note") or ""
                if extra:
                    p.append(f'<text x="360" y="{y}" font-size="11.5" font-family="{MONO}" fill="{LABEL}">{escape(extra)}</text>')
        y += 18

    # bounds (trace-derived; optional)
    if bounds:
        y += 24
        p.append(f'<line x1="26" y1="{y - 18}" x2="834" y2="{y - 18}" stroke="{STROKE}"/>')
        section_title("WORLD BOUNDS", "value ranges the agent operates within — observed band vs. hard cap", y)
        for b in bounds:
            y += 34
            frac = max(0.0, min(1.0, float(b.get("fill", 0))))
            p.append(f'<text x="30" y="{y}" font-size="13" font-family="{MONO}" fill="{BLUE}">{escape(b["name"])}</text>')
            p.append(f'<text x="248" y="{y}" font-size="12" font-family="{MONO}" fill="{MUTED}">{escape(b.get("type", ""))}</text>')
            p.append(f'<text x="312" y="{y}" font-size="13" font-family="{MONO}" fill="{TEXT}">{escape(str(b.get("observed", "")))}</text>')
            p.append(f'<rect x="470" y="{y - 9}" width="150" height="7" rx="3.5" fill="{TRACK}"/>')
            p.append(f'<rect x="470" y="{y - 9}" width="{max(6, 150 * frac):.0f}" height="7" rx="3.5" fill="{BLUE}"/>')
            p.append(f'<text x="640" y="{y}" font-size="11.5" fill="{MUTED}">{escape(str(b.get("cap", "")))}</text>')
        y += 14

    # noise / failure modes (trace-derived; optional)
    if noise:
        y += 24
        p.append(f'<line x1="26" y1="{y - 18}" x2="834" y2="{y - 18}" stroke="{STROKE}"/>')
        section_title("FAILURE MODES & NOISE", "injected into the mock surface", y)
        hi = max((n.get("rate", 0) for n in noise), default=1) or 1
        for n in noise:
            y += 32
            accent = ACCENTS.get(n.get("accent", "orange"), ORANGE)
            frac = (n.get("rate", 0) / hi)
            p.append(f'<text x="30" y="{y}" font-size="13" font-family="{MONO}" fill="{TEXT}">{escape(n["name"])}</text>')
            p.append(f'<rect x="248" y="{y - 10}" width="360" height="13" rx="3" fill="{TRACK}"/>')
            p.append(f'<rect x="248" y="{y - 10}" width="{max(8, 360 * frac):.0f}" height="13" rx="3" fill="{accent}"/>')
            p.append(f'<text x="620" y="{y}" font-size="12.5" font-weight="700" font-family="{MONO}" fill="{accent}">{escape(str(n["rate"]))}%</text>')
            if n.get("note"):
                p.append(f'<text x="700" y="{y}" font-size="11" fill="{MUTED}">{escape(n["note"])}</text>')
        y += 14

    return _svg(w, int(y + 16), "".join(p), rx=14)


# ── complexity: say what the numbers ACTUALLY are ─────────────────────────────
# The suite spec carries complexity in ONE of two currencies and the report must not
# conflate them: path surprisal in BITS (the normal case — derived from the walk_trace
# through the flow graph), or the server's 1–5 `complexity_level` (the skill's documented
# fallback when walk_trace ids don't map cleanly onto graph nodes, which drops
# `complexity_unit`). Emitting the surprisal explainer over level numbers would misdescribe the
# report's own figures.
# The fallback is stated PER SCENARIO ("if walk_trace ids don't map cleanly, fall back to
# complexity_level, drop complexity_unit"), so a suite MIXES currencies routinely. Returning
# the FIRST unit found would claim bits for all of them and plot a level 3 beside 7.5
# bits on one axis. So: group by unit, plot only the largest comparable cohort, and say the
# split out loud.
def complexity_cohorts(scenarios: list[dict]) -> dict[str, list[dict]]:
    """Graded scenarios grouped by their complexity unit ('' = the 1–5 level fallback)."""
    out: dict[str, list[dict]] = {}
    for s in scenarios:
        if s.get("complexity") is None:
            continue
        out.setdefault(s.get("complexity_unit") or "", []).append(s)
    return out


def _dominant(scenarios: list[dict]) -> tuple[str, list[dict]]:
    cohorts = complexity_cohorts(scenarios)
    if not cohorts:
        return "", []
    # Largest cohort wins; ties break on the unit name so the render stays deterministic.
    unit = max(cohorts, key=lambda u: (len(cohorts[u]), u))
    return unit, cohorts[unit]


def complexity_unit(scenarios: list[dict]) -> str:
    """The unit of the cohort this report PLOTS; '' means the 1–5 level fallback.

    On a mixed suite this is the majority cohort's unit — never a label for the rest. Read
    it together with ``complexity_mix_note``, which is what tells the reader about the split.
    """
    return _dominant(scenarios)[0]


def plottable_scenarios(scenarios: list[dict]) -> list[dict]:
    """The comparable subset: the graded scenarios sharing the dominant unit. Plotting the
    others on the same axis would put a 1–5 level next to a bits figure."""
    return _dominant(scenarios)[1]


def _unit_words(unit: str, n: int) -> str:
    return f"{n} in {unit}" if unit else f"{n} as bare 1–5 levels"


def complexity_mix_note(scenarios: list[dict]) -> str:
    """One honest sentence when the suite carries more than one complexity currency."""
    cohorts = complexity_cohorts(scenarios)
    if len(cohorts) < 2:
        return ""
    unit, plotted = _dominant(scenarios)
    others = ", ".join(_unit_words(u, len(rows)) for u, rows in sorted(cohorts.items()) if u != unit)
    label = unit if unit else "bare 1–5 level"
    return (
        f"_**These scenarios are scored in more than one currency** — {_unit_words(unit, len(plotted))}, "
        f"{others}. The two are NOT comparable, so the chart above plots only the {label} group; "
        f"the rest appear in the table below but are not plotted. (The generator falls back to a "
        f"bare 1–5 level for any scenario whose walk trace doesn't map onto the flow graph.)_"
    )


def _levels_in_range(scenarios: list[dict]) -> bool:
    """Are the unit-less values actually in the generator's 1–5 range?"""
    return all(1 <= s["complexity"] <= 5 for s in scenarios if s.get("complexity") is not None)


_SURPRISAL_BLURB = (
    "_**Complexity = path surprisal**: −log₂ of the route's probability — where the route "
    "starts, times each step it takes — measured in bits, so rarer routes through the agent "
    "score higher. The dot rating beside each scenario is a SEPARATE axis: the 1–5 difficulty "
    "the suite asked that scenario to be. They need not agree — a hard scenario can walk a "
    "common route, and a rare route can be an easy one._"
)
_LEVEL_BLURB = (
    "_**Complexity = the generator's 1–5 complexity level** for each scenario. Path surprisal "
    "(−log₂ of the path probability, in bits) was not derivable for this suite — the scenarios' "
    "walk traces don't map cleanly onto the flow graph — so these are difficulty levels, not bits, "
    "and they are comparable to each other but not to a bits-scored suite._"
)
_SURPRISAL_SUBTITLE = "path surprisal — rarer paths score higher"
_LEVEL_SUBTITLE = "generator complexity level (1–5) — higher is harder"


_UNKNOWN_UNIT_BLURB = (
    "_**Complexity** carries no unit in this suite and the values fall outside the "
    "generator's 1–5 level range, so this report does not name what they measure: they are "
    "comparable to each other and to nothing else._"
)
_UNKNOWN_UNIT_SUBTITLE = "unlabeled complexity — comparable within this suite only"


def complexity_blurb(unit: str, scenarios: list[dict] | None = None) -> str:
    if unit == "bits":
        return _SURPRISAL_BLURB
    if unit:
        return f"_**Complexity** is reported in {unit} — higher is harder._"
    # No unit means the documented 1–5 fallback — but only if the numbers ARE 1–5. Claiming
    # the level scale over a 7.5 would misdescribe the figures exactly like the bits case.
    if scenarios is not None and not _levels_in_range(scenarios):
        return _UNKNOWN_UNIT_BLURB
    return _LEVEL_BLURB


def complexity_subtitle(unit: str, scenarios: list[dict] | None = None) -> str:
    """Default chart subtitle. MUST track the unit: a level-based suite that inherited the
    surprisal default claimed bits on the chart while the page said otherwise."""
    if unit == "bits":
        return _SURPRISAL_SUBTITLE
    if unit:
        return f"{unit} — higher is harder"
    if scenarios is not None and not _levels_in_range(scenarios):
        return _UNKNOWN_UNIT_SUBTITLE
    return _LEVEL_SUBTITLE


# ── per-scenario page names ───────────────────────────────────────────────────
# Scenario ids are LLM-derived from backend scenario names and reach this script through a
# hand-assembled suite.json — they are untrusted input, and they were being interpolated
# straight into an output path. `id: "../evals"` wrote `<artifacts>/evals.md`, clobbering the
# EVAL CATALOG with a scenario page, and prune_stale's keep set then held `'../evals.md'`,
# which matches no page name, so the orphan was invisible forever; `../..` reaches the
# customer's repo root. An id containing `/` raised FileNotFoundError (no mkdir for the
# implied subdir) and killed the render AFTER scenarios.md and evals.md were already written.
#
# The FILENAME is sanitised to one safe segment; the ORIGINAL id stays in the page content
# and every link points at the real file, so nothing the reader sees is lossy.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def page_name(s: dict, index: int) -> str:
    """A safe single path segment for this scenario's page (no extension)."""
    raw = s.get("id")
    raw = str(raw) if raw not in (None, "") else ""
    slug = _UNSAFE.sub("-", raw).strip("-._")
    if not raw:
        return f"scenario-{index + 1}"
    if slug != raw or not slug:
        # Sanitising CHANGED the id, so the slug is no longer a unique key ("a/b" and "a-b"
        # collapse). Pin it back to the original with a short digest.
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        _warn(f"render_suite: scenario id {raw!r} is not a safe filename — page written as {slug or 'scenario'}-{digest}.md")
        return f"{slug or 'scenario'}-{digest}"
    return slug


def scenario_pages(spec: dict) -> list[tuple[dict, str]]:
    """(scenario, page name) for every scenario in the spec, in order."""
    return [(s, page_name(s, i)) for i, s in enumerate(spec.get("scenarios") or [])]


# ── markdown ──────────────────────────────────────────────────────────────────
def _bits_label(s: dict) -> str:
    if s.get("complexity") is None:
        return ""
    u = s.get("complexity_unit", "")
    return f'{s["complexity"]:g}{(" " + u) if u else ""}'


def _title(s: dict) -> str:
    """A scenario with no title is a partial assembly, not a reason to crash the report."""
    return str(s.get("title") or s.get("id") or "(untitled scenario)")


#: `final_bundle.py stage`'s hold-back reasons, as the sentence the report reads them by.
_HELD_BACK_REASONS = {
    "harness_cannot_replay": "the harness cannot replay a recorded conversation yet",
    "over_cap": "it did not fit under the scenario cap",
    "unfinished": "its conversion never finished",
}


#: Why a folder asked for did not ship, as `final_bundle.py missing` records it: the backend's
#: `bundle_folders_attempted_reasons`, or `not_handed_over` for a folder the build never handed it.
_MISSING_REASONS = {
    "infra_fault": "not judged: the cause was outside the folder (provider, configuration or host)",
    "failed_validation": "authored, and did not pass validation",
    "session_fault": "its authoring session failed",
    "unknown": "no reason recorded",
    "not_handed_over": "never handed to this build",
}


def _missing_rows(entries: object) -> list[str]:
    """One bullet per missing folder, with its reason when `suite.json` carries one."""
    rows = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict):
            folder, reason = str(entry.get("folder") or "").strip(), str(entry.get("reason") or "").strip()
        else:
            folder, reason = str(entry or "").strip(), ""
        if not folder:
            continue
        rows.append(f'- `{folder}` — {_MISSING_REASONS.get(reason, reason)}' if reason else f'- `{folder}`')
    return rows


def _shortfall_line(spec: dict, scenarios: list) -> str | None:
    """`> **N of M requested scenarios shipped**`, when fewer shipped than were asked for.

    The customer render would be SILENT on a short set (say 5 of 7 requested scenarios shipped):
    it counts what it has, so a short set reads exactly like a complete one. Absent `requested_count` (an older
    backend, or a bundle run) says nothing rather than assuming the count it happens to see.

    `requested_count` is the WALK's `count`, so only the walk's scenarios (`source: spec`, the
    default) count against it; a regression or trace row has its own lost-folder list below.
    """
    shipped = sum(1 for s in scenarios if isinstance(s, dict) and s.get("source", "spec") == "spec")
    requested = spec.get("requested_count")
    if not isinstance(requested, int) or isinstance(requested, bool) or requested <= shipped:
        return None
    missing = requested - shipped
    return (f'> **{shipped} of {requested} requested scenarios shipped** — '
            f'{missing} slot{"" if missing == 1 else "s"} failed generation.')


def _coverage_blurb(coverage: dict) -> str:
    """What the chart's cards measure, said per kind: the simulated tools the scored run called, and/or the flow-graph share."""
    parts = []
    if isinstance(coverage.get("reached"), dict):
        parts.append("how many of the agent's **simulated tools** the scored run actually called")
    if any(isinstance(c, dict) and c.get("unit", "%") == "%" for c in coverage["cards"]):
        parts.append("the share of the agent's **flow-graph paths** — the valid routes a user can take "
                     "through its tools/flows — that these scenarios exercise")
    body = " and ".join(parts) or "what these scenarios exercise of the agent"
    return f"_{body[0].upper()}{body[1:]}. Higher means more of the agent's real behaviour is actually under test._"


def render_scenarios_md(spec: dict) -> str:
    scen = spec.get("scenarios") or []
    L = ["# Scenarios", ""]
    shortfall = _shortfall_line(spec, scen)
    if shortfall:
        _warn(f"render_suite: {shortfall.lstrip('> ')}")
        L += [shortfall, ""]
    coverage = spec.get("coverage") or {}
    # A `coverage` block with no usable `cards` renders NO coverage section at all, and a
    # missing section is indistinguishable from a suite whose coverage was never computed.
    # Every other missing field here gets a note; a silent omission here would drop a whole
    # chart. Said once, naming the key and the surface to re-read — the same shape as the
    # shortfall line above.
    # An EMPTY list is the builder's deliberate answer (tools in no flow, no scored run: no honest card to draw).
    if coverage and not isinstance(coverage.get("cards"), list):
        _warn("render_suite: suite.json has a `coverage` block but no usable `coverage.cards` — "
              "the Coverage section and its chart are OMITTED. Re-read the scenario job's "
              "`coverage` roll-up (get_artifact_meta -> detail.coverage) and rebuild the cards; "
              "an absent section reads as a suite whose coverage nobody computed.")
    if coverage.get("cards"):
        L += ["## Coverage", "", _coverage_blurb(coverage), "", "![Coverage](charts/scenarios_coverage.svg)", ""]
        # The caption path: an older backend publishes only the legacy per-flow credit map, whose
        # values are CREDITS and not flows — they do not subtract from a flow denominator, so any
        # percentage derived from them is wrong by construction. It gets a sentence, never a
        # number the reader can mistake for a rate.
        caption = coverage.get("caption")
        if isinstance(caption, str) and caption.strip():
            L += [f"_{caption.strip()}_", ""]
    if any(s.get("complexity") is not None for s in scen):
        plotted = plottable_scenarios(scen)
        L += ["## Complexity", "", complexity_blurb(complexity_unit(scen), plotted), ""]
        mix = complexity_mix_note(scen)
        if mix:
            L += [mix, ""]
        L += ["![Scenario complexity](charts/scenarios_complexity.svg)", ""]
    L += ["## Scenarios", "", "| Scenario | Path likelihood | Complexity | Persona |", "|---|---|---|---|"]
    for s, page in scenario_pages(spec):
        like = f'{s["likelihood"]:g}%' if s.get("likelihood") is not None else "—"
        rating = _rating(s["stars"]) if s.get("stars") else "—"
        pcell = _persona_text(s.get("persona"), limit=120)
        # Link to the REAL page: a sanitised id means the filename no longer echoes it.
        L.append(f'| [{_title(s)}](scenarios/{page}.md) | {like} | {rating} | {pcell} |')
    L.append("")
    # Regressions the final batch asked for and the set did not ship. The backend does not fail a
    # walk over a folder it cannot author against the current environment, so without this section
    # the suite renders complete while the issues those folders reproduce go ungraded.
    missing = _missing_rows(spec.get("missing_regressions"))
    if missing:
        L += ["## Regressions NOT in this suite", "",
              "_Each of these replays a confirmed agent issue and was asked for on this build, and "
              "did not ship, for the reason beside it. A folder the backend cannot author does not "
              "fail the build — so everything in the table above is here, and these are not. The "
              "issues they reproduce are ungraded by this suite._", ""]
        L += missing
        L.append("")
    # Regressions that shipped but whose eval-set row is declined on the record: no check owns
    # their reply, so the issue they reproduce is ungraded as surely as a lost folder's.
    declined = _missing_rows(spec.get("declined_regressions"))
    if declined:
        L += ["## Regressions declined on the record", "",
              "_Each of these replays a confirmed agent issue whose eval-set row was declined on the "
              "record, for the reason beside it, so no check grades its reply. The issues they "
              "reproduce are ungraded by this suite._", ""]
        L += declined
        L.append("")
    # Trace scenarios lost the same way — kept apart from the regressions because a trace scenario
    # carries no confirmed issue, only a failure production showed.
    lost_traces = _missing_rows(spec.get("missing_trace_scenarios"))
    if lost_traces:
        L += ["## Trace scenarios NOT in this suite", "",
              "_Each of these continues a production conversation cut just before a failure, was "
              "asked for on this build, and did not ship, for the reason beside it. A folder the "
              "backend cannot author does not fail the build. The production failures they replay "
              "are ungraded by this suite._", ""]
        L += lost_traces
        L.append("")
    # Trace scenarios the stage never handed over, with the stage's reason. Written by the plugin,
    # not the backend, so the reason vocabulary is closed and each one reads as a sentence here.
    held = [h for h in (spec.get("held_back_trace_scenarios") or [])
            if isinstance(h, dict) and str(h.get("folder") or "").strip()]
    if held:
        L += ["## Trace scenarios held back from this suite", "",
              "_These were left out of the batch before it was generated; the walk and the "
              "regressions shipped without them._", ""]
        for h in held:
            reason = _HELD_BACK_REASONS.get(str(h.get("reason") or ""), str(h.get("reason") or "no reason recorded"))
            L.append(f'- `{str(h["folder"]).strip()}` — {reason}')
        L.append("")
    # Only dict entries render as bullets — a list of all non-dict entries would
    # otherwise produce the heading + intro with zero bullets under them.
    gaps = [g for g in (spec.get("capability_gaps") or []) if isinstance(g, dict)]
    if gaps:
        L += ["## Capability gaps", "",
              "_Scenario pressure the agent has no surface for — the generator flagged these while "
              "authoring the suite. Close each by refining the PRD's purpose/design-constraints per "
              "its suggested fix, or by adding the missing tool._", ""]
        for g in gaps:
            affected = g.get("affected_scenarios") or []
            aff = f'  ·  affects: {", ".join(str(a) for a in affected)}' if affected else ""
            L.append(f'- **{g.get("capability", "—")}** — {g.get("why_natural", "")}{aff}')
            if g.get("suggested_fix"):
                L.append(f'  - _Suggested fix:_ {g["suggested_fix"]}')
        L.append("")
    L += render_knowledge_md(spec)
    env = spec.get("environment") or {}
    if env.get("schema"):
        L += ["## Environment", "", "![Environment](charts/environment.svg)", "",
              "_The record types and fields each scenario's starting data is drawn from._", ""]
    L += ["> Per-scenario detail (path · probability · persona · ground-truth) is linked from each row above.", ""]
    return "\n".join(L)


# ── knowledge coverage: per document ─────────────────────────────────────────
# Every other surface gives the corpus as totals. This joins the typed per-document fields —
# the extract's chunk counts, the build's set-aside map, the set's reach — into one row per
# document, so the reader sees WHICH documents were extracted, kept, planted and graded.
_NO_KNOWLEDGE_LINE = "_This suite has no knowledge base, so no conversation asks a question answered from documents._"
#: `suite.json` `knowledge_missing`: the map says the agent answers from a knowledge base, and none was built.
_KNOWLEDGE_NOT_BUILT = ("_Your agent answers from a knowledge base, but none was built for this suite{why}, so no conversation "
                        "checks its answers against it._")
_READER_MISSING = ", because the document reader could not be installed"


def _count_map(block: dict, key: str) -> dict[str, int]:
    """`{doc: int}` off the knowledge block, dropping (and noting) a non-integer entry."""
    raw = block.get(key)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _warn(f"render_suite: knowledge.{key} is {type(raw).__name__}, not a {{document: count}} map — ignored")
        return {}
    out = {str(k): v for k, v in raw.items() if _is_number(v)}
    if len(out) != len(raw):
        _warn(f"render_suite: knowledge.{key} carries {len(raw) - len(out)} non-numeric count(s) — dropped")
    return out


def knowledge_documents(block: dict) -> list[dict]:
    """One row per document: `{name, chunks, set_aside, planted, corpus_chunks, scenarios}`; None = not published."""
    per_doc = _count_map(block, "per_document")
    set_aside = _count_map(block, "set_aside_documents")
    reach = block.get("knowledge_reach") if isinstance(block.get("knowledge_reach"), dict) else {}
    by_doc = reach.get("by_document") if isinstance(reach.get("by_document"), dict) else {}
    rows = []
    for name in sorted(set(per_doc) | set(set_aside) | set(by_doc)):
        doc = by_doc.get(name) if isinstance(by_doc.get(name), dict) else {}
        rows.append({
            "name": name,
            "chunks": per_doc.get(name) if per_doc else None,
            "set_aside": set_aside.get(name, 0) if "set_aside_documents" in block else None,
            "planted": doc.get("chunks_planted") if by_doc else None,
            "corpus_chunks": doc.get("corpus_chunks"),
            "scenarios": [str(x) for x in doc.get("scenarios") or []],
        })
    return rows


def render_knowledge_md(spec: dict) -> list[str]:
    block = spec.get("knowledge")
    L = ["## Knowledge coverage", ""]
    if not isinstance(block, dict) or not block:
        missing = spec.get("knowledge_missing")
        if isinstance(missing, dict):
            return L + [_KNOWLEDGE_NOT_BUILT.format(why=_READER_MISSING if missing.get("reader_missing") is True else ""), ""]
        return L + [_NO_KNOWLEDGE_LINE, ""]
    rows = knowledge_documents(block)
    reach = block.get("knowledge_reach") if isinstance(block.get("knowledge_reach"), dict) else {}
    if not isinstance(reach.get("by_document"), dict):
        _warn("render_suite: knowledge.knowledge_reach carries no `by_document` — the Scenarios and Evals columns render "
              "'—'. Copy detail.dataset_metadata.knowledge_reach off get_artifact_meta(<scenario_set_id>) verbatim.")
    evals = {str(e.get("id")) for e in spec.get("evals") or []}
    graders = [str(g) for g in block.get("graders") or []]
    unknown = [g for g in graders if evals and g not in evals]
    if unknown:
        _warn(f"render_suite: knowledge.graders names {unknown}, which the suite's evals do not carry")
    titles = {str(s.get("id")): (s, page) for s, page in scenario_pages(spec)}
    missing = sorted({sc for r in rows for sc in r["scenarios"] if sc not in titles})
    if missing:
        _warn(f"render_suite: knowledge_reach names scenario(s) {missing} that suite.json does not carry — shown by id")

    def _scen(sid: str) -> str:
        if sid not in titles:
            return f"`{sid}`"
        s, page = titles[sid]
        return f"[{_title(s)}](scenarios/{page}.md)"

    def _num(v) -> str:
        return "—" if v is None else f"{v:g}"

    L += ["_Each document of the corpus: the chunks it was extracted into, how many of them the corpus build set "
          "aside (never reaching the graph, so never planted), the chunks the scenarios' planted knowledge questions are "
          "graded on, the scenarios that plant it, and the evals that grade those questions._", "",
          "| Document | Chunks | Set aside | Chunks planted | Scenarios that plant it | Evals that grade it |",
          "|---|---|---|---|---|---|"]
    for r in rows:
        aside = _num(r["set_aside"]) if r["set_aside"] else ("kept" if r["set_aside"] == 0 else "—")
        scen = "<br>".join(_scen(x) for x in r["scenarios"]) or "—"
        grade = ", ".join(f"`{g}`" for g in graders) if r["scenarios"] and graders else "—"
        L.append(f"| `{_cell(r['name'])}` | {_num(r['chunks'])} | {aside} | {_num(r['planted'])} | {scen} | {grade} |")
    totals = []
    if any(r["chunks"] is not None for r in rows):
        totals.append(f"{sum(r['chunks'] or 0 for r in rows):g} chunk(s) from {len(rows)} document(s)")
    if any(r["set_aside"] is not None for r in rows):
        totals.append(f"{sum(r['set_aside'] or 0 for r in rows):g} set aside by the build")
    if _is_number(reach.get("chunks_planted")):
        planted = sum(r["planted"] or 0 for r in rows)
        if isinstance(reach.get("by_document"), dict) and planted != reach["chunks_planted"]:
            _warn(f"render_suite: knowledge_reach.by_document plants {planted} chunk(s) but chunks_planted is "
                  f"{reach['chunks_planted']} — the per-document split does not add up to the set total")
        of = f" of {reach['corpus_chunks']:g}" if _is_number(reach.get("corpus_chunks")) else ""
        totals.append(f"{reach['chunks_planted']:g}{of} graph chunk(s) planted by {reach.get('planted_turns', 0):g} knowledge question(s)")
    if totals:
        L += ["", f"_{' · '.join(totals)}._"]
    return L + [""]


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|")


#: Where the qualification passes leave their per-run results, relative to --artifacts-dir.
_QUAL_RUNS_GLOB = "runs/qual-*/run.json"

#: The verdict axes an eval can land on without deciding, in tie-break order. ``not_applicable`` is
#: a draw that never planted the eval's precondition (steering reaches it); the others are not.
_UNDECIDED_AXES = ("not_applicable", "skipped", "errored", "inconclusive")

#: The axis of an eval no readable qualification run reported at all (added after the last pass).
_UNREPORTED = "unreported"


def qualification_counts(art: Path) -> dict[str, dict[str, int]] | None:
    """Per eval id, ``passed + failed`` (``decided``) and each undecided axis, summed over every
    qualification run under ``art``.

    ``None`` when no qualification run is readable: unknown, which is not the same as an eval no
    qualification scenario decided.
    """
    counts: dict[str, dict[str, int]] = {}
    readable = 0
    for path in sorted(art.glob(_QUAL_RUNS_GLOB)):
        try:
            evals = json.loads(path.read_text(encoding="utf-8")).get("evals") or []
        except (OSError, ValueError, AttributeError):
            _warn(f"render_suite: {path} unreadable — left out of the qualification count")
            continue
        readable += 1
        for row in evals:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            tally = counts.setdefault(str(row["id"]), dict.fromkeys(("decided", *_UNDECIDED_AXES), 0))
            # A deferred cell DECIDED: the eval it defers to carries that verdict, so the check was exercised.
            for key in ("passed", "failed", "deferred", *_UNDECIDED_AXES):
                if isinstance(value := row.get(key), int):
                    tally["decided" if key in ("passed", "failed", "deferred") else key] += value
    return counts if readable else None


def not_qualified(counts: dict[str, dict[str, int]] | None, eval_ids: list[str]) -> dict[str, str]:
    """``{eval_id: axis}`` for the evals of ``eval_ids`` no qualification run decided.

    The axis is the undecided verdict the eval landed on most (``unreported`` when no run reported
    it). Nothing is returned when no qualification run is known.
    """
    if counts is None:
        return {}
    out: dict[str, str] = {}
    for eval_id in eval_ids:
        tally = counts.get(eval_id)
        if tally is None:
            out[eval_id] = _UNREPORTED
        elif tally["decided"] == 0:
            top = max(_UNDECIDED_AXES, key=lambda axis: (tally[axis], -_UNDECIDED_AXES.index(axis)))
            out[eval_id] = top if tally[top] else _UNREPORTED
    return out


def mark_qualification(spec: dict, counts: dict[str, dict[str, int]] | None) -> None:
    """Stamp ``qualification: not_qualified`` + ``qualification_axis`` on every eval no
    qualification run decided.

    Computed from the run data on every render, never authored: markers already on the spec are
    dropped first, so a later qualification pass that decides an eval clears them.
    """
    evals = spec.get("evals") or []
    for e in evals:
        e.pop("qualification", None)
        e.pop("qualification_axis", None)
    missing = not_qualified(counts, [str(e.get("id")) for e in evals if e.get("id")])
    for e in evals:
        if (axis := missing.get(str(e.get("id")))) is not None:
            e["qualification"] = "not_qualified"
            e["qualification_axis"] = axis


#: The per-eval stored-label summary ``upload_eval_labels`` returned, saved by the audit under --artifacts-dir.
_EVAL_LABELS = "eval_labels.json"

#: The decided label kinds; ``undecidable`` is never a labelled example.
_DECIDED_LABELS = ("true_red", "false_red", "confirmed_pass", "false_pass")


def mark_eval_labels(spec: dict, art: Path) -> None:
    """Carry ``art/eval_labels.json``'s ``policies`` block on the spec as ``eval_labels``.

    Read on every render, never authored: a marker already on the spec is dropped first."""
    spec.pop("eval_labels", None)
    path = art / _EVAL_LABELS
    if not path.is_file():
        return
    try:
        policies = json.loads(path.read_text(encoding="utf-8")).get("policies")
    except (OSError, ValueError, AttributeError):
        _warn(f"render_suite: {path} unreadable — no eval recommended for optimization")
        return
    if isinstance(policies, dict):
        spec["eval_labels"] = policies


#: The evals the final run's corrections found at fault (``eval_rounds.py flagged``), saved under --artifacts-dir.
_FLAGGED_EVALS = "flagged_evals.json"


def mark_flagged_evals(spec: dict, art: Path) -> None:
    """Carry ``art/flagged_evals.json``'s ``policies`` on the spec as ``flagged_evals`` — only when the file is this
    suite's final set (``scenario_set_id``): a file another build left is read as none. Never authored."""
    spec.pop("flagged_evals", None)
    path = art / _FLAGGED_EVALS
    if not path.is_file():
        return
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _warn(f"render_suite: {path} unreadable — no eval flagged for improvement")
        return
    if isinstance(doc, dict) and isinstance(doc.get("policies"), dict) and doc["policies"] \
            and str(doc.get("scenario_set_id") or "") == str(spec.get("scenario_set_id") or "-"):
        # `rulings` is audit prose for the next build's eval generation, never suite content.
        spec["flagged_evals"] = {p: ({k: v for k, v in e.items() if k != "rulings"} if isinstance(e, dict) else e)
                                 for p, e in doc["policies"].items()}
        names = doc.get("display_names") if isinstance(doc.get("display_names"), dict) else {}
        for policy, summary in (spec.get("eval_labels") or {}).items():
            if isinstance(summary, dict) and isinstance(names.get(policy), str) and names[policy].strip():
                summary["display_name"] = names[policy].strip()


def render_optimize_md(labels: object, flagged: object = None) -> list[str]:
    """``## Evals to optimize``: each eval whose judge still misapplies a correct rule on the final run
    (``needs_optimization``). ``/optimize-eval`` is named only when its stored labels are enough gold
    (``gold_ready``, the backend's own predicate). An eval only ``flagged`` (``eval_rounds.py flagged``: a final-run
    correction with an eval-owned cause the labels do not carry) gets the eval-edit step. A recommendation — nothing is
    started."""
    labels = labels if isinstance(labels, dict) else {}
    flagged = flagged if isinstance(flagged, dict) else {}
    needs = {n for n, s in labels.items() if isinstance(s, dict) and s.get("needs_optimization")}
    names = sorted(needs | set(flagged))
    if not names:
        return []
    L = ["## Evals to optimize", "", "Recommended only — nothing runs until you start it.", "",
         "| Eval | Why | Labelled examples | Next step |", "|---|---|---|---|"]

    def said(name: str) -> str:
        """The eval by its display name (off ``flagged_evals.json``), its id beside it — the key this page and
        ``/optimize-eval`` use; the id alone where it has no name."""
        entry = flagged.get(name) if isinstance(flagged.get(name), dict) else labels.get(name)
        shown = entry.get("display_name") if isinstance(entry, dict) else None
        if isinstance(shown, str) and shown.strip() and shown.strip() != name:
            return f"{_cell(shown.strip())} (`{_cell(name)}`)"
        return f"`{_cell(name)}`"

    for name in names:
        causes = (flagged.get(name) or {}).get("causes") if isinstance(flagged.get(name), dict) else None
        structural = name not in needs and not (isinstance(causes, dict) and causes and set(causes) <= {"prompt"})
        if structural:  # a structural cause (rubric, evidence wiring, type): the optimizer's labels never see it
            L.append(f"| {said(name)} | our review of the final run found some of its results wrong "
                     f"| 0 | ask me to edit the evals |")
            continue
        if name not in needs:  # prompt-only: judge accuracy is the optimizer's, whatever the labels say yet
            ready = isinstance(labels.get(name), dict) and labels[name].get("gold_ready")
            step = f"`/optimize-eval {_cell(name)}`" if ready else "collect more runs first"
            L.append(f"| {said(name)} | its judge misapplies a correct rule | 0 | {step} |")
            continue
        count = {k: v if isinstance(v := labels[name].get(k), int) else 0 for k in _DECIDED_LABELS}
        examples = (f'{sum(count.values())} ({count["false_red"]} false reds, '
                    f'{count["true_red"] + count["confirmed_pass"]} confirmed)')
        step = f"`/optimize-eval {_cell(name)}`" if labels[name].get("gold_ready") else "collect more runs first"
        L.append(f"| {said(name)} | its judge misapplies a correct rule | {examples} | {step} |")
    return L + [""]


def render_goal_checks_md(scenarios: list) -> list[str]:
    """The per-scenario goal checks (``goal_check``, the scenario's compiled
    ``success_criteria.deterministic``), one entry per scenario carrying one; nothing when none does.

    They grade a run beside the catalog above and live in no policy, so the catalog alone hides a
    check that can fail a scenario."""
    rows = [s for s in scenarios if isinstance(s, dict) and isinstance(s.get("goal_check"), dict) and s["goal_check"].get("expression")]
    if not rows:
        return []
    L = ["## Per-scenario goal checks", "",
         f"**{len(rows)} scenario(s)** also carry a deterministic goal check, compiled into the scenario itself "
         "(`success_criteria.deterministic`) rather than the eval set. Each grades only its own scenario's run, "
         "beside the evals above.", ""]
    for s in rows:
        check = s["goal_check"]
        L.append(f'- `{s.get("id", "—")}` — `{check.get("label") or "unlabeled"}`')
        if check.get("rationale"):
            L.append(f'  {" ".join(str(check["rationale"]).split())}')
        # Verbatim (only indented into the list item): a collapsed space inside a string literal changes the check.
        L += ["", "  ```python", *(f"  {line}" for line in str(check["expression"]).strip().splitlines()), "  ```", ""]
    return L


def render_evals_md(spec: dict) -> str:
    evals = spec.get("evals") or []
    n = len(evals)
    L = [
        "# Eval suite", "",
        f"**{n} evals**. Each one is SCOPED — its applicability guard and flow scope decide which "
        "scenarios it grades — so per-eval coverage varies across the suite and no eval is "
        "assumed to score every scenario. The eval `id` is the feedback key on the run.", "",
        "## Catalog", "",
        "| Eval (feedback key) | Type | Scope | Score |",
        "|---|---|---|---|",
    ]
    for e in evals:
        L.append(f'| `{e.get("id", "—")}` | `{e.get("type", "")}` | {e.get("scope", "")} | {e.get("score", "")} |')
    L += ["",
          "Types: **`code`** — expressions in the backend `code_eval` sandbox; "
          "**`llm`** — judges scored per target; **`retrieval`** — retrieval metrics over the agent's "
          "cited/returned evidence; **`code + llm`** — code gathers ground truth, the judge reads the agent's text; "
          "**`knowledge beat`** — planted questions, and probes about facts the agent's world does not hold — "
          "resolved per scenario: a fraction over all of them (coverage / accuracy), or one probe's outcome.", ""]
    L += render_goal_checks_md(spec.get("scenarios") or [])
    unexercised = [e.get("id") for e in evals if e.get("qualification") == "not_qualified"]
    if unexercised:
        L += ["## Qualification", "", "No qualification scenario exercised these checks:", ""]
        L += [f"- `{i}`" for i in unexercised] + [""]
    L += render_optimize_md(spec.get("eval_labels"), spec.get("flagged_evals"))
    return "\n".join(L)


def render_scenario_detail_md(s: dict) -> str:
    # The ORIGINAL id, verbatim — the page FILENAME may be a sanitised slug, but what the
    # reader (and the backend) keys on is the id the suite actually carries.
    head = f'`{s.get("id", "—")}`  ·  source: **{s.get("source", "spec")}**'
    if s.get("stars"):
        head += f'  ·  complexity: {_rating(s["stars"])}'
    elif s.get("complexity") is not None:
        head += f'  ·  complexity: {_bits_label(s)}'
    L = [f'# Scenario — {_title(s)}', "", head, ""]
    if s.get("summary"):
        L += ["## What happens", "", s["summary"], ""]
    path = s.get("path") or []
    if path:
        L += ["## Path practiced", ""]
        L.append(" → ".join([path[0]["from"]] + [e["to"] for e in path]))
        L.append("")
        for i, e in enumerate(path, 1):
            pp = f'  ·  p = {e["p"]:g}' if e.get("p") is not None else ""
            L.append(f'{i}. {e["from"]} → {e["to"]}{pp}')
        L.append("")
        if all(e.get("p") is not None for e in path):
            prod = 1.0
            for e in path:
                prod *= e["p"]
            factors = " × ".join(f"{e['p']:g}" for e in path)
            # Two different denominators, so they must not be printed as one equation: the
            # per-edge p's are re-derived from the flow graph over ALL of a node's neighbours,
            # while the recorded probability is over the candidate set the walker actually
            # offered (unvisited, and knowledge flows excluded on a functional walk). Showing
            # `−log₂(product) = <recorded> bits` asserted an identity whose halves disagree on
            # any multi-hop walk. The recorded value wins; the factors stay as a route breakdown.
            recorded = s["likelihood"] / 100 if s.get("likelihood") is not None else None
            if recorded is not None:
                line = f"**Path probability** = **{recorded:.4g}** (per-edge, as weighted: {factors})"
            else:
                line = f"**Path probability** = {factors} = **{prod:.4g}**"
            if s.get("complexity_unit") == "bits":
                basis = recorded if recorded is not None else prod
                line += f"  →  surprisal = −log₂({basis:.4g}) = **{s['complexity']:g} bits**"
            L.append(line)
            L.append("")
    per = s.get("persona")
    if per:
        L += ["## Persona", "", _persona_text(per), ""]
    if s.get("ground_truth"):
        L += ["## Ground truth (code-invariant)", "", f'> {s["ground_truth"]}', ""]
    elif not s.get("single_hop"):  # a single-hop replay grades no goal (build_suite_json), by design
        # The section is the customer's only view of what the judges GRADE, so its absence is
        # a gap in the deliverable, not a formatting choice — and the page renders identically
        # whether the scenario_target was missing or the assembly dropped it.
        _warn(
            f'render_suite: scenario {s.get("id", "?")} has no \'ground_truth\' — the page ships with no statement '
            "of what the judges grade. Copy scenario.scenario_target VERBATIM from scenario.yaml "
            "(get_scenario(<scenario_set_id>, scenario_name=<name>), or get_artifact_meta's "
            "detail.scenarios[].scenario_target); never paraphrase it."
        )
    if s.get("world_state_note"):
        L += ["## World state", "", s["world_state_note"], ""]
    return "\n".join(L)


def _scalar_parts(per: dict, *, skip: tuple[str, ...]) -> list[str]:
    """``key: value`` for every non-empty scalar field of a persona dict outside ``skip``, in order."""
    return [f"{k}: {v}" for k, v in per.items() if k not in skip and isinstance(v, (str, int, float)) and str(v).strip()]


def _persona_text(per: object, limit: int = 0) -> str:
    """Faithful persona rendering. ``persona.yaml`` is a free-shape backend
    artifact — temperament / speaking_style / ... keys, or plain prose — NOT a
    ``{name, traits}`` record. Coercing a string into
    ``{'name': <whole text>, 'traits': ''}`` would PERSIST that into the canonical
    suite.json (the original text becoming structurally indistinguishable from an authored
    name), and a faithfully-copied backend dict would render as ``**—** · ``. Render what is
    there; invent no structure. ``limit`` > 0 ellipsizes (for table cells)."""
    if isinstance(per, str) and per.strip():
        text = per.strip()
    elif isinstance(per, dict) and per:
        if per.get("name") or per.get("traits"):
            name = str(per.get("name") or "—")
            traits = str(per.get("traits") or "")
            # The backend's own shape ({name, information, temperament, ...}) has no `traits`:
            # its other scalar keys ARE the persona, so they render after the name, not vanish.
            rest = traits or " · ".join(_scalar_parts(per, skip=("name", "traits", "emotion")))
            text = f"**{name}**" + (f" · {rest}" if rest else "")
        else:
            parts = _scalar_parts(per, skip=("emotion",))
            text = " · ".join(parts) if parts else "—"
        if per.get("emotion"):
            text += f'  ·  emotion **{per["emotion"]}**'
    else:
        return "—"
    text = " ".join(text.split())
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _normalize_spec(spec: dict) -> None:
    """Tolerant readers at the artifact boundary: the suite spec is assembled from server
    artifacts whose field types drift (the skill's own fallback yields complexity_level as a
    STRING like '3' or 'medium'; persona arrives as a plain string or a free-shape dict —
    handled at RENDER time by ``_persona_text``, never mutated here). A renderer must
    degrade a malformed field to absent-with-a-stderr-note — never crash the whole report over
    one cell, and NEVER invent a number for a word (the skill's own rule: a fabricated 3.0
    would persist into the canonical suite.json and chart as if real, unit-labeled 'bits').
    Malformed shapes covered: `val / hi` TypeError on a string complexity, int() on a string
    stars, format-g on a string likelihood, `.get` on a string persona, `for s in None` on a
    null `scenarios` (TypeError), and `s["id"]` / `s["title"]` on a partially-assembled
    scenario (KeyError). The last three matter because suite.json is hand-assembled by the
    LLM from several backend fetches: a fetch that returned nothing yields exactly those
    shapes, and the promise above is that ONE bad cell never costs the whole report."""

    # `scenarios` / `evals` must be LISTS OF DICTS from here down — every reader assumes it.
    for key in ("scenarios", "evals"):
        rows = spec.get(key)
        if rows is None:
            spec[key] = []
            continue
        if not isinstance(rows, list):
            _warn(f"render_suite: {key} is {type(rows).__name__}, not a list — dropped")
            spec[key] = []
            continue
        kept = [r for r in rows if isinstance(r, dict)]
        if len(kept) != len(rows):
            _warn(f"render_suite: dropped {len(rows) - len(kept)} non-object entr(y/ies) from {key}")
        spec[key] = kept

    def _num(row: dict, key: str, cast) -> None:
        v = row.get(key)
        if v is None or isinstance(v, (int, float)):
            return
        try:
            row[key] = cast(v)
        except (TypeError, ValueError):
            _warn(f'render_suite: scenario {row.get("id", "?")}: non-numeric {key} {v!r} dropped')
            row[key] = None

    # Snapshot presence BEFORE coercion. `_num` nulls a value it cannot cast and reports it, so
    # afterwards "the field was absent" and "the field was there but unparseable" are the same
    # shape — and the fallback's own documented word form ('medium') would be re-reported as an
    # assembly drop on top of the line `_num` already printed for it.
    present = [{key: row.get(key) for key in _WALK_FIELDS} for row in spec["scenarios"]]

    for s in spec["scenarios"]:
        if not s.get("title"):
            _warn(f"render_suite: scenario {s.get('id', '?')} has no 'title' — it renders by its id. "
                  "Author it in the suite overlay (`scenarios.<id>.title`) and build again.")
        _num(s, "complexity", float)
        _num(s, "stars", int)
        _num(s, "likelihood", float)
        # Persona is deliberately NOT normalized: coercing a string into
        # {'name': <text>, 'traits': ''} persisted the coercion into the canonical
        # suite.json, making the original prose indistinguishable from an authored name.
        # _persona_text renders every shape faithfully at read time instead.

    _warn_unpopulated(present)


# The walk numbers reach the report through ONE hand-copy — the skill reads them off
# get_artifact_meta and writes them into suite.json, so that hop is checked here. A MALFORMED
# value is caught above; an ABSENT one would render as a silent em-dash, indistinguishable from
# "the backend recorded no walk".
#
# A bare "likelihood is missing" check would cry wolf: the skill's DOCUMENTED fallback (older
# backend, or a bundle-built set — neither has a recorded walk) drops `likelihood` on purpose
# and keeps `complexity` from complexity_level. So only the two shapes that fallback cannot
# produce are flagged, and each is reported for what it is.


#: The fields the walk numbers arrive in, and the only ones this check reads.
_WALK_FIELDS = ("likelihood", "complexity", "complexity_unit")

#: Where the numbers actually live, named in both messages so the fix needs no lookup.
_WALK_SURFACE = "get_artifact_meta(<scenario_set_id>) -> detail.scenarios[]; job_status / GET /jobs/{id} does NOT carry it"


def _warn_unpopulated(present: list[dict]) -> None:
    """Flag the two spec shapes the documented fallback cannot produce.

    Takes the raw walk fields captured before `_num` ran, not the coerced rows — see the call site.

    1. NEITHER likelihood nor complexity — the fallback always yields `complexity`, so a row
       with neither carries no walk information at all: an assembly that dropped the fields,
       not a backend that had none.
    2. `complexity_unit == 'bits'` with no likelihood — bits and path_probability come off the
       SAME summary, so reading one and not the other is internally inconsistent.

    Both stay warnings, never failures: a report without likelihoods is still worth rendering."""
    total = len(present)
    blank = sum(1 for raw in present if raw.get("likelihood") is None and raw.get("complexity") is None)
    if blank:
        _warn(
            f"render_suite: {blank} of {total} scenario(s) carry neither 'likelihood' nor 'complexity' — those "
            f"cells render as '—'. Read path_probability / surprisal_bits / complexity_level off {_WALK_SURFACE}."
        )
    inconsistent = sum(1 for raw in present if raw.get("complexity_unit") == "bits" and raw.get("likelihood") is None)
    if inconsistent:
        _warn(
            f"render_suite: {inconsistent} of {total} scenario(s) report complexity in bits but no 'likelihood' — "
            f"both come off the same summary, so one was dropped in assembly. likelihood = path_probability x 100, "
            f"from {_WALK_SURFACE}."
        )


# ── stale-output pruning ──────────────────────────────────────────────────────
# The renderer OWNS exactly these chart names + the per-scenario pages it writes. Re-rendering
# a smaller or changed suite over an older one would leave the superseded pages and charts in
# place, and the report would then link scenarios that are not in the suite (and keep a
# charts/environment.svg from a spec that has no environment section).
CHART_NAMES = ("scenarios_coverage.svg", "scenarios_complexity.svg", "environment.svg")
# Every per-scenario page this script writes starts with this. Checking for it before an unlink
# is what keeps the sweep from touching a human's own note dropped into scenarios/.
_PAGE_SIGNATURE = "# Scenario — "

#: Written beside the render when it ran to the end: the sha256 of the `suite.json` it left behind.
#: `bench_manifest.py phase record --phase render --status landed` reads it, so a render that raised
#: never lands, nor does one over a suite with no scenarios. Removed at the start of every render, so a
#: crash leaves no stamp from an earlier one.
#: The name is shared with that script.
RENDER_STAMP = ".render_ok"


def prune_stale(art: Path, spec: dict, written: list[str]) -> list[str]:
    """Delete renderer-owned outputs this spec no longer produces. Files only, never a
    directory, and never a name outside CHART_NAMES / a page this renderer signed.

    REFUSES on a spec with zero scenarios. suite.json is hand-assembled by the LLM from
    several backend fetches; a partial assembly, or a fetch that returned nothing, yields
    `{}` — and rendering that over a good report would prune every page AND every chart
    and leave a stub scenarios.md, destroying the last good render. An empty spec is a
    FAILED ASSEMBLY, not an instruction to delete. Pruning a suite that genuinely lost its
    last scenario is the cost, and it is the cheap direction: a stale page is visible and
    re-rendered away, a deleted report is gone."""
    pages = scenario_pages(spec)
    if not pages:
        _warn(
            "render_suite: spec yields ZERO scenarios — skipping the stale-output sweep. "
            "An empty spec reads as a failed assembly, not a delete instruction; re-render "
            "with the real suite to prune."
        )
        return []
    removed: list[str] = []
    keep = {f"{name}.md" for _, name in pages}
    scen_dir = art / "scenarios"
    if scen_dir.is_dir():
        for page in sorted(scen_dir.glob("*.md")):
            if page.name in keep or not page.is_file():
                continue
            try:
                if not page.read_text(encoding="utf-8", errors="replace").startswith(_PAGE_SIGNATURE):
                    continue  # not ours — leave it alone
                page.unlink()
            except OSError:
                continue
            removed.append(f"scenarios/{page.name}")
    for name in CHART_NAMES:
        rel = f"charts/{name}"
        chart = art / "charts" / name
        if rel in written or not chart.is_file():
            continue
        try:
            chart.unlink()
        except OSError:
            continue
        removed.append(rel)
    return removed


#: Opens the notes file. Doubles as the ownership check before deleting one — same role as
#: `_PAGE_SIGNATURE` for the per-scenario pages, and for the same reason: an operator may drop
#: their own notes beside the report, and this script must not eat them.
_NOTES_SIGNATURE = "Warnings from the last render."


def _write_notes(art: Path, written: list[str]) -> None:
    """Persist this render's warnings beside the report, or clear a stale file it owns.

    stderr reaches the agent and dies with the turn; the artifact is what survives to a human.
    Deleting on a clean render is deliberate — a previous run's complaint sitting beside new
    output describes the wrong render — but it deletes ONLY a file this script wrote, and never
    on a spec that yielded nothing, because `prune_stale` treats that as a failed assembly rather
    than an instruction to delete and this must agree with it.

    Best-effort: the notes annotate the report, so failing to write them must not turn a rendered
    report into a hard failure after scenarios.md and evals.md are already on disk."""
    notes = art / "render_notes.txt"
    try:
        if _NOTES:
            # dict.fromkeys, not set: page_name is reached through three scenario_pages passes per
            # render, so an unsafe-id warning arrives three times. Order is the emit order.
            body = "\n".join(f"- {n}" for n in dict.fromkeys(_NOTES))
            notes.write_text(
                f"{_NOTES_SIGNATURE} Each names the field and the surface to re-read;\n"
                f"fix suite.json and re-render rather than editing the output.\n\n{body}\n",
                encoding="utf-8",
            )
            written.append("render_notes.txt")
        elif notes.is_file() and notes.read_text(encoding="utf-8").startswith(_NOTES_SIGNATURE):
            notes.unlink()
    except OSError as exc:
        print(f"render_suite: could not update {notes} ({exc}) — the report itself is unaffected.", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    # One render, one set of notes: the list is module-level (the emit sites are scattered),
    # so a second render in the same process would otherwise inherit the first one's complaints.
    _NOTES.clear()
    ap = argparse.ArgumentParser(description="Render the test-suite report (scenarios + evals + environment).")
    ap.add_argument("suite", nargs="?", help="path to suite.json (not needed with --not-qualified)")
    ap.add_argument("--artifacts-dir", required=True, help="output dir (gitignored); reports + charts + per-scenario md go here")
    ap.add_argument("--not-qualified", action="store_true",
                    help="print, as JSON {eval_id: axis}, the evals the qualification runs under --artifacts-dir decided no verdict for, and exit")
    args = ap.parse_args(argv)
    art = Path(args.artifacts_dir)
    if args.not_qualified:
        counts = qualification_counts(art)
        print(json.dumps(not_qualified(counts, sorted(counts or {}))))
        return 0
    if not args.suite:
        ap.error("the suite.json path is required")

    (art / RENDER_STAMP).unlink(missing_ok=True)
    spec = json.loads(Path(args.suite).read_text(encoding="utf-8"))
    _normalize_spec(spec)
    mark_qualification(spec, qualification_counts(art))
    mark_eval_labels(spec, art)
    mark_flagged_evals(spec, art)
    charts = art / "charts"
    scen_dir = art / "scenarios"
    for d in (art, charts, scen_dir):
        d.mkdir(parents=True, exist_ok=True)

    written = []
    cov = (spec.get("coverage") or {}).get("cards")
    if cov:
        (charts / "scenarios_coverage.svg").write_text(render_coverage_svg(cov), encoding="utf-8"); written.append("charts/scenarios_coverage.svg")
    plotted = plottable_scenarios(spec["scenarios"])
    if plotted:
        subtitle = spec.get("complexity_subtitle") or complexity_subtitle(complexity_unit(spec["scenarios"]), plotted)
        (charts / "scenarios_complexity.svg").write_text(render_complexity_svg(plotted, subtitle), encoding="utf-8")
        written.append("charts/scenarios_complexity.svg")
    if (spec.get("environment") or {}).get("schema") or (spec.get("environment") or {}).get("bounds") or (spec.get("environment") or {}).get("noise"):
        (charts / "environment.svg").write_text(render_environment_svg(spec["environment"]), encoding="utf-8"); written.append("charts/environment.svg")

    (art / "scenarios.md").write_text(render_scenarios_md(spec), encoding="utf-8"); written.append("scenarios.md")
    (art / "evals.md").write_text(render_evals_md(spec), encoding="utf-8"); written.append("evals.md")
    for s, page in scenario_pages(spec):
        (scen_dir / f"{page}.md").write_text(render_scenario_detail_md(s), encoding="utf-8"); written.append(f"scenarios/{page}.md")
    suite_text = json.dumps(spec, indent=2, ensure_ascii=False) + "\n"
    suite_bytes = suite_text.encode("utf-8")
    (art / "suite.json").write_bytes(suite_bytes)

    # The notes land BESIDE the render, and are DELETED when there are none — a stale
    # render_notes.txt from an earlier run would read as a live complaint about this one, which
    # is the same failure (a signal detached from its subject) in the other direction.
    # AFTER prune_stale: its zero-scenario refusal is the loudest warning this script can emit
    # (a `{}` or partial suite.json — a failed assembly), and writing the notes first would leave
    # that one message stderr-only while its own run deleted the previous notes. The single most
    # valuable warning would be the one the artifact channel could not carry.
    removed = prune_stale(art, spec, written)

    _write_notes(art, written)

    for w in written:
        print(f"wrote {art / w}")
    for r in removed:
        print(f"pruned stale {art / r}")
    if spec["scenarios"]:  # a spec with none is a failed assembly, which no landing may stand on
        (art / RENDER_STAMP).write_text(hashlib.sha256(suite_bytes).hexdigest() + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.path.append(str(Path(__file__).resolve().parents[3] / "scripts"))
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
