#!/usr/bin/env bash
# scan_contract.sh — execute the scan's own conclusions against the corpus, before generation spends.
#
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/scan_contract.sh" --workdir "$WORKDIR" \
#        --prd "$WORKDIR/simulation_prd.md" --source-root "$PWD" --corpus <abs-path> [--corpus <abs-path> ...] \
#        [--suffixes <ext> ...] [--service-indicators <yaml>]
#
# --suffixes WIDENS which files the checker walks under each --corpus root past its default set
# (.md/.txt/.yaml/.json/.rst). REPEATABLE and ADDITIVE — one flag per extension, `html` and
# `.html` both accepted — and forwarded verbatim; this script has no opinion about the value.
# --service-indicators names a YAML of in-house vendor/service names the checker would otherwise
# not know. Both are FORWARDED, and a checker that predates either REFUSES rather than dropping
# it: they change WHICH bytes are hashed, so ignoring one would verify a different corpus and
# report that as the caller's result.
#
# THE SUFFIXES ARE ALSO RECORDED, at `$WORKDIR/.scan_corpus_suffixes`, beside the roots and for
# the same reason. The later `plurai-run-simulation --scan-contract` RE-hashes this baseline, and
# a baseline recorded WIDE and re-hashed NARROW reports every widened file as `removed` — a drift
# verdict the sources never earned. The recheck takes the same flags; give it the same values.
#
# It projects the PRD's typed facts (scan_facts.py) and runs `<checker> check` — `plurai-scan-contract`,
# the console script the SDK installs — which derives the corpus's URL allowlist (placeholder TEMPLATES
# kept whole, not truncated), hashes the sources, verifies those facts, RESOLVES each fact's `source:`
# citation against the trees given as --source-root, cross-references the scan's own outputs (agent_context governance, the typed
# complication pool against the families the PRD's design directives demand), and writes ONE
# pre-generation findings report carrying a PROCEED / FIX-FIRST signal.
#
# --source-root is EXPLICIT and never defaulted to $PWD: a citation check pointed at the wrong tree
# files a blocking finding against every fact. Without it that check reports itself BLOCKED, which
# the report's headline names — the citations are unchecked, not clean. $WORKDIR is added ALONGSIDE
# whatever the caller passes, and only then: two of the four documented `source:` forms
# (`artifacts/*`, `plurai.config.yaml`) live under the workdir this script was handed, so a run
# given only the agent module reports a correctly-authored PRD as citing evidence that is not there.
#
# A CLI AND NOT AN MCP TOOL. The checks need the SOURCE FILES. A backend that only ever receives
# an uploaded chunk archive can hash what it was sent, never what the files are now — so the
# drift re-check is only meaningful where the sources live, which is this machine.
#
# ONE ENTRY POINT, deliberately. The projection used to be a separate command the skill chained
# to this one, which put a failure between the two steps where NOTHING owned the artifacts: the
# projector refused, this never ran, and the previous scan's verdict stayed on disk for the
# suite's gate to read as a pass. Running the projector from here means every way this can fail
# reaches the sweep below.
#
# WHAT THIS SCRIPT ADDS over invoking the CLI directly:
#   * resolution — WHICH checker, said out loud, with a distinct NOT RUN outcome when there is none;
#     and WHICH python3 the fact projector runs under (`resolve-python.sh`), because a system
#     interpreter with no PyYAML would otherwise turn a projectable PRD into a NOT-VERIFIED exit;
#   * the workdir conventions — `--out $WORKDIR/artifacts`,
#     `--against` the previous run's own artifact, moved aside first; and `--corpus-id` from the
#     workdir's corpus coordinate (`corpus_coordinate.py` — all three marker shapes, not `.corpus_id` alone);
#   * the stale-verdict sweep below, and the pre-exec clear that gives it teeth.
#
# RESOLUTION ORDER (first hit wins, and it is printed). Each tier is searched for `plurai-scan-contract`:
#   1. $PLURAI_SCAN_CONTRACT_BIN              — an explicit override
#   2. $VIRTUAL_ENV/bin                   — the venv the operator activated
#   3. ./.venv/bin, then other ./.venv*|./venv* — repo-root venvs, sorted
#   4. PATH
# On every platform each venv's `bin` is followed by its `Scripts` (a Windows venv's layout), where the
# scripts are `<name>.exe` — the order bench_manifest.scan_contract_checker probes.
#
# EXIT CODES. This script's own table, which `bench_manifest.py` and the skills read. It is not the
# checker's: `plurai-scan-contract` returns the SDK's (`plurai.exit_codes`, names in sdk-exit-codes.sh) and this
# script maps it once, at the end: OK -> 0, SCAN_HELD -> 1, INTERNAL_FAULT -> 5, and every other status
# (USAGE for a malformed input document, REFUSED for an unsupported platform) -> 2, an input to fix.
#   0  PROCEED   — no holding finding is left un-overruled
#   1  FIX FIRST — a VERDICT: at least one holding finding is not overruled (`report.gate_findings`
#      in $WORKDIR/artifacts/scan_contract.json; an older checker files blocking report items
#      instead). Never NOT VERIFIED
#   2  NOT VERIFIED — the facts block cannot be projected (on a checker that predates --overrule,
#      also one malformed or repeated fact), the corpus is unreadable, the CLI
#      could not complete, or the previous run's verdict could not be cleared. An input to fix,
#      never a verdict. When an artifact SURVIVED the sweep, `$WORKDIR/.scan_contract_stale`
#      records it, because exiting 2 warns this caller and nothing else — the suite's Step-0 gate
#      opens the workdir in a later session and reads the file regardless
#   3  NOT RUN   — the checker is not on this machine. Not a pass: nothing was verified
#   5  NOT RUN   — the checker itself FAILED (INTERNAL_FAULT: it crashed; it asserted nothing). Also not
#      a pass, and deliberately NOT folded into FIX FIRST: "the scan found something false" and "the
#      checker died" send an operator to opposite places, and the second one has no findings to
#      read
#  64  NO VERDICT, and it carries TWO facts that send an operator to OPPOSITE places. The
#      MESSAGE says which, so read it before acting:
#      (a) THE INVOCATION — a usage error in this script's own arguments. It prints a
#          `usage: scan_contract.sh …` line. Fix the invocation and re-run.
#      (b) THE CHECKER — the invocation was fine and the resolved `$BIN` PREDATES a flag this run
#          requires. It prints `predates the flag (its \`check\` does not accept --<flag>)` and no
#          usage line. Four flags reach here, and two of them are NOT the caller's: `--probe-consent`
#          and `--agent-command` come from the workdir's own `.live_probe_consent` /
#          `.agent_command` markers, so a FLAWLESS command hits this refusal. `--suffixes` and
#          `--service-indicators` are the caller's. Re-running the same command changes nothing:
#          upgrade the SDK, or WITHDRAW what this checker cannot honour — set `.live_probe_consent`
#          to `declined`, remove `.agent_command`, or drop the flag you passed.
#      BOTH sweep the artifacts, like every other non-verdict below, and the sweep line on stderr
#      names the files it removed.
#
# NO VERDICT ON DISK IS ROUTED BY THE RECORD, NOT BY ITS ABSENCE. `bench_manifest.py verify` files
# exit 2 as `not_verified` (the scan's own inputs were refused — fix and re-run) and 3 / 5 / 64 as
# `not_run`; `/simulation-generate-suite` Step 0 reads that through `bench_manifest.py
# contract-gate`, which STOPS and asks on the first and carries a NOT-RUN line for the second.
#
# THE VERDICT IS BOUND TO THE PRD IT PROJECTED. `scan_contract.json` gains `prd_sha256`, the digest
# of the `--prd` bytes read before projection. The source hash covers the corpus only, so a PRD
# edited after the scan (the corpus step's reconciliation) was read through a contract projected
# from the document it replaced; the gate reads a digest that no longer matches as STALE.
#
# ANY OUTCOME THAT IS NOT A VERDICT SWEEPS BOTH ARTIFACTS. `scan_contract.json` is not only the
# drift baseline — `/simulation-generate-suite` Step 0 reads `report.proceed` out of the same
# file. Those two roles have opposite freshness contracts: a hash of the sources as they were is
# still true, while a PROCEED from a run that did not happen is the "checked and clean vs never
# checked" collapse this whole contract exists to remove. The verdict wins, so both go, and the
# next run simply records a fresh baseline and reports no drift. Losing a drift comparison is a
# visible degrade; keeping a stale pass is a silent one.
#
# THE ARTIFACT NAMES ARE ALSO CLEARED BEFORE THE CLI RUNS. A post-run check can only ask whether
# a file EXISTS, and with the previous run's files still in place that question cannot tell "this
# run wrote a verdict" from "an older run did" — so a CLI that exits 0 or 1 without reaching its
# writes hands the operator, and the suite's gate, the LAST scan's conclusions. The drift half is
# kept by moving the baseline aside to a name the CLI does not write, and passing THAT to
# `--against`.

