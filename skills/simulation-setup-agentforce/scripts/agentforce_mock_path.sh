#!/usr/bin/env bash
# The mock path's lifecycle for a hosted Agentforce agent: bring the rewired copy up, take it
# down, delete it.
#
#   ./plurai_sim/agentforce_mock_path.sh up [rewire args...]
#   ./plurai_sim/agentforce_mock_path.sh down
#   ./plurai_sim/agentforce_mock_path.sh clean
#
# `up` deploys and activates a rewired COPY of the agent that calls the plurai mock service
# instead of the org's real actions; the customer's own agent is never touched. `down` deactivates
# the copy and leaves everything in place. `clean` deletes it from the org and removes the
# generated tree.
#
# This file, `rewire_agentforce.py` and `resolve-plurai-urls.sh` are copied into the project
# rather than run from the plugin, because teardown has to work in a checkout that no longer has
# the plugin installed. Between them they use only the standard library and the `sf` CLI, for
# the same reason: the liveness probes run in `rewire_agentforce.py --probe`, not in the SDK.
#
# The environment supplies the plugin's own PLURAI_URL and PLURAI_TOKEN:
# `resolve-plurai-urls.sh` turns the first into SERVER_URL, the Plurai backend REST base the callout
# reaches, and the second is the bearer the credential principal carries. A `.env_id` file under
# the workdir holds the mock env id the plugin's env generation created. SF_ORG_ALIAS names the
# org; unset, the `sf` CLI's own default target org is used.

# `paths` and `names` assign AGENT_ROOT/DX_ROOT/PACKAGE_DIR/WORKDIR and
# copy/permset/apiver/ec/principal/agent_user by `eval`-ing the generator's output, so every use of
# them below is a bare name shellcheck cannot trace to an assignment.
# shellcheck disable=SC2154

set -euo pipefail

