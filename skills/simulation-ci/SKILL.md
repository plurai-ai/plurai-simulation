---
name: simulation-ci
description: >-
  Set up CI for your simulations in one command: finds each agent, checks it is ready, renders a GitHub workflow per agent with its ci.yaml, and prints the token and secret commands. Run again to edit the setup.
allowed-tools: Read, Grep, Glob, Bash
---

# /simulation-ci

**Usage:** `/simulation-ci`

You are the LOCAL Claude Code agent. This command takes a repository with one or more working simulation directories
(`plurai_sim/`) to a first CI run. The workflow starts by hand; the user decides which other events start it, because each run spends
credits. It stops for two confirmations only: which agents get CI and the smoke list. Run again, it finds the
setup that exists, runs the readiness check, offers any fix first and then edits.

**Three rules hold in every step.**

- **Scripts write every file.** You never write or edit workflow YAML or `ci.yaml` text yourself. The scripts in
  `${CLAUDE_PLUGIN_ROOT}/skills/simulation-ci/scripts/` render and edit them, then run `plurai ci validate`. A script that
  refuses a value (a name, a directory) names the fix: say it, and never route around it.
- **You never see a secret.** You read variable NAMES only. Never read, print or ask for a key, a token or a `.env` value, and
  never run `plurai login`, `plurai ci token` or `gh secret set`: print them for the user (Step 5).
- **No cost or credit figure.** The size of a run is a thread count (scenarios × trials). Spend is in the Plurai web dashboard.

<!-- BEGIN backend-check: generated from ${CLAUDE_PLUGIN_ROOT}/docs/backend-check-block.md; edit there -->
## Check the backend first

Before anything else in this skill, run the backend check. It needs no backend tool and no SDK, and it comes before the
tool-list step of the error posture:

```bash
python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py"
```

- Exit 0: go on. A line it printed is an update notice: say it once, as printed, first; it stops nothing.
- Exit 7 is a version refusal: follow the error posture's version refusal rule. Say what the check printed, verbatim and
  alone, with nothing before it: not the exit code, not that it is a refusal. Then stop. No tool call, scan or setup follows.
- Exit 8 is a credit refusal: its line is `CREDIT-STOP: <sentence>`. Follow the error posture's credit refusal rule.
- Exit 3 prints one line. A `SETUP` line (no usable login; this skill has no readiness step, so the check is its login
  test): run the `harness-setup` skill from the command the user typed (`from /<command>`) and show the user nothing from
  the line. That skill checks the backend after its login, so go on when it reports Plurai set up, and stop when it stops.
  Any other line, such as a `NOT READY` line for hooks Codex has not trusted: tell the user in plain words what it says
  to fix, and stop.
<!-- END backend-check -->

## Step 0 — resolve the tools and look at what exists

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require 'ci validate')" || { [ $? -eq 3 ] && exit 1; }; [ -n "$PLURAI_BIN" ] || exit 1   # 3 = the resolver stopped or asked: follow the lines it printed. Empty = no runner found: stop — never proceed on an empty PLURAI_BIN
CI="${CLAUDE_PLUGIN_ROOT}/skills/simulation-ci/scripts"
REPO="$(git rev-parse --show-toplevel)"
"$PLURAI_PY" "$CI/ci_edit.py" state --repo "$REPO" --plurai "$PLURAI_BIN"
```

The shell resets between Bash calls, so each block below, and each command of **Re-entry**, opens with the lines it needs from this one: `resolve-python.sh`, `CI` and
`REPO`, and the `PLURAI_BIN` line where the block passes `--plurai`.

A block that stops at the `PLURAI_BIN` line (exit 3, or no runner found) has not run what follows it. With exit 3, follow the
lines the resolver printed. With an empty runner there is no usable `plurai`: say so and stop. Never run the readiness check or an edit
with an empty runner, and never call an agent ready or a file validated without one: the readiness check then prints `FIX ... validator-not-run`
and `NOT READY`, and a script prints `NOT-VALIDATED`, which means the files are written and **unchecked** (say that, and run `plurai ci validate`
from the agent folder once a runner exists).

`state` prints one JSON row per `plurai_sim/` directory: whether its workflow and `ci.yaml` exist, the events its workflow runs on
(`events`, and `pull_request` true when that trigger is uncommented), whether a report exists, the edits on offer, and, for a
configured agent, `fixes`: the `FIX` lines of the readiness check (Step 2), run as part of `state`. No row means there is nothing to set up: say to run `/simulation-scan-agent` first, and stop. If every agent is
`configured`, go to **Re-entry**. Otherwise this is a first setup (an agent that is not configured joins it, also on a
repository where other agents already are); there, say the `fixes` of each configured row first and work through them as Step 2
does, before Step 1.

## Step 1 — find the agents (confirmation 1 of 2)

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
CI="${CLAUDE_PLUGIN_ROOT}/skills/simulation-ci/scripts"
REPO="$(git rev-parse --show-toplevel)"
"$PLURAI_PY" "$CI/ci_render.py" discover --repo "$REPO"
```