set -u

# Everything this script runs is resolved from the working directory (the checker under ./.venv*,
# the interpreter under resolve-python.sh) and works on local files, so none of it is given the
# backend bearer.
unset PLURAI_TOKEN

WORKDIR=""
PRD=""
CORPORA=()
# Trees each fact's `source:` citation is resolved against, and the finding that stands in for a
# state schema. Both EXPLICIT: defaulting the roots to $PWD would file a fix-first citation
# finding against every fact whenever the caller ran from somewhere else, and an unstated
# stateless claim is exactly the "checked and clean vs never looked" collapse this script exists
# to remove. Unset, each check reports itself BLOCKED rather than passing.
SOURCE_ROOTS=()
STATELESS=""
# Extra file suffixes the checker walks under each --corpus root, and the in-house service names
# it would not otherwise know. FORWARDED, never interpreted: both are the CLI's own vocabulary and
# this script has no business having an opinion about the values. Unset means the checker's
# defaults, which is what every caller wants until it is not.
SUFFIXES=()
SERVICE_INDICATORS=""

# The artifact names this script owns. Derived once the workdir is known, and derived AGAIN by
# `_usage`, which can refuse before the parse completes and still has to sweep. One definition, so
# a rename cannot leave the refusal path sweeping a name nothing writes.
_paths() {
  OUT="$WORKDIR/artifacts"
  REPORT="$OUT/pre_generation_findings.md"
  BASELINE="$OUT/scan_contract.json"
  FACTS="$OUT/scan_facts.yaml"
  # The checker's INTERNAL gate record (overrule reasons included). The SDK writes it under the
  # workdir's private, self-ignoring `.plurai/` — never in $OUT beside the committed contract — and
  # it describes the run that wrote `scan_contract.json`, so it is cleared and swept with that file.
  GATE_AUTHORITY="$WORKDIR/.plurai/gate_authority/scan-contract-check.json"
  # Where an older SDK wrote that record. Nothing writes it now; it is only ever deleted, so a
  # leftover never lingers beside the committed contract.
  LEGACY_GATE_AUTHORITY="$OUT/gate_authority.json"
  # The plugin's overrule ledger (`gate_ledger.py`). Read, never written, here.
  LEDGER="$OUT/.gate_overrules.jsonl"
}

