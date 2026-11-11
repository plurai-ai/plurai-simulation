#!/usr/bin/env python3
"""The one consent screen for a delivery that carries every source the scan found.

Delivery is per skill: the scan delivers the agent descriptions, `/analyze-traces` the trace part and
`/build-rag-corpus` the corpus part. When the scan already holds all three sources, this script delivers
them as one: it plans every part with the SDK (`--plan-only`: the real archive, its real size, posture and
content address, and not a byte sent), renders ONE screen from the planned entries, waits for the user's
approval (the skill asks it as a sanctioned question), then runs each ingest for real with the hash the plan
printed (`--expect-sha256`) and prints the combined `added` argument of ONE `ground_workspace_start` call.

    grounding_consent.py sources  --workdir D [--flowdir F]       # are all three sources found? writes the sources record
    grounding_consent.py plan     --workdir D --plurai BIN [--backend URL]
                                                                  # plan every part, print the screen and its digest
    grounding_consent.py approve  --workdir D --screen DIGEST     # record the user's approval of that screen
    grounding_consent.py deliver  --workdir D --plurai BIN        # run the ingests as approved; print ADDED=<json>
    grounding_consent.py screen   --workdir D                     # print the planned screen again

The sources are found when: the declared part is built; a trace connection is recorded (`simulate_plan.py traces
--connect`) and its credentials are in the environment; and exactly one directory knowledge base is registered
(`artifacts/corpus_parts.json`) with the scan's corpus roots and its domain file on disk. Anything else leaves
`sources` at exit 1, and every skill delivers its own part as before.

The approval binds to the digest of the planned parts. `plan` clears any earlier approval, `deliver` refuses
without one that matches the plan on disk, and a delivery consumes its approval. A part that differs from the
plan when its ingest runs for real (new bytes, a description file or a scan contract that appeared, a trace the
sink gained) makes the SDK exit 2 before it discloses or sends that part; the delivery stops there, prints
`CONSENT=ABORTED` and no `ADDED=`, and the caller sends nothing to `ground_workspace_start`. Parts delivered
before the mismatch sit in the backend's storage under their hash, in no package.

Exit codes: 0 done; 1 the sources are not all found (`sources`); 2 usage; 3 a check failed or the delivery
aborted (stated on stderr, nothing delivered); 4 the SDK cannot plan a delivery (the upgrade line is on stderr);
7 the backend refused the SDK's version (the plugin's update text is on stdout, nothing more is delivered).
Stdlib only, on the Python 3.9 floor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / 'skills' / 'analyze-traces' / 'scripts'))

import _client_handshake  # noqa: E402 - the plugin's text for a version refusal
import grounding_corpus  # noqa: E402 - the corpus part entry check and the registry
import grounding_delivery  # noqa: E402 - the description entries checked against the files on disk
import grounding_files  # noqa: E402 - the part-entry check and the atomic write
import resolve_plurai  # noqa: E402 - the upgrade line
import simulate_plan  # noqa: E402 - the recorded trace connection
from _stdio import utf8_child_text  # noqa: E402 - sibling script: UTF-8 children on Windows
import trace_delivery  # noqa: E402 - the names the traces keep through an anonymized scrub

CAPABILITY = 'plan-only-parts'
SOURCES_FILE = 'consent_sources.json'
PLAN_FILE = 'consent_plan.json'
SCREEN_FILE = 'consent_screen.txt'
APPROVAL_FILE = 'consent_approved.json'
ADDED_FILE = 'consent_added.json'
NAMES_FILE = 'declared_names.json'
DESCRIPTIONS, TRACES, CORPUS = 'descriptions', 'traces', 'corpus'
ORDER = (DESCRIPTIONS, TRACES, CORPUS)
#: The environment variables a child ingest must not see while it plans: a plan reaches no backend.
BACKEND_ENV = ('PLURAI_URL', 'PLURAI_TOKEN')
_KIND_WORDS = {'agent_description': 'agent description', 'trace_archive': 'trace archive', 'corpus_source': 'knowledge corpus'}


class Refusal(Exception):
    """A check failed; the message is the one stderr line, and nothing was delivered."""

    def __init__(self, message: str, code: int = 3, *, aborted: bool = True) -> None:
        super().__init__(message)
        self.code = code
        #: Whether an approved delivery was stopped, as against one that never started.
        self.aborted = aborted


def _fail(message: str) -> int:
    print(f'grounding_consent: {message}', file=sys.stderr)
    return 3


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, value: Any) -> None:
    grounding_files.write_atomic(path, json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + '\n')


def _lines(path: Path) -> list:
    try:
        return [line.strip() for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    except OSError:
        return []


def _art(workdir: Path) -> Path:
    return workdir / 'artifacts'


def _forget(workdir: Path, *names: str) -> None:
    for name in names:
        try:
            (_art(workdir) / name).unlink()
        except OSError:
            pass


# --- the sources -------------------------------------------------------------------------------------


def _traces_source(workdir: Path, flowdir: Optional[Path], env: Any) -> tuple:
    """`(source, None)` for a recorded trace connection whose credentials are in the environment, else `(None, why)`."""
    record = simulate_plan.traces_record(workdir, flowdir)
    provider, project = record.get('provider'), record.get('project')
    if provider not in simulate_plan.PROVIDERS or not project:
        return None, 'no trace source is connected'
    if not all((env.get(name) or '').strip() for name in simulate_plan.PROVIDER_KEYS[provider]):
        return None, f'the {simulate_plan.PROVIDERS[provider]} credentials are not in the environment'
    return {'source_kind': provider, 'project': project}, None


def _corpus_source(workdir: Path) -> tuple:
    """`(source, None)` for the one registered directory knowledge base whose roots and domain file are on disk, else `(None, why)`."""
    registry = grounding_corpus._read_registry(workdir)  # noqa: SLF001 - the registry reader is this module's sibling
    if registry is None:
        return None, 'the corpus registry cannot be read'
    if len(registry) != 1:
        return None, 'no knowledge base is registered' if not registry else 'more than one knowledge base is registered'
    row = registry[0]
    if row.get('source_kind') != 'directory':
        return None, 'the registered knowledge base is not a directory source'
    roots = _lines(workdir / '.scan_corpus_roots')
    if not roots:
        return None, 'the scan recorded no corpus roots'
    domain = _art(workdir) / 'corpus_domain.json'
    if not domain.is_file():
        return None, 'the knowledge base has no domain file'
    source = {
        'source_kind': 'directory',
        'roots': roots,
        'suffixes': _lines(workdir / '.scan_corpus_suffixes'),
        'label': row['label'],
        'kb_id': row['kb_id'],
        'kb_label': row['kb_label'],
    }
    if row.get('tool'):
        source['tool'] = row['tool']
    return source, None


def discover(workdir: Path, flowdir: Optional[Path], env: Any) -> tuple:
    """`(sources, missing)`: the sources found, by name, and a `(name, why)` pair for each that is not."""
    sources: dict = {}
    missing: list = []
    if (_art(workdir) / grounding_files.DECLARED_PART_NAME).is_file():
        sources[DESCRIPTIONS] = {}
    else:
        missing.append((DESCRIPTIONS, 'the declared part is not built'))
    for name, found in ((TRACES, lambda: _traces_source(workdir, flowdir, env)), (CORPUS, lambda: _corpus_source(workdir))):
        source, why = found()
        if source is None:
            missing.append((name, why))
        else:
            sources[name] = source
    return sources, missing


def _cmd_sources(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    flowdir = Path(args.flowdir) if args.flowdir else (Path(os.environ['FLOWDIR']) if os.environ.get('FLOWDIR') else None)
    sources, missing = discover(workdir, flowdir, os.environ)
    _forget(workdir, SOURCES_FILE, PLAN_FILE, SCREEN_FILE, APPROVAL_FILE, ADDED_FILE)
    print('FOUND=' + ','.join(name for name in ORDER if name in sources))
    for name, why in missing:
        print(f'MISSING={name}: {why}')
    if missing:
        return 1
    _write_json(_art(workdir) / SOURCES_FILE, {'version': 1, 'sources': sources})
    return 0


# --- running the ingests -----------------------------------------------------------------------------


def _load_sources(workdir: Path) -> dict:
    record = _read_json(_art(workdir) / SOURCES_FILE)
    sources = record.get('sources') if isinstance(record, dict) else None
    if not isinstance(sources, dict) or not all(name in sources for name in ORDER):
        raise Refusal(f'artifacts/{SOURCES_FILE} is missing or incomplete: run `sources` first; the per-skill delivery stands.')
    return sources


def _require_capability(plurai: str) -> None:
    try:
        proc = subprocess.run([plurai, 'capabilities'], capture_output=True, text=True, **utf8_child_text(), timeout=60, check=False)  # noqa: S603
        features = json.loads(proc.stdout).get('features') if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        features = None
    if not isinstance(features, list) or CAPABILITY not in features:
        raise Refusal(
            f'this plurai cannot plan a delivery (`plurai capabilities` lacks {CAPABILITY}), so the one-screen delivery is refused and nothing was sent. '
            f'Upgrade the SDK in the client\'s venv: `{resolve_plurai.INSTALL_LINE}`.',
            code=4,
        )


def _trace_config(source: dict, env: Any) -> dict:
    """The sink connection the export reads, from the environment: it is staged in a private temp file and never kept."""
    if source['source_kind'] == 'langsmith':
        config = {'api_key': env.get('LANGSMITH_API_KEY', ''), 'project': source['project']}
        if (env.get('LANGSMITH_ENDPOINT') or '').strip():
            config['api_url'] = env['LANGSMITH_ENDPOINT'].strip()
        return config
    config = {'public_key': env.get('LANGFUSE_PUBLIC_KEY', ''), 'secret_key': env.get('LANGFUSE_SECRET_KEY', '')}
    if (env.get('LANGFUSE_HOST') or '').strip():
        config['host'] = env['LANGFUSE_HOST'].strip()
    return config


def _declared_names(workdir: Path) -> Path:
    """Write the names the declared part carries to `artifacts/declared_names.json`; the traces keep them through a scrub."""
    part = _read_json(_art(workdir) / grounding_files.DECLARED_PART_NAME)
    try:
        keys = [entry['key'] for entry in part['entries']]
    except (KeyError, TypeError):
        raise Refusal(f'artifacts/{grounding_files.DECLARED_PART_NAME} cannot be read; build the declared part again.') from None
    path = _art(workdir) / NAMES_FILE
    grounding_files.write_atomic(path, json.dumps(trace_delivery.declared_names(keys), ensure_ascii=False) + '\n')
    return path


def _argv(workdir: Path, name: str, sources: dict, *, config: Optional[Path], names: Optional[Path], extra: list) -> list:
    """The ingest command of one source. What exists on disk NOW decides the optional inputs, so a file that appeared since the plan changes the part."""
    root, art = str(workdir), _art(workdir)
    contract = art / 'scan_contract.json'
    if name == DESCRIPTIONS:
        argv = ['ingest-descriptions', '--workspace-root', root, '--descriptions-dir', root]
        if contract.is_file():
            argv += ['--scan-contract', str(contract)]
    elif name == TRACES:
        argv = ['ingest-traces', '--workspace-root', root, '--source-kind', sources[TRACES]['source_kind'], '--source-config', str(config)]
        argv += ['--declared-names', str(names), '--out-dir', str(workdir / 'traces')]
    else:
        source = sources[CORPUS]
        argv = ['ingest-corpus', '--workspace-root', root, '--source-kind', source['source_kind']]
        for corpus_root in source['roots']:
            argv += ['--root', corpus_root]
        if contract.is_file() and contract.stat().st_size:
            argv += ['--scan-contract', str(contract)]
        elif source['suffixes']:
            argv += ['--suffixes', *source['suffixes']]
        argv += ['--label', source['label'], '--domain-file', str(art / 'corpus_domain.json'), '--out-dir', str(workdir / 'rag_corpus')]
    return argv + extra


def _run_ingest(plurai: str, argv: list, *, planning: bool, env: Any) -> tuple:
    """`(entries, None)` from one ingest's stdout, else `(None, cause)`. Its stderr (the target line, the disclosure) is passed on.

    Raises `VersionRefused` with the plugin's text when the SDK exits upgrade-required; the SDK's own words are never shown."""
    child_env = {key: value for key, value in env.items() if not (planning and key in BACKEND_ENV)}
    try:
        proc = subprocess.run([plurai, *argv], capture_output=True, text=True, **utf8_child_text(), env=child_env, check=False)  # noqa: S603
    except OSError as error:
        return None, f'{plurai} cannot run ({type(error).__name__})'
    _client_handshake.raise_if_refused(proc)
    if proc.stderr:
        sys.stderr.write(proc.stderr if proc.stderr.endswith('\n') else proc.stderr + '\n')
    if proc.returncode != 0:
        return None, f'`plurai {argv[0]}` exited {proc.returncode}'
    try:
        value = json.loads(proc.stdout)
    except ValueError:
        return None, f'`plurai {argv[0]}` printed no part entry'
    return (value if isinstance(value, list) else [value]), None


def _expectations(name: str, entries: list) -> list:
    if name == DESCRIPTIONS:
        return [arg for entry in entries for arg in ('--expect-sha256', f'{entry["name"]}={entry["sha256"]}')]
    return ['--expect-sha256', entries[0]['sha256']]


def _check_entries(name: str, entries: list, workdir: Path) -> Optional[str]:
    """The cause the entries one ingest printed are not deliverable, or None."""
    if name == DESCRIPTIONS:
        return grounding_delivery.checked_descriptions(workdir, entries)[1]
    if len(entries) != 1:
        return f'printed {len(entries)} part entries, not one'
    problem = grounding_files.check_part_entry(entries[0], 'trace_archive') if name == TRACES else grounding_corpus.check_entry(entries[0])
    return f'printed an entry that {problem}' if problem else None


# --- the plan and the screen -------------------------------------------------------------------------


def _plain(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if key != 'planned'}


def digest_of(plan: dict) -> str:
    """The content address of the plan's parts and backend: what the approval binds to."""
    body = json.dumps({'backend': plan.get('backend'), 'parts': plan['parts']}, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return 'sha256:' + hashlib.sha256(body.encode('utf-8')).hexdigest()


def _size(count: int) -> str:
    if count < 1024:
        return f'{count} B'
    value = float(count)
    for unit in ('KB', 'MB', 'GB'):
        value /= 1024
        if value < 1024 or unit == 'GB':
            return f'{value:.1f} {unit}'
    return f'{count} B'


def _posture(entry: dict) -> str:
    if entry['kind'] == 'agent_description':
        return 'sent as written'
    return 'anonymized before it leaves' if entry.get('posture') == 'anonymized' else 'raw, not anonymized'


def _title(source: str, entry: dict, sources: dict) -> str:
    if source == TRACES:
        return entry.get('name') or f'traces ({sources[TRACES]["source_kind"]} project {sources[TRACES]["project"]})'
    return entry.get('name') or entry.get('label') or source


def render_screen(plan: dict) -> str:
    """The one consent screen: every planned part with its size and posture, and the digest the approval names."""
    rows = [(part['title'], _KIND_WORDS.get(part['entry']['kind'], part['entry']['kind']), _size(part['entry']['bytes']), _posture(part['entry'])) for part in plan['parts']]
    header = ('Part', 'Kind', 'Size', 'Posture')
    widths = [max(len(str(row[i])) for row in [header, *rows]) for i in range(4)]

    def line(row: tuple) -> str:
        return '  ' + '  '.join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)).rstrip()

    total = sum(part['entry']['bytes'] for part in plan['parts'])
    out = [f'Plurai will deliver {len(rows)} parts in one delivery. Nothing has left this machine yet.']
    if plan.get('backend'):
        out.append(f'Backend: {plan["backend"]}')
    out += ['', line(header), *(line(row) for row in rows), '', f'Total: {len(rows)} parts, {_size(total)}.']
    out.append(f'Screen: {digest_of(plan)}')
    return '\n'.join(out) + '\n'