Each row is an agent: its `name` (the workflow is `.github/workflows/plurai-<name>.yml`) and its directory. A `NAME-CLASH` line means two
directories share a name: pick a name for one (`--name <dir>=<name>` in Step 4). A directory with `"safe": false` has characters
that cannot go into a workflow or a command: tell the user to rename it, and leave it out. With one agent, say which one you will
set up. With several, ask which get CI; each chosen agent gets its own workflow file and its own `ci.yaml`, and one red agent never
hides another. Steps 2 to 4 run for each chosen agent.

## Step 2 — the readiness check

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require 'ci validate')" || { [ $? -eq 3 ] && exit 1; }; [ -n "$PLURAI_BIN" ] || exit 1   # 3 = the resolver stopped or asked: follow the lines it printed. Empty = no runner found: stop — never proceed on an empty PLURAI_BIN
CI="${CLAUDE_PLUGIN_ROOT}/skills/simulation-ci/scripts"
REPO="$(git rev-parse --show-toplevel)"
"$PLURAI_PY" "$CI/ci_readiness.py" check --repo "$REPO" --agent <dir> --plurai "$PLURAI_BIN" [--catalog "$CATALOG"]
```

One `FIX` line per thing that stops CI, each with its fix; `ADVISE` lines are recommendations; `INFO` lines are facts. A ready agent
prints no `FIX` and no `ADVISE`. Work through them:

- `FIX` lines naming a skill (`no-id-file`, `bare-env-layout`, `no-env`): say what is missing and run or name that skill. A fix that
  changes the user's own files (a lockfile, `.gitignore`, the Python version) is the user's to make: say what to do and
  wait.
- `id-copies-disagree`: the scenario or eval set id has copies (the env folder's marker, its manifest key, the root marker) that hold
  different ids, so CI would test one the user may not mean. Ask which id the suite should test (the finding names each copy
  and the id CI reads), write it with the `write_id_file.py set` command the fix names (it runs under `"$PLURAI_PY"` with an absolute `--workdir`,
  after `resolve-python.sh`), and list the files to commit with
  **Files to commit** (the write can add a marker that did not exist).
- `id-files-uncommitted` is an `ADVISE`, never a block: a suite id file (a marker or a manifest key) differs from `HEAD`, so a push
  without it makes CI run the suite `HEAD` names, with no sign of the edit. Name each file as the finding names it, with the id at
  `HEAD` and the id on disk, and tell the user to commit them with the change that moved them (list them with **Files to commit**).
  Say the finding's last sentence too: after a suite change merges, main needs a run (see Step 4). For the same list from any skill:
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/id_changes.py" list --workdir <the plurai_sim directory>`.
- `validator-not-run`: `plurai ci validate` did not run, because no runner was given. Nothing was checked, so the agent is not ready: resolve the
  runner (Step 0) and run the check again. The validator runs last, once no other `FIX` is left, so a `validate-refused` can appear only after
  the other fixes are done; it also covers a validator that did not finish in time (exit 124 in the line).
- `id-conflict`: an id file holds an unresolved merge conflict, so it names no id. Say the fix the finding names (resolve the merge, take main's id,
  re-apply the edit with the skill that made it) and stop for this agent. `id-files-unchecked` is an `ADVISE`: git gave no `HEAD` to compare the id
  files with, so say that the comparison did not run.
- `claude-cli-login`: the agent signs in through a local `claude` login, which cannot run in CI. Say the fix and stop for this agent.
- `sdk-pin`: a lockfile or the project's virtual environment holds the Plurai SDK on another minor (`1.0` against `1.1`) than the
  one this plugin release works with. The lock decides which SDK CI runs, and the CI action accepts the same minor, so another
  minor would be refused. Say the fix: move the lock to a release of that minor. For a uv project that is
  the `uv add --dev` line the finding names, which the SDK check of the next Plurai command offers to run after the user says yes; for Poetry and
  requirements projects the line is the user's to run. When the project is on a newer minor, the plugin needs the update.
  A project with no SDK in its lock has no finding: the action installs the tested release.
- `agent-env-missing` lists NAMES the agent reads that `agent.env` lacks (from the bench manifest's provider key, the agent's `.env`
  names and the credentials of `real` or `observe` tools). Ask once which to add (names only). Add the confirmed ones:
  `"$PLURAI_PY" "$CI/ci_edit.py" agent-env add --repo "$REPO" --agent <dir> --plurai "$PLURAI_BIN" NAME [NAME ...]`.
  `cloud-provider` means Bedrock, Azure or Vertex: use `--oidc <aws|azure|gcp>` in Step 4 instead of a stored key.