# Removes the VERDICT artifacts. Called from every path that ends without a verdict, so no outcome
# can leave a report or a `proceed` flag describing a check that did not complete — whether the
# file is this run's half-written output or the previous run's.
#
# It reports what it REMOVED, never what it attempted. `rm -f` returns 0 for an absent file and
# non-zero for one it cannot unlink (a root-written artifacts dir from an earlier container run, a
# read-only mount), and the skill's own text tells the model to trust this line — so a claim made
# on attempt would be a false statement about the one file the suite's gate then reads.
# Returns non-zero when any artifact SURVIVED.
_sweep_verdict() {
  _swept=""
  _stuck=""
  for _f in "$REPORT" "$BASELINE" "$FACTS" "$GATE_AUTHORITY" "$LEGACY_GATE_AUTHORITY"; do
    [ -f "$_f" ] || continue
    if rm -f "$_f" 2>/dev/null && [ ! -f "$_f" ]; then
      _swept="$_swept ${_f#"$WORKDIR"/}"
    else
      _stuck="$_stuck ${_f#"$WORKDIR"/}"
    fi
  done
  [ -z "$_swept" ] || echo "scan-contract:   removed the scan artifacts under $WORKDIR ($_swept ) — a verdict about a check that did not run reads as coverage, and the suite's gate reads that file." >&2
  [ -z "$_stuck" ] || echo "scan-contract:   COULD NOT REMOVE ($_stuck ) under $WORKDIR — the PREVIOUS run's verdict is still on disk and \`/simulation-generate-suite\` Step 0 reads that file. Make the directory writable and delete those files by hand before building a suite." >&2
  # The QUARANTINE marker, and it is what actually protects the suite's gate. Exiting 2 tells THIS
  # caller nothing was verified; it does nothing for a later session that opens the workdir and
  # reads a `proceed` nobody could clear. The marker goes beside the workdir rather than under
  # $OUT — $OUT is the directory that could not be written — and Step 0 treats an artifact
  # standing beside it as belonging to a run that did not produce it.
  [ -z "$_stuck" ] || printf '%s\n' "the scan at $(date -u '+%Y-%m-%dT%H:%M:%SZ') could not remove$_stuck from $WORKDIR; that artifact is NOT this scan's verdict" > "$WORKDIR/.scan_contract_stale" 2>/dev/null || true
  [ -z "$_stuck" ]
}

# The argument parser's own refusal, and it runs BEFORE the workdir is necessarily known — so it
# cannot call `_fail`, which sweeps. It sweeps for itself once the workdir HAS been parsed:
# `--workdir X --bogus` is a run that ends with no verdict exactly like every other, and leaving
# the previous one behind for the suite's gate is the same collapse whichever refusal got there.
_usage() {
  echo "usage: scan_contract.sh --workdir <dir> --corpus <path> [--corpus <path> ...] [--prd <file>] [--source-root <dir> ...] [--stateless-finding <text>] [--suffixes <ext> ...] [--service-indicators <yaml>]${1:+  ($1)}" >&2
  if [ -n "${WORKDIR:-}" ]; then
    _paths
    _sweep_verdict || exit 2
  fi
  exit 64
}

while [ $# -gt 0 ]; do
  case "$1" in
    --workdir) WORKDIR="${2:-}"; [ -n "$WORKDIR" ] || _usage "--workdir needs a path"; shift 2 ;;
    --corpus)  [ -n "${2:-}" ] || _usage "--corpus needs a path"; CORPORA+=("$2"); shift 2 ;;
    --prd)     PRD="${2:-}"; [ -n "$PRD" ] || _usage "--prd needs a path"; shift 2 ;;
    --source-root) [ -n "${2:-}" ] || _usage "--source-root needs a path"; SOURCE_ROOTS+=("$2"); shift 2 ;;
    --stateless-finding) STATELESS="${2:-}"; [ -n "$STATELESS" ] || _usage "--stateless-finding needs text"; shift 2 ;;
    --suffixes) [ -n "${2:-}" ] || _usage "--suffixes needs a value"; SUFFIXES+=("$2"); shift 2 ;;
    --service-indicators) SERVICE_INDICATORS="${2:-}"; [ -n "$SERVICE_INDICATORS" ] || _usage "--service-indicators needs a path"; shift 2 ;;
    *) _usage "unknown argument: $1" ;;
  esac
done

[ -n "$WORKDIR" ] || _usage "--workdir is required"

_paths
_sc_dirname() {  # dirname(1) by parameter expansion: no PATH lookup decides where this script's files are
  _plurai_dn=$1
  if [ -x /usr/bin/cygpath ]; then  # native Windows: a backslash separates too, as it does for MSYS dirname
    while :; do case $_plurai_dn in *\\*) _plurai_dn="${_plurai_dn%%\\*}/${_plurai_dn#*\\}" ;; *) break ;; esac; done
  fi
  case $_plurai_dn in
    '') printf '.'; return 0 ;;
    *[!/]*) ;;
    *) printf '/'; return 0 ;;
  esac
  while :; do case $_plurai_dn in */) _plurai_dn=${_plurai_dn%/} ;; *) break ;; esac; done
  case $_plurai_dn in */*) ;; *) printf '.'; return 0 ;; esac
  _plurai_dn=${_plurai_dn%/*}
  while :; do case $_plurai_dn in */) _plurai_dn=${_plurai_dn%/} ;; *) break ;; esac; done
  printf '%s' "${_plurai_dn:-/}"
}
_HERE="$(CDPATH='' cd -- "$(_sc_dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=sdk-exit-codes.sh
. "$_HERE/sdk-exit-codes.sh"
PROJECTOR="$_HERE/../skills/simulation-setup/scripts/scan_facts.py"

# A surviving artifact OUTRANKS the caller's own exit code: 2 is "an input to fix, never a
# verdict", which is exactly the state a stale `proceed` nobody could clear leaves behind. A usage
# error reported as 64 would send the operator to fix the invocation while the gate still reads a
# verdict from a check that did not run.
#
# The message goes LAST, after the sweep's own lines: `bench_manifest.py` records a check's final
# stderr line as its evidence, and the suite's Step-0 gate quotes that line — the reason, not the
# list of files the sweep removed.
_fail() {  # message, exit code
  _sweep_verdict; _sweep_rc=$?
  echo "scan-contract: $1" >&2
  [ "$_sweep_rc" -eq 0 ] || exit 2
  exit "$2"
}

# Through `_fail`, not `_usage`: by here the workdir is known, so the previous scan's verdict is
# reachable and a run that ends without one must not leave it behind.
[ "${#CORPORA[@]}" -gt 0 ] || _fail "at least one --corpus is required; NOTHING was verified." 64
for _root in "${CORPORA[@]}"; do
  [ -e "$_root" ] || _fail "--corpus $_root does not exist, so the corpus this scan reasoned about cannot be read. NOTHING was verified." 2
done
[ -z "$PRD" ] || [ -f "$PRD" ] || _fail "--prd $PRD does not exist, so the scan's typed facts cannot be projected. NOTHING was verified." 2
for _root in ${SOURCE_ROOTS[@]+"${SOURCE_ROOTS[@]}"}; do
  [ -d "$_root" ] || _fail "--source-root $_root is not a directory, so the tree the facts cite cannot be read. NOTHING was verified." 2
done

# --- resolve ---------------------------------------------------------------------------------
BIN=""
#: The console script that implements this contract. It ships with the SDK the run already depends on.
_NAME="plurai-scan-contract"
# Adopts its argument as the resolved binary when that path is an executable FILE, and REPORTS
# whether it did — every tier below reads one or the other half. `-f` as well as `-x`: a
# DIRECTORY is executable, and one named here would pass the tier and fail at exec instead.
_probe() { [ -f "$1" ] && [ -x "$1" ] && BIN="$1"; }
# One TIER: a directory that may hold the script. The tier order is the point — a repo venv holding
# the script beats a global install, so the search never reaches past the operator's own environment
# for a binary somewhere on PATH.
_tier() {
  [ -n "$1" ] || return 1
  _probe "$1/$_NAME"
}
# The Windows detector, identical to `_plurai_on_windows` in trusted-python.sh: by absolute path, so a `cygpath` on
# PATH cannot switch a Mac or Linux host onto the Windows spelling.
_sc_on_windows() {
  [ -x /usr/bin/cygpath ]
}
_SC_WIN=0
_sc_on_windows && _SC_WIN=1
# On Windows, a path spelled `C:\...` (an activated venv's VIRTUAL_ENV, an override) as bash spells it; elsewhere as given.
_sc_posix() {
  case "$_SC_WIN:$1" in
    1:*\\* | 1:?:*) /usr/bin/cygpath -u -- "$1" 2>/dev/null || printf '%s' "$1" ;;
    *) printf '%s' "$1" ;;
  esac
}
# A venv's `Scripts` tier (a Windows venv's layout), probed after its `bin` on every platform: the same name as
# `<name>.exe`. `bench_manifest.scan_contract_checker` mirrors this order.
_scripts_tier() {
  [ -n "$1" ] || return 1
  _probe "$1/$_NAME.exe"
}
# An override that does not resolve is a usage error and never a fallthrough: continuing down the
# tiers would run a DIFFERENT install than the one the operator named.
_OVERRIDE="${PLURAI_SCAN_CONTRACT_BIN:-}"
[ -z "$_OVERRIDE" ] || _probe "$_OVERRIDE" \
  || _fail "PLURAI_SCAN_CONTRACT_BIN=$_OVERRIDE is not an executable file, so the install the operator named cannot run. NOTHING was verified." 64
