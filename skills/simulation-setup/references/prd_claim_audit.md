# PRD claim audit — the independent reader (setup Step 9c)

A claim about which code writes a field, or what is counted toward a limit, is true or false at sites the claim
does not name: a second writer, a shared builder's default branch, a second list added to the counted one. The
author reads one caller and cites a real line. This reader opens every site `claim_sites.py` listed instead.

Spawn ONE fresh `general-purpose` sub-agent with the prompt below and wait for it. Fill the three paths and give it nothing else:
not your notes, not any sub-agent's summary of the code, not why a claim was written. A reader that inherits a
summary inherits its omissions.

## The prompt

```
You audit claims in an AI agent's PRD against the agent's own code. You are report-only: you never edit the
PRD or the code, and you write exactly one file.

Inputs:
- the PRD: <WORKDIR>/simulation_prd.md
- the site list: <WORKDIR>/artifacts/claim_sites.json. For each declared name (an entity field, an agent-context
  field, a tool, a fact's subject, a name the PRD back-ticks) it lists every line of the code that writes,
  defaults or reads that name, strongest first, with `total` and `truncated`.
- the repo root, which every path in both files is relative to: <REPO_ROOT>

Read no other file under plurai_sim/ (<WORKDIR>): its notes, mocks and other artifacts are the author's view.

1. Read the PRD's entity-field rows and field descriptions, each tool's preconditions and side effects, and the
   business rules and hard limits.
2. Take every claim that narrows WHO writes or defaults a field, WHAT is counted toward a limit or feeds a check,
   or WHEN a write takes effect: "set only by X", "when committed by X", "Y does not count", "only uploaded rows
   count", "Y feeds Z", "never changed after creation". Skip every other sentence. Skip closed vocabularies,
   absences ('cannot …'), naming conventions and model/config settings unless the sentence itself says who
   writes, what counts, or when.
3. For each claim, name the declared names it is about and open EVERY site the list gives for them. Read enough
   around each site to see the branch it sits in and what flows into it, and follow a call into its callee when
   the claim depends on it, and open the lines the claim itself cites. Where a name is `truncated` or not in the
   list, search the repo for it yourself.
4. Give one verdict:
   - supported: the sites agree with the claim and none contradicts it;
   - refuted: only when, on a path the agent's tool calls reach with arguments its declared schema offers, a site
     does otherwise (another writer, a default branch, a second input to a count). An argument the schema does not
     offer, a seed row stored as written, a test, or an unconfigured provider is not a contradiction: use
     `unsupported` and name it in the reason;
   - unsupported: no site decides it either way.
5. Write one JSON object per line to <WORKDIR>/artifacts/prd_claim_audit.jsonl, replacing the file:
   {"claim": "<the claim's words copied exactly from the PRD; for a table row, the cell>",
    "where": "<section / heading>", "names": ["<name>", ...],
    "verdict": "supported" | "refuted" | "unsupported",
    "sites": ["<path>:<LINE>[-<END>]", ...], "reason": "<one sentence: what those lines do>"}
   A supported or refuted row names the sites that decide it.
6. Reply with the three counts only.
```

## What reads the file

`lint_prd.py` reads `artifacts/prd_claim_audit.jsonl` beside the PRD. Each `refuted` row is an open finding while
its claim's words are still in the PRD; it closes when the PRD rewrites them, or with `--accept <fp> --reason
"<the file:line you verified>"`. A refuted row whose words were never in the audited PRD stays open as a misquote.
`unsupported` rows, and a refuted row that names no site, are counted in the status line, never findings. With no file the status line reads
`claim audit: not run`.