- `online-unchecked` is an `INFO`: whether the committed ids exist on the backend needs the CI token, which you never hold. Say the
  first CI run checks it, and `plurai ci validate --online` does it locally once the token exists.
- `smoke-over-default` is an `ADVISE`, never a block: the smoke list, `trials` and the `regression_*` scenarios make a run of more threads than the
  12 the proposal keeps to. That size is a provisional default, not a limit. Say the thread count and that the run costs more threads, and let the
  user keep it or shorten the list (Step 3).
- `agent-env-reserved` is a `FIX`: `agent.env` names a variable a CI run refuses: a CI credential, token endpoint or job-steering file
  (`GITHUB_TOKEN`, `GH_TOKEN`, `ACTIONS_ID_TOKEN_REQUEST_TOKEN`, `GITHUB_ENV`, `CI_JOB_TOKEN` and the like), any `PLURAI_*` name, or a name
  under a CI platform prefix (`GITHUB_`, `CI_`, `RUNNER_`, …) that reads as a credential (`TOKEN`, `KEY`, `SECRET`, `PASSWORD`, `JWT`, `OIDC`).
  A platform's other variables (`CI_ENVIRONMENT`, `GITHUB_REPOSITORY`) are ordinary. Such a name is refused, never passed on silently. Say the
  variable, the reason and the fix the finding names: remove it from
  `agent.env`, and give the agent a variable name of its own if it needs a key. The render, `agent-env add` and `github-names` refuse it the same way.
- `INFO run-size` and `posture`: tell the user the run size as threads, and the ingestion posture with what a transcript can then
  contain. Add nothing about cost.

Run the check again after each fix, until no `FIX` line is left or the user stops. After a fix that wrote files (an id, an
`agent.env` name, a skill you ran), list **Files to commit**.

## Step 3 — the smoke list (confirmation 2 of 2)