# Guarded on VIRTUAL_ENV being SET, not just on the joined path existing: with it unset the
# join is the absolute `/bin/<name>`, which is a real system path this would then adopt as
# "the venv the operator activated".
[ -n "$BIN" ] || [ -z "${VIRTUAL_ENV:-}" ] || _tier "$VIRTUAL_ENV/bin"
[ -n "$BIN" ] || [ -z "${VIRTUAL_ENV:-}" ] || _scripts_tier "$(_sc_posix "$VIRTUAL_ENV")/Scripts"
[ -n "$BIN" ] || _tier "$PWD/.venv/bin"
[ -n "$BIN" ] || _scripts_tier "$PWD/.venv/Scripts"
if [ -z "$BIN" ]; then
  for _venv in "$PWD"/.venv* "$PWD"/venv*; do
    _tier "$_venv/bin" && break
    _scripts_tier "$_venv/Scripts" && break
  done
fi
[ -n "$BIN" ] || BIN="$(command -v "$_NAME" 2>/dev/null || true)"

if [ -z "$BIN" ]; then
  echo "scan-contract: NOT RUN — \`plurai-scan-contract\` (ships with the SDK) is not on this machine, so NOTHING about this scan was verified." >&2
  echo "scan-contract:   The typed facts, the URL allowlist and the corpus hash are all unchecked; generation proceeds on unverified premises." >&2
  echo "scan-contract:   Install/upgrade the SDK in this repo's venv — or point \$PLURAI_SCAN_CONTRACT_BIN at the script — and re-run the scan to get the check back." >&2
  _sweep_verdict
  exit 3
fi
# A Windows-spelled tier (an activated venv's `VIRTUAL_ENV` is `C:\...`) is spelled as bash spells paths, as every
# other tier already is: one form for this script and the hint below.
BIN="$(_sc_posix "$BIN")"
echo "scan-contract: using $BIN" >&2

# The interpreter for this script's own python helpers. Resolved rather than assumed: the
# projector imports PyYAML, which a system `python3` does not carry, and a projector that cannot
# start is reported below as NOT VERIFIED — a resolution failure wearing a verdict's clothes.
# Hinted with the resolved CLI's own bin dir, so the projector runs under the install whose
# contract is about to read its output.
PLURAI_PY_HINT="$(_sc_dirname "$BIN")"
. "$_HERE/resolve-python.sh"
unset PLURAI_PY_HINT

mkdir -p "$OUT" || _fail "cannot create $OUT, so this run can write no verdict. NOTHING was verified." 2

# The PRD's digest, taken BEFORE the projection reads it — so an edit landing mid-run makes the
# recorded digest disagree with the file, which is the direction that re-projects.
PRD_SHA256=""
if [ -n "$PRD" ]; then
  PRD_SHA256="$("$PLURAI_PY" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$PRD" 2>/dev/null)" \
    || _fail "--prd $PRD could not be hashed, so no verdict from this run could be bound to it. NOTHING was verified." 2
fi

# --- what this checker understands -------------------------------------------------------------
# The two repos ship separately and no CI compares them, so this seam has to defend itself.
#
# A checker that predates `kind: asserted` reads it as an UNKNOWN kind, reports that as
# unresolvable, and its report files unresolvable as FIX-FIRST — so a new plugin against an older
# backend hard-stops a PRD that is fine, with one blocking finding per check-less fact, and the
# operator is told their scan refuted its own conclusions. A missing signal is a worse report; a
# manufactured blocking finding is a WRONG one, and only the second stops a build.
#
# Probed on `--source-root`, which landed in the same change as the kind. Not a proxy chosen for
# convenience: the flags and the kind are one release, and asking the checker what it accepts is
# the only question it can answer about itself without running a scan.
#
# ONE probe for every flag question below. Shelling out per flag asks the checker the same
# question three times, and the answers could disagree if the binary moved between them.
_CHECK_HELP="$("$BIN" check --help 2>/dev/null || true)"
ASSERTED_ARGS=()
if ! printf '%s' "$_CHECK_HELP" | grep -q -- '--source-root'; then
  ASSERTED_ARGS=(--no-asserted)
  echo "scan-contract: $BIN predates the asserted-fact kind (its \`check\` does not accept --source-root), so facts declaring no \`check:\` are DROPPED rather than carried. They are still NAMED below, but the report cannot say its verdict is provisional over them — and the citation + governance checks do not exist on this checker at all. Upgrade the SDK to get them back." >&2
