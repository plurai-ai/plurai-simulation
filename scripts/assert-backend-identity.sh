#!/usr/bin/env bash
# assert-backend-identity.sh — preflight: prove the backend you are ABOUT TO SHELL OUT TO is
# the same one the MCP server is bound to.
#
#   source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" "<mcp_identity>" || exit 1
#
# It also sources resolve-plurai-urls.sh + resolve-workdir.sh, so one line gives you
# $SERVER_URL / $WORKDIR / $PLURAI_DIR as well — no separate source needed.
#
# WHY. Three resolvers answer "which backend?" independently and can silently disagree:
#   1. the MCP server, whose URL was expanded ONCE at session launch from
#      ${PLURAI_URL:-…} (.claude-plugin/plugin.json) — bash cannot observe it;
#   2. this shell, where resolve-plurai-urls.sh reads the env AT BASH TIME (which, under
#      directory-scoped shell hooks, is not necessarily the env the harness launched with);
#   3. the SDK CLI, which resolves again at call time.
# A split surfaces late and misleadingly: a healthy-but-WRONG backend answers a clean 404,
# which reads as "expired id" rather than "wrong box". Thirty seconds here beats that.
#
# SCOPE — this check IS the wrong-backend guard, and it asks one question: does THIS SHELL
# agree with the MCP server? Run it before an upload and the answer covers the upload too,
# because `plurai ingest-*` resolves its target from the same $PLURAI_URL this
# shell just compared. The SDK holds no second opinion of its own (settings are the single
# enforcer), so a caller who skips this has NO wrong-backend check at all. One caveat worth knowing:
# Claude Code fixes the MCP server's url at launch from $PLURAI_URL, so a shell that resolves another
# backend (a saved login for a different one) splits the two, and this comparison is where it shows up.
#
# ARGUMENT. <mcp_identity> is what the MCP server says it is. Bash CANNOT obtain it — that is
# the whole reason it is an argument. Two accepted forms, which select the COMPARISON MODE:
#
#   PRECISE  the identity OBJECT, verbatim, from the `backend_identity()` MCP tool. Compared
#            field-for-field against this shell's own GET /backend-identity. Pass this.
#   HOST     a bare host or URL. Compared as hostnames only. Coarser — it cannot tell two
#            backend processes on one host apart — but it is honest about what it knows.
#
# The modes are NEVER mixed: an instance_id is an opaque box id, and comparing one against a hostname
# would manufacture a split on every run.
#
#   ARMED    no argument at all — which includes the unsubstituted `<backend_identity() object>`
#            placeholder every call site ships — AND this session has already completed a
#            verified PRECISE check. The identity then comes from the arm-time record described
#            below and is compared host to host, exactly as HOST mode is.
#
# With NEITHER an argument NOR that record the script WARNS and continues: a preflight that
# hard-fails on its own missing input would just get skipped.
#
#   HELPER   exactly ONE argument, and it is empty (`. assert-backend-identity.sh "" || exit 1`) —
#            the entry for scripts with no model in the loop. Armed-record mode, but SILENT unless
#            it refuses: the unarmed NOTE asks for an argument such a caller cannot pass, and the
#            workdir receipt is not its output. Every refusal, and the resolver's own warnings, stay.
#
# EXIT / RETURN
#   0  identities agree; or a restart- or replica-only difference; or a deployment split the
#      plugin overruled in its gate ledger; or the check degraded to a warning (no argument AND
#      nothing armed, a backend with no identity route, or no `plurai` runner to read this
#      shell's identity with)
#   1  a SPLIT that holds — the message names BOTH sides, field by field (PRECISE) or host to host,
#      and in PRECISE mode prints one `finding:` line per class of field that differs; or, in
#      PRECISE mode, a comparison that could not run at all, or the client pin or the SDK floor
#      refused the resolved runner; or the runner's credit stop, printed as one `CREDIT-STOP: <sentence>` line
#
# It also exports $WORKDIR / $PLURAI_DIR (via resolve-workdir.sh); the only thing read under
# them here is the arm-time record below.

