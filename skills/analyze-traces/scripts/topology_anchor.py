#!/usr/bin/env python3
"""Print the active env's `scenario_variables.yaml`, or nothing when there is none. `/analyze-traces` runs it for its exit code.

    source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/skills/analyze-traces/scripts/topology_anchor.py"

The anchor is `<workdir>/scenario_variables.yaml` of the ACTIVE env — `plurai_sim/` in bare mode,
`plurai_sim/envs/<alias>/` in named-env mode — and no other. The backend files the grounding ledger
and the trace brief per `workspace_id` (`<bench>.<alias>` under a named env), and the file is one of
the agent descriptions the trace part is delivered on top of, so the exit code is the check that the
shell is on the one env those belong to. Merging sibling envs' files is not a substitute: YAML keeps the
last of a repeated top-level key, so a merge silently reduces to whichever file sorts last.

The file is printed unchanged: the helper neither parses nor repairs it.

Exit 0 = printed the file, or printed nothing because the active env has none · 2 = no workdir resolved · 3 = the resolved workdir is not an existing
directory (a stale `.active-env`, or no `plurai_sim/` anywhere): the wrong env, which is not the same
as an env without an anchor · 4 = the anchor exists but cannot be read (an I/O error, a dangling symlink, a directory of that name, or bytes that are
not UTF-8): the env has an anchor the analysis cannot use, which is not the same as an env without one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
import harness_fingerprint  # noqa: E402 - sibling of the plugin's scripts/: the workdir resolver cascade lives there

ANCHOR = 'scenario_variables.yaml'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workdir', help='defaults to $WORKDIR, else the resolver cascade')
    args = parser.parse_args(argv)

    workdir = harness_fingerprint.resolve_workdir(args.workdir)
    if not workdir.is_dir():
        sys.stderr.write(f'topology_anchor: workdir {workdir} is not a directory: wrong env, not an env without an anchor\n')
        return 3
    anchor = workdir / ANCHOR
    # `is_symlink()` keeps a dangling link and `exists()` a directory out of the "no anchor" branch: both are an anchor
    # the analysis cannot use, which the read below reports.
    if anchor.is_symlink() or anchor.exists():
        # Bytes, not text: a text-mode read folds CRLF to LF, and the anchor goes out exactly as written.
        try:
            raw = anchor.read_bytes()
            raw.decode('utf-8')
        except (OSError, UnicodeDecodeError) as exc:
            sys.stderr.write(f'topology_anchor: anchor unreadable: {anchor}: {type(exc).__name__}: {exc}\n')
            return 4
        sys.stdout.flush()
        sys.stdout.buffer.write(raw)
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