fi

# --- project the PRD's typed facts -------------------------------------------------------------
# One malformed fact is the CHECKER's to drop, with a named advisory item: the projector forwards
# it verbatim and names it here. Only a facts block that cannot be read at all (unparseable, not a
# list, an unreadable PRD) ends the run, with the sweep — and, on a checker with no per-fact drop,
# any malformed or repeated fact. `scan_facts.py` removes its own stale
# output; the sweep covers the rest.
ARGS=(check --out "$OUT")
# With no --prd there is no projection this run, and the skill documents `scan_facts.yaml` as
# regenerated on every scan — so a previous run's projection would be read as this one's inputs.
if [ -z "$PRD" ] && [ -f "$FACTS" ]; then
  rm -f "$FACTS" 2>/dev/null
  [ ! -f "$FACTS" ] || _fail "a previous scan's $FACTS could not be removed and this run projects no facts, so the file on disk describes a different scan. NOTHING was verified." 2
fi
if [ -n "$PRD" ]; then
  # The projector's first FINDING is carried INTO the refusal: this script's last line is what the
  # bench manifest records as the evidence, and "see above" means nothing to the later session
  # whose Step-0 gate quotes it. The finding names the fact indices; its headline only counts them.
  # A checker that predates `--overrule` also predates the per-fact drop, so it would DECIDE a
  # malformed fact the projector forwards verbatim (an unknown key over the whole corpus, several
  # terms as `any`, `expected_count: true` as 1) — a false VERIFIED. Against it the projector
  # refuses instead, as it always did.
  REFUSE_ARGS=()
  case "$_CHECK_HELP" in
    *--overrule*) : ;;
    *) REFUSE_ARGS=(--refuse-malformed) ;;
  esac
  _PROJ_ERR="$("$PLURAI_PY" "$PROJECTOR" "$PRD" --out "$FACTS" ${ASSERTED_ARGS[@]+"${ASSERTED_ARGS[@]}"} ${REFUSE_ARGS[@]+"${REFUSE_ARGS[@]}"} 2>&1 >/dev/null)" && _PROJ_RC=0 || _PROJ_RC=$?
  [ -z "$_PROJ_ERR" ] || printf '%s\n' "$_PROJ_ERR" >&2
  if [ "$_PROJ_RC" -eq 0 ]; then
    ARGS+=(--facts "$FACTS")
  else
    _PROJ_WHY="$(printf '%s\n' "$_PROJ_ERR" | grep -m 1 '^  - ' | sed 's/^  - //')"
    [ -n "$_PROJ_WHY" ] || _PROJ_WHY="${_PROJ_ERR%%$'\n'*}"
    _fail "the scan's typed facts could not be projected (${_PROJ_WHY}), so NOTHING was verified." 2
  fi
fi

# --- workdir conventions ---------------------------------------------------------------------
for _root in "${CORPORA[@]}"; do ARGS+=(--corpus "$_root"); done

# `--suffixes` / `--service-indicators` are FORWARDED, and probed for first — for the same reason
# `--source-root` is. The two console scripts ship separately from this plugin and no CI compares
# them, so passing a flag an older checker does not know turns a fine scan into a usage error
# (exit 2, NOTHING verified) over an option the caller could have done without. Refused rather
# than dropped, though: unlike the asserted-fact kind, both of these change WHAT IS WALKED, so
# quietly ignoring one would hash a different corpus than the caller asked for and report the
# result as theirs.
#
if [ "${#SUFFIXES[@]}" -gt 0 ]; then
  case "$_CHECK_HELP" in
    *--suffixes*) for _sfx in "${SUFFIXES[@]}"; do ARGS+=(--suffixes "$_sfx"); done ;;
    *) _fail "--suffixes was given but $BIN predates the flag (its \`check\` does not accept it). Ignoring it would walk a DIFFERENT corpus than you asked for and report that as your result. Upgrade the SDK, or drop the flag. NOTHING was verified." 64 ;;
  esac
fi
if [ -n "$SERVICE_INDICATORS" ]; then
  [ -f "$SERVICE_INDICATORS" ] || _fail "--service-indicators $SERVICE_INDICATORS is not a file, so the in-house service names cannot be read. NOTHING was verified." 2
  case "$_CHECK_HELP" in
    *--service-indicators*) ARGS+=(--service-indicators "$SERVICE_INDICATORS") ;;
    *) _fail "--service-indicators was given but $BIN predates the flag (its \`check\` does not accept it). Ignoring it would classify your in-house services as unknown vendors and report that as your result. Upgrade the SDK, or drop the flag. NOTHING was verified." 64 ;;
  esac
fi

# The tree every `source:` citation is resolved against, and the PRD the design directives are
# read out of. Both are what turns "the file exists" into "the LINE says what the fact says":
# a citation eleven lines off resolves perfectly and cites nothing.
#
# $WORKDIR joins them whenever the caller opted in — and it is not a guess about anything. TWO of
# the four documented `source:` forms (`artifacts/data_profile.md`, `artifacts/probe_report.md`,
# `plurai.config.yaml`) live under the workdir this script was HANDED, and only `path/to/file.py`
# lives under the agent module. With just the module passed, those two resolve to nothing, come
# back MISSING — which is BLOCKING — and a correctly-authored PRD is reported as citing evidence
# that does not exist, `proceed=false`, on to all three generators. Skipped when no root was
# given at all, so "no citations were checked" stays the distinct, blocked state it must be.
_HAVE_SOURCE_ROOT=0
for _root in ${SOURCE_ROOTS[@]+"${SOURCE_ROOTS[@]}"}; do ARGS+=(--source-root "$_root"); _HAVE_SOURCE_ROOT=1; done
[ "$_HAVE_SOURCE_ROOT" = 0 ] || ARGS+=(--source-root "$WORKDIR")
[ -z "$PRD" ] || ARGS+=(--prd "$PRD")

