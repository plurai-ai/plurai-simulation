#!/usr/bin/env python3
"""Fingerprint the harness — the files whose contents decide how the agent BEHAVES in a run.

WHY. A `score_run` replay re-judges a SAVED transcript. That is only honest while the thing
that produced the transcript is still the thing on disk: edit `simulate.py` and the saved
behaviour is a record of a harness that no longer exists, yet every replay against it keeps
returning verdicts that look current. Nothing observes that drift — a harness edit leaves no
mark on the run artifacts, the eval set, or any id the backend tracks.

So the harness gets an identity of its own: one sha256 over a language-aware manifest of the
files a run's behaviour depends on. Two runs with the same fingerprint were driven by the same
harness; a changed fingerprint says the saved artifacts are stale, whatever else still matches.

MANIFEST. `plurai.config.yaml` is in every language's manifest (it is the descriptor contract,
and it is language-agnostic by design). The rest follows the language:

    python        simulate.py, _mocks.py, _storage.py
    claude-skill  simulate.py, _mocks.py, _storage.py, _skill_tools.py
    bridge        simulate.py, _mocks.py, _storage.py, every file under bridge/ except
                  bridge/runtime/node_modules/, bytecode caches and hidden (dot-prefixed) files
                  (a symlinked directory is followed and its files hashed), and every file the
                  bench REQUIRES (below), hidden or not

A bridge bench is Python-harnessed: `simulate.py` drives a Node child through `bridge/host.py`,
so the child's map, entry, replacements and runtime are as much harness as `simulate.py` is. The
installed dependencies are not: `npm ci` rebuilds them from the hashed lockfile, and a host's
`__pycache__` appears on the first run, which would otherwise read as a harness edit.

The REQUIRED bridge files (`bridge_required_files`) are one list, shared with `stage-inputs.sh`
(`--bridge-files`): the map, the host, the map's `entry.path`, every `modules.*.replace` and
`local.*.impl` file, and the runtime's `constants.json` `paths.runtime_files`. An absent one hashes
as `null` here, and is reported MISSING there.

THE DECLARED ENTRY WRAPPER. A bench may drive the agent through a file the operator wrote beside
it (a runner `simulate.py` imports, which in turn imports the product). It is harness, not agent,
only when `plurai.config.yaml` DECLARES it, block form, path relative to `plurai_sim/`:

    harness:
      entry_wrapper: ../run_agent.py

A declared wrapper joins the manifest under that key. The file set is read from the config, which
is itself hashed, so it never depends on anything the fingerprint does not hash. With no
declaration the manifest and the hex are byte-identical to a bench that never had the key; the
first fingerprint after a declaration is added differs, so that bench's earlier runs, rescore
sidecars and fingerprint-keyed overrules read as stale once. `--entry-wrapper` reports whether a
declaration holds up (advisory; it never changes the hash): `simulate.py` imports it, and it
imports the package of the recorded `agent.entry_symbol` without being part of it.

PRESENCE IS PART OF THE HASH. A manifest entry that does not exist hashes as `null`, not as
"omitted" — deleting `_mocks.py` and inlining it into `simulate.py` is a harness change, and a
manifest that simply skipped absent files would hash the two states identically. The manifest
ITSELF (the language and the exact key set) is hashed too, so a language redetected differently
never collides with the old reading.

DETERMINISTIC ACROSS MACHINES. Content hashes only — no mtimes, no sizes, no absolute paths,
keys sorted. The one thing outside this script's control is the checkout's line endings; a repo
that rewrites them per platform will fingerprint differently there, which is a real difference
in the bytes the interpreter reads.

Usage:
    harness_fingerprint.py                       # print {fingerprint, lang, files} as JSON
    harness_fingerprint.py --write               # also store the hex in $WORKDIR/.harness_fingerprint
    harness_fingerprint.py --workdir <dir> --lang bridge
    harness_fingerprint.py --quiet               # print the bare hex, nothing else
    harness_fingerprint.py --entry-wrapper       # print {declared, findings} for harness.entry_wrapper
    harness_fingerprint.py --bridge-files        # print the bridge bench's required files, one per line

Exit 0 on success; 2 when the workdir cannot be resolved. `--bridge-files` exits 1 when the map
cannot be read or names a path outside `bridge/` (each reason on stderr; the paths it could derive
are still printed).
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import posixpath
import re
import sys
from pathlib import Path

#: In EVERY manifest — the descriptor contract the runner and the backend both read, and the
#: one harness file that is language-agnostic (a bridged agent's descriptors use the same schema).
SHARED = ('plurai.config.yaml',)

#: The language-specific half. Keys are the values `--lang` accepts.
BY_LANG: dict[str, tuple[str, ...]] = {
    'python': ('simulate.py', '_mocks.py', '_storage.py'),
    'claude-skill': ('simulate.py', '_mocks.py', '_storage.py', '_skill_tools.py'),
    'bridge': ('simulate.py', '_mocks.py', '_storage.py', 'bridge/bridge.map.json', 'bridge/entry.mjs', 'bridge/host.py'),
}

#: The bridge child's directory and its seam map (the file that marks a bridge bench), as the
#: runtime's `constants.json` `paths` names them.
BRIDGE_DIR = 'bridge'
BRIDGE_MAP = 'bridge/bridge.map.json'

#: Under `BRIDGE_DIR` but not harness: installed dependencies (`constants.json`
#: `paths.never_copied`), and any directory or file a run or a desktop leaves behind. A hidden
#: (dot-prefixed) file is run-time state, not harness (a seed's id map, say), unless it is REQUIRED:
#: `harness_files` adds the required list back.
BRIDGE_NOT_HASHED_DIRS = ('bridge/runtime/node_modules',)
BRIDGE_NOT_HASHED_NAMES = frozenset({'__pycache__', '.DS_Store'})

#: The bridge runtime's shared protocol facts: which files a bench copies, and where.
BRIDGE_CONSTANTS = Path(__file__).resolve().parents[1] / 'skills' / 'simulation-setup-bridge' / 'references' / 'node' / 'runtime' / 'constants.json'

MARKER = '.harness_fingerprint'


def manifest_for(lang: str) -> tuple[str, ...]:
    """The sorted file list this language's fingerprint covers."""
    return tuple(sorted(set(SHARED) | set(BY_LANG[lang])))


