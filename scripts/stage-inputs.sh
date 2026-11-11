#!/usr/bin/env bash
# stage-inputs.sh — `git add` the plurai inputs a command just wrote, in the CUSTOMER's repo.
#
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/stage-inputs.sh"                       # everything present
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/stage-inputs.sh" .scenario_set_id      # what THIS step wrote
#
# WHY IT EXISTS. The managed .gitignore block leaves the authored inputs and the
# backend-identity markers stageable — and nothing ever staged them. `git status` collapses an
# untracked parent to a single line, so a workspace holding a full suite looked committed and
# held nothing; the loss surfaced only on a fresh clone. Un-ignoring a file is not tracking it.
#
# WHY BESIDE EACH WRITE, not once at setup. Most of these files do not exist when
# /simulation-scan-agent runs — .scenario_set_id, .eval_set_id, policies_config.yaml and the
# corpus markers are written by LATER commands. A single staging step at scan time can only
# ever stage what scan itself produced, so each command stages its own output as it lands.
#
# ARGUMENTS are paths relative to $WORKDIR. With none, the full authored set is staged and
# anything absent is skipped (it has not been generated yet). A file named EXPLICITLY and
# missing is an error: the caller just wrote it, so its absence means the write did not happen.
#
# EXIT 0 staged (or nothing yet, or NOT STAGED: see below); 1 a `git add` failed; 2 no workdir or
# no repo; 3 an explicitly named file is absent; 4 a bridge bench's required file is absent,
# ignored, or unknowable (the rest is staged first).
#
# NOT STAGED. Codex's workspace-write sandbox leaves `.git` read-only, so no `git add` can take the
# index lock there and the only way past it is an approval prompt. Under Codex, when a write probe
# beside the index fails, nothing is added: the run classifies the files as usual, prints
# `NOT STAGED` with the one `git add` command the operator runs, and exits 0 so the caller does not
# escalate. A writable index (a full-access session, or an operator who approved the write) stages
# as always, and every other harness keeps the exit 1 below: an index that cannot be written there
# is a real fault, not a sandbox.
#
# It stages; it never commits. Committing is the customer's, in their own message.
set -uo pipefail

# shellcheck source=/dev/null
. "$(dirname "${BASH_SOURCE[0]}")/resolve-workdir.sh"

if [ -z "${WORKDIR:-}" ] || [ ! -d "$WORKDIR" ]; then
  echo "stage-inputs: no plurai_sim workdir resolved — nothing staged." >&2
  exit 2
fi

# Every input a plurai command authors. Dotfiles here are the BACKEND-IDENTITY markers only —
# the ids naming ground truth that lives on the backend, which the managed block re-includes.
# Machine-local state (.run_mode, .turn_timeout, .last_run, .mock_service_url, .env_id,
# .ingest_host) is ignored by that block and is deliberately absent from this list —
# .turn_timeout describes THIS developer's agent on THIS machine, not the shared suite.
# _storage.py/_mocks.py: the split-template helpers simulate.py imports (canonical names pinned
# by verify_config.py's _SPLIT_HELPER_NAMES); a single-file simulate.py has none — absent skips.
# artifacts/data_profile.md is an INPUT, not evidence: generation is grounded on it like the PRD,
# so it is un-ignored by the managed block and staged here. The rest of artifacts/ stays local,
# EXCEPT the READER files — scenarios.md / evals.md / suite.json / agent_findings.md / fixes.md.
# Those carry the suite's own scenario text and its findings: the deliverable a human opens. A
# suite whose text lives on one machine is not a suite the customer owns. `.corpus_scope` is the
# human gloss on `.corpus_id` and travels with it — the id alone says nothing about what was
# ingested. artifacts/pre_generation_findings.md is a READER file too — the scan's own verdict on
# its conclusions — and artifacts/scan_contract.json is the DRIFT BASELINE the contract CLI's
# `recheck --against` compares to, so a clone that lacks it cannot tell whether the sources moved
# since the scan. All are un-ignored by the managed block; the two lists move together (the
# rev lives on the block's own BEGIN line — restating it here is how this comment went stale).
# NOT here: the `.artifact_*_url` markers. They address a published page on ONE claude.ai account,
# and `url` only updates an artifact its own account owns — so a committed URL is one a teammate's
# clone can never republish to, only be refused by. They stay ignored and machine-local, like
# `.mock_service_url` and `.env_id`.
# bench_manifest.json + probes/ are INPUTS in the same sense simulate.py is: they are the bench's
# typed self-description and the checks that prove it, authored once and re-run on every scan. A
# clone without them can re-verify nothing — which is the state every bench was in before they
# existed, and the reason a probe report outlived the probes that produced it. `host_probes/` holds
# the fixtures those declared probes run on, so it travels with them.
# artifacts/verification.json is a READER file: what this scan executed, reused and could not run.
# It is what `/simulation-generate-suite` Step 0 reads before it spends, and the record behind the
# hand-off's run-integrity line.
# `regressions` and `trace_scenarios` are DIRECTORIES, and the two staging targets no ignore rule
# touches: each folder under them replays one conversation the next final batch carries — a
# confirmed agent issue, or a production conversation cut before a trace failure — nothing else
# records that it is covered, and deleting a folder is how it is retired, so the folders have to
# be in history for that deletion to mean anything. A trace scenario holds a production
# conversation verbatim; that is the operator's own repository under a raw-posture env.
# `grounding_ledger.yaml` is the grounding ledger mirror (the served bytes of the latest version, written only
# by `grounding_mirror.py pull`) and `.ledger_version` the version a suite was pinned to: together they say
# which facts the committed env was built on, so a clone sees them without the backend.
# `inputs_schema.yaml` / `outputs_schema.yaml` are authored inputs of every scenario batch (inline only, written by
# /extract-scenario-variables): a clone's first batch names the committed `.scenario_set_id` as its predecessor, and
# the backend refuses a batch that drops a document its predecessor chain was built from.
DEFAULT_INPUTS="simulate.py plurai.config.yaml simulation_prd.md \
bench_manifest.json probes host_probes \
artifacts/data_profile.md artifacts/scenarios.md artifacts/evals.md artifacts/suite.json \
artifacts/agent_findings.md artifacts/fixes.md \
artifacts/pre_generation_findings.md artifacts/scan_contract.json artifacts/verification.json regressions \
trace_scenarios \
scenario_variables.yaml scenario_state_schema.yaml inputs_schema.yaml outputs_schema.yaml eval_intent.yaml policies_config.yaml \
test_mock_contract.py _storage.py _mocks.py .scenario_set_id .eval_set_id .corpus_id .corpus_ids \
.corpus_scope .knowledge_bases .doc_examples_set_id .scenario_bundle_id .document_fields .workspace_id \
grounding_ledger.yaml .ledger_version"

