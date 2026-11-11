# Gate findings — what to do with one

A gate finding is the backend (or the SDK) telling you what one of its checks saw. It is evidence,
not a verdict: you decide what happens next, alone. The user is never asked about a finding or an
overrule, and neither ever appears in anything the customer reads (hand-off, run report, suite
report, `evals.md`, `scenarios.md`).

## Where findings arrive

- a job's `detail`: its gate findings, with the overrules it applied or found stale beside them;
- a grounding job's detail, on the findings block of the job that consolidated a delivery: `docs/grounding-findings.md`
  handles them, including the hold on a trace corpus whose environment is not attested;
- an edit-session turn or an `end_*_session` result;
- a refusal naming a `gate_id`, or an overrule whose outcome is `refused`;
- `score_run` and the run's own outputs (the SDK's internal gate record and its stderr).

Each finding carries `gate_id`, `authority`, `subject`, `evidence`, `remedy`, `fingerprint`, `holds`,
and `draft_ref` when work was held back. Copy fields; never recompute or paraphrase them.

## Dispatch on `authority` — and on nothing else

The backend resolves `authority` from its own registry; you need no copy of it, and the `gate_id`
never changes the branch you take.

| `authority` | what it means | what you do |
|---|---|---|
| `security` | credentials, tenancy, consent, privacy, isolation | Never overrule; an overrule is refused. When the remedy is a grant or credential only the user holds (a write or host-effect grant, a route grant, a key), ask for that material per error posture item 2; otherwise run the contact hand-off (`stop_message.py contact --done`). |
| `invariant` | downstream would crash or corrupt on this input | repair the input or regenerate. Held work is fetchable by `draft_ref` — repair from it rather than starting over. Never overrule. |
| `advisory` | everything else; the operation shipped with the finding attached — one with `holds: true` keeps back one effect (a commit, a release, a score) until you repair or overrule it | the advisory procedure below |

## Advisory procedure

Take the first branch that applies:

1. **It changes nothing shipped, scored or held** → accept it silently and continue.
2. **Its `remedy` is in reach** → repair (re-call with the fix, an edit session, a regeneration).
3. **You hold evidence the backend could not see that the finding is wrong for THIS agent** — a
   file:line in the customer's repo, the agent's code, a PRD section, a run artifact path → send an
   Overrule. Otherwise repair or regenerate.

A reading of the installed SDK or plugin is never evidence about a server-side mechanism; the
backend's own measurement (a scored run, an audit result) is. Evidence from the customer's repo,
agent, PRD or run is exactly what an overrule cites.

## Writing an Overrule

Send an Overrule for the finding's `gate_id` + `fingerprint` with your reason, on the re-call of
the SAME operation that reported it (the same tool, or the `end_*_session` that commits the edit):

- `gate_id` and `fingerprint` are copied from the finding, byte for byte;
- `reason` (at least 20 characters) states what you verified and where — the file:line, the PRD
  section, the run artifact.

Each overrule comes back with an outcome:

- `applied` → done; the effect the finding held is released.
- `stale` → the evidence moved since you judged it; decide again on the new finding.
- `not_holding` → the finding held nothing; nothing changes.
- `not_evaluated` → that gate did not run in this call; send it on the call that runs it.
- `refused` (in the result's `gate_authority`, under `overrules`; locally `gate_ledger: overrule_refused`, exit 2) → by the finding's `authority`: on a `security` gate the
  protection stands and its effect stays withheld — run the contact hand-off (`stop_message.py contact --done`) only when you
  believe the gate itself is wrong; on an `invariant` gate repair what the finding names, then re-run. A
  holding finding of missing evidence (`evidence_missing`) is also refused: no overrule lifts it, so the plugin
  never sends one. Repair it by removing the part (`grounding_findings.py remove`) or delivering it again. A `refused` outcome is
  an answer, not an error: the finding keeps holding and the call goes on.

## Findings that set a verdict aside

A finding on a scored cell set the judge's verdict aside after the judge decided it, or filed a red
to another owner. The cell keeps the raw verdict; the finding's `remedy` says what to read. Read the
transcript turns and the rubric yourself, with judgement, never by matching words. Overrule to
restore the raw verdict when the transcript shows the judge was right; otherwise leave it set aside.
An overrule never makes a verdict worse than the judge's raw one: that is an eval edit.

Restore on the run that scored it: `score_run(..., run_upload_id=<the upload>, rescore='changed',
overrules=[...])` re-judges nothing, the run upload records the overrule, and every later re-score
and suite report of that run applies it. Apply that result to the conversation's verdicts
(`scripts/apply_rescore.py`, with `--pin "$WORKDIR"` or `--art "$ART"`) so the report reads the
restored cell: the verdicts keep the effective verdict only, and the applied overrule goes to this plugin's
ledger, never to a file that is uploaded or rendered. A finding that arrived on
`suite_diagnostics_start` is overruled on its re-call with the same arguments.

## Plugin-local findings

The plugin's own scripts (verifiers, smokes, the run gate, …) print the same record as a `finding:`
line: `finding: <gate_id> <authority>[ holding] <fingerprint> — <remedy>`. The same dispatch applies.
To overrule an advisory one, record it in the plugin's ledger — never by hand, never in a file the
customer reads:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gate_ledger.py" overrule --art "$ART" --gate-id <gate_id> \
  --fingerprint <fingerprint> --reason "<what you verified, citing the customer's repo file:line, PRD section or run artifact>"
```

Then re-run the check that printed it. Exit 2 is a refusal (a `security`/`invariant` id, a reason
under 20 characters, a malformed fingerprint) — read it; a refused `security` finding stands (run the contact hand-off (`stop_message.py contact --done`) only when you believe the gate is wrong), a refused `invariant` one is
repaired, and neither gets a second attempt.

The world read-back findings of `verify_smoke.py --seed-world` and the Init smoke (their table:
`skills/simulation-setup-python/references/finding-codes.md`) are advisory and holding: ONE repair round in the
harness lane of `/analyze-simulation`, then re-run the manifest's `seed_readback` check; still standing, overrule it with
the reason and name it as a risk.

The runner's own findings (`ERROR: held by gate …` on its stderr, `gate_authority.json` in its
`--out`) are overruled the same way, by their own `gate_id`, with `--finding-from` naming that
`gate_authority.json`, so a `security` or `invariant` one is refused when written: the runner reads
this ledger through `--overrule`, so re-running the run is the re-run of the check. It skips every `plugin.`
id there: a plugin finding the runner holds again as its own is overruled under the runner's `gate_id` too,
with the same reason.

The authoring validators (`verify_policies.py`, the scenario-variables `verify.py`,
`verify_state_schema.py`) print no `finding:` line. Their errors are inputs the
backend refuses or cannot read: treat one as `invariant` and repair the input. Their `⚠` lines
hold nothing: repair when the remedy is in reach (advisory branch 2); otherwise continue. There is nothing to overrule.

`lint_prd.py` differs: each `⚠` line is a finding that HOLDS (exit `4`, and the manifest files
the check `not_verified`) until the PRD is repaired or the finding is accepted with `lint_prd.py … --accept <fp> --reason
"<what you verified>"`, where `<fp>` is the `[fp:xxxxxxxx]` that ends the line. That is the overrule path for this gate: the
reason lands in `$ART/.gate_overrules.jsonl` and the acceptance lapses when the finding's text changes. Its errors
(`✖`, exit `1`) stay inputs to repair.

## Never

- overrule in bulk, or overrule a finding you did not read;
- overrule to turn a number green without evidence;
- ask the user about a finding or an overrule, or mention either in a hand-off or report;
- keep your own record of a backend finding's overrule — the backend's ledger and the `overrules` of the
  result's `gate_authority` are the record (`apply_rescore.py` moves a `score_run` result's applied ones into the
  plugin ledger, since the verdicts carry none); plugin-local findings go only through `scripts/gate_ledger.py`.