def detect_lang(workdir: Path) -> str:
    """Which harness template stands in this workdir.

    Read off the files themselves rather than a config key: the fingerprint must be computable
    from the same bytes it hashes, and a config that disagrees with what is on disk would make
    the identity depend on a claim instead of on the harness. `_skill_tools.py` is unique to the
    nested claude-agent-sdk template; `bridge/bridge.map.json` to the bridge one; Python is the
    default (it is also the harness language of a claude-skill agent, so defaulting there is the
    conservative direction — it never drops a file the other manifests share).
    """
    if (workdir / '_skill_tools.py').is_file():
        return 'claude-skill'
    if (workdir / BRIDGE_MAP).is_file():
        return 'bridge'
    return 'python'


def bridge_files(workdir: Path) -> tuple[str, ...]:
    """Every file under `bridge/`, POSIX and relative to the workdir, minus what is not harness (hidden files included).

    Read off the directory, not a list: an added replacement module or adapter changes how the
    child behaves, and a fixed list would hash the bench as unchanged. A symlinked directory is
    followed (its files are what the child imports), each real directory once, so a link cycle ends.
    """
    root = workdir / BRIDGE_DIR
    found: list[str] = []
    seen: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        real = os.path.realpath(dirpath)
        if real in seen:
            dirnames[:] = []
            continue
        seen.add(real)
        rel = Path(dirpath).relative_to(workdir).as_posix()
        dirnames[:] = [d for d in dirnames if d not in BRIDGE_NOT_HASHED_NAMES and f'{rel}/{d}' not in BRIDGE_NOT_HASHED_DIRS]
        found.extend(f'{rel}/{name}' for name in filenames
                     if name not in BRIDGE_NOT_HASHED_NAMES and not name.startswith('.') and (Path(dirpath) / name).is_file())
    return tuple(sorted(found))


