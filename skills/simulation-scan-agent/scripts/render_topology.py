#!/usr/bin/env python3
"""Render an agent-topology spec into `agent_topology.svg` + `agent_topology.md`.

This is the deterministic renderer behind `/simulation-scan-agent`'s final step.
The skill (the local Claude) is responsible for the *judgement* — reading the
agent's code + plurai.config.yaml mock contract and deciding which surfaces are
LIVE vs MOCKED and why. This script does only the *layout*, so the diagram is
consistent and never hand-placed.

Input: a topology JSON (see example_topology.json). Output: the two artifacts,
written side-by-side into --out-dir.

    python render_topology.py topology.json --out-dir plurai_sim/

Schema (all `status` values are "live" or "mocked"):

    {
      "agent":     {"name", "framework", "state"?, "entry"?, "action_channel"?},
      "rag":       [{"name", "status", "why", "subtitle"?, "description"?}, ...],   # optional
      "knowledge": [{"name", "status", "why", "subtitle"?}, ...],   # optional
      "tools":     [{"name", "status", "why", "writes"?, "effects"?, "runs"?, "backend"?, "consent"?, "description"?}, ...],  # optional
      "subagents": [{"name", "status", "why", "call", "tools"?, "inner_calls"?}, ...],  # optional; see below
      "skills":    [{"name", "status", "why"}, ...],                # optional
      "store":     {"label", "edge"?, "owner"?},                    # optional; owner: mock | agent | external
      "probes":    [{"name", "why_eligible"?}, ...],                # optional
      "grounding": {"needs_corpus", "surfaces"?, "sources"?, "records_of"?, "why"?, "off_map"?, "inline"?} # optional
    }

Every live tool carries three typed facts: `writes` (`live` | `world` | `none`), `effects` (a list
from `shell` | `files` | `network` | `schedule`, or `["none"]` alone) and `runs` (`in_process` |
`provider_hosted` | `subprocess`). The renderer REFUSES a live tool without them. `consent:
"required"` is COMPUTED from them (`scripts/host_effects.consent_required`, never left to the
author), written into the canonical JSON, rendered on the map, and asked about at the consent step;
what an ungranted one does is rendered from the runtime's confiner, never promised.

Every live tool (and live `rag` entry) also carries `backend`: the service its code signs in to, the local stack's
included, or "none" when it opens no client. Once any live tool carries it, the renderer REFUSES a live tool without
it (a map from before the fact renders with a warning); the tool's `kind: live` check (`bench_manifest.py`) proves the
sign-in works.

A tool's `description` is the first sentence of its `descriptor.description` in `plurai.config.yaml`, copied by
`scripts/record_tool_descriptions.py` after the config exists, for the report page. The renderer draws nothing from it
and writes it back unchanged into the canonical JSON.

A sub-agent's `call` is the name its delegation carries on the tool trail, or null when it is reached
without a call. With a `call`, `tools` lists the tools it calls itself, and `inner_calls` (`linked` | `recorded` |
`unrecorded`) is what one delegating smoke turn showed about those calls reaching the trail (`linked`: each carries
`parent_call_id`).

`store.owner` is the `world_state.owner` the scan declares for that store. With `agent` and nothing mocked, the
headline names the store the harness seeds and resets per conversation instead of saying nothing is mocked: every
surface runs for real, over a store the run controls.

`probes` are the surfaces scan judged safe to CALL read-only. The list is scan's judgement,
recorded at Step 2a so the consent question at Step 2c fires on a RECORDED LIST rather than on the
same judgement made again from memory, which would fire that question on some runs and
not others. Rendered so a wrong call is correctable at the approval gate.

`grounding` is scan's RECORDED verdict on whether this agent needs a knowledge corpus.
`/simulation-generate-suite`'s grounding gate reads it rather than re-deriving the judgement from
the config a second time, differently — and the map renders it so the user can correct it at the
approval gate. Omitted in files written before the block existed; it then renders nothing rather
than a verdict nobody made.

`grounding.sources` are the DIRECTORIES those surfaces read from, relative to the AGENT MODULE
directory (the same anchor Step 3c's `$PWD` resolves to) — typed paths,
not the prose labels `surfaces` carries. They are what the pre-generation contract
(`plurai-scan-contract`) hashes and verifies the scan's typed facts against, so a corpus-needing
agent with none recorded renders as an explicit gap: the contract has nothing to check, and
that must be visible on the map rather than looking like an all-clear.

`grounding.off_map` names the agent's search/retrieval tools this map leaves off (production tools, never on the map).
It renders one line saying no conversation exercises them, with the reason read from the scope proposal, an answered
override or a default-off switch — and then `why` is not printed, so a map that cuts a search tool never says it has none.

`grounding.inline` lists knowledge written in the agent's own code: `[{"name", "tool", "source"}]`, `source` being
`<code file>::<variable>` (the literal's assigned name, e.g. `agent.py::lookup_knowledge_base.kb`) and `tool` the
read-only tool returning its values, or null. `scripts/extract_inline_knowledge.py` copies each into the corpus; the
map renders one line per entry. A malformed entry raises, as a malformed `sources` does.

`rag` vs `knowledge`: both are grounding surfaces and share the diagram's left band, but
they are different kinds and the report says which. `rag` is knowledge reached through a
retrieval tool at run time; `knowledge` is knowledge the agent already HAS — prompt-inlined
into its system prompt, with nothing retrieving. Filing that case under `rag`
with a hand-written subtitle would render it under a RAG heading and report
`kind_of == "rag"` — wrong on both counts. Specs that carry that workaround keep working
unchanged; new specs should use `knowledge`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from host_effects import EFFECTS, RUNS, WRITES, action_channel_of, consent_required, outcome_for, runtime_of, ungranted_line  # noqa: E402 - the plugin's one host-effects table
import agent_inventory  # noqa: E402 - the ONE "N of M production tools" reading, the validator's too

#: `grounding.inline[].source`'s variable: `func.var`, `Class.method.var` or a module-level `VAR`.
_INLINE_SYMBOL = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")

# ── palette (GitHub dark) ─────────────────────────────────────────────────────
BG = "#0d1117"
PANEL = "#161b22"
PANEL_STROKE = "#30363d"
MUTED = "#6e7681"
LABEL = "#9aa5b1"
TEXT = "#e6edf3"
EDGE = "#3d444d"
AGENT_STROKE = "#4493f8"
LIVE = {"fill": "#13251b", "stroke": "#3fb950"}
MOCKED = {"fill": "#2a2014", "stroke": "#f0883e"}
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"

WIDTH = 980
MARGIN = 16
BUS_Y = 150        # horizontal connector bus between agent and the section panels
SECTION_TOP = 182  # top of the TOOLS / SUB-AGENTS / SKILL panels


def _text_w(s: str, size: float, *, mono: bool = False) -> float:
    """Rough rendered width of a string (px). Monospace is wider per glyph."""
    return len(s) * size * (0.62 if mono else 0.52)


def _fit_font(s: str, size: float, max_w: float, *, mono: bool = False, floor: float = 8.5) -> float:
    """Shrink font until the string fits max_w (down to a floor)."""
    while size > floor and _text_w(s, size, mono=mono) > max_w:
        size -= 0.5
    return size


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _node_colors(status: str) -> dict:
    return MOCKED if status == "mocked" else LIVE


def _rect(x, y, w, h, status, *, rx=8, sw=1.6):
    c = _node_colors(status)
    dash = ' stroke-dasharray="6 3"' if status == "mocked" else ""
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
        f'fill="{c["fill"]}" stroke="{c["stroke"]}" stroke-width="{sw}"{dash}/>'
    )


def _node_text(cx, cy, status, name, *, size=11.5):
    c = _node_colors(status)
    return (
        f'<text x="{cx}" y="{cy}" text-anchor="middle" font-size="{size}" '
        f'font-weight="600" fill="{c["stroke"]}" font-family="{MONO}">{escape(name)}</text>'
    )


def _section(parts, *, x, y, w, title, accent, items, cols, row_h, node_h, pad=18):
    """Emit a panel with a titled grid of nodes; return its bottom y + centers."""
    n = len(items)
    rows = (n + cols - 1) // cols if n else 0
    inner_w = (w - pad * 2 - (cols - 1) * 10) // cols
    body_top = y + 40
    h = (body_top - y) + rows * row_h + (10 if rows else 0)
    parts.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" '
        f'fill="{PANEL}" stroke="{PANEL_STROKE}" stroke-width="1.2"/>'
    )
    parts.append(f'<rect x="{x + 14}" y="{y + 13}" width="9" height="9" rx="2.5" fill="{accent}"/>')
    parts.append(
        f'<text x="{x + 30}" y="{y + 22}" font-size="12" font-weight="800" '
        f'fill="{accent}" letter-spacing="0.06em">{escape(title)} ({n})</text>'
    )
    for i, it in enumerate(items):
        r, col = divmod(i, cols)
        nx = x + pad + col * (inner_w + 10)
        ny = body_top + r * row_h
        parts.append(_rect(nx, ny, inner_w, node_h, it["status"]))
        parts.append(_node_text(nx + inner_w / 2, ny + node_h / 2 + 5, it["status"], it["name"]))
    return y + h, x + w / 2


#: The agent's own tool inventory (`scripts/agent_inventory.py`), beside the map in the artifacts dir.
INVENTORY_NAME = "agent_inventory.json"
#: The bench manifest, in the working dir: its cited `not_loaded` groups and the operator's `capability_overrides`.
MANIFEST_NAME = "bench_manifest.json"


def load_production(artifacts: Path, workdir: Path) -> dict | None:
    """The EFFECTIVE production set the map is held to — the inventory's `production_tools`, with the manifest's cited
    `not_loaded` groups and its operator-answered `harness.capability_overrides` (under that key) — so the map's
    "N of M" is the validator's. None when no inventory was read."""
    try:
        production = json.loads((artifacts / INVENTORY_NAME).read_text(encoding="utf-8")).get("production_tools")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(production, dict):
        return None
    try:
        manifest = json.loads((workdir / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    manifest = manifest if isinstance(manifest, dict) else {}
    stored = (manifest.get("surfaces") or {}).get("production_tools") or {}
    return {**production, "not_loaded": stored.get("not_loaded") or [], "capability_overrides": answered_overrides(manifest, workdir)}


def answered_overrides(manifest: dict, workdir: Path) -> list:
    """The validator's own reading: only an override the scope record backs (its switch, or a setting's tools, named by the
    scope gate's record) counts. No override counts when that reading cannot be made — a count is never more generous
    than the validator's."""
    try:
        import bench_manifest  # noqa: PLC0415 - heavy; only the coverage line needs it

        return bench_manifest.answered_overrides(
            {"harness": manifest.get("harness") or {}, "surfaces": manifest.get("surfaces") or {}}, workdir)
    except Exception:  # noqa: BLE001
        return []


def production_coverage(topo: dict, production: dict | None) -> dict | None:
    """`agent_inventory.production_coverage` of the map — ``{total, carried, dropped, exempt}``, the validator's N of M,
    read from the ONE inventory artifact — or None when no inventory was read or it is incomplete (a registrar named at
    run time with no live enumeration): a count the guard refuses is never printed."""
    if not (production or {}).get("names") or ((production or {}).get("dynamic") and "live" not in production):
        return None
    return agent_inventory.production_coverage(production, agent_inventory.map_tool_names(topo), production.get("capability_overrides"))


def off_by_default(production: dict, names: list[str]) -> list[str]:
    """The switches that keep ``names`` off at the production defaults: each `not_loaded` group's cite, each override's switch."""
    found = [str(g.get("switch") or g.get("selector") or g.get("surface")) for g in production.get("not_loaded") or []
             if isinstance(g, dict) and set(g.get("names") or []) & set(names)]
    gates = {s.get("switch"): set(s.get("gates") or []) for s in production.get("switches") or [] if isinstance(s, dict)}
    found += [str(o.get("switch")) for o in production.get("capability_overrides") or []
              if isinstance(o, dict) and (gates.get(o.get("switch"), set()) | set(o.get("tools") or [])) & set(names)]
    return sorted(set(found) - {"None"})


def render_svg(topo: dict, production: dict | None = None) -> str:
    agent = topo["agent"]
    # The left band draws GROUNDING surfaces — retrieved (`rag`) and prompt-inlined
    # (`knowledge`) alike, in that order. They differ in kind, not in position: both feed the
    # agent's answers, and the "grounds" edge is true of either. render_md is where the kinds
    # part ways.
    rag = (topo.get("rag") or []) + (topo.get("knowledge") or [])
    tools = topo.get("tools") or []
    subagents = topo.get("subagents") or []
    skills = topo.get("skills") or []
    store = topo.get("store")

    p: list[str] = []
    p.append(
        f'<rect width="{WIDTH}" height="HEIGHT" fill="{BG}"/>'
    )
    # arrow marker
    p.append(
        '<defs><marker id="a" markerWidth="9" markerHeight="9" refX="7" refY="3" '
        f'orient="auto"><path d="M0,0 L7,3 L0,6 Z" fill="{LABEL}"/></marker></defs>'
    )

    # ── legend (top-right) ────────────────────────────────────────────────
    p.append(f'<rect x="760" y="14" width="206" height="64" rx="10" fill="{PANEL}" stroke="{PANEL_STROKE}"/>')
    p.append(f'<rect x="776" y="29" width="14" height="14" rx="3" fill="{LIVE["fill"]}" stroke="{LIVE["stroke"]}" stroke-width="2"/>')
    p.append(f'<text x="800" y="40" font-size="11.5" fill="{TEXT}"><tspan fill="{LIVE["stroke"]}" font-weight="700">live</tspan> — run for real</text>')
    p.append(f'<rect x="776" y="51" width="14" height="14" rx="3" fill="{MOCKED["fill"]}" stroke="{MOCKED["stroke"]}" stroke-width="2" stroke-dasharray="4 2"/>')
    p.append(f'<text x="800" y="62" font-size="11.5" fill="{TEXT}"><tspan fill="{MOCKED["stroke"]}" font-weight="700">mocked</tspan></text>')

    # ── RAG node + agent (top band) ───────────────────────────────────────
    agent_cx = 487
    if rag:
        r0 = rag[0]
        p.append(_rect(40, 56, 250, 46, r0["status"], rx=8))
        p.append(_node_text(165, 83, r0["status"], r0["name"]))
        if r0.get("subtitle"):
            p.append(f'<text x="165" y="118" text-anchor="middle" font-size="10.5" fill="{MUTED}">{escape(r0["subtitle"])}</text>')
        # The band fits ONE node, but render_md lists every RAG surface. Drawing only the
        # first silently contradicts the report for a multi-KB agent, so name the remainder
        # instead of dropping it.
        if len(rag) > 1:
            _more = f"+{len(rag) - 1} more knowledge base{'s' if len(rag) > 2 else ''}"
            p.append(f'<text x="165" y="{143 if r0.get("subtitle") else 118}" text-anchor="middle" font-size="9.5" fill="{MUTED}">{escape(_more)}</text>')
        p.append(f'<path d="M290,79 L372,79" stroke="{EDGE}" stroke-width="1.6" fill="none" marker-end="url(#a)"/>')
        p.append(f'<rect x="300" y="69" width="63" height="19" rx="5" fill="{PANEL}" stroke="{PANEL_STROKE}"/>')
        p.append(f'<text x="331" y="82" text-anchor="middle" font-size="11.5" font-weight="600" fill="{LABEL}">grounds</text>')

    p.append(f'<rect x="372" y="50" width="230" height="58" rx="8" fill="{PANEL}" stroke="{AGENT_STROKE}" stroke-width="2.2"/>')
    p.append(f'<text x="{agent_cx}" y="83" text-anchor="middle" font-size="14" font-weight="700" fill="{TEXT}" font-family="{MONO}">{escape(agent["name"])}</text>')

    # ── section panels (set the centers the connectors target) ────────────
    bottoms = []
    tools_center = subagents_center = skills_center = None
    if tools:
        coverage = production_coverage(topo, production)
        title = f"TOOLS · {coverage['total'] - len(coverage['dropped']) - len(coverage['exempt'])} of {coverage['total']} production" if coverage else "TOOLS"
        b, tools_center = _section(p, x=40, y=SECTION_TOP, w=340, title=title, accent=AGENT_STROKE,
                                   items=tools, cols=2, row_h=44, node_h=32)
        bottoms.append(b)
    if subagents:
        b, subagents_center = _section(p, x=420, y=SECTION_TOP, w=300, title="SUB-AGENTS", accent="#bc8cff",
                                       items=subagents, cols=1, row_h=44, node_h=34)
        bottoms.append(b)
    if skills:
        b, skills_center = _section(p, x=760, y=SECTION_TOP, w=200, title="SKILL", accent="#39c5cf",
                                    items=skills, cols=1, row_h=44, node_h=34)
        bottoms.append(b)

    # ── connectors agent → sections (bus at BUS_Y; labels sit on the drops)─
    centers = [(tools_center, "calls tools"), (subagents_center, "delegates"), (skills_center, "invokes")]
    centers = [(cx, lbl) for cx, lbl in centers if cx is not None]
    if centers:
        # include agent_cx so the agent's drop-line always meets the horizontal bus.
        xs = [cx for cx, _ in centers] + [agent_cx]
        bus = f'M{agent_cx},108 L{agent_cx},{BUS_Y} M{min(xs)},{BUS_Y} L{max(xs)},{BUS_Y}'
        for cx, _ in centers:
            bus += f' M{cx},{BUS_Y} L{cx},{SECTION_TOP}'
        p.append(f'<path d="{bus}" stroke="{EDGE}" stroke-width="1.6" fill="none"/>')
        lbl_y = (BUS_Y + SECTION_TOP) / 2
        for cx, lbl in centers:
            lw = _text_w(lbl, 11) + 12
            p.append(f'<rect x="{cx - lw / 2:.0f}" y="{lbl_y - 10:.0f}" width="{lw:.0f}" height="18" rx="5" fill="{BG}"/>')
            p.append(f'<text x="{cx}" y="{lbl_y + 3:.0f}" text-anchor="middle" font-size="11" font-weight="600" fill="{LABEL}">{escape(lbl)}</text>')

    # ── agent subtitle: drawn last, over a bg pill that masks the bus line ─
    sub = agent.get("framework", "") or ""
    if agent.get("state"):
        sub = f'{sub} · state {agent["state"]}' if sub else f'state {agent["state"]}'
    if sub:
        fs = _fit_font(sub, 10.5, 560)
        pw = _text_w(sub, fs) + 16
        p.append(f'<rect x="{agent_cx - pw / 2:.0f}" y="119" width="{pw:.0f}" height="18" rx="5" fill="{BG}"/>')
        p.append(f'<text x="{agent_cx}" y="132" text-anchor="middle" font-size="{fs}" fill="{MUTED}">{escape(sub)}</text>')

    # ── store node (under tools): size-to-fit + clamp inside the canvas ────
    # 137 = the agent subtitle pill's bottom edge. With no tools/subagents/skills there is
    # no band for the store edge to leave FROM, and SECTION_TOP left it floating 45px
    # below the agent attached to nothing.
    max_bottom = max(bottoms) if bottoms else 137
    # Centre on the tools band when there is one, else on the agent: a store declared by a
    # TOOL-LESS agent is still real, and gating the draw on tools_center made it disappear.
    store_center = tools_center if tools_center is not None else agent_cx
    if store:
        label = store["label"]
        fs = _fit_font(label, 11.5, WIDTH - 2 * MARGIN - 36, mono=True)
        store_w = max(200.0, _text_w(label, fs, mono=True) + 36)
        store_x = _clamp(store_center - store_w / 2, MARGIN, WIDTH - MARGIN - store_w)
        box_cx = store_x + store_w / 2
        store_y = max_bottom + 48
        midy = (max_bottom + store_y) / 2
        p.append(
            f'<path d="M{store_center},{max_bottom} L{store_center},{midy:.0f} '
            f'L{box_cx:.0f},{midy:.0f} L{box_cx:.0f},{store_y}" '
            f'stroke="{EDGE}" stroke-width="1.6" fill="none" marker-end="url(#a)"/>'
        )
        edge_lbl = store.get("edge", "writes")
        lw = _text_w(edge_lbl, 11.5) + 16
        p.append(f'<rect x="{store_center - lw / 2:.0f}" y="{max_bottom + 7}" width="{lw:.0f}" height="19" rx="5" fill="{PANEL}" stroke="{PANEL_STROKE}"/>')
        p.append(f'<text x="{store_center}" y="{max_bottom + 20}" text-anchor="middle" font-size="11.5" font-weight="600" fill="{LABEL}">{escape(edge_lbl)}</text>')
        p.append(f'<rect x="{store_x:.0f}" y="{store_y}" width="{store_w:.0f}" height="46" rx="8" fill="#1b2230" stroke="{LABEL}" stroke-width="1.6"/>')
        p.append(f'<text x="{box_cx:.0f}" y="{store_y + 27}" text-anchor="middle" font-size="{fs}" font-weight="600" fill="{TEXT}" font-family="{MONO}">{escape(label)}</text>')
        max_bottom = store_y + 46

    height = int(max_bottom + 30)
    body = "".join(p).replace("HEIGHT", str(height))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" '
        f'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif">'
        f'{body}</svg>'
    )


def _emoji(status: str) -> str:
    return "🟡" if status == "mocked" else "🟢"


def mark_host_effects(topo: dict) -> dict:
    """A copy of `topo` whose live tools that run for real only on a grant carry `consent: required`.

    Refuses a `writes` outside its vocabulary on any tool, and a live tool the map does not classify."""
    tools = [dict(t) for t in topo.get("tools") or []]
    bad = [t.get("name") for t in tools if t.get("writes") not in (None, *WRITES)]
    if bad:
        raise ValueError(f"agent_topology.json: `writes` is one of {', '.join(WRITES)}; offending tools: {bad!r}")
    try:
        required = set(consent_required({**topo, "tools": tools}, runtime=runtime_of(topo, None)))
    except ValueError as error:
        raise ValueError(f"{error} (`effects` is a list from {', '.join(EFFECTS)}, `none` alone; `runs` is one of {', '.join(RUNS)})") from error
    rag = [dict(r) for r in topo.get("rag") or []]
    for t in [*tools, *rag]:  # a typed `rag` entry is a tool too
        if t.get("name") in required and t.get("status") != "mocked":
            t["consent"] = "required"
    return {**topo, **({"tools": tools} if "tools" in topo else {}), **({"rag": rag} if "rag" in topo else {})}


def render_md(topo: dict, svg_name: str, production: dict | None = None, scope_tools: frozenset = frozenset()) -> str:
    topo = mark_host_effects(topo)
    agent = topo["agent"]
    rag = topo.get("rag") or []
    knowledge = topo.get("knowledge") or []
    tools = topo.get("tools") or []
    subagents = topo.get("subagents") or []
    skills = topo.get("skills") or []
    surfaces = rag + knowledge + tools + subagents + skills

    total = len(surfaces)
    mocked = [s for s in surfaces if s["status"] == "mocked"]
    live = [s for s in surfaces if s["status"] != "mocked"]

    kind_of = {}
    for s in rag:
        kind_of[id(s)] = "rag"
    # Prompt-inlined knowledge is its own kind. Filed under `rag` (the pre-`knowledge`
    # workaround) it reported as retrieval the agent never performs; the reader then looks
    # for a retrieval tool that does not exist.
    for s in knowledge:
        kind_of[id(s)] = "knowledge"
    for s in tools:
        kind_of[id(s)] = "tool"
    for s in subagents:
        kind_of[id(s)] = "sub-agent"
    for s in skills:
        kind_of[id(s)] = "skill"

    L = []
    sub = agent.get("framework", "")
    bits = []
    if sub:
        bits.append(f"`{sub}`")
    if agent.get("state"):
        bits.append(f"state `{agent['state']}`")
    if agent.get("entry"):
        bits.append(f"entry `{agent['entry']}`")
    bits.append("legend: 🟢 live · 🟡 mocked")
    L.append(f"# Agent topology — {agent['name']}")
    L.append("")
    L.append("  ·  ".join(bits))
    L.append("")
    writers = [s["name"] for s in live if s.get("writes") == "live"]
    host = [s["name"] for s in live if s.get("consent") == "required" and s["name"] not in writers]
    if mocked:
        L.append(f"**🟢 {len(live)} run for real · 🟡 {len(mocked)} mocked** (of {total} surfaces). "
                 "Each surface, and exactly why, is below.")
    elif host:
        L.append(f"**🟢 all {total} surfaces run for real** — {len(host)} of them can act on this machine.")
    elif isinstance(topo.get("store"), dict) and topo["store"].get("owner") == "agent" and str(topo["store"].get("label") or "").strip():
        L.append(f"**🟢 all {total} surfaces run for real** — over {topo['store']['label'].strip()}, "
                 "which the harness seeds and resets per conversation.")
    else:
        L.append(f"**🟢 all {total} surfaces run for real** — nothing is mocked.")
    L.append("")
    # What an ungranted one does is the runtime's confiner, per tool: never a sandbox that does not exist.
    runtime = runtime_of(topo, None)
    by_outcome: dict[str, list[str]] = {}
    for s in live:
        if s["name"] in host:
            by_outcome.setdefault(outcome_for(s, runtime=runtime), []).append(s["name"])
    for outcome, names in by_outcome.items():
        L.append(f"**⚠ {' · '.join(f'`{n}`' for n in names)} act on this machine** — "
                 + ungranted_line(outcome, action_channel=action_channel_of(topo)))
        L.append("")
    if writers:
        L.append(f"**⚠ {' · '.join(f'`{n}`' for n in writers)} write your live system** — unless you grant them, "
                 "a scored run cannot call them.")
        L.append("")
    if coverage := production_coverage(topo, production):
        m, off = coverage["total"], coverage["exempt"]
        switches = off_by_default(production, off)
        line = f"**{m - len(coverage['dropped']) - len(off)} of {m} production tools are on this map** at the agent's production defaults"
        if off:
            line += f"; {len(off)} are off by default ({', '.join(f'`{s}`' for s in switches) or 'your answer'})"
        L.append(line + ".")
        L.append("")
    L.append("## Map")
    L.append("")
    L.append(f"![Agent topology]({svg_name})")
    L.append("")
    # Each heading renders only over a non-empty list: an all-mocked (or all-live) agent
    # otherwise shows an empty section header, which reads as a rendering bug rather than
    # a legitimate 0-count split. Same guard one level up: a pure prompt-only agent
    # (no tools, no rag) has NO surfaces at all, so the parent "## Why each surface"
    # heading would render bare — omit the whole section then.
    if live or mocked:
        L.append("## Why each surface")
        L.append("")
    if live:
        L.append("**🟢 Live**")
        for s in live:
            consent = " · **needs consent**" if s.get("consent") == "required" else ""
            L.append(f"- 🟢 `{s['name']}` ({kind_of[id(s)]}){consent} — {s['why']}")
        L.append("")
    if mocked:
        L.append("**🟡 Mocked**")
        for s in mocked:
            L.append(f"- 🟡 `{s['name']}` ({kind_of[id(s)]}) — {s['why']}")
        L.append("")
    L += _probe_lines(topo.get("probes"))
    L += _grounding_lines(topo.get("grounding"), _off_map_line(topo, production, scope_tools))
    return "\n".join(L)


def _probe_lines(probes: object) -> list[str]:
    """The surfaces scan judged safe to CALL read-only, listed so the user sees them before saying
    yes.

    Eligibility is scan's judgement — it reads the code and weighs what is genuinely safe to
    invoke, which no predicate over `plurai.config.yaml` could do. Rendering it is what makes the
    judgement reviewable: the consent question names these surfaces, and a wrong call here is
    otherwise invisible until something runs.

    Absent or empty renders NOTHING. Empty is a real answer (no eligible surface ⇒ no question),
    and a section saying so would be noise on every transactional agent.
    """
    if probes is None:
        return []
    if not isinstance(probes, list):
        raise ValueError(f"agent_topology.json: `probes` must be a list, got {probes!r}.")
    # A row whose `name` is missing or mistyped rendered NO section at all, while Step 2c — which
    # reads the raw JSON, not this output — still saw a non-empty list and asked consent for a
    # surface the approved map never showed. Divergence between what was shown and what was
    # consented to is the one thing this block exists to prevent.
    bad = [p for p in probes if not isinstance(p, dict) or not str(p.get("name") or "").strip()]
    if bad:
        raise ValueError(
            f"agent_topology.json: every `probes` entry needs a non-empty `name` — the consent "
            f"question quotes it and the map shows it. Offending entries: {bad!r}"
        )
    rows = list(probes)
    if not rows:
        return []
    L = ["## Read-only probes", "",
         "Scan proposes calling these locally — your venv, your own data — to verify the tool specs "
         "it writes. No writes, no external services, no LLM calls. You are asked before any of it runs.",
         ""]
    for r in rows:
        why = str(r.get("why_eligible") or "").strip()
        L.append(f"- `{r['name']}`" + (f" — {why}" if why else ""))
    L.append("")
    return L


#: The scope gate's proposal (`scripts/scope_proposal.py`), beside the map in the artifacts dir: the tools it leaves out.
SCOPE_PROPOSAL_NAME = "scope_proposal.json"


def load_scope_tools(artifacts: Path) -> frozenset:
    """The tools the scope proposal leaves out of the map; empty when none was written yet."""
    try:
        tools = json.loads((artifacts / SCOPE_PROPOSAL_NAME).read_text(encoding="utf-8")).get("tools")
    except (OSError, ValueError, AttributeError):
        return frozenset()
    return frozenset(str(t) for t in tools) if isinstance(tools, list) else frozenset()


def _off_map_line(topo: dict, production: dict | None, scope_tools: frozenset) -> str | None:
    """The sentence for `grounding.off_map` — the agent's search/retrieval tools the map leaves off — or None.

    The scan names the tools (its one judgement); the reason is read from records that already exist: the scope
    proposal or an answered override ("left out at approval"), else a switch off by default or a cited `not_loaded`
    group ("off in production by default"). A name on the map, or no production tool, is warned on stderr and still
    rendered."""
    grounding = topo.get("grounding")
    raw = grounding.get("off_map") if isinstance(grounding, dict) else None
    names = list(dict.fromkeys(str(n).strip() for n in raw if str(n).strip())) if isinstance(raw, list) else []
    if not names:
        return None
    production = production or {}
    on_map = agent_inventory.map_tool_names(topo) | {str(r.get("name")) for r in topo.get("rag") or [] if isinstance(r, dict)}
    if listed := [n for n in names if n in on_map]:
        print(f"warning: grounding.off_map names {', '.join(listed)}, which the map carries", file=sys.stderr)
    known = set(production.get("names") or [])
    if known and (unknown := [n for n in names if n not in known]):
        print(f"warning: grounding.off_map names {', '.join(unknown)}, which is no production tool", file=sys.stderr)
    approved = set(scope_tools) | agent_inventory.overridden_tools(production, production.get("capability_overrides"))
    default_off = agent_inventory.not_loaded_tools(production) | {
        str(t) for sw in production.get("switches") or [] if isinstance(sw, dict) and sw.get("default_on") is False
        for t in sw.get("gates") or []}
    groups: dict[str, list[str]] = {}
    for n in names:
        reason = "left out at approval" if n in approved else "off in production by default" if n in default_off else ""
        groups.setdefault(reason, []).append(f"`{n}`")
    parts = [", ".join(tools) + (f" ({reason})" if reason else "") for reason, tools in groups.items()]
    return f"the agent's search tools {' and '.join(parts)} are not on this map, so no conversation exercises them."


def _grounding_lines(grounding: object, off_map: str | None = None) -> list[str]:
    """The knowledge-corpus requirement scan RECORDED, rendered so the user sees it at the gate.

    Scan decides whether this agent has a knowledge surface; `/simulation-generate-suite` acts on
    that decision at its grounding gate. Putting it on the map is what makes the decision
    reviewable at the one moment the user is already looking — a corpus's scope decides what the
    whole suite can test, so it must not be built during the scan with nothing shown and nothing
    asked.

    Absent renders NOTHING: an older `agent_topology.json` predates the block, and inventing a
    verdict for it would put a claim on the map that nobody made.
    """
    if grounding is None:
        return []
    # PRESENT but malformed is a different case from absent, and it must never render.
    # `{}` (the key mistyped as needs_kb / requires_corpus) would print a confident
    # "**No knowledge corpus needed**" — a verdict nobody made, in the exact voice this function's
    # docstring promises to avoid — and `{"needs_corpus": "false"}` would print NEEDED, because a
    # non-empty string is truthy. The gate LLM reading the same JSON would most likely read
    # "false" as false, so the map and the flow would disagree about what the user approved.
    # Raising is the right answer rather than silence: Step 2b runs this BEFORE the approval gate,
    # so a bad block is fixed while the authoring session still has the code in front of it.
    if not isinstance(grounding, dict) or not isinstance(grounding.get("needs_corpus"), bool):
        raise ValueError(
            f"agent_topology.json: `grounding` is present but malformed ({grounding!r}). It needs a "
            f"JSON boolean `needs_corpus` (true/false, not a string). Omit the block entirely if "
            f"you have not made the judgement yet — a half-written block would put a verdict on the "
            f"map that nobody made, and the suite's grounding gate reads the same field."
        )
    why = str(grounding.get("why") or "").strip()
    surfaces = [str(s) for s in (grounding.get("surfaces") or []) if str(s).strip()]
    raw_sources = grounding.get("sources")
    # A bare STRING is the shape an LLM produces for a one-element list, and it is iterable — so
    # `sources: "assets/docs"` rendered one "source" per CHARACTER and read on the map as eleven
    # checked directories. Refused for the same reason `needs_corpus` is: Step 2b runs before the
    # approval gate, so a bad block fails while the code is still in front of the author.
    if raw_sources is not None and not isinstance(raw_sources, list):
        raise ValueError(
            f"agent_topology.json: `grounding.sources` must be a JSON list of directory paths, got "
            f"{type(raw_sources).__name__} ({raw_sources!r}). A bare string is iterable, so it would render "
            f"one corpus source per character."
        )
    sources = [str(s) for s in (raw_sources or []) if str(s).strip()]
    # Distinct basenames, because the contract addresses a corpus unit as `<root-dir>/<path>`:
    # two roots sharing a name collide and one silently shadows the other.
    if len(({Path(s).name for s in sources})) != len(sources):
        raise ValueError(
            f"agent_topology.json: `grounding.sources` {sources} do not have distinct directory names. "
            f"The pre-generation contract addresses corpus units as `<dir-name>/<path>`, so two roots "
            f"sharing a name would collide and one would silently shadow the other."
        )
    if grounding.get("inline") and not grounding.get("needs_corpus"):
        raise ValueError("agent_topology.json: `grounding.inline` declares knowledge in the code, so `needs_corpus` must be true.")
    L = ["## Grounding", ""]
    if grounding.get("needs_corpus"):
        named = " (" + ", ".join(f"`{s}`" for s in surfaces) + ")" if surfaces else ""
        L.append(f"**Knowledge corpus NEEDED**{named}" + (f" — {why}" if why else ""))
        L.append("")
        inline = _inline_entries(grounding.get("inline"))
        if sources:
            named_sources = ", ".join(f"`{s}`" for s in sources)
            L.append(f"Corpus sources: {named_sources} — the pre-generation contract hashes these "
                     "and verifies the scan's typed facts against them.")
        elif inline:
            # Before the copy runs (it runs after this approval), the copy IS the recorded source.
            L.append("Corpus sources: the articles copied from your code — recorded before generation, then hashed and "
                     "checked like any other source.")
        else:
            # Absent is a GAP and renders as one. Rendering nothing would leave a corpus-needing
            # agent looking identically to one whose sources were recorded and checked out.
            L.append("Corpus sources: **not recorded** — the pre-generation contract has nothing to "
                     "check, so no typed fact, URL or source hash was verified before generation.")
        for entry in inline:
            behind = f", behind `{entry['tool']}`" if entry.get("tool") else ""
            if Path(entry["source"].partition("::")[0].replace("\\", "/")).suffix.lower() != ".py":
                L += ["", f"Knowledge written in your code (`{entry['source']}`{behind}) is not copied: only Python code is read today, "
                      "so answers from it are not checked against it."]
                continue
            L += ["", f"Knowledge written in your code (`{entry['source']}`{behind}): the articles in your code are copied word for "
                  f"word into a test knowledge base, **{entry['name']}**."]
        records_of = grounding.get("records_of")
        if isinstance(records_of, dict) and records_of:
            L.append("")
            L.append("What each search returns: " + " · ".join(
                f"`{tool}` reference content" if kind is None else f"`{tool}` records of `{kind}`"
                for tool, kind in sorted(records_of.items())))
        L.append("")
        L.append("`/simulation-generate-suite` asks about this and builds it at its grounding gate. "
                 "Without a corpus the suite contains 0 retrieval scenarios and the knowledge base "
                 "is never tested.")
        if off_map:
            L += ["", off_map[0].upper() + off_map[1:]]
    elif off_map:
        # `why` is not printed: written over the map, it can say the agent has no search at all while one is cut.
        L.append(f"**No knowledge corpus for this map** — {off_map}")
    else:
        L.append("**No knowledge corpus needed**" + (f" — {why}" if why else ""))
    L.append("")
    return L


def _inline_entries(raw: object) -> list[dict]:
    """`grounding.inline`, refused unless every entry is `{name, tool, source}` with `source` = `<code file>::<variable>`.

    The variable is found by NAME, never by line range: an edit that moves lines must not lose the knowledge base."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"agent_topology.json: `grounding.inline` must be a JSON list of {{name, tool, source}}, got {type(raw).__name__}.")
    for i, entry in enumerate(raw):
        name = entry.get("name") if isinstance(entry, dict) else None
        source = entry.get("source") if isinstance(entry, dict) else None
        tool = entry.get("tool") if isinstance(entry, dict) else None
        path, sep, symbol = source.partition("::") if isinstance(source, str) else ("", "", "")
        if (not isinstance(name, str) or not name.strip() or "\n" in name or not (tool is None or (isinstance(tool, str) and tool.strip()))
                or not sep or not path.strip() or not _INLINE_SYMBOL.fullmatch(symbol) or _machine_path(path)):
            raise ValueError(
                f"agent_topology.json: `grounding.inline[{i}]` is {entry!r}. Each entry is {{\"name\": <one line>, \"tool\": <the "
                f"tool returning it, or null>, \"source\": \"<code file relative to the agent module>::<variable>\"}} — the variable "
                f"the literal is assigned to (`agent.py::lookup_knowledge_base.kb`, `agent.py::FAQ_ARTICLES`), never a line range.")
    return raw


SURFACE_KEYS = ("rag", "knowledge", "tools", "subagents", "skills")
STATUSES = ("live", "mocked")
#: What one delegating smoke turn showed about a sub-agent's own tool calls reaching the trail: `linked` (each inner row
#: carries `parent_call_id` = the delegation row's `call_id`), `recorded` (on the trail, unlinked), `unrecorded`.
INNER_CALLS = ("linked", "recorded", "unrecorded")
#: `store.owner`: the `world_state.owner` vocabulary (`verify_config` refuses any other word there).
STORE_OWNERS = ("mock", "agent", "external")


def _machine_path(value: object) -> bool:
    """An absolute or home-relative path: it names one machine, and the map and the page are shared."""
    text = str(value or "").strip()
    return text.startswith(("/", "~", "\\")) or (len(text) > 2 and text[0].isalpha() and text[1] == ":" and text[2] in "/\\")


def _live_tools(topo: dict) -> list[dict]:
    return [t for key in ("tools", "rag") for t in (topo.get(key) or []) if isinstance(t, dict) and t.get("status") == "live"]


def backend_typed(topo: dict) -> bool:
    """Whether the map was typed by a scan that writes `backend`: then every live tool must carry it. A map from before
    the fact (no live tool typed) renders, and :func:`main` names its untyped live tools on stderr."""
    return any("backend" in t for t in _live_tools(topo))


def _backend_problems(where: str, item: dict) -> list[str]:
    """A live tool's `backend`: the service its code signs in to (a database, an API, a cloud SDK; the local stack's
    included), or "none" when it opens no client. Its `kind: live` check proves the sign-in works (simulation-setup
    Step 8c.3); "none" is refused for agent code that reaches the network, which always opens a client."""
    backend = item.get("backend")
    if not isinstance(backend, str) or not backend.strip():
        return [f"{where}: `backend` is missing — name the service its code signs in to (a database, an API, a cloud SDK; the "
                'local stack\'s included), or "none" when its code opens no client']
    if backend.strip() == "none" and "network" in (item.get("effects") or []) and item.get("runs") == "in_process":
        return [f'{where}: `backend` is "none", but its own code reaches the network (`effects` has `network`), so it opens a client — name its service']
    return []


def spec_problems(topo: object) -> list[str]:
    """Every field the spec gets wrong, each named by its path; `[]` for a spec the map may be drawn from.

    The approval gate shows the map, and the overview page reads the canonical JSON this writes, so a
    field the page needs is refused here, while the authoring session still has the code in front of it."""
    if not isinstance(topo, dict) or not isinstance(topo.get("agent"), dict):
        return ["`agent` must be an object"]
    agent = topo["agent"]
    problems = [f"`agent.{key}` is missing or empty" for key in ("name", "framework") if not str(agent.get(key) or "").strip()]
    if _machine_path(agent.get("entry")):
        problems.append(f"`agent.entry` is a machine path ({agent['entry']!r}); write it relative to the agent module")
    for key in SURFACE_KEYS:
        items = topo.get(key)
        if items is None:
            continue
        if not isinstance(items, list):
            problems.append(f"`{key}` must be a list")
            continue
        for i, item in enumerate(items):
            where = f"`{key}[{i}]`" + (f" ({item.get('name')})" if isinstance(item, dict) and item.get("name") else "")
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                problems.append(f"{where}: `name` is missing or empty")
                continue
            if item.get("status") not in STATUSES:
                problems.append(f"{where}: `status` is {item.get('status')!r}; it must be one of {', '.join(STATUSES)}")
            if not str(item.get("why") or "").strip():
                problems.append(f"{where}: `why` is missing or empty — the map states why each surface is live or mocked")
            if key == "subagents" and ("call" not in item or not (item["call"] is None or str(item["call"]).strip())):
                problems.append(f"{where}: `call` is missing — write the exact name the delegation call carries on the tool "
                                "trail (declared as a `tools` entry), or null when the sub-agent is reached without a call")
            if key == "subagents" and item.get("call") is not None:
                owned = item.get("tools")
                if not isinstance(owned, list) or not all(isinstance(t, str) and t.strip() for t in owned):
                    problems.append(f"{where}: `tools` must list the tool names this sub-agent calls itself (`[]` for none)")
            if key in ("tools", "rag") and item.get("status") == "live" and backend_typed(topo):
                problems += _backend_problems(where, item)
            if key == "subagents" and "inner_calls" in item and item["inner_calls"] not in INNER_CALLS:
                problems.append(f"{where}: `inner_calls` is {item['inner_calls']!r}; it must be one of {', '.join(INNER_CALLS)}")
    store = topo.get("store")
    if isinstance(store, dict) and "owner" in store and store["owner"] not in STORE_OWNERS:
        problems.append(f"`store.owner` is {store['owner']!r}; it must be one of {', '.join(STORE_OWNERS)} (the `world_state.owner` the scan declares)")
    grounding = topo.get("grounding")
    if isinstance(grounding, dict) and isinstance(grounding.get("sources"), list):
        problems += [f"`grounding.sources[{i}]` is a machine path ({s!r}); write it relative to the agent module"
                     for i, s in enumerate(grounding["sources"]) if _machine_path(s)]
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render an agent-topology spec to md + svg.")
    ap.add_argument("topology", help="path to the topology JSON spec")
    ap.add_argument("--out-dir", required=True, help="the working dir (artifacts/ is created under it)")
    ap.add_argument(
        "--artifacts-dir",
        default=None,
        help="directory for ALL outputs — .md + .svg + canonical .json "
        "(default: <out-dir>/artifacts). Keep this folder gitignored; its contents are regenerable.",
    )
    ap.add_argument("--basename", default="agent_topology", help="basename for the artifacts")
    args = ap.parse_args(argv)

    topo = json.loads(Path(args.topology).read_text(encoding="utf-8"))
    if problems := spec_problems(topo):
        print("error: the topology spec is refused; nothing was written:", file=sys.stderr)
        print("\n".join(f"  - {problem}" for problem in problems), file=sys.stderr)
        return 2
    if untyped := [str(t.get("name")) for t in _live_tools(topo) if "backend" not in t]:
        print(f"warning: no live tool carries `backend` ({', '.join(untyped)}), so no credential check can be declared for "
              "them — type each live tool's `backend` (scan Step 2a)", file=sys.stderr)
    topo = mark_host_effects(topo)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    artifacts = Path(args.artifacts_dir) if args.artifacts_dir else out / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    # Everything (.md report + .svg + canonical .json) lives under artifacts/ —
    # one gitignored folder, so nothing derived clutters the working dir.
    md_path = artifacts / f"{args.basename}.md"
    svg_path = artifacts / f"{args.basename}.svg"
    json_path = artifacts / f"{args.basename}.json"
    svg_ref = f"{args.basename}.svg"  # md + svg are siblings

    production = load_production(artifacts, out)
    svg_path.write_text(render_svg(topo, production), encoding="utf-8")
    json_path.write_text(json.dumps(topo, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_md(topo, svg_ref, production, load_scope_tools(artifacts)), encoding="utf-8")
    print(f"wrote {md_path}")
    print(f"wrote {svg_path}")
    print(f"wrote {json_path}")
    return 0


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