# ─── THE BACKEND-IDENTITY CONTRACT (fixed — not provisional) ──────────────────
# GET /backend-identity on $SERVER_URL, and the no-arg read-only MCP tool backend_identity(),
# return the SAME flat object — there is no wrapper key:
#   {"instance_id","started_at","hostname","build_id","source_id","organization_id","workspace_id","storage_root"}
# The route is bearer-guarded (it is NOT in the backend's PRE_AUTH_PATHS). The fetch is the SDK's
# `plurai backend identity`, which sends $PLURAI_TOKEN from its own process, so the
# bearer is in no command line.
#
# instance_id names the live box: on an E2B box it IS the E2B sandbox id, so it holds across a
# backend restart in that box and moves when the box is replaced; off E2B it is a per-process uuid
# and moves on every restart. started_at is when the backend process first observed the box, so it
# moves on every restart, in the same box or not. Equal instance_id ⇒ the same live box; unequal
# says only that the box (or, off E2B, the process) moved. A restart is either an instance_id that
# differs, or an instance_id that agrees while started_at, present on both sides, differs. Either
# one counts only when no other field differs: _abi_verdict reports a tenant, storage or deployment
# split, then a replica, ahead of a restart. A started_at missing on either side (an older backend)
# claims no restart.
# The route and key below are the contract, kept in one place so a future backend change is a
# one-line edit here. The route is the SDK's, and is named here only for the messages.
PLURAI_IDENTITY_PATH=/backend-identity
PLURAI_IDENTITY_KEY=instance_id
#
# The fields that survive a restart fall into CLASSES, and the class — not this script — decides
# who may lift a split on it (the catalogue in gate_ledger.py). They are fixed in _ABI_JUDGE_PY:
#   tenant      organization_id workspace_id  security   another tenant would receive the artifacts
#   storage     storage_root                  invariant  ids minted through MCP 404 in another store
#   deployment  source_id build_id            advisory   the same tenant and store on another build:
#                                                        a rolling deploy holds until it settles or the
#                                                        plugin overrules it
#   host        hostname                      a replica (never a split) when the two sides share a
#                                             tenant; with no tenant on either side, deployment
# `source_id` sits FIRST among the deployment fields because it is the only one a
# per-file source patch moves, so two boxes on one image can still run different code.
# ──────────────────────────────────────────────────────────────────────────────────────

# ─── THE ARM-TIME RECORD — $PLURAI_DIR/.backend_identity ──────────────────────────────
# One line of JSON — {"session_id","server_url","instance_id"} — written by the branch below
# the FIRST time this guard completes a verified PRECISE check, and rewritten on every one
# after. Machine-local, gitignored with the rest of the dotfile family.
#
# WHY A FILE AND NOT A SMARTER RESOLVER. The binding is the URL Claude Code expanded ONCE at
# session launch; no later bash can observe it, which is why the identity is an ARGUMENT here.
# But six shell/python helpers resolve a backend from the environment with no model in the loop
# to paste one, so they carried no check at all — and the argument form fails OPEN when the
# placeholder reaches this script unsubstituted. A record written by the one component that CAN
# see the binding is what both of those read instead.
#
# KEYED ON THE BINDING'S LIFETIME — the Claude Code PROCESS ($CLAUDE_PID) that expanded
# plugin.json, falling back to $CLAUDE_CODE_SESSION_ID where no pid is exported, and never armed
# or read with neither. Copilot and Codex export no pid: their session id is the key. Not the conversation id alone: `claude -c` / `--resume` keep it while the
# relaunch re-binds MCP, so a session-keyed record refused the new, correct backend. A record from
# any other process reads as ABSENT. In-process sub-agents share the pid, and so the record.
# HOST-GRAIN, like HOST mode and for the same reason the resolver does no network: this read
# runs in poll_job.sh's preflight, and a round trip there is a cost every poll of every job pays.
# ──────────────────────────────────────────────────────────────────────────────────────

_abi_here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
# shellcheck source=sdk-exit-codes.sh
. "$_abi_here/sdk-exit-codes.sh"
# HELPER entry (see ARGUMENT above). Read from `$#` BEFORE anything else is sourced: a bare
# `. script` sees the caller's positional parameters, so an explicit "" is the only form that
# says "a helper is calling" rather than "the model passed nothing".
_abi_quiet=0
if [ "$#" -eq 1 ] && [ -z "${1:-}" ]; then _abi_quiet=1; fi
# shellcheck source=resolve-plurai-urls.sh
. "$_abi_here/resolve-plurai-urls.sh" || return 1 2>/dev/null || exit 1
# shellcheck source=resolve-workdir.sh
if [ "$_abi_quiet" = 1 ]; then
  . "$_abi_here/resolve-workdir.sh" 2>/dev/null
else
  . "$_abi_here/resolve-workdir.sh"
fi

# The key the arm-time record is written and read under. Empty ⇒ no binding to key on ⇒ unarmed.
# The innermost harness decides (Copilot's variable, then Codex's, as resolve-workdir.sh orders them):
# those two export a session id and no process id.
_abi_binding() {
  if [ -n "${COPILOT_AGENT_SESSION_ID:-}" ]; then
    printf 'session:%s' "$COPILOT_AGENT_SESSION_ID"
  elif [ -n "${CODEX_SESSION_ID:-}" ]; then
    printf 'session:%s' "$CODEX_SESSION_ID"
  elif [ -n "${CODEX_THREAD_ID:-}" ]; then
    printf 'session:%s' "$CODEX_THREAD_ID"
  elif [ -n "${CLAUDE_PID:-}" ]; then
    printf 'pid:%s' "$CLAUDE_PID"
  elif [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then
    printf 'session:%s' "$CLAUDE_CODE_SESSION_ID"
  fi
}

# resolve-plurai-urls.sh leaves _plurai_host_of defined — ONE definition of host parsing
# for the whole plugin. Redefined here only if a future refactor unsets it.
if ! command -v _plurai_host_of >/dev/null 2>&1; then
  _plurai_host_of() { local u="${1#*://}"; u="${u%%/*}"; u="${u##*@}"; case "$u" in \[*\]*) u="${u#\[}"; u="${u%%\]*}" ;; *) u="${u%%:*}" ;; esac; printf '%s' "$u"; }