def _bridge_rel(path: object) -> str | None:
    """A map path relative to `bridge/` as a workdir-relative POSIX path, or None when it is not one under `bridge/`."""
    if not isinstance(path, str) or not path or path.startswith('/'):
        return None
    norm = posixpath.normpath(path.split('#', 1)[0])
    return None if norm == '..' or norm.startswith('../') else f'{BRIDGE_DIR}/{norm}'


def bridge_required_files(workdir: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`(required, problems)`: the files a bridge bench cannot run without, workdir-relative, and why any could not be read.

    The map and the host (`constants.json` `paths`), the map's `entry.path`, every `modules.*.replace` and every
    `local.*.impl` file, and each `paths.runtime_files` under `paths.runtime_dir`. ONE list: the fingerprint hashes an
    absent one as `null`, and `stage-inputs.sh` reports it MISSING.
    """
    paths = json.loads(BRIDGE_CONSTANTS.read_text(encoding='utf-8'))['paths']
    required = {paths['map'], paths['host'], *(f'{paths["runtime_dir"]}/{name}' for name in paths['runtime_files'])}
    problems: list[str] = []
    try:
        doc = json.loads((workdir / paths['map']).read_text(encoding='utf-8'))
    except FileNotFoundError:
        doc = {}
    except (OSError, ValueError) as exc:
        doc = {}
        problems.append(f'{paths["map"]} cannot be read ({type(exc).__name__}), so the files it names are unknown')
    if not isinstance(doc, dict):
        problems.append(f'{paths["map"]} is not a JSON object, so the files it names are unknown')
        doc = {}
    named: list[tuple[str, object]] = [('entry.path', (doc.get('entry') or {}).get('path') if isinstance(doc.get('entry'), dict) else None)]
    modules = doc.get('modules') if isinstance(doc.get('modules'), dict) else {}
    bodies = [entry for entry in (doc.get('packages') or {}).values() if isinstance(entry, dict)] if isinstance(doc.get('packages'), dict) else []
    for key, entry in modules.items():
        if isinstance(entry, dict) and 'replace' in entry:
            named.append((f'modules.{key}.replace', entry['replace']))
        if isinstance(entry, dict) and isinstance(entry.get('proxy'), dict):
            bodies.append(entry['proxy'])
    for body in bodies:
        local = body.get('local') if isinstance(body.get('local'), dict) else {}
        named += [(f'local.{name}.impl', spec['impl']) for name, spec in local.items() if isinstance(spec, dict) and 'impl' in spec]
    for label, value in named:
        if value is None and label == 'entry.path' and not doc:
            continue
        rel = _bridge_rel(value)
        if rel is None:
            problems.append(f'{paths["map"]} {label} is {value!r}, not a path under {BRIDGE_DIR}/')
        else:
            required.add(rel)
    return tuple(sorted(required)), tuple(problems)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 16), b''):
            digest.update(block)
    return digest.hexdigest()


_ROOT_LINE = re.compile(r'^harness:\s*(?:#.*)?$')
_WRAPPER_LINE = re.compile(r'^(?P<indent>[ \t]+)entry_wrapper:[ \t]*(?P<value>.*)$')


def _scalar(raw: str) -> str:
    """A plain or quoted YAML scalar with any trailing comment removed."""
    raw = raw.strip()
    if raw[:1] in ('"', "'"):
        end = raw.find(raw[0], 1)
        return raw[1:end] if end > 0 else ''
    return raw.split(' #', 1)[0].strip()


def declared_entry_wrapper(workdir: Path) -> str | None:
    """`harness.entry_wrapper` from `<workdir>/plurai.config.yaml` (POSIX, normalized), or None.

    Stdlib only and block form only, on purpose: the value decides which files are hashed, so every
    interpreter must read it the same way, with or without pyyaml. `--entry-wrapper` reports a
    declaration this reader cannot see.
    """
    try:
        lines = (workdir / SHARED[0]).read_text(encoding='utf-8').splitlines()
    except (OSError, ValueError):
        return None
    in_block, child_indent = False, None
    for line in lines:
        if not in_block:
            in_block = bool(_ROOT_LINE.match(line))
            continue
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if not line[:1].isspace():
            return None  # the next root key: the block ended without the key
        indent = len(line) - len(line.lstrip())
        child_indent = indent if child_indent is None else child_indent
        match = _WRAPPER_LINE.match(line)
        if match and indent == child_indent:
            value = _scalar(match.group('value')).replace('\\', '/')
            return posixpath.normpath(value) if value else None
    return None


def harness_files(workdir: Path, lang: str) -> tuple[str, ...]:
    """The language's manifest, every bridge file for a bridge bench, and the declared entry wrapper."""
    wrapper = declared_entry_wrapper(workdir)
    bridged = {*bridge_files(workdir), *bridge_required_files(workdir)[0]} if lang == 'bridge' else set()
    return tuple(sorted(set(manifest_for(lang)) | bridged | ({wrapper} if wrapper else set())))


def _imported_modules(tree: ast.AST) -> set[str]:
    """Every absolute module name an AST imports, at any depth (function-local imports included)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
            names.update(f'{node.module}.{alias.name}' for alias in node.names)
    return names


def _entry_symbol_ref(workdir: Path) -> str | None:
    """The scan-recorded `agent.entry_symbol.ref` that runs in the harness: bench_manifest.json, else the topology."""
    for path in (workdir / 'bench_manifest.json', workdir / 'artifacts' / 'agent_topology.json'):
        try:
            symbol = json.loads(path.read_text(encoding='utf-8')).get('agent', {}).get('entry_symbol')
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(symbol, dict) and symbol.get('executes') == 'in_harness' and isinstance(symbol.get('ref'), str):
            return symbol['ref']
    return None


def _parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    except (OSError, ValueError, SyntaxError):
        return None


def entry_wrapper_findings(workdir: Path) -> list[str]:
    """Why the declared `harness.entry_wrapper` is NOT harness; `[]` when it holds up or nothing is declared.

    Advisory: the scan shows these in the topology approval, and the quality loop treats the file as
    harness only when this returns `[]`. Two checks on the declaration, both read from the files:
    `simulate.py` imports the wrapper by its module name under the agent directory, and the wrapper
    imports the top-level package of the recorded `agent.entry_symbol` without being inside it.
    Every harness is `simulate.py` (a bridge bench included), so the one rule applies to all.
    """
    wrapper = declared_entry_wrapper(workdir)
    if wrapper is None:
        return _unread_declaration(workdir)
    agent_root = workdir.resolve().parent
    path = (workdir / wrapper).resolve()
    if posixpath.isabs(wrapper) or path.suffix != '.py' or agent_root not in path.parents:
        return [f'harness.entry_wrapper {wrapper!r}: must be a relative path to a .py file under the agent directory {agent_root.name}/']
    if not path.is_file():
        return [f'harness.entry_wrapper {wrapper!r}: no such file']
    module = '.'.join(path.relative_to(agent_root).with_suffix('').parts)
    simulate, tree = _parse(workdir / 'simulate.py'), _parse(path)
    if simulate is None or tree is None:
        return [f'harness.entry_wrapper {wrapper!r}: simulate.py or the wrapper does not parse']
    findings = []
    if module not in _imported_modules(simulate):
        findings.append(f'harness.entry_wrapper {wrapper!r}: simulate.py never imports `{module}`, so it does not drive the agent')
    ref = _entry_symbol_ref(workdir)
    if ref is None:
        return [*findings, f'harness.entry_wrapper {wrapper!r}: no in-harness agent.entry_symbol is recorded, so nothing shows it reaches the product']
    package = ref.split(':', 1)[0].split('.', 1)[0]
    if module.split('.', 1)[0] == package:
        findings.append(f'harness.entry_wrapper {wrapper!r}: it is inside the agent package `{package}`, which is product code')
    elif not any(name.split('.', 1)[0] == package for name in _imported_modules(tree)):
        findings.append(f'harness.entry_wrapper {wrapper!r}: it never imports the agent package `{package}` (agent.entry_symbol {ref})')
    return findings


def _unread_declaration(workdir: Path) -> list[str]:
    """A `harness.entry_wrapper` pyyaml sees but the block-form reader does not: it is not hashed, so say so."""
    try:
        import yaml  # noqa: PLC0415 - optional; only the advisory cross-check uses it

        doc = yaml.safe_load((workdir / SHARED[0]).read_text(encoding='utf-8'))
    except Exception:
        return []
    block = doc.get('harness') if isinstance(doc, dict) else None
    if isinstance(block, dict) and block.get('entry_wrapper'):
        return ['harness.entry_wrapper is declared in a form this reader does not take; write it as a `harness:` block '
                'with `entry_wrapper: <path>` on its own indented line']
    return []


def fingerprint(workdir: Path, lang: str) -> dict[str, object]:
    """`{fingerprint, lang, files}` — the manifest's per-file hashes and the hash OF that."""
    files: dict[str, str | None] = {}
    for name in harness_files(workdir, lang):
        path = workdir / name
        files[name] = _sha256(path) if path.is_file() else None
    # The hashed document carries the manifest (lang + the full key set incl. absent entries),
    # not just the hashes — see PRESENCE IS PART OF THE HASH above.
    canonical = json.dumps({'lang': lang, 'files': files}, sort_keys=True, separators=(',', ':'))
    return {
        'fingerprint': hashlib.sha256(canonical.encode('utf-8')).hexdigest(),
        'lang': lang,
        'files': files,
    }


def agent_root_refused() -> bool:
    """Did `resolve-workdir.sh` REFUSE to name an agent root? Its own answer, read not re-derived.

    An ambiguous anchor is answered by pointing `$WORKDIR`/`$PLURAI_DIR`/`$ART` at an unwritable
    sentinel AND exporting `PLURAI_AGENT_RESOLVED=0` beside them. The paths alone cannot carry
    that: emptiness was the old signal and was wrong in both directions, and any non-empty
    sentinel satisfies `[ -n "$ART" ]` — which is exactly why the field exists. `flow-window.sh`
    refuses to arm on it; this is the same fact read by every script under scripts/, not a
    second name for it.

    FAIL OPEN when unset, spelled the way `flow-window.sh` spells it (`${PLURAI_AGENT_RESOLVED:-1}`):
    a caller that exported `$WORKDIR` by hand and never sourced the resolver has said nothing
    about this field, and reading silence as a refusal would break every un-sourced call. `:-`
    treats SET-BUT-EMPTY as unset too, hence `or` and not a `.get()` default.
    """
    return (os.environ.get('PLURAI_AGENT_RESOLVED') or '1') != '1'


def find_workdir(explicit: str | None) -> Path | None:
    """`--workdir`, then `$WORKDIR`, then the shared resolver cascade; None when nothing resolves.

    The ONE cascade for every script under scripts/ (bench_manifest, lane_pin, client_pin,
    corpus_coordinate and this file all resolve through it): the harness resets the shell between
    tool calls, so a snippet that forgot to re-source `resolve-workdir.sh` reaches a script with a
    clean environment, and each script re-deriving the answer is how two of them came to disagree.

    A REFUSED root is not a workdir, and not a miss to retry either. The sentinel arriving as a
    bench hashes to the same digest as an empty workdir and makes `phase resume` read as a fresh
    build; falling through to the cascade below instead names whatever bench the cwd reaches —
    a different agent than the one the resolver refused to guess. So the env branch returns None.

    NOT an `is_dir()`/`exists()` test on this branch. A repo with no `plurai_sim/` yet resolves to
    `<git-root>/plurai_sim` with `PLURAI_AGENT_RESOLVED=1` — a correct resolve to a directory that
    does not exist, and `/simulation-scan-agent`'s first run. The field is the only discriminator
    that separates it from the refusal. `--workdir` stays ungated: an explicit path is an
    operator's assertion, and the field reports on the RESOLVER, which that path bypasses.
    """
    if explicit:
        return Path(explicit)
    env = os.environ.get('WORKDIR') or os.environ.get('PLURAI_DIR')
    if env:
        return None if agent_root_refused() else Path(env)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
    try:
        from _workdir import resolve_workdir as _resolve  # noqa: PLC0415 - hooks/ is only on sys.path here
    except Exception:
        resolved = None
    else:
        resolved = _resolve()
    # Must already EXIST: the resolver returns `<cwd>/plurai_sim` for a directory with no agent
    # in it, and fingerprinting that hashes four absent files into a confident-looking digest.
    return resolved if resolved is not None and resolved.is_dir() else None


def resolve_workdir(explicit: str | None) -> Path:
    """`find_workdir`, or exit 2 — for the scripts whose question has no answer without one.

    TWO SENTENCES, because they send an operator to opposite places. "Nothing named a workdir" is
    fixed by sourcing the resolver; a resolver that RAN and refused is fixed by telling it which
    agent you meant, and repeating "source resolve-workdir.sh" at someone who just did is how a
    refusal gets read as a missing export.
    """
    resolved = find_workdir(explicit)
    if resolved is not None:
        return resolved
    if agent_root_refused():
        print(
            'ERROR: no workdir — scripts/resolve-workdir.sh REFUSED to name an agent root '
            '(PLURAI_AGENT_RESOLVED=0), so $WORKDIR/$PLURAI_DIR hold its unwritable sentinel and '
            'not a bench. Read that script\'s own ERROR above: it names the candidates it would '
            'have had to guess between. `cd` into the intended agent directory (the one holding '
            'its plurai_sim/) and re-run, or pass --workdir explicitly.',
            file=sys.stderr,
        )
        raise SystemExit(2)
    print(
        'ERROR: no workdir. Source scripts/resolve-workdir.sh (which exports $WORKDIR), or pass '
        '--workdir explicitly.',
        file=sys.stderr,
    )
    raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workdir', help='defaults to $WORKDIR, else the resolver cascade')
    parser.add_argument('--lang', choices=sorted(BY_LANG), help='defaults to detection from the files present')
    parser.add_argument('--write', action='store_true', help=f'also store the hex in <workdir>/{MARKER}')
    parser.add_argument('--quiet', action='store_true', help='print the bare fingerprint, not the JSON')
    parser.add_argument('--entry-wrapper', action='store_true', help='print {declared, findings} for harness.entry_wrapper; advisory, exit 0')
    parser.add_argument('--bridge-files', action='store_true', help="print the bridge bench's required files, one per line (stage-inputs.sh reads it)")
    args = parser.parse_args(argv)

    workdir = resolve_workdir(args.workdir)
    if args.bridge_files:
        required, problems = bridge_required_files(workdir)
        print('\n'.join(required))
        for problem in problems:
            print(f'harness_fingerprint: {problem}', file=sys.stderr)
        return 1 if problems else 0
    lang = args.lang or detect_lang(workdir)
    if args.entry_wrapper:
        report = {'declared': declared_entry_wrapper(workdir), 'findings': entry_wrapper_findings(workdir)}
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    result = fingerprint(workdir, lang)

    if args.write:
        (workdir / MARKER).write_text(str(result['fingerprint']) + '\n', encoding='utf-8')
    print(result['fingerprint'] if args.quiet else json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
