#!/usr/bin/env python3
"""Record, in `plurai.config.yaml`, which PRODUCTION hosts the agent under test can still reach.

The SDK blocks every declared production host inside a guarded Python agent's process during a run,
and holds a run whose declared host is reachable from this machine when nothing enforces that, until
the finding is overruled — isolation in a simulation is BY CONFIG (the harness redirects a base URL),
so an endpoint the agent holds in its own CODE is not redirected, and a simulated user then drives a
real refund, a real delete, a real email. Both read `egress.production_hosts`, and the only step
that has ever read the agent's source is the scan. This script is how the scan's
reading becomes the declaration.

TWO THINGS IT REFUSES TO DO, both because the failure is silent:

* **An absent key cannot say "we looked".** The SDK reports an absent declaration as
  `not_declared` — which reads as "checked and clean" and means "never checked". So a scan that
  found nothing writes `production_hosts: []` WITH a `discovery` block saying it swept and what
  it swept, and a scan that could not decide writes `not_determinable` with the reason.
* **A host the SDK cannot parse is a hole in the isolation.** Its parser drops a host that is
  not a hostname or an IP literal (an unresolvable typo would come back "does not resolve" and be
  recorded as positive evidence of isolation), so that host is neither blocked nor probed, and an
  entry with no `source` leaves its finding with no line to act on. Both rules are mirrored here,
  and hosts are written already SPLIT into host + scheme + port, so the SDK's own splitting is an
  identity on what this writes.

  That mirror is the cross-repo seam: `plurai/egress.py` owns the contract and this owns the
  producer. `scripts/tests/test_record_egress.py` carries a fixture table that is byte-identical
  to the SDK's — that table is the seam's one owner — and something now COMPARES the two copies:
  `.github/workflows/cross-repo-parity.yml`'s `plurai-sdk` job checks out the SDK and byte-
  compares the marked block, failing on the skip as well as on a divergence. So a change to one
  side alone is red in this repo, not a thing to remember. Copy the whole block; never edit one.

THIS SCRIPT DECLARES FACTS; THE SDK DECIDES PROBE POLICY. It used to invent a port from the
scheme — `https://api.acme.com` was written out as `port: 443` — and an explicit `port:`
OUTRANKS the SDK's default probe set, so the producer silently reinstated the exact hole the
consumer had just closed: a production host answering on 80 passed a preflight that only ever
tried 443. So a `port` is written ONLY when the declaration actually carried one, and the URL's
`scheme` is written BESIDE the host instead. The scheme is not decoration — it is what lets the
SDK read a portless `https://…` as declaring the WEB PORT FAMILY (80 and 443) rather than as a
guess, which is what keeps `isolated` reachable on the default path the scan skill teaches.

COMMENTS SURVIVE. `plurai.config.yaml` is authored, committed and heavily commented; a
round-trip through a YAML dumper would erase all of it. So the block is spliced as TEXT between
markers, the way the .gitignore block is, and an `egress:` key found OUTSIDE the markers is
refused rather than overwritten — it is somebody's hand-written declaration.

Usage:
    record_egress.py --config <plurai.config.yaml> --declaration <file|->

The declaration document:

    status: declared | none_discovered | not_determinable
    agent_runtime: python_child | subprocess    # required — where the agent executes (below)
    scanned: [agent.py, config/settings.py]     # what was READ, required; recorded relative to the agent folder
    scan_root: ../src/agent                     # optional — where the agent's source lives
    why: "one line"                             # required unless status is `declared`
    hosts:                                      # required (non-empty) iff status is `declared`
      - host: api.example.com                   # or `https://api.example.com/v1`, or `host:port`
        source: svc/payments.py:42              # REQUIRED — the SDK's finding names this line
        port: 8443                              # optional; overrides a port in `host`
      - env: CRM_URL                            # OR the env var the agent reads its backend URL from;
        source: agent/crm.py:12                 #   resolved at run time inside the agent process
    allow_hosts: [mock.internal]                # optional — declared but deliberately reachable
                                                # (omit: the prior block's list is kept; `[]` clears it)

`agent_runtime` is `subprocess` when the harness starts the process that runs the agent (a server,
a CLI, a non-Python runtime), else `python_child`. The SDK's `guarded` verdict claims the declared
hosts are blocked inside the agent process, which is only true of `python_child`; `subprocess`
sends the run to the reachability probe instead.

Each written host row carries `host` + `source`, plus `scheme` when the declaration was a URL
and `port` when a port was actually written. A host with neither a port nor an http/https
scheme (a Postgres, a Kafka, an SMTP relay) can be PROBED but never PROVEN isolated — the SDK
guesses 80/443 for it — so this says so on stderr rather than leaving it to be discovered.

Exit 0 — written (or already byte-identical). 2 — the declaration is invalid or unreadable.
3 — the config carries an `egress:` key this script does not own, or a corrupted managed block.
4 — the config could not be read or written.
"""