fi

# This shell's own view of the backend it is about to use, through the runner in $1. Sets
# ABI_RC (the SDK's exit code), ABI_BODY (the identity JSON on success) and ABI_DETAIL (the
# SDK's one-line reason otherwise). The SDK retries a transport failure once, 1s later, and
# bounds each attempt at 5s: a dropped connection is the state where a split is most plausible
# and least excluded, so one blip must not disarm the check. Any HTTP answer is final.
_abi_fetch_identity() {
  local err
  err="$(mktemp "${TMPDIR:-/tmp}/abi.XXXXXX" 2>/dev/null)" || err=""
  # The SDK reads only PLURAI_URL; set for this one call, since this file is sourced
  # and must not change the caller's environment.
  ABI_BODY="$(PLURAI_URL="$SERVER_URL" "$1" backend identity 2>"${err:-/dev/null}")"
  ABI_RC=$?
  ABI_DETAIL=""
  ABI_ERR=""
  if [ -n "$err" ]; then
    ABI_DETAIL="$(sed -n 's/^backend_identity: //p' "$err" | head -n 1)"
    # An upgrade-required exit is rendered from the SDK's whole refusal, which is the plugin's text and never the SDK's own.
    [ "$ABI_RC" != "$PLURAI_RC_UPGRADE_REQUIRED" ] || ABI_ERR="$(cat "$err")"
    rm -f "$err"
  fi
  [ "$ABI_RC" = "$PLURAI_RC_OK" ] || ABI_BODY=""
  return 0
}

# What `plurai backend identity` exits with when the gateway refuses the read for credit; in that command's table the code has
# that one meaning (`sdk_exit_codes.FETCH_CREDIT_REFUSED`).
_ABI_RC_CREDIT_REFUSED="$PLURAI_RC_JOB_FAILED"

# The directory gate_ledger.py is imported from. Not unset with the other locals at the end: the
# two functions below outlive the source, and read it when they are called.
_ABI_SCRIPTS="$_abi_here"

# Field-for-field verdict, and the gate findings a split mints. ONE program, two modes, so the
# comparison the verdict prints is the comparison the findings are fingerprinted on.
#
#   verdict <mcp> <shell>  line 1: ok | replica | restart | degrade | split <class>...; then detail
#   settle  <mcp> <shell>  one `finding:` line per differing class, settled against the plugin's
#                          gate ledger ($ART/.gate_overrules.jsonl); exit 1 while one still holds
#
# The verdict half is stdlib-only and imports nothing from this directory: it is what decides a
# tenant split, so it must run on any python3 that can run the rest of this file. gate_ledger.py
# is imported in settle only, where a failed import exits non-zero and the split STOPS.
#
# Only fields PRESENT ON BOTH SIDES are COMPARED — a version skew between the MCP surface and
# the REST route must never read as a wrong backend. But a field only one side reports is not
# nothing either: silently dropping it verdicted `ok` when `source_id` — the FIRST deployment
# field, and the only one a per-file source patch moves — was observed on one side and absent
# from the other. Those fields are listed as UNCOMPARABLE beneath whatever verdict the comparable
# ones produced, including `ok`; they never change the verdict, because "one side is older" and
# "the two are different boxes" cannot be told apart from here.
#
# `hostname` alone reads as a replica only when the two sides SHARE a tenant: some tenant field
# carries a non-blank value on both. With every tenant field blank or absent, nothing ties the two
# hosts to one backend (separate containers on one image agree on every other field), so a
# different hostname joins the deployment class and holds until the plugin overrules it.
#
# A finding's evidence is the NAMES of the differing fields plus the MCP side's workspace and
# store; the values go in `observed`. So one overrule covers a rollout however many builds and
# hosts it passes through.
_ABI_JUDGE_PY='
import json, math, os, sys