A manual run, a pull request and a merge-queue entry run the **smoke list** plus every `regression_*` scenario; a manual run can pick
`full` instead, and a push to the default branch and the nightly run the whole set. Build the
catalog of the committed scenario set: a JSON file (not YAML) of `{"name": "<scenario>", "gating_cells": <n>}` for each scenario in
the set `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/read_id.py" scenarios --workdir <the plurai_sim directory>` prints (its names come from the backend's metadata for that set). `gating_cells` counts the
policies of the committed eval set whose `verdict_tier` is in the gating tags (`catastrophic`, `failing`; the default `fail_on.tags`
also gates `regression`) that apply to the scenario; leave a pair out when the last report says it is not applicable. Write the file
under the scratch directory, then:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
CI="${CLAUDE_PLUGIN_ROOT}/skills/simulation-ci/scripts"
REPO="$(git rev-parse --show-toplevel)"
"$PLURAI_PY" "$CI/ci_smoke.py" propose --catalog "$CATALOG" --trials 3
```

It prints JSON: the proposed `smoke` names, the `regressions` that are always in, the thread count and `cap_threads`, the default size the
proposal keeps to (12 threads, provisional). Show the list and the thread count (for example "4 scenarios × 3 trials = 12 threads on a
smoke run"), say that regressions are always included, and let the user confirm or edit it. A longer list is allowed: the scripts print a
`NOTE` that the run is over the default size and costs more threads; say that, and do not refuse. Everything else takes its default (`trials: 3`, the default `fail_on.tags`). There is no interview about
budget, thresholds or tiers; a wrong tier is fixed in the eval itself (ask me to edit the evals).

The workflow starts by hand and asks no trigger question: `workflow_dispatch` is on, and the other four events are commented lines in
its triggers block (Step 4). When the user asks about runs on pull requests, a merge queue, pushes or a schedule, point at those lines:
each is off until the user uncomments it, and each run of an event spends credits. Uncomment the lines they name on request, as an
ordinary edit of the user-owned block, after they confirm which events; never turn one on that they did not name. The check can be
required in branch protection only once `pull_request` is on: a required check that never runs on a pull request blocks every merge.

## Step 4 — render

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require 'ci validate')" || { [ $? -eq 3 ] && exit 1; }; [ -n "$PLURAI_BIN" ] || exit 1   # 3 = the resolver stopped or asked: follow the lines it printed. Empty = no runner found: stop — never proceed on an empty PLURAI_BIN
CI="${CLAUDE_PLUGIN_ROOT}/skills/simulation-ci/scripts"
REPO="$(git rev-parse --show-toplevel)"
"$PLURAI_PY" "$CI/ci_render.py" render --repo "$REPO" --agent <dir> --smoke '<dir>=a,b' [--catalog "$CATALOG"] [--oidc aws] [--name <dir>=<name>] --plurai "$PLURAI_BIN"
```

Repeat `--agent` and `--smoke` for each chosen agent. It writes, for each agent,
`.github/workflows/plurai-<name>.yml` and `plurai_sim/ci.yaml` (kept if it exists, except that a confirmed smoke list is written into it:
a `SMOKE-SET` line names the new list and the old one, and the file's comments stay), and runs `plurai ci validate`. `--catalog` is the
catalog of Step 3: its `regression_*` scenarios count toward the size of a smoke run, and a run over the default size of 12 threads prints a `NOTE` and
still renders. Read its lines: `REFUSED` names the fix; say it and stop (a
refused render puts back every file it wrote, and removes the ones it created). Exit 7 or 13 means the validator itself stopped: say its lines
verbatim, as the error posture below says. `NOT-VALIDATED` means no runner was given and nothing was checked: say the files are unchecked.
`REFUSED-UNMANAGED` means a file already sits at the workflow's path that this script did not write: show the user, and re-run with `--force` only
on a yes to replacing it. `FORBIDDEN-TRIGGER` and `FORBIDDEN-WALL-BOUND` name something in the file that no flag overrides: tell the user to
remove it by hand (`pull_request_target` from the triggers, anything but `timeout-minutes` from the wall-bound region). Name the files it changed from **Files to commit**, not from its lines alone (earlier
fixes stay pending beside them). Tell the user:

- the workflow starts by hand (`workflow_dispatch`, `smoke` unless the run picks `full`). The triggers block is theirs: it holds
  `pull_request`, `merge_group`, `push` and `schedule` as commented lines, each with a one-line note, and they uncomment what they
  want. A re-render keeps the block as the file holds it;
- the workflow has two jobs: `simulate` (read-only, runs the agent) and `report` (the only write permission, never runs agent code).
  `report` posts one sticky comment per agent on pull requests and, on every event that ran `simulate`, creates a check
  run named "Plurai simulation results (<agent>)" that holds the report, through the Checks API with the job's token (no outside
  action). That check is not the required one. The job needs `pull-requests: write` and `checks: write`. Deleting the `report` job turns both off;
- the uv and Poetry install steps (uv: `uv sync --no-default-groups --group dev`; Poetry: an in-project `.venv` with
  `poetry install --no-root --with dev`) install the dev group, where the plugin puts the SDK, so the `.venv` the action finds holds the SDK the
  lock names. A project whose `pyproject.toml` has no dev group gets `uv sync` or `poetry install --no-root` without the group flag, which uv
  and Poetry would refuse. The action uses an SDK of its pin's minor as is, so a lock bump inside the minor needs no new action release. `--no-dev` or
  `--without dev` in these steps leaves the SDK out, and the action then installs its own pin, which the SDK's lock check refuses whenever the lock
  names another version. A requirements project gets `pip install -r` into the setup-python interpreter. The install steps are theirs to change,
  as are the `env:` block (one `${{ secrets.NAME }}` line per `agent.env` name; change a line to `vars.NAME` for a non-secret) and the wall bound
  `timeout-minutes`. The template carries no number for the wall bound: set one after a first run shows how long it takes;
- main never runs on its own, so say this once, from the printed lines: after a suite change merges, run the workflow on the default
  branch (Actions > Run workflow), or turn on the `push` trigger, because until main has run on the new suite the next pull request has
  no "vs main" comparison. The default triggers stay as they are, since each run spends credits;
- re-rendering changes only the managed block; `ci_render.py check` shows any difference first, flags `pull_request_target` in the triggers block
  and any key but `timeout-minutes` in the wall-bound region, and exits 1 on `ENV-MISSING` (an `agent.env` name the env block does not pass: re-render with `--sync-env`).
  A managed block that was edited by hand is not overwritten: `render` prints `REFUSED-DRIFT` with the diff, writes nothing and exits 1.
  Show the user that diff, say a re-render would undo it, and ask whether to move the edit into a user-owned region (triggers, install steps,
  `env:` block) or to overwrite it. Only on a yes to overwriting, run `render` again with `--force`. Never pass `--force` unasked;

An edit of a user-owned block (triggers, install steps, `env:`) is an ordinary file edit, so no script validates it. After it, run both
lines. `plurai ci validate` takes no argument: it reads `ci.yaml` from the folder it runs in, so run it from the agent folder (add
`--online` only when the user says the CI token is already in their shell). `ci_render.py check` reads the workflow and reports `IN-STEP`, `DRIFT`,
`ENV-MISSING`, `NOT-MANAGED`, `FORBIDDEN-TRIGGER` or `FORBIDDEN-WALL-BOUND`:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require 'ci validate')" || { [ $? -eq 3 ] && exit 1; }; [ -n "$PLURAI_BIN" ] || exit 1   # 3 = the resolver stopped or asked: follow the lines it printed. Empty = no runner found: stop — never proceed on an empty PLURAI_BIN
REPO="$(git rev-parse --show-toplevel)"
(cd "$REPO/<dir>" && "$PLURAI_BIN" ci validate)
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-ci/scripts/ci_render.py" check --repo "$REPO" --agent <dir>
```

Say what each printed. A refusal from `plurai ci validate` names its fix: say it, and do not leave the edit unchecked.

## Step 5 — print the token line and the secret lines

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
CI="${CLAUDE_PLUGIN_ROOT}/skills/simulation-ci/scripts"
REPO="$(git rev-parse --show-toplevel)"
"$PLURAI_PY" "$CI/ci_render.py" lines --repo "$REPO" --agent <dir>
```