def _posture_conflict(parts: list) -> Optional[str]:
    """The cause the evidence parts cannot share one package, or None: a workspace holds one anonymization setting."""
    evidence = [part['entry'] for part in parts if part['entry']['kind'] != 'agent_description']
    postures = {entry['posture'] for entry in evidence}
    if len(postures) > 1:
        return 'the traces and the corpus are prepared under different anonymization settings, and a workspace holds one'
    if len({entry.get('anon_key_fingerprint') for entry in evidence}) > 1:
        return 'the anonymized parts carry different key fingerprints, so their pseudonyms would not join'
    return None


def build_plan(workdir: Path, plurai: str, backend: Optional[str], env: Any) -> dict:
    """Plan every source with `--plan-only` and return the plan. Raises `Refusal` when any part cannot be planned."""
    sources = _load_sources(workdir)
    _require_capability(plurai)
    names = _declared_names(workdir)
    parts: list = []
    handle, staged = tempfile.mkstemp(prefix='plurai-trace-source-')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as out:
            json.dump(_trace_config(sources[TRACES], env), out)
        config = Path(staged)
        for name in ORDER:
            entries, why = _run_ingest(plurai, _argv(workdir, name, sources, config=config, names=names, extra=['--plan-only']), planning=True, env=env)
            if entries is None:
                raise Refusal(f'the {name} could not be planned: {why}; nothing was sent.')
            if not all(isinstance(entry, dict) and entry.get('planned') is True for entry in entries):
                raise Refusal(f'the {name} ingest printed an entry that is not marked planned, so it may have sent the part; the delivery was stopped.')
            entries = [_plain(entry) for entry in entries]
            problem = _check_entries(name, entries, workdir)
            if problem:
                raise Refusal(f'the {name} plan {problem}; nothing was sent.')
            parts += [{'source': name, 'title': _title(name, entry, sources), 'entry': entry} for entry in entries]
    finally:
        try:
            os.unlink(staged)
        except OSError:
            pass
    conflict = _posture_conflict(parts)
    if conflict:
        raise Refusal(f'{conflict}; nothing was sent. Prepare both under the setting `anonymize:` records in plurai.config.yaml.')
    return {'version': 1, 'backend': backend, 'parts': parts}