# The two scan outputs the contract cross-references against each other. Both are OPTIONAL and
# both are found by convention rather than passed: the state schema is an env-gen output that
# legitimately does not exist before the suite's Step 0c, and the variables file is written by
# `/extract-scenario-variables`. An absent one leaves its check BLOCKED with the reason, which is
# what puts `agent_context is UNGOVERNED` in the headline instead of nowhere.
#
# `_usable_input`, not `[ -s ]`. `-s` is a SIZE test: it passes a DIRECTORY of that name — which
# then trips the checker's own `dir_okay=False` and kills the whole scan with a usage error — and
# it passes a whitespace-only file, which the checker reads as a schema that declares nothing.
_usable_input() {  # path -> true when it is a regular file with something other than whitespace in it
  [ -f "$1" ] && [ -n "$(tr -d '[:space:]' < "$1" 2>/dev/null)" ]
}
_SCHEMA="$WORKDIR/scenario_state_schema.yaml"
if _usable_input "$_SCHEMA"; then
  ARGS+=(--state-schema "$_SCHEMA")
  # PROVENANCE, said out loud. This file is an env-gen OUTPUT that no scan produces, so one left
  # by a PREVIOUS env is indistinguishable from this env's — and the check then reports
  # `agent_context is GOVERNED` over another env's fields. This script cannot know which env wrote
  # it; what it must not do is bind to it silently.
  echo "scan-contract: agent_context governance is decided from $_SCHEMA (last modified $(date -u -r "$_SCHEMA" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || echo 'unknown')) — an env-gen OUTPUT this scan did not write. If it belongs to a previous env, the GOVERNED verdict names that env's fields." >&2
  # Its OWN header says --stateless-finding "Stands in for --state-schema, never beside it", and
  # the checker consults the schema first — so forwarding both would discard the finding in
  # silence, which is the collapse this script exists to remove.
  if [ -n "$STATELESS" ]; then
    echo "scan-contract: --stateless-finding was given AND $_SCHEMA exists. The SCHEMA decides; the stateless finding was NOT used. Remove the schema, or drop the flag." >&2
    STATELESS=""
  fi
fi
[ -z "$STATELESS" ] || ARGS+=(--stateless-finding "$STATELESS")
_VARIABLES="$WORKDIR/scenario_variables.yaml"
! _usable_input "$_VARIABLES" || ARGS+=(--variables "$_VARIABLES")

# The built corpus this fingerprint belongs to. Absent on a first scan (the corpus is built at
# the suite's grounding gate, later), and the report says so rather than implying a binding.
# Read through the ONE shared reader (`corpus_coordinate.py`), so the three marker shapes are
# known in one place: this branch used to read `.corpus_id` alone and then say "no single
# .corpus_id" for everything else — which left a single-knowledge-base agent whose id lives in
# `.knowledge_bases` (or a one-line `.corpus_ids` from /manage-envs) unbound for no reason.
_CORPUS_IDS=()
_CORPUS_READ="$("$PLURAI_PY" "$_HERE/corpus_coordinate.py" --workdir "$WORKDIR" 2>/dev/null)" && _CORPUS_RC=0 || _CORPUS_RC=$?
while IFS= read -r _cid; do [ -z "$_cid" ] || _CORPUS_IDS+=("$_cid"); done <<<"$_CORPUS_READ"
if [ "$_CORPUS_RC" -eq 2 ]; then
  # UNKNOWN, and NOT "no corpus": a marker is there and cannot be parsed. Exit 1 (no marker at
  # all) is the ordinary pre-corpus state and stays silent; this one has to be said, or a broken
  # `.knowledge_bases` leaves the source hash bound to nothing and looks exactly like an agent
  # that has no knowledge base — the conflation the shared reader exists to end.
  echo "scan-contract: a corpus marker in $WORKDIR is present but unreadable, so the source hash is NOT bound to a corpus this run. Run scripts/corpus_coordinate.py for the file and the reason." >&2
elif [ "${#_CORPUS_IDS[@]}" -eq 1 ]; then
  ARGS+=(--corpus-id "${_CORPUS_IDS[0]}")
elif [ "${#_CORPUS_IDS[@]}" -gt 1 ]; then
  # The flag takes ONE id, and picking one of several would bind the fingerprint to a corpus
  # nobody chose — so it is left unbound, and that is stated rather than left to look deliberate.
  echo "scan-contract: this env stands on ${#_CORPUS_IDS[@]} corpora (${_CORPUS_IDS[*]}) and --corpus-id takes one, so the source hash is NOT bound to a corpus this run." >&2
fi
# Drift against THIS workdir's previous contract run — from a COPY under a name the CLI never
# writes, so the artifact names it does write are free to be cleared below. Passing `$BASELINE`
# itself would mean the drift baseline and this run's verdict share a path, and the post-run
# check could no longer tell which run put it there.
#
# An UNUSABLE baseline is dropped rather than passed: the CLI treats a --against it cannot read
# as a usage error and refuses to run at all, so one stale file from an older format would cost
# the whole verification. Dropped LOUDLY — the drift half is the part that stops being checked.
PREV="$OUT/.scan_contract.previous.json"
rm -f "$PREV"
if [ -s "$BASELINE" ]; then
  if "$PLURAI_PY" -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("source_inventory",{}).get("content_hash") else 1)' "$BASELINE" 2>/dev/null && cp -f "$BASELINE" "$PREV" 2>/dev/null; then
    ARGS+=(--against "$PREV")
  else
    echo "scan-contract: $BASELINE cannot be a drift baseline (no source_inventory.content_hash, or it could not be copied aside) — this run RECORDS a new one and reports no drift." >&2
  fi