from __future__ import annotations

import argparse
import ipaddress
import ntpath
import os
import posixpath
import re
import sys
from collections.abc import Sequence
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import _platform  # noqa: E402 - the plugin's one answer to "is this native Windows", and its UNC gate

#: The managed block's delimiters. `BEGIN_PREFIX` is what LOCATES an existing block, and it is
#: rev-agnostic on purpose: matching the full `rev N` line would make the next rev bump read every
#: config the plugin itself wrote as hand-authored, and refuse to touch it.
BEGIN_PREFIX = '# BEGIN plurai-simulation egress rev'
BEGIN = f'{BEGIN_PREFIX} 1 (managed — rewritten by /simulation-scan-agent)'
END = '# END plurai-simulation egress'

STATUSES = ('declared', 'none_discovered', 'not_determinable')
#: Mirrors `plurai/egress.py::AgentRuntime`, which reads any other value as undeclared and reports it.
AGENT_RUNTIMES = ('python_child', 'subprocess')

#: Mirrors `plurai/egress.py::_HOSTNAME`. Permissive about labels, strict about everything else.
_HOSTNAME = re.compile(r'^(?=.{1,253}$)([A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?)(\.[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?)*$')
#: Mirrors `plurai/egress.py::_ALWAYS_LOCAL`. The loopback family IS the mock; declaring one as
#: production produces a declaration the preflight can never probe.
_ALWAYS_LOCAL = frozenset({'localhost', '127.0.0.1', '::1', '0.0.0.0'})  # noqa: S104 - a host to EXCLUDE, never one to bind
#: Every key a host row may carry. A row is REFUSED when it carries anything else, rather than
#: having it dropped: the note this script prints for a portless non-web host used to advise
#: "add `port:` (or `ports:`)", and `ports:` is not a key — a declaration that took the advice was
#: written out without it and the same note fired again on the next run, which is a producer
#: teaching an operator a spelling it silently ignores. `port` is singular on purpose (the SDK
#: probes one), so a host reachable on several gets a row each.
_HOST_KEYS = frozenset({'host', 'source', 'port'})
_ENV_KEYS = frozenset({'env', 'source'})
_ENV_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

#: The schemes whose port family the SDK probes by default (80 and 443). Named here only so the
#: "you declared no port" note below can tell a web host from a Postgres one — this script does
#: not turn a scheme into a port, which is the whole point of T-01.
_WEB_SCHEMES = frozenset({'http', 'https'})
#: A top-level `egress:` the markers do not own — somebody's hand-written declaration.
_UNMANAGED_EGRESS = re.compile(r'^egress\s*:', re.MULTILINE)


def split_host(raw: str) -> tuple[str, int | None, str]:
    """`(host, port, scheme)` for a bare host, `host:port`, or a full URL. Mirrors `plurai/egress.py`.

    `port` is a port SOMEBODY WROTE and nothing else. A scheme is not a port declaration:
    `https://api.example.com` says which protocol the agent speaks there, not that 443 is the
    only door. Inventing 443 here did not merely lose information — it OVERRODE the consumer,
    whose `_entry_ports` ranks an explicit `port:` above its own default probe set, so a
    production host answering on 80 sailed through a preflight that only ever tried 443.

    The scheme therefore comes out SEPARATELY, and the consumer decides what it implies. That
    split is the load-bearing half: without the scheme every URL-declared host would become a
    port the SDK had to guess, and `isolated` — the one positive safety claim this system makes —
    would stop being reachable on the default path the scan skill teaches.
    """
    text = raw.strip()
    if '://' in text:
        parsed = urlsplit(text)
        return (parsed.hostname or '').lower(), parsed.port, parsed.scheme.lower()
    if text.startswith('['):
        head, _, tail = text.partition(']')
        port = tail.lstrip(':')
        return head[1:].lower(), int(port) if port.isdigit() else None, ''
    if text.count(':') == 1 and text.rsplit(':', 1)[1].isdigit():
        host, port = text.rsplit(':', 1)
        return host.lower(), int(port), ''
    return text.lower(), None, ''