KEY = os.environ["PLURAI_IDENTITY_KEY"]
TENANT, STORAGE, DEPLOYMENT, HOST = ("organization_id", "workspace_id"), ("storage_root",), ("source_id", "build_id"), "hostname"
START = "started_at"
REMEDY = {
    "tenant": "the MCP server and this shell answer for different organizations or workspaces. Point both at one "
              "backend: align PLURAI_URL in this shell with the one Claude Code was launched with; if the MCP side is the "
              "wrong one, restart Claude Code with the intended PLURAI_URL.",
    "storage": "the two sides read and write different artifact stores, so ids minted through MCP 404 here. Point "
               "both at one backend: align PLURAI_URL in this shell with the one Claude Code was launched with.",
    "deployment": "the same tenant and store served by another build, source or host (a rolling deploy, replicas "
                  "on different builds, or a second box nothing tells apart). Re-run this check once a rollout "
                  "settles; or confirm the ids this flow uses resolve through this shell and overrule per "
                  "${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md.",
}
FIELDS = list(TENANT + STORAGE + DEPLOYMENT) + [HOST]


def load(s):
    try:
        d = json.loads(s)
    except Exception:
        return None
    return d if isinstance(d, dict) else None


mode, mcp, shell = sys.argv[1], load(sys.argv[2]), load(sys.argv[3])
if mcp is None or shell is None or not shell.get(KEY):
    print("degrade")
    raise SystemExit(1 if mode == "settle" else 0)


def differs(f):
    return f in mcp and f in shell and str(mcp[f]) != str(shell[f])


def blank(value):
    return value is None or not str(value).strip()


def as_seconds(value):
    """A finite float for a number or a numeric string; None for a bool, NaN, an infinity or any other
    value, which are not comparable."""
    if isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def restarted_in_place():
    """The box id agrees but the process start moved: one E2B box whose backend restarted. No timestamp
    format is parsed: two starts that are numbers, or strings of finite numbers, are one start when
    they lie within a second of each other; anything else compares as the JSON values. A start
    missing or null on either side (an older backend) claims nothing."""
    a, b = mcp.get(START), shell.get(START)
    if blank(a) or blank(b):
        return False
    x, y = as_seconds(a), as_seconds(b)
    if x is not None and y is not None:
        # The MCP value is copied through a model and can lose digits; a real restart moves it by seconds.
        return abs(x - y) >= 1.0
    return a != b


def detail(f):
    return "  %s:  MCP %s vs shell %s" % (f, mcp[f] or "(unset)", shell[f] or "(unset)")


shared_tenant = any(not blank(mcp.get(f)) and not blank(shell.get(f)) for f in TENANT)
deployment = DEPLOYMENT if shared_tenant else DEPLOYMENT + (HOST,)
split = [(name, [f for f in fields if differs(f)]) for name, fields in (("tenant", TENANT), ("storage", STORAGE), ("deployment", deployment))]
split = [(name, fields) for name, fields in split if fields]

if mode == "verdict":
    if split:
        print("split " + " ".join(name for name, _ in split))
        for _, fields in split:
            for f in fields:
                print(detail(f))
        if shared_tenant and differs(HOST):
            print(detail(HOST) + "   (a machine name; never a split by itself)")
    elif differs(HOST):
        print("replica")
        print(detail(HOST))
    elif str(mcp.get(KEY)) != str(shell.get(KEY)):
        print("restart")
        print(f"  {KEY}:  MCP {mcp.get(KEY)} vs shell {shell.get(KEY)}")
    elif restarted_in_place():
        print("restart")
        print(f"  {START}:  MCP {mcp[START]} vs shell {shell[START]}")
    else:
        print("ok")
    for f in FIELDS:
        if (f in mcp) != (f in shell):
            side, other = ("MCP", "this shell") if f in mcp else ("this shell", "MCP")
            value = mcp.get(f) if f in mcp else shell.get(f)
            print("  UNCOMPARABLE %s:  reported by %s (%s), absent from %s — this field could not be compared" % (
                f, side, value if value not in (None, "") else "(unset)", other))
    raise SystemExit(0)

from pathlib import Path

sys.path.insert(0, os.environ["ABI_HERE"])
import gate_ledger as gl

GATE = {
    "tenant": gl.BACKEND_IDENTITY_TENANT_DIFFERS,
    "storage": gl.BACKEND_IDENTITY_STORAGE_DIFFERS,
    "deployment": gl.BACKEND_IDENTITY_DEPLOYMENT_DIFFERS,
}


def text(value):
    return None if value is None else str(value)


findings = [
    gl.make_finding(
        GATE[name],
        subject=os.environ["ABI_SERVER_URL"],
        evidence={"differing": fields, "mcp_workspace_id": text(mcp.get("workspace_id")),
                  "mcp_storage_root": text(mcp.get("storage_root"))},
        observed={f: {"mcp": text(mcp.get(f)), "shell": text(shell.get(f))} for f in FIELDS if differs(f)},
        remedy=REMEDY[name],
        draft_ref=None,
    )
    for name, fields in split
]
evaluated = list(GATE.values())
art = os.environ.get("ABI_ART", "")
try:
    if not art:
        raise OSError("no artifacts directory resolved")
    applied, lines = gl.settle(findings, art=Path(art), evaluated=evaluated, findings_out=None)