fi
# --- probe consent: TWO facts, and they are not the same one ----------------------------------
# `.probe_consent` records a grant for a NAMED list of the agent's OWN READ-ONLY TOOLS, given at
# the topology gate — `granted: load_assets, build_system_prompt`. The CLI's `--probe-consent`
# authorizes a DIFFERENT probe class: scan-time asks sent to the LIVE AGENT. Forwarding one as the
# other is consent laundering, so `.probe_consent` is never read here and never will be.
#
# With one shared name, a workdir marker reading `granted: load_assets, build_system_prompt` beside
# a contract whose own `scan_time_smoke_probes.probe_consent` is `false` would lead anybody
# reading the workdir to conclude the live probes had run, when they were
# `DERIVED ONLY — 2 probe(s) derived and NOT run`.
#
# So the live class has its own marker, `.live_probe_consent`, with the same three states
# (`granted` / `declined` / `unasked`) and no possibility of being confused for the other file.
# ONLY a literal `granted` forwards the flag: `declined`, `unasked`, an absent file and any
# spelling this script does not recognise all mean "not given", which is what the contract then
# reports with that reason.
_LIVE_CONSENT="$WORKDIR/.live_probe_consent"
if [ -f "$_LIVE_CONSENT" ]; then
  # Read with builtins: what a `head`/`tr` on PATH printed would otherwise decide whether the live agent is probed.
  _live_state=""
  { IFS= read -r _live_state || true; } 2>/dev/null < "$_LIVE_CONSENT" || true
  _live_state="${_live_state//[[:space:]]/}"
  case "$_live_state" in
    granted)
      # PROBED FOR FIRST, like `--suffixes` and `--service-indicators` above and for exactly the
      # same reason: the console scripts ship separately from this plugin and no CI compares them,
      # so passing a flag an older checker does not know turns a fine scan into a usage error —
      # exit USAGE, which this script's `*)` arm reports as "the contract did not complete;
      # NOTHING was verified" and which sweeps the `scan_contract.json` that Step 0 of
      # /simulation-generate-suite reads `report.proceed` from. `--probe-consent` had no caller at
      # all before this ticket and `--agent-command` is newer still, so an operator who grants live
      # probes on a machine with an older SDK is the EXPECTED case, not an edge one.
      #
      # REFUSED, never dropped. Quietly ignoring a consent flag ships a contract that reports the
      # probes derived-and-not-run while the workdir says they were granted — the degradation
      # the separate live-probe marker exists to remove.
      case "$_CHECK_HELP" in
        *--probe-consent*) ARGS+=(--probe-consent) ;;
        *) _fail "$_LIVE_CONSENT grants LIVE-agent smoke probes but $BIN predates the flag (its \`check\` does not accept --probe-consent). Ignoring it would report the probes as derived-and-not-run while this workdir says they were granted. Upgrade the SDK, or set the marker to \`declined\`. NOTHING was verified." 64 ;;
      esac
      # The consent flag alone fires nothing: the SDK needs a command that takes one ask on stdin
      # and prints the agent's reply. Consent WITHOUT it lands on a different not-run reason, which
      # is a better report than the old one but is still not a probe — so say which of the two
      # states this run is in rather than letting `granted` read as "the probes ran".
      if [ -s "$WORKDIR/.agent_command" ]; then
        case "$_CHECK_HELP" in
          *--agent-command*)
            _agent_command=""
            { IFS= read -r _agent_command || true; } < "$WORKDIR/.agent_command"
            ARGS+=(--agent-command "$_agent_command") ;;
          *) _fail "$WORKDIR/.agent_command names a command for the LIVE-agent smoke probes but $BIN predates the flag (its \`check\` does not accept --agent-command). Ignoring it would fire nothing and report consent as granted. Upgrade the SDK, or remove the marker. NOTHING was verified." 64 ;;
        esac
      else
        echo "scan-contract: $_LIVE_CONSENT grants LIVE-agent smoke probes, but there is no $WORKDIR/.agent_command to send them with, so none will be fired. Write one line there — a command taking one ask on stdin and printing the agent's reply on stdout." >&2
      fi
      ;;
    declined|unasked) : ;;
    *)
      echo "scan-contract: $_LIVE_CONSENT says '$_live_state', which is not one of granted/declined/unasked — read as NOT GRANTED. The live-agent smoke probes are derived and not run." >&2
      ;;
  esac
fi

# --- the plugin's overrules ----------------------------------------------------------------------
# The checker applies them itself and computes `report.proceed` after them, so the ledger is
# forwarded whole and this script never filters it. PROBED FOR like the flags above, but DROPPED
# rather than refused when the checker predates it: without it every holding finding simply stays
# held, which is the direction that cannot pass anything the plugin did not decide.
if [ -f "$LEDGER" ]; then
  case "$_CHECK_HELP" in
    *--overrule*) ARGS+=(--overrule "$LEDGER") ;;
    *) echo "scan-contract: $BIN predates --overrule, so the overrules in $LEDGER cannot lift a held finding on this checker. Upgrade the SDK to apply them." >&2 ;;
  esac
fi

# --- clear the previous run's verdict, THEN run ------------------------------------------------
# The existence check after the run is only meaningful over names this run alone can have
# written. `--against` already holds its own copy, so nothing is lost by emptying them here.
#
# CHECKED, and by re-testing the paths rather than by `rm`'s status alone. An unwritable artifacts
# dir makes the unlink fail; the post-run check then passes on the PREVIOUS run's files and the
# script announces PROCEED over a corpus this run never read. That is the same collapse the clear
# exists to remove, arriving through the clear itself.
rm -f "$REPORT" "$BASELINE" "$GATE_AUTHORITY" "$LEGACY_GATE_AUTHORITY" 2>/dev/null
if [ -f "$REPORT" ] || [ -f "$BASELINE" ] || [ -f "$GATE_AUTHORITY" ] || [ -f "$LEGACY_GATE_AUTHORITY" ]; then
  _fail "the previous run's verdict could not be cleared from $WORKDIR, so a verdict from this run cannot be told from an older one. NOTHING was verified." 2
fi
# The copy is scratch, not an artifact: it must not outlive the run under any exit path, or the
# next scan inherits a `--against` from two runs ago.
trap 'rm -f "$PREV"' EXIT

"$BIN" "${ARGS[@]}"
STATUS=$?