_af_dirname() {  # dirname(1) by parameter expansion: no PATH lookup decides where this script's files are
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
here="$(CDPATH='' cd -- "$(_af_dirname "${BASH_SOURCE[0]}")" && pwd -P)"
rewire="${here}/rewire_agentforce.py"

# The rewrite is stdlib-only and some of its calls hold the org bearer, so it runs under a fixed
# system interpreter, isolated, never a `python3` that PATH names (`PATH=/tmp/x:$PATH <script>` would
# run its own program with the bearer, and `eval` the output of the calls below). Without `--bearer`
# the bearer is removed from its environment; the two calls that send or print it say so and inherit it
# (never as an argument). This file is copied into the project and runs after the plugin is gone, so it
# carries its own copy of the lookup. None found answers 127.
py() {
    local interpreter candidate keep=""
    [ "${1:-}" = --bearer ] && { keep=1; shift; }
    if [ -x /usr/bin/cygpath ]; then
        # Native Windows has no fixed system python: the copied `trusted-python.sh` beside this file names one.
        # shellcheck source=/dev/null
        if [ -f "${here}/trusted-python.sh" ] && . "${here}/trusted-python.sh" && _plurai_on_windows; then
            interpreter="$(_plurai_trusted_python "")" ||
                { echo "no trusted python on this Windows host (install the py launcher for all users); this script does not take one from PATH" >&2; return 127; }
        elif [ ! -f "${here}/trusted-python.sh" ]; then
            echo "no trusted python on this Windows host: copy trusted-python.sh beside this script; it does not take one from PATH" >&2
            return 127
        fi
    fi
    for candidate in /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
        [ -z "${interpreter:-}" ] || break
        if [ -x "$candidate" ]; then interpreter="$candidate"; break; fi
    done
    [ -n "${interpreter:-}" ] || { echo "no python3 at /usr/bin, /opt/homebrew/bin or /usr/local/bin; this script does not take one from PATH" >&2; return 127; }
    if [ -n "$keep" ]; then
        /usr/bin/env "$interpreter" -I "$@"
    else
        /usr/bin/env -u PLURAI_TOKEN "$interpreter" -I "$@"
    fi
}

# Every org call goes through here so the target-org flag is spelled once. An unset alias is not an
# error: `sf` resolves the target org from its own config, which is what a single-org project relies
# on. The branch is what keeps an empty expansion off the command line — bash before 4.4, which is
# what macOS ships, treats one as an unbound variable under `set -u` and kills the run at its first
# org call. `sf` accepts its flags in any position, so appending is safe.
sf_org() {
    if [ -n "${SF_ORG_ALIAS:-}" ]; then
        sf "$@" -o "$SF_ORG_ALIAS"
    else
        sf "$@"
    fi
}

# The rewrite owns the layout. Which directory holds `sfdx-project.json` and which package
# directory that manifest declares differ between checkouts, so a walk repeated here is a second
# answer that can disagree with the tree the rewrite writes.
paths() {
    eval "$(py "$rewire" --project-dir "$here" --paths)"
    gen_meta="${PACKAGE_DIR}/generated"
    gen_out="${DX_ROOT}/generated"
}

# The generator owns the naming, so this script reads the names from it rather than spelling them
# again: a name written twice can deploy one set of files and delete another.
names() { eval "$(py "$rewire" --project-dir "$AGENT_ROOT" --names)"; }

# The rewrite reads the bearer from the environment, not from an argument: an argument would leave
# the org token in the process table for the length of the call, readable by anyone on the machine.
# No answer prints 000, so a caller under `set -e` prints a code and decides rather than ending the
# run on a bare non-zero exit with nothing on screen.
probe() {
    local code
    code="$(py --bearer "$rewire" --probe "$1")" || code=000
    printf '%s' "${code:-000}"
}

#: The name the operator grants the bring-up by, in `.host_effects_consent` (`granted: a, b`).
BRING_UP_GRANT=route_smoke

# The rewrite owns the consent grammar, so the grant is read there, once.
bring_up_granted() { py "$rewire" --project-dir "$AGENT_ROOT" --granted "$BRING_UP_GRANT"; }

cmd_up() {
    # The resolver is the one place a Plurai backend REST base is derived, and it refuses a remote base
    # with no bearer. Sourcing it here rather than at file scope keeps teardown working in a shell
    # that never had the plugin's env exported.
    # shellcheck source=/dev/null
    source "${here}/resolve-plurai-urls.sh" || exit 1
    : "${PLURAI_TOKEN:?export it from the plugin env; the credential principal carries it as the org bearer}"
    local base="${SERVER_URL%/}"

    # Salesforce dials this base from its own data centre. A harness that moves the host's own
    # calls to a loopback edge names the org-facing form here, and that one wins: the rewire
    # refuses a non-https base, and no org can reach 127.0.0.1.
    base="${E2E_ORG_CALLOUT_BASE:-$base}"
    base="${base%/}"

    paths
    names

    # Deploying and activating the copy is an effect on the customer's org, so it runs only on the
    # operator's named grant, before anything leaves this machine.
    bring_up_granted || {
        echo "up deploys and activates a copy of the agent in your org, and that was not granted. To allow it, add ${BRING_UP_GRANT} to the granted: line of ${WORKDIR}/.host_effects_consent (granted: ${BRING_UP_GRANT})" >&2
        exit 3
    }

    # The copy's own actions run as the bundle's default agent user, and without the grant to that
    # user they are refused at run time. Refusing here costs nothing; refusing after the deploy
    # leaves a half-built agent in the org.
    [ -n "${agent_user:-}" ] || {
        echo "the bundle declares no access.default_agent_user, so ${permset} cannot be granted to the agent's own user" >&2
        exit 1
    }

    # Generation comes first because it is local, free, and it is what refuses a non-HTTPS base or
    # a missing env id — no point warming a sandbox for a URL the callout could never use. It also
    # prints the actions that stay real, which the developer needs whatever happens next.
    py "$rewire" --project-dir "$AGENT_ROOT" --base-url "$base" "$@"

    # Warm the sandbox before any Apex touches it. A cold start can outrun the two-minute cap
    # Salesforce puts on a callout, and `/healthz` is anonymous on the proxy, so this warms the
    # whole path without depending on a deployed env. A 503 never resolves by waiting, so the loop
    # is bounded and prints what it saw.
    local attempt code
    echo "warming ${base}/healthz"
    for attempt in 1 2 3 4 5; do
        code="$(probe "${base}/healthz")"
        echo "  attempt ${attempt}: ${code}"
        [ "$code" = 200 ] && break
        [ "$attempt" = 5 ] && { echo "the Plurai backend never answered 200 on /healthz" >&2; exit 1; }
        sleep 5
    done

    # The proxy answers 503 while the env's sandbox restarts, which clears in seconds, so that one
    # code is worth waiting on. 410 says the env has no live deployment at all, and no amount of
    # waiting produces one.
    local env_id
    env_id="$(py "$rewire" --project-dir "$AGENT_ROOT" --print-env-id)"
    for attempt in 1 2 3 4 5; do
        code="$(probe "${base}/mock/${env_id}/health")"
        echo "env ${env_id} health attempt ${attempt}: ${code}"
        [ "$code" = 503 ] || break
        [ "$attempt" = 5 ] || sleep 5
    done
    if [ "$code" = 410 ]; then
        echo "the mock env has no live deployment; run the plugin's env generation, then up again" >&2
        exit 1
    fi
    [ "$code" = 200 ] || { echo "the mock env is not reachable (${code})" >&2; exit 1; }

    # The token reaches the request body through the environment only, so it is never an argument
    # and never a file.
    local body
    body="$(py --bearer "$rewire" --project-dir "$AGENT_ROOT" --credential-body)"

    cd "$DX_ROOT"

    # The credentials go first: the permission set names the principal, so the principal has to
    # exist before the grant that references it is validated.
    sf_org project deploy start --source-dir "${gen_meta}/externalCredentials" --source-dir "${gen_meta}/namedCredentials"

    # This is the only place the token enters the ORG, and it goes in on stdin through `--body -`,
    # so it is never a command-line argument and never a file. The response is captured rather than
    # printed: a success envelope is a credential response and has no business on screen. Both
    # streams are captured, because the CLI puts the part that explains a refusal on stderr.
    push_credential() {
        printf '%s' "$body" | sf_org api request rest "/services/data/v${apiver}/named-credentials/credential" --method "$1" --body - 2>&1
    }
    local post_out put_out
    if ! post_out="$(push_credential POST)"; then
        # A principal that already carries the parameter is updated rather than created, and the
        # Connect API spells an update PUT. The POST envelope is printed before the retry and the
        # PUT envelope after it: when both refuse, the two answers rarely say the same thing and
        # the first one is usually the one that names the cause.
        echo "the org refused to create the credential:" >&2
        printf '%s\n' "$post_out" >&2
        echo "retrying as an update" >&2
        if ! put_out="$(push_credential PUT)"; then
            echo "the org refused to update the credential:" >&2
            printf '%s\n' "$put_out" >&2
            exit 1
        fi
    fi
    echo "pushed the org token to ${ec}-${principal}"

    # Classes before the permission set that grants access to them, and both before the bundle
    # whose actions target them.
    sf_org project deploy start --source-dir "${gen_meta}/classes"
    sf_org project deploy start --source-dir "${gen_meta}/permissionsets"

    # Without the grant the callout is refused before it leaves the org, and it is needed twice
    # over: the health script runs as the CLI user, the agent's own actions run as the bundle's
    # default agent user. `--on-behalf-of` replaces the target user rather than adding to it, so
    # the grant is issued once per user.
    grant() {
        local out rc=0
        out="$(sf_org org assign permset --json --name "$permset" "$@")" || rc=$?
        [ "$rc" = 0 ] && return 0
        # A repeated bring-up re-issues a grant that is already there. The platform names that
        # refusal two ways — a duplicate value on the field, or a duplicate PermissionSetAssignment
        # record — and those two are the only failures absorbed here.
        if printf '%s\n' "$out" | grep -qiE 'duplicate value found|duplicate permissionsetassignment'; then
            echo "the ${permset} grant is already in place"
            return 0
        fi
        printf '%s\n' "$out" >&2
        return 1
    }
    grant
    grant --on-behalf-of "$agent_user"

    # The verdict. The script throws on anything but 200, so a failed callout stops the run here
    # rather than activating an agent that cannot reach the mock env.
    sf_org apex run --file "${gen_out}/apex/plurai_mock_health.apex"

    # The bundle is never deployed on its own. Its `target` names a BotVersion that exists only
    # once the copy is published, and the org refuses a bundle whose target it cannot resolve.
    # Publishing reads the bundle out of the DX project, creates the Bot, BotVersion and GenAi*
    # records from it, and deploys the bundle last. Retrieval is skipped because it would write
    # that runtime metadata into the tracked source tree.
    sf_org agent validate authoring-bundle --api-name "$copy"
    sf_org agent publish authoring-bundle --api-name "$copy" --skip-retrieve

    # Activation refuses when every version of the copy is already in the target state, which is
    # what a repeated bring-up looks like from the org's side. That refusal is absorbed; every
    # other envelope is printed and stops the run.
    local activate_out
    if activate_out="$(sf_org agent activate --json --api-name "$copy")"; then
        echo "${copy} is active"
    elif already_active "$activate_out"; then
        echo "${copy} is already active"
    else
        printf '%s\n' "$activate_out" >&2
        exit 1
    fi
    record_agent_id

    echo "the rewired copy ${copy} is active against mock env ${env_id}"
}

# The harness drives the copy over the Agent API, which addresses an agent by its BotDefinition
# id rather than its developer name. That lookup happens here, once, because this is where the
# org login already is: the harness then needs no `sf` CLI to start a run.
record_agent_id() {
    local query found
    query="SELECT Id FROM BotDefinition WHERE DeveloperName = '${copy}'"
    found="$(sf_org data query --json -q "$query" 2>/dev/null \
        | sed -n 's/.*"Id" *: *"\([a-zA-Z0-9]\{15,18\}\)".*/\1/p' | head -n 1)" || found=''
    if [ -n "$found" ] && printf '%s' "$found" > "${WORKDIR}/.mock_agent_id"; then
        echo "recorded the copy's agent id in ${WORKDIR}/.mock_agent_id"
        return 0
    fi
    # The id is the only handle the harness has on the copy, so an unrecorded one is a bring-up
    # that produced an agent nothing can drive.
    echo "${copy} is published and active, but its agent id was not recorded in ${WORKDIR}/.mock_agent_id." >&2
    echo "run this command again once the copy is visible to sf data query, or export SF_MOCK_AGENT_ID" >&2
    echo "with the copy's id before the run." >&2
    exit 1
}

# Whether a `sf agent deactivate --json` envelope says the copy has no active version, which is
# what an already-inactive copy looks like. Older CLIs name it NoVersionsAvailableError; current
# ones (2.133.x) answer DeactivationFailed with the NoActiveVersionError text in cause/actions.
# Any other DeactivationFailed (a permission refusal, say) is a real failure and is not matched.
already_inactive() {
    printf '%s\n' "$1" | grep -qE '"name": *"(NoVersionsAvailableError|NoActiveVersionError)"' && return 0
    printf '%s\n' "$1" | grep -q '"name": *"DeactivationFailed"' \
        && printf '%s\n' "$1" | grep -qi 'No active version'
}

# The same reading for a `sf agent activate --json` envelope: no version is left to activate, which is
# what an already-active copy looks like. The CLI names it NoVersionsAvailableError ("All versions are
# already in the target state"); a CLI that wraps it in ActivationFailed, the way 2.133.x wraps the
# deactivation refusal, keeps that text in cause/actions. Any other ActivationFailed is a real failure.
already_active() {
    printf '%s\n' "$1" | grep -qE '"name": *"NoVersionsAvailableError"' && return 0
    printf '%s\n' "$1" | grep -q '"name": *"ActivationFailed"' \
        && printf '%s\n' "$1" | grep -qiE 'NoVersionsAvailable|already in the target state'
}

cmd_down() {
    paths
    names
    cd "$DX_ROOT"

    # Deactivation refuses when every version is already inactive, which is what a repeated
    # teardown looks like. That one error name is absorbed; everything else stops the run.
    local deactivate_out
    if deactivate_out="$(sf_org agent deactivate --json --api-name "$copy")"; then
        echo "${copy} is deactivated; its metadata is still in the org and up brings it back"
    elif already_inactive "$deactivate_out"; then
        echo "${copy} is already deactivated; its metadata is still in the org and up brings it back"
    else
        printf '%s\n' "$deactivate_out" >&2
        exit 1
    fi

    # The marker names an agent the org does not answer for, and the harness reads it without
    # asking the org whether the copy is up.
    rm -f "${WORKDIR}/.mock_agent_id"
}

# Never reached from `down`: deactivation is what protects spend, and the generated files make a
# rebuild cheap, so deletion is always something the developer asks for.
cmd_clean() {
    paths
    names
    cd "$DX_ROOT"

    # An active agent cannot be deleted, and a copy that was never deployed cannot be deactivated;
    # both leave the delete below as the step that decides, so both names are absorbed here. A copy
    # the org does not know was never published, so it has nothing minted to look up.
    local deactivate_out published=yes
    if deactivate_out="$(sf_org agent deactivate --json --api-name "$copy")"; then
        echo "${copy} is deactivated"
    elif already_inactive "$deactivate_out"; then
        echo "${copy} was not active"
    elif printf '%s\n' "$deactivate_out" | grep -qE '"name": *"AgentNotFound"'; then
        echo "${copy} was not active"
        published=no
    else
        printf '%s\n' "$deactivate_out" >&2
        exit 1
    fi

    # `publish` created the copy's runtime records (the Bot and its versions, the planner, its topics
    # and actions) under names the rewrite never wrote, so the generated manifest cannot list them.
    # The org can: the `Agent:` pseudo-type expands to exactly those records. The retrieve lands in a
    # scratch metadata dir, never the tracked tree, and its members are deleted BEFORE the generated
    # manifest, because they reference the classes that manifest deletes.
    local generated="${gen_out}/manifest/destructiveChanges.xml" minted='' scratch retrieve_out
    if [ "$published" = yes ]; then
        scratch="$(mktemp -d)"
        # shellcheck disable=SC2064  # the path is fixed now; the function's locals are gone at exit
        trap "rm -rf '${scratch}'" EXIT
        if ! retrieve_out="$(sf_org project retrieve start --json --metadata "Agent:${copy}" --target-metadata-dir "$scratch" --unzip)"; then
            echo "the org refused to list the records publish created for ${copy}, so nothing was deleted:" >&2
            printf '%s\n' "$retrieve_out" >&2
            exit 1
        fi
        py "$rewire" --project-dir "$AGENT_ROOT" --minted-manifest "$scratch" > "${scratch}/destructiveChangesPre.xml"
        if [ -s "${scratch}/destructiveChangesPre.xml" ]; then minted="${scratch}/destructiveChangesPre.xml"; fi
    fi

    # One deploy, so a refusal anywhere leaves the org as it was rather than half deleted.
    if [ -n "$minted" ]; then
        sf_org project deploy start \
            --manifest "${gen_out}/manifest/package.xml" \
            --pre-destructive-changes "$minted" \
            --post-destructive-changes "$generated"
    else
        sf_org project deploy start \
            --manifest "${gen_out}/manifest/package.xml" \
            --post-destructive-changes "$generated"
    fi

    # Print the manifests themselves rather than a summary of them, so this cannot drift from what
    # the deploy sent.
    echo "deleted from the org, by type:"
    if [ -n "$minted" ]; then print_manifest "$minted"; fi
    print_manifest "$generated"

    rm -rf "$gen_meta" "$gen_out"
    echo "the generated tree is removed"

    # The recorded id belongs to the copy this just deleted; a stale one would point the next
    # harness at an agent the org no longer has.
    rm -f "${WORKDIR}/.mock_agent_id"
}

print_manifest() {
    awk -F'[<>]' '/<members>/ { m = m (m ? ", " : "") $3 } /<name>/ { printf "  %s: %s\n", $3, m; m = "" }' "$1"
}

case "${1:-}" in
    up)    shift; cmd_up "$@" ;;
    down)  shift; cmd_down ;;
    clean) shift; cmd_clean ;;
    *)     echo "usage: ${BASH_SOURCE[0]##*/} {up [rewire args...]|down|clean}" >&2; exit 2 ;;
esac