Say what it prints, as printed: `plurai login`, then `plurai ci token --github <owner>/<repo>`, how a manual run starts (the Actions
tab "Run workflow" button, or `gh workflow run plurai-<agent>.yml --ref <branch> -f selection=smoke`) and that the workflow file
must be on the default branch first, the one line about uncommenting triggers, one `gh secret set <NAME>` per `agent.env` name (the
provider key first) and the local dry run (`plurai ci run` from the agent folder, before the push). Say plainly that the key has full access to the user's Plurai organisation and that every
run of a workflow in the repository uses it, so write access to the repository is the boundary; a team that wants a gate before the
key is used puts the job behind a GitHub Environment with required reviewers. Recommend a provider key used by CI alone with a low
spend limit. The user runs these lines; you do not.

Also tell the user: the suite changes a run makes appear in its job summary (and in a pull request's comment), and `plurai suite diff`
shows the same locally.

## Files to commit

After each edit (a fix, a render, a re-entry edit) and before a commit, list the files to commit from git, never from what you
remember writing: an id fix can add a marker nobody named, and a re-render leaves earlier edits pending.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
CI="${CLAUDE_PLUGIN_ROOT}/skills/simulation-ci/scripts"
REPO="$(git rev-parse --show-toplevel)"
"$PLURAI_PY" "$CI/ci_edit.py" pending --repo "$REPO"
```

It prints one `git status --porcelain` line per changed file under each `plurai_sim/` and in the `.github/workflows/plurai-*.yml` files (`??` untracked,
` M` modified, `R ` renamed as `old -> new`), or `PENDING none`. Name every file it prints, and give the count only as the
number of lines it printed.

## Step 6 — commit on a branch and open the pull request

Ask first. With a yes: create a branch, add the files the **Files to commit** command prints (the workflow files, each `ci.yaml`, any `agent.env`
edit, the id files and the env folder), commit, push and open the pull request. The first run is a manual run the user starts once the workflow file is on the default branch (GitHub lists a manual
workflow only from there). Until the user has run the token and secret lines, a run is not measured, and its heading says why:

```text
not measured: <NAME> empty
not measured: no secrets (PLURAI_TOKEN is not set)
```

The first line is a name declared under `agent.env` that is empty, the second a missing token. After the user has run the token and secret lines, the run measures.

## Re-entry — the setup exists

A user is often sent here by a CI run that refused the branch, so the readiness check comes first. Before the menu, say each
`fixes` line of every row and work through it as Step 2 does (for `id-copies-disagree`: ask which id to keep, write it, list
**Files to commit**), re-running Step 2's command until no `FIX` line is left or the user stops. Then show `state`'s `offers`, worded
as `state` words them, and let the user pick. Every edit in this table is a script, keeps the user's comments, runs `plurai ci validate`, and
puts the file back when it is refused (`--plurai "$PLURAI_BIN"` on each; without it the line is `NOT-VALIDATED` and the edit is unchecked). Exit 7 or 13
is the validator's own refusal: say its lines verbatim. After each edit, list **Files to commit**. An edit you make to a
user-owned block of the workflow is not in this table: validate it with the two lines in Step 4.

| Edit | Command |
| --- | --- |
| Change the smoke list | `ci_edit.py smoke --agent <dir> a,b [--catalog "$CATALOG"]` (propose a new list with Step 3 first; an empty list needs `--catalog`, and is set only when the catalog has `regression_*` scenarios, which join every run) |
| Change `trials` or `fail_on.tags` | `ci_edit.py trials --agent <dir> 5 [--catalog "$CATALOG"]` (a smoke list times trials over the default size of 12 threads prints a `NOTE` that the run costs more threads, and the edit still applies), `ci_edit.py tags --agent <dir> catastrophic,failing,regression` |
| Overrides: add, change, remove | `ci_edit.py override add\|change\|remove --agent <dir> ...`: only offered when a report exists. Ask the user which cell (scenario and policy, from the last report), and a reason; an override ignores that cell for the verdict and shows under "Ignored by ci.yaml". |
| Add a name to `agent.env` | `ci_edit.py agent-env add --agent <dir> NAME`, then `ci_edit.py github-names --agent <dir>` lists the secret and variable names GitHub still lacks (names only); print the `gh secret set` line for each `MISSING` |
| Secrets for Dependabot | Dependabot pull requests read their own secrets. While `pull_request` is commented out (`state`'s `pull_request` is false) the offer says they are only useful once it is uncommented; when it is on, offer them plainly. Print `gh secret set --app dependabot <NAME> --repo <owner>/<repo>` for the secret the workflow's `token:` line reads and for each `agent.env` name |
| Add an agent | Steps 1 to 4 for it |
| Remove an agent | `ci_edit.py remove-agent --agent <dir>`; say that its required check stays "expected" in branch protection until the user removes it there |
| Re-render a managed block | `ci_render.py check --agent <dir>` first (it shows hand edits), then `ci_render.py render --agent <dir>`; `--sync-env` adds env lines for new `agent.env` names. A hand-edited managed block stops the render (`REFUSED-DRIFT`): show the diff, ask, and re-run with `--force` only on a yes to overwriting it |
| Re-run the readiness check | Step 2 (the full check, with its `ADVISE` and `INFO` lines) |

When the suite changed (an `id-files-uncommitted` finding, or an id file in **Files to commit**), say once, as the finding words it: after the
change merges, run the workflow on the default branch or turn on `push`, so the next pull request has a "vs main" comparison.

When a CI run failed on an undeclared name (`KeyError: 'X'` in the job log), add `X` to `agent.env` with the first row of the
`agent.env` edit.

## Hard rules

- Never write workflow or `ci.yaml` text by hand; never pass a value to a script that it refused.
- Never read, echo or ask for a secret value, and never run `plurai login`, `plurai ci token` or `gh secret set`.
- Never use `pull_request_target`, never add a `paths` filter, never add a matrix: the scripts render none of them and a hand edit
  would break the security boundary or the one-check-per-agent shape.
- Never give a cost or a credit figure, and never name a model. Saying that a run spends credits is not a figure.
- Never uncomment a trigger the user did not name: manual only is the default.

<!-- BEGIN error-posture: generated from ${CLAUDE_PLUGIN_ROOT}/docs/error-posture.md; edit there -->
## Error posture — findings are evidence, internal issues are yours to handle

Backend errors, warnings, diagnostics and gate findings are internal: never show the customer
raw backend output. Read the raw error or finding yourself, always.

**Gate findings are evidence; you decide.** On `gate_findings`, a refusal naming a `gate_id`, or an
overrule outcome of `stale` or `refused` on `gate_authority.overrules`, follow
`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md` (read it the first time one appears). Never ask the
user about a finding or an overrule; never put either in a report.

**Any Plurai tool error.** On an error from a Plurai tool or a `plurai` command, run
`python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py"` once, before anything else, adding `--close <flow>` when this
command's Step 0 armed a flow window. On exit 7, 8 or 3 it explains
the error: say what it printed as the rule for that exit code does (exit 7 the version refusal below, exit 8 the credit
refusal, exit 3 item 6), and never paraphrase the tool error. On exit 0 it does not explain the error: handle the error
with the items below.

**Version refusal.** The software is refused, not the project; nothing here is yours to fix. On exit 7 of
`python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py"`, or of any plugin script that reaches Plurai (`sdk_call.py`,
`poll_job.sh`, `run_wait.sh`, `mcp_call.py`), what it printed is the refusal: stop, and say those lines verbatim and alone,
nothing before them, not even `exit 7`. Their last line is the update command, or the contact hand-off: add nothing after it. Close the flow window
this command's Step 0 armed first and silently: the check run with `--close <flow>` closed it in its own call; after
any other script run the `flow-window.sh close` line of its `NOT READY` stop. Never retry; never run `harness-setup`, ask the user to log in, reconnect or give a key. A later request in this
session gets the same lines: run the check again and say what it prints. Items 1, 2, 3 and 6 below do not apply.

**Credit refusal.** Neither a fault to fix nor a provider refusal. On a line `CREDIT-STOP: <sentence>`, a `plurai`
command that exits 13 on a credit sentence, or a credit block in a tool result or job envelope
(`terminal_reason: credit`, or `code` one of `insufficient-credit`, `new-run-refused`, `free-tier-ceiling`,
`credit-unavailable`, `credit-refused`): stop, whatever the snippet's own fallback says, and say what
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stop_message.py" refusal (--sentence "<the sentence>" | --refusal <the block's JSON>)
--continue "<the user's command>" --done "<outcome>"` prints, verbatim and alone (with `--refusal`, add
`--plurai "$PLURAI_BIN"` when you hold it). `<the user's command>` is the slash command the user typed to start this
flow (for example `/simulate`), or their request in words; never this skill's name. Exit 5 means it names a skill the
user cannot type: run again with the user's command. For `credit-unavailable` the command prints the contact hand-off with
no continue line: say it. The run stays halted until the user's next message lifts it, as for capacity.

Read `${CLAUDE_PLUGIN_ROOT}/docs/credit-refusal.md` the first time one appears, and when a tool reports a payment
refusal with no block. Say the stop once, then only what is new. When the stop has a continue line, name no other
command or skill, then or later: that line is the one way on. Never retry the refusal; never run
`harness-setup` or ask the user to log in again; never ask for a key; never name or suggest a model; never start
another job. Items 1, 2, 3 and 6 below do not apply.

**SDK install question.** When `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask` exits 3 and its stderr holds
`resolve_plurai: REFUSED` with `header:`, `question:` and `options:` lines, the SDK here is older than this plugin release
needs, or this project's environment holds none: ask the question once, exactly as printed, through the blocker gate. That call
sanctioned it, so the ask tool is your next action, with no text before it; if the lines say to run
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction blocker` first, run it. On `Install it`, run the
`--install-pin` command the lines name, unchanged, tell the user its last line, then run the command that stopped again from
its start. On `Not now`, or when nobody can answer, close the window as the version refusal rule does, then say the stop
lines that follow the question, verbatim and alone, and run nothing after them. Stop lines with no
question are said the same way. Never run `harness-setup` for it, and never offer another install.

On any other backend error or warning:

1. **Fix it yourself, silently.** Retry a transient failure (retry rule below). When the error names something
   this side can change (a malformed parameter, a stale or missing local artifact, an input the skill's error table
   names, the corpus's shape), change it and re-run the call once with the changed inputs: that fixed re-run is item 1,
   not a retry. At most one fixed re-run per failed call, never a third run. Say no sentence about the fix, the re-run,
   the wait or the re-call, before or after it: the next text the user reads is the flow's normal output. A fixed re-run
   that fails on something the user must supply is item 2; on anything else, item 3.
2. **User-actionable input problem:** say what to change in product terms ("the PRD has no
   refund-policy section"), never the backend's error text, exception or internal field names.
   If the missing input blocks the flow (a provider key unset or expired, credentials, a service
   that stopped answering), ask; never degrade silently. Sanction the ask first with
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction <gate>` (never a raw write to a
   flow marker), ask once, at discovery, in product language, offer this skill's fallback, and
   resume on the answer. A sanction allows one ask call, within 10 minutes: put every question the
   gate needs into it, and never ask twice. Where you cannot ask the user at all, run
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" unasked <gate>` and never ask in prose.
   Where the ask is a plain-text question (no ask tool), ask one question with its options and end
   the turn on it: the next prompt is the reply. Where `request_user_input_async` is the ask tool,
   send exactly `{"questions":[{"title":"<the question>","options":["<option>","<option>"]}]}`
   and no other key (a call with an unknown key is rejected, and the sanction survives it). Never
   call `request_user_input`. A run with nobody at the terminal declares its run driver first
   (`workspace.run_driver: headless`) so the gate takes its default; an undeclared run ends on the
   question and waits for nobody.
   With `workspace.run_driver: headless`, sanction and ask the same way: the guard replies with the
   gate's default or files a `run_driver_input_required` finding for you to report. Never guess.
3. **Unfixable from here** (a misconfigured backend, a failure nothing on this side can change, a fixed re-run that
   failed again, a missing server-side capability, a `security` finding, an overrule outcome of `refused`): when the
   remedy is a grant or credential only the user holds, ask as in item 2. Otherwise stop on the contact hand-off: say
   what `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stop_message.py" contact --done "<outcome>"` prints (each `--done` an
   outcome that names what exists, with its id), verbatim and alone. The contact hand-off is for typed faults; a
   transport failure with no envelope ends on the unavailable stop (retry rule). A refusal typed `code: capacity_exhausted`, a job envelope whose
   `capacity` is set (any status), an `unavailable` block or a job envelope with `terminal_reason: unavailable` instead
   takes `stop_message.py refusal --refusal <its JSON>`; never quote the provider's own limit text. A typed block wins
   over the fault rule and over `terminal_reason`: a failed job with `terminal_reason: internal_error` that holds an
   `unavailable` block is the unavailable stop, never the contact hand-off.

4. **Cost figures and backend internals stay internal.** `total_cost_usd` and every backend spend
   figure stay out of everything the customer sees, the final message included. So do the
   backend's internal ports (a mock service's loopback port), process ids and filesystem paths;
   a deployed mock's only address is its public `https://<host>/mock/<env_id>/` URL.