def is_addressable(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return bool(_HOSTNAME.match(host))
    return True


def validate(declaration: Any) -> tuple[dict, list[str]]:
    """`(block, findings)` — the `egress` mapping to write, and every reason it cannot be."""
    findings: list[str] = []
    if not isinstance(declaration, dict):
        return {}, [f'the declaration must be a mapping, got {type(declaration).__name__}.']

    status = str(declaration.get('status') or '').strip()
    if status not in STATUSES:
        findings.append(f'`status` is {status or "missing"!r}; it must be one of {", ".join(STATUSES)}.')

    # Required under EVERY status: the verdict needs it even when nothing is declared yet.
    agent_runtime = declaration.get('agent_runtime')
    if agent_runtime not in AGENT_RUNTIMES:
        findings.append(
            f'`agent_runtime` is {agent_runtime if agent_runtime is not None else "missing"!r}; it must be one of {", ".join(AGENT_RUNTIMES)} — '
            '`subprocess` when the harness starts the process that runs the agent (a server, a CLI, a non-Python runtime).'
        )

    scanned = declaration.get('scanned') or []
    if not isinstance(scanned, list) or not scanned or any(not str(s).strip() for s in scanned):
        # Required under EVERY status. "We found none" without "here is what we read" is an
        # assertion, not a sweep, and it is the assertion the SDK will print as a safety claim.
        findings.append('`scanned` must be a non-empty list of the files/directories that were read.')

    # OPTIONAL, and validated only for shape: it names a directory on the machine the RUN happens
    # on, which this script has no standing to resolve. A wrong one degrades in the SDK to the
    # roster and then to the marker walk — the pre-existing behaviour — so a stale value costs a
    # warning, never a run.
    scan_root_raw = declaration.get('scan_root')
    if scan_root_raw is not None and (not isinstance(scan_root_raw, str) or not scan_root_raw.strip()):
        findings.append('`scan_root`, when present, must be a non-empty string: the directory the agent\'s source lives in, relative to the config.')
    scan_root = scan_root_raw.strip() if isinstance(scan_root_raw, str) and scan_root_raw.strip() else ''

    why = str(declaration.get('why') or '').strip()
    if status in ('none_discovered', 'not_determinable') and not why:
        findings.append(f'`why` is required under status `{status}` — an empty declaration with no reason is indistinguishable from an absent one.')

    raw_hosts = declaration.get('hosts') or []
    if not isinstance(raw_hosts, list):
        findings.append(f'`hosts` must be a list, got {type(raw_hosts).__name__}.')
        raw_hosts = []
    if status == 'declared' and not raw_hosts:
        findings.append('status is `declared` but `hosts` is empty. Use `none_discovered` (with a `why`) to record a sweep that found nothing.')
    if status != 'declared' and raw_hosts:
        findings.append(f'status is `{status}` but `hosts` carries {len(raw_hosts)} entr(ies). A host that was found makes the status `declared`.')

    hosts: list[dict] = []
    for i, entry in enumerate(raw_hosts):
        where = f'hosts[{i}]'
        if isinstance(entry, dict) and 'env' in entry and 'host' not in entry:
            # The customer's backend URL read from an env var: only the agent process holds its value.
            var, source = str(entry.get('env') or '').strip(), str(entry.get('source') or '').strip()
            if set(map(str, entry)) - _ENV_KEYS or not _ENV_NAME.match(var) or not source:
                findings.append(f'{where} must be exactly `env: <VARIABLE_NAME>` and `source: <file:line>`.')
            else:
                hosts.append({'env': var, 'source': source})
            continue
        if not isinstance(entry, dict) or not str(entry.get('host') or '').strip():
            findings.append(f'{where} must be a mapping with a non-empty `host`.')
            continue
        raw_host = entry['host']
        unsupported = sorted(set(map(str, entry)) - _HOST_KEYS)
        if unsupported:
            findings.append(
                f'{where} ({raw_host}) carries key(s) this producer does not write: '
                f'{", ".join(unsupported)}. Supported: {", ".join(sorted(_HOST_KEYS))}. A key that is '
                'dropped in silence is a declaration the author believes they made — write `port:` '
                '(singular) once per row, or a row per port.'
            )
            continue
        source = str(entry.get('source') or '').strip()
        if not source:
            findings.append(f'{where} ({raw_host}) has no `source`. The SDK names it in its finding; a finding nobody can trace to a line is one nobody acts on.')
        try:
            host, port, scheme = split_host(str(raw_host))
        except ValueError as exc:
            # `urlsplit(...).port` RAISES on an out-of-range port. An invalid declaration is a
            # finding, never a traceback: the exit code is what the caller maps.
            findings.append(f'{where} declares {raw_host!r}, which is not a host this can split ({exc}).')
            continue
        declared_port = entry.get('port')
        if declared_port is not None:
            port = declared_port
        # `isinstance(True, int)` is True in Python, so a bare int test writes `port: true` into
        # the config; and the SDK probes whatever number is there, so a negative or out-of-range
        # one is a probe that can never succeed and an isolation claim nobody made.
        if port is not None and (isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535):
            findings.append(f'{where} ({raw_host}) declares port {port!r}; it must be an integer in 1..65535.')
            continue
        if host in _ALWAYS_LOCAL:
            findings.append(f'{where} declares {host!r}, which is the loopback family — that is the MOCK, never production, and the preflight never probes it.')
            continue
        if not is_addressable(host):
            findings.append(f'{where} declares {raw_host!r}, which does not split into a hostname or an IP literal. The SDK drops it, so that host is neither blocked nor probed.')
            continue
        row = {'host': host, 'source': source}
        if scheme:
            # The scheme is a FACT the declaration carried, and the ONLY one that lets the SDK
            # read a portless URL as naming the web port family rather than as a guess. Drop it
            # and `isolated` becomes unreachable for every URL-declared host — which is the
            # default path — so the fix for the invented port would have flipped every currently
            # green preflight to `unprovable`.
            row['scheme'] = scheme
        if port is not None:
            # ONLY a port somebody wrote. See `split_host`: a scheme is not a port declaration,
            # and an explicit `port:` outranks the SDK's whole default probe set.
            row['port'] = port
        hosts.append(row)

    # `allow_hosts` is the ONE key that consents to a host LIVE, so it is held to the same standard
    # as the hosts it exempts — not merely to being non-blank. The SDK compares it by hostname
    # alone, so a port written here is dropped, and that is said rather than done quietly.
    raw_allow = declaration.get('allow_hosts') or []
    allow: list[str] = []
    if not isinstance(raw_allow, list):
        findings.append(f'`allow_hosts` must be a list, got {type(raw_allow).__name__}.')
        raw_allow = []
    for i, entry in enumerate(raw_allow):
        where = f'allow_hosts[{i}]'
        if not str(entry).strip():
            findings.append(f'{where} is blank.')
            continue
        try:
            host, port, _scheme = split_host(str(entry))
        except ValueError as exc:
            findings.append(f'{where} declares {entry!r}, which is not a host this can split ({exc}).')
            continue
        if not is_addressable(host):
            findings.append(f'{where} declares {entry!r}, which is not a hostname or an IP literal — an exemption the SDK cannot match is an exemption that does nothing.')
            continue
        if host in _ALWAYS_LOCAL:
            findings.append(f'{where} declares {host!r}; the loopback family is ALREADY excluded by the SDK and never needs an exemption.')
            continue
        if port is not None:
            findings.append(f'{where} declares a port on {entry!r}. The SDK exempts by HOST, so the exemption would silently cover every port — write the bare host if that is what you mean.')
            continue
        allow.append(host)

    if findings:
        return {}, findings

    discovery: dict[str, Any] = {'status': status, 'scanned': [str(s) for s in scanned]}
    if why:
        discovery['why'] = why
    block: dict[str, Any] = {'production_hosts': hosts, 'discovery': discovery, 'agent_runtime': agent_runtime}
    if scan_root:
        # WHERE THE AGENT IS, when the roster alone would not say it. The SDK's own hint walk
        # roots itself at the first build-file marker above the harness, and an agent directory
        # holding only `agent.py`, `README.md` and `requirements.txt` inside a monorepo has none
        # — so the walk climbed to the MONOREPO and spent its whole file budget on the parent
        # repo's workflows and `.env`, reporting 14 "undeclared" hosts that belong to neither the
        # agent nor the run while the agent's own files went unread. `scanned` normally answers
        # this by itself (its common parent is where the scan actually read); this key is for the
        # cases it cannot — a roster of one file in a subdirectory, or a source tree the scan
        # sampled rather than walked.
        block['scan_root'] = scan_root
    if allow:
        block['allow_hosts'] = allow
    return block, []


#: How far above the agent folder the no-pin fallback looks for the repository root.
_REPO_ROOT_MAX_HOPS = 16


def normalize_scanned(
    scanned: Sequence[str],
    *,
    agent_dir: str,
    bases: Sequence[str],
    exists: Any = os.path.exists,
) -> tuple[list[str], int, list[str]]:
    """`(entries, rebased, outside)` — the roster rewritten relative to ``agent_dir``.

    The SDK resolves `discovery.scanned` against the config's folder and that folder's PARENT and keeps
    only what lands inside the parent (`plurai/egress.py::_resolve_roster`) — so ``agent_dir`` is
    `plurai_sim/..`. A scan reads through tools whose paths are relative to the SESSION root, and
    with `plurai_sim/` below the repo root (`/simulation-scan-agent src/pkg/main.py`) a roster
    written that way resolved NONE of its entries and the scan root fell back to the derived walk.

    ONE base for the whole roster, by quorum over ``bases`` (ties to the earlier one, ``agent_dir``
    first), the way the SDK picks between its own two: a scan writes its roster in one convention,
    and a per-entry first hit would rebase a `README.md` that happens to exist one level up. When
    ``agent_dir`` wins, relative entries are written exactly as declared. An entry that exists
    nowhere, a glob, or a path this machine may not touch (a UNC share) stays as written; an
    absolute one becomes relative when it lies inside ``agent_dir``. An entry outside it is written
    `../`-relative — the SDK's out-of-scope spelling, which it discards from the root.
    """
    win = _platform.WINDOWS
    pm = ntpath if win else posixpath

    def key(path: str) -> str:
        return pm.normcase(pm.normpath(path))

    def inside(path: str) -> bool:
        p, root = key(path), key(agent_dir).rstrip(pm.sep)
        return p == root or p.startswith(root + pm.sep)

    def present(path: str) -> bool:
        # The gate comes FIRST: on Windows a model-written `\\host\share` path is never touched.
        if not _platform.is_local_drive_path(path, within=bases):
            return False
        try:
            return bool(exists(path))
        except OSError:
            return False

    def relative(path: str) -> str | None:
        try:
            out = pm.relpath(path, agent_dir)
        except ValueError:  # another drive: there is no relative spelling
            return None
        return out.replace('\\', '/') if win else out

    def canonical(path: str) -> str:
        # Off Windows the bases are real paths, so an absolute entry spelled through a symlinked
        # directory (macOS `/tmp`) is resolved the same way. On Windows a `subst` or mapped drive
        # would resolve to a UNC path, so it is only normalised.
        path = pm.normpath(path)
        return path if win else posixpath.join(os.path.realpath(posixpath.dirname(path)), posixpath.basename(path))

    def kind(entry: str) -> str:
        if not win:
            return 'abs' if posixpath.isabs(entry) else 'rel'
        parsed = PureWindowsPath(entry)
        if parsed.drive and (parsed.root or parsed.drive.startswith('\\\\')):
            return 'abs'
        return 'odd' if parsed.drive or parsed.root else 'rel'  # `C:x`, `\x`: no single meaning

    kinds = [kind(e) for e in scanned]
    out = list(scanned)
    rebased = 0
    rel_idx = [i for i, k in enumerate(kinds) if k == 'rel']
    scores = [sum(1 for i in rel_idx if present(pm.join(base, scanned[i]))) for base in bases]
    best = max(range(len(bases)), key=lambda b: (scores[b], -b)) if bases else 0
    for i, entry in enumerate(scanned):
        if kinds[i] == 'rel':
            if best and scores[best] and present(pm.join(bases[best], entry)):
                new = relative(canonical(pm.join(bases[best], entry)))
                if new is not None:
                    out[i] = new
                    rebased += 1
            elif win:
                out[i] = entry.replace('\\', '/')  # a committed config is read on POSIX CI too
        elif kinds[i] == 'abs' and (inside(entry) or present(entry)):
            new = relative(pm.normpath(entry) if inside(entry) else canonical(entry))
            if new is not None:
                out[i] = new
                rebased += out[i] != scanned[i]
    outside = [e for e in out if e == '..' or e.startswith(('../', '..\\')) or kind(e) == 'abs']
    return out, rebased, outside


def _roster_bases(config_path: Path) -> list[str]:
    """Where a roster entry may have been written relative to, nearest first: the agent folder, the
    config's own folder (the SDK's other anchor), the session's project dir (its pin), the cwd — and,
    only when there is no pin, the nearest repository root above the agent folder. Real paths off
    Windows, as the SDK compares them; on Windows only absolute + normalised (see `canonical` above)."""
    win = _platform.WINDOWS
    pm = ntpath if win else posixpath

    def canon(path: str) -> str:
        return pm.normpath(os.path.abspath(path)) if win else os.path.realpath(path)

    config_dir = canon(str(config_path.parent))
    agent_dir = pm.dirname(config_dir)
    found: list[str] = [agent_dir, config_dir]
    project = None
    try:
        import session_pin  # noqa: PLC0415 - hooks/ is on sys.path; only this step reads the pin

        pin = session_pin.read()
        project = pin.project_dir if pin is not None and pin.project_dir else None
    except (ImportError, OSError, ValueError):
        project = None
    if project:
        found.append(canon(project))
    try:
        found.append(canon(os.getcwd()))
    except OSError:
        pass
    if not project:
        here = agent_dir
        for _ in range(_REPO_ROOT_MAX_HOPS):
            if _platform.is_local_drive_path(here, within=(agent_dir,)) and os.path.exists(pm.join(here, '.git')):
                found.append(here)
                break
            parent = pm.dirname(here)
            if parent == here:
                break
            here = parent
    unique: list[str] = []
    for base in found:
        if pm.normcase(base) not in {pm.normcase(b) for b in unique}:
            unique.append(base)
    return unique


def _record_roster_relative_to_agent(block: dict, config_path: Path) -> None:
    """Rewrite `block['discovery']['scanned']` in place (see `normalize_scanned`) and say what changed.

    Only for a config in `plurai_sim/`: that is the layout whose parent is the agent folder. A named env
    (`plurai_sim/envs/<name>/`) or a workspace-root config puts the SDK's base elsewhere, and a rewrite
    there would change the roster's bytes for no gain or tie it to this checkout's directory name.
    """
    discovery = block['discovery']
    if config_path.absolute().parent.name != 'plurai_sim':
        return
    bases = _roster_bases(config_path)
    agent_dir = bases[0]
    entries, rebased, outside = normalize_scanned(discovery['scanned'], agent_dir=agent_dir, bases=bases)
    discovery['scanned'] = entries
    if rebased:
        print(f'record_egress: recorded {rebased} `scanned` entr(ies) relative to the agent folder {agent_dir}, the folder the SDK resolves the roster against.', file=sys.stderr)
    if outside:
        print(
            f'record_egress: NOTE — {len(outside)} `scanned` entr(ies) lie OUTSIDE the agent folder {agent_dir} (first: {outside[0]}). '
            'The SDK drops them from the roster, which leaves it PARTIAL and withholds a clean egress verdict; list only what you '
            'read inside the agent folder, or name a wider root in `scan_root`.',
            file=sys.stderr,
        )


def render_block(block: dict) -> str:
    body = yaml.safe_dump({'egress': block}, sort_keys=False, allow_unicode=True, default_flow_style=False).rstrip('\n')
    return f'{BEGIN}\n{body}\n{END}\n'


def managed_span(text: str) -> tuple[int, int] | None:
    """`(start, end)` of the managed block, `None` when there is none — and it RAISES on a
    half-block rather than treating it as absent.

    A `BEGIN` whose `END` was lost (a hand-edit, a bad merge) used to fall through to the append
    branch and produce a SECOND `egress:` key, unbounded on every re-run. PyYAML is last-wins on
    duplicate keys, so the read-back guard passed and the SDK read the newest block while the
    file grew a marker per scan.
    """
    start = text.find(BEGIN_PREFIX)
    end = text.find(END)
    if start < 0 and end < 0:
        return None
    if start < 0 or end < 0 or end < start:
        raise ValueError(
            'the managed egress block is corrupted — '
            + ('its `# END plurai-simulation egress` marker is missing' if end < 0 else 'it has an `# END` marker with no `# BEGIN`' if start < 0 else 'its `# END` marker comes before its `# BEGIN`')
            + '. Repair or delete the block by hand; splicing into it would leave the config with two `egress:` keys and the SDK reading whichever came last.'
        )
    return start, end + len(END)


def splice(text: str, rendered: str) -> tuple[str, str]:
    """`(new_text, action)` — replace the managed block, or append it. Never touches anything else."""
    span = managed_span(text)
    if span is not None:
        start, end = span
        return text[:start] + rendered + text[end:].removeprefix('\n'), 'replaced'
    if text.endswith('\n\n') or not text.strip():
        separator = ''
    elif text.endswith('\n'):
        separator = '\n'
    else:
        separator = '\n\n'
    return text + separator + rendered, 'appended'


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--config', required=True, help='path to plurai.config.yaml')
    ap.add_argument('--declaration', required=True, help="the declaration document, or '-' for stdin")
    args = ap.parse_args(argv)

    config_path = Path(args.config)
    try:
        original = config_path.read_text(encoding='utf-8')
    except OSError as exc:
        print(f'record_egress: cannot read {config_path} ({exc}).', file=sys.stderr)
        return 4

    try:
        raw = sys.stdin.read() if args.declaration == '-' else Path(args.declaration).read_text(encoding='utf-8')
    except OSError as exc:
        print(f'record_egress: cannot read the declaration {args.declaration} ({exc}).', file=sys.stderr)
        return 2
    try:
        declaration = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        print(f'record_egress: the declaration is not parseable YAML ({exc}).', file=sys.stderr)
        return 2

    block, findings = validate(declaration)
    if findings:
        print(f'record_egress: {len(findings)} problem(s) — NOTHING was written:', file=sys.stderr)
        for finding in findings:
            print(f'  - {finding}', file=sys.stderr)
        return 2

    # Over the config MINUS the managed span, so the guard keeps working once a block EXISTS.
    # Keyed on `BEGIN not in original`, it fired on the first scan only: from the second onwards a
    # hand-added `egress:` was spliced past, the file carried two top-level keys, PyYAML took the
    # last (this script's), and the operator's declared production host stopped being probed.
    try:
        span = managed_span(original)
    except ValueError as exc:
        print(f'record_egress: {config_path}: {exc}', file=sys.stderr)
        return 3
    outside = original if span is None else original[: span[0]] + original[span[1] :]
    if _UNMANAGED_EGRESS.search(outside):
        # Somebody wrote this by hand. Overwriting it would delete a declaration an operator made
        # deliberately, and the SDK would go on probing whatever this script decided instead.
        print(
            f'record_egress: {config_path} carries a top-level `egress:` key OUTSIDE the managed block. '
            'Refusing to overwrite a hand-authored declaration — merge it into the block by hand, or delete it to let the scan manage it.',
            file=sys.stderr,
        )
        return 3

    # `allow_hosts` is the OPERATOR's waiver; everything else in the block is the scan's. A
    # declaration that omits the key carries the prior block's list forward, through the SAME
    # validator; one that carries the key (even `[]`) replaces it.
    if span is not None and 'allow_hosts' not in declaration:
        try:
            prior = (yaml.safe_load(original[span[0] : span[1]]) or {}).get('egress') or {}
        except (yaml.YAMLError, AttributeError) as exc:
            print(f'record_egress: {config_path}: the managed egress block does not parse ({exc}); repair it by hand.', file=sys.stderr)
            return 3
        kept = prior.get('allow_hosts') if isinstance(prior, dict) else None
        if kept:
            block, findings = validate({**declaration, 'allow_hosts': kept})
            if findings:
                print(f'record_egress: the carried-forward allow_hosts {kept!r} is invalid — NOTHING was written:', file=sys.stderr)
                for finding in findings:
                    print(f'  - {finding}', file=sys.stderr)
                return 2
            print(f'record_egress: kept the operator-declared allow_hosts {block["allow_hosts"]} (declare `allow_hosts: []` to clear).', file=sys.stderr)

    # HERE, after the `allow_hosts` carry-forward: that branch re-runs `validate()` from the raw
    # declaration, so a rewrite made any earlier would be lost on every re-scan that keeps a waiver.
    _record_roster_relative_to_agent(block, config_path)

    updated, action = splice(original, render_block(block))
    if updated == original:
        print(f'record_egress: {config_path} already carries this exact block; nothing to do.', file=sys.stderr)
        return 0

    try:
        parsed = yaml.safe_load(updated)
    except yaml.YAMLError as exc:
        print(f'record_egress: the spliced config does not parse as YAML ({exc}); {config_path} is UNCHANGED.', file=sys.stderr)
        return 2
    if not isinstance(parsed, dict) or parsed.get('egress') != block:
        # A config whose own `egress` does not read back is one the SDK will parse differently
        # from what was intended — and a host it cannot read is neither blocked nor probed.
        print(f'record_egress: the spliced config does not read back the declared block; {config_path} is UNCHANGED.', file=sys.stderr)
        return 2

    temp = config_path.with_suffix(config_path.suffix + '.egress-tmp')
    try:
        temp.write_text(updated, encoding='utf-8')
        temp.replace(config_path)
    except OSError as exc:
        # A read-only tree or a full disk must not leave a stray half-file beside the config an
        # operator would later mistake for a backup.
        temp.unlink(missing_ok=True)
        print(f'record_egress: cannot write {config_path} ({exc}); it is UNCHANGED and NOTHING was declared.', file=sys.stderr)
        return 4

    status = block['discovery']['status']
    count = len(block['production_hosts'])
    print(f'record_egress: {action} the egress block in {config_path} — status={status}, {count} production host(s) declared.', file=sys.stderr)
    for row in block['production_hosts']:
        scheme = f'{row["scheme"]}://' if 'scheme' in row else ''
        port = f':{row["port"]}' if 'port' in row else ''
        print(f'  - {scheme}{row["host"]}{port}  ({row["source"]})' if 'host' in row else f'  - env:{row["env"]}  ({row["source"]})', file=sys.stderr)
    # A host with neither a port nor a web scheme is PROBED (on 80 and 443) but can never be
    # PROVEN isolated: a refusal on the web ports says nothing about the Postgres on 5432 the
    # agent actually talks to, so the SDK downgrades the verdict to `unprovable`. Said HERE, at
    # the moment the declaration is written, rather than left to surface in a scored run's egress verdict.
    guessed = [row for row in block['production_hosts'] if 'host' in row and 'port' not in row and row.get('scheme') not in _WEB_SCHEMES]
    if guessed:
        print(
            f'record_egress: NOTE — {len(guessed)} declared host(s) name neither a port nor an http/https scheme, so the SDK '
            'probes 80 and 443 as a GUESS and reports `unprovable` rather than `isolated` for them: '
            + ', '.join(row['host'] for row in guessed)
            + '. Add `port:` — Postgres 5432, Kafka 9092, SMTP 587, Redis 6379, a gRPC port — to make isolation provable. '
            'ONE port per row (there is no `ports:` key; a declaration carrying one is refused rather than dropped), '
            'so a host reachable on several gets a row each.',
            file=sys.stderr,
        )
    for host in block.get('allow_hosts', []):
        # The list that consents to a host LIVE is printed, not merely written. It used to appear
        # nowhere an operator would see it.
        print(f'  ! allow-listed, so a run neither blocks nor holds it: {host}', file=sys.stderr)
    if status == 'not_determinable':
        print(
            f'record_egress: WARNING — status is `{status}`: nothing is probed, and the SDK preflight and the run report say '
            f'`{status}` (the scan could not decide), never a clean sweep. Say so in the scan summary, and resolve the endpoints '
            'before a scored run.',
            file=sys.stderr,
        )
    elif status == 'none_discovered':
        # CLOSED. The SDK reads this block as a BRANCH: an empty `production_hosts`
        # beside `discovery.status: none_discovered` reaches its own `swept_clean` status, and the
        # run report renders "swept N file(s), nothing found" instead of the word that means
        # nobody looked. Stated here because this script is what writes the evidence that makes
        # the distinction possible.
        print(
            f'record_egress: the sweep is RECORDED ({len(block["discovery"]["scanned"])} path(s)); the SDK preflight reads this block and reports '
            '`swept_clean`, not `not_declared`. An older SDK still collapses the two — check the run report says '
            '`swept_clean` and not `not_declared (0 declared / ...)`.',
            file=sys.stderr,
        )
    elif status != 'declared':
        print(
            f'record_egress: NOTE — discovery status {status!r} is not one the SDK reads as a sweep, so the empty declaration '
            'reports as `not_declared` ("never looked"). Only `none_discovered` reaches `swept_clean`.',
            file=sys.stderr,
        )
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
