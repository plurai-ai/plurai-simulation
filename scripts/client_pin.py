#!/usr/bin/env python3
"""`$WORKDIR/.client_pin.json` — which CLIENT a scored run must be made with, verified by COMMIT.

WHY. A run has two client halves: the plugin whose skills drive it, and the SDK runner that
executes it. Both are installed as SNAPSHOTS — the plugin from a marketplace checkout or a
version-keyed cache, the SDK as a copy in the agent directory's own `.venv` — so what a session
actually loads is not implied by any repository's state. An A/B meant to exercise two PR tips
ran a plugin 58 commits behind one and SDK copies 30+ commits behind the other, and every run
it produced looked valid: the version strings were plausible, nothing compared a commit, and
the fixes under test were simply not in play.

Version strings cannot carry this — `0.5.0` spans dozens of commits — so the pin records the two
COMMITS the runs must be made with, and the resolver every scored run goes through refuses to
hand out a runner while either half is somewhere else:

    {plugin_commit, sdk_commit, note, pinned_at, armed_by?}

HOW EACH HALF IS IDENTIFIED. Mechanically, from what is installed — never from the checkout
the operator believes was installed:

    plugin   a checkout (the plugin root has a `.git`): `git rev-parse HEAD` of the plugin root
             THIS file runs from — the checkout the marketplace SERVES (a `source: directory`
             marketplace entry or a worktree).
             a marketplace cache install (no `.git`): the manifest's version and a hash of the
             installed files, `<version>+<sha256 prefix>`. The cache is what a self-serve user
             runs, and it records no commit, so the content is the identity. The hash covers every
             file under the root except Python bytecode, the one thing a session writes
             there, so it is the same before and after a session. A folder with
             neither a `.git` nor a manifest version is UNIDENTIFIABLE — which, while a pin
             exists, is a refusal, not a pass.
    sdk      the `plurai-simulation-sdk` distribution's PEP 610 `direct_url.json`, read by the interpreter the
             resolved binary actually runs under (its shebang, else the `python` beside it —
             the binary is resolved through symlinks first) and cross-checked to import from
             that interpreter's own environment: a git install carries `vcs_info.commit_id`;
             an EDITABLE `file://` install points at a checkout whose HEAD is read; a
             non-editable `file://` copy records no commit at all and is UNIDENTIFIABLE
             (reinstall it editable, or from git, so the run can be identified).

A DIRTY checkout is refused too: its content is not the commit it names. A pin that names only
one half, or that cannot be read, is a refusal — never a partial check.

WHO WRITES IT. An operator, once, before an A/B (`write`); or the loop ENTRY step, which ARMS a
pin from the loaded client (`arm --by <skill>`) so the client cannot drift mid-loop and prints
both commits where an A/B operator reads them, and RELEASES it at the loop's end (`release --by
<skill>`, which never touches a hand-written pin, and records the release in `.client_pin_released` so the
hand-off's rung can still read that a pin governed the loop: :func:`in_force`). An armed pin whose loop ended without releasing
it — that skill's flow window is no longer open — is released by the next check, with a NOTE and a
line appended to `$WORKDIR/.client_pin_released`.
NO PIN, NO CHECK: customers who never armed one see the resolver behave exactly as before.

Usage:
    client_pin.py write --plugin-commit <sha> --sdk-commit <sha> [--note "…"]
    client_pin.py write --plugin-checkout <dir> --sdk-checkout <dir>   # HEAD of each, clean only
    client_pin.py check [--plurai <bin>] [--plugin-root <dir>]        # 0 ok / no pin, 3 refused
    client_pin.py arm --by <skill> [--note "…"]                        # pin the loaded client, or check the existing pin
    client_pin.py release --by <skill>                                  # delete a pin that skill armed

`resolve_plurai.py` runs the same check in-process on the binary it resolved, and exits 3 —
printing NO path — when the pin refuses (or when the check itself cannot run while a pin
exists), so a stale client cannot obtain a runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    from _stdio import utf8_child_text
except ImportError:  # `python3 -I` leaves this file's directory off sys.path
    sys.path.append(str(Path(__file__).resolve().parent))
    from _stdio import utf8_child_text

PIN = '.client_pin.json'
#: Beside the pin: one JSON line per armed pin released because its loop's flow window had closed —
#: the durable record of a release that otherwise lives only in one run's stderr.
RELEASED = '.client_pin_released'

#: Exit codes. 3 is also `resolve_plurai.py`'s exit on a refusal, so a caller can tell "no
#: usable runner" (1) from "a usable runner the pin forbids" (3) and never fall back on the latter.
EXIT_UNREADABLE = 2
EXIT_REFUSED = 3

#: The shortest commit prefix a pin may carry. Operators write short SHAs; below seven hex
#: characters a prefix stops identifying one commit in a repository of this size.
_MIN_PREFIX = 7

#: A plugin-root override, for TESTS ONLY: the real root is the checkout this file runs from,
#: and a test that pins the repository's own live HEAD is a test that fails on every dirty tree.
_PLUGIN_ROOT_ENV = 'CLIENT_PIN_PLUGIN_ROOT'

#: Run inside the SDK's own interpreter: `importlib.metadata` sees THAT environment's `plurai`,
#: which is the whole point — the resolver's interpreter is not the runner's. It reports WHICH
#: interpreter answered and WHERE the distribution lives, so the caller can verify the answer
#: came from the environment the resolved binary runs in and not from a `python` that merely
#: sits beside it.
#: The PyPI distribution that carries the SDK. The name is the resolver's (`resolve_plurai.SDK_DISTRIBUTION`; a
#: test holds the two equal): this module runs standalone and does not import it.
SDK_DISTRIBUTION = 'plurai-simulation-sdk'

_SDK_PROBE = (
    'import json, sys\n'
    'from importlib.metadata import PackageNotFoundError, distribution\n'
    # THE SDK'S OWN ANSWER FIRST. Everything below it derives an identity from
    # INSTALL metadata — the dist version and `direct_url.json` — which describes how the
    # package was put there, not which files are being imported. A legacy editable install left
    # the canonical repo reporting 0.5.0 for a 0.7.0 tree, and this script and resolve_plurai
    # described the same binary two different ways in the same second. `plurai.identity` reads
    # `plurai.__file__`. Absent on an SDK that predates it, and then the derivation below still
    # answers — one identity where there is one, never a hard dependency on a new SDK.
    'try:\n'
    '    from plurai.identity import sdk_identity as _si\n'
    '    loaded = _si()\n'
    'except Exception:\n'
    '    loaded = None\n'
    'try:\n'
    f'    d = distribution("{SDK_DISTRIBUTION}")\n'
    'except PackageNotFoundError:\n'
    '    print(json.dumps({"installed": False, "executable": sys.executable, "identity": loaded}))\n'
    '    sys.exit(0)\n'
    'print(json.dumps({"installed": True, "version": d.version, "direct_url": d.read_text("direct_url.json"),\n'
    '                  "executable": sys.executable, "location": str(d.locate_file("")), "identity": loaded}))\n'
)


@dataclass(frozen=True)
class Identity:
    """One client half as it was actually found: its commit, or WHY it has none."""

    commit: str | None
    dirty: bool
    source: str

    @property
    def label(self) -> str:
        if self.commit is None:
            return f'UNIDENTIFIABLE ({self.source})'
        return f'{self.commit}{" +DIRTY" if self.dirty else ""} ({self.source})'


def _git(path: Path, *args: str) -> tuple[int, str, str]:
    """`(returncode, stdout, stderr)` — a failed git is a FAILURE, never an empty answer.

    Collapsing "git failed" into "no output" read a failed `git status` as a clean tree.

    `core.fsmonitor` is switched off for every call: the checkout is named by a command line, its
    config can set that option to any command, and git runs the command on each `status`. Nothing
    here needs a file-system monitor. No `GIT_*` variable reaches git either: they can relocate the
    repository and inject config (`GIT_CONFIG_COUNT`, `GIT_EXTERNAL_DIFF`), and the question is about
    the directory that was named, read under its own config.
    """
    env = {name: value for name, value in os.environ.items() if not name.startswith('GIT_')}
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv on a checkout the caller named
            ['git', '-c', 'core.fsmonitor=false', '-C', str(path), *args],  # noqa: S607 - `git` is resolved on PATH, like every other caller in this repo
            capture_output=True, text=True, **utf8_child_text(), timeout=30, check=False, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, '', str(exc)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _repository_programs(path: Path) -> list[str]:
    """The filter drivers the repository's own config (not the user's global one) defines.

    `git status` runs a tracked file's clean filter whenever its stat data changed, so a checkout
    that defines one would run that command on a plain cleanliness question. Reading the config
    runs nothing. A config that cannot be read counts as defining one: the question is unanswerable
    rather than clean.
    """
    rc, out, _err = _git(path, 'config', '--show-scope', '--get-regexp', r'^filter\..*\.(clean|smudge|process)$')
    if rc == 1:  # git's answer for "no key matches"
        return []
    if rc != 0:
        return ['filter.* (the repository config could not be read)']
    keys = (line.split('\t') for line in out.splitlines())
    return sorted({parts[1] for parts in keys if len(parts) >= 2 and parts[0] in ('local', 'worktree')})


def git_head(path: Path, what: str) -> Identity:
    """HEAD of a checkout, and whether its tracked files differ from it."""
    rc, head, err = _git(path, 'rev-parse', 'HEAD')
    if rc != 0 or not head:
        return Identity(None, False, f'{what}: {path} is not a git checkout — a snapshot records no commit ({err or "no HEAD"})')
    if programs := _repository_programs(path):
        return Identity(
            None, False,
            f'{what}: HEAD is {head} but {path} defines {", ".join(programs)}, which runs a program when git checks for changes; '
            'its cleanliness is not read. Pin by commit (`--plugin-commit` / `--sdk-commit`) instead',
        )
    # `dirty` still compares the commit each submodule is at with the recorded one, but starts no
    # status inside it: that would run under the submodule's own config, which is read nowhere above.
    rc, status, err = _git(path, 'status', '--porcelain', '--untracked-files=no', '--ignore-submodules=dirty')
    if rc != 0:
        # The commit is known but its cleanliness is not — and "not that commit" is what a
        # dirty tree means, so an unanswered status question cannot read as clean.
        return Identity(None, False, f'{what}: HEAD is {head} but `git status` failed ({err or f"exit {rc}"}) — cleanliness unknown')
    return Identity(head, bool(status), f'{what}: git HEAD of {path}')


def plugin_root() -> Path:
    override = os.environ.get(_PLUGIN_ROOT_ENV)
    return Path(override) if override else Path(__file__).resolve().parents[1]


#: The only thing a session writes inside the install: Python bytecode. It is not part of what was installed, so it stays out
#: of the content hash. Session pins live under `~/.plurai/pins`, never in the install.
_RUNTIME_DIRECTORY_NAMES = ('__pycache__',)

#: Hex digits of the content hash kept in an identity: long enough that two installs never collide, short enough to read.
_CONTENT_HASH_LENGTH = 40


def _content_hash(root: Path) -> str:
    """SHA-256 over every file under `root` (path and bytes, in path order), minus Python bytecode.

    A symlink is hashed by its target text and never followed, so a link out of the install cannot change the identity
    or send the walk elsewhere.
    """
    digest = hashlib.sha256()
    for directory, folders, files in os.walk(root, followlinks=False):
        here = Path(directory)
        folders[:] = sorted(name for name in folders if name not in _RUNTIME_DIRECTORY_NAMES)
        for name in sorted(files):
            path = here / name
            digest.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0')
            digest.update(os.readlink(path).encode('utf-8', 'surrogateescape') + b'\1' if path.is_symlink() else path.read_bytes() + b'\2')
        # A symlinked directory is listed among `folders` by os.walk but not entered; hash the link itself.
        for name in folders:
            path = here / name
            if path.is_symlink():
                digest.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0' + os.readlink(path).encode('utf-8', 'surrogateescape') + b'\1')
    return digest.hexdigest()[:_CONTENT_HASH_LENGTH]


def snapshot_identity(root: Path) -> Identity:
    """A marketplace cache install: its manifest version plus a hash of the installed files, in the commit slot.

    The pin compares identities, and an install with no commit has none to give; its content is the comparable fact.
    """
    try:
        version = json.loads((root / '.claude-plugin' / 'plugin.json').read_text(encoding='utf-8')).get('version')
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        version = None
    if not isinstance(version, str) or not version.strip():
        return Identity(None, False, f'plugin: {root} is not a git checkout and has no version in .claude-plugin/plugin.json — nothing identifies it')
    try:
        content = _content_hash(root)
    except OSError as exc:
        return Identity(None, False, f'plugin: {root} is a marketplace cache install {version}, but its files could not be hashed ({exc})')
    return Identity(f'{version.strip()}+{content}', False, f'plugin: marketplace cache install {version.strip()} of {root}, content hash {content}')


def plugin_identity(root: Path | None = None) -> Identity:
    """The plugin half: a checkout by its commit, a marketplace cache install (no `.git`) by version and content hash.

    The `.git` test is made here rather than left to `git rev-parse`, which walks up and would answer with the commit of
    whatever repository happens to contain the cache folder.
    """
    root = root or plugin_root()
    if (root / '.git').exists():
        return git_head(root, 'plugin')
    return snapshot_identity(root)


#: What a Python interpreter's file name looks like (`python`, `python3`, `python3.12`, `pypy3`).
#: A console script's shebang names one of these; a `#!/bin/sh` wrapper names a SHELL, which is
#: not the interpreter the runner's `plurai-simulation-sdk` distribution lives under.
_PYTHON_NAME = re.compile(r'^(?:python|pypy)\d*(?:\.\d+)?$')


def _shebang_interpreter(binary: Path) -> Path | None:
    """The Python interpreter a script's `#!` line names, when it names a usable one.

    `#!/usr/bin/env python` names nothing (it defers to PATH), a bare `#!` names nothing, a
    non-Python shebang (`#!/bin/sh`) names nothing FOR THIS PURPOSE, and a binary that is not a
    script has no line to read.
    """
    try:
        with binary.open('rb') as handle:
            first = handle.readline().decode('utf-8', 'replace').strip()
    except OSError:
        return None
    if not first.startswith('#!'):
        return None
    parts = first[2:].split()
    if not parts or not _PYTHON_NAME.match(Path(parts[0]).name):
        return None
    candidate = Path(parts[0])
    return candidate if os.access(candidate, os.X_OK) else None


def _interpreter_for(plurai_bin: Path) -> Path | None:
    """The interpreter the resolved `plurai` runs under.

    The binary is RESOLVED first (a symlinked shim points at the real script), and its own
    shebang is read FIRST: a pipx / uv / pyenv shim directory holds a `python` that belongs to
    some other environment, and preferring the sibling refused a correct client with exit 3.
    The sibling `python` is the fallback for a script whose shebang names nothing usable
    (`/usr/bin/env python`).
    """
    binary = plurai_bin.resolve()
    if named := _shebang_interpreter(binary):
        return named
    beside = binary.parent / 'python'
    return beside if os.access(beside, os.X_OK) else None


def _file_url_checkout(url: str) -> Path:
    """The checkout an editable ``file://`` install names: ``url2pathname`` of the URL's path, the inverse of
    ``Path.as_uri()`` (``file:///C:/a%20b`` is ``C:\\a b`` on Windows, ``file:///a%20b`` is ``/a b`` elsewhere)."""
    import urllib.parse  # noqa: PLC0415 - only an editable install needs them
    import urllib.request  # noqa: PLC0415

    return Path(urllib.request.url2pathname(urllib.parse.urlsplit(url).path))


def _direct_url_identity(raw: str | None, version: str) -> Identity:
    """PEP 610 metadata -> a commit, or the reason there is none."""
    if not raw:
        return Identity(None, False, f'sdk {version}: no direct_url.json — an index install records no commit')
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        return Identity(None, False, f'sdk {version}: direct_url.json is not JSON')
    vcs = doc.get('vcs_info') if isinstance(doc, dict) else None
    if isinstance(vcs, dict) and vcs.get('commit_id'):
        return Identity(str(vcs['commit_id']), False, f'sdk {version}: git install of {doc.get("url")}')
    url = str(doc.get('url') or '') if isinstance(doc, dict) else ''
    dir_info = doc.get('dir_info') if isinstance(doc, dict) else None
    if url.startswith('file://') and isinstance(dir_info, dict) and dir_info.get('editable'):
        return git_head(_file_url_checkout(url), f'sdk {version}: editable install')
    if url.startswith('file://'):
        return Identity(
            None, False,
            f'sdk {version}: a non-editable file:// copy of {url[len("file://"):]} — its bytes were frozen at install '
            'and no commit was recorded; reinstall editable (`uv pip install -e <checkout>`) or from git',
        )
    return Identity(None, False, f'sdk {version}: direct_url.json carries no commit ({url or "no url"})')


def _under(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    return any(resolved == root or root in resolved.parents for root in roots)


def sdk_identity(plurai_bin: Path) -> Identity:
    """The commit of the `plurai` the resolved binary will run — read from ITS interpreter.

    The probe's answer is accepted only when the distribution it found lives in the environment
    the binary runs in (the interpreter's prefix, or the binary's own venv): a `python` that
    happens to sit beside a shim could otherwise answer for an unrelated install.
    """
    interpreter = _interpreter_for(plurai_bin)
    if interpreter is None:
        return Identity(None, False, f'sdk: no interpreter found for {plurai_bin} (no usable shebang, no `python` beside it)')
    try:
        proc = subprocess.run(  # noqa: S603 - the interpreter is the one the resolved runner itself uses
            # Isolated (`-I`): `-c` otherwise puts the working directory first on `sys.path`, where a file
            # named like a stdlib module would run in place of it. Reading metadata needs no bearer.
            [str(interpreter), '-I', '-c', _SDK_PROBE], capture_output=True, text=True, timeout=60, check=False,
            env={name: value for name, value in os.environ.items() if name != 'PLURAI_TOKEN'},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Identity(None, False, f'sdk: {interpreter} could not run the metadata probe ({exc})')
    try:
        doc = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.returncode == 0 and proc.stdout.strip() else None
    except json.JSONDecodeError:
        doc = None
    if not isinstance(doc, dict):
        return Identity(None, False, f'sdk: {interpreter} returned no metadata (exit {proc.returncode}: {proc.stderr.strip()[:120]})')
    if not doc.get('installed'):
        return Identity(None, False, f'sdk: {interpreter} has no `{SDK_DISTRIBUTION}` distribution')
    location = doc.get('location')
    executable = Path(str(doc.get('executable') or interpreter))
    roots = [executable.resolve().parents[1], plurai_bin.resolve().parents[1]]
    if not isinstance(location, str) or not location or not _under(Path(location), roots):
        return Identity(
            None, False,
            f'sdk: the `plurai` that {executable} imports lives at {location!r}, outside the environment of the resolved '
            f'binary {plurai_bin} — the runner and the probed distribution are not the same install',
        )
    return _loaded_module_identity(doc.get('identity')) or _direct_url_identity(doc.get('direct_url'), str(doc.get('version') or '?'))


def _loaded_module_identity(payload: object) -> Identity | None:
    """The SDK's OWN `plurai.identity.sdk_identity()`, as this pin's `Identity`, or None.

    Preferred over :func:`_direct_url_identity` because that one reads how the package was INSTALLED (dist version + PEP 610 metadata), this one reads which files
    are being IMPORTED. On an editable install pointing at a checkout the two usually agree; when
    they disagree — a stale `egg-info`, a `file://` copy frozen at install time, a source overlay
    — only this one describes the code that will run.

    None for an SDK that predates the module, whose identity is unknown, or whose payload is not
    the shape this pin can read: the caller then falls back, so a plugin newer than the SDK keeps
    working instead of reporting UNIDENTIFIABLE.
    """
    if not isinstance(payload, dict):
        return None
    version = str(payload.get('version') or '?')
    if payload.get('source') == 'git' and payload.get('commit'):
        return Identity(str(payload['commit']), bool(payload.get('dirty')), f'sdk {version}: the loaded module, from its own checkout')
    if payload.get('source') == 'dist' and payload.get('file_hash'):
        # A wheel has no commit and never will; its CONTENT hash is the identity, and it is
        # returned in the commit slot deliberately — the pin compares identities, and a build
        # with no comparable identity is exactly what this ticket removes.
        return Identity(str(payload['file_hash']), False, f'sdk {version}: the loaded module, content hash (no checkout)')
    return None


def _matches(pinned: str, actual: str) -> bool:
    pinned, actual = pinned.strip().lower(), actual.strip().lower()
    return len(pinned) >= _MIN_PREFIX and actual.startswith(pinned)


def verify(pin: dict, plugin: Identity, sdk: Identity) -> list[str]:
    """Every reason the pin refuses these two halves — empty when both are the pinned commits.

    Both halves are always reported: an operator fixing one and re-running to discover the other
    is the slow loop this exists to remove. Unidentifiable is a refusal while a pin exists — a
    client whose commit cannot be read cannot be the pinned one either — and so is a pin that
    names only one half: `write` refuses to create one, so a half-pin is a hand edit, and a check
    with a blind side is not a check.
    """
    reasons: list[str] = []
    for name, expected, found in (('plugin', pin.get('plugin_commit'), plugin), ('sdk', pin.get('sdk_commit'), sdk)):
        if not isinstance(expected, str) or not expected.strip():
            reasons.append(f'{name}: the pin carries no {name}_commit — a pin with one half is a check with a blind side; rewrite it with `client_pin.py write`')
            continue
        if found.commit is None or not _matches(expected, found.commit):
            reasons.append(f'{name}: pinned {expected}, loaded {found.label}')
        elif found.dirty:
            reasons.append(f'{name}: pinned {expected}, loaded {found.label} — uncommitted changes make the content not that commit')
    return reasons


def _find_workdir() -> Path | None:
    """`$WORKDIR` / `$PLURAI_DIR`, else the shared resolver cascade; None when nothing resolves.

    Never fails: the resolver runs from the customer repo root on every ingest and every run,
    and "no workdir" simply means "no pin" here — said once on stderr, so an operator who
    expected a pin to bite can see why it did not.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import harness_fingerprint  # noqa: PLC0415 - sibling script; the one owner of the cascade

    workdir = harness_fingerprint.find_workdir(None)
    if workdir is None and harness_fingerprint.agent_root_refused():
        print('client_pin: no workdir — scripts/resolve-workdir.sh REFUSED to name an agent root (PLURAI_AGENT_RESOLVED=0) — no pin was looked for', file=sys.stderr)
    elif workdir is None:
        print('client_pin: no workdir resolved (no $WORKDIR / $PLURAI_DIR, no plurai_sim/ in reach) — no pin was looked for', file=sys.stderr)
    return workdir


def read_pin(workdir: Path) -> dict | None:
    try:
        doc = json.loads((workdir / PIN).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _refusal(workdir: Path, reasons: list[str]) -> str:
    return (
        f"client_pin: REFUSED — {workdir / PIN} pins the client this workdir's runs must be made with, and the loaded client "
        f'is not it:\n  ' + '\n  '.join(reasons) + '\n  A run made now would look valid and exercise the wrong code. Fast-forward '
        '/ reinstall the half named above to the pinned commit (verify with `client_pin.py check`), or delete the pin to '
        'declare that any client is acceptable.'
    )


def _hooks() -> None:
    """Put hooks/ on sys.path: the flow window's owners live there."""
    hooks = str(Path(__file__).resolve().parents[1] / 'hooks')
    if hooks not in sys.path:
        sys.path.insert(0, hooks)


def _window_bases(workdir: Path) -> list[Path]:
    """Every directory this workdir's flow window may live in — the hooks' own resolution."""
    _hooks()
    import _run_driver  # noqa: PLC0415 - hooks/ is only on sys.path here

    return _run_driver.marker_bases(workdir)


def _arming_window_open(workdir: Path, armed_by: str) -> bool | None:
    """Is the flow window of the skill that armed this pin open? None when that cannot be decided
    (a skill that opens no window, or a hook module that does not import) — and an undecided pin
    keeps governing."""
    try:
        _hooks()
        import flow_arm  # noqa: PLC0415 - THE skill -> window-command table

        command = flow_arm.COMMANDS.get(armed_by)
        if command is None:
            return None
        bases = _window_bases(workdir)
    except Exception:  # noqa: BLE001 - undecided, so the pin keeps governing
        return None
    for base in bases:
        try:
            if (base / flow_arm.FLOW_MARKER).read_text(encoding='utf-8').strip() == command:
                return True
        except OSError:
            continue
    return False


#: Why an armed pin was released: its loop's own release step, or a later check after that loop's window had closed.
RELEASED_BY_LOOP, RELEASED_WINDOW_CLOSED = 'loop ended', 'arming flow window closed'


def _record_release(workdir: Path, pin: dict, because: str = RELEASED_WINDOW_CLOSED) -> str:
    """Append the released pin to :data:`RELEASED`; the sentence the NOTE ends with says where it went, or why it did not."""
    line = {**pin, 'released_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'released_because': because}
    path = workdir / RELEASED
    try:
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(line, sort_keys=True) + '\n')
    except OSError as exc:
        return f'The release could NOT be recorded in {path} ({exc}).'
    return f'Recorded in {path}.'


def enforce(plurai_bin: Path, workdir: Path | None = None, root: Path | None = None) -> str | None:
    """The refusal message for this resolved runner, or None when no pin exists or both halves match.

    The in-process entry `resolve_plurai.py` calls. While a pin file EXISTS, every failure to
    decide is a refusal — a pin that cannot be read, an identification that raises — because the
    alternative is a pin that stops biting the moment something around it breaks.

    A pin a skill ARMED governs only while that skill's flow window is open. One whose loop ended
    without releasing it is released here, with a NOTE, and the run continues unpinned; an
    operator-written pin (no `armed_by`) is never released.
    """
    workdir = workdir or _find_workdir()
    if workdir is None or not (workdir / PIN).exists():
        return None
    pin = read_pin(workdir)
    if pin is None:
        return f'client_pin: REFUSED — {workdir / PIN} exists but does not read as a pin; rewrite it with `client_pin.py write` (or delete it).'
    armed_by = pin.get('armed_by')
    if isinstance(armed_by, str) and armed_by and _arming_window_open(workdir, armed_by) is False:
        try:
            (workdir / PIN).unlink()
        except OSError as exc:
            return _refusal(workdir, [f'it was armed by {armed_by}, whose flow is no longer open, but it could not be released ({exc})'])
        recorded = _record_release(workdir, pin)  # after the unlink: the record never claims a release that did not happen
        print(f'client_pin: NOTE — {workdir / PIN} was armed by {armed_by}, whose flow is no longer open, so it was left '
              f'over from a loop that ended without releasing it. Released; this run proceeds without a client pin. {recorded}',
              file=sys.stderr)
        return None
    try:
        reasons = verify(pin, plugin_identity(root), sdk_identity(plurai_bin))
    except Exception as exc:  # noqa: BLE001 - any failure to decide is a refusal while the pin exists
        return _refusal(workdir, [f'the check itself failed ({type(exc).__name__}: {exc}) — the pin cannot be verified, so it refuses'])
    return _refusal(workdir, reasons) if reasons else None


# --- CLI ---------------------------------------------------------------------------------------


def _workdir_or_exit(explicit: str | None) -> Path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import harness_fingerprint  # noqa: PLC0415 - sibling script; the one owner of the cascade

    return harness_fingerprint.resolve_workdir(explicit)


def _write_pin(workdir: Path, pin: dict) -> Path:
    path = workdir / PIN
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(pin, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    tmp.replace(path)
    return path


def _resolved_plurai(explicit: str | None) -> Path | None:
    """The runner the skills would resolve, WITHOUT the pin enforcing itself recursively."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import resolve_plurai  # noqa: PLC0415 - sibling script; the same resolution every run uses

    if explicit:
        # The check runs the interpreter the runner's shebang names, so a path nobody resolved would
        # run whatever program that line points at.
        if (why := resolve_plurai.explicit_runner_refusal(explicit)) is not None:
            print(f'client_pin: REFUSED — --plurai {explicit} was not run: {why}', file=sys.stderr)
            raise SystemExit(EXIT_REFUSED)
        return Path(explicit)
    resolved, _code = resolve_plurai.resolve(enforce_pin=False)
    return Path(resolved) if resolved else None


def cmd_write(args: argparse.Namespace) -> int:
    workdir = _workdir_or_exit(args.workdir)
    halves: dict[str, str] = {}
    for name, commit, checkout in (('plugin', args.plugin_commit, args.plugin_checkout), ('sdk', args.sdk_commit, args.sdk_checkout)):
        if commit:
            if len(commit.strip()) < _MIN_PREFIX:
                print(f'client_pin write: --{name}-commit {commit!r} is shorter than {_MIN_PREFIX} characters and identifies nothing.', file=sys.stderr)
                return EXIT_UNREADABLE
            halves[name] = commit.strip()
            continue
        if checkout:
            found = git_head(Path(checkout), name)
            if found.commit is None or found.dirty:
                print(f'client_pin write: cannot pin {name} from {checkout}: {found.label} — a pin names one commit, and this is not one.', file=sys.stderr)
                return EXIT_UNREADABLE
            halves[name] = found.commit
            continue
        print(f'client_pin write: give --{name}-commit or --{name}-checkout — a pin with one half is a check with a blind side.', file=sys.stderr)
        return EXIT_UNREADABLE
    pin = {
        'plugin_commit': halves['plugin'],
        'sdk_commit': halves['sdk'],
        'note': args.note or None,
        'pinned_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    path = _write_pin(workdir, pin)
    print(f'pinned {path} — plugin {pin["plugin_commit"]} · sdk {pin["sdk_commit"]}')
    return 0


def _check(workdir: Path, plurai_bin: Path | None, root: Path | None) -> int:
    if plurai_bin is None:
        print('client_pin: no usable `plurai` resolved — see the resolver output above.', file=sys.stderr)
        return EXIT_UNREADABLE
    refusal = enforce(plurai_bin, workdir, root)
    if refusal:
        print(refusal, file=sys.stderr)
        return EXIT_REFUSED
    pin = read_pin(workdir)
    if pin is None:  # a left-over armed pin, released by `enforce` (its NOTE says so)
        print(f'no {PIN} in {workdir} after the release — no client pin, nothing to verify.')
        return 0
    print(f'OK — plugin {pin.get("plugin_commit")} · sdk {pin.get("sdk_commit")} · runner {plurai_bin}')
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    workdir = _workdir_or_exit(args.workdir)
    if not (workdir / PIN).exists():
        print(f'no {PIN} in {workdir} — no client pin, nothing to verify (the customer default).')
        return 0
    return _check(workdir, _resolved_plurai(args.plurai), Path(args.plugin_root) if args.plugin_root else None)


def _live_simulate_plan(workdir: Path) -> bool:
    """Is a `/simulate` run live for this workdir (`_run_driver.autonomous_plan`)? False when undecidable."""
    try:
        _hooks()
        import _run_driver  # noqa: PLC0415 - hooks/ is only on sys.path here

        return _run_driver.autonomous_plan(_window_bases(workdir)) is not None
    except Exception:  # noqa: BLE001 - undecided reads as no live run: the lock keeps refusing
        return False


def _relock_plugin(workdir: Path, plurai_bin: Path, root: Path | None, by: str) -> bool:
    """Re-arm the pin THIS skill armed onto the loaded plugin, when only the plugin moved during a live
    `/simulate` run. True when it re-armed (the loop continues), False to fall through to the check.

    The user started `/simulate` and is not asked again after its start call: a plugin update mid-run
    (the run ships its own skills) re-locks the build to the loaded plugin rather than stopping it.
    Everything else still refuses exactly as before: no live run, a hand-written pin (no `armed_by`),
    another skill's pin, an SDK that moved, or a plugin that cannot be named by a clean commit.
    """
    pin = read_pin(workdir)
    if pin is None or pin.get('armed_by') != by or not _live_simulate_plan(workdir):
        return False
    try:
        plugin, sdk = plugin_identity(root), sdk_identity(plurai_bin)
        reasons = verify(pin, plugin, sdk)
    except Exception:  # noqa: BLE001 - any failure to decide leaves the refusal to the check
        return False
    if not reasons or any(not r.startswith('plugin:') for r in reasons) or plugin.commit is None or plugin.dirty:
        return False
    relocked = {**pin, 'plugin_commit': plugin.commit, 'pinned_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'relocked_from': pin.get('plugin_commit')}
    path = _write_pin(workdir, relocked)
    print(f're-armed {path} by {by} — the plugin moved during this /simulate run ({pin.get("plugin_commit")} → {plugin.commit}); '
          f'sdk {sdk.commit} unchanged; continuing on the loaded plugin')
    return True


def cmd_arm(args: argparse.Namespace) -> int:
    """Pin the loaded client at a loop's entry — or verify the pin that already governs it.

    The arming is what makes the pin mechanical rather than remembered: from here on a client
    that drifts mid-loop (a plugin update, a re-synced venv) is refused, and the two commits are
    printed where an A/B operator reads them against the tips they meant to test. A half that
    cannot be identified is a WARNING, not a refusal — a customer whose SDK is a content copy
    must still be able to build a suite — but it means this loop's runs are not verifiable by
    commit, and an A/B must not proceed on it.
    """
    workdir = _workdir_or_exit(args.workdir)
    plurai_bin = _resolved_plurai(args.plurai)
    root = Path(args.plugin_root) if args.plugin_root else None
    if (workdir / PIN).exists():
        if plurai_bin is not None and _relock_plugin(workdir, plurai_bin, root, args.by):
            return 0
        code = _check(workdir, plurai_bin, root)
        if code or (workdir / PIN).exists():
            return code
        # A left-over armed pin was released: this loop arms its own, below.
    if plurai_bin is None:
        print('client_pin arm: no usable `plurai` resolved — nothing armed; see the resolver output above.', file=sys.stderr)
        return EXIT_UNREADABLE
    plugin, sdk = plugin_identity(root), sdk_identity(plurai_bin)
    blocked = [f'{name}: {found.label}' for name, found in (('plugin', plugin), ('sdk', sdk)) if found.commit is None or found.dirty]
    if blocked:
        print("client_pin arm: WARNING — the loaded client cannot be pinned, so this loop's runs are NOT verifiable by commit "
              '(an A/B must not proceed on it):\n  ' + '\n  '.join(blocked), file=sys.stderr)
        return 0
    pin = {
        'plugin_commit': plugin.commit,
        'sdk_commit': sdk.commit,
        'note': args.note or None,
        'pinned_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'armed_by': args.by,
    }
    path = _write_pin(workdir, pin)
    print(f'armed {path} by {args.by} — plugin {plugin.commit} · sdk {sdk.commit} · runner {plurai_bin}')
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    """Delete the pin the named skill armed — and only that one: a hand-written pin outlives the loop."""
    workdir = _workdir_or_exit(args.workdir)
    path = workdir / PIN
    if not path.exists():
        print(f'no {PIN} in {workdir} — nothing to release.')
        return 0
    pin = read_pin(workdir)
    armed_by = pin.get('armed_by') if pin else None
    if armed_by != args.by:
        print(f'{path} was not armed by {args.by} (armed_by={armed_by!r}) — left in place; an operator-written pin is released by deleting it.')
        return 0
    path.unlink()
    print(f'released {path} (armed by {args.by}). {_record_release(workdir, pin or {}, RELEASED_BY_LOOP)}')
    return 0


def _stamp(raw: object) -> datetime | None:
    try:
        return datetime.strptime(str(raw), '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def in_force(workdir: Path, at: datetime | None = None) -> bool:
    """Whether a client pin governed this workdir's runs: one is armed now, or :data:`RELEASED` records one in force at
    ``at`` (the scored run's time — pinned at or before it, released at or after it; with no ``at``, any recorded
    release). A loop releases its pin before its hand-off reads this. A record whose times do not read proves nothing."""
    if read_pin(workdir) is not None:
        return True
    try:
        lines = (workdir / RELEASED).read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or (released := _stamp(row.get('released_at'))) is None:
            continue
        pinned = _stamp(row.get('pinned_at'))
        if at is None or (released >= at and (pinned is None or pinned <= at)):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workdir', help='defaults to $WORKDIR, else the resolver cascade')
    sub = parser.add_subparsers(dest='cmd', required=True)

    write = sub.add_parser('write', help="record the two commits this workdir's runs must be made with")
    write.add_argument('--plugin-commit', default=None)
    write.add_argument('--sdk-commit', default=None)
    write.add_argument('--plugin-checkout', default=None, help='pin the CLEAN HEAD of this plugin checkout')
    write.add_argument('--sdk-checkout', default=None, help='pin the CLEAN HEAD of this SDK checkout')
    write.add_argument('--note', default=None, help='what this pin is for (an A/B name, a PR pair)')
    write.set_defaults(func=cmd_write)

    for verb, func, blurb in (
        ('check', cmd_check, 'is the loaded client the pinned one? 0 = yes or no pin, 3 = refused'),
        ('arm', cmd_arm, 'pin the loaded client at a loop entry (or check the pin that already governs it)'),
    ):
        cmd = sub.add_parser(verb, help=blurb)
        cmd.add_argument('--plurai', default=None, help='the resolved `plurai` binary; defaults to running the resolver')
        cmd.add_argument('--plugin-root', default=None, help=f'defaults to the checkout this file runs from (${_PLUGIN_ROOT_ENV} overrides)')
        if verb == 'arm':
            cmd.add_argument('--by', required=True, help='the skill arming the pin; only it may release it')
            cmd.add_argument('--note', default=None)
        cmd.set_defaults(func=func)

    release = sub.add_parser('release', help='delete the pin the named skill armed; a hand-written pin is left alone')
    release.add_argument('--by', required=True)
    release.set_defaults(func=cmd_release)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