5. **Internal quality-loop work is process, not product.** Defects found and fixed during
   generation or qualification stay out of the report.
6. **The endpoint is the configured backend.** The backend is the one the user logged in to with the
   `harness-setup` skill; a project `.env` file is not read. If no backend tool is in your tool list, or a
   call is refused because Plurai is not logged in or not set up, run the `harness-setup` skill from the
   command the user typed (`from /<command>`), then go on from the step you were at. Never show the
   refusal's text. Never tell the user to set, export or unset an environment variable: that is for developers
   of the plugin, never for its users. Never switch to another address to get unblocked, and never send the
   token anywhere but the configured backend.

**Before a stop, say nothing about it.** Say nothing about the error or its cause before a stop (no "credit",
"capacity", "503" or "error" wording, no apology, no status line): the stop text alone says it. Its "Done so far" lines
name what exists, with its id (for example "Scenario set saved: <name>"), never the stop or the failure.

**A stop inside a /simulate run publishes the page once.** After a stop above (the credit refusal's line, the contact
hand-off, the capacity stop or the unavailable stop), run its `stop_message.py` command and say nothing yet. Then run the
end publish, silently:
`source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh" && source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh" &&
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" fill "$WORKDIR" --step end --flowdir "$FLOWDIR"`. When
`publish` is true, publish the file at `report` with the Artifact tool, passing `report` as `file_path` character for
character, with `icon: "report"` and `url` set to `page` when it is not null (never `force`, never `Read` the file),
then run the same command with `done "$WORKDIR" --step end --flowdir "$FLOWDIR" --url "<the URL, or - when the publish
failed>"` in place of `fill …`. When `publish` is false, never fill again and never run `done`. Your final message, the
only one this stop has, is what the stop command printed, verbatim, then the link line `done` printed, or the `say`
lines of a fill that did not publish, if any. Say the stop nowhere else in the turn. Never run `flow-window.sh simulate
end` for the stop itself: the run is stopped, and no job starts until the user's next message. Outside a /simulate run the fill skips and adds
nothing. A typed message after a capacity or unavailable stop lifts it. A go-on continues the stage at once, without
repeating the stop text and never waiting out the time it names; a stop runs `flow-window.sh simulate end`; a question is
answered, then wait for the user.