case "$STATUS" in
  "$PLURAI_RC_OK" | "$PLURAI_RC_SCAN_HELD")
    # A verdict is only a verdict if this run wrote one. A CLI that exits 0 or 1 without leaving
    # a report — crashed after parsing, an out-dir it could not write, a version writing a
    # different filename — would otherwise have the line below point an operator, and the suite's
    # gate, at an older scan's conclusions.
    if [ ! -f "$REPORT" ] || [ ! -f "$BASELINE" ]; then
      _fail "the contract exited $STATUS but wrote no report, so there is no verdict from this run. NOTHING was verified." 2
    fi
    # Bound to the PRD this run projected (the header's `prd_sha256`). Written into THIS run's
    # artifact only; a failed write leaves it without a digest, which the gate reads as STALE.
    if [ -n "$PRD_SHA256" ]; then
      "$PLURAI_PY" -c 'import json,os,sys; p=sys.argv[1]; a=json.load(open(p,encoding="utf-8")); a["prd_sha256"]=sys.argv[2]; open(p+".tmp","w",encoding="utf-8").write(json.dumps(a,indent=2,ensure_ascii=False)); os.replace(p+".tmp",p)' "$BASELINE" "$PRD_SHA256" 2>/dev/null \
        || echo "scan-contract: could not record the PRD digest in $BASELINE — /simulation-generate-suite Step 0 will read this verdict as STALE and re-project." >&2
    fi
    # This run wrote both artifacts, so whatever an earlier run could not clear is gone: the
    # quarantine must lift with it, or one unwritable-directory scan gates every later suite.
    rm -f "$WORKDIR/.scan_contract_stale" 2>/dev/null || true
    # The ROOTS this baseline was hashed over, one absolute path per line. `scan_contract.json`
    # records the file inventory and not the roots it walked, so a later `plurai-run-simulation
    # --scan-contract` had a baseline and nothing to re-hash against it — the drift check reported
    # NOT CHECKED on every run, which is how a suite shipped beside a drift finding nobody rechecked.
    # Best-effort: an unwritable workdir costs the post-run check, never this verdict.
    # RESOLVED, because the promise is "one absolute path per line" and the later reader runs
    # from a different cwd — a relative `--corpus docs/` recorded verbatim re-hashes nothing there
    # and the drift check reports NOT CHECKED without saying why.
    #
    # A ROOT THAT WILL NOT RESOLVE WRITES NO LINE. The fallback used to echo the relative path,
    # which is the very thing three lines up says re-hashes nothing there — so the file kept its
    # promise's SHAPE while breaking its content, and the later reader had no way to tell. An
    # omitted root is a shorter re-hash the operator is told about; a relative one is a silent
    # NOT CHECKED. (Unreachable on the normal path: every `--corpus` was proved to exist
    # above. It survives a root that disappeared mid-run, or one this process cannot enter.)
    _unresolved=""
    for _root in "${CORPORA[@]}"; do
      # A FILE root resolves through its directory; the line names the file itself.
      if [ -d "$_root" ]; then _abs="$( (cd "$_root" 2>/dev/null && pwd) || true)"
      elif [ -f "$_root" ]; then _abs="$( (cd "$(dirname "$_root")" 2>/dev/null && printf '%s/%s' "$(pwd)" "$(basename "$_root")") || true)"
      else _abs=""; fi
      if [ -n "$_abs" ]; then printf '%s\n' "$_abs"; else _unresolved="$_unresolved $_root"; fi
    done > "$WORKDIR/.scan_corpus_roots" 2>/dev/null || \
      echo "scan-contract: could not record the corpus roots at $WORKDIR/.scan_corpus_roots — a later run's drift re-check will have nothing to re-hash." >&2
    [ -z "$_unresolved" ] || \
      echo "scan-contract: could not resolve to an absolute path, so NOT recorded in $WORKDIR/.scan_corpus_roots ($_unresolved ) — a later run's drift re-check will not cover those roots. The file holds absolute paths only; a relative one re-hashes nothing from another cwd and reports NOT CHECKED without saying why." >&2
    # The SUFFIXES this baseline was walked with, one per line, beside the roots and for the same
    # reason. The later `plurai-run-simulation --scan-contract` RE-hashes this baseline, and the
    # recheck takes the same flag — so a baseline recorded WIDE and re-hashed NARROW reports every
    # widened file as `removed`, a drift verdict the sources never earned. Removed when this run
    # widened nothing, so a stale file from a previous wide scan cannot narrow a later recheck.
    if [ "${#SUFFIXES[@]}" -gt 0 ]; then
      printf '%s\n' "${SUFFIXES[@]}" > "$WORKDIR/.scan_corpus_suffixes" 2>/dev/null || \
        echo "scan-contract: could not record the corpus suffixes at $WORKDIR/.scan_corpus_suffixes — a later drift re-check will walk the checker's DEFAULT suffixes and report every widened file as removed." >&2
    else
      rm -f "$WORKDIR/.scan_corpus_suffixes" 2>/dev/null || true
    fi
    if [ "$STATUS" = "$PLURAI_RC_OK" ]; then
      _EXIT=0
      echo "scan-contract: PROCEED — report at $REPORT" >&2
    else
      _EXIT=1
      echo "scan-contract: FIX FIRST — held finding(s); report at $REPORT, the findings (gate id + fingerprint) in $BASELINE \`report.gate_findings\`" >&2
    fi
    ;;
  "$PLURAI_RC_INTERNAL_FAULT")
    # THE CHECKER ITSELF FAILED — it asserted nothing. Kept OUT of the FIX-FIRST arm deliberately: "your scan
    # found something false" and "the checker crashed" send an operator to opposite places, and
    # the second one has no findings to read. Swept like every other non-verdict.
    echo "scan-contract: NOT RUN — $BIN FAILED (exit $STATUS). It crashed rather than reaching a verdict, so it asserted NOTHING about this scan; there are no findings to read and nothing here is a blocking finding. See its own output above, re-run it, and report the crash if it repeats." >&2
    _sweep_verdict || exit 2
    _EXIT=5
    ;;
  *) _fail "the contract did not complete (exit $STATUS); NOTHING was verified." 2 ;;
esac
exit "$_EXIT"
