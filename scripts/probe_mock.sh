#!/usr/bin/env bash
# probe_mock.sh — probe a deployed mock with the backend bearer, without a hand-written `curl`.
#
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/probe_mock.sh" "$(cat "$WORKDIR/.mock_service_url")" [--timing N] [--tool NAME --args JSON]
#
# `scripts/probe_mock.py` does the work (see its docstring for the wire, the exit codes and when the
# bearer is withheld). This wrapper exists because the bearer has to reach that program some way, and
# the ways open to a skill are worse: a `curl -H "Authorization: Bearer …"` puts the token in a command
# line every process on the machine can read, and `curl` or `python3` is whatever the command's PATH
# names, which would receive the token. Here the program runs under the plugin's fixed system python,
# isolated, and inherits the bearer from the environment (never as an argument).
#
# Sourcing the resolver first means a bearer that would go to a host the session is not bound to is
# refused before any request, exactly as for every other script that sends it.

set -u

_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=resolve-plurai-urls.sh
source "$_here/resolve-plurai-urls.sh" || exit 1

_plurai_stdlib --bearer "$_here/probe_mock.py" "$@"