def _cmd_plan(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    _forget(workdir, PLAN_FILE, SCREEN_FILE, APPROVAL_FILE, ADDED_FILE)
    try:
        plan = build_plan(workdir, args.plurai, args.backend, os.environ)
    except Refusal as refusal:
        return _report(refusal)
    plan['digest'] = digest_of(plan)
    screen = render_screen(plan)
    _write_json(_art(workdir) / PLAN_FILE, plan)
    grounding_files.write_atomic(_art(workdir) / SCREEN_FILE, screen)
    sys.stdout.write(screen)
    print(f'SCREEN={plan["digest"]}')
    return 0


def _report(refusal: Refusal) -> int:
    print(f'grounding_consent: {refusal}', file=sys.stderr)
    return refusal.code


def _load_plan(workdir: Path) -> dict:
    plan = _read_json(_art(workdir) / PLAN_FILE)
    parts = plan.get('parts') if isinstance(plan, dict) else None
    if not isinstance(parts, list) or not parts or plan.get('digest') != digest_of(plan):
        raise Refusal(f'artifacts/{PLAN_FILE} is missing or was changed after it was planned; plan again.')
    return plan


def _cmd_screen(args: argparse.Namespace) -> int:
    try:
        plan = _load_plan(Path(args.workdir))
    except Refusal as refusal:
        return _report(refusal)
    sys.stdout.write(render_screen(plan))
    print(f'SCREEN={plan["digest"]}')
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    try:
        plan = _load_plan(workdir)
    except Refusal as refusal:
        return _report(refusal)
    if args.screen != plan['digest']:
        return _fail('the screen named is not the one planned on disk; nothing was approved. Plan again and show that screen.')
    _write_json(_art(workdir) / APPROVAL_FILE, {'version': 1, 'digest': plan['digest']})
    print(f'CONSENT=APPROVED {plan["digest"]}')
    return 0


# --- the delivery ------------------------------------------------------------------------------------


def _approved_plan(workdir: Path) -> dict:
    plan = _load_plan(workdir)
    approval = _read_json(_art(workdir) / APPROVAL_FILE)
    if not isinstance(approval, dict) or approval.get('digest') != plan['digest']:
        raise Refusal('the planned screen is not approved: no byte was sent. Show the screen, ask, and record the answer with `approve`.', aborted=False)
    return plan


def deliver(workdir: Path, plurai: str, env: Any) -> list:
    """Run every ingest for real with the planned hashes and return the `added` array, or raise `Refusal` on the first difference."""
    plan = _approved_plan(workdir)
    sources = _load_sources(workdir)
    _require_capability(plurai)
    planned: dict = {name: [part['entry'] for part in plan['parts'] if part['source'] == name] for name in ORDER}
    names = _declared_names(workdir)
    printed: dict = {}
    handle, staged = tempfile.mkstemp(prefix='plurai-trace-source-')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as out:
            json.dump(_trace_config(sources[TRACES], env), out)
        for name in ORDER:
            argv = _argv(workdir, name, sources, config=Path(staged), names=names, extra=_expectations(name, planned[name]))
            entries, why = _run_ingest(plurai, argv, planning=False, env=env)
            if entries is None:
                raise Refusal(f'the {name} are not what the screen showed ({why}); the delivery was stopped and nothing was sent to the grounding.')
            if entries != planned[name]:
                raise Refusal(f'the {name} printed entries other than the planned ones; the delivery was stopped and nothing was sent to the grounding.')
            problem = _check_entries(name, entries, workdir)
            if problem:
                raise Refusal(f'the {name} {problem}; the delivery was stopped and nothing was sent to the grounding.')
            printed[name] = entries
    finally:
        try:
            os.unlink(staged)
        except OSError:
            pass
    corpus = sources[CORPUS]
    added, problem = grounding_corpus.register_part(
        workdir, printed[CORPUS][0], corpus['kb_id'], corpus['kb_label'], corpus.get('tool'), workdir / 'rag_corpus', [*printed[DESCRIPTIONS], *printed[TRACES]]
    )
    if problem is not None or added is None:
        raise Refusal(f'{problem}; the delivery was stopped and nothing was sent to the grounding.')
    return added


def _cmd_deliver(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    try:
        added = deliver(workdir, args.plurai, os.environ)
    except Refusal as refusal:
        _forget(workdir, APPROVAL_FILE)
        if refusal.aborted:
            _forget(workdir, ADDED_FILE)
            print('CONSENT=ABORTED')
        return _report(refusal)
    except _client_handshake.VersionRefused:
        _forget(workdir, APPROVAL_FILE, ADDED_FILE)
        raise
    _forget(workdir, APPROVAL_FILE)
    _write_json(_art(workdir) / ADDED_FILE, added)
    print('CONSENT=DELIVERED')
    print('ADDED=' + json.dumps(added, separators=(',', ':'), ensure_ascii=False))
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="The scan's one consent screen across the descriptions, the traces and the corpus.")
    sub = parser.add_subparsers(dest='command', required=True)
    sources = sub.add_parser('sources', help='find the three sources; exit 1 when any is missing')
    sources.add_argument('--workdir', required=True)
    sources.add_argument('--flowdir')
    sources.set_defaults(func=_cmd_sources)
    plan = sub.add_parser('plan', help='plan every part with --plan-only and print the screen')
    plan.add_argument('--workdir', required=True)
    plan.add_argument('--plurai', required=True, help='the resolved plurai runner')
    plan.add_argument('--backend', help='the backend the delivery reaches, shown on the screen')
    plan.set_defaults(func=_cmd_plan)
    screen = sub.add_parser('screen', help='print the planned screen again')
    screen.add_argument('--workdir', required=True)
    screen.set_defaults(func=_cmd_screen)
    approve = sub.add_parser('approve', help="record the user's approval of the screen")
    approve.add_argument('--workdir', required=True)
    approve.add_argument('--screen', required=True, help='the SCREEN= digest the user was shown')
    approve.set_defaults(func=_cmd_approve)
    deliver_cmd = sub.add_parser('deliver', help='run the ingests as approved and print the added argument')
    deliver_cmd.add_argument('--workdir', required=True)
    deliver_cmd.add_argument('--plurai', required=True)
    deliver_cmd.set_defaults(func=_cmd_deliver)
    args = parser.parse_args(argv)
    return _client_handshake.refusal_main(lambda: args.func(args))


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