**Retry rule — only a transient failure is retried, and only once.** Read the failed job's
`terminal_reason` first (Job envelope, beside `status` and `error`). Transient is exactly one of:
- A transport failure with no envelope (connection reset, client timeout, a reply cut in
  transit, a bare `502` / `503` / `504`). Wait first: run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/wait.sh" 45 --quiet` as
  one foreground shell call, silently, and never narrate it. Then re-call a read (`job_status`, a fetch) or a `*_start`
  once, with the same arguments: the backend answers a repeated start with the first call's job or session. A refusal naming an already-open session or job from that
  call means the first call ran: continue with it. Never re-call any other call that writes (`interact_edit_session`,
  `end_edit_session`, `score_run`, `diagnose_mock`, `edit_scenario_gt`, …). A re-call that fails again, or a transport
  failure on a write that is never re-called, is the unavailable stop: say what
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stop_message.py" unavailable --done "<outcome>"` prints, verbatim and alone,
  never the contact hand-off. A `poll_job.sh` that exits 69 is this transport failure: run the wait above, re-invoke it
  once, and a second 69 is the same stop.
- `terminal_reason: interrupted` from a restart (not a cancel you or the user asked for), or
  `terminal_reason: watchdog` (the backend stopped a job that made no progress).
- A failure this skill's error table marks as retried once.