except (OSError, ValueError) as exc:
    # A ledger that cannot be read lifts nothing: skipping it would drop a hold without a word.
    applied = gl.apply(findings, [], evaluated=evaluated)
    lines = [gl.finding_line(f) for f in findings]
    lines.append("  the gate ledger could not be read (%s: %s), so no overrule applies" % (type(exc).__name__, exc))
print("\n".join(lines))
raise SystemExit(1 if applied.holding else 0)
'

_abi_verdict() {
  _plurai_stdlib PLURAI_IDENTITY_KEY="$PLURAI_IDENTITY_KEY" -c "$_ABI_JUDGE_PY" verdict "$1" "$2"
}

# Exit 0 only when nothing the split minted still holds: every differing class is advisory and the
# plugin's gate ledger lifts each one. Tenant and storage findings hold whatever the ledger says.
_abi_settle() {
  _plurai_stdlib ABI_HERE="$_ABI_SCRIPTS" ABI_ART="${ART:-}" ABI_SERVER_URL="$SERVER_URL" PLURAI_IDENTITY_KEY="$PLURAI_IDENTITY_KEY" \
    -c "$_ABI_JUDGE_PY" settle "$1" "$2"
}

# The two lines that tell the operator what to DO about a split. Every mode that refuses ends
# with them, and three copies of the one paragraph an operator acts on is three chances for it
# to drift.
_abi_split_remedy() {
  echo "  Everything generated through MCP lives on the FIRST; this command would run against the SECOND and get clean 404s that look like expired ids." >&2
  echo "  Fix: align PLURAI_URL in this shell with the one Claude Code was launched with and retry. If the MCP side is the wrong one, restart Claude Code with the intended PLURAI_URL — it cannot be changed mid-session." >&2
}

# The HOST-grain refusal, shared by HOST mode and by the armed-record mode. $1 the MCP-side
# host, $2 HOW that side was learned (the two modes know it differently, and an operator who
# cannot tell which is talking cannot tell which side to fix), $3 this shell's host.
_abi_stop_split_hosts() {
  echo "STOP(backend-identity): SPLIT BACKENDS." >&2
  echo "  MCP server is bound to : $1   ($2)" >&2
  echo "  this shell resolves to : $3   (SERVER_URL=$SERVER_URL)" >&2
  _abi_split_remedy
}

# ARM. Best-effort in EVERY direction: an unresolved agent root, a read-only tree or a full disk
# costs the next helper its cross-check, never this call its flow. A plain redirect rather than
# a tmp+rename because the degrade is the right one either way — a truncated or half-written
# record fails to parse and reads as UNARMED, which is exactly the state it would be in.
_abi_arm() {
  # resolve-workdir.sh points $PLURAI_DIR at an unwritable sentinel when it refuses to name an
  # agent; PLURAI_AGENT_RESOLVED is the state to read, never the path (see that script). The
  # directory test is separate and not redundant: on the no-agent-yet branch the state is 1 and
  # the plurai_sim/ the path names has not been created yet.
  [ "${PLURAI_AGENT_RESOLVED:-0}" = 1 ] && [ -d "$PLURAI_DIR" ] || return 0
  local binding
  binding="$(_abi_binding)"
  [ -n "$binding" ] || return 0
  # `2>/dev/null` BEFORE the redirect, not after: redirections are applied left to right, so a
  # failing `>` on the other order reports "cannot create" on a stderr that has not been silenced
  # yet — a bash error line in the middle of a flow, for a best-effort write.
  ABI_IDENTITY="$1" ABI_SERVER_URL="$SERVER_URL" ABI_BINDING="$binding" \
  _plurai_stdlib PLURAI_IDENTITY_KEY="$PLURAI_IDENTITY_KEY" -c '
import json, os, sys

identity = json.loads(os.environ["ABI_IDENTITY"])
json.dump({"binding": os.environ["ABI_BINDING"],
           "server_url": os.environ["ABI_SERVER_URL"],
           "instance_id": identity.get(os.environ["PLURAI_IDENTITY_KEY"])}, sys.stdout)
' 2>/dev/null > "$PLURAI_DIR/.backend_identity" || true
}

# READ. `<server_url><TAB><instance_id>` for THIS binding, or a non-zero return when there is
# nothing to compare against: no binding key, no record, an unreadable or malformed one, or one
# armed by a DIFFERENT process. All the same answer — unarmed — and none of them is an error.
_abi_armed_record() {
  [ "${PLURAI_AGENT_RESOLVED:-0}" = 1 ] || return 1
  local binding
  binding="$(_abi_binding)"
  [ -n "$binding" ] || return 1
  _plurai_stdlib ABI_BINDING="$binding" -c '
import json, os, sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        record = json.load(handle)
except Exception:
    raise SystemExit(1)
if not isinstance(record, dict) or record.get("binding") != os.environ["ABI_BINDING"] or not record.get("server_url"):
    raise SystemExit(1)
print("%s\t%s" % (record["server_url"], record.get("instance_id") or ""))
' "$PLURAI_DIR/.backend_identity" 2>/dev/null
}