if [ "$#" -gt 0 ]; then
  REQUESTED="$*"
  EXPLICIT=1
else
  REQUESTED="$DEFAULT_INPUTS"
  EXPLICIT=0
fi

# A repo is a precondition, not a detail to swallow: a customer working outside git gets one
# clear line instead of a silent no-op that reads as success.
if ! REPO="$(git -C "$WORKDIR" rev-parse --show-toplevel 2>/dev/null)"; then
  echo "stage-inputs: $WORKDIR is not inside a git repository — the plurai inputs are NOT tracked." >&2
  echo "stage-inputs: run 'git init' (or move the agent into its repo) and re-run this, or commit them by hand." >&2
  exit 2
fi

# Whether a file can be created beside the index, which is where git puts `index.lock`. A write
# attempt, because a read-only `.git` is enforced by the sandbox and not by the file's mode bits, so
# `test -w` answers yes. An index path git cannot name is left to `git add` to report.
_index_writable() {
  local index probe
  index="$(git -C "$REPO" rev-parse --git-path index 2>/dev/null)" || return 0
  case "$index" in /*) ;; *) index="$REPO/$index" ;; esac
  probe="$(mktemp "$(dirname "$index")/plurai-probe.XXXXXX" 2>/dev/null)" || return 1
  rm -f "$probe"
}

INDEX_READ_ONLY=0
if [ "${PLURAI_HARNESS:-}" = codex ] && ! _index_writable; then
  INDEX_READ_ONLY=1
fi

# The only place this script writes the index. Read-only, nothing is added: the files are still
# classified, and the report names the command that stages them.
_add() {
  [ "$INDEX_READ_ONLY" = 1 ] && return 0
  git -C "$REPO" add -- "$1"
}

STAGED=""
MISSING=""
# The three GROUPS this run actually produced. "staged" used to be one undifferentiated list on
# every run, so the line read identically whether the command had just written a whole suite or
# re-added twenty untouched files — and it told the customer to "commit them" either way. The
# classification is read BEFORE `git add`, because adding is what erases the difference.
NEW=""
RESTAGED=""
UNCHANGED=""

# NEW      — not tracked at all yet (`git ls-files --error-unmatch` fails).
# RESTAGED — tracked AND different from HEAD, or a tracked directory holding new files.
# UNCHANGED— tracked and identical to HEAD; `git add` is a no-op on it.
# `git diff HEAD` does not see untracked files, so a new folder inside a tracked directory
# (`regressions`, `trace_scenarios`) is read off `git ls-files -o` instead.
_classify() {
  # $1 = absolute path, $2 = the label to record
  if ! git -C "$REPO" ls-files --error-unmatch -- "$1" >/dev/null 2>&1; then
    NEW="$NEW $2"
  elif [ -n "$(git -C "$REPO" ls-files -o --exclude-standard -- "$1" 2>/dev/null)" ]; then
    RESTAGED="$RESTAGED $2"
  elif git -C "$REPO" diff --quiet HEAD -- "$1" 2>/dev/null; then
    UNCHANGED="$UNCHANGED $2"
  else
    RESTAGED="$RESTAGED $2"
  fi
}

for f in $REQUESTED; do
  if [ -e "$WORKDIR/$f" ]; then
    _classify "$WORKDIR/$f" "$f"
    if _add "$WORKDIR/$f"; then
      STAGED="$STAGED $f"
    else
      echo "stage-inputs: git add failed for $WORKDIR/$f" >&2
      exit 1
    fi
  else
    MISSING="$MISSING $f"
  fi
done

# Every OTHER top-level `*.py` is harness too: conftest.py and test_agent_envelope.py (the contract
# tier), _skill_tools.py, and any helper module simulate.py imports — without which a clone cannot
# even import simulate.py. Globbed, not listed, so a helper written for one agent lands with no edit
# here. Default mode only: an explicit call stages exactly what it names.
if [ "$EXPLICIT" = 0 ]; then
  for py in "$WORKDIR"/*.py; do
    [ -e "$py" ] || continue
    rel="${py#"$WORKDIR"/}"
    case " $DEFAULT_INPUTS " in *" $rel "*) continue ;; esac
    # one the customer's own .gitignore excludes is theirs to keep out — skipped, never a failed run
    git -C "$REPO" check-ignore -q -- "$py" && continue
    _classify "$py" "$rel"
    _add "$py" || exit 1
    STAGED="$STAGED $rel"
  done
fi

# A bridge bench's harness is also its Node child: the seam map, the entry, the replacements, the host
# `simulate.py` loads, and the runtime copied beside them. The list is the one `harness_fingerprint.py`
# hashes (`--bridge-files`: the map's entry.path, every replace and impl, and constants.json
# paths.runtime_files), staged path by path and never as `bridge/`, so the installed
# `bridge/runtime/node_modules/` and a host's `__pycache__` stay out whatever the customer's .gitignore
# says. `bridge/replace/` is staged whole besides: a replacement may import a helper beside it.
# The map is what makes a bench a bridge bench (`harness_fingerprint.detect_lang`'s rule).
# A REQUIRED file that is absent, or that the customer's .gitignore excludes, is a bench a clone cannot
# run: each is named, the rest is still staged, and the run exits 4. Default mode only.
BRIDGE_MISSING=""
BRIDGE_IGNORED=""
BRIDGE_ERROR=""
if [ "$EXPLICIT" = 0 ] && [ -f "$WORKDIR/bridge/bridge.map.json" ]; then
  BRIDGE_REQUIRED=""
  if ! command -v python3 >/dev/null 2>&1; then
    BRIDGE_ERROR="no python3 on PATH to read bridge/bridge.map.json, so the bridge files were not staged"
  else
    _err="$(mktemp "${TMPDIR:-/tmp}/stage-inputs.XXXXXX")"
    if ! BRIDGE_REQUIRED="$(python3 "$(dirname "${BASH_SOURCE[0]}")/harness_fingerprint.py" --workdir "$WORKDIR" --bridge-files 2>"$_err")"; then
      BRIDGE_ERROR="$(cat "$_err")"
      [ -n "$BRIDGE_ERROR" ] || BRIDGE_ERROR="harness_fingerprint.py --bridge-files failed, so the bridge files it names are unknown"
    fi
    rm -f "$_err"
  fi
  for rel in $BRIDGE_REQUIRED; do
    path="$WORKDIR/$rel"
    if [ ! -e "$path" ]; then
      BRIDGE_MISSING="$BRIDGE_MISSING $rel"
      continue
    fi
    if git -C "$REPO" check-ignore -q -- "$path"; then
      BRIDGE_IGNORED="$BRIDGE_IGNORED $rel"
      continue
    fi
    _classify "$path" "$rel"
    _add "$path" || exit 1
    STAGED="$STAGED $rel"
  done
  if [ -d "$WORKDIR/bridge/replace" ] && ! git -C "$REPO" check-ignore -q -- "$WORKDIR/bridge/replace"; then
    if [ -n "$(git -C "$REPO" ls-files -o --exclude-standard -m -- "$WORKDIR/bridge/replace" 2>/dev/null)" ]; then
      _classify "$WORKDIR/bridge/replace" "bridge/replace"
      _add "$WORKDIR/bridge/replace" || exit 1
      STAGED="$STAGED bridge/replace"
    fi
  fi
fi

# The env manifests travel with the artifacts they pin. Globbed, so an absent envs/ is simply
# no match — never an error, and never staged as a literal pattern.
for m in "$WORKDIR"/envs/*/plurai.env.yaml; do
  [ -e "$m" ] || continue
  _classify "$m" "${m#"$WORKDIR"/}"
  _add "$m" || exit 1
  STAGED="$STAGED ${m#"$WORKDIR"/}"
done

if [ "$EXPLICIT" = 1 ] && [ -n "$MISSING" ]; then
  echo "stage-inputs: asked to stage$MISSING, which does not exist under $WORKDIR — the write did not land." >&2
  exit 3
fi

# The bridge bench's required files, named AFTER everything else is staged, so one gap never costs
# the rest of the bench its staging.
_bridge_report() {
  [ -n "$BRIDGE_MISSING$BRIDGE_IGNORED$BRIDGE_ERROR" ] || return 0
  [ -n "$BRIDGE_ERROR" ] && echo "stage-inputs: ERROR — $BRIDGE_ERROR" >&2
  [ -n "$BRIDGE_MISSING" ] && echo "stage-inputs: MISSING — the bridge bench requires$BRIDGE_MISSING, which does not exist under $WORKDIR; re-run the bridge setup step that writes or copies it (B2 map, B3 entry, B4 runtime and host)." >&2
  [ -n "$BRIDGE_IGNORED" ] && echo "stage-inputs: ERROR — the bridge bench requires$BRIDGE_IGNORED, which the repo's .gitignore excludes, so a clone cannot run the bench; un-ignore it (or 'git add -f' it) and re-run." >&2
  exit 4
}

if [ -z "$STAGED" ]; then
  echo "stage-inputs: nothing to stage yet under $WORKDIR."
  _bridge_report
  exit 0
fi
# A read-only index with something to add is reported as that, not as staging. With nothing to
# add (every file already committed and identical) there is nothing to report but the no-op.
if [ "$INDEX_READ_ONLY" = 1 ] && { [ -n "$NEW" ] || [ -n "$RESTAGED" ]; }; then
  echo "stage-inputs: NOT STAGED — the git index of $REPO is read-only in this sandbox, so nothing was added and these files are NOT tracked."
  [ -n "$NEW" ] && echo "stage-inputs:   NEW —$NEW"
  [ -n "$RESTAGED" ] && echo "stage-inputs:   RESTAGED (changed since HEAD) —$RESTAGED"
  [ -n "$UNCHANGED" ] && echo "stage-inputs:   UNCHANGED (already committed, identical) —$UNCHANGED"
  # %q: a path with a space survives being pasted. Relative to $WORKDIR, which `git -C` names.
  printf 'stage-inputs: the operator stages them with:\n  git -C %q add --' "$WORKDIR"
  for f in $NEW $RESTAGED; do printf ' %q' "$f"; done
  printf '\nstage-inputs: do not ask for broader access to stage them; say in the summary that they are untracked and give this command.\n'
else
  echo "stage-inputs: staged in $REPO —$STAGED"
  # Only the NON-EMPTY groups, so a no-op run reads as a no-op.
  [ -n "$NEW" ] && echo "stage-inputs:   NEW —$NEW"
  [ -n "$RESTAGED" ] && echo "stage-inputs:   RESTAGED (changed since HEAD) —$RESTAGED"
  [ -n "$UNCHANGED" ] && echo "stage-inputs:   UNCHANGED (already committed, identical) —$UNCHANGED"
  # …and the call to action ONLY when there is something to commit. Printing it over a run that
  # re-added twenty untouched files is how "commit them" became noise the customer scrolls past.
  if [ -n "$NEW" ] || [ -n "$RESTAGED" ]; then
    echo "stage-inputs: these are now TRACKED; commit them with your own changes (nothing was committed here)."
  fi
fi
# Trace scenarios are production conversations, verbatim: the operator hears that before the
# commit the line above invites. This is the one copy of the privacy line; /trace-to-scenarios
# and /analyze-traces relay what it prints.
case " $NEW $RESTAGED " in
  *" trace_scenarios"*)
    echo "stage-inputs: Privacy: \`trace_scenarios/\` holds production conversations verbatim, and git tracks it. Review each folder before you commit; retire one with \`trace_scenarios.py retire --slug <slug>\`, which runs \`git rm -r\` (a plain \`rm\` leaves it in the index). Git history keeps every committed copy."
    ;;
esac
_bridge_report