Everything else is final, whatever its `terminal_reason` (`refused_inputs`,
`usage_window_exhausted`, `capacity`, `unavailable`, `credit`, `cost_cap`, `internal_error`, or none),
including a provider timeout or 5xx (`504 Provider timed out`): the backend already retries those
inside the job. Final is about re-sending the same inputs; item 1's fixed re-run, with changed inputs, is not a retry.
A retried job that fails again is final, whatever its reason. Hand it off per step 3.

**Provider refusal.** `terminal_reason: refused_inputs` is an input gate (this skill's error table
says what to fix) or the provider refusing the backend's own call. Decide by the error's shape,
never by words anywhere in `error` (a flow named `credit_limit` is not a billing problem). It is a
provider refusal only when `error` contains `SDK turn failed (HTTP 4xx)` (at its start or wrapped,
as in `corpus build failed (RuntimeError: SDK turn failed (HTTP 403): …)`) with status 400 / 401 /
403 / 404 and provider text after the marker about access, permission, the model, the
account, credit or billing. Then stop on the contact text alone, with no lead-in: say what
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stop_message.py" contact --done "<outcome>"` prints, verbatim. No model name,
no retry, and never pass or suggest a `model`: the model is a backend decision. Provider text opening with
`AWS authentication failed · refresh your AWS credentials` is about the backend's credentials:
never repeat it and never ask the user to refresh anything.
<!-- END error-posture -->
