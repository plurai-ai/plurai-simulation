# PRD authoring contract — the facts block `lint_prd.py` holds you to

The PRD is the one input carrying the agent's ground truth to scenario generation and every judge; its
`## Agent facts` block is linted. The skills carry the short form inline; this page adds the worked block.
Structure and fill-in sections: `references/simulation_prd_template.md` (delete every placeholder). The
closed vocabulary: `references/agent_facts.md`.

## The short form (inline in the skills)

<!-- inline:begin -->
**PRD facts contract.** (1) Write the PRD without an `## Agent facts` block and run `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/scaffold_rule_facts.py --profile artifacts/data_profile.md` before you type any fact: it adds the block and writes the `instruction_sources`, one `rule` fact per instruction, a line or the lines one wrapped sentence spans (`subject` = `source` = `path:LO-HI`, `evidence` = the agent's own bytes), the quoted block, and the measured facts the profiled files settle (closed URL sets, URLs outside them, topics no document covers); confirm or drop each. `--source` is the text the model is given as instructions: prompts, task and agent configs, each skill or rules file (`.claude/skills/*/SKILL.md` for a Claude Agent SDK agent), each live sub-agent's own prompt, and what fills each template `{slot}`: follow it to the code, declare the file or range that fills it with instructions, decline a data slot. The lint reports a loaded file you neither declared nor declined. Never one fact per file, never a paraphrase, never a slug for a subject. (2) Add only facts you measured: `kind`, `subject`, `predicate`, `evidence` (a number or the quoted bytes), `source`; cite code as repo-root-relative `path:LINE` or mark the claim `(inferred)`. (3) Run `lint_prd.py` until it exits `0`: exit `4` is open work (a measured fact you never typed counts), and a finding stays only with `--accept <fp> --reason`. (4) Write the prose from the agent's own instructions and code, never as a summary and never by copying a corpus document (the verbatim appendix carries those; summarise a corpus procedure in one line with its `path:LO-HI` citation): it states the agent's hard limits (context and history handling, how the prompt is built, model and env defaults, size caps), every closed vocabulary or catalog the agent stays inside with all its members, its absences (what it cannot do), the procedures its own instructions state as numbered steps, and two or three example exchanges that show the expected reply shape; `lint_prd.py` prints a hint, not a finding, when the prose shows too few of these or repeats appendix lines. The worked block and the required prose content are in `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/references/prd_authoring_contract.md`.
<!-- inline:end -->

## The order of work

1. Write the prose sections (the required content below) with no `## Agent facts` block.
2. Run the scaffold (setup Step 9) with `--profile artifacts/data_profile.md`. A re-run replaces everything between
   its `scaffold:` markers. Front matter, fences and tables of a markdown source are declined
   (`--row` keeps one); decline any other non-instruction with `--decline`; name the `RENDER_ACTIONS` sweep verbs with
   `--omit-terms`. Read each proposed measured fact against its source; drop one with `--skip-measured SUBJECT`.
3. Add the facts only you can measure (tool surfaces, absences the sweep cannot see) outside the markers.
4. Run `lint_prd.py` and fix every finding until it is clean.

## Required prose content

The prose carries what no typed fact can, the agent's shape, written from its own instructions and code:

1. **Hard limits**: context and history handling, how the prompt is built (once per conversation or per turn),
   model and env defaults, size caps, retries and timeouts.
2. **Closed vocabularies and catalogs**: every set its own instructions or code keep it inside (states, roles, types,
   ids, links, tool names), with all members and a `path:LO-HI` citation, as a table or list.
3. **Absences**: what it cannot do (no tools, no write path, no memory past the thread), so which requests it declines.
4. **Procedures**: each step sequence its own instructions or code state, as numbered steps with a `path:LO-HI`
   citation. A corpus document is never copied into the prose: the verbatim appendix carries it. Summarise a corpus
   procedure in one line with its citation.
5. **Example exchanges**: two or three short user and agent turns (main path, out-of-scope ask, request to act) showing
   the reply shape (length, list use, closing line).

Three sentences per section hold none of this. Corpus documents pasted beside the appendix are a duplicate copy with
no depth: `lint_prd.py` skips those lines and hints when many repeat. It also hints (no finding, no exit code) when the
prose shows under two of four traces: turn-marked example lines, ordered steps, a table, a heading naming limits,
absences, a catalog or a vocabulary.

## What the lint holds

1. **`subject` of a `rule` is the cited `path:LO-HI`, and `source` is the same.** A slug resolves to nothing.
2. **`evidence` of a `rule` is the agent's own bytes**, the whole range, also quoted in the PRD's
   `### The agent's own instructions, quoted` block. A summary is law the agent was never given.
3. **One rule per fact**: a line, or the lines one wrapped sentence spans; a lead-in and each item of its list are
   separate rules. The scaffold and the lint split alike; a fact holding several is "too coarse".
4. **Every contentful line of an `instruction_sources` range is rowed or declined** (`not-an-instruction`,
   `duplicate-of-<subject>` naming a real `rule` subject, `not-loaded` with `source: path:LINE`); a slot-only line
   takes only `filled-by` (the declared range filling it) or `data-slot` (the line filling it). A missing
   `instruction_sources` is an open finding.
5. **A declared `render_actions:` is closed by one `RENDER_ACTIONS` / `omits` fact** whose `check:` terms are verbs the
   agent does NOT have. Every other fact has all five keys and a `kind` / `predicate` pair from the closed set;
   `evidence` is a measurement, never an instruction to the generator. One fact per `subject` + `predicate`.

## A worked facts block

The values show the shape; never copy one.

```yaml
instruction_sources:
  - path: prompts/system_prompt.md
    lines: 1-18
    unrowed:
      - {lines: 3-3, class: not-an-instruction}
  - path: prompts/response_prompt.md
    lines: 1-4
    unrowed:
      - {lines: 3-3, class: duplicate-of-prompts/system_prompt.md:12}
facts:
  - kind: rule
    subject: prompts/system_prompt.md:5
    predicate: instructs
    evidence: "Your job is to walk a new member through their first steps:"
    source: prompts/system_prompt.md:5
  - kind: rule
    subject: prompts/system_prompt.md:6
    predicate: instructs
    evidence: "1. Finding a book in the catalogue."
    source: prompts/system_prompt.md:6
  - kind: rule
    subject: prompts/system_prompt.md:7
    predicate: instructs
    evidence: "2. Placing a hold."
    source: prompts/system_prompt.md:7
  - kind: rule
    subject: prompts/system_prompt.md:12
    predicate: instructs
    evidence: "- Answer **only** from the help articles provided below. They are your complete knowledge base."
    source: prompts/system_prompt.md:12
  - kind: rule
    subject: prompts/system_prompt.md:13
    predicate: instructs
    evidence: "- If the user asks about something outside them, say so and point them to the front desk."
    source: prompts/system_prompt.md:13
  - kind: rule
    subject: prompts/system_prompt.md:18
    predicate: instructs
    evidence: "Warm and concise."
    source: prompts/system_prompt.md:18
  - kind: rule
    subject: prompts/response_prompt.md:4
    predicate: instructs
    evidence: "End with one short follow-up question."
    source: prompts/response_prompt.md:4
  - kind: tool_surface
    subject: RENDER_ACTIONS
    predicate: omits
    evidence: "plurai.config.yaml declares render_actions: none; none of the swept verbs occurs in the instruction files or the help articles"
    source: plurai.config.yaml
    check:
      kind: absence
      terms: [render_card, open_panel, navigate_to, handoff]
      match: any
  - kind: data_profile
    subject: help_link.url
    predicate: closed_set
    evidence: "2 help_link entries carry 2 url values: https://example.org/help/loans, https://example.org/help/holds"
    source: assets/links.yaml:4
    check:
      kind: existence
      terms: ["https://example.org/help/loans", "https://example.org/help/holds"]
      match: all
  - kind: data_profile
    subject: overdue_charges
    predicate: absent_from_source
    evidence: "fines, fees and payments occur in none of the help articles or in either prompt file"
    source: assets/help/
    check:
      kind: absence
      terms: [fine, fee, payment]
      match: any
      probe_ask: "How much is the fine if my book is late?"
```

Below the block, the quoted-instructions block holds each paragraph once, as the rules' context:

````markdown
### The agent's own instructions, quoted

From `prompts/system_prompt.md:5-7`:

```
Your job is to walk a new member through their first steps:
1. Finding a book in the catalogue.
2. Placing a hold.
```
````

## Citations

- A `` `path/to/file.py:LINE` `` citation is repo-root-relative (`src/pkg/utils.py:11`, not `utils.py:11`), back-ticked,
  and must resolve; read the lines you cite. A claim about what the code does cites its lines
  or carries `(inferred)`. A write-path or count-path claim comes from code you opened, never from a sub-agent's summary.
- A corpus claim carries `(source: <doc>)` or `(unverified — not found in corpus)`; a tool-spec behaviour carries
  `(observed — probe_report.md)` or `(from code — unprobed)`.
- Never write a target-shape or grading rule derived from this agent: state the measurement as a fact; the backend
  holds the rule.