# Mode selection is by SHAPE, and anything object-shaped that will not parse is refused rather
# than guessed at. Falling back to host mode there would hand `_plurai_host_of` a brace-string
# and compare it against a real hostname — a false split on every run, which is the one outcome
# worse than not checking. (The live shape for that is a Python-style dict repr, single quotes,
# which is how an MCP result often reads on screen.)
_abi_arg="${1:-}"
case "$_abi_arg" in
  '') _abi_mode=empty ;;
  # An UNSUBSTITUTED placeholder (`<backend_identity() object>` — the literal every call site
  # carries for the model to replace) is not object-shaped, so without this case it would fall to host
  # mode, compare a garbage host against the real one, and abort the ingest with
  # "SPLIT BACKENDS" — a false alarm whose message is the most alarming one the script owns.
  # Treated as the documented no-argument case, which compares against the identity the
  # session armed rather than warning and continuing.
  '<'*'>') _abi_mode=empty ;;
  \{*) _abi_mode="$(printf '%s' "$_abi_arg" | _plurai_stdlib PLURAI_IDENTITY_KEY="$PLURAI_IDENTITY_KEY" -c '
import json, os, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("malformed"); raise SystemExit(0)
print("precise" if isinstance(d, dict) and d.get(os.environ["PLURAI_IDENTITY_KEY"]) else "malformed")
' 2>/dev/null)" ;;
  *) _abi_mode=host ;;
esac

if [ "$_abi_mode" = empty ]; then
  # The arm-time record stands in for the argument nobody passed. This is the branch that
  # carries the six helpers with no model in the loop, AND the one the unsubstituted
  # placeholder now falls through to instead of continuing unverified.
  if _abi_record="$(_abi_armed_record)" && [ -n "$_abi_record" ]; then
    _abi_armed_url="${_abi_record%%$'\t'*}"
    _abi_armed_id="${_abi_record#*$'\t'}"
    _abi_mcp_host="$(_plurai_host_of "$_abi_armed_url")"
    _abi_shell_host="$(_plurai_host_of "$SERVER_URL")"
    if [ "$_abi_mcp_host" != "$_abi_shell_host" ]; then
      _abi_stop_split_hosts "$_abi_mcp_host" \
        "recorded by this Claude Code process's latest verified backend_identity() check, at $_abi_armed_url${_abi_armed_id:+ — instance_id $_abi_armed_id}" \
        "$_abi_shell_host"
      echo "  If the RECORD is the stale side, re-arm it: call backend_identity() and source this guard with its object — a verified check rewrites $PLURAI_DIR/.backend_identity." >&2
      return 1 2>/dev/null || exit 1
    fi
  elif [ "$_abi_quiet" != 1 ]; then
    echo "NOTE(backend-identity): no MCP identity to compare against — shelling out to $SERVER_URL unverified. Call the read-only backend_identity() MCP tool and pass its object as the argument; the first call that does also ARMS \$PLURAI_DIR/.backend_identity, which is what this check reads when no argument is passed. (A backend that predates that tool degrades here by design.)" >&2
  fi

elif [ "$_abi_mode" = malformed ]; then
  echo "NOTE(backend-identity): the argument looks like an object but is not JSON carrying '${PLURAI_IDENTITY_KEY}' — continuing unverified rather than guessing. Pass the backend_identity() result as JSON (double quotes), or pass a bare host/URL for the coarser host-only check." >&2

elif [ "$_abi_mode" = precise ]; then
  # ── PRECISE mode ──
  ABI_RC="$PLURAI_RC_TRANSPORT"; ABI_BODY=""; ABI_DETAIL=""
  # Exit 3 from the resolver is the client pin refusing a usable runner: a hard stop, like a
  # split. Any other failure leaves the runner empty, and the check degrades to the WARNING
  # below: a project with no usable Python SDK (not installed yet, or too old) must
  # not be stopped by a preflight it cannot satisfy. The resolver's stderr is shown only when
  # it did not resolve; on success it is a run-log note this preflight would repeat every call.
  _abi_err="$(mktemp "${TMPDIR:-/tmp}/abi.XXXXXX" 2>/dev/null)" || _abi_err=""
  _abi_bin="$(_plurai_stdlib "$_abi_here/resolve_plurai.py" --require "backend identity" 2>"${_abi_err:-/dev/null}")" || { [ $? -eq 3 ] && { [ -z "$_abi_err" ] || { cat "$_abi_err" >&2; rm -f "$_abi_err"; }; return 1 2>/dev/null || exit 1; }; }
  if [ -n "$_abi_err" ]; then
    [ -n "$_abi_bin" ] || cat "$_abi_err" >&2
    rm -f "$_abi_err"
  fi
  if [ -n "$_abi_bin" ]; then
    _abi_fetch_identity "$_abi_bin"
    # The runner exits with the credit code and the kind's sentence for a credit refusal the gateway answered on this route.
    # Nothing behind the gateway can run, so this is a stop, never an identity that "could not be read": the sentence goes
    # out as it came.
    if [ "$ABI_RC" = "$_ABI_RC_CREDIT_REFUSED" ] && [ -n "$ABI_DETAIL" ]; then
      echo "CREDIT-STOP: $ABI_DETAIL" >&2
      return 1 2>/dev/null || exit 1
    fi
    # A verdict program that fails or prints nothing has compared nothing, and that is never a
    # reason to continue: it STOPS like a split (only `degrade` — an identity this shell could not
    # read — and `norunner` continue unverified, below).
    _abi_out="$(_abi_verdict "$_abi_arg" "$ABI_BODY")" || _abi_out=""
  else
    _abi_out=norunner
  fi
  _abi_call="${_abi_out%%$'\n'*}"
  _abi_detail="$(printf '%s' "$_abi_out" | tail -n +2)"
  case "$_abi_call" in
    ok)
      # `ok` means every field BOTH sides reported agrees — which is not the same as "the two
      # identities agree", and the gap is exactly where `source_id` lives during a rollout. Say
      # which fields nobody could compare; do not turn it into a stop, because "one side is
      # older" and "these are different boxes" are indistinguishable from here.
      if [ -n "$_abi_detail" ]; then
        echo "NOTE(backend-identity): the two identities agree on every field BOTH sides report — but not every field was reported by both:" >&2
        printf '%s\n' "$_abi_detail" >&2
        echo "  A field only one side carries cannot verify or refute a split. If it is source_id, the source-patch axis is DARK for this call: neither side can show what code the other is running." >&2
      fi
      # The one place the binding is OBSERVED and CONFIRMED, so the one place that may record
      # it. `restart` arms too: a restarted process is the same deployment at the same host,
      # which is the whole grain of what is stored.
      _abi_arm "$_abi_arg"
      ;;
    split\ *)
      # Whether the split stops is the gate catalogue's call, not this script's: a tenant or store
      # field always holds, and a deployment-only split holds until the plugin overrules it in its
      # gate ledger (${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md, "Plugin-local findings").
      if _abi_settled="$(_abi_settle "$_abi_arg" "$ABI_BODY")"; then
        echo "NOTE(backend-identity): the MCP server and this shell are on different deployments (build, source, or a host no shared tenant ties to the other) with no tenant or store field differing — proceeding on the plugin's recorded overrule." >&2
        printf '%s\n' "$_abi_detail" >&2
        printf '%s\n' "$_abi_settled" >&2
        # An overruled deployment split arms like `restart`: no tenant or store field differs, same URL.
        _abi_arm "$_abi_arg"
      else
        echo "STOP(backend-identity): SPLIT BACKENDS — the MCP server and this shell are on different deployments." >&2
        printf '%s\n' "$_abi_detail" >&2
        printf '%s\n' "$_abi_settled" >&2
        echo "  this shell: SERVER_URL=$SERVER_URL   (MCP's URL was fixed at session launch from PLURAI_URL — a bash export cannot move it)" >&2
        case " $_abi_call " in
          *" tenant "*|*" storage "*) _abi_split_remedy ;;
          *) echo "  Only the deployment differs (build, source, or a host no shared tenant ties to the other) — no tenant or store field does: a rolling deploy, or a second box nothing tells apart. Re-run this check once a rollout settles, or, after confirming the ids this flow uses resolve through SERVER_URL, overrule the advisory finding above with scripts/gate_ledger.py and re-run." >&2 ;;
        esac
        return 1 2>/dev/null || exit 1
      fi
      ;;
    replica)
      # A shared tenant, and every store and deployment field agrees; only the machine name
      # differs. That is one backend served by more than one host, and never stops a flow.
      echo "NOTE(backend-identity): tenant, store and build agree and only the machine name differs — read as a replica of one backend, not a split." >&2
      printf '%s\n' "$_abi_detail" >&2
      echo "  Proceed. If an id minted through MCP then reads as missing here, the two hosts do not share a store — suspect that first." >&2
      _abi_arm "$_abi_arg"
      ;;
    restart)
      # Same deployment on every field that survives a restart — so this is one box that
      # restarted, not two boxes. Saying "wrong backend" here would accuse the operator of
      # something that did not happen.
      echo "NOTE(backend-identity): same deployment, a different box or process — $SERVER_URL restarted since this MCP session began." >&2
      printf '%s\n' "$_abi_detail" >&2
      echo "  Not a wrong-backend split: organization/workspace/source/build/storage_root/hostname all agree. Proceed — but ids minted before the restart may be gone, so treat an unknown job/set id as lost to the restart rather than mistyped." >&2
      _abi_arm "$_abi_arg"
      ;;
    norunner)
      echo "WARNING(backend-identity): no \`plurai\` runner with \`backend identity\` is installed for this repo, so $SERVER_URL$PLURAI_IDENTITY_PATH was NOT read — continuing UNVERIFIED." >&2
      echo "  The split-brain check this guard exists for is DISARMED for this call. Installing the Python runner (scripts/resolve_plurai.py --install-line prints the command) enables it." >&2
      echo "  If the next call behaves as though it hit a different box (an id that reads as expired, an artifact that is not there), suspect that FIRST." >&2
      ;;
    degrade)
      # The SDK's exit table for `backend identity`: TRANSPORT is no response after its one retry or
      # any other HTTP status (its one-line reason says which), ROUTE_MISSING no identity route (or
      # not an identity), CREDENTIALS a rejected bearer, UPGRADE_REQUIRED a client the backend refuses,
      # REFUSED a target that is not a usable URL.
      case "${ABI_RC}:${ABI_DETAIL}" in
        "$PLURAI_RC_TRANSPORT":*"did not respond"*) _abi_no_response=1 ;;
        *) _abi_no_response=0 ;;
      esac
      if [ "$ABI_RC" = "$PLURAI_RC_UPGRADE_REQUIRED" ]; then
        # The backend refuses every call of this SDK, so continuing means the next call is refused too: the guard stops. The
        # text is the one `_client_handshake.py` renders for an SDK command's refusal, which also files it for a headless
        # run driver.
        printf '%s\n' "$ABI_ERR" | _plurai_stdlib "$_abi_here/_client_handshake.py" sdk-refusal >&2 2>/dev/null \
          || printf '%s\n' "$PLURAI_SDK_REFUSAL_FALLBACK" >&2  # SDK_REFUSAL_FALLBACK in _client_handshake.py
        return "$PLURAI_RC_VERSION_REFUSED" 2>/dev/null || exit "$PLURAI_RC_VERSION_REFUSED"
      elif [ "$_abi_no_response" = 1 ]; then
        # Wrapped into this file's own 3-line shape: every other message here is hand-wrapped
        # with a two-space continuation, and the actionable half must not be the tail of a
        # 400-character paragraph.
        echo "WARNING(backend-identity): $SERVER_URL$PLURAI_IDENTITY_PATH did not respond at all (two attempts, 5s each, 1s apart) — continuing UNVERIFIED." >&2
        echo "  This is TRANSPORT, not a backend that lacks the identity route: the split-brain check this guard exists for is DISARMED for this call." >&2
        echo "  If the next call behaves as though it hit a different box (an id that reads as expired, an artifact that is not there), suspect that FIRST." >&2
      else
        echo "NOTE(backend-identity): could not read an identity from $SERVER_URL$PLURAI_IDENTITY_PATH (${ABI_DETAIL:-plurai backend identity exited ${ABI_RC}}) — continuing unverified. A missing route or an HTML answer means this backend predates the identity route; 401/403 means PLURAI_TOKEN does not match the backend SERVER_URL points at." >&2
      fi
      ;;
    *)
      echo "STOP(backend-identity): the identity comparison did not run — the plugin's python3 could not produce a verdict, so a split cannot be excluded." >&2
      echo "  this shell: SERVER_URL=$SERVER_URL   plugin python3=$(_plurai_guard_python 2>/dev/null || echo '(none: /usr/bin, /opt/homebrew/bin and /usr/local/bin have none)')" >&2
      echo "  Fix: install a python3 (3.9 or newer) at /usr/bin/python3 and retry; the plugin does not take one from PATH." >&2
      return 1 2>/dev/null || exit 1
      ;;
  esac

else
  # ── HOST mode ── a bare host/URL. No identity fetch: the route's `hostname` is the MACHINE's,
  # not the URL's, so it cannot canonicalize this comparison and must not be dragged into it.
  _abi_mcp_host="$(_plurai_host_of "$_abi_arg")"
  _abi_shell_host="$(_plurai_host_of "$SERVER_URL")"
  if [ "$_abi_mcp_host" != "$_abi_shell_host" ]; then
    _abi_stop_split_hosts "$_abi_mcp_host" \
      "fixed at session launch from PLURAI_URL — a bash export cannot move it" \
      "$_abi_shell_host"
    return 1 2>/dev/null || exit 1
  fi
fi

unset _abi_no_response _abi_here _abi_arg _abi_mode _abi_out _abi_call _abi_detail _abi_mcp_host _abi_shell_host ABI_RC ABI_BODY ABI_DETAIL
unset _abi_bin _abi_err _ABI_RC_CREDIT_REFUSED ABI_ERR
unset _abi_record _abi_armed_url _abi_armed_id _abi_quiet _abi_settled
