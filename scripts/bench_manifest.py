#!/usr/bin/env python3
"""The bench manifest: declare the bench once, then VERIFY each declaration deterministically.

WHY. A scan that is a sequence of steps, each deciding for itself what to run, ends in a hand-off
the model writes from memory about what it thinks it did. The failure this file prevents: a re-scan
reuses a probe report whose scripts no longer exist — they were written to a session scratchpad and
died with it — drives no agent turn on a bench whose only live gate is an agent turn, and reports
"Environment ready" over three checks that never ran. Nothing on disk contradicts it.

So the bench declares itself in `bench_manifest.json` (committed, like `plurai.config.yaml`), and
every declaration NAMES the checks that prove it. This script walks that manifest and decides —
by hash, by consent, by prerequisite — what to run, what to reuse and what cannot run; it writes
`artifacts/verification.json` and renders ONE integrity line from it. The model authors the
agent-specific check scripts once (into `$WORKDIR/probes/`, committed and hashed); it never
decides again what to run, what to reuse, or what to claim.

TWO VOCABULARIES, deliberately overlapping but not identical. Every CHECK ends in one of:

    verified        it ran and passed
    reused          nothing it depends on changed since a recorded pass (carries that signature)
    failed          it ran and failed
    not_verified    it ran and REFUSED the bench's own inputs, so it reached no verdict — an
                    input to fix and re-run (the contract's exit 2: a PRD it cannot project)
    not_run         it could not run — consent, a prerequisite, a missing key
    not_applicable  its `mode_rule` does not hold for this bench
    stale           `verify --only` left it out; it PASSED earlier (carries when), but what it
                    depends on changed since, so that pass cannot stand in for a run — and it is
                    not a "not verified" either: nothing was found wrong, nothing was re-checked

and every field `verifiable_paths` names ends in one of those too, EXCEPT that `reused` collapses
into `verified` (a field cares that something proved it, not when), plus one of its own:

    declared_only   NO check names this field: it is a claim this scan did not test

`declared_only` is the state that makes the rest honest. A field nothing checks used to be
indistinguishable from a field that passed, because both were silent.

NEVER FABRICATES. A prerequisite that is missing (`node`, a built `dist/`, the contract checker,
the agent's provider key) yields `not_run` naming what was missing — never a pass, and never a
failure either: "the tool is absent" and "the bench is broken" send an operator to opposite
places, and only the second is about the customer's agent.

Subcommands:

    bench_manifest.py init [--from-topology <path>]   create/refresh the manifest from the topology
    bench_manifest.py validate                        schema + cross-artifact agreement + coverage
    bench_manifest.py verify --mode reuse|reverify
    bench_manifest.py integrity-line                  the ONE line the hand-off must carry
    bench_manifest.py contract-gate                   the suite Step 0's read of the scan contract
    bench_manifest.py run-gate                        can a run on this bench produce signal (Step 0)
    bench_manifest.py coverage                        which checks verify which fields
    bench_manifest.py env-check NAME... [--providers]  where each setting is found (names only, never a value)
    bench_manifest.py run-mode                        derive the mock posture; write `.run_mode` = mockless (the one writer)
    bench_manifest.py phase record|resume|reset       the suite loop's phase record + resume point (+ set a stale build aside)
    bench_manifest.py window claim|status             the account-window claim (one window, one suite)

Exit codes (`verify`): 0 no blocking check failed or was blocked — verified, reused, or not
applicable to this bench (a pipeline bench's `smoke_turn` is the usual not-applicable, and the line
names it), or failed only in a way the run measures (the agent raised, a turn came back silent); 1 a blocking check FAILED;
2 the manifest is invalid or no workdir resolves; 3 a blocking check could NOT RUN or is STALE; 4 a blocking
check is NOT VERIFIED. `validate`: 0 clean, 2 findings. The 1/3/4 split is the same one the error
posture makes everywhere: a failure has findings to read, a not-run has a prerequisite to install,
and a not-verified has an input to fix.

`contract-gate` (the suite's Step 0): 0 PROCEED, 1 FIX FIRST, 2 NOT VERIFIED, 3 NOT RUN, 4 STALE.
`run-gate` (the suite's and `/simulation-run`'s Step 0): 0 RUNNABLE, 1 NOT RUNNABLE, 3 NOT RECORDED. Its own
reasons are gate findings (`scripts/gate_ledger.py`), printed as `finding:` lines and settled against the plugin's
ledger; the checks' own findings are recorded on their results. A declared host requirement measured unusable where
the agent runs it (missing, broken, fails sandboxed) is NOT RUNNABLE until the plugin repairs the host or overrules its
finding; one nothing measured (unverified, not checked) ships with its finding. While a host gap holds, the gate
prints one copy-paste install block from the resolver's plan — only install lines the agent's own docs give, each
cited, for the gaps still held — and stops; nothing installs. The runner skips `plugin.` ids in
that ledger and re-holds a host gap as its own `host.requirement_gap` finding: overrule that one too, with the same
reason.

`run-mode`: 0 the marker says the derived posture (first line `run_mode: mock|mockless`); 2 no readable config.
`phase`: 0 recorded / the record and the markers agree; 2 the record cannot be read (or a bad
`--set`, or a `render`/`report landed` while a file the report reads is missing — `deliverables.py`);
4 `resume` found a disagreement between the typed record and a marker file; 5 `resume` found a
handed-off build missing a file the report reads (re-run the suite's Steps 4–6).
`window`: 0 the window is free or ours; 4 it is not provably free — claimed by another workdir, or
a claim file that cannot be read. Both REPORT rather than block, which is why they have a code a
caller can branch on instead of a sentence a human has to read.
"""

from __future__ import annotations

import argparse
import ast
import glob
import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple

MANIFEST_FILENAME = 'bench_manifest.json'
VERIFICATION_FILENAME = 'verification.json'
SCHEMA_FILENAME = 'bench_manifest.schema.json'
SCHEMA_ID = 'plurai-bench-manifest/1'
VERIFICATION_SCHEMA_ID = 'plurai-bench-verification/1'
PROBES_DIRNAME = 'probes'
#: Where the world's witness tests land (C4): env-gen's `simulation_env/tests/{test_mock_witness.py, _mock_witness.py,
#: mock_witness.json}`, the three in one directory, as `fetch_artifact` serves them.
MOCK_WITNESS_DIR = 'mock_witness'
#: The backend's last contract run of the world (`fetch_env_file.py`), beside the witness files and never mixed with them.
MOCK_BACKEND_RUN_DIR = f'{MOCK_WITNESS_DIR}/backend_run'
MOCK_WITNESS_FILES = ('test_mock_witness.py', '_mock_witness.py', 'mock_witness.json')

#: The literal an `init` writes where the ANALYSIS must decide. `validate` refuses it: a bench
#: that reached generation with one still in place declares something nobody established.
SENTINEL = 'TODO'

#: The two words a `plurai.config.yaml` may declare for `shape:` — the config verifier owns this
#: vocabulary (`verify_config._SHAPE_HOOKS`) because it is the reader that maps a shape to the
#: hook a harness must export; the manifest schema's enum is pinned to it by test.
DECLARABLE_SHAPES = ('dialog', 'pipeline')

#: What `agent.shape` says when the config declares NONE of them. Not a `SENTINEL` (which
#: `validate` refuses — an undeclared bench is legal and is the overwhelming majority of them)
#: and not `dialog` (which would state a declaration nobody made). Nothing keys on it: what runs
#: is decided by `agent.dispatch_shape`, which resolves an absent declaration the way the runner
#: does.
UNDECLARED_SHAPE = 'undeclared'

STATUS_VERIFIED = 'verified'
STATUS_REUSED = 'reused'
STATUS_FAILED = 'failed'
#: It ran and refused the bench's OWN inputs. Neither `failed` (no finding about the agent) nor
#: `not_run` (nothing to install): filed as `not_run`, a contract that refused its PRD was routed
#: to "a prerequisite, not a defect" and a suite was built over no contract at all.
STATUS_NOT_VERIFIED = 'not_verified'
STATUS_NOT_RUN = 'not_run'
STATUS_NOT_APPLICABLE = 'not_applicable'
#: A check `verify --only` left out whose LAST result was a pass, when its signature no longer shows
#: that pass's inputs unchanged. Not `reused` (that would claim evidence that may no longer hold) and
#: never `not_run` (a partial re-verify rewrote a scan's passing two-turn smoke as `not_run`, and the
#: overview page told the operator "Harness not verified" minutes after the hand-off said it answered).
#: Carries `passed_at` and `passed_signature`, so a later `--only` reuses it again once its inputs
#: are back to what passed. Field-level too: a field a stale check covers is `stale`, never `verified`.
STATUS_STALE = 'stale'
#: Field-only. No check names the field; there is no result to carry it.
STATUS_DECLARED_ONLY = 'declared_only'
#: Field-only. A passing check's runtime witness could not observe the field (an entry outside
#: the harness interpreter, an SDK without the witness). Never a pass.
STATUS_NOT_DETERMINABLE = 'not_determinable'

#: Fields a check proves ONLY through the runtime witness it returns (its `witnesses`), never by
#: naming them in `verifies`, which would clear the field whether or not the agent ever ran it.
WITNESS_ONLY_FIELDS = ('agent.entry',)
#: The witness states a smoke reports beside `not_determinable` (the SDK's `plurai._entry_witness`).
WITNESSED = 'witnessed'
NOT_WITNESSED = 'not_witnessed'

#: A pass for the field it verifies. `reused` is a pass whose evidence is a recorded one.
_PASSING = (STATUS_VERIFIED, STATUS_REUSED)

#: Provider -> env vars that carry its key, when `harness.provider_env` does not say. PRESENCE
#: is all that is ever read; a key is never printed, logged or written to the verification file.
_PROVIDER_ENV: dict[str, tuple[str, ...]] = {
    'gemini': ('GOOGLE__API_KEY', 'GOOGLE_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS'),
    'google': ('GOOGLE__API_KEY', 'GOOGLE_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS'),
    'anthropic': ('ANTHROPIC_API_KEY',),
    'openai': ('OPENAI_API_KEY',),
    'azure': ('AZURE_OPENAI_API_KEY',),
    'bedrock': ('AWS_ACCESS_KEY_ID', 'AWS_PROFILE', 'AWS_WEB_IDENTITY_TOKEN_FILE', 'AWS_CONTAINER_CREDENTIALS_RELATIVE_URI', 'AWS_CONTAINER_CREDENTIALS_FULL_URI'),
}
#: LangChain `init_chat_model` provider ids (`google_genai:gemini-…`) for a row above: read for a model prefix and for the
#: declared `provider` alike. LiteLLM's `vertex_ai/` is not one of them.
_PROVIDER_ALIASES: dict[str, str] = {'google_genai': 'google', 'google_vertexai': 'google', 'bedrock_converse': 'bedrock', 'azure_openai': 'azure'}
#: The variable a Claude Agent SDK agent can authenticate with instead of `ANTHROPIC_API_KEY`: the SDK drives the Claude
#: CLI, which reads the login token from the environment.
CLAUDE_CLI_TOKEN_ENV = 'CLAUDE_CODE_OAUTH_TOKEN'
#: Provider -> the default credential files its SDK chain reads, relative to `$HOME`. EXISTENCE
#: only: a file is never opened, so nothing in it can be printed, logged or recorded.
_PROVIDER_FILES: dict[str, tuple[str, ...]] = {
    'bedrock': ('.aws/credentials', '.aws/config'),
    'gemini': ('.config/gcloud/application_default_credentials.json',),
    'google': ('.config/gcloud/application_default_credentials.json',),
}

_ID_RE = re.compile(r'^[a-z][a-z0-9_]*$')
#: `surfaces.knowledge[Nadia — onboarding]` / `surfaces.tools[get_order].mode` / `agent.entry`.
_SELECTOR_RE = re.compile(r'^(?P<head>[a-z_]+(?:\.[a-z_]+)*)(?:\[(?P<sel>[^\]]+)\])?(?P<tail>(?:\.[a-z_]+)*)$')

#: Boolean vocabulary that must never appear on the RIGHT of a `mode_rule` conjunct's `==`.
#: `' and '` is the whole joiner grammar, so everything here is a spelling that `split(' and ')`
#: leaves inside one conjunct — where it can only become part of a value that compares equal to
#: nothing. Matched on the raw (unstripped) right-hand side so a TRAILING ` and ` — which leaves
#: an EMPTY final conjunct plus a right-hand side ending in the word — is caught too.
_BOOLEAN_WORD_RE = re.compile(r'(?:^|\s)(?:and|or|not)(?:\s|$)', re.IGNORECASE)

_TIMEOUT_DEFAULT = 900
#: The share of its timeout `mock_contract` spends starting tests (`--deadline-s`): past it the check stops and still
#: writes the per-tool verdicts of what ran, where the timeout's kill would leave none.
_MOCK_GATE_DEADLINE_SHARE = 0.9

#: The check ids this build knows how to run itself. ONE vocabulary: `command_for` builds the argv
#: and `_check_findings` refuses a manifest naming anything else. The manifest is COMMITTED to the
#: customer's repo while the plugin is a cached snapshot updated by version, so a renamed or dropped
#: builtin would otherwise disarm every manifest that names it — silently, as a `not_run` routed to
#: the exit code that tells the operator to install a prerequisite that does not exist.
BUILTIN_IDS = ('verify_config', 'verify_simulate', 'lint_prd', 'verify_prd_appendix', 'smoke_init', 'smoke_turn', 'smoke_pipeline', 'scan_contract', 'mock_contract',
               'verify_bridge', 'seed_readback')

#: Exit codes that mean "this check reached NO VERDICT", by check KIND — never a pass and never a
#: failure. Keyed on the kind, not the id, so a second check of the same kind cannot silently lose
#: the mapping; a kind absent from this table has no such code and every non-zero exit is a
#: FAILURE, which is the safe default for a check whose contract nobody wrote down.
#:
#: `contract`: scan_contract's exit 1 is FIX-FIRST — findings about the customer's documents,
#: which is a real failure of the check; 2/3/5 mean it reached no verdict at all. 64 belongs with
#: them: `scan_contract.sh` exits 64 for every INVOCATION refusal ("at least one --corpus is
#: required; NOTHING was verified", an unsupported flag), which is this runner's mistake about how
#: to call it — not a finding about the customer's documents. Read as FAILED it becomes a blocking
#: exit 1, and the skill's table then tells the operator to lead their report with a defect the
#: scan never found.
#:
#: `smoke`: `verify_smoke.py` exits 2 for every state where it reached no verdict — simulate.py is
#: not a file, the SDK is not importable, or the bench's only live channel cannot be observed on
#: this SDK build. Missing here, all three were recorded as a blocking FAILURE of the customer's
#: agent, which is the other half of the same error-posture mistake.
#:
#: `verifier`: every builtin verifier exits 2 when it reached no verdict — its input is not a file or
#: cannot be read, or the gate ledger its findings are settled against cannot be read. None of those
#: is a finding about the bench.
#:
#: `mock`: `mock_contract_check.py` exits 2 when no contract test passed on the deployed mock — unreachable,
#: a refused token, no test file — which says nothing about any one mocked tool.
NO_VERDICT_EXITS: dict[str, tuple[int, ...]] = {'contract': (3, 5, 64), 'smoke': (2,), 'verifier': (2,), 'mock': (2,)}
#: Exit codes that mean "this check REFUSED the bench's own inputs", by kind — `not_verified`.
#: `contract`: scan_contract's 2 is NOT VERIFIED — a PRD it cannot project, an unreadable corpus,
#: a verdict it could not clear. Each names an input to fix, so it is routed to that fix, never to
#: the not-run "install a prerequisite" row. Checked BEFORE `NO_VERDICT_EXITS`.
REFUSED_INPUT_EXITS: dict[str, tuple[int, ...]] = {'contract': (2,)}
#: Exit codes that mean "this check RAN and left findings OPEN", by check ID — `not_verified`, never `verified`. Keyed by id
#: and not by kind: the same exit number means something else for another verifier. `lint_prd` exits 4 while a PRD finding
#: is neither fixed nor accepted with a recorded reason, and a PRD with open findings has not passed. Checked before
#: `REFUSED_INPUT_EXITS` and `NO_VERDICT_EXITS`.
OPEN_FINDINGS_EXITS: dict[str, tuple[int, ...]] = {'lint_prd': (4,)}
#: The count a check's headline leads with: `21 PRD finding(s) open (...)`.
_OPEN_FINDINGS_RE = re.compile(r'^(\d+) PRD finding\(s\) open\b', re.MULTILINE)
#: Exit codes that mean "the AGENT raised" (by the SDK's owner rule), by kind: `failed` with
#: `owner: agent`. Any other failed smoke is `owner: harness`. The owner decides the one remedy.
AGENT_RAISE_EXITS: dict[str, tuple[int, ...]] = {'smoke': (3,)}
#: Exit codes that mean "a turn came back silent and the smoke could not tell whose it is" (the agent, the
#: harness or the SDK), by kind: `failed` with `owner: undetermined`. Like the agent's raise, the run measures it.
OWNER_UNDETERMINED_EXITS: dict[str, tuple[int, ...]] = {'smoke': (4,)}
#: The recorded owners of a failure the run measures rather than refuses on.
RUN_MEASURED_OWNERS = ('agent', 'undetermined')
#: The typed mark on a `not_run` result whose check STARTED and ended without a verdict (a no-verdict
#: exit, an import refusal, a timeout). A plan-time `not_run` (a missing prerequisite, a host route,
#: an `--only` deselect) never carries it: that check never touched the agent.
ATTEMPTED = 'attempted'


# --------------------------------------------------------------------------------------------
# workdir + io
# --------------------------------------------------------------------------------------------


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import _platform  # noqa: E402 - the plugin's one answer to "is this native Windows"
from harness_fingerprint import detect_lang, find_workdir, resolve_workdir  # noqa: E402, F401 - the ONE cascade, bound not copied
from harness_fingerprint import fingerprint as harness_fingerprint  # noqa: E402 - the ONE harness identity
from host_effects import (  # noqa: E402 - the plugin's one host-effects table
    CONSENT_FILE, TYPING_REMEDY, UNGRANTABLE_PROGRAMS, WRITES, UnclassifiedTools, consent_required, granted, intercepted_at_agent_host, live_write_tools,
    runtime_of, typed_live_writers, typed_writes, unclassified_tools,
)
from corpus_coordinate import corpus_coordinate  # noqa: E402 - the ONE reader of the corpus markers
import gate_ledger  # noqa: E402 - the plugin's one finding contract and ledger
import agent_inventory  # noqa: E402 - the ONE reader of the agent's own inputs (C2) and production tools (C3)
import deliverables  # noqa: E402 - the ONE table of the files the report page reads, per stage
from probe_mock import _bearer_for, url_guard  # noqa: E402 - the ONE rule for where the bearer goes
from fetch_env_file import gap_reason  # noqa: E402 - the ONE reading of a witness fetch that failed
import writer_readers  # noqa: E402 - the ONE reading of which tool reads back what a write tool writes
import mock_shape_probe  # noqa: E402 - the ONE reading of the mock's answers against the agent's types the mock gate joins
import real_reader_probe  # noqa: E402 - the ONE reading of the real-reader probe the mock gate joins
import coverage_census  # noqa: E402 - the ONE count of contract units per disposition (report-only)
from _stdio import utf8_child_text  # noqa: E402 - sibling script: UTF-8 children on Windows


def manifest_path(workdir: Path) -> Path:
    return workdir / MANIFEST_FILENAME


def verification_path(workdir: Path) -> Path:
    return workdir / 'artifacts' / VERIFICATION_FILENAME


def plugin_root() -> Path:
    """The plugin checkout this script lives in — never `$CLAUDE_PLUGIN_ROOT`, which may name
    a DIFFERENT installed copy than the one whose scripts we are about to run."""
    return Path(__file__).resolve().parent.parent


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def _write_json(path: Path, payload: Any) -> None:
    """Written ATOMICALLY. A half-written verification.json parses as nothing, and the hook that
    reads it fails open on a parse error — so an interrupted write silently disarms the one rule
    that catches this file's originating failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def _invalidate(path: Path, why: str) -> None:
    """Replace a record with a REFUSAL, so no reader can mistake the last run's for this one's.

    A FAILURE HERE IS SAID, and then the stale record is REMOVED. `except OSError: pass` made the
    strongest guarantee in this file revert in total silence: on any errno the previous run's
    `verification.json` simply stayed on disk, `integrity-line` printed its sentence and exited 0
    over a scan that verified nothing, and `report_guard` then required that sentence verbatim —
    this file's own originating failure, one unhandled errno away from being reinstated by the
    very function written to end it.

    An ABSENT record is the safe state, not a worse one: `cmd_integrity_line` refuses by name on
    it. So a write that fails falls back to deleting, and a delete that fails is shouted about,
    because at that point nothing mechanical can stop the stale line being read as this run's.
    """
    try:
        _write_json(path, {'schema': VERIFICATION_SCHEMA_ID, 'ran_at': _now(), 'refused': why, 'results': [], 'fields': {}})
        return
    except OSError as exc:
        print(f'bench-manifest: could NOT write the refusal to {path} ({exc}).', file=sys.stderr)
    try:
        existed = path.exists()
        path.unlink(missing_ok=True)
    except OSError as exc:
        print(f'bench-manifest: and the stale {path.name} could NOT be removed either ({exc}). DO NOT TRUST any '
              f'integrity line read from it — it describes an EARLIER run, not this one: {why}', file=sys.stderr)
        return
    if existed:
        print(f'bench-manifest: removed the stale {path.name} instead, so nothing can read it as this run\'s '
              f'record. Re-run `verify` once the directory is writable: {why}', file=sys.stderr)


def _rewire() -> Any:
    """The agent-host route's rewire script, imported only for a bench whose tools are intercepted at the
    agent's host: it owns the ONE hash its `up` checks the host-effects list against, and no other bench
    loads it."""
    path = str(Path(__file__).resolve().parent.parent / 'skills' / 'simulation-setup-agentforce' / 'scripts')
    if path not in sys.path:
        sys.path.insert(0, path)
    import rewire_agentforce  # noqa: PLC0415

    return rewire_agentforce


def _tools_sha256(workdir: Path) -> str | None:
    """The `tools:` block hash `host_effects` is derived from (None: no config to derive it from)."""
    rewire = _rewire()
    path = workdir / 'plurai.config.yaml'
    try:
        return rewire.tools_block_sha256(path.read_text(encoding='utf-8')) if path.is_file() else None
    except rewire.RewireError:  # a block `up` cannot check faithfully gets no hash, so `up` refuses it by name
        return None


def _host_effects_hashes(topology: dict[str, Any], workdir: Path) -> dict[str, Any]:
    """What the agent-host route's `up` checks the host-effects list is current against — recorded only on
    that route, the one reader of both."""
    if not intercepted_at_agent_host(topology):
        return {}
    return {
        'host_effects_tools_sha256': _tools_sha256(workdir),  # the config's `tools:` block
        'host_effects_map_sha256': _rewire().map_writes_sha256(topology),  # …and the map's `writes:` facts
    }


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str | None:
    """The file's hash, or None when it does not exist. ABSENCE IS A VALUE — a signature over a
    deleted probe must differ from one over the probe, or a re-scan reuses a result whose script
    is gone (which is exactly how the reuse this file exists to fix went unnoticed)."""
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 16), b''):
            digest.update(block)
    return digest.hexdigest()


def _load_yaml(path: Path) -> Any:
    try:
        import yaml  # noqa: PLC0415 - optional at import time; only the config readers need it
    except ImportError:
        return None
    try:
        return yaml.safe_load(path.read_text(encoding='utf-8'))
    except Exception:
        return None


#: The bridge harness's map, relative to the workdir: its presence is what makes a bench a bridge bench.
BRIDGE_MAP = 'bridge/bridge.map.json'
#: The `language` value of the retired TypeScript-SDK harness; a manifest carrying it is refused, never aliased.
RETIRED_LANGUAGE = 'typescript'  # retired-lane-refusal


def harness_bridge(workdir: Path) -> dict[str, str] | None:
    """`harness.bridge` of a bench whose workdir holds the bridge map (`harness_fingerprint.detect_lang`'s rule), else None."""
    path = workdir / BRIDGE_MAP
    if not path.is_file():
        return None
    try:
        child = json.loads(path.read_text(encoding='utf-8')).get('child')
    except (OSError, ValueError, AttributeError):
        child = None
    return {'child': child if isinstance(child, str) and child else 'node', 'map': BRIDGE_MAP}


def bridge_resume(workdir: Path) -> dict[str, str] | None:
    """`harness.resume: unsupported` when the bridge map declares an entry with no `seed` export, citing the line.

    The entry's conversation lives in the child, which this harness cannot write without that export: the same
    `history_not_writable` the resume gate reads. None for a non-bridge bench or an entry that seeds."""
    path = workdir / BRIDGE_MAP
    try:
        text = path.read_text(encoding='utf-8')
        resume = (json.loads(text).get('entry') or {}).get('resume')
    except (OSError, ValueError, AttributeError):
        return None
    if not (isinstance(resume, dict) and 'unsupported' in resume):
        return None
    line = next((n for n, row in enumerate(text.splitlines(), 1) if '"unsupported"' in row), 1)
    return {'mode': 'unsupported', 'reason': 'history_not_writable', 'evidence': f'{BRIDGE_MAP}:{line}'}


def agent_dir(workdir: Path) -> Path:
    """The agent module — `plurai_sim`'s parent, by construction. Probes and surface scripts run
    with this as cwd, so a script's relative imports mean what they meant when it was authored."""
    return workdir.parent


def resolve_source(workdir: Path, rel: str) -> Path:
    """A manifest path (`sources.corpus_roots`, `derived_from.paths`) against the agent module
    first, then the repo root. Authors write whichever they were looking at; both resolve, and a
    path that resolves under neither is reported by `validate` rather than silently hashing None."""
    candidate = agent_dir(workdir) / rel
    # A manifest a command can write names `rel`: on Windows a UNC one is never touched (it would contact its host).
    if not _platform.is_local_drive_path(candidate, within=(agent_dir(workdir),)):
        return candidate
    if candidate.exists():
        return candidate
    top = _git_toplevel(workdir)
    if top is not None and (top / rel).exists():
        return top / rel
    return candidate


def _git_toplevel(start: Path) -> Path | None:
    try:
        out = subprocess.run(
            ['git', '-C', str(start), 'rev-parse', '--show-toplevel'], capture_output=True, text=True, **utf8_child_text(), timeout=15, check=False
        )
    except Exception:
        return None
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None


# --------------------------------------------------------------------------------------------
# schema + validation
# --------------------------------------------------------------------------------------------


def load_schema() -> dict[str, Any]:
    return json.loads((plugin_root() / 'scripts' / SCHEMA_FILENAME).read_text(encoding='utf-8'))


def _enum_findings(doc: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Required keys and enum values, read OFF the published schema rather than restated here.

    Deliberately not a general JSON-Schema engine (the plugin ships stdlib + PyYAML): it walks the
    schema's own `required` / `enum` / `type` for the shapes this document uses. The schema file is
    therefore the contract and not decoration — `test_bench_manifest.py` asserts the two agree.
    """
    findings: list[str] = []

    def walk(node: Any, spec: dict[str, Any], path: str) -> None:
        if not isinstance(spec, dict):
            return
        if 'const' in spec and node != spec['const']:
            findings.append(f'{path or "<root>"}: must be {spec["const"]!r}, found {node!r}')
            return
        if 'enum' in spec and node not in spec['enum']:
            findings.append(f'{path}: {node!r} is not one of {spec["enum"]}')
            return
        expected = spec.get('type')
        if isinstance(expected, list):
            # `["string", "null"]`. Matched by NO branch below, so six fields were walked past
            # entirely — and a `finding:` written as a dict then reached `.strip()` in
            # `command_for` as an uncaught AttributeError: a traceback, no verification.json, and
            # Python's exit 1, which this runner's own table calls "a blocking check FAILED".
            ok = {'string': str, 'boolean': bool, 'integer': int, 'array': list, 'object': dict}
            if node is not None and not any(isinstance(node, ok[e]) for e in expected if e in ok):
                findings.append(f'{path}: expected one of {expected}, found {type(node).__name__}')
            return
        if expected == 'object':
            if not isinstance(node, dict):
                findings.append(f'{path or "<root>"}: expected an object, found {type(node).__name__}')
                return
            for key in spec.get('required', []):
                if key not in node:
                    findings.append(f'{path or "<root>"}: missing required key {key!r}')
            props = spec.get('properties') or {}
            if spec.get('additionalProperties') is False:
                for key in node:
                    if key not in props:
                        findings.append(f'{path or "<root>"}: unknown key {key!r}')
            for key, sub in props.items():
                if key in node:
                    walk(node[key], sub, f'{path}.{key}' if path else key)
        elif expected == 'array':
            if not isinstance(node, list):
                findings.append(f'{path}: expected a list, found {type(node).__name__}')
                return
            item_spec = spec.get('items')
            if isinstance(item_spec, dict):
                for index, item in enumerate(node):
                    walk(item, item_spec, f'{path}[{index}]')
        elif expected == 'string' and not isinstance(node, str):
            findings.append(f'{path}: expected a string, found {type(node).__name__}')
        elif expected == 'boolean' and not isinstance(node, bool):
            findings.append(f'{path}: expected a boolean, found {type(node).__name__}')
        elif expected == 'integer' and (isinstance(node, bool) or not isinstance(node, int)):
            findings.append(f'{path}: expected an integer, found {type(node).__name__}')

    walk(doc, schema, '')
    return findings


def _sentinel_findings(doc: Any, path: str = '') -> list[str]:
    """Every place an `init` left `TODO` for the analysis to decide."""
    findings: list[str] = []
    if isinstance(doc, dict):
        for key, value in doc.items():
            findings += _sentinel_findings(value, f'{path}.{key}' if path else key)
    elif isinstance(doc, list):
        for index, value in enumerate(doc):
            findings += _sentinel_findings(value, f'{path}[{index}]')
    elif doc == SENTINEL:
        findings.append(f'{path}: still {SENTINEL!r} — the analysis has to decide this before anything is verified')
    return findings


#: The workdir's declared mock posture. The SDK reads it as rung 3 of `--mock-mode` > `$NETWORK__MOCK_MODE` >
#: `.run_mode` > `connect` (`run_simulation._sidecar_mock_mode`); `run-mode` and `init` are its one writer.
RUN_MODE_MARKER = '.run_mode'
MOCKLESS = 'mockless'
#: The world owners whose store the harness holds; beside `owned_types` the mock holds every other declared type.
_HARNESS_WORLD_OWNERS = ('agent', 'external')


def _reads_mocked(spec: dict[str, Any]) -> bool:
    """`ToolDescriptor.effective_read_strategy == 'mocked'`: explicit, or the default a non-empty `reads_from` implies."""
    descriptor = spec.get('descriptor')
    descriptor = descriptor if isinstance(descriptor, dict) else {}
    explicit = descriptor.get('read_strategy')
    if isinstance(explicit, str) and explicit:
        return explicit == 'mocked'
    reads_from = descriptor.get('reads_from')
    return isinstance(reads_from, str) and bool(reads_from.strip())


def mock_needs(config: dict[str, Any]) -> list[str]:
    """What on this bench needs a mocked world, named; empty means nothing does (`mockless`).

    A tool in any mode but `real` (`observe` records into the mock's world), a `real` tool whose reads come from the
    generated world-state adapter, and the entity types a split world leaves to the mock."""
    tools = config.get('tools')
    tools = tools if isinstance(tools, dict) else {}
    needs = [f'tool {name}' for name, spec in sorted(tools.items())
             if not isinstance(spec, dict) or spec.get('mode', 'mock') != 'real' or _reads_mocked(spec)]
    world = config.get('world_state')
    world = world if isinstance(world, dict) else {}
    owner = str(world.get('owner') or '').strip().lower()
    owned, types = world.get('owned_types'), world.get('entity_types')
    if owner in _HARNESS_WORLD_OWNERS and isinstance(owned, list) and isinstance(types, dict):
        needs += [f'world type {name}' for name in sorted(set(types) - {str(t) for t in owned})]
    return needs


def derive_run_mode(config: dict[str, Any]) -> str:
    return 'mock' if mock_needs(config) else MOCKLESS


def sync_run_mode_marker(workdir: Path, run_mode: str) -> str:
    """Make `.run_mode` say the derived posture; return what was done, one line.

    `mockless` is written over anything else (an older `local` claimed a held-constant world nothing holds). On a
    mocked bench only a stale `mockless` is removed: `local` / `connect` are postures for a mocked world, kept."""
    marker = workdir / RUN_MODE_MARKER
    lines = marker.read_text(encoding='utf-8').splitlines() if marker.is_file() else []
    current = lines[0].strip().lower() if lines else ''
    if run_mode == MOCKLESS:
        if current == MOCKLESS:
            return f'{RUN_MODE_MARKER} already says {MOCKLESS}'
        marker.write_text(f'{MOCKLESS}\n', encoding='utf-8')
        return f'wrote {RUN_MODE_MARKER} = {MOCKLESS}' + (f' (was {current!r})' if current else '')
    if current == MOCKLESS:
        marker.unlink()
        return f'removed a stale {RUN_MODE_MARKER} ({MOCKLESS}): this bench mocks something'
    return f'{RUN_MODE_MARKER} ' + (f'kept ({current!r})' if current else 'absent (connect, the default)')


def _cross_artifact_findings(doc: dict[str, Any], workdir: Path) -> list[str]:
    """The manifest, `plurai.config.yaml` and the topology must name the SAME surfaces.

    This is the check nothing did: a config regenerated without the map (or a map approved and
    then edited) left two files disagreeing about what runs for real, and the disagreement only
    surfaced as a scoring anomaly weeks later.
    """
    findings: list[str] = []
    config = _load_yaml(workdir / 'plurai.config.yaml')
    if not isinstance(config, dict):
        return ['plurai.config.yaml: absent or unreadable, so the manifest agrees with nothing']

    tools_cfg = config.get('tools')
    tools_cfg = tools_cfg if isinstance(tools_cfg, dict) else {}
    declared_modes = {str(t.get('name')): str(t.get('mode')) for t in doc['surfaces']['tools']}
    config_modes = {name: (spec.get('mode', 'mock') if isinstance(spec, dict) else 'mock') for name, spec in tools_cfg.items()}
    if declared_modes != config_modes:
        # MODES, not just names. "What runs for real" is the mode; comparing name sets alone let a
        # manifest say `mock` over an all-real config, which flips `harness.run_mode` and silently
        # retires the only live gate a mockless bench has.
        findings.append(f'surfaces.tools: manifest {sorted(declared_modes.items())} != plurai.config.yaml {sorted(config_modes.items())}')
    derived_run_mode = derive_run_mode(config)
    if doc['harness']['run_mode'] != derived_run_mode:
        findings.append(f'harness.run_mode: manifest {doc["harness"]["run_mode"]!r} != {derived_run_mode!r} derived from plurai.config.yaml')

    declared_verbs = [str(v.get('name')) for v in doc['surfaces']['render_actions']]
    config_verbs = config.get('render_actions')
    config_verbs = [str(v) for v in config_verbs] if isinstance(config_verbs, list) else []
    if sorted(declared_verbs) != sorted(config_verbs):
        findings.append(
            f'surfaces.render_actions: manifest {sorted(declared_verbs)} != plurai.config.yaml render_actions {sorted(config_verbs)}'
        )
    findings += _agent_context_findings(doc, config)

    # The map's `writes: live` offers the grant and the config's typed write enforces it: two
    # declarations of one fact, so any disagreement is refused, in either direction.
    topology = _read_json(workdir / 'artifacts' / 'agent_topology.json')
    if isinstance(topology, dict):
        findings += _live_writer_findings(topology, config)

    run_mode_marker = workdir / RUN_MODE_MARKER
    if run_mode_marker.is_file():
        marker = run_mode_marker.read_text(encoding='utf-8').strip()
        # `local` is what older workdirs wrote for the same posture — accepted, never re-emitted.
        if marker and marker not in {doc['harness']['run_mode'], 'local'}:
            findings.append(f'harness.run_mode: manifest {doc["harness"]["run_mode"]!r} != .run_mode marker {marker!r}')
    return findings


#: The run clock (the backend's `RUN_CLOCK_KEYS`): every scenario pins it, so declaring it
#: seeds nothing the agent's own prior flow produced.
RUN_CLOCK_KEYS = ('current_date', 'today')


def _agent_context_findings(doc: dict[str, Any], config: dict[str, Any]) -> list[str]:
    """`agent_context_undeclared`: the scan recorded that the agent reads turn-0 state (`finding: null`) and the
    config declares nothing to seed it. Every later stage builds only on what the config declares."""
    if str(doc['surfaces']['agent_context'].get('finding') or '').strip():
        return []
    context = config.get('agent_context')
    fields = context.get('fields') if isinstance(context, dict) else None
    if isinstance(fields, dict) and any(str(name) not in RUN_CLOCK_KEYS for name in fields):
        return []
    return [
        'agent_context_undeclared: the scan recorded that the agent reads turn-0 state, and the config declares no '
        '`agent_context` field, so every scenario opens on a fresh agent. The env build takes its fields from this '
        'config and this scan\'s PRD. Declare the turn-0 keys a prior flow produces (setup Step 4b) and write them '
        'where the agent reads them in `seed_state`, or record the search that shows no turn-0 state must come from '
        'a caller or a prior flow (`surfaces.agent_context.finding`)'
    ]


def _live_writer_findings(topology: dict[str, Any], config: dict[str, Any]) -> list[str]:
    """The map and the config must name the same live writers, spelled the same, in the typed vocabulary."""
    remedy = 'fix the map (scan Step 2a), re-render it (Step 2b) and re-run `bench_manifest.py init`'
    tools = [t for t in topology.get('tools') or [] if isinstance(t, dict)]
    findings = []
    if untyped := sorted(str(t.get('name')) for t in tools if 'writes' in t and t['writes'] not in WRITES):
        findings.append(f'agent_topology.json: `writes` is one of {", ".join(WRITES)}; {untyped} carry another value; {remedy}')
    configured = {str(name) for name in (config.get('tools') if isinstance(config.get('tools'), dict) else {})}
    folded = {name.lower(): name for name in configured}
    if by_case := sorted(f'{t.get("name")} (config: {folded[str(t.get("name")).lower()]})' for t in tools
                         if str(t.get('name')) not in configured and str(t.get('name')).lower() in folded):
        findings.append(f'agent_topology.json names {by_case} only by case, so neither declaration reaches the other; spell each '
                        f'as plurai.config.yaml does; {remedy}')
    if unclassified := unclassified_tools(topology, config):
        findings.append(f'agent_topology.json does not classify {unclassified}, which the bench would run for real, so nobody knows what '
                        f'they do on this machine and none may run by default; {TYPING_REMEDY}')
    written = set(live_write_tools(config, at_agent_host=intercepted_at_agent_host(topology), typed_writes=typed_writes(topology)))
    typed = set(typed_live_writers(topology))
    if written != typed:
        findings.append(f'surfaces.host_effects: the live writers disagree — plurai.config.yaml writes the live system through '
                        f'{sorted(written - typed)} that agent_topology.json does not type `writes: live`, and the map types '
                        f'{sorted(typed - written)} `writes: live` that the config does not type as live writes; correct the '
                        'wrong side, then re-render the map (scan Step 2b) and re-run `bench_manifest.py init`')
    return findings


#: Where `init` and `agent_inventory.py extract` leave the reader's output; `validate` re-reads the source itself.
INVENTORY_REMEDY = '`agent_inventory.py extract` lists what the source carries, `bench_manifest.py init` copies it'


#: `coverage_holes[].mechanism` of a hole the config's own `seam: tool_body` declares (derived; the rest are accepted gaps).
SEAM_HOLE_PREFIX = 'seam:tool_body:'
_CODE_CITE = re.compile(r'^[^\s:]+:\d+(-\d+)?$')


def seam_coverage_holes(config: Any) -> list[dict[str, str]]:
    """C12: one `coverage_holes[]` row per `skipped_code` cite of a `seam: tool_body` mock — the agent code no run executes."""
    tools = config.get('tools') if isinstance(config, dict) else None
    return [
        {'mechanism': f'{SEAM_HOLE_PREFIX}{name}', 'source': cite, 'reason': str(entry.get('seam_reason') or '')}
        for name, entry in sorted((tools if isinstance(tools, dict) else {}).items())
        if isinstance(entry, dict) and entry.get('seam') == 'tool_body'
        for item in (entry.get('skipped_code') if isinstance(entry.get('skipped_code'), list) else [])
        for cite in [item.get('cite') if isinstance(item, dict) else item] if isinstance(cite, str)  # a typed row-source answer's `cite`
    ]


def merged_coverage_holes(fresh: list[dict[str, str]], existing: Any) -> list[dict[str, str]]:
    """The config's derived holes, then every accepted gap the suite recorded (kept across a refresh)."""
    accepted = [h for h in existing if isinstance(h, dict) and not str(h.get('mechanism', '')).startswith(SEAM_HOLE_PREFIX)] if isinstance(existing, list) else []
    return fresh + accepted


def _coverage_hole_findings(doc: dict[str, Any], workdir: Path) -> list[str]:
    """Every `tool_body` cite in the config is a recorded hole; every recorded source is a `path:line` cite."""
    recorded = {(h['mechanism'], h['source']) for h in doc.get('coverage_holes') or []}
    findings = [f'coverage_hole_unrecorded: {h["mechanism"]} skips {h["source"]} and coverage_holes does not record it — re-run `bench_manifest.py init`'
                for h in seam_coverage_holes(_load_yaml(workdir / 'plurai.config.yaml')) if (h['mechanism'], h['source']) not in recorded]
    findings += [f'coverage_holes[{i}].source {h["source"]!r} is not `<path>:<line>`' for i, h in enumerate(doc.get('coverage_holes') or [])
                 if not _CODE_CITE.match(h['source'])]
    return findings


def _io_declaration(config: dict[str, Any]) -> dict[str, Any]:
    """The root `agent.io` block as the SDK reads it: absent or bare is the defaults (`input: [text]`)."""
    block = config.get('agent.io')
    return block if isinstance(block, dict) else {}


def modelled_by(entry: dict[str, Any], config: dict[str, Any]) -> str | None:
    """What models a C2 entry: the `agent.io.input` part or the `agent.io.channels` entry of its kind, or None."""
    io = _io_declaration(config)
    part = agent_inventory.PART_KINDS.get(str(entry.get('kind')))
    if part is not None:
        parts = io.get('input')
        return f'agent.io.input {part}' if part in (parts if isinstance(parts, list) else ['text']) else None
    names = {str(entry.get('channel') or ''), str(entry.get('name'))} - {''}
    channels = io.get('channels')
    for channel in channels if isinstance(channels, list) else []:
        if isinstance(channel, dict) and channel.get('kind') == entry.get('kind') and names & {str(channel.get('name')), str(channel.get('field'))}:
            return f'agent.io.channels {channel.get("name")}'
    return None


def _source_key(entry: dict[str, Any]) -> tuple[str, str]:
    return str(entry.get('name')), str(entry.get('source') or '').rpartition(':')[0]


#: Why the AGENT never receives an input (C2 `waived.because`) — a closed set, each cited at an agent line. What the
#: harness drives or chooses is never one: a harness choice narrows the test, not the agent.
WAIVER_BECAUSE = ('not_reachable_in_production', 'internal_field', 'tool_argument', 'transport_only', 'harness_session_state')
#: `waived.because: operator_answer:<id>` — the operator excluded the input; the answer id is its citation.
OPERATOR_ANSWER = 'operator_answer:'
WAIVER_REMEDY = (f'type it `waived: {{because, cite: "<path>:<line>", detail}}` with `because` one of {", ".join(WAIVER_BECAUSE)} or '
                 f'{OPERATOR_ANSWER}<id>; what the harness drives is never a reason — model the input instead')


def harness_cite(doc: dict[str, Any], workdir: Path, cite: str) -> str | None:
    """Why ``cite`` is no line of the AGENT, or None: it names no line, or it sits in the harness workdir, outside the
    manifest's `agent_root` (a wrapper or a config beside the agent's checkout), or in its `bench_overlay` (a file git
    says the bench wrote). Each is a harness choice: it narrows the test, never the agent."""
    rel = cite.rpartition(':')[0]
    cited = resolve_source(workdir, rel).resolve() if rel else None
    if _cited_line(workdir, cite) is None or cited is None:
        return 'which is no line of the agent\'s own files'
    if workdir.resolve() in cited.parents:
        return 'which is no line of the agent\'s own files: it is the harness\'s'
    root = agent_inventory.find_root(workdir)[0]
    if root is None:
        return None
    top = (root / str(doc.get('agent_root') or '.')).resolve()
    if cited != top and top not in cited.parents:
        return f'which is outside the agent\'s tree ({doc.get("agent_root")}): a bench file is a harness choice'
    if os.path.relpath(cited, root) in set(doc.get('bench_overlay') or []):
        return 'which is a bench overlay file (git: not the customer\'s): a harness choice, which needs the operator\'s answer (harness.capability_overrides)'
    return None


#: Waivers that claim the agent's behaviour never depends on the input: refused where the reader saw it steer.
REACH_REFUTES = ('transport_only', 'harness_session_state', 'internal_field')


def _waiver_problem(entry: dict[str, Any], workdir: Path, doc: dict[str, Any], read: dict[str, Any] | None = None, *,
                    inventory: dict[str, Any] | None = None) -> str | None:
    """Why a `waived` C2 entry does not hold: untyped, a `because` outside the closed set, a cite that is no line of
    the agent's own files (`harness_cite`), or a reason the reader's own read refutes (``read``: the input reaches a
    branch or the prompt, or an in-repo caller sends it). An operator-answer waiver holds only when the scope record
    of that answer lists the input by name (`run_driver_record`, re-checked against ``inventory``)."""
    waived = entry.get('waived')
    if not isinstance(waived, dict):
        return f'is waived untyped{" (a free-text `reason`)" if entry.get("reason") else ""}'
    because = str(waived.get('because') or '').strip()
    if because.startswith(OPERATOR_ANSWER):
        answer = because[len(OPERATOR_ANSWER):].strip()
        if not answer:
            return 'is waived on an operator answer with no id'
        kind = str((read or {}).get('kind') or entry.get('kind'))
        if kind in _content_kinds():  # whatever a scope record lists: content the agent takes is never scoped out
            return f'is waived on the operator answer `{answer}`, and it is a {kind} the agent takes: its kind is modelled, never scoped out — model it'
        if _listed(answer, 'inputs', {str(entry.get('name') or '')}, run_driver_record(workdir, _scope_inventory(workdir, doc, inventory))):
            return None
        return f'is waived on the operator answer `{answer}`, and ' + _unbacked_cut(answer, 'this input', 'name it')
    if because not in WAIVER_BECAUSE:
        return f'is waived because {because!r}, which is not why an agent never receives an input'
    read = read or {}
    if because in REACH_REFUTES and read.get('reaches'):
        return f'is waived {because}, yet it steers the agent at {read["reaches"]}: model it'
    if because == 'not_reachable_in_production' and read.get('sent_by'):
        return f'is waived {because}, yet {read["sent_by"]} sends it: model it'
    cite = str(waived.get('cite') or '')
    if why := harness_cite(doc, workdir, cite):
        return f'cites {cite!r}, {why}'
    return None


def _hooks_module(name: str) -> Any:
    """A module of the plugin's `hooks/` (only on sys.path here), or None when it cannot be imported."""
    try:
        hooks = str(plugin_root() / 'hooks')
        if hooks not in sys.path:
            sys.path.insert(0, hooks)
        return importlib.import_module(name)
    except Exception:  # noqa: BLE001 - the callers fail closed on None
        return None


def _scope_gates() -> dict[str, frozenset[str]]:
    """`hooks/_run_driver.py` SCOPE_GATES: the gates whose record may back a scope cut. Empty when unreadable."""
    run_driver = _hooks_module('_run_driver')
    return dict(run_driver.SCOPE_GATES) if run_driver is not None else {}


def _content_kinds() -> tuple[str, ...]:
    import scope_proposal  # noqa: PLC0415 - scripts/ is this file's own directory

    return scope_proposal.CONTENT_KINDS


def _scope_inventory(workdir: Path, doc: dict[str, Any] | None = None, inventory: dict[str, Any] | None = None) -> dict[str, Any]:
    """The inventory a scope record's items are re-checked against: the one the caller loaded, else the inventory artifact,
    else the manifest's own copy of it (`surfaces.inputs` and `surfaces.production_tools`)."""
    if isinstance(inventory, dict):
        return inventory
    stored = agent_inventory.read_artifact(workdir)
    if stored is not None:
        return stored
    surfaces = (doc or {}).get('surfaces') if isinstance((doc or {}).get('surfaces'), dict) else {}
    return {'inputs': surfaces.get('inputs') or [], 'production_tools': surfaces.get('production_tools') or {}}


def run_driver_record(workdir: Path, inventory: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The records that may back a scope cut (`hooks/_run_driver.py` FINDINGS_MARKER rows, across the session's flow
    dir and `<workdir>/artifacts`): per scope gate (`SCOPE_GATES`), its LATEST answered or defaulted row — an approval
    (`hooks/ask_answered.py`) or a default the declaration applied (`_run_driver.scope_of_default`), each carrying the
    typed items the scope proposal named. A later decline or a bare default revokes an earlier one, so what backs is
    what the run's end and run.json name. Any other gate backs nothing: a `blocker`'s answer is never a scope approval.

    The items are re-checked against ``inventory`` (`_scope_inventory` when None) the way `scope_proposal.py` checks a
    proposal (`scope_proposal.admitted`): the proposal file can be written by hand, and an item that script refuses — a
    content kind, a name the inventory never found — backs nothing. `[]` when none was filed."""
    run_driver = _hooks_module('_run_driver')
    if run_driver is None:
        return []
    try:
        bases = [*run_driver.marker_bases(workdir), workdir / 'artifacts']
        rows = run_driver.read_findings(list(dict.fromkeys(bases)))
    except Exception:  # noqa: BLE001 - no record readable is no answer on record
        return []
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:  # oldest first (`read_findings`)
        if isinstance(row, dict) and row.get('kind') in (run_driver.ASK_ANSWERED, run_driver.DEFAULT_APPLIED) and row.get('gate') in run_driver.SCOPE_GATES:
            latest[str(row['gate'])] = row
    if not latest:
        return []
    import scope_proposal  # noqa: PLC0415 - scripts/ is this file's own directory

    view = _scope_inventory(workdir, None, inventory)
    return [{**row, **scope_proposal.admitted(view, row)} for row in latest.values()]


def _unbacked_cut(answer: str, what: str, name_it: str, otherwise: str = 'model it') -> str:
    """The second half of a refusal for a cut `answer` does not back: no scope gate at all, or not this item."""
    gates = _scope_gates()
    if answer not in gates:
        scope = ' or '.join(f'`{g}`' for g in sorted(gates)) or 'the scope gate'
        return (f'`{answer}` is no gate whose answer carries a scope: only {scope} backs a cut — its approval, or its default when '
                'nobody is asked — and only for the items its scope proposal named. Never relabel a cut onto another gate: '
                f'{name_it} in the scope proposal (scope_proposal.py write) before that gate, or {otherwise}')
    return (f'no recorded scope of `{answer}` names {what}: an approval, or its default, backs only the items its scope '
            f'proposal named — {name_it} there (scope_proposal.py write) and sanction `{answer}` again, or {otherwise}')


def _listed(answer: str, key: str, names: set[str], record: list[dict[str, Any]]) -> bool:
    """Whether the scope record of `answer` (`run_driver_record`) lists one of ``names`` under ``key``: an input waiver
    reads `inputs`, a switch reads `switches` — never across kinds, never question prose, and a declined answer backs
    nothing."""
    return any(row.get('gate') == answer and row.get('approved') is not False and names & {str(n) for n in row.get(key) or []}
               for row in record)


def answered_overrides(doc: dict[str, Any], workdir: Path, inventory: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The `harness.capability_overrides` entries the scope record backs (`run_driver_record`), by the scope of its
    `answer_id`: a switch the agent inventory found must be listed in its `switches`, with every tool the entry names in
    its `tools`; a setting the inventory does not list (a config key) is backed by its tools alone — a non-empty `tools`,
    every one listed. An approval or a default that listed neither switches nothing off."""
    view = _scope_inventory(workdir, doc, inventory)
    record = run_driver_record(workdir, view)
    reader = {str(s.get('switch')) for s in (view.get('production_tools') or {}).get('switches') or [] if isinstance(s, dict)}

    def backed(override: dict[str, Any], answer: str) -> bool:
        if not _tools_named(answer, override, record):
            return False
        if str(override.get('switch')) in reader:
            return _listed(answer, 'switches', {str(override.get('switch'))}, record)
        return bool(override.get('tools'))

    return [o for o in (doc.get('harness') or {}).get('capability_overrides') or []
            if isinstance(o, dict) and (answer := str(o.get('answer_id') or '').strip()) and backed(o, answer)]


def _tools_named(answer: str, override: dict[str, Any], record: list[dict[str, Any]]) -> bool:
    """An override that lists the `tools` it switches off is backed only when the scope record of `answer` lists every
    one: the proposal named those toolsets, never one more (a record that lists none backs none)."""
    listed = [set(map(str, row.get('tools') or [])) for row in record if row.get('gate') == answer and row.get('approved') is not False]
    return not override.get('tools') or any(set(map(str, override['tools'])) <= tools for tools in listed)


def _switch_answered(doc: dict[str, Any], switch: str, *, workdir: Path, inventory: dict[str, Any] | None = None) -> bool:
    return any(o.get('switch') == switch for o in answered_overrides(doc, workdir, inventory))


def selector_findings(doc: dict[str, Any], inventory: dict[str, Any], declared: dict[tuple[str, str], dict[str, Any]], *,
                      workdir: Path) -> list[tuple[tuple[str, str], str]]:
    """A mode selector (C3 `kind: selector`) is a capability switch at EVERY production value: its field and the inputs
    a value's branch reaches are the agent's, so neither is waived (save on an operator answer naming the switch or
    the input). Covering each value is the harness's job. Returns ``(declared key, finding)`` pairs."""
    findings: list[tuple[tuple[str, str], str]] = []
    by_name: dict[str, list[tuple[tuple[str, str], dict[str, Any]]]] = {}
    for key, entry in declared.items():
        by_name.setdefault(str(entry.get('name')), []).append((key, entry))
    for switch in (s for s in (inventory.get('production_tools') or {}).get('switches') or [] if s.get('kind') == 'selector'):
        if _switch_answered(doc, switch['switch'], workdir=workdir, inventory=inventory):
            continue
        values = ', '.join(v['value'] for v in switch.get('values') or [])
        reach = [(switch['field'], None), *((name, value) for value, names in (switch.get('selects') or {}).items() for name in names)]
        for name, value in reach:
            for key, entry in by_name.get(name, []):
                because = str((entry.get('waived') or {}).get('because') or '') if isinstance(entry.get('waived'), dict) else ''
                if entry.get('status') != 'waived' or because.startswith(OPERATOR_ANSWER):
                    continue
                what = (f'selects between the production task types ({values}) at {switch["source"]}' if value is None
                        else f'is reached on the production task type `{value}` ({switch["field"]} at {switch["source"]})')
                findings.append((key, f'input_channel_unaccounted: surfaces.inputs `{name}` ({entry.get("source")}) {what}, so the agent '
                                 f'receives it in production. `{switch["switch"]}` is a capability switch: model it and cover each value, or '
                                 "record the operator's answer (harness.capability_overrides)"))
    return findings


def input_channel_findings(doc: dict[str, Any], inventory: dict[str, Any], config: dict[str, Any], workdir: Path) -> list[str]:
    """`input_channel_unaccounted`: an input the agent's source accepts that no `surfaces.inputs` entry accounts for,
    a `modelled` entry nothing models, a `waived` one whose typed waiver does not hold, or a waived input a production
    mode selector reaches. Keyed by name and file, so a moved line or a reclassified kind is still the same input."""
    if 'inputs' not in doc['surfaces'] and inventory.get('inputs'):
        return [f'input_channel_unaccounted: surfaces.inputs is absent and the agent\'s source accepts {len(inventory["inputs"])} input(s) '
                f'({", ".join(sorted({e["kind"] for e in inventory["inputs"]}))}); {INVENTORY_REMEDY}, then decide each one']
    declared = {_source_key(e): e for e in doc['surfaces'].get('inputs') or [] if isinstance(e, dict)}
    findings = [
        f'input_channel_unaccounted: {e["kind"]} `{e["name"]}` ({e["source"]}: {e.get("evidence", "")}) is in no surfaces.inputs entry; '
        f'{INVENTORY_REMEDY}, then mark it `modelled` (declare its part or channel, simulation-setup Step 8) or `waived` (typed)'
        for e in inventory.get('inputs') or [] if _source_key(e) not in declared
    ]
    reads = {_source_key(e): e for e in inventory.get('inputs') or []}
    # A production mode selector's finding already says the input steers the agent (and names the switch): its
    # reach is not reported twice. The waiver's form is still checked.
    selected = selector_findings(doc, inventory, declared, workdir=workdir)
    by_selector = {key for key, _ in selected}
    for key, entry in declared.items():
        where = f'surfaces.inputs `{entry.get("name")}` ({entry.get("source")})'
        read = {k: v for k, v in (reads.get(key) or {}).items() if k != 'reaches'} if key in by_selector else reads.get(key)
        if entry.get('status') == 'waived' and (problem := _waiver_problem(entry, workdir, doc, read, inventory=inventory)):
            findings.append(f'input_channel_unaccounted: {where} {problem}; {WAIVER_REMEDY}')
        elif entry.get('status') == 'modelled' and modelled_by(entry, config) is None:
            kind = entry.get('kind')
            need = (f'`{agent_inventory.PART_KINDS[kind]}` in agent.io.input' if kind in agent_inventory.PART_KINDS
                    else f'agent.io.channels entry of kind {kind} named or fielded `{entry.get("name")}` (or set the entry\'s `channel`)')
            findings.append(f'input_channel_unaccounted: {where} is marked modelled, and plurai.config.yaml declares no {need}')
    return findings + [finding for _, finding in selected]


def production_coverage(doc: dict[str, Any], present: set[str], *, workdir: Path, inventory: dict[str, Any] | None = None) -> dict[str, Any]:
    """`agent_inventory.production_coverage` of ``present`` against the manifest's C3 set, its cited `not_loaded` groups
    and the `harness.capability_overrides` the run driver record backs — the ONE "N of M" the map and the validator print."""
    production = doc['surfaces'].get('production_tools') or {}
    return agent_inventory.production_coverage(production, present, answered_overrides(doc, workdir, inventory))


def dropped_tools(doc: dict[str, Any], present: set[str], *, workdir: Path) -> list[str]:
    """The C3 tools ``present`` lacks that neither an operator's override nor a cited production default accounts for."""
    return production_coverage(doc, present, workdir=workdir)['dropped']


def production_tool_findings(doc: dict[str, Any], inventory: dict[str, Any], workdir: Path) -> list[str]:
    """`production_tool_dropped`: the map carries fewer of the agent's production tools than its source defines, with no
    override an operator answered. Also refuses a stale `surfaces.production_tools`, an override with no answer, and a
    `not_loaded` group whose citation names no agent line (the harness's own files never count)."""
    fresh = inventory.get('production_tools') or {}
    stored = doc['surfaces'].get('production_tools')
    findings: list[str] = []
    if not isinstance(stored, dict) or stored.get('inventory_sha256') != agent_inventory.digest(inventory) \
            or sorted(stored.get('names') or []) != sorted(fresh.get('names') or []):
        findings.append(f'production_tool_dropped: surfaces.production_tools is {"absent" if not isinstance(stored, dict) else "stale"} — it is not '
                        f'the reading of artifacts/{agent_inventory.INVENTORY_FILENAME} (the agent\'s source defines {fresh.get("count", 0)} tool(s) '
                        f'under its production defaults); {INVENTORY_REMEDY}')
    if not isinstance(stored, dict):
        return findings
    switches = {s.get('switch') for s in stored.get('switches') or [] if isinstance(s, dict)}
    answered = [id(o) for o in answered_overrides(doc, workdir, inventory)]
    for o in doc['harness'].get('capability_overrides') or []:
        if not str(o.get('answer_id') or '').strip():
            findings.append(f'production_tool_dropped: harness.capability_overrides `{o.get("switch")}` carries no answer_id — disabling a '
                            "capability needs the operator's answer naming it")
        elif id(o) not in answered:
            # A switch the reader found is named in the proposal's `--switch`; a setting it does not list (a config key)
            # is cut by the production tools it holds, named with `--tool` (`answered_overrides`).
            what, name_it = (('that switch and its tools', 'name the switch and its tools') if o.get('switch') in switches else
                             ("that setting's tools", f'`{o.get("switch")}` is a setting the inventory does not list: name its tools (--tool)'))
            findings.append(f'production_tool_dropped: harness.capability_overrides `{o.get("switch")}` rests on the operator answer '
                            f'`{o.get("answer_id")}`, and ' + _unbacked_cut(str(o.get('answer_id')), what, name_it, 'put its tools on the map'))
    by_switch = {s.get('switch'): s for s in stored.get('switches') or [] if isinstance(s, dict)}
    for group in stored.get('not_loaded') or []:
        if problem := _not_loaded_problem(group if isinstance(group, dict) else {}, by_switch, doc, workdir):
            findings.append(f'production_tool_dropped: surfaces.production_tools.not_loaded {group.get("names") if isinstance(group, dict) else group} '
                            f'{problem}; {NOT_LOADED_REMEDY}')
    topology = _read_json(workdir / 'artifacts' / 'agent_topology.json')
    if isinstance(topology, dict):
        coverage = production_coverage(doc, agent_inventory.map_tool_names(topology), workdir=workdir, inventory=inventory)
        if dropped := coverage['dropped']:
            total = coverage['total']
            findings.append(
                f'production_tool_dropped: the map carries {total - len(dropped) - len(coverage["exempt"])} of {total} production tools '
                f'({len(coverage["exempt"])} off by default); {", ".join(dropped[:12])}'
                f'{f" and {len(dropped) - 12} more" if len(dropped) > 12 else ""} are missing. Mock the backend, never the tool set: put '
                'each on the map, or record the operator\'s answer disabling its switch (harness.capability_overrides), or cite the '
                'agent line that keeps it off by default (surfaces.production_tools.not_loaded)'
            )
    return findings


#: C12 seams beneath the tool: such a config entry mocks a transport, client or entity the agent's tools call, so it
#: is a backend of a production tool, never one itself.
BACKEND_SEAMS = ('transport', 'client', 'entity')


def backend_tools(production: dict[str, Any], config: dict[str, Any]) -> set[str]:
    """The bench's tool entries that are backends, not model tools: an installed tool class the agent's code calls
    itself (`production_tools.backends`, by name or by the config descriptor's class), or a config entry mocked at a
    C12 seam beneath the tool."""
    backends = [b for b in production.get('backends') or [] if isinstance(b, dict)]
    names = {str(b['name']) for b in backends if b.get('name')}
    classes = {str(b.get('class')) for b in backends}
    tools = config.get('tools') if isinstance(config.get('tools'), dict) else {}
    for name, spec in tools.items():
        descriptor = spec.get('descriptor') if isinstance(spec, dict) and isinstance(spec.get('descriptor'), dict) else {}
        owner = f'{descriptor.get("original_module")}.{str(descriptor.get("original_function") or "").split(".")[0]}'
        if isinstance(spec, dict) and (spec.get('seam') in BACKEND_SEAMS or owner in classes):
            names.add(str(name))
    return names


def live_enumeration_recorded(production: dict[str, Any], workdir: Path) -> bool:
    """Whether the setup smoke recorded the agent's live tool list. A recorded EMPTY list (`live: []` with the
    `live_enumerated_by` call that built it) is a tool-less agent's honest enumeration, distinct from no `live` key at
    all; a non-empty list is recorded on its own. The source is read off the manifest's copy, else off the one
    inventory artifact it copies."""
    live = production.get('live')
    if not isinstance(live, list):
        return False
    if live:
        return True
    source = production.get('live_enumerated_by')
    if not source:
        stored = (agent_inventory.read_artifact(workdir) or {}).get('production_tools')
        stored = stored if isinstance(stored, dict) else {}
        source = stored.get('live_enumerated_by') if stored.get('live') == [] else None
    return isinstance(source, str) and bool(source.strip())


def production_inventory_findings(doc: dict[str, Any], config: dict[str, Any], workdir: Path) -> list[str]:
    """`production_inventory_incomplete`: a tool the bench carries — a config `tools:` entry, a tool or sub-agent call
    on the map, a live-enumerated tool — that C3 (`names`), a cited `not_loaded` group, an operator's override and the
    backends all leave unaccounted is a reader blind spot, never a pass; and a registrar the reader cannot enumerate
    (`dynamic`) needs the live enumeration at production defaults."""
    production = doc['surfaces'].get('production_tools')
    if not isinstance(production, dict):
        return []
    topology = _read_json(workdir / 'artifacts' / 'agent_topology.json')
    topology = topology if isinstance(topology, dict) else {}
    on_map = {str(t.get('name')) for t in topology.get('tools') or [] if isinstance(t, dict)}
    on_map |= {str(t.get('call')) for t in topology.get('subagents') or [] if isinstance(t, dict) and t.get('call')}
    tools = config.get('tools') if isinstance(config.get('tools'), dict) else {}
    carried = set(map(str, tools)) | on_map | set(map(str, production.get('live') or []))
    accounted = (set(map(str, production.get('names') or [])) | agent_inventory.not_loaded_tools(production) | backend_tools(production, config)
                 | agent_inventory.overridden_tools(production, answered_overrides(doc, workdir)))
    findings: list[str] = []
    if missing := sorted(carried - accounted):
        findings.append(
            f'production_inventory_incomplete: the bench carries {len(missing)} tool(s) the agent inventory does not account for '
            f'({", ".join(missing[:12])}{f" and {len(missing) - 12} more" if len(missing) > 12 else ""}); C3 holds '
            f'{len(production.get("names") or [])}. The static read missed them: record the agent\'s live tool list at production defaults '
            '(`agent_inventory.py record-live`), or cite why each is not loaded (surfaces.production_tools.not_loaded)')
    recorded = live_enumeration_recorded(production, workdir)
    if (dynamic := production.get('dynamic')) and not recorded:
        findings.append(
            f'production_inventory_incomplete: the agent names tools at run time ({", ".join(str(d.get("source")) for d in dynamic[:5])}), '
            'which no static read can count; the setup smoke must record its live tool list at production defaults (`agent_inventory.py record-live`)')
    elif not recorded and _in_harness(doc):
        findings.append(
            'production_inventory_incomplete: no live tool list is recorded, so C3 is the static read alone (static ∪ live); the setup smoke '
            'records the tool list the agent builds at its production defaults (`agent_inventory.py record-live`, setup Step 9f.4)')
    return findings


#: Why the production default does not load a tool (C11 `not_loaded[].because`), each with the cite it needs. A world
#: precondition (an empty calendar, a device the test home lacks) is never one: the scan seeds the precondition.
NOT_LOADED_CITES = {'default_off_switch': 'switch', 'provider_variant': 'selector', 'separate_surface': 'surface'}
NOT_LOADED_REMEDY = ('type it `{names, because, <cite>, detail}`: `default_off_switch` + `switch` (a reader switch whose production '
                     'default is OFF), `provider_variant` + `selector` (a reader switch, covered per value or answered by the operator in '
                     'harness.capability_overrides), `separate_surface` + `surface` (<path>:<line> of the other entry point); a world '
                     'precondition is never a reason — seed it')


#: What makes a file an entry point of its own: an HTTP route, a CLI, an MCP server.
_ENTRY_POINT = re.compile(
    r'@\w+(?:\.\w+)*\.(?:get|post|put|patch|delete|route|api_route|websocket)\(|\bAPIRouter\(|\bFastAPI\(|\bFlask\('
    r'|export\s+(?:async\s+)?(?:function|const)\s+(?:GET|POST|PUT|PATCH|DELETE)\b|\b(?:app|router|server)\.(?:get|post|put|patch|delete)\('
    r'|\bargparse\.ArgumentParser\(|@\w*\.?command\(|\btyper\.Typer\(|\bclick\.(?:group|command)\('
    r'|\bFastMCP\(|@\w+\.tool\(|\bMcpServer\(|\bserver\.(?:tool|registerTool)\(|\bif __name__ == [\'"]__main__[\'"]')
#: Files that configure, never serve: a cite into one is a default, not another surface.
_CONFIG_SUFFIXES = ('.yaml', '.yml', '.json', '.toml', '.ini', '.cfg', '.env', '.conf', '.properties')


def _is_entry_point(path: Path) -> bool:
    if path.suffix.lower() in _CONFIG_SUFFIXES or not path.is_file():
        return False
    try:
        return bool(_ENTRY_POINT.search(path.read_text(encoding='utf-8', errors='replace')))
    except OSError:
        return False


def _not_loaded_problem(group: dict[str, Any], switches: dict[Any, dict[str, Any]], doc: dict[str, Any], workdir: Path) -> str | None:
    """Why a typed `not_loaded` group does not hold, or None."""
    because = str(group.get('because') or '')
    field = NOT_LOADED_CITES.get(because)
    if field is None:
        return f'is not loaded because {because!r}, which is no C11 reason' if because else 'is untyped (no `because`)'
    cite = str(group.get(field) or '')
    if field == 'surface':
        if why := harness_cite(doc, workdir, cite):
            return f'cites the surface {cite!r}, {why}'
        if not _is_entry_point(resolve_source(workdir, cite.rpartition(':')[0])):
            return (f'cites {cite!r} as the other surface, which is no entry point (a route, a CLI or an MCP server): a configuration '
                    'default is `default_off_switch` with the switch the reader found — and with none found it cannot be typed')
        return None
    switch = switches.get(cite)
    if switch is None:
        return f'names `{field}: {cite}`, which is no switch the reader found'
    if because == 'default_off_switch' and switch.get('default_on') is not False:
        return f'names `{cite}`, whose production default is {"ON" if switch.get("default_on") else "unresolved (counted ON)"}, not OFF'
    if because == 'provider_variant' and not _switch_answered(doc, cite, workdir=workdir):
        return f'is a variant of `{cite}` with no operator answer: cover each value, or record the answer (harness.capability_overrides)'
    return None


def _switch_key(name: str) -> str:
    """A switch or a pinned name, compared loosely: `env:X` / `is_x` / `x` are one flag."""
    return re.sub(r'^(env:|is_)', '', name).lower()


def harness_pin_findings(doc: dict[str, Any], workdir: Path) -> list[str]:
    """A capability switch the harness pins away from its production default (a constructor keyword, a config dict
    entry, an env var it sets) is a harness choice: it needs the operator's answer naming the switch
    (`harness.capability_overrides`). An unresolved default counts ON, so only a pin turning it OFF is one there."""
    production = doc['surfaces'].get('production_tools')
    switches = {_switch_key(str(s.get('switch'))): s for s in (production or {}).get('switches') or [] if isinstance(s, dict) and s.get('kind') != 'selector'}
    findings: list[str] = []
    for path in (p for p in agent_inventory._harness_files(workdir) if p.suffix == '.py'):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
            continue
        for node in ast.walk(tree):
            pins: list[tuple[str, ast.AST]] = []
            if isinstance(node, ast.keyword) and node.arg:
                pins.append((node.arg, node.value))
            elif isinstance(node, ast.Dict):
                pins += [(k.value, v) for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and (env := agent_inventory._env_var(node.targets[0])):
                pins.append((env[0], node.value))
            elif isinstance(node, ast.Call) and _dotted_name(node.func).endswith('environ.setdefault') and len(node.args) == 2 and isinstance(node.args[0], ast.Constant):
                pins.append((str(node.args[0].value), node.args[1]))
            for name, value in pins:
                switch = switches.get(_switch_key(name))
                if switch is None or not isinstance(value, ast.Constant) or _switch_answered(doc, str(switch['switch']), workdir=workdir):
                    continue
                on = agent_inventory._truthy(value.value)
                if on != switch.get('default_on') and (switch.get('default_on') is not None or not on):
                    findings.append(f'production_inventory_incomplete: the harness pins `{name}={value.value!r}` ({path.name}:{getattr(value, "lineno", 1)}) '
                                    f'away from the production default of `{switch["switch"]}` ({switch.get("default") or "unresolved"}); a harness choice '
                                    "needs the operator's answer naming the switch (harness.capability_overrides), or cover the default")
    return findings


def _dotted_name(node: ast.AST) -> str:
    return f'{_dotted_name(node.value)}.{node.attr}' if isinstance(node, ast.Attribute) else getattr(node, 'id', '')


#: Why the agent never receives an input — the reasons that leave its channel out of the harness legitimately.
NEVER_RECEIVED = ('not_reachable_in_production', 'internal_field', 'tool_argument', 'transport_only', 'harness_session_state')


def input_scope_cut_findings(doc: dict[str, Any], inventory: dict[str, Any], config: dict[str, Any]) -> list[str]:
    """`input_scope_cut`: the config declares less than the agent ships. A content part the reader finds the agent
    takes (an image, a file, an upload by reference no channel models) that `agent.io.input` lacks, unless every input of that kind is waived for a reason the
    agent never receives it; and a pause for the user's answer (a card the UI renders) with no `render_actions:` verb.
    A scope approval is no such reason: an out-of-harness channel is a `coverage_holes[]` entry and the input is still
    modelled or refused."""
    io = _io_declaration(config)
    declared = set(io.get('input') if isinstance(io.get('input'), list) else ['text'])
    waived = {_source_key(e): e for e in doc['surfaces'].get('inputs') or [] if isinstance(e, dict)}
    findings: list[str] = []
    by_part: dict[str, list[str]] = {}
    for entry in inventory.get('inputs') or []:
        decided = waived.get(_source_key(entry)) or {}
        # A file the user uploads by reference reaches the agent as a file: a `file` part, unless a channel models it.
        part = agent_inventory.PART_KINDS.get(str(entry.get('kind'))) or (
            'file' if entry.get('kind') == 'file_by_reference' and modelled_by({**entry, **decided}, config) is None else None)
        because = str((decided.get('waived') or {}).get('because') or '') if isinstance(decided.get('waived'), dict) else ''
        if part and part not in declared and because not in NEVER_RECEIVED:
            by_part.setdefault(part, []).append(f'{entry["name"]} ({entry["source"]})')
    for part, names in sorted(by_part.items()):
        findings.append(f'input_scope_cut: the agent takes `{part}` parts — {", ".join(names[:6])}{" …" if len(names) > 6 else ""} — and '
                        f'agent.io.input declares {sorted(declared)}; declare the part and model it, or waive each input for a reason the '
                        'agent never receives it (a scope approval is none: an out-of-harness channel is a coverage_holes[] entry)')
    verbs = config.get('render_actions')
    if (pauses := (inventory.get('production_tools') or {}).get('pauses')) and not (isinstance(verbs, list) and verbs):
        findings.append(f'input_scope_cut: the agent pauses for the user\'s answer ({", ".join(str(p) for p in pauses[:3])}), a card the UI renders, '
                        'and render_actions: declares no verb for it; return it on agent_actions and list the verb (or `interrupt`)')
    return findings


def _in_harness(doc: dict[str, Any]) -> bool:
    """Whether the agent's turn logic runs in the harness interpreter, where its tool list can be enumerated live."""
    symbol = (doc.get('agent') or {}).get('entry_symbol')
    return isinstance(symbol, dict) and symbol.get('executes') == 'in_harness'


def _inventory_findings(doc: dict[str, Any], workdir: Path) -> list[str]:
    """The bench against the agent's OWN source, re-read now (never the scan's copy): C2 and C3, held to the ONE
    inventory artifact the map and lint_prd read."""
    fresh = agent_inventory.extract(workdir)
    if 'unavailable' in fresh:
        return []
    inventory = agent_inventory.current(workdir, fresh)
    config = _load_yaml(workdir / 'plurai.config.yaml')
    config = config if isinstance(config, dict) else {}
    findings = [] if inventory is not fresh else [
        f'production_tool_dropped: artifacts/{agent_inventory.INVENTORY_FILENAME} is absent or not the agent\'s current reading, so the map, '
        f'lint_prd and this check would read different inventories; {INVENTORY_REMEDY}']
    findings += input_channel_findings(doc, inventory, config, workdir) + production_tool_findings(doc, inventory, workdir)
    findings += input_scope_cut_findings(doc, inventory, config)
    return findings + production_inventory_findings(doc, config, workdir) + harness_pin_findings(doc, workdir)


def _declared_writers(workdir: Path) -> set[str]:
    """The config tools whose descriptor declares a write: a non-null `writes_to`, or `stateful: true`."""
    config = _load_yaml(workdir / 'plurai.config.yaml')
    tools = config.get('tools') if isinstance(config, dict) else None
    return {str(name) for name, spec in (tools.items() if isinstance(tools, dict) else ())
            if isinstance(spec, dict) and isinstance(d := spec.get('descriptor'), dict) and (d.get('writes_to') is not None or d.get('stateful') is True)}


#: A live map tool's `backend` when its code opens no client: it computes over its arguments, in-repo files or the harness world.
NO_BACKEND = 'none'


def map_backends(workdir: Path) -> dict[str, str | None]:
    """`{name: backend}` for every LIVE tool on the agent map (`tools` and `rag` banks): the service its code signs in to
    (the local stack's included), :data:`NO_BACKEND`, or None where the map does not type it (a map written before the fact)."""
    topology = _read_json(workdir / 'artifacts' / 'agent_topology.json')
    rows = [s for bank in ('tools', 'rag') for s in (topology.get(bank) or [] if isinstance(topology, dict) else [])]
    return {str(s['name']): (str(s['backend']).strip() or None) if s.get('backend') is not None else None
            for s in rows if isinstance(s, dict) and s.get('name') and s.get('status') == 'live'}


def _check_findings(doc: dict[str, Any], workdir: Path) -> list[str]:
    """Check ids unique and well-formed; declared scripts present; `verifies` addresses real fields."""
    findings: list[str] = []
    seen: set[str] = set()
    known = set(verifiable_paths(doc)) | map_paths(workdir)
    mocked = {f'surfaces.tools[{t["name"]}]' for t in doc['surfaces']['tools'] if t.get('mode') != 'real'}
    writers = _declared_writers(workdir)
    real = {str(t['name']) for t in doc['surfaces']['tools'] if t.get('mode') == 'real'}
    services = {name for name, backend in map_backends(workdir).items() if backend not in (None, NO_BACKEND)}
    claimed = {f for c in doc['checks'] if c.get('kind') == 'live' for f in c.get('verifies') or []}
    if unchecked := sorted(n for n in services & real if f'surfaces.tools[{n}]' not in claimed):
        findings.append(f'checks: {", ".join(unchecked)} run for real and sign in to a service (the agent map\'s `backend`), but no '
                        '`kind: live` check verifies them — declare one per service (simulation-setup Step 8c.3), or mock them')
    for check in doc['checks']:
        cid = str(check.get('id', ''))
        if not _ID_RE.match(cid):
            findings.append(f'checks: {cid!r} is not a lower_snake_case id')
        if cid in seen:
            findings.append(f'checks: duplicate id {cid!r}')
        seen.add(cid)
        if check['runner'] == 'builtin' and cid not in BUILTIN_IDS:
            findings.append(f'checks[{cid}]: runner builtin, but this plugin build knows no such check — known: {", ".join(BUILTIN_IDS)}')
        if check['kind'] == 'surface':
            # An absent or misspelled `expect.equals` resolved to None and `execute` reported
            # "expected a list on both sides" — a manifest-authoring mistake rendered as the
            # AGENT's live surface disagreeing with its declaration, on a blocking check.
            equals = str((check.get('expect') or {}).get('equals') or '').strip()
            if not equals:
                findings.append(f'checks[{cid}]: kind surface needs expect.equals naming the declaration the live surface must match')
            elif not isinstance(resolve_field(doc, equals), list):
                findings.append(f'checks[{cid}]: expect.equals {equals!r} does not resolve to a list in this manifest')
        if check['runner'] != 'builtin':
            script = check.get('script')
            if not script:
                findings.append(f'checks[{cid}]: runner {check["runner"]!r} needs a `script`')
            elif not (workdir / script).is_file():
                # The whole point: a declared check whose script is missing must STOP the scan,
                # not silently become a reuse of the last time it ran.
                findings.append(f'checks[{cid}]: script {script} does not exist under the workdir')
        if check.get('witnesses'):
            # Only a builtin returns a runtime witness: a smoke for the entry, the mock contract for each mocked tool.
            if check['runner'] == 'builtin' and check['kind'] == 'smoke':
                witnessable = set(WITNESS_ONLY_FIELDS)
            elif check['runner'] == 'builtin' and cid == 'mock_contract':
                witnessable = mocked
            else:
                witnessable = set()
            if unwitnessable := [f for f in check['witnesses'] if f not in witnessable]:
                findings.append(f'checks[{cid}]: declares `witnesses` {unwitnessable}, which no runtime witness of this check reports — a builtin '
                                'smoke witnesses agent.entry, and the mock contract each mocked tool')
        if check['kind'] == 'probe' and (probed := sorted(n for n in writers if any(str(f).endswith(f'[{n}]') for f in check['verifies']))):
            # A probe runs with no network, not with no writes: the declared write exclusion is what keeps a writer out.
            findings.append(f'checks[{cid}]: a probe verifies {", ".join(probed)}, which plurai.config.yaml declares as writing '
                            '(`descriptor.writes_to` or `descriptor.stateful: true`); a writer is never probed — remove the check or that claim')
        if check['kind'] == 'probe' and (reaching := sorted(n for n in services if any(str(f).endswith(f'[{n}]') for f in check['verifies']))):
            # A probe runs with no network, so its pass never shows that a tool reaches the service it signs in to.
            findings.append(f'checks[{cid}]: a probe verifies {", ".join(reaching)}, which the agent map types with a `backend` (a service '
                            'it signs in to); a probe runs with no network and never reaches it — remove that claim: the tool\'s `kind: live` check verifies it')
        if check['kind'] == 'live' and (off := [f for f in check['verifies'] if f not in {f'surfaces.tools[{n}]' for n in real}]):
            findings.append(f'checks[{cid}]: a live check verifies only tools the bench runs for real (`surfaces.tools[<name>]`); {off} are not')
        if check['kind'] == 'live' and check.get('blocking') is not True:
            findings.append(f'checks[{cid}]: a live check is `blocking: true` — a tool that cannot reach its service stops the scan for the user\'s answer')
        for field in check['verifies']:
            if field in WITNESS_ONLY_FIELDS:
                findings.append(f'checks[{cid}]: verifies {field!r}, which only a smoke\'s runtime witness can prove (`witnesses`) — '
                                'a `verifies` entry would clear it whether or not the agent ever ran it')
            elif field not in known:
                findings.append(f'checks[{cid}]: verifies {field!r}, which is not a field of this manifest or a surface of its agent map')
        if check.get('policy') == 'on_mode':
            # PARSED, not merely present. `mode_rule_holds` returns False on anything it cannot
            # read, and False means `not_applicable` — so a single typo (`=` for `==`, a field path
            # that does not exist) silently and permanently disables the check, and the report says
            # "correctly skipped" rather than "the rule is broken". On a mockless bench the check
            # this happens to is `smoke_turn`, the ONLY live gate such a bench has.
            rule = str(check.get('mode_rule', '')).strip()
            # Validated CONJUNCT BY CONJUNCT, the same grammar each: every one must parse, address
            # a readable path, and read a field this manifest carries. A rule half of which is
            # unreadable is not partly true — `mode_rule_holds` returns False for it, so a check
            # would silently never apply.
            conjuncts = [c.strip() for c in rule.split(' and ')] if rule else []
            if not rule:
                findings.append(f'checks[{cid}]: policy on_mode needs a mode_rule')
            for conjunct in conjuncts:
                left, sep, right = conjunct.partition('==')
                if not sep or not right.strip():
                    findings.append(f'checks[{cid}]: mode_rule {conjunct!r} is not `<field path> == <value>` — the only grammar')
                elif _BOOLEAN_WORD_RE.search(right):
                    # The RIGHT of `==` is a bare VALUE, so a boolean word there is the author
                    # reaching for an expression language this grammar does not have. `' and '`
                    # is the only joiner: `or`, a capitalised `AND` and a trailing ` and` all
                    # survive `split(' and ')` intact and then compare a field to a string that
                    # is never equal to it — `mode_rule_holds` returns False, `plan` records
                    # `not_applicable`, and on a mockless bench the check silently disabled that
                    # way is `smoke_turn`, its ONLY live gate. Reported here, as this function's
                    # docstring promises, rather than evaluated to a permanent False.
                    findings.append(
                        f'checks[{cid}]: mode_rule {conjunct!r} carries a boolean word on the RIGHT of `==`, '
                        f'where only a bare value goes. The only joiner is a lowercase ` and `; there is no '
                        f'`or` and no `not`, and a trailing ` and ` joins nothing.'
                    )
                elif _SELECTOR_RE.match(left.strip()) is None:
                    findings.append(f'checks[{cid}]: mode_rule addresses {left.strip()!r}, which is not a readable field path')
                elif resolve_field(doc, left.strip()) is None:
                    findings.append(f'checks[{cid}]: mode_rule reads {left.strip()!r}, which this manifest does not carry')
        if check.get('policy') == 'on_change' and not (check.get('derived_from') or {}).get('paths'):
            findings.append(f'checks[{cid}]: policy on_change needs derived_from.paths — nothing decides whether it is stale')
        for rel in (check.get('derived_from') or {}).get('paths', []):
            resolved = resolve_source(workdir, rel)
            if not resolved.exists():
                findings.append(f'checks[{cid}]: derived_from path {rel} resolves to nothing')
            elif not resolved.is_file():
                # `sha256_path` returns None for anything that is not a file, so a directory hashes
                # identically forever and the check is reused for the life of the bench while the
                # sources it claims to depend on change underneath it.
                findings.append(f'checks[{cid}]: derived_from path {rel} is not a file — name the files, a directory hashes the same forever')
    return findings


def _entry_symbol_findings(doc: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """`agent.entry_symbol` against the schema's `oneOf`, which `_enum_findings` does not walk.

    ABSENT is accepted: a manifest written before the field validates as it did, and its witness
    reads `not_determinable`. `init` writes the sentinel when the topology names an entry, so a
    scan cannot reach verification without the analysis authoring it.
    """
    symbol = doc['agent'].get('entry_symbol')
    if symbol is None or symbol == SENTINEL:
        return []
    in_harness, _external = schema['properties']['agent']['properties']['entry_symbol']['oneOf']
    if isinstance(symbol, dict) and symbol.keys() == {'executes', 'ref'} and symbol['executes'] == 'in_harness':
        ref = str(symbol['ref'])
        if not re.match(in_harness['properties']['ref']['pattern'], ref):
            return [f'agent.entry_symbol.ref: {ref!r} is not `package.module:Qual.name`']
        if ref.partition(':')[0] == 'simulate':
            return [f'agent.entry_symbol.ref: {ref!r} names the harness, which runs on every turn — name the agent function it calls']
        return []
    if (isinstance(symbol, dict) and symbol.get('executes') == 'external' and str(symbol.get('why') or '').strip()
            and (symbol.keys() == {'executes', 'why'} or (symbol.keys() == {'executes', 'why', 'launched_by'} and symbol['launched_by'] == 'harness'))):
        return []
    return [f'agent.entry_symbol: {symbol!r} is neither {{executes: in_harness, ref}} nor {{executes: external, why[, launched_by: harness]}}']


def _resume_findings(doc: dict[str, Any]) -> list[str]:
    """`harness.resume: {mode: unsupported}` is a CLAIM about the harness, so it carries its reason and evidence."""
    resume = doc['harness'].get('resume')
    if isinstance(resume, dict) and resume.get('mode') == 'unsupported':
        return [f'harness.resume: mode unsupported needs `{key}`' for key in ('reason', 'evidence') if not resume.get(key)]
    return []


def _tool_interception_findings(doc: dict[str, Any]) -> list[str]:
    """`harness.tool_interception: agent_host` must say why and name its route's smoke, and must not contradict the entry or the run mode."""
    hosted = host_interception(doc)
    if hosted is None:
        return []
    findings = [f'harness.tool_interception: agent_host needs a non-empty `{key}`' for key in ('why', 'route_smoke') if not str(hosted.get(key) or '').strip()]
    symbol = doc['agent'].get('entry_symbol')
    if isinstance(symbol, dict) and symbol.get('executes') != 'external':
        findings.append('harness.tool_interception: agent_host while agent.entry_symbol says the agent runs in the harness')
    if doc['harness'].get('run_mode') == 'mockless':
        findings.append('harness.tool_interception: agent_host on a mockless bench, which intercepts nothing')
    return findings


def _names_requirement(text: str, req: dict[str, Any]) -> bool:
    """Whether a cited line names the requirement: its name standing alone, any case — a module also by
    its import spelling (`python-dateutil` imported as `python_dateutil`)."""
    spellings = {req['name'], req['name'].replace('-', '_')} if req['kind'] == 'python_module' else {req['name']}
    return any(re.search(rf'(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])', text, re.IGNORECASE) for name in spellings)


def _cited_line(workdir: Path, citation: str) -> str | None:
    """The line a `<path>:<line>` citation names in the agent's files, or None when it names none."""
    rel, _, line = citation.rpartition(':')
    path = resolve_source(workdir, rel) if rel else None
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines() if path and path.is_file() else []
    return lines[int(line) - 1] if line.isdigit() and 1 <= int(line) <= len(lines) else None


#: A probe element naming the agent's own file (the SDK expands it to an absolute path before the probe runs).
AGENT_PATH_PREFIX = '{agent}/'

#: An option word: a dash or two, then a letter.
_OPTION = re.compile(r'--?[A-Za-z][\w.-]*')

#: A line's (or a code span's) leading shell prompt or list marker — `$ `, `% `, `> `, `- `, `* `, `1. ` — which is
#: no word of the command after it.
_LEADING_MARKER = r'(?:^|(?<=`))[ \t]*(?:[$%>*-]|\d+\.)[ \t]'

#: What ends one command on a documentation line: a code span's edge, a pipe, a `;`, a `&&`, a comment, or a
#: leading prompt / list marker before it.
_COMMAND_END = re.compile(rf'`|\||;|&&|(?<!\S)#|{_LEADING_MARKER}')

#: The same boundaries for an install command, except a pipe: a pipeline (`curl … | sh`) is ONE install command,
#: and a part of it is never one. `||` still separates two.
_INSTALL_COMMAND_END = re.compile(rf'`|\|\||;|&&|(?<!\S)#|{_LEADING_MARKER}')


def _shape(words: list[str]) -> tuple[frozenset[str], tuple[str, ...]]:
    """The options and the operands of a command's words after its head; `--opt=value` is `--opt` and the operand `value`."""
    options: set[str] = set()
    operands: list[str] = []
    for word in words:
        head, eq, value = word.partition('=') if word.startswith('-') else (word, '', '')
        if _OPTION.fullmatch(head):
            options.add(head)
            operands += [value] if eq else []
        else:
            operands.append(word)
    return frozenset(options), tuple(operands)


def _segments(word: str) -> list[str]:
    """A line's path word as the segments it names, its leading `./` and `../` dropped: where it starts from is the
    line's working directory (a `cd`, the cited file's folder), which the line does not always show."""
    parts = word.split('/')
    while len(parts) > 1 and parts[0] in ('.', '..'):
        parts = parts[1:]
    return parts


def _suffix_of(word: str, rel: str) -> bool:
    """Whether `word`'s path segments end `rel`'s: how a line shows an agent file it names relative to wherever it sits."""
    parts, tail = rel.removeprefix(AGENT_PATH_PREFIX).split('/'), _segments(word)
    return parts[-len(tail) :] == tail


#: Trees under an agent that hold its dependencies or history, never its own files.
_NOT_AGENT_FILES = frozenset({'.git', 'node_modules', '.venv', 'venv', '__pycache__'})


def _agent_files(agent: Path, workdir: Path) -> list[str]:
    """Every file of the agent, relative to it, outside the workdir (the scan's own folder)."""
    files = []
    for root, dirs, names in os.walk(agent):
        here = Path(root)
        dirs[:] = [d for d in dirs if d not in _NOT_AGENT_FILES and (here / d).resolve() != workdir.resolve()]
        files += [(here / n).relative_to(agent).as_posix() for n in names]
    return files


def _runs_as(
    probe: tuple[frozenset[str], tuple[str, ...]], documented: tuple[frozenset[str], tuple[str, ...]], fixtures: Path, agent_file: Callable[[str], bool]
) -> bool:
    """The line's options, and its operands in order. An operand the line shows as an agent file is that file, as its
    `{agent}/` path; any other is the line's own word, or a fixture in `host_probes/` keeping the word's extension, if it has one."""
    (options, operands), (line_options, line_operands) = probe, documented

    def stands_for(mine: str, theirs: str) -> bool:
        if agent_file(theirs):
            return mine.startswith(AGENT_PATH_PREFIX) and _suffix_of(theirs, mine)
        return mine == theirs or ((fixtures / mine).is_file() and Path(theirs).suffix in ('', Path(mine).suffix))

    return (
        options == line_options
        and len(operands) == len(line_operands)
        and all(stands_for(mine, theirs) for mine, theirs in zip(operands, line_operands))
    )


def _commands_on(line: str, is_head: Callable[[str], bool]) -> list[tuple[list[str], list[str]]]:
    """`(words before, words after)` each place on the line whose word `is_head`, within that one command."""
    words = [w.rstrip('.,:') or w for w in _COMMAND_END.sub(' \0 ', line).split()]
    found = []
    for i, word in enumerate(words):
        if word != '\0' and is_head(word.lstrip('(').removeprefix('./')):
            before, rest = words[:i], words[i + 1 :]
            start = len(before) - before[::-1].index('\0') if '\0' in before else 0
            found.append((before[start:], rest[: rest.index('\0')] if '\0' in rest else rest))
    return found


def _probe_findings(req: dict[str, Any], workdir: Path) -> list[str]:
    """A probe runs the invocation its `probe_evidence` line documents: headed by the program itself, or by the agent's
    own route to it (an `{agent}/<path>` element, the agent's file, shown on the line). After that head the probe passes
    exactly the line's options and its operands in order (an agent file as its `{agent}/` path, anything else the line's
    own word or a same-extension fixture in `host_probes/`), and a route's words before it are the line's own; a route is
    never in the scan's own folder. Run, never parsed, by the host toolchain check."""
    probe, cite, name = req.get('probe'), req.get('probe_evidence'), req['name']
    where = f'harness.host_requirements: {name!r}'
    if probe is None:
        return [f'{where} declares probe_evidence with no probe — remove it or add the probe it cites'] if cite else []
    if req['kind'] != 'executable' or not probe:
        return [f'{where} declares probe {probe!r} — only a kind: executable has a probe, and it is a non-empty argv']
    if not cite:
        return [f"{where} declares probe {probe!r} with no probe_evidence — cite the line of the agent's files that documents the invocation it runs"]
    agent = agent_dir(workdir).resolve()
    routes = [i for i, arg in enumerate(probe) if arg.startswith(AGENT_PATH_PREFIX)]
    findings = []
    for i in routes:
        target = (agent / probe[i].removeprefix(AGENT_PATH_PREFIX)).resolve()
        if not (target.is_relative_to(agent) and target.is_file()) or target.is_relative_to(workdir.resolve()):
            # the workdir is the scan's own folder: nothing in it is the agent's
            findings.append(f"{where} probe names {probe[i]!r}, which is no file of the agent's")
    if probe[0] != name and not routes:
        findings.append(f"{where} probe {probe!r} is headed by another program and names no route of the agent's ({AGENT_PATH_PREFIX}<path>)")
    line = _cited_line(workdir, cite)
    if line is None:
        return [*findings, f"{where} probe_evidence {cite!r} is no line of the agent's files"]
    if not _names_requirement(line, req):
        findings.append(f'{where} probe_evidence {cite!r} is a line that does not name it — cite the line that documents the invocation')

    def shows(rel: str) -> Callable[[str], bool]:
        return lambda word: _suffix_of(word, rel)

    # a word of the line naming a file of the agent, from wherever the line runs: any agent file its segments end
    files: list[str] = []

    def agent_file(word: str) -> bool:
        files[:] = files or _agent_files(agent, workdir)
        return any(_suffix_of(word, rel) for rel in files)

    for i in routes:
        if not _commands_on(line, shows(probe[i])):
            findings.append(f'{where} probe names {probe[i]!r}, which {cite!r} does not show')
    head = routes[0] if probe[0] != name and routes else 0
    is_head = shows(probe[head]) if probe[0] != name else (lambda word: word.rsplit('/', 1)[-1].casefold() == name.casefold())
    commands = _commands_on(line, is_head)
    wanted = _shape(probe[head + 1 :])
    if not probe[head + 1 :]:
        findings.append(f'{where} probe passes nothing after {probe[head]!r} — a probe runs the documented invocation, not the program alone')
    elif head and commands and not [before for before, _ in commands if before == probe[:head]]:
        findings.append(
            f'{where} probe runs {probe[:head]} before its route, where {cite!r} runs {commands[0][0]} — the words before the route are the line\'s own'
        )
    elif commands and not any(_runs_as(wanted, _shape(after), workdir / 'host_probes', agent_file) for _, after in commands):
        options, operands = _shape(commands[0][1])
        findings.append(
            f'{where} probe passes {sorted(wanted[0])} and operands {list(wanted[1])} after {probe[head]!r}, where {cite!r} documents '
            f'{sorted(options)} and {list(operands)} — probe the invocation that line documents: its options, and its operands '
            f'— an agent file as its {AGENT_PATH_PREFIX}<path>, anything else the word itself or a fixture in plurai_sim/host_probes/ keeping its extension'
        )
    elif not commands and not findings:
        findings.append(f'{where} probe_evidence {cite!r} does not show {probe[head]!r} — cite the line that documents the invocation')
    return findings


def _install_findings(req: dict[str, Any], workdir: Path) -> list[str]:
    """An install line is the agent's own: it IS one whole command (a whole pipeline) of its `install_evidence` line,
    the line cut at :data:`_INSTALL_COMMAND_END`, spacing aside — never a fragment, which would run another program
    than the docs' — and that line is in a file under the agent's folder, where the block says to run it (never the
    scan's own folder). So run-gate never prints a command nobody in the agent's docs wrote down. `validate`'s only:
    the line is print-only, so the run gate reads a refused one as none (:func:`_install_refused`), never a hold."""
    install, cite, name = req.get('install'), req.get('install_evidence'), req['name']
    where = f'harness.host_requirements: {name!r}'
    if install is None and cite is None:
        return []
    if not (isinstance(install, str) and install.strip()):
        return [f'{where} declares install_evidence with no install — remove it or add the command that line gives']
    if not (isinstance(cite, str) and cite.strip()):
        return [f"{where} declares install {install!r} with no install_evidence — cite the line of the agent's install docs that gives it"]
    agent, source = agent_dir(workdir).resolve(), resolve_source(workdir, cite.rpartition(':')[0]).resolve()
    if not (source.is_file() and source.is_relative_to(agent)) or source.is_relative_to(workdir.resolve()):
        return [f"{where} install_evidence {cite!r} is no file under the agent's folder — cite its own install docs, where the block runs the line"]
    line = _cited_line(workdir, cite)
    if line is None:
        return [f"{where} install_evidence {cite!r} is no line of the agent's files"]
    if ' '.join(install.split()) not in {' '.join(command.split()) for command in _INSTALL_COMMAND_END.split(line)}:
        return [
            f"{where} install {install!r} is not one whole command on {cite!r} — copy the command the agent's install docs give, whole, never write one"
        ]
    return []


def _install_refused(doc: dict[str, Any], workdir: Path) -> set[str]:
    """The requirements whose declared install line `validate` refuses: run-gate prints them as declaring none."""
    harness = doc.get('harness') if isinstance(doc, dict) else None
    reqs = harness.get('host_requirements') if isinstance(harness, dict) else None
    return {str(r.get('name')) for r in reqs or () if isinstance(r, dict) and isinstance(r.get('name'), str) and _install_findings(r, workdir)}


def _host_requirement_findings(doc: dict[str, Any], workdir: Path) -> list[str]:
    """Each declared host requirement cites a line of the agent's own files that NAMES it, and a declared
    probe runs what the agent's files document (:func:`_probe_findings`). The run gate's too: no install line here."""
    findings = []
    for req in doc['harness'].get('host_requirements') or []:
        line = _cited_line(workdir, req['evidence'])
        if line is None:
            findings.append(f"harness.host_requirements: {req['name']!r} cites {req['evidence']!r}, which is no line of the agent's files")
        elif not _names_requirement(line, req):
            findings.append(
                f"harness.host_requirements: {req['name']!r} cites {req['evidence']!r}, a line that does not name it — "
                "cite the line of the agent's files that says it needs it"
            )
        findings += _probe_findings(req, workdir)
    return findings


def host_declaration_findings(doc: dict[str, Any], workdir: Path) -> list[str]:
    """`validate`'s own findings on `harness.host_requirements`, for a gate that cannot trust `validate` ran: its
    shape (read off the schema) first, then :func:`_host_requirement_findings` on a declaration that has one. An
    install line is print-only, so its findings never hold a run: none of them is here (:func:`_install_refused`)."""
    harness = doc.get('harness')
    if not (isinstance(harness, dict) and harness.get('host_requirements')):
        return []
    shape = [
        f
        for f in _enum_findings(doc, load_schema())
        if f.startswith('harness.host_requirements') and not re.match(r'harness\.host_requirements\[\d+\]\.install(?:_evidence)?:', f)
    ]
    return shape or _host_requirement_findings(doc, workdir)


def _unsandboxed_grant_findings(doc: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """`consent.unsandboxed.names`: each a bare program name the agent declares as an executable.

    The CLI reads `*` as a wildcard and a trailing `:*` as a prefix, so a name that is not bare
    would take the Bash sandbox off far more than one program; and a grant reaches only what
    `harness.host_requirements` declares with `kind: executable`. The name pattern is read off
    the schema, which `_enum_findings` does not walk for patterns.
    """
    grant = doc['consent'].get('unsandboxed') or {}
    pattern = schema['properties']['consent']['properties']['unsandboxed']['properties']['names']['items']['pattern']
    executables = {req['name'] for req in doc['harness'].get('host_requirements') or [] if req['kind'] == 'executable'}
    findings = []
    for name in grant.get('names') or []:
        if not re.fullmatch(pattern.removeprefix('^').removesuffix('$'), name):
            findings.append(f'consent.unsandboxed: {name!r} is not a bare program name — name the program itself (no path, no space, no `*` or `:*`)')
        elif name.casefold() in {program.casefold() for program in UNGRANTABLE_PROGRAMS}:  # a case-insensitive volume runs BASH as bash
            findings.append(f'consent.unsandboxed: {name!r} is a shell or a program that runs other commands — granting it would let every command out, remove it')
        elif name not in executables:
            findings.append(f'consent.unsandboxed: {name!r} is not declared in harness.host_requirements as kind: executable — declare it or remove it')
    return findings


def _retired_lane_findings(doc: Any) -> list[str]:
    """A manifest written for the retired TypeScript-SDK harness: no alias, only a re-scan onto the bridge."""
    sections = [(key, doc.get(key)) for key in ('agent', 'harness')] if isinstance(doc, dict) else []
    retired = [f'{key}.language' for key, section in sections if isinstance(section, dict) and section.get('language') == RETIRED_LANGUAGE]
    return [f"{', '.join(retired)}: {RETIRED_LANGUAGE!r} names a harness lane this plugin no longer ships; re-scan the bench (/simulation-scan-agent), which puts a Node agent on the bridge harness"] if retired else []


def validate(doc: Any, workdir: Path) -> list[str]:
    """Every finding, in one pass. Empty means the manifest may be verified against."""
    retired = _retired_lane_findings(doc)
    if retired:
        return retired
    schema = load_schema()
    findings = _enum_findings(doc, schema)
    if findings:
        # Shape first: the readers below index into the document and would raise on a broken one.
        return findings
    findings += _sentinel_findings(doc)
    findings += _entry_symbol_findings(doc, schema)
    findings += _tool_interception_findings(doc)
    findings += _resume_findings(doc)
    findings += _host_requirement_findings(doc, workdir)
    findings += [f for req in doc['harness'].get('host_requirements') or [] for f in _install_findings(req, workdir)]
    findings += _unsandboxed_grant_findings(doc, schema)
    findings += _cross_artifact_findings(doc, workdir)
    findings += _inventory_findings(doc, workdir)
    findings += _check_findings(doc, workdir)
    findings += _coverage_hole_findings(doc, workdir)
    return findings


# --------------------------------------------------------------------------------------------
# fields + coverage
# --------------------------------------------------------------------------------------------


def verifiable_paths(doc: dict[str, Any]) -> list[str]:
    """The claims this manifest makes that a check could prove, in report order.

    Deliberately coarse — one path per SURFACE, not per attribute. A finer partition would report
    a wall of `declared_only` rows nobody reads, and the question this file answers is "was this
    surface tested", not "was every adjective on it tested".
    """
    paths = ['harness', 'sources', 'surfaces.agent_context']
    if (doc.get('agent') or {}).get('entry'):
        paths.append('agent.entry')
    if doc['surfaces']['render_actions']:
        paths.append('surfaces.render_actions')
    paths += [f'surfaces.tools[{t["name"]}]' for t in doc['surfaces']['tools']]
    paths += [f'surfaces.knowledge[{k["name"]}]' for k in doc['surfaces']['knowledge']]
    return paths


#: The banks of the approved agent map (`artifacts/agent_topology.json`); the report page keys a surface
#: as `surfaces.<bank>[<name>]` in the bank the map lists it under.
MAP_BANKS = ('tools', 'subagents', 'skills', 'rag', 'knowledge')


def map_paths(workdir: Path) -> set[str]:
    """`surfaces.<bank>[<name>]` for every surface the agent map lists — what a probe may claim beyond the
    config's own tools (a live tool, a lookup in the `rag` bank, a sub-agent). A bench with no map has none."""
    topology = _read_json(workdir / 'artifacts' / 'agent_topology.json')
    banks = [topology.get(bank) for bank in MAP_BANKS] if isinstance(topology, dict) else []
    return {f'surfaces.{bank}[{s["name"]}]' for bank, rows in zip(MAP_BANKS, banks) if isinstance(rows, list)
            for s in rows if isinstance(s, dict) and s.get('name')}


def _covers(check_field: str, field: str) -> bool:
    """A check that verifies `surfaces.knowledge[X]` also covers `surfaces.knowledge[X].sources`."""
    return check_field == field or check_field.startswith(field + '.') or field.startswith(check_field + '.')


def coverage(doc: dict[str, Any]) -> dict[str, list[str]]:
    """field path -> the check ids that verify it (possibly none).

    A map surface the config does not declare is a field only once a check claims it: an unclaimed one
    stays off the record, where the page marks it unrecorded, rather than joining the declared-only list."""
    fields = verifiable_paths(doc)
    fields += list(dict.fromkeys(v for c in doc['checks'] for v in c['verifies'] if v.startswith('surfaces.') and v not in fields))
    return {
        field: [c['id'] for c in doc['checks'] if any(_covers(v, field) for v in [*c['verifies'], *c.get('witnesses', [])])]
        for field in fields
    }


def resolve_field(doc: dict[str, Any], path: str) -> Any:
    """`surfaces.render_actions[*].name` / `surfaces.tools[get_order].mode` -> the value.

    Used only by `expect.equals` on a surface check, so the value it returns is the thing the
    agent's own live surface is compared against.
    """
    match = _SELECTOR_RE.match(path)
    if match is None:
        return None
    node: Any = doc
    for part in match.group('head').split('.'):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    selector = match.group('sel')
    tail = [p for p in (match.group('tail') or '').split('.') if p]

    def descend(value: Any) -> Any:
        for part in tail:
            value = value.get(part) if isinstance(value, dict) else None
        return value

    if selector is None:
        return descend(node)
    if not isinstance(node, list):
        return None
    if selector == '*':
        return [descend(item) for item in node]
    return descend(next((i for i in node if isinstance(i, dict) and str(i.get('name')) == selector), None))


# --------------------------------------------------------------------------------------------
# prerequisites + planning
# --------------------------------------------------------------------------------------------


def env_file_path(doc: dict[str, Any], workdir: Path) -> Path | None:
    """The env file the HARNESS loads, or None when there is none.

    `harness.env_file` when declared (relative to the agent module, or absolute). Otherwise the nearest `.env` from the
    agent module up to its repository's top level, the walk the harness's dotenv load makes; outside a repository only
    the module's own. A path off this machine's local drives is never touched (`_platform.is_local_drive_path`).
    """
    harness = doc.get('harness') if isinstance(doc.get('harness'), dict) else {}
    base = agent_dir(workdir)
    declared = harness.get('env_file')
    if isinstance(declared, str) and declared.strip():
        candidates = [base / Path(declared.strip()).expanduser()]
    else:
        candidates = []
        for directory in (base, *base.parents):
            if not _platform.is_local_drive_path(directory, within=(base,)):
                break
            candidates.append(directory / '.env')
            if (directory / '.git').exists():
                break
        else:
            candidates = [base / '.env']
    for candidate in candidates:
        if _platform.is_local_drive_path(candidate, within=(base,)) and candidate.is_file():
            return candidate
    return None


def env_file_label(doc: dict[str, Any], workdir: Path) -> str | None:
    """Where-we-looked text for the env file: its path; a declared one that is not there as `<path> (declared, not
    found)`, so a missing setting is asked for in the file the user named; None when none is declared or found."""
    found = env_file_path(doc, workdir)
    if found is not None:
        return str(found)
    declared = (doc.get('harness') or {}).get('env_file') if isinstance(doc.get('harness'), dict) else None
    if isinstance(declared, str) and declared.strip():
        return f'{agent_dir(workdir) / Path(declared.strip()).expanduser()} (declared, not found)'
    return None


def _env_file_values(doc: dict[str, Any], workdir: Path) -> str | None:
    """The text of the .env the HARNESS loads (:func:`env_file_path`)."""
    env_file = env_file_path(doc, workdir)
    try:
        return env_file.read_text(encoding='utf-8') if env_file is not None else None
    except Exception:
        return None


def _runs_on_claude_cli(doc: dict[str, Any]) -> bool:
    """Whether the agent is a Claude Agent SDK agent (`Claude Agent SDK`, `claude_agent_sdk`, `claude-agent-sdk`)."""
    agent = doc.get('agent')
    framework = re.sub(r'[^a-z0-9]', '', str(agent.get('framework') or '').lower()) if isinstance(agent, dict) else ''
    return 'claudeagentsdk' in framework


def _with_cli_login(doc: dict[str, Any], provider: str, names: tuple[str, ...]) -> tuple[str, ...]:
    """``names`` plus the Claude CLI's login token when an Anthropic provider's agent runs on that CLI."""
    if provider == 'anthropic' and _runs_on_claude_cli(doc) and CLAUDE_CLI_TOKEN_ENV not in names:
        return (*names, CLAUDE_CLI_TOKEN_ENV)
    return names


#: The Claude CLI's own structured answer to "is it signed in": a JSON object whose `loggedIn` is a bool.
CLAUDE_AUTH_STATUS_ARGV = ('claude', 'auth', 'status', '--json')


def _claude_cli_login_present() -> bool:
    """Whether the Claude CLI reports a login: `loggedIn` from `claude auth status --json`, parsed in-process. Only that
    one key is read; the rest of the reply names the account and is never printed or kept. The CLI exits non-zero when
    signed out and still answers in JSON, so the exit code decides nothing. Undecidable (no `claude` on PATH, a failed
    start, a timeout, a reply with no boolean `loggedIn`) is treated as present, like an unknown provider: this check
    must not BLOCK, and a genuinely missing login then surfaces as the smoke's own failure with the child's stderr."""
    if shutil.which(CLAUDE_AUTH_STATUS_ARGV[0]) is None:
        return True
    # A `claude` started from inside a Claude Code session inherits its session id; `auth status` opens no session.
    env = {k: v for k, v in os.environ.items() if not k.startswith('CLAUDE_CODE_SESSION')}
    try:
        done = subprocess.run(list(CLAUDE_AUTH_STATUS_ARGV), capture_output=True, text=True, **utf8_child_text(), timeout=10, check=False, env=env)
        reply = json.loads(done.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return True
    logged_in = reply.get('loggedIn') if isinstance(reply, dict) else None
    return logged_in if isinstance(logged_in, bool) else True


def resolved_provider(doc: dict[str, Any], workdir: Path) -> tuple[str, tuple[str, ...]]:
    """(provider, the env var names that carry its key) for the agent's main model.

    `harness.model_env` names the env var that selects the model at run time. When its value (from the
    environment, else that one name in the harness's .env) carries an explicit provider prefix
    (`<provider>/<model>` or `<provider>:<model>`; an `init_chat_model` id through `_PROVIDER_ALIASES`) that has a
    row here, that row decides, ahead of an explicit `provider_env` (written for the default model). A bare model name, an unset variable or an
    unknown prefix falls back to `provider_env`, then the declared `provider`'s row. The value is never
    recorded: only the resolved provider name is.
    """
    harness = doc['harness']
    variable = harness.get('model_env')
    value = os.environ.get(variable) if isinstance(variable, str) and variable else None
    if isinstance(variable, str) and variable and value is None:
        text = _env_file_values(doc, workdir) or ''
        found = re.search(rf'^\s*(?:export\s+)?{re.escape(variable)}\s*=\s*["\']?([^"\'\s#]+)', text, re.MULTILINE)
        value = found.group(1) if found else None
    prefix = re.match(r'([A-Za-z][A-Za-z0-9_-]*)[/:]', value or '')
    named = _PROVIDER_ALIASES.get(prefix.group(1).lower(), prefix.group(1).lower()) if prefix else None
    if named in _PROVIDER_ENV:
        return named, _with_cli_login(doc, named, _PROVIDER_ENV[named])
    provider = str(harness.get('provider') or '').lower()
    provider = _PROVIDER_ALIASES.get(provider, provider)
    return provider, _with_cli_login(doc, provider, tuple(harness.get('provider_env') or ()) or _PROVIDER_ENV.get(provider, ()))


def provider_key_present(doc: dict[str, Any], workdir: Path) -> bool:
    """Whether the agent's provider key is reachable. PRESENCE ONLY — never the value.

    Checked here and not left to the smoke run because "the agent crashed" and "nobody gave it a
    key" are different reports, and only the first is about the customer's agent. The provider is
    :func:`resolved_provider`'s: the model the run selects, else the declared default.
    """
    provider, names = resolved_provider(doc, workdir)
    if not names:
        # Unknown provider: this check cannot decide, so it must not BLOCK. Treated as present;
        # a genuinely missing key then surfaces as the smoke's own failure, with the child's stderr.
        return True
    if any(os.environ.get(name) for name in names):
        return True
    if any((Path.home() / rel).exists() for rel in _PROVIDER_FILES.get(provider, ())):
        return True
    if provider == 'anthropic' and _runs_on_claude_cli(doc) and _claude_cli_login_present():
        return True
    # The .env the HARNESS loads (`env_file_path`); names only.
    pairs = _env_file_pairs(doc, workdir)
    return any(pairs.get(name) for name in names)


def name_source(doc: dict[str, Any], workdir: Path, name: str, pairs: Mapping[str, str] | None = None) -> str | None:
    """Where setting ``name`` is set for the run: ``environment``, the env file's path, or None. PRESENCE ONLY.

    The environment is this process's, which is what a command Claude Code starts (the runner included) inherits.
    An empty value is not set. ``pairs`` is :func:`_env_file_pairs`, passed when asking about several names.
    """
    if os.environ.get(name):
        return 'environment'
    if (_env_file_pairs(doc, workdir) if pairs is None else pairs).get(name):
        return str(env_file_path(doc, workdir))
    return None


#: A variable that is a credential only with its partner: an access key id without its secret signs nothing.
_PAIRED_ENV = {'AWS_ACCESS_KEY_ID': 'AWS_SECRET_ACCESS_KEY'}
#: A default file that only POINTS at a credential (a profile's region, an SSO start URL): a candidate, never found.
_POINTER_FILES = frozenset({'.aws/config'})


def _has_profile(path: Path) -> bool:
    """Whether an INI credentials file holds at least one `[profile]` header. Only header lines are matched; no value
    is kept, printed or returned."""
    try:
        return any(re.match(r'\s*\[[^\]]+\]\s*$', line) for line in path.read_text(encoding='utf-8', errors='replace').splitlines())
    except OSError:
        return False


def provider_credentials(doc: dict[str, Any], workdir: Path) -> dict[str, str | None]:
    """Model provider -> where a USABLE credential was found, or None: a variable name (with its partner, when it needs
    one), a default credential file under `~` (an INI one only when it holds a profile), or the Claude CLI login for an
    agent that runs on that CLI. A file that only points at a credential reads `candidate (<file>)`. Presence only: no
    value is read out; a provider listed under two names (`gemini` / `google`) is listed once."""
    pairs = _env_file_pairs(doc, workdir)
    found: dict[str, str | None] = {}
    seen: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    for provider, names in _PROVIDER_ENV.items():
        files = _PROVIDER_FILES.get(provider, ())
        if (names, files) in seen:
            continue
        seen.add((names, files))
        hit = next((n for n in names if name_source(doc, workdir, n, pairs)
                    and (n not in _PAIRED_ENV or name_source(doc, workdir, _PAIRED_ENV[n], pairs))), None)
        candidate = None
        for rel in files:
            path = Path.home() / rel
            if hit or not path.is_file():
                continue
            if rel in _POINTER_FILES:
                candidate = f'candidate (~/{rel})'
            elif path.suffix == '.json' or _has_profile(path):
                hit = f'~/{rel}'
        if hit is None and provider == 'anthropic' and _runs_on_claude_cli(doc) and _claude_cli_login_present():
            hit = 'Claude CLI login'
        found[provider] = hit or candidate
    return found


#: The contract checker's console script, the one `scan_contract.sh` searches for.
CHECKER_NAME = 'plurai-scan-contract'


def scan_contract_checker(workdir: Path) -> str | None:
    """The checker `scan_contract.sh` would run for this workdir, or None when it would find none.

    Mirrors the script's search so the probe and the script cannot disagree: an explicit
    `$PLURAI_SCAN_CONTRACT_BIN`, then `$VIRTUAL_ENV/bin`, then the agent module's `.venv/bin`, then its other
    `.venv*` / `venv*` directories (sorted), then PATH; on every platform each `bin` is followed by its venv's
    `Scripts` (a Windows venv), where a console script is `<name>.exe`. A tier counts only for an executable FILE. Reading
    PATH alone would declare the checker absent while the script finds it in a venv, and the contract would
    then never run. An override that names no executable file is the script's own usage error
    (exit 64), not a missing install, so it is reported by the script and not here.
    """
    override = os.environ.get('PLURAI_SCAN_CONTRACT_BIN')
    if override:
        return override
    agent = agent_dir(workdir)
    venvs = [Path(os.environ['VIRTUAL_ENV'])] if os.environ.get('VIRTUAL_ENV') else []
    venvs.append(agent / '.venv')
    venvs += [Path(venv) for pattern in ('.venv*', 'venv*') for venv in sorted(glob.glob(str(agent / pattern)))]
    for venv in venvs:
        for directory, name in ((venv / 'bin', CHECKER_NAME), (venv / 'Scripts', f'{CHECKER_NAME}.exe')):
            candidate = directory / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return shutil.which(CHECKER_NAME)


def missing_prerequisite(check: dict[str, Any], doc: dict[str, Any], workdir: Path) -> str | None:
    """The first prerequisite that is absent, or None. Names the thing, never a generic failure."""
    for need in check.get('requires', []):
        if need == 'node' and shutil.which('node') is None:
            return 'node is not on PATH'
        if need == 'dist' and not (agent_dir(workdir) / 'dist').is_dir():
            return 'the agent has no built dist/ (run its build first)'
        if need == 'checker' and scan_contract_checker(workdir) is None:
            return 'plurai-scan-contract is not installed'
        if need == 'corpus_roots':
            missing = [r for r in doc['sources']['corpus_roots'] if not resolve_source(workdir, r).exists()]
            if missing:
                return f'corpus root(s) not found: {", ".join(missing)}'
        # Probes need no grant (they run with no network); only an explicit earlier "No" holds them.
        if need == 'consent:probes' and doc['consent']['probes']['state'] == 'declined':
            return 'probe consent is declined'
        if need == 'consent:live' and doc['consent']['live']['state'] != 'granted':
            return f'live-probe consent is {doc["consent"]["live"]["state"]}'
        if need == 'provider_key' and not provider_key_present(doc, workdir):
            provider, names = resolved_provider(doc, workdir)
            if provider == 'anthropic' and _runs_on_claude_cli(doc):
                return f'the Claude login the agent runs on was not found (sign in with `claude`, or set {CLAUDE_CLI_TOKEN_ENV})'
            return f'the agent provider key ({provider}: {" or ".join(names)}) is not set in the environment or the module .env'
        if need == 'smoke_inputs' and not doc['agent'].get('smoke_inputs'):
            # Never defaulted: a world nobody chose proves nothing. Recorded, and the other checks run.
            return 'agent.smoke_inputs is not declared, so there is no world to run the pipeline over'
        if need == 'no_real_tools' and (real := [t['name'] for t in doc['surfaces']['tools'] if t.get('mode') == 'real' and t['name'] not in _inert_map_tools(workdir)]):
            return f'tool(s) {", ".join(real)} run for real, and this check may reach only the local mock and the model route'
        if need == 'mock_built' and (unbuilt := _mock_unbuilt(workdir)):
            return unbuilt
    return None


def _inert_map_tools(workdir: Path) -> set[str]:
    """The map's tools typed `runs: in_process` with `effects: ['none']`: they run inside the agent and reach nothing
    beyond it, so running them for real keeps a check on the local mock and the model route."""
    topology = _read_json(workdir / 'artifacts' / 'agent_topology.json')
    tools = topology.get('tools') if isinstance(topology, dict) else None
    return {str(t.get('name')) for t in tools if isinstance(t, dict) and t.get('runs') == 'in_process' and t.get('effects') == ['none']} \
        if isinstance(tools, list) else set()


#: Why the mocked tools are not checked at scan time: the suite builds the mock. Deferred by design, so the
#: integrity line does not count it as a check that did not run.
MOCK_UNBUILT = 'the mock is not built yet; /simulation-generate-suite builds it'


def _mock_unbuilt(workdir: Path) -> str | None:
    """Why the deployed mock cannot be checked against this bench's contract tests now, or None.

    A mock stood up (deployed, or edited — Step 6.6 writes `.seed_probe_world.json` after either) before the
    config or the contract test last changed was built from other inputs: its answers to the new tests would
    read as mock defects. And a mock off this machine sits behind the backend's bearer proxy, which the token
    reaches only on the bound backend's origin (`probe_mock.py`)."""
    url_file, contract = workdir / '.mock_service_url', workdir / 'test_mock_contract.py'
    url = url_file.read_text(encoding='utf-8').strip() if url_file.is_file() else ''
    if not url:
        return MOCK_UNBUILT
    # A bench whose setup writes no contract tests (a hosted agent) is decided by the world's witness tests alone.
    if not contract.is_file() and not (workdir / MOCK_WITNESS_DIR / MOCK_WITNESS_FILES[0]).is_file():
        return gap_reason(workdir / MOCK_WITNESS_DIR) or f'the bench has neither test_mock_contract.py nor {MOCK_WITNESS_DIR}/{MOCK_WITNESS_FILES[0]} to check the mock against'
    stood_up = max(p.stat().st_mtime for p in (url_file, workdir / '.seed_probe_world.json') if p.is_file())
    if any(p.is_file() and p.stat().st_mtime > stood_up for p in (workdir / 'plurai.config.yaml', contract)):
        return 'the mock was not rebuilt since plurai.config.yaml or test_mock_contract.py changed'
    if not url_guard.is_loopback(url) and not _bearer_for(url):
        return 'PLURAI_TOKEN is not set for the backend that serves the mock, so its bearer proxy would refuse every call'
    return None


def mode_rule_holds(rule: str, doc: dict[str, Any]) -> bool:
    """`<field path> == <value>` — the only grammar, on purpose.

    A general expression language here would be a second place where a bench decides what runs,
    and the manifest exists so there is exactly one. Unparseable rules return False and are
    reported by `validate`, never silently treated as true.
    """
    # `and` joins CONJUNCTS of the same one grammar — still no expression language, and still
    # one place where a bench decides what runs, for a check whose applicability is two facts.
    for conjunct in rule.split(' and '):
        left, sep, right = conjunct.partition('==')
        if not sep:
            return False
        if str(resolve_field(doc, left.strip())) != right.strip().strip('"\''):
            return False
    return True


#: What a BUILTIN's verdict is a function of, beyond its own declaration: the plugin script it runs
#: (plugin-relative — the path `command_for` builds, pinned equal by test) and the bench inputs it
#: reads. A builtin has no `script` or `derived_from` of its own, so without this its signature hashed
#: only its declaration and could never show that a recorded pass still stands — which is why `--only`
#: used to drop every builtin's pass to `not_run`. Input kinds (:data:`_INPUT_READERS`):
#:
#:     harness   the harness fingerprint's files (simulate.py, its split helpers, plurai.config.yaml, a declared
#:               entry wrapper, and a bridge bench's `bridge/`) — `harness_fingerprint.py`, the ONE harness identity —
#:               with the config read as `config` reads it (:func:`harness_digest`)
#:     config    plurai.config.yaml alone, parsed, minus the run driver's boolean declarations (:func:`config_digest`)
#:     prd       simulation_prd.md
#:     claim_audit  the Step-9c claim audit `lint_prd` reads beside the PRD (`artifacts/prd_claim_audit.jsonl`, `claim_sites.json`)
#:     agent     every file of the agent module outside the workdir (:func:`_tree_digest`); a bridge bench's
#:               agent is the map's `agent_root` (:func:`agent_input_root`)
#:     sdk       the SDK the smokes import (:func:`sdk_identity_stamp`)
#:     corpus    each declared corpus root, file or tree
#:     probe_world  the probe world `seed_readback` Inits (:data:`SEED_PROBE_WORLD`)
#:     mock      the deployed mock's handles (`.mock_service_url`, `.env_id`) and the contract tests run against it
#:
#: and the manifest SECTIONS it acts on — what `command_for` passes it (the agent's language and entry
#: symbol, the interception route, the corpus roots and the stateless finding) and what the script
#: reads back from `bench_manifest.json` itself (`agent.smoke_inputs`, `harness.resume`,
#: `harness.tool_interception`). Over-inclusion only costs a re-run; an input left out lets a pass
#: stand over a bench it never saw, so a builtin missing from this table is never reused.
_SMOKE_SCRIPT = 'skills/simulation-setup-python/scripts/verify_smoke.py'
_BUILTIN_INPUTS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    'verify_config': ('skills/simulation-setup/scripts/verify_config.py', ('harness',), ('agent', 'harness')),
    'verify_simulate': ('skills/simulation-setup-python/scripts/verify_simulate.py', ('harness',), ('agent', 'harness')),
    'lint_prd': ('skills/simulation-setup/scripts/lint_prd.py', ('prd', 'config', 'agent', 'claim_audit'), ()),
    'verify_prd_appendix': ('skills/simulation-setup/scripts/verify_prd_appendix.py', ('prd',), ()),
    # A smoke drives the agent's own code through the harness on the SDK: those three are its world.
    'smoke_init': (_SMOKE_SCRIPT, ('harness', 'agent', 'sdk'), ('agent', 'harness')),
    'smoke_turn': (_SMOKE_SCRIPT, ('harness', 'agent', 'sdk'), ('agent', 'harness')),
    'smoke_pipeline': (_SMOKE_SCRIPT, ('harness', 'agent', 'sdk'), ('agent', 'harness')),
    'scan_contract': ('scripts/scan_contract.sh', ('prd', 'agent', 'corpus'), ('sources', 'surfaces')),
    'mock_contract': ('scripts/mock_contract_check.py', ('config', 'mock'), ('surfaces',)),
    # Its static half reads the map, the config and simulate.py; its route probe drives the bridge child on the SDK.
    'verify_bridge': ('skills/simulation-setup-bridge/scripts/verify_bridge.py', ('harness', 'config', 'agent', 'sdk'), ('agent', 'harness')),
    # The Init smoke over the probe world: what seed_state writes, read back through the harness's world read.
    'seed_readback': (_SMOKE_SCRIPT, ('harness', 'agent', 'sdk', 'probe_world'), ('agent', 'harness')),
}

#: Trees and files inside an agent module that the TOOLING writes, never the agent's behaviour: caches,
#: the plugin's own state (`.plurai` moves on every hook), the session's local permission
#: grants (`.claude/settings.local.json`) and the managed `.gitignore` block. Hashed, each would turn every
#: smoke stale minutes after it passed — the false "not verified" this table exists to end, reworded.
_DIGEST_SKIP_DIRS = frozenset({'.plurai', '.mypy_cache', '.pytest_cache', '.ruff_cache', '.tox', '.nox'})
_DIGEST_SKIP_FILES = frozenset({'.DS_Store', '.gitignore'})


def _unreadable() -> str:
    """A digest that equals nothing, ever: a file that cannot be read cannot be shown unchanged."""
    return 'unreadable:' + os.urandom(8).hex()


def _tree_digest(root: Path, *, exclude: Path | None = None) -> str | None:
    """One hash over a file, or over every file under a directory by relative path and content.

    None when `root` does not exist (ABSENCE IS A VALUE, as for :func:`sha256_path`). Skips the
    agent's dependencies and history (`_NOT_AGENT_FILES`), tooling state (`_DIGEST_SKIP_*`), and
    `exclude` — the workdir, whose artifacts move on every verify.
    """
    if root.is_file():
        try:
            return sha256_path(root)
        except OSError:
            return _unreadable()
    if not root.is_dir():
        return None
    skip = exclude.resolve() if exclude is not None else None
    files: dict[str, str | None] = {}
    for here, dirs, names in os.walk(root):
        base = Path(here)
        dirs[:] = sorted(d for d in dirs if d not in _NOT_AGENT_FILES and d not in _DIGEST_SKIP_DIRS and (skip is None or (base / d).resolve() != skip))
        for name in sorted(names):
            if name in _DIGEST_SKIP_FILES or name.endswith('.pyc') or (base.name == '.claude' and name == 'settings.local.json'):
                continue
            path = base / name
            try:
                files[path.relative_to(root).as_posix()] = sha256_path(path)
            except OSError:
                files[path.relative_to(root).as_posix()] = _unreadable()
    return sha256_bytes(json.dumps(files, sort_keys=True, separators=(',', ':')).encode('utf-8'))


def agent_input_root(workdir: Path) -> Path:
    """The tree the `agent` input hashes: a bridge bench's map `agent_root` (relative to the workdir; the agent may sit
    beside `plurai_sim/`, not above it), else the agent module. A map that cannot be read names no root of its own."""
    try:
        root = json.loads((workdir / BRIDGE_MAP).read_text(encoding='utf-8')).get('agent_root')
    except (OSError, ValueError, AttributeError):
        root = None
    return (workdir / root).resolve() if isinstance(root, str) and root.strip() else agent_dir(workdir)


def _run_driver_declared_keys() -> tuple[frozenset[str], frozenset[str]]:
    """The top-level config keys only the run driver writes (`hooks/_run_driver.py` DECLARED_BY_CONFIG), and the retired
    ones that answer nothing any more (`RETIRED_DECLARATION_KEYS`); both empty if unreadable."""
    try:
        hooks = str(plugin_root() / 'hooks')
        if hooks not in sys.path:
            sys.path.insert(0, hooks)
        import _run_driver  # noqa: PLC0415 - hooks/ is only on sys.path here

        return frozenset(_run_driver.DECLARED_BY_CONFIG.values()), frozenset(_run_driver.RETIRED_DECLARATION_KEYS)
    except Exception:  # noqa: BLE001 - no table read hashes every key, which only costs a re-run
        return frozenset(), frozenset()


def config_digest(workdir: Path) -> str | None:
    """`plurai.config.yaml` as the checks read it: its parsed content, without the run driver's declarations.

    Every check reads the config through a YAML parser, so the digest is over the parsed mapping; a
    comment or a blank line changes no verdict. A run driver-declaration key (`anonymize`) set to a
    boolean is left out: it answers a gate (upload posture) and changes nothing a check verifies, yet the
    run driver writes it at suite start, after the scan's checks passed, and hashed it marked every
    config/harness/smoke pass stale. A non-boolean value declares nothing, so it stays in. A
    retired declaration key (`approve_harness_edits`) is left out whatever its value: it answers nothing.
    A config that does not parse to a mapping hashes by its bytes.
    """
    path = workdir / 'plurai.config.yaml'
    data = _load_yaml(path)
    if not isinstance(data, dict):
        return sha256_path(path)
    declared, retired = _run_driver_declared_keys()
    kept = {key: value for key, value in data.items() if not (key in retired or (key in declared and isinstance(value, bool)))}
    return sha256_bytes(json.dumps(kept, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8'))


def harness_digest(workdir: Path) -> str:
    """The harness fingerprint's file set, with `plurai.config.yaml` read as :func:`config_digest` reads it."""
    lang = detect_lang(workdir)
    files = dict(harness_fingerprint(workdir, lang)['files'])
    files['plurai.config.yaml'] = config_digest(workdir)
    return sha256_bytes(json.dumps({'lang': lang, 'files': files}, sort_keys=True, separators=(',', ':')).encode('utf-8'))


#: Input kind -> its digest, read from the bench as it is NOW. See :data:`_BUILTIN_INPUTS`.
_INPUT_READERS: dict[str, Callable[[Path, dict[str, Any]], Any]] = {
    'harness': lambda workdir, _doc: harness_digest(workdir),
    'config': lambda workdir, _doc: config_digest(workdir),
    'prd': lambda workdir, _doc: sha256_path(workdir / 'simulation_prd.md'),
    'claim_audit': lambda workdir, _doc: {name: sha256_path(workdir / 'artifacts' / name) for name in ('prd_claim_audit.jsonl', 'claim_sites.json')},
    'agent': lambda workdir, _doc: _tree_digest(agent_input_root(workdir), exclude=workdir),
    'sdk': lambda _workdir, _doc: sdk_identity_stamp(),
    'corpus': lambda workdir, doc: {
        str(rel): _tree_digest(resolve_source(workdir, str(rel)), exclude=workdir) for rel in ((doc.get('sources') or {}).get('corpus_roots') or [])
    },
    'probe_world': lambda workdir, _doc: sha256_path(workdir / SEED_PROBE_WORLD),
    'mock': lambda workdir, _doc: {name: sha256_path(workdir / name) for name in (
        '.mock_service_url', '.env_id', 'test_mock_contract.py', 'conftest.py', *(f'{MOCK_WITNESS_DIR}/{f}' for f in MOCK_WITNESS_FILES))},
}


def check_signature(check: dict[str, Any], workdir: Path, doc: dict[str, Any] | None = None, memo: dict[str, Any] | None = None) -> str:
    """What must not have changed for a recorded pass to still stand.

    The script's bytes and every `derived_from` path's bytes — so editing the agent code a probe
    reads re-runs the probe, and DELETING the probe re-runs it too (an absent file hashes as
    null, not as "unchanged"). A BUILTIN's are its :data:`_BUILTIN_INPUTS`: the plugin script it runs,
    the bench inputs it reads and the manifest sections it acts on. `doc` defaults to the manifest on
    disk; `memo` shares one read of each input across a whole `plan` (the agent tree, the SDK stamp).
    """
    parts: dict[str, Any] = {}
    if check.get('script'):
        parts[f'script:{check["script"]}'] = sha256_path(workdir / check['script'])
    for rel in (check.get('derived_from') or {}).get('paths', []):
        parts[f'src:{rel}'] = sha256_path(resolve_source(workdir, rel))
    if check.get('runner') == 'builtin' and check.get('id') in _BUILTIN_INPUTS:
        doc = doc if isinstance(doc, dict) else (_read_json(manifest_path(workdir)) or {})
        memo = {} if memo is None else memo
        tool, kinds, sections = _BUILTIN_INPUTS[check['id']]
        parts[f'tool:{tool}'] = sha256_path(plugin_root() / tool)
        for kind in kinds:
            if kind not in memo:
                memo[kind] = _INPUT_READERS[kind](workdir, doc)
            parts[f'in:{kind}'] = memo[kind]
        for section in sections:
            parts[f'decl:{section}'] = doc.get(section)
    # The DECLARATION is hashed too, not only the bytes it points at. Re-pointing a surface check's
    # `expect.equals` at a different field, or changing a check's args/requires/runner, changes what
    # the check MEANS — and left out of the signature, the recorded pass was reused under a
    # declaration that no longer described what had been run. Same reason `harness_fingerprint.py`
    # hashes its manifest alongside the file hashes.
    canonical = json.dumps({'check': check, 'parts': parts}, sort_keys=True, separators=(',', ':'), default=str)
    return sha256_bytes(canonical.encode('utf-8'))


def signature_witnesses_the_world(check: dict[str, Any]) -> bool:
    """Whether this check's signature covers anything BEYOND its own declaration.

    A signature over the declaration alone changes only when someone edits the manifest — "the
    signature still matches" then says nothing about the bench. Reusing a recorded pass on that
    basis is how `verify --only verify_config` once printed `smoke: 2-turn` and exited 0 over a
    mockless bench's ONLY live gate, which had not run in that invocation and might never have run.
    A probe's signature covers its script and `derived_from` paths; a builtin's covers its
    :data:`_BUILTIN_INPUTS` — so a builtin this table does not know is never reused.

    `plan`'s own `on_change` reuse is safe without this test only because `validate` refuses
    `policy: on_change` with no `derived_from.paths`; `--only` is the path with no such guard.
    """
    if check.get('runner') == 'builtin':
        return check.get('id') in _BUILTIN_INPUTS
    return bool(check.get('script') or (check.get('derived_from') or {}).get('paths'))


def _passed_at(was: dict[str, Any], prior: dict[str, Any] | None) -> str:
    """When a recorded pass was established: its own `passed_at` (a reuse carries it), else the record it was written in."""
    return str(was.get('passed_at') or (prior or {}).get('ran_at') or 'an earlier verify')


def carry_forward(check: dict[str, Any], signature: str, was: dict[str, Any] | None, prior: dict[str, Any] | None) -> dict[str, Any]:
    """The result of a check `verify --only` did NOT select: its LAST recorded result, never a downgrade.

    A recorded pass (`verified`, `reused`, or a `stale` one that remembers its `passed_signature`)
    is `reused` while its signature still witnesses the inputs it passed on — carrying its pass time,
    evidence and runtime witnesses — and `stale` otherwise. Never `not_run`: a partial re-verify used to
    record a scan's passing two-turn smoke as `not_run`, and the overview page then contradicted the
    scan's own hand-off with "Entry point / Harness / Agent context not verified". A check with no pass
    on record has nothing to keep, and stays `not_run` naming what it last recorded.
    """
    entry: dict[str, Any] = {'id': check['id'], 'signature': signature, 'cause': CAUSE_DESELECTED}
    status = (was or {}).get('status')
    if was is None or not (gate_passed(was) or status == STATUS_STALE):
        last = f'; its last recorded result was {status}' if status else ', and no earlier result is recorded'
        return {**entry, 'status': STATUS_NOT_RUN, 'reason': 'not selected by --only' + last}
    passed_signature = was.get('signature') if gate_passed(was) else was.get('passed_signature')
    when = _passed_at(was, prior)
    # What the pass recorded beside its status: its proof, its runtime witnesses (`agent.entry` is
    # proved by nothing else), its settled findings, and a contract's PRD binding (the suite's
    # contract gate reads it, and a reuse holds only over the PRD it passed on).
    kept = {key: was[key] for key in ('evidence', 'witnesses', 'findings', 'prd_sha256') if key in was}
    witnessed = signature_witnesses_the_world(check)
    if witnessed and passed_signature and passed_signature == signature:
        return {
            **entry, **kept, 'status': STATUS_REUSED, 'reason': f'not selected by --only; unchanged since it passed at {when}',
            'reused_at': signature[:12], 'passed_at': when,
        }
    if not witnessed:
        why = 'nothing records what it depends on, so it cannot be shown unchanged'
    elif not passed_signature:
        why = 'no signature was recorded with that pass, so it cannot be shown unchanged'
    else:
        why = 'its inputs changed since'
    return {
        **entry, **kept, 'status': STATUS_STALE, 'reason': f'not selected by --only; passed at {when}, not re-run in this verify, and {why}',
        'passed_at': when, 'passed_signature': passed_signature,
    }


#: The builtins that exercise the harness's OWN tool wrapping. On a bench whose tools are intercepted
#: at the agent's host they test a route the bench does not use, so they record `not_run` by name.
_HOST_INTERCEPTED_SKIP = frozenset({'verify_simulate', 'smoke_init', 'smoke_turn', 'smoke_pipeline', 'seed_readback'})

#: The world `seed_readback` seeds and reads back: every declared type, one row each with every declared field. Written by
#: generate-mock-env Step 6.6 from the env job's `seed_probe_world`, or on an env-less bench by /generate-scenarios from
#: the scenario job's.
SEED_PROBE_WORLD = '.seed_probe_world.json'


def _verify_smoke() -> Any:
    """The Python smoke script, imported only to ask whether simulate.py binds `seed_state` (its ONE reading of that)."""
    path = str(Path(__file__).resolve().parent.parent / 'skills' / 'simulation-setup-python' / 'scripts')
    if path not in sys.path:
        sys.path.insert(0, path)
    import verify_smoke  # noqa: PLC0415

    return verify_smoke


def no_seeded_world(workdir: Path) -> str | None:
    """Why no world reaches this harness at Init for `seed_readback` to read back (`not_applicable`), else None.

    No probe world on disk, a probe world of `null` (the env has no world schema), or a Python harness that binds no
    `seed_state`. A probe world that does not parse, or a harness that does not, is the smoke's to report (NO VERDICT)."""
    path = workdir / SEED_PROBE_WORLD
    if not path.is_file():
        return f'the bench has no {SEED_PROBE_WORLD} (no env or scenario job has published its probe world yet)'
    try:
        if json.loads(path.read_text(encoding='utf-8')) is None:
            return 'the env has no world schema, so no world reaches the harness at Init'
    except (OSError, ValueError):
        return None
    simulate = workdir / 'simulate.py'
    try:
        if simulate.is_file() and not _verify_smoke()._declares_seed_state(simulate):  # noqa: SLF001
            return 'simulate.py defines no seed_state, so no world reaches the harness at Init'
    except (OSError, SyntaxError, ValueError):
        return None
    return None


def host_interception(doc: dict[str, Any]) -> dict[str, Any] | None:
    """The declared `harness.tool_interception` block iff the tools are intercepted at the agent's host; absent means the harness."""
    block = (doc.get('harness') or {}).get('tool_interception')
    return block if isinstance(block, dict) and block.get('at') == 'agent_host' else None


def plan(doc: dict[str, Any], workdir: Path, mode: str, prior: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Per check: `run` / `reuse` / `not_run` / `not_applicable`, with the reason that decided it.

    Pure — it executes nothing — so the decision table is testable on its own, which is the half
    that used to live in a model's head.
    """
    prior_by_id = {r['id']: r for r in (prior or {}).get('results', [])}
    decisions: list[dict[str, Any]] = []
    memo: dict[str, Any] = {}
    for check in doc['checks']:
        signature = check_signature(check, workdir, doc, memo)
        entry: dict[str, Any] = {'id': check['id'], 'signature': signature}
        rule = check.get('mode_rule')
        if check.get('policy') == 'on_mode' and not mode_rule_holds(str(rule), doc):
            entry.update(decision=STATUS_NOT_APPLICABLE, reason=f'{rule} does not hold for this bench')
        elif 'corpus_roots' in check.get('requires', []) and not doc['sources']['corpus_roots']:
            # An EMPTY list has no missing member, so `missing_prerequisite` passed it and the runner
            # invoked the check with no corpus at all (the contract's exit 64 → not_run → gate 3).
            entry.update(decision=STATUS_NOT_APPLICABLE, reason='the bench declares no corpus (sources.corpus_roots is empty)')
        elif 'seeded_world' in check.get('requires', []) and (unseeded := no_seeded_world(workdir)):
            entry.update(decision=STATUS_NOT_APPLICABLE, reason=unseeded)
        elif check['runner'] == 'builtin' and check['id'] in _HOST_INTERCEPTED_SKIP and (hosted := host_interception(doc)):
            entry.update(decision=STATUS_NOT_RUN, reason=f"the mocked tools are intercepted at the agent's host ({hosted['why']})", route_smoke=hosted['route_smoke'])
        elif (missing := missing_prerequisite(check, doc, workdir)) is not None:
            entry.update(decision=STATUS_NOT_RUN, reason=missing)
        elif check.get('policy') == 'on_change' and mode == 'reuse':
            was = prior_by_id.get(check['id'])
            if was and was.get('status') in _PASSING and was.get('signature') == signature:
                entry.update(decision=STATUS_REUSED, reason='script and sources unchanged since it passed', reused_at=signature[:12], passed_at=_passed_at(was, prior))
            else:
                entry.update(decision='run', reason='no recorded pass for this signature')
        else:
            entry.update(decision='run', reason=f'policy {check.get("policy", "always")}, mode {mode}')
        decisions.append(entry)
    return decisions


# --------------------------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------------------------


def _plurai_py() -> str:
    return os.environ.get('PLURAI_PY') or sys.executable or 'python3'


def _child_python() -> str:
    # The SDK's child rule (`plurai/_subprocess.py` ChildProcess): the agent's code runs under the
    # interpreter the smoke child gets, never the tooling one that only had to import PyYAML.
    return os.environ.get('PLURAI_CHILD_PYTHON') or _plurai_py()


#: argv[0] of a bash check on a Windows host with no Git Bash: `_run_check` reports it not run, by name, and runs nothing.
NO_GIT_BASH = '<no Git Bash>'


def _bash() -> str:
    """`bash` off Windows; Git Bash on it (a bare `bash` there can start the System32 WSL launcher); else `NO_GIT_BASH`."""
    return _platform.git_bash() or NO_GIT_BASH


def command_for(check: dict[str, Any], doc: dict[str, Any], workdir: Path) -> list[str] | None:
    """The argv for a check, or None when this build does not know the builtin.

    Pure, so a test can assert the contract flags a bench produces without running anything —
    the flags used to be hand-assembled in a skill snippet, which is where a forgotten
    `--suffixes` and a forgotten `--source-root` each cost a scan.

    A BUILTIN's script comes from THIS PLUGIN and only its OPERANDS come from the bench, so the
    verdict describes the contract this build ships and `provenance.plugin_version` names the
    build that produced it. Four of them ran the setup flow's COPY in `plurai_sim/` instead: a
    copy is frozen at the version that made it, nothing re-syncs or stamps one, and a bench
    carrying a verifier 42 releases behind printed the success line and exited 0 where the
    shipped script refuses. A `python` / `node` / `bash` check is the customer's OWN probe and
    keeps running from the workdir — the rule is keyed on `runner`, not on a name.
    """
    root = plugin_root()
    setup_scripts = root / 'skills' / 'simulation-setup' / 'scripts'
    python_scripts = root / 'skills' / 'simulation-setup-python' / 'scripts'
    py = _plurai_py()
    if check['runner'] == 'python':
        return [_child_python(), str(workdir / check['script']), *check.get('args', [])]
    if check['runner'] == 'node':
        return ['node', str(workdir / check['script']), *check.get('args', [])]
    if check['runner'] == 'bash':
        return [_bash(), str(workdir / check['script']), *check.get('args', [])]

    simulate = 'simulate.py'
    builtin: dict[str, list[str]] = {
        'verify_config': [py, str(setup_scripts / 'verify_config.py'), str(workdir / 'plurai.config.yaml'), '--simulate', str(workdir / simulate)],
        'verify_simulate': [py, str(python_scripts / 'verify_simulate.py'), str(workdir / simulate)],
        'lint_prd': [py, str(setup_scripts / 'lint_prd.py'), str(workdir / 'simulation_prd.md'), '--config', str(workdir / 'plurai.config.yaml')],
        'verify_prd_appendix': [py, str(setup_scripts / 'verify_prd_appendix.py'), str(workdir / 'simulation_prd.md')],
        'smoke_init': [py, str(python_scripts / 'verify_smoke.py'), str(workdir / simulate)],
        'smoke_turn': [py, str(python_scripts / 'verify_smoke.py'), str(workdir / simulate), '--turn'],
        'smoke_pipeline': [py, str(python_scripts / 'verify_smoke.py'), str(workdir / simulate), '--pipeline'],
        'verify_bridge': [py, str(root / 'skills' / 'simulation-setup-bridge' / 'scripts' / 'verify_bridge.py'), str(workdir / simulate)],
        'seed_readback': [py, str(python_scripts / 'verify_smoke.py'), str(workdir / simulate), '--seed-world', str(workdir / SEED_PROBE_WORLD)],
    }
    for cid in FINDINGS_OUT_BUILTINS:
        # Right after the script, so the operands keep their places (a host-route verify_config drops `--simulate`).
        builtin[cid][2:2] = ['--findings-out', str(findings_path(workdir, cid))]
    if host_interception(doc):
        # The harness wraps no tool, so its source has nothing for the config's harness checks to read.
        at = builtin['verify_config'].index('--simulate')
        del builtin['verify_config'][at:at + 2]
    if check['id'] in builtin:
        symbol = doc['agent'].get('entry_symbol')
        if not check.get('witnesses') or not isinstance(symbol, dict):
            return builtin[check['id']]
        return [*builtin[check['id']], '--entry-symbol', json.dumps(symbol, sort_keys=True), '--agent-language', str(doc['agent']['language']),
                '--witness-out', str(witness_path(workdir, check['id']))]
    if check['id'] == 'scan_contract':
        argv = [
            _bash(),
            str(root / 'scripts' / 'scan_contract.sh'),
            '--workdir',
            str(workdir),
            '--prd',
            str(workdir / 'simulation_prd.md'),
            '--source-root',
            str(agent_dir(workdir)),
        ]
        for rel in doc['sources']['corpus_roots']:
            argv += ['--corpus', str(resolve_source(workdir, rel))]
        for suffix in doc['sources'].get('suffixes', []):
            argv += ['--suffixes', suffix]
        finding = (doc['surfaces']['agent_context'].get('finding') or '').strip()
        if finding:
            argv += ['--stateless-finding', finding]
        return argv
    if check['id'] == 'mock_contract':
        # Its witnesses name the mocked tools it reports on: `surfaces.tools[<name>]`. It runs under the plugin's own
        # pytest+requests interpreter (`tool_python.py`), never the agent's, which need not carry either.
        # The world's witness tests (C4) run beside the contract tests; the backend's own run of the deployed tree, when
        # it measured exactly that mock, stands in for both (`--backend-dir`).
        tools = [w[len('surfaces.tools['):-1] for w in check.get('witnesses', []) if w.startswith('surfaces.tools[')]
        return [py, str(root / 'scripts' / 'tool_python.py'), 'mock_contract', str(root / 'scripts' / 'mock_contract_check.py'),
                str(workdir / 'test_mock_contract.py'), '--witness-out', str(witness_path(workdir, check['id'])), '--tools', ','.join(tools),
                '--witness-dir', str(workdir / MOCK_WITNESS_DIR), '--answers-out', str(workdir / mock_shape_probe.ANSWERS),
                '--backend-dir', str(workdir / MOCK_BACKEND_RUN_DIR)]
    return None


#: `cause` of a check `verify --only` did not select: the page reads it as "not run in this scan", never as a failure.
CAUSE_DESELECTED = 'deselected'
#: An absolute or home path token inside a sentence (a URL's `//host/path` is not one).
_MACHINE_PATH = re.compile(r"(?<![\w./~:-])~?/(?:[^\s/'\"`:,;()\[\]]+/)+([^\s/'\"`:,;()\[\]]*)")
#: The reason a failed result is given when it printed nothing at all.
NO_OUTPUT_REASON = 'it failed and printed nothing'
#: The mark a check's verdict line opens with when the check failed (`✗ smoke FAILED — …`).
FAILURE_MARK = '✗'
#: `cause` of a check that exited clean over a failure its own evidence records — an advisory finding that holds
#: nothing, or one the plugin overruled in the gate ledger: `not_verified`, yet it gates as a pass (gates never block).
CAUSE_FAILURE_LET_THROUGH = 'failure_let_through'


def gate_passed(result: dict[str, Any] | None) -> bool:
    """Whether `result` lets the bench through: a pass, or a failure the gate let through (:func:`evidenced_status`)."""
    return bool(result) and (result.get('status') in _PASSING or result.get('cause') == CAUSE_FAILURE_LET_THROUGH)  # type: ignore[union-attr]


def _evidence_failure(evidence: str) -> str | None:
    """The failure `evidence` records, or None: a verdict line opening with :data:`FAILURE_MARK`, or a verifier's
    JSON verdict that says `fail` (`{"verdict": "fail", "findings": [{"severity": "fail", …}]}`; with no `verdict`
    key, one carrying a `fail` finding). A warning never makes a failure: a `pass` verdict carrying warnings stays a
    pass, the warnings kept in its evidence."""
    text = evidence.strip()
    if text.startswith(FAILURE_MARK):
        return text
    try:
        verdict = json.loads(text) if text.startswith('{') else None
    except ValueError:
        return None
    if not isinstance(verdict, dict):
        return None
    findings = verdict.get('findings')
    failed = [f for f in findings if isinstance(f, dict) and f.get('severity') == 'fail'] if isinstance(findings, list) else []
    if (verdict['verdict'] != 'fail') if 'verdict' in verdict else not failed:
        return None
    return '; '.join(f'[{f.get("code")}] {f.get("message")}' for f in failed) or f'the verdict is {verdict["verdict"]!r}'


def evidenced_status(result: dict[str, Any]) -> dict[str, Any]:
    """THE derivation of a result's status word from its own evidence: a pass whose evidence records a failure
    (:func:`_evidence_failure`) is `not_verified` (cause :data:`CAUSE_FAILURE_LET_THROUGH`), never `verified`.
    Keyed on the evidence alone — no check, tool or agent name."""
    failure = _evidence_failure(str(result.get('evidence') or '')) if result.get('status') in _PASSING else None
    if not failure:
        return result
    return {**result, 'status': STATUS_NOT_VERIFIED, 'cause': CAUSE_FAILURE_LET_THROUGH,
            'reason': f'its own evidence records a failure the gate let through, so nothing verified it: {failure[:200]}'}


def relative_text(text: str, workdir: Path) -> str:
    """`text` with no path that names this machine: the agent module's prefix is dropped (its paths read
    workdir-relative), and any other absolute or `~` path keeps only its last part. `verification.json`
    feeds the shared overview page, so a reason may carry a file's name but never its location."""
    for root in sorted({str(agent_dir(workdir)), str(agent_dir(workdir).resolve())}, key=len, reverse=True):
        text = text.replace(root.rstrip('/') + '/', '')
    return _MACHINE_PATH.sub(lambda m: m.group(1) or '<path>', text)


def with_reason(result: dict[str, Any], workdir: Path) -> dict[str, Any]:
    """`result`, with a `reason` on every non-passing status and no machine path in it.

    A failed check records its proof as `evidence`; the reader of the verification record is told WHY
    from `reason`, so a failure without one reads as unexplained. Derived from the evidence's first
    line when the check gave none."""
    if result.get('status') in _PASSING:
        return result
    reason = str(result.get('reason') or '').strip()
    if not reason:
        evidence = next((line.strip() for line in str(result.get('evidence') or '').splitlines() if line.strip()), '')
        reason = evidence[:240] or NO_OUTPUT_REASON
    return {**result, 'reason': relative_text(reason, workdir)}


def select_evidence(stdout: str, stderr: str, returncode: int) -> str:
    """The ONE line kept as a check's evidence — the manifest's whole extraction rule, in one place.

    On a FAILURE read stderr first: a check that prints progress on stdout and its diagnosis on
    stderr recorded the progress line as the evidence for its own failure, which is a
    success-looking fragment in the place an operator looks for the cause.

    Exposed (rather than inlined in :func:`execute`) so a check that cares what gets recorded for
    it can DRIVE this rule in its own tests instead of restating half of it: a guard that modelled
    only the stdout branch stayed green while a `--turn` smoke failure recorded a stderr advisory
    about a different check.
    """
    streams = (stderr, stdout) if returncode != 0 else (stdout, stderr)
    tail = next((s.strip().splitlines() for s in streams if s and s.strip()), [])
    return tail[-1][:400] if tail else ''


#: The builtins that mint gate findings (`scripts/gate_ledger.py`) and write them to `--findings-out`.
FINDINGS_OUT_BUILTINS = ('verify_config', 'verify_simulate', 'smoke_init', 'smoke_turn', 'smoke_pipeline', 'seed_readback')


def findings_path(workdir: Path, check_id: str) -> Path:
    """`$ART/.findings/<check>.json`: every gate finding the check's last run minted, raw (before the ledger)."""
    return workdir / 'artifacts' / '.findings' / f'{check_id}.json'


def _mints_findings(check: dict[str, Any]) -> bool:
    return check['runner'] == 'builtin' and check['id'] in FINDINGS_OUT_BUILTINS


def execute(check: dict[str, Any], doc: dict[str, Any], workdir: Path, timeout: int) -> dict[str, Any]:
    """Run one check. Returns its result fields, with the gate findings a builtin minted as `findings`;
    never raises for the check's own failure."""
    if not _mints_findings(check):
        return _run_check(check, doc, workdir, timeout)
    # Cleared first: an earlier run's findings must never stand in for this one's.
    findings_path(workdir, check['id']).unlink(missing_ok=True)
    outcome = _run_check(check, doc, workdir, timeout)
    if outcome['status'] not in (STATUS_VERIFIED, STATUS_FAILED):
        return outcome  # no verdict: whatever it minted before it stopped was settled against nothing
    written = _read_json(findings_path(workdir, check['id']))
    return {**outcome, 'findings': written} if isinstance(written, list) else outcome


def check_env(check: dict[str, Any], workdir: Path) -> dict[str, str]:
    """The environment a check runs in: this one without the backend pair (`PLURAI_URL` + `PLURAI_TOKEN`; a URL left
    without its token is a partial pair the SDK refuses) — except `mock_contract`'s, which calls the deployed mock
    behind the backend's bearer proxy and keeps the pair only when that mock is on the pinned backend origin
    (`probe_mock._bearer_for`). A declared check is a script from the workdir and never gets it."""
    env = {name: value for name, value in os.environ.items() if name not in ('PLURAI_URL', 'PLURAI_TOKEN')}
    if check['runner'] == 'builtin' and check['id'] == 'mock_contract':
        url_file = workdir / '.mock_service_url'
        if url_file.is_file() and _bearer_for(url_file.read_text(encoding='utf-8').strip()):
            env |= {name: os.environ[name] for name in ('PLURAI_URL', 'PLURAI_TOKEN') if name in os.environ}
    return env


#: Every probe check runs inside one of these, so "a probe reaches no network" holds whatever its
#: script does: DNS, a raw IP, loopback, subprocesses and C-level sockets are all denied. The first
#: that works on this host is used; none working means the probe does not run.
_NET_DENY_PREFIXES: dict[str, tuple[tuple[str, ...], ...]] = {
    'darwin': (('sandbox-exec', '-p', '(version 1)(allow default)(deny network*)'),),
    'linux': (('bwrap', '--dev-bind', '/', '/', '--unshare-net', '--'), ('unshare', '-rn')),
}
NO_NETWORK_SANDBOX_REASON = 'this machine has no network sandbox, so the probe did not run; its claims stay unprobed'


def _net_deny_works(prefix: list[str]) -> bool:
    try:
        return subprocess.run([*prefix, 'true'], capture_output=True, timeout=10, check=False, stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def net_deny_prefix() -> list[str] | None:
    """The argv prefix that runs a child with no network on this host, or None when there is none.

    A wrapper on PATH that cannot start (unprivileged user namespaces off, a nested sandbox) is
    no wrapper: it would turn into a probe failure instead of "the probe did not run"."""
    for prefix in _NET_DENY_PREFIXES.get(sys.platform, ()):
        if (found := shutil.which(prefix[0])) and _net_deny_works(argv := [found, *prefix[1:]]):
            return argv
    return None


def _run_check(check: dict[str, Any], doc: dict[str, Any], workdir: Path, timeout: int) -> dict[str, Any]:
    argv = command_for(check, doc, workdir)
    if argv is None:
        return {'status': STATUS_NOT_RUN, 'reason': f'no command is defined for builtin check {check["id"]!r} in this plugin build'}
    if argv[0] == NO_GIT_BASH:
        return {'status': STATUS_NOT_RUN, 'reason': _platform.GIT_BASH_MISSING, 'cause': 'no_git_bash'}
    # Checked for EVERY runner, not just the interpreted ones: a missing script handed to `bash`
    # or `node` exits 127, which is not in the no-verdict set, so a partial plugin install reported
    # the customer's bench as a blocking FAILURE.
    if not Path(argv[1]).is_file():
        return {'status': STATUS_NOT_RUN, 'reason': f'{Path(argv[1]).name} is not on disk'}
    if check['kind'] == 'live':
        return _run_live(check, argv, doc, workdir, timeout)
    env = check_env(check, workdir)
    if check['runner'] == 'builtin' and check['id'] == 'mock_contract':
        argv += ['--deadline-s', str(int(timeout * _MOCK_GATE_DEADLINE_SHARE))]
    # Keyed on the check's kind, never a tool name: a probe calls only local or mocked surfaces, so
    # the platform denies it the network instead of trusting the stubs its script wrote. No sandbox
    # means no probe. Smokes and builtins are not wrapped: they need the model provider.
    run_argv = argv
    if check['kind'] == 'probe':
        if (deny := net_deny_prefix()) is None:
            return {'status': STATUS_NOT_RUN, 'cause': 'no_network_sandbox', 'reason': NO_NETWORK_SANDBOX_REASON}
        run_argv = [*deny, *argv]
    try:
        done = subprocess.run(run_argv, cwd=str(agent_dir(workdir)), capture_output=True, text=True, timeout=timeout, check=False,
                              stdin=subprocess.DEVNULL, env=env)
    except subprocess.TimeoutExpired:
        return {'status': STATUS_NOT_RUN, 'reason': f'timed out after {timeout}s', ATTEMPTED: True, 'cause': 'timeout'}
    except Exception as exc:  # noqa: BLE001 - the reason is the report
        return {'status': STATUS_NOT_RUN, 'reason': f'could not start: {relative_text(str(exc), workdir)}'}

    evidence = select_evidence(done.stdout, done.stderr, done.returncode)
    if check['runner'] == 'python' and done.returncode != 0 and (m := _IMPORT_ERROR_RE.match(evidence)):
        # An interpreter that cannot import the agent measured nothing about it: a prerequisite.
        unset = '' if os.environ.get('PLURAI_CHILD_PYTHON') else " — set PLURAI_CHILD_PYTHON to the agent's interpreter"
        reason = f'the {check["kind"]} interpreter ({Path(argv[0]).name}) cannot import {m.group(1)!r}{unset}'
        return {'status': STATUS_NOT_RUN, 'reason': reason, 'evidence': evidence, ATTEMPTED: True, 'cause': f'unimportable:{m.group(1)}'}
    if check['kind'] == 'surface':
        return _surface_result(check, doc, done, evidence, workdir)
    if done.returncode in OPEN_FINDINGS_EXITS.get(check['id'], ()):
        found = _OPEN_FINDINGS_RE.search(done.stdout)
        count = int(found.group(1)) if found else None
        what = f'{count} PRD finding(s)' if count is not None else 'PRD findings'
        return {
            'status': STATUS_NOT_VERIFIED, 'evidence': evidence, 'open_findings': count,
            'reason': f'{what} are open (exit {done.returncode}) — fix the PRD and re-run, or accept one with the reason you verified (the last stderr line names how)',
        }
    if done.returncode in REFUSED_INPUT_EXITS.get(check['kind'], ()):
        return {'status': STATUS_NOT_VERIFIED, 'reason': f'it refused the bench\'s own inputs (exit {done.returncode}) — fix what the evidence names and re-run', 'evidence': evidence}
    if done.returncode in NO_VERDICT_EXITS.get(check['kind'], ()):
        what = 'the contract' if check['kind'] == 'contract' else f'the {check["kind"]} check'
        return {
            'status': STATUS_NOT_RUN, 'reason': f'{what} reached no verdict (exit {done.returncode})', 'evidence': evidence, ATTEMPTED: True,
            'cause': f'no_verdict_exit:{done.returncode}',
        }
    if done.returncode == 0:
        if check['kind'] == 'probe' and not evidence:
            # A probe stubbed out, early-returning, or swallowing its own exception exits 0 and
            # prints nothing — indistinguishable from one that proved something. An observation
            # nobody can read is not an observation.
            return {'status': STATUS_NOT_RUN, 'reason': 'the probe exited 0 but printed nothing, so it recorded no observation'}
        return {'status': STATUS_VERIFIED, 'evidence': evidence}
    if done.returncode in AGENT_RAISE_EXITS.get(check['kind'], ()):
        owner = {'owner': 'agent'}
    elif done.returncode in OWNER_UNDETERMINED_EXITS.get(check['kind'], ()):
        owner = {'owner': 'undetermined'}
    else:
        owner = {'owner': 'harness'} if check['kind'] == 'smoke' else {}
    return {'status': STATUS_FAILED, 'evidence': evidence, 'exit_code': done.returncode, **owner}


#: A live check's own deadline, and the pause before its one retry of a failed read: a blip that recovers is a read.
LIVE_TIMEOUT_S = 30
LIVE_RETRY_PAUSE_S = 2.0
#: The one `cause` of a live check that did not read its service; the evidence is the check's own error, verbatim.
LIVE_FAILED = 'live:failed'


def _last_line(text: str) -> str:
    lines = [line for line in (text or '').strip().splitlines() if line.strip()]
    return lines[-1].strip() if lines else ''


def live_outcome(returncode: int, stdout: str, stderr: str) -> tuple[bool, str]:
    """(read its service, the line to quote) for one run of a live check script, from its exit status alone.

    A non-zero exit is the propagated exception: its last stderr line, quoted verbatim. Exit 0 is a read only when the
    script printed its result; what it printed is never parsed."""
    if returncode != 0:
        return False, _last_line(stderr) or _last_line(stdout) or f'exited {returncode} and printed nothing'
    if not (stdout or '').strip():
        return False, 'exited 0 but printed nothing, so it recorded no read'
    return True, _last_line(stdout)


def redact(text: str, secrets: list[str]) -> str:
    """`text` with each secret value and any URL `user:password@` removed: evidence is recorded and printed."""
    for secret in sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True):
        text = text.replace(secret, '***')
    return re.sub(r'(//[^/\s:@]+):[^@\s/]+@', r'\1:***@', text)


def _env_file_pairs(doc: dict[str, Any], workdir: Path) -> dict[str, str]:
    """`{NAME: value}` from the .env the harness loads (:func:`_env_file_values`); values never leave this process but to the check."""
    pairs: dict[str, str] = {}
    for m in re.finditer(r'^\s*(?:export\s+)?([A-Za-z_]\w*)\s*=[ \t]*(.*?)[ \t]*$', _env_file_values(doc, workdir) or '', re.MULTILINE):
        value = m.group(2)
        quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in '"\''
        pairs[m.group(1)] = value[1:-1] if quoted else value.split(' #', 1)[0].strip()
    return pairs


def _run_live(check: dict[str, Any], argv: list[str], doc: dict[str, Any], workdir: Path, timeout: int) -> dict[str, Any]:
    """A `kind: live` check: ONE read-only call through a live tool's own read path, made WITH the network (reaching the
    service is the point; a probe's deny keys on `kind: probe` and is untouched). It runs with the environment plus the
    harness .env the run loads, and its exit status decides (`live_outcome`): a read is `verified`; anything else is
    retried once, then `failed` (`live:failed`) quoting the check's own last error line. Declared `credentials` names that
    are unset are recorded as `missing`; no value is ever recorded."""
    env = check_env(check, workdir)
    env |= {name: value for name, value in _env_file_pairs(doc, workdir).items() if not env.get(name)}
    names = [str(n) for n in check.get('credentials') or []]
    secrets = [env[n] for n in names if env.get(n)]
    deadline = min(timeout, LIVE_TIMEOUT_S)
    for attempt in range(2):
        try:
            done = subprocess.run(argv, cwd=str(agent_dir(workdir)), capture_output=True, text=True, timeout=deadline, check=False,
                                  stdin=subprocess.DEVNULL, env=env)
        except subprocess.TimeoutExpired:
            read, line = False, f'no answer within {deadline}s'
        except Exception as exc:  # noqa: BLE001 - the reason is the report
            return {'status': STATUS_NOT_RUN, 'reason': f'could not start: {relative_text(str(exc), workdir)}'}
        else:
            evidence = select_evidence(done.stdout, done.stderr, done.returncode)
            if check['runner'] == 'python' and done.returncode != 0 and (m := _IMPORT_ERROR_RE.match(evidence)):
                return {'status': STATUS_NOT_RUN, 'reason': f"the live check's interpreter ({Path(argv[0]).name}) cannot import {m.group(1)!r}",
                        'evidence': evidence, ATTEMPTED: True, 'cause': f'unimportable:{m.group(1)}'}
            read, line = live_outcome(done.returncode, done.stdout, done.stderr)
        if read or attempt:
            break
        time.sleep(LIVE_RETRY_PAUSE_S)
    evidence = relative_text(redact(line, secrets), workdir)[:400]
    if read:
        return {'status': STATUS_VERIFIED, 'evidence': evidence}
    missing = [n for n in names if not env.get(n)]
    return {'status': STATUS_FAILED, 'evidence': evidence, 'cause': LIVE_FAILED, **({'missing': missing} if missing else {})}


def witness_path(workdir: Path, check_id: str) -> Path:
    return workdir / 'artifacts' / f'.witness_{check_id}.json'


def execute_witnessed(check: dict[str, Any], doc: dict[str, Any], workdir: Path, timeout: int) -> dict[str, Any]:
    """`execute`, plus `witnesses`: per field, the `{state, reason}` THIS run's smoke wrote."""
    # Cleared first: a smoke run without `--witness-out` writes nothing, and an earlier run's file
    # must never stand in for this one's.
    witness_path(workdir, check['id']).unlink(missing_ok=True)
    outcome = execute(check, doc, workdir, timeout)
    if not check.get('witnesses'):
        return outcome
    written = _read_json(witness_path(workdir, check['id']))
    unsymbolled = check['kind'] == 'smoke' and not isinstance(doc['agent'].get('entry_symbol'), dict)
    why = 'agent.entry_symbol is not declared' if unsymbolled else 'the check returned no witness'
    witnesses: dict[str, Any] = {}
    for field in check['witnesses']:
        got = written.get(field) if isinstance(written, dict) else None
        witnesses[field] = got if isinstance(got, dict) else {'state': STATUS_NOT_DETERMINABLE, 'reason': why}
    return {**outcome, 'witnesses': witnesses}


#: Python's own last traceback line for an unimportable module — its grammar, not an agent's.
_IMPORT_ERROR_RE = re.compile(r"^(?:ModuleNotFoundError|ImportError): No module named '([A-Za-z0-9_.]+)'")


def _surface_result(check: dict[str, Any], doc: dict[str, Any], done: subprocess.CompletedProcess[str], evidence: str, workdir: Path) -> dict[str, Any]:
    """A surface script prints the agent's LIVE surface as a JSON array; we compare it to the
    declaration. This is 9f.4 generalised: the same "declared == bound" question the tool lane
    asks, for a bench whose surface is render verbs and whose `tools:` is empty."""
    if done.returncode != 0:
        return {'status': STATUS_FAILED, 'evidence': evidence, 'exit_code': done.returncode}
    expected = resolve_field(doc, str((check.get('expect') or {}).get('equals', '')))
    try:
        actual = json.loads(done.stdout)
    except Exception:
        return {'status': STATUS_FAILED, 'evidence': f'the script printed no JSON array on stdout: {evidence}'}
    if not isinstance(actual, list) or not isinstance(expected, list):
        return {'status': STATUS_FAILED, 'evidence': f'expected a list on both sides; declared={type(expected).__name__} live={type(actual).__name__}'}
    if not expected and not actual:
        return {'status': STATUS_NOT_RUN, 'reason': 'both the declaration and the live surface are empty, so this check compared nothing'}
    missing = sorted(set(map(str, expected)) - set(map(str, actual)))
    extra = sorted(set(map(str, actual)) - set(map(str, expected)))
    if missing or extra:
        return {
            'status': STATUS_FAILED,
            'evidence': f'declared but not live: {missing or "none"}; live but not declared: {extra or "none"}',
        }
    # Declared == live is vacuous when the declaration was narrowed: a live TOOL set is also held to the production set.
    if str((check.get('expect') or {}).get('equals', '')).startswith('surfaces.tools') and (dropped := dropped_tools(doc, set(map(str, actual)), workdir=workdir)):
        return {
            'status': STATUS_FAILED,
            'evidence': f'production_tool_dropped: {len(dropped)} production tool(s) are not live and no operator override names their switch: '
                        f'{", ".join(dropped[:12])}{" …" if len(dropped) > 12 else ""}',
        }
    return {'status': STATUS_VERIFIED, 'evidence': f'{len(actual)} surface item(s) match the declaration'}


def read_denials(workdir: Path) -> list[dict[str, Any]]:
    """Tool calls the permission mode denied during this scan, as `denial_watch` recorded them.

    A denied publish and a denied verifier both used to vanish into "fix it yourself, silently" —
    which is right for the flow and wrong for the report: it is the operator's own permission
    mode, and only they can change it.
    """
    candidates = [workdir / '.denials.jsonl']
    # The window is session-keyed when Claude Code exports a session id, so the log
    # `denial_watch` writes BESIDE it is under $HOME, not under the bench. Both are read: which
    # one exists is a property of how the session was launched, not of this bench.
    try:
        sys.path.insert(0, str(plugin_root() / 'hooks'))
        from _workdir import flow_dir  # noqa: PLC0415 - hooks/ is only on sys.path here

        if (flow := flow_dir()) is not None:
            candidates.append(flow / '.denials.jsonl')
    except Exception:
        pass
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in candidates:
        if not path.is_file():
            continue
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip() or line in seen:
                continue
            seen.add(line)
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


# --------------------------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------------------------


def witness_verdicts(doc: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Per witnessed field, the `{state, reason}` of the PASSING checks that witness it: any
    `witnessed`, else any `not_witnessed`, else `not_determinable`. A field no passing check
    witnesses is absent, and `field_status` falls back to the covering checks' own statuses."""
    witnesses_of = {c['id']: c.get('witnesses') or [] for c in doc['checks']}
    rank = {WITNESSED: 0, NOT_WITNESSED: 1}
    out: dict[str, dict[str, str]] = {}
    for result in results:
        if result['status'] not in _PASSING:
            continue
        for field in witnesses_of.get(result['id'], []):
            got = (result.get('witnesses') or {}).get(field) or {'state': STATUS_NOT_DETERMINABLE, 'reason': 'the check returned no witness'}
            if field not in out or rank.get(str(got.get('state')), 2) < rank.get(str(out[field].get('state')), 2):
                out[field] = got
    return out


def field_status(doc: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, str]:
    """Each field's ONE state. `declared_only` when no check names it — the state that makes the
    others honest, because a field nothing checks used to look exactly like a field that passed."""
    by_id = {r['id']: r for r in results}
    out: dict[str, str] = {}
    witnessed = witness_verdicts(doc, results)
    for field, ids in coverage(doc).items():
        if field in witnessed:
            out[field] = {WITNESSED: STATUS_VERIFIED, NOT_WITNESSED: STATUS_FAILED}.get(str(witnessed[field].get('state')), STATUS_NOT_DETERMINABLE)
            continue
        # A failure the run measures (the agent's raise, a silent turn) says nothing about the fields a check verifies.
        statuses = [STATUS_NOT_DETERMINABLE if _run_measured(by_id[i]) else by_id[i]['status']
                    for i in ids if i in by_id]
        if not statuses:
            out[field] = STATUS_DECLARED_ONLY
        elif STATUS_FAILED in statuses:
            # A witness-only field fails only on a `not_witnessed` witness; a failed check witnessed nothing.
            out[field] = STATUS_NOT_DETERMINABLE if field in WITNESS_ONLY_FIELDS else STATUS_FAILED
        elif STATUS_NOT_DETERMINABLE in statuses:
            out[field] = STATUS_NOT_DETERMINABLE
        elif STATUS_NOT_VERIFIED in statuses:
            out[field] = STATUS_NOT_VERIFIED
        elif STATUS_NOT_RUN in statuses:
            # A blocking check that could NOT RUN outranks a sibling that passed. The other order
            # reported `harness: verified` on a mockless bench whose only live gate never executed
            # — and `fields` is the half a PROGRAM reads (the suite's Step 0), where the integrity
            # line's honesty does not reach.
            out[field] = STATUS_NOT_RUN
        elif STATUS_STALE in statuses:
            # A covering check passed EARLIER and its inputs moved since: the field is not shown
            # verified now, and nothing was found wrong with it either. Below `not_run` for the same
            # reason `not_run` outranks a sibling's pass; above `verified`, or a stale smoke would hide
            # behind a static verifier that re-ran.
            out[field] = STATUS_STALE
        elif any(s in _PASSING for s in statuses):
            out[field] = STATUS_VERIFIED
        else:
            out[field] = STATUS_NOT_APPLICABLE
    return out


#: C10: what each mocked tool's witness says, as the suite's start gate reads it. `failed`: a test charged to it failed,
#: or its test file did not collect. `unwitnessed`: the gate ran and no test exercises it (no test calls it; nothing
#: mocked reads its write) — the one status a waiver lifts. `no_verdict`: the gate reached none over it (the check did
#: not pass, its witness test did not run, every caller failed on another tool) — never waived.
MOCK_TOOL_STATUS = {WITNESSED: 'witnessed', NOT_WITNESSED: 'failed'}
MOCK_UNWITNESSED, MOCK_NO_VERDICT = 'unwitnessed', 'no_verdict'
MOCK_TOOL_PROCEEDS = ('witnessed', 'waived')
#: Who owns closing a waived tool: the generated mock, or the user's own call. Never the harness: it cannot waive the
#: gate that tests its own mock.
MOCK_WAIVER_OWNERS = ('backend', 'operator')
#: Why no test can witness a tool: nothing reads what it writes, only it reads it back, or it writes the agent's own
#: bookkeeping no answer depends on.
MOCK_WAIVER_BECAUSE = ('no_reader', 'self_reader', 'internal_bookkeeping')
MOCK_WAIVERS_FILENAME = 'mock_waivers.json'
MOCK_GATE_LOG = 'mock_gate.jsonl'
#: Why a world-served real tool no mock serves sits in the gate unwitnessed: the retired route serves it from a storage
#: class the harness wrote, which no contract or witness test reaches. Never waived; re-routing its store to a seam mock
#: with `serves:` (`verify_config` `world_served_without_mock`) is what clears it.
HARNESS_SERVED_REASON = 'served by harness-authored storage; no test reached it'


def harness_served_rows(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """C10 rows for the `mode: real` tools served by harness-authored storage (`writer_readers.world_served_without_mock`):
    each `unwitnessed`, so a legacy config reads WARN instead of a vacuous READY."""
    return {tool: {'status': MOCK_UNWITNESSED, 'reason': HARNESS_SERVED_REASON, 'harness_served': True}
            for tool in writer_readers.world_served_without_mock(config)}


def _env_id(workdir: Path) -> str:
    path = workdir / '.env_id'
    return path.read_text(encoding='utf-8').strip() if path.is_file() else ''


def read_mock_waivers(workdir: Path) -> dict[str, dict[str, Any]]:
    """`artifacts/mock_waivers.json` `waivers`: tool -> {because, cite, owner, env_id, at}. Internal: no customer artifact reads it."""
    data = _read_json(workdir / 'artifacts' / MOCK_WAIVERS_FILENAME)
    waivers = data.get('waivers') if isinstance(data, dict) else None
    return {str(k): v for k, v in waivers.items() if isinstance(v, dict)} if isinstance(waivers, dict) else {}


def _mock_status(witness: dict[str, Any]) -> str:
    state = str(witness.get('state'))
    if state in MOCK_TOOL_STATUS:
        return MOCK_TOOL_STATUS[state]
    return MOCK_UNWITNESSED if witness.get('cause') == 'untested' else MOCK_NO_VERDICT


def _declared_readers(config: dict[str, Any], tool: str) -> list[str]:
    """The other tools `tool_graph` declares reading what `tool` writes, live readers included."""
    graph = config.get('tool_graph') if isinstance(config, dict) else None
    return sorted({str(e['target']) for e in graph if isinstance(e, dict) and e.get('source') == tool and isinstance(e.get('target'), str)
                   and e['target'] != tool}) if isinstance(graph, list) else []


def waiver_refusal(config: dict[str, Any], tool: str, status: str, waiver: dict[str, Any]) -> str | None:
    """Why `waiver` does not lift `tool` (C10), else None."""
    if status != MOCK_UNWITNESSED:
        return f'a {status} tool is never waived; only an unwitnessed one (no test exercises it) is'
    if tool in writer_readers.world_served_without_mock(config):
        return f'it is {HARNESS_SERVED_REASON}; no waiver lifts it — put its store under a seam mock that names it in `serves:`'
    if waiver.get('because') not in MOCK_WAIVER_BECAUSE:
        return f'`because` is none of {", ".join(MOCK_WAIVER_BECAUSE)}'
    if waiver.get('owner') not in MOCK_WAIVER_OWNERS:
        return f'`owner` is none of {", ".join(MOCK_WAIVER_OWNERS)}'
    if len(str(waiver.get('cite') or '').strip()) < gate_ledger.OVERRULE_REASON_MIN_CHARS:
        return f'no `cite` of at least {gate_ledger.OVERRULE_REASON_MIN_CHARS} characters naming what shows it'
    if waiver['because'] in ('no_reader', 'self_reader') and (readers := _declared_readers(config, tool)):
        return f'`tool_graph` declares {", ".join(readers)} reading what it writes'
    return None


def mock_tools(doc: dict[str, Any], results: list[dict[str, Any]], workdir: Path) -> dict[str, dict[str, Any]]:
    """C10 `verification.mock_tools`: per mocked tool `{status, reason, test?, read_back?: {reader: probe status}, waiver? |
    waiver_refused?}` from the recorded
    `mock_contract` result and the waivers written for THIS world (`.env_id`). A check that did not pass leaves every tool
    `no_verdict`."""
    mocked = [str(t['name']) for t in doc['surfaces']['tools'] if t.get('mode') != 'real']
    result = next((r for r in results if r.get('id') == 'mock_contract'), None)
    passed = result is not None and result.get('status') in _PASSING
    witnesses = (result or {}).get('witnesses') or {} if passed else {}
    why = 'mock_contract did not run' if result is None else str(result.get('evidence') or result.get('reason') or result.get('status'))
    waivers, env_id = read_mock_waivers(workdir), _env_id(workdir)
    config = _load_yaml(workdir / 'plurai.config.yaml') or {}
    unread = unread_writes(workdir)
    store_splits = {writer: (store, real) for writer, store, real in writer_readers.store_splits(config)}
    probed = real_reader_pairs(workdir, env_id)
    shapes = mock_shape_probe.verdicts(workdir, env_id)
    no_read = None if shapes else mock_shape_probe.unread(workdir, env_id)
    out: dict[str, dict[str, Any]] = {}
    for tool in mocked:
        witness = witnesses.get(f'surfaces.tools[{tool}]') if passed else None
        witness = witness if isinstance(witness, dict) else {}
        entry: dict[str, Any] = {'status': _mock_status(witness), 'reason': str(witness.get('reason') or why)}
        if witness.get('test'):
            entry['test'] = witness['test']
        if tool in store_splits:
            # A write-only mock of a store the agent reads for real, whatever `tool_graph` says: no waiver lifts it.
            store, real = store_splits[tool]
            entry.update(status='failed', split=real or [f'seam: {writer_readers.seam(config["tools"][tool])}'],
                         reason=f'writer_reader_mode_split: it writes `{store}`, which ' + (f'{", ".join(real)} read for real' if real else
                                'no mock-world tool reads, while its seam stands in for a store the agent reads through'))
        elif tool in unread:
            # No mocked read tool reads what it writes (env-gen's `unread_writes`): a real reader declared over those types is
            # the topology error no waiver lifts; with neither a real reader nor `target: null`, nothing can witness the write.
            if real := writer_readers.real_readers(config, tool, unread[tool]):
                entry.update(status='failed', split=real, reason=f'writer_reader_mode_split: it writes {", ".join(unread[tool])}, which no mocked tool '
                                                                f'reads and the real tool(s) {", ".join(real)} read')
            elif not writer_readers.declared_write_only(config, tool) and entry['status'] == 'witnessed':
                # A passing test that called, after it, another mock a declared reader of one of those types reads through:
                # the read-back at the seam.
                after = witness.get('read_after') if isinstance(witness.get('read_after'), dict) else {}
                if seams := sorted(set(after) & writer_readers.seam_readers(config, tool, unread[tool])):
                    entry.update(reason=f'{after[seams[0]]} called `{seams[0]}` after it and passed; `{seams[0]}` is the seam a '
                                        f'declared reader of its {", ".join(unread[tool])} write reads through')
                else:
                    remedy = (f'`tool_graph` declares {", ".join(readers)} reading it, so the world must type that read: route it to /edit-env'
                              if (readers := _declared_readers(config, tool)) else 'declare its readers on `tool_graph`, or `target: null`')
                    entry.update(status=MOCK_UNWITNESSED, reason=f'no mocked read tool reads what it writes ({", ".join(unread[tool])}), so no '
                                                                 f'read-your-writes test witnesses it: {remedy}')
        pairs = probed.get(tool, [])
        if pairs and all(p.get('status') == real_reader_probe.WAIVED for p in pairs) and entry['status'] == 'witnessed':
            # A pair's waiver covers that pair only (C10): a writer with no probed pair is undecided, never waived whole.
            entry.update(status=MOCK_UNWITNESSED, reason='every real-reader pair is waived `no_reader`; probe at least one through a reader that shows the write')
        for pair in pairs:
            # The real-reader probe: the write read back through the agent's real reader inside the harness. A probe the
            # world owes and that reached no verdict is `no_verdict` (C10): never waived, never a pass.
            if pair.get('status') == real_reader_probe.WAIVED:
                entry.setdefault('waived_pairs', []).append({'read_tool': pair.get('read_tool'), **pair['waiver']})
                continue
            # What the customer may be told about this reader seeing the write: the probe's verdict, never the declaration
            # (`release_verdict.mock_gap` says a reader not witnessed as a read-back gap). A reader's worst pair stands.
            read_back = entry.setdefault('read_back', {})
            if read_back.get(str(pair.get('read_tool')), real_reader_probe.WITNESSED) == real_reader_probe.WITNESSED:
                read_back[str(pair.get('read_tool'))] = str(pair.get('status'))
            if pair.get('status') == real_reader_probe.FAILED:
                entry.update(status='failed', split=sorted({*entry.get('split', []), str(pair.get('read_tool'))}),
                             reason=f'writer_reader_mode_split (real_reader_probe): {pair.get("reason")}')
            elif pair.get('status') != real_reader_probe.WITNESSED and entry['status'] == 'witnessed':
                entry.update(status=MOCK_NO_VERDICT, reason=f'real_reader_probe reached no verdict on {pair.get("read_tool")}: {pair.get("reason")}')
        if shape := shapes.get(tool) or no_read:
            # The mock's answers read against the agent's own types. A mismatch is a mock defect the gate names; unverified says only that.
            entry['shape'] = shape
            if shape.get('status') == mock_shape_probe.MISMATCH:
                said = mock_shape_probe.mismatch_reason(tool, shape)
                entry.update(status='failed', reason=said if entry['status'] == 'witnessed' else f'{entry["reason"]}; {said}')
                entry.setdefault('test', shape.get('test'))
        waiver = waivers.get(tool)
        if entry['status'] != 'witnessed' and waiver and waiver.get('env_id') == env_id:
            if refused := waiver_refusal(config, tool, entry['status'], waiver):
                entry['waiver_refused'] = refused
            else:
                entry.update(status='waived', waiver={k: waiver.get(k) for k in ('because', 'cite', 'owner')})
        out[tool] = entry
    return {**out, **{t: row for t, row in harness_served_rows(config).items() if t not in out}}


def census_of(workdir: Path, tools: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """`verification.coverage_census`: the bench's contract units per disposition (`coverage_census`), with `tools` the
    C10 record it reads waivers from. Report-only: no gate, verdict or exit code reads it."""
    config = _load_yaml(workdir / 'plurai.config.yaml')
    return coverage_census.census(config if isinstance(config, dict) else {}, workdir / 'test_mock_contract.py', workdir / MOCK_WITNESS_DIR, tools)


def _outside_gate(workdir: Path) -> str:
    """The world-served real tools C10 does not count (it reads `mode != real`, plus the harness-served rows), said beside
    its own count; '' for none."""
    config = _load_yaml(workdir / 'plurai.config.yaml')
    config = config if isinstance(config, dict) else {}
    served = [t for t in coverage_census.served_tools(config)[1] if t not in harness_served_rows(config)]
    return f'; {len(served)} world-served real tool(s) outside the gate' if served else ''


def real_reader_pairs(workdir: Path, env_id: str) -> dict[str, list[dict[str, Any]]]:
    """Writer -> its `drive: real_reader` pairs' verdicts from `real_reader_probe.py`, for THIS world. A pair the world
    owes and no probe of this world decided is `no_verdict`: the probe did not run, never a pass."""
    recorded = real_reader_probe.read_result(workdir)
    verdicts = {str(p.get('test')): p for p in (recorded or {}).get('pairs') or [] if isinstance(p, dict)} if (recorded or {}).get('env_id') == env_id else {}
    out: dict[str, list[dict[str, Any]]] = {}
    for pair in real_reader_probe.probe_pairs(workdir):
        # A pair the world ships waived (`no_reader`, cited) is decided by the manifest itself, probe or no probe.
        verdict = (real_reader_probe.probe(pair, None, None, None) if real_reader_probe.pair_waiver(pair) else verdicts.get(str(pair.get('test')))) or {
            'read_tool': pair.get('read_tool'), 'status': real_reader_probe.NO_VERDICT, 'reason': 'real_reader_probe.py has not run on this world'}
        out.setdefault(str(pair.get('write_tool')), []).append(verdict)
    return out


def unread_writes(workdir: Path) -> dict[str, list[str]]:
    """Env-gen's `unread_writes` (the world's `mock_witness.json`): mocked write tool -> the entity types it writes that no
    mocked read tool reads."""
    data = _read_json(workdir / MOCK_WITNESS_DIR / 'mock_witness.json')
    rows = data.get('unread_writes') if isinstance(data, dict) and data.get('schema') == 'plurai-mock-witness/1' else None
    return {str(r['write_tool']): [str(t) for t in r.get('entity_types') or []] for r in rows or [] if isinstance(r, dict) and r.get('write_tool')}


#: `mock-gate` states, by `mock_gate`'s code. The gate WARNS and never blocks: the command exits 0 in every state and
#: prints `MOCK_GATE=<state>`; a tool it did not witness is a named risk on the release verdict (internal), never a stop.
MOCK_GATE_STATES = {0: 'READY', 1: 'WARN', 3: 'NOT RECORDED'}


def mock_gate(workdir: Path) -> tuple[int, dict[str, dict[str, Any]], str]:
    """(state code, C10 mock_tools, why) — is every mocked tool of this world witnessed or waived? From the RECORDED
    `mock_contract` result, which must have run over the mock as it is now (its signature), plus the waivers for this world."""
    doc = _read_json(manifest_path(workdir))
    record = _read_json(verification_path(workdir))
    if not isinstance(doc, dict) or not isinstance(record, dict) or record.get('schema') != VERIFICATION_SCHEMA_ID:
        return 3, {}, f'no readable {MANIFEST_FILENAME} and {VERIFICATION_FILENAME}'
    check = next((c for c in doc.get('checks', []) if isinstance(c, dict) and c.get('id') == 'mock_contract' and c.get('runner') == 'builtin'), None)
    if check is None:
        config = _load_yaml(workdir / 'plurai.config.yaml')
        if rows := harness_served_rows(config if isinstance(config, dict) else {}):
            return 1, rows, (f'{len(rows)} world-served real tool(s) {HARNESS_SERVED_REASON} (a named risk; the build proceeds)'
                             + _outside_gate(workdir))
        return 0, {}, 'nothing on this bench is mocked' + _outside_gate(workdir)
    results = [r for r in record.get('results', []) if isinstance(r, dict)]
    result = next((r for r in results if r.get('id') == 'mock_contract'), None)
    if result is None or result.get('signature') != check_signature(check, workdir, doc):
        return 3, {}, 'no mock_contract result for the mock as it stands — run `verify --mode reuse --only mock_contract` first'
    tools = mock_tools(doc, results, workdir)
    held = [tool for tool, entry in tools.items() if entry['status'] not in MOCK_TOOL_PROCEEDS]
    unread = sorted(tool for tool, entry in tools.items() if (entry.get('shape') or {}).get('status') == mock_shape_probe.UNVERIFIED)
    shape_note = f'; answers not read against the agent\'s types, so unverified: {", ".join(unread)}' if unread else ''
    if held:
        harness = sum(1 for entry in tools.values() if entry.get('harness_served'))
        return 1, tools, (f'{len(held)} of {len(tools)} mocked tool(s) neither witnessed nor waived (a named risk; the build proceeds)'
                          + (f', {harness} of them world-served real tool(s) {HARNESS_SERVED_REASON}' if harness else '')
                          + _outside_gate(workdir) + shape_note)
    waived = sorted(tool for tool, entry in tools.items() if entry['status'] == 'waived')
    return 0, tools, (f'{len(tools) - len(waived)} of {len(tools)} mocked tool(s) witnessed' + (f', waived: {", ".join(waived)}' if waived else '')
                      + _outside_gate(workdir) + shape_note)


def cmd_mock_gate(args: argparse.Namespace) -> int:
    """READY / WARN / NOT RECORDED, printed as `MOCK_GATE=<state>` and logged to `artifacts/mock_gate.jsonl`; exit 0 in
    every state (2 only for a refused `--waive`). With `--waive`, first records a waiver for one tool in this world."""
    workdir = resolve_workdir(args.workdir)
    if args.waive:
        waiver = {'because': args.because, 'cite': (args.cite or '').strip(), 'owner': args.owner, 'env_id': _env_id(workdir), 'at': _now()}
        entry = mock_gate(workdir)[1].get(args.waive)
        refused = 'it is not a mocked tool with a recorded mock_contract verdict in this world' if entry is None else \
            waiver_refusal(_load_yaml(workdir / 'plurai.config.yaml') or {}, args.waive, str(entry['status']), waiver)
        if refused:
            print(f'ERROR: --waive {args.waive} refused: {refused}. Nothing was written.', file=sys.stderr)
            return 2
        waivers = read_mock_waivers(workdir)
        waivers[args.waive] = waiver
        _write_json(workdir / 'artifacts' / MOCK_WAIVERS_FILENAME, {'schema': 'plurai-mock-waivers/1', 'waivers': waivers})
    code, tools, why = mock_gate(workdir)
    print(f'mock-gate: {MOCK_GATE_STATES[code]} — {why}')
    for tool, entry in tools.items():
        if entry['status'] not in MOCK_TOOL_PROCEEDS:
            print(f'mock-gate: WARN — {tool}: {entry["status"]} — {entry["reason"]}' + (f' [{entry["test"]}]' if entry.get('test') else '')
                  + (' (no waiver lifts it; a coverage gap — never replace a granted real reader with a mock)' if entry.get('split') else '')
                  + (f' (waiver refused: {entry["waiver_refused"]})' if entry.get('waiver_refused') else ''))
    record = _read_json(verification_path(workdir))
    if tools and isinstance(record, dict):
        # A tool the gate downgrades reads the same state in `fields`, which the raw witnesses wrote.
        fields = dict(record.get('fields') or {}) | {f'surfaces.tools[{tool}]': entry['status'] for tool, entry in tools.items()
                                                       if entry['status'] in ('failed', MOCK_UNWITNESSED)}
        # The census reads waivers off the same record; the integrity line keeps the counts `verify` rendered, as it keeps
        # the `fields` it rendered.
        _write_json(verification_path(workdir), {**record, 'mock_tools': tools, 'fields': fields, 'coverage_census': census_of(workdir, tools)})
    log = workdir / 'artifacts' / MOCK_GATE_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps({'at': _now(), 'env_id': _env_id(workdir), 'state': MOCK_GATE_STATES[code].lower().replace(' ', '_'), 'why': why,
                                 'mock_tools': tools}, sort_keys=True) + '\n')
    print(f'MOCK_GATE={MOCK_GATE_STATES[code].replace(" ", "_")}')
    return 0


def cmd_local_parity(args: argparse.Namespace) -> int:
    """The local-served world's parity, from the contract tier's exit code and its log: PROVEN clears the record, UNPROVEN
    writes `artifacts/local_world_parity.json` (a named risk on the release verdict, internal). Exit 0 either way — a
    parity gap is a risk, never a stop."""
    import release_verdict as rv  # noqa: PLC0415 - the risk's one reader owns its shape

    workdir = resolve_workdir(args.workdir)
    path = workdir / 'artifacts' / rv.PARITY_FILENAME
    if args.exit_code == 0:
        path.unlink(missing_ok=True)
        print('LOCAL_PARITY=PROVEN')
        return 0
    try:
        log = Path(args.log).read_text(encoding='utf-8', errors='replace')
    except OSError:
        log = ''
    record = rv.parity_record(args.exit_code, log)
    _write_json(path, {**record, 'at': _now(), 'env_id': _env_id(workdir)})
    print(f'local-parity: UNPROVEN — exit {args.exit_code}: ' + ('; '.join(record['errors']) or 'no error line'))
    print('LOCAL_PARITY=UNPROVEN')
    return 0


#: The customer's words for the builtin checks (`_default_checks`). Any other id is said as itself.
CHECK_WORDS = MappingProxyType({
    'verify_config': 'the config check',
    'verify_simulate': 'the harness check',
    'lint_prd': 'the PRD check',
    'verify_prd_appendix': 'the PRD appendix check',
    'smoke_init': 'the agent start-up check',
    'smoke_turn': 'the two-turn agent check',
    'smoke_pipeline': 'the pipeline run check',
    'scan_contract': 'the source contract check',
    'mock_contract': 'the mocked-tools check',
    'verify_bridge': 'the tool bridge check',
    'seed_readback': 'the seeded-world read-back check',
})


def _check_words(check_id: str) -> str:
    return CHECK_WORDS.get(check_id, check_id)


def _counted(fields: list[str]) -> str:
    """`3 tools` / `1 field` / `2 tools and 1 other field`: a count, never the selectors (they stay in `fields`)."""
    tools = sum(1 for f in fields if f.startswith('surfaces.tools['))
    others = len(fields) - tools
    said = [f'{tools} tool{"s" if tools != 1 else ""}'] if tools else []
    if others:
        said.append(f'{others} {"other " if tools else ""}field{"s" if others != 1 else ""}')
    return ' and '.join(said)


def integrity_line(doc: dict[str, Any], results: list[dict[str, Any]], denials: list[dict[str, Any]], toolchain: HostToolchain, *,
                   census: Mapping[str, Any] | None) -> str:
    """The ONE sentence the hand-off must carry, rendered from the results rather than recalled.

    Doctrine item 4's shape: a flat statement of what this run could execute, no cause, no repair
    story. `report_guard` requires it byte-equal, so it is a lookup and not a judgement about
    prose — which is what keeps that guard off its historical false-positive ground. `census` is the
    record `coverage_census.census` wrote for this run (None: none was taken); its counts are one clause.
    """
    # The mocked tools are checked once the suite builds the mock: deferred by design, so outside the count.
    deferred = [r for r in results if r['status'] == STATUS_NOT_RUN and r.get('reason') == MOCK_UNBUILT and _kind_of(doc, r['id']) == 'mock']
    counted = [r for r in results if r not in deferred]
    total = len(counted)
    executed = [r for r in results if r['status'] in (STATUS_VERIFIED, STATUS_FAILED)]
    reused = [r for r in results if r['status'] == STATUS_REUSED]
    stale = [r['id'] for r in results if r['status'] == STATUS_STALE]
    # `reused` is counted SEPARATELY from `executed`. Folding it in read as "everything ran" over a
    # run that executed nothing new — which is the sentence this line exists to stop anyone writing.
    parts = [f'Run integrity — checks {len(executed)} of {total} executed'
             + (f', {len(reused)} reused' if reused else '')
             + (f', {len(stale)} stale' if stale else '')]
    blocked = [r for r in counted if r['status'] == STATUS_NOT_RUN]
    if blocked:
        # One clause per reason, naming every check it held: a reason shared by three checks is said once.
        by_reason: dict[str, list[str]] = {}
        for r in blocked:
            by_reason.setdefault(str(r.get('reason', 'not run')), []).append(_check_words(r['id']))
        parts[0] += ' (' + '; '.join(f'{", ".join(names)}: {reason}' for reason, names in by_reason.items()) + ')'
    if deferred:
        parts.append('the mocked tools are checked when the suite builds the mock')
    probes = [r for r in results if _kind_of(doc, r['id']) == 'probe']
    if probes:
        n = {st: sum(1 for r in probes if r['status'] == st) for st in (STATUS_VERIFIED, STATUS_REUSED, STATUS_FAILED, STATUS_STALE)}
        failed_part = f'{n[STATUS_FAILED]} failed, ' if n[STATUS_FAILED] else ''
        stale_part = f'{n[STATUS_STALE]} stale, ' if n[STATUS_STALE] else ''
        parts.append(f'probes: {n[STATUS_VERIFIED]} run, {n[STATUS_REUSED]} reused, {failed_part}{stale_part}{len(probes) - sum(n.values())} not run')
    smoke = {r['id']: r['status'] for r in results if _kind_of(doc, r['id']) == 'smoke'}
    if smoke:
        turn, pipe = smoke.get('smoke_turn'), smoke.get('smoke_pipeline')
        why = f'{_check_words("smoke_pipeline")} {pipe}' if pipe not in (None, STATUS_NOT_APPLICABLE) else (turn or 'no turn check declared')
        if turn in _PASSING or pipe in _PASSING:
            parts.append('smoke: 2-turn' if turn in _PASSING else 'smoke: pipeline')
        elif STATUS_STALE in (turn, pipe):
            # A smoke is paid turns, so a partial verify never re-runs one and nothing re-marks it after a config rewrite.
            parts.append('smoke: stale (it passed earlier; its inputs changed since and it was not re-run)')
        else:
            parts.append(f'smoke: init only ({why})')
    failed = [r['id'] for r in results if r['status'] == STATUS_FAILED]
    if failed:
        parts.append('FAILED: ' + ', '.join(map(_check_words, failed)))
    # An open lint is counted by what it found; the bare id would say only that something was refused.
    open_lint = [r for r in results if r['status'] == STATUS_NOT_VERIFIED and r['id'] in OPEN_FINDINGS_EXITS]
    refused = [r['id'] for r in results if r['status'] == STATUS_NOT_VERIFIED and r not in open_lint]
    if refused:
        parts.append('NOT VERIFIED: ' + ', '.join(map(_check_words, refused)))
    for r in open_lint:
        parts.append(f'PRD lint: {r["open_findings"]} open' if isinstance(r.get('open_findings'), int) else 'PRD lint: findings open')
    if stale:
        parts.append('STALE: ' + ', '.join(map(_check_words, stale)))
    only = [f for f, s in field_status(doc, results).items() if s == STATUS_DECLARED_ONLY]
    # Counts only: the selectors and their reasons stay in verification.json's `results` and `fields`.
    if only:
        parts.append(f'typed from the code only: {_counted(only)}')
    verdicts = witness_verdicts(doc, results)
    if unwitnessed := [f for f, v in verdicts.items() if v.get('state') == NOT_WITNESSED]:
        parts.append(f'not confirmed by their checks: {_counted(unwitnessed)}')
    if blind := [f for f, v in verdicts.items() if v.get('state') not in (WITNESSED, NOT_WITNESSED)]:
        parts.append(f'could not be checked: {_counted(blind)}')
    if coverage := coverage_census.clause(census):
        parts.append(coverage)
    if denials:
        parts.append(f'{len(denials)} tool call(s) denied by the permission mode')
    if gaps := toolchain.gaps():
        parts.append('host toolchain: ' + ', '.join(f'{r.get("name")} {_status_word(r)}' for r in gaps))
    # No run-readiness verdict here: this line is committed and quoted to the customer, and whether a run
    # may start is the run gate's decision, which a plugin overrule can change. The facts above stay.
    return ' · '.join(parts) + '.'


class HostToolchain(NamedTuple):
    """What this machine gives the agent of what its own files declare it needs: the SDK resolver's one
    record per requirement (`status`: present / missing / fails_sandboxed / broken / unverified / not_checked,
    with `detail` and the resolver's `remedy`), or why the resolver could not run at all (`not_checked`, and
    then every requirement reads not_checked). Only `present` is not a gap — and not even that from a resolver
    older than `unverified`, whose `present` row with `usable: unverified` declared no probe."""

    requirements: list[dict[str, Any]]
    not_checked: str
    #: The resolver's structured install plan for what is missing (SDK `plurai.host_toolchain.install_plan`: `head`,
    #: `interpreters`, `lines`, `uncited`); empty for none, or from a resolver that writes none.
    install: Mapping[str, Any] = MappingProxyType({})

    def gaps(self) -> list[dict[str, Any]]:
        return [r for r in self.requirements if r.get('status') != 'present' or r.get('usable') == 'unverified']


#: A bench that declares nothing, or a machine that gives the agent everything it declares.
NO_HOST_GAP = HostToolchain([], '')


def host_toolchain(doc: dict[str, Any], workdir: Path) -> HostToolchain:
    """Live, never recorded: a fact about the machine the run is on, so installing a tool clears it without a
    re-scan. ONE resolver, the SDK's (`python -m plurai.host_toolchain`), run in the agent's interpreter: each
    declared probe runs where the agent runs the program — its CLI's confined Bash, or its own process."""
    wanted = [r for r in (doc.get('harness') or {}).get('host_requirements') or [] if isinstance(r, dict)]
    if not wanted:
        return NO_HOST_GAP
    argv = [_child_python(), '-m', 'plurai.host_toolchain', '--workdir', str(workdir), '--json']
    try:
        done = subprocess.run(argv, cwd=agent_dir(workdir), capture_output=True, text=True, timeout=900)  # the resolver exits 1 on a gap
    except (OSError, subprocess.TimeoutExpired) as exc:
        why = f'{type(exc).__name__}: {exc}'
    else:
        try:
            found = json.loads(done.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            found = None
        if isinstance(found, dict) and isinstance(rows := found.get('requirements'), list) and all(isinstance(r, dict) for r in rows):
            install = found.get('install')
            return HostToolchain(rows, '', install if isinstance(install, dict) else {})
        why = select_evidence(done.stdout, done.stderr, done.returncode)
        if "No module named 'plurai.host_toolchain'" in done.stderr or 'No module named plurai.host_toolchain' in done.stderr:
            why = "the agent's plurai predates the host toolchain check — upgrade plurai in the agent's venv"
    return HostToolchain([{'name': r.get('name'), 'kind': r.get('kind'), 'status': 'not_checked', 'detail': why} for r in wanted], why)


def _host_status(req: dict[str, Any]) -> str:
    """The resolver's status for one requirement; an older resolver's `present` + `usable: unverified` is `unverified`."""
    return 'unverified' if req.get('usable') == 'unverified' else str(req.get('status'))


def _status_word(req: dict[str, Any]) -> str:
    return _host_status(req).replace('_', ' ')


#: The resolver statuses that measured nothing: no probe confirmed the requirement, or none could run.
#: Such a gap ships with a finding; every other gap was measured and holds the run.
HOST_UNMEASURED = ('unverified', 'not_checked')


def host_gap_line(req: dict[str, Any]) -> str:
    """One requirement this machine does not give the agent, with the resolver's detail and, as the resolver wrote
    it, its remedy (an SDK that writes none gets none: the remedy is the resolver's, never this script's)."""
    remedy = f' — {req["remedy"]}' if req.get('remedy') else ''
    return f'{req.get("name")} ({req.get("kind")}) {_status_word(req)} — {req.get("detail") or "no detail"}{remedy}'


class GateReason(NamedTuple):
    """One run-gate finding (`scripts/gate_ledger.py`) and the NOT RUNNABLE line it prints while it holds."""

    finding: gate_ledger.GateFinding
    reason: str


def host_toolchain_gate(toolchain: HostToolchain) -> list[GateReason]:
    """One finding per requirement this machine does not give the agent: `plugin.run_gate.host_gap`, holding, for
    one measured unusable where the agent runs it; `plugin.run_gate.host_unverified`, which holds nothing, for one
    nothing measured (:data:`HOST_UNMEASURED`). The plugin lifts a hold by overruling its finding in the ledger,
    never by a line relayed to the customer."""
    out = []
    for r in toolchain.gaps():
        name, status = str(r.get('name')), _host_status(r)
        gate_id = gate_ledger.RUN_GATE_HOST_UNVERIFIED if status in HOST_UNMEASURED else gate_ledger.RUN_GATE_HOST_GAP
        finding = gate_ledger.make_finding(
            gate_id,
            subject=name,
            evidence={'requirement': name, 'status': status},
            observed={'kind': r.get('kind'), 'detail': r.get('detail')},
            # the line names the requirement, so a finding that holds nothing still says which one it is
            remedy=host_gap_line(r),
            draft_ref=None,
        )
        out.append(GateReason(finding, f'host toolchain: {host_gap_line(r)}'))
    return out


def host_declaration_gate(findings: list[str]) -> list[GateReason]:
    """An invalid host declaration runs no probe, so no host fact is measured: one invariant finding each."""
    return [
        GateReason(
            gate_ledger.make_finding(
                gate_ledger.RUN_GATE_HOST_DECLARATION_INVALID,
                subject='harness.host_requirements',
                evidence={'declaration': f},
                observed={},
                remedy='fix the declaration (re-run /simulation-scan-agent)',
                draft_ref=None,
            ),
            f'host toolchain: {f} — fix the declaration (re-run /simulation-scan-agent); no overrule lifts this.',
        )
        for f in findings
    ]


def _kind_of(doc: dict[str, Any], check_id: str) -> str:
    return next((c['kind'] for c in doc['checks'] if c['id'] == check_id), '')


#: The ONE remedy for a harness failure that makes a bench NOT RUNNABLE. Never /simulation-fixes: it has no run to read.
RUN_GATE_REMEDY = 'Fix what it names, then re-run /simulation-scan-agent.'


def _run_measured(result: dict[str, Any]) -> bool:
    """A failure the run measures rather than refuses on: the agent's own raise, or a silent turn whose owner the
    smoke could not determine (:data:`RUN_MEASURED_OWNERS`). The run's signal, never a reason not to run."""
    return result.get('status') == STATUS_FAILED and result.get('owner') in RUN_MEASURED_OWNERS


def not_runnable(doc: dict[str, Any], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The ONE run-readiness rule `verify` and `run-gate` share: the results of the checks covering
    `harness` that FAILED, except a failure the run measures (:func:`_run_measured`). Keyed on the
    RESULTS, never on `fields.harness`. A recorded status is already effective — the check settled its
    findings against the plugin's ledger — so an overruled check reads `not_verified` (:func:`evidenced_status`)
    and never counts here. No verdict (`not_run`,
    no record) never counts here; a blocking smoke that RAN without one is `run-gate`'s (:func:`unproven`)."""
    harness_checks = set(coverage(doc).get('harness', []))
    return [r for r in results if r.get('id') in harness_checks and r.get('status') == STATUS_FAILED and not _run_measured(r)]


def unproven(doc: dict[str, Any], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The results of the BLOCKING smokes covering `harness` that STARTED and reached no verdict
    (`not_run` marked :data:`ATTEMPTED`). A run over one dies the way the smoke did, on every scenario.
    A plan-time `not_run` never carries the mark, so a missing prerequisite stays RUNNABLE."""
    harness_checks = set(coverage(doc).get('harness', []))
    smokes = {c['id'] for c in doc['checks'] if c.get('blocking') and c.get('kind') == 'smoke' and c['id'] in harness_checks}
    return [r for r in results if r.get('id') in smokes and r.get('status') == STATUS_NOT_RUN and r.get(ATTEMPTED) is True]


#: The SDK the smoke imports, as its own `plurai.identity.sdk_identity()` names it (else its version and file).
_SDK_IDENTITY_PROGRAM = (
    'import json\n'
    'try:\n'
    '    from plurai.identity import sdk_identity\n'
    '    print(json.dumps(sdk_identity(), sort_keys=True))\n'
    'except ImportError:\n'
    '    import importlib.metadata as m, plurai\n'
    "    print(json.dumps({'version': m.version('plurai'), 'file': plurai.__file__}, sort_keys=True))\n"
)


def sdk_identity_stamp() -> str:
    """A hash of the identity of the SDK the smokes import (`_plurai_py()`), or a typed `unreadable:` stamp."""
    try:
        done = subprocess.run([_plurai_py(), '-c', _SDK_IDENTITY_PROGRAM], capture_output=True, text=True, timeout=60, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return f'unreadable:{type(exc).__name__}'
    lines = done.stdout.strip().splitlines()
    if done.returncode != 0 or not lines:
        return f'unreadable:exit_{done.returncode}'
    return sha256_bytes(lines[-1].encode('utf-8'))


def unproven_gate(doc: dict[str, Any], results: list[dict[str, Any]], workdir: Path) -> list[GateReason]:
    """`plugin.run_gate.smoke_unproven` per :func:`unproven` result, fingerprinted on the check, the signature it
    ran at, the harness on disk now (simulate.py, its split helpers and the config), the SDK the smokes import
    now, and the typed cause it stopped on — so an overrule lapses once any of them moves. What it saw is
    `observed`. Also its NOT RUNNABLE line: what it saw, and the one remedy."""
    out = []
    found = unproven(doc, results)
    harness = str(harness_fingerprint(workdir, detect_lang(workdir))['fingerprint']) if found else ''
    sdk = sdk_identity_stamp() if found else ''
    for result in found:
        seen = result.get('evidence') or result.get('reason') or 'no evidence recorded'
        remedy = 'supply what it names, then re-run bench_manifest.py verify --mode reuse and this gate.'
        finding = gate_ledger.make_finding(
            gate_ledger.RUN_GATE_SMOKE_UNPROVEN,
            subject=str(result['id']),
            evidence={
                'check_id': str(result['id']), 'signature': str(result.get('signature') or ''), 'harness': harness, 'sdk': sdk,
                'cause': str(result.get('cause') or 'unrecorded'),
            },
            observed={'seen': seen},
            remedy=remedy,
            draft_ref=None,
        )
        out.append(GateReason(finding, f'{result["id"]}: it ran and reached no verdict — {seen} — {remedy}'))
    return out


#: The grant a declared route smoke runs on — the name the hosted `up` refuses without (its `BRING_UP_GRANT`).
ROUTE_SMOKE_GRANT = 'route_smoke'


def ungranted_route(doc: dict[str, Any], workdir: Path) -> list[GateReason]:
    """`plugin.run_gate.route_ungranted` (security: consent) for a declared route smoke the operator has not
    granted, and its NOT RUNNABLE line. The grant is readable at $0, so it is refused here, before a world is
    built for a route that cannot come up."""
    if host_interception(doc) is None or ROUTE_SMOKE_GRANT in granted(workdir):
        return []
    remedy = f'add {ROUTE_SMOKE_GRANT} to the granted: line of {workdir / CONSENT_FILE} (granted: {ROUTE_SMOKE_GRANT}).'
    finding = gate_ledger.make_finding(
        gate_ledger.RUN_GATE_ROUTE_UNGRANTED, subject=CONSENT_FILE, evidence={'grant': ROUTE_SMOKE_GRANT}, observed={}, remedy=remedy, draft_ref=None,
    )
    return [GateReason(finding, f'{ROUTE_SMOKE_GRANT}: bringing the route up puts a copy of the agent into your system, and that was not granted — {remedy}')]


def run_gate_line(result: dict[str, Any]) -> str:
    """One NOT RUNNABLE reason: the failed harness check, its evidence, and the one remedy."""
    return f'{result["id"]}: {result.get("evidence") or result.get("reason") or "no evidence recorded"} — {RUN_GATE_REMEDY}'


def finding_lines(result: dict[str, Any]) -> list[str]:
    """The `finding:` line of each gate finding a result recorded (raw, as its check minted them)."""
    lines = []
    for raw in result.get('findings') or []:
        try:
            lines.append(gate_ledger.finding_line(gate_ledger.GateFinding.from_dict(raw)))
        except (TypeError, ValueError):
            continue  # a record this build cannot read states nothing it can quote
    return lines


def recorded_finding_lines(doc: dict[str, Any], results: list[dict[str, Any]]) -> list[str]:
    """The `finding:` lines of every FAILED harness check — the agent-owned ones included, which hold
    nothing and say what the run will measure."""
    harness_checks = set(coverage(doc).get('harness', []))
    return [line for r in results if r.get('id') in harness_checks and r.get('status') == STATUS_FAILED for line in finding_lines(r)]


def settle_run_gate(reasons: list[GateReason], workdir: Path, *, evaluated: tuple[str, ...]) -> tuple[list[GateReason], list[str]]:
    """The run gate's own findings against the plugin's ledger (`$ART/.gate_overrules.jsonl`): the reasons
    whose finding still HOLDS (hard, or advisory and not overruled), and the lines to print. A ledger that
    cannot be read lifts nothing — every hard or holding finding stays — and says so."""
    findings = [r.finding for r in reasons]
    try:
        applied, lines = gate_ledger.settle(findings, art=workdir / 'artifacts', evaluated=evaluated, findings_out=None)
    except (OSError, gate_ledger.LedgerError) as exc:
        held = [r for r in reasons if r.finding.authority in gate_ledger.HARD_AUTHORITIES or r.finding.holds]
        lines = [*map(gate_ledger.finding_line, findings),
                 f'the gate ledger {gate_ledger.ledger_path(workdir / "artifacts")} could not be read ({type(exc).__name__}: {exc}); no overrule was applied']
        return held, lines
    holding = {id(f) for f in applied.holding}
    return [r for r in reasons if id(r.finding) in holding], lines


def exit_code_for(doc: dict[str, Any], results: list[dict[str, Any]]) -> int:
    """1 a blocking check FAILED or the bench is NOT RUNNABLE; 4 one is NOT VERIFIED; 3 one could not run or is STALE; else 0.

    The split is the error posture's: a failure has findings about the agent to read, a not-run
    has a prerequisite to install, and telling an operator the wrong one sends them to the wrong
    place for the whole afternoon. A failure the run measures (:func:`_run_measured`) is the
    run's signal, not the bench's: it changes nothing here.
    """
    blocking = {c['id'] for c in doc['checks'] if c.get('blocking')}
    by_id = {r['id']: r['status'] for r in results}
    if any(r.get('id') in blocking and r.get('status') == STATUS_FAILED and not _run_measured(r) for r in results) or not_runnable(doc, results):
        return 1
    if any(r.get('id') in blocking and r.get('status') == STATUS_NOT_VERIFIED and not gate_passed(r) for r in results):
        return 4
    # STALE reads like a not-run: nothing re-checked it in this invocation, and a full `verify` is the remedy.
    if any(by_id.get(i) in (STATUS_NOT_RUN, STATUS_STALE) for i in blocking):
        return 3
    return 0


# --------------------------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------------------------


def _topology_to_manifest(topology: dict[str, Any], config: dict[str, Any] | None, workdir: Path) -> dict[str, Any]:
    """The manifest an `init` can honestly derive, with `TODO` wherever the ANALYSIS must decide."""
    config = config if isinstance(config, dict) else {}
    agent = topology.get('agent') or {}
    inventory = _inventory_artifact(workdir)
    tools_cfg = config.get('tools') or {}
    verbs = config.get('render_actions')
    verbs = [str(v) for v in verbs] if isinstance(verbs, list) else []
    # Guarded BEFORE the comprehension: `tools_cfg.items()` is evaluated first, so an `if
    # isinstance(tools_cfg, dict)` inside the comprehension protected nothing and a `tools:` written
    # as a LIST crashed `init` with an AttributeError instead of a finding.
    tools_cfg = tools_cfg if isinstance(tools_cfg, dict) else {}
    mock_modes = {name: (spec.get('mode', 'mock') if isinstance(spec, dict) else 'mock') for name, spec in tools_cfg.items()}
    run_mode = derive_run_mode(config)
    # ROOT key only, and only a word the shape vocabulary admits — the same read the config
    # verifier and the runner perform. A `shape:` nested under `agent:` declares nothing to
    # anybody, and a typo is not a declaration either: both are findings `verify_config` owns,
    # and publishing them here as if they were declarations is how the manifest disagreed with
    # the verifier in the first place. Either way this reads UNDECLARED, which is honest.
    declared_shape = str(config.get('shape') or '').strip().lower()
    declared_shape = declared_shape if declared_shape in DECLARABLE_SHAPES else ''
    bridge = harness_bridge(workdir)
    return {
        'schema': SCHEMA_ID,
        'agent': {
            'name': str(agent.get('name') or SENTINEL),
            'framework': str(agent.get('framework') or ''),
            # From the FILES, never the config's free-text `agent.language`: a bridge harness means a Node agent.
            'language': 'node' if detect_lang(workdir) == 'bridge' else 'python',
            # THE DECLARATION, not a constant. Hardcoding `dialog` published `agent.shape:
            # dialog` over a plurai.config.yaml the config verifier had just validated as
            # `pipeline` — two documents stating one fact, disagreeing, with nothing comparing
            # them.
            #
            # AND THE ABSENCE IS ITS OWN WORD. `or 'dialog'` would publish `dialog`
            # as a STATED FACT for a config that says nothing — including over an
            # `invoke_pipeline`-only harness, where `verify_config --simulate` FAILS
            # `[shape_absent_pipeline]` and the runner exits 2. Same file, opposite verdicts,
            # nothing comparing them. A manifest that cannot say "the bench did not answer"
            # answers for it.
            #
            # TWO NAMED QUESTIONS, ONE READ, because they are two facts and conflating them is
            # the whole defect: `shape` is what the DOCUMENT declares, `dispatch_shape` is what
            # the RUNNER will drive it as (the declaration, else `dialog` — the runner's own
            # rule, and the only one a check may key on). Derived together here so they cannot
            # disagree; nothing re-derives either downstream.
            #
            # NORMALISED HERE, at the one place the manifest is built, with the same
            # `.strip().lower()` both other readers apply (`verify_config._check_shape_declaration`,
            # and the runner as that function documents it). `mode_rule_holds` compares EXACTLY —
            # deliberately, since folding there would change the grammar for every rule — so a
            # legal, runnable `shape: Dialog` published verbatim made `agent.shape == dialog`
            # false and planned the mockless bench's ONLY live gate (`smoke_turn`, below) as
            # `not_applicable`.
            'shape': declared_shape or UNDECLARED_SHAPE,
            'dispatch_shape': declared_shape or 'dialog',
            'entry': str(agent.get('entry') or ''),
            # Typed, and only the analysis can author it: prose cannot be resolved to a function.
            **({'entry_symbol': agent.get('entry_symbol') or SENTINEL} if agent.get('entry') or agent.get('entry_symbol') else {}),
            'module_path': agent_dir(workdir).name,
        },
        'surfaces': {
            'tools': [{'name': name, 'mode': mode} for name, mode in sorted(mock_modes.items())],
            'render_actions': [{'name': v, 'args': {}, 'args_source': 'unknown'} for v in verbs],
            'knowledge': [
                {'name': str(k.get('name') or SENTINEL), 'kind': 'prompt_inlined' if 'inlined' in str(k.get('why', '')) else 'retrieval', 'sources': []}
                for k in (topology.get('knowledge') or [])
            ],
            'agent_context': {'seedable': False, 'finding': SENTINEL},
            # The live tools that run for real only on a grant: the map's typed consent set (raises
            # `UnclassifiedTools` on a live tool it does not classify), plus either declaration of a
            # live writer — the map's `writes: live` and the config's write.
            'host_effects': sorted(
                set(consent_required(topology, runtime=runtime_of(topology, config)))
                | set(live_write_tools(config, at_agent_host=intercepted_at_agent_host(topology), typed_writes=typed_writes(topology)))
                | set(typed_live_writers(topology))
            ),
            **_host_effects_hashes(topology, workdir),
            **inventory_surfaces(inventory, {}),
        },
        # `provider` is a SENTINEL, not omitted: absent, `provider_key_present` cannot decide and
        # returns True, so `requires: ['provider_key']` silently became a no-op on every bench this
        # function produced — the mockless live gate then ran with no key and reported the agent's
        # own crash as a bench defect. The analysis has to name the provider.
        'harness': {'run_mode': run_mode, 'language': 'python', 'provider': SENTINEL,
                    **({'bridge': bridge} if bridge else {}),
                    **({'tool_interception': agent['tool_interception']} if agent.get('tool_interception') else {}),
                    **({'host_requirements': agent['host_requirements']} if agent.get('host_requirements') else {}),
                    # Written on CREATE only — the refresh below never adds it, so a manifest without it is one
                    # that predates the field, and `verify_simulate` warns rather than fails over it.
                    **({'resume': bridge_resume(workdir) or {'mode': 'replay'}} if (declared_shape or 'dialog') == 'dialog' else {})},
        **_provenance_fields(inventory),
        'sources': {'corpus_roots': [str(s) for s in ((topology.get('grounding') or {}).get('sources') or [])], 'suffixes': []},
        'consent': _consent_from_marker(workdir),
        'checks': _default_checks(run_mode, verbs, mock_modes, bridge=bool(bridge)),
        'provenance': {'created_at': _now(), 'plugin_version': _plugin_version(), 'skill': 'simulation-scan-agent'},
    }


def _inventory_artifact(workdir: Path) -> dict[str, Any]:
    """The ONE inventory `init` copies: the artifact when it is the source's current reading, else a fresh read
    written to it — so the manifest, the map and lint_prd never hold two readings."""
    fresh = agent_inventory.extract(workdir)
    inventory = agent_inventory.current(workdir, fresh)
    if inventory is fresh and 'unavailable' not in fresh:
        _write_json(agent_inventory.inventory_path(workdir), fresh)
    return inventory


def _provenance_fields(inventory: dict[str, Any]) -> dict[str, Any]:
    """`agent_root` / `bench_overlay[]` from the reader (C11): where the agent's own tree ends, and the files in it git
    says the bench wrote. `{}` when no repository resolved."""
    return {k: inventory[k] for k in ('agent_root', 'bench_overlay') if k in inventory}


def inventory_surfaces(inventory: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """C2 + C3 from the agent's source: every input the reader found (an entry the analysis already decided keeps its
    status, waiver, channel and kind; a new one is `waived` with the `TODO` waiver `validate` refuses; a free-text
    `reason` becomes the `detail` of a `TODO` waiver to type), and the production tool set, re-derived — keeping the
    analysis's cited `not_loaded` groups. `{}` when no repository resolved.

    `inventory` may be a fresh `init`'s own surfaces, whose every input already carries the new-input `TODO` waiver:
    only the reader's fields are taken from it, so that stub never lands on an input the analysis `modelled`."""
    if 'unavailable' in inventory or 'production_tools' not in inventory:
        return {}
    decided = {_source_key(e): e for e in existing.get('inputs') or [] if isinstance(e, dict)}
    inputs = []
    for entry in inventory['inputs']:
        was = decided.get(_source_key(entry)) or {'status': 'waived'}
        kept = {k: was[k] for k in ('kind', 'status', 'channel') if k in was}
        if kept.get('status') == 'waived':
            typed = was.get('waived')
            kept['waived'] = typed if isinstance(typed, dict) else {'because': SENTINEL, 'cite': SENTINEL, 'detail': str(was.get('reason') or '')}
        read = {k: v for k, v in entry.items() if k not in ('status', 'waived', 'channel', 'reason')}
        inputs.append({**read, **kept})
    source = inventory['production_tools']
    production = {k: source[k] for k in ('loader', 'count', 'names', 'switches', 'dynamic', 'backends', 'pauses', 'static', 'live', 'live_enumerated_by') if k in source}
    production['inventory_sha256'] = source.get('inventory_sha256') or agent_inventory.digest(inventory)
    was_production = existing.get('production_tools')
    if isinstance(was_production, dict) and was_production.get('not_loaded'):
        production['not_loaded'] = was_production['not_loaded']
    return {'inputs': inputs, 'production_tools': production}


def _read_consent_marker(path: Path) -> dict[str, Any]:
    """One three-state consent marker → `{state, names?}`. Absent or unreadable is `unasked`.

    "We asked and they said no" and "nobody ever asked" are different facts and used to be the
    same file contents; the marker keeps them apart and so does this.
    """
    state: dict[str, Any] = {'state': 'unasked'}
    if not path.is_file():
        return state
    text = path.read_text(encoding='utf-8').strip()
    if text.startswith('granted'):
        names = [n.strip() for n in text.partition(':')[2].split(',') if n.strip()]
        return {'state': 'granted', **({'names': names} if names else {})}
    if text.startswith('declined'):
        return {'state': 'declined'}
    return state


def _consent_from_marker(workdir: Path) -> dict[str, Any]:
    """Import the TWO consent markers — they are two different grants under two names.

    `.probe_consent` covers calls to the agent's OWN READ-ONLY TOOLS; `.live_probe_consent`
    covers the scan-time asks sent to the LIVE AGENT, which `scan_contract.sh` forwards as
    `--probe-consent`. A customer who lets you call `load_assets()` has not thereby let you talk
    to their agent, which is why they are separate files.

    `live` reads the second marker rather than a constant. `requires: ['consent:live']` gates on
    this value, so a hardcoded `unasked` would skip any check declaring it with "live-probe consent
    is unasked" while the workdir granted it and `scan_contract.sh` fired the probes — the bench
    declaring the opposite of the truth on the one axis these two markers exist to keep straight.
    """
    return {
        'probes': _read_consent_marker(workdir / '.probe_consent'),
        'live': _read_consent_marker(workdir / '.live_probe_consent'),
        # asked at the scan and written here, never read from a marker: no answer lets nothing out
        'unsandboxed': {'state': 'unasked'},
    }


def _default_checks(run_mode: str, verbs: list[str], tools: dict[str, str], *, bridge: bool) -> list[dict[str, Any]]:
    """The checks every bench gets. Agent-specific ones (probes, the surface script) are added by
    the analysis, because only it knows what the agent exposes and how to call it."""
    checks: list[dict[str, Any]] = [
        # A static verifier proves what is DECLARED, never what reaches the agent: a delivery surface
        # is verified only by a check that drives the harness (`smoke_turn`).
        {'id': 'verify_config', 'kind': 'verifier', 'verifies': ['harness'], 'runner': 'builtin', 'policy': 'always', 'blocking': True},
        {'id': 'verify_simulate', 'kind': 'verifier', 'verifies': ['harness'], 'runner': 'builtin', 'policy': 'always', 'blocking': True},
        {'id': 'lint_prd', 'kind': 'verifier', 'verifies': ['sources'], 'runner': 'builtin', 'policy': 'always'},
        {'id': 'verify_prd_appendix', 'kind': 'verifier', 'verifies': ['sources'], 'runner': 'builtin', 'policy': 'always'},
        {'id': 'smoke_init', 'kind': 'smoke', 'verifies': ['harness'], 'runner': 'builtin', 'policy': 'always', 'blocking': True},
        {
            'id': 'smoke_turn',
            'kind': 'smoke',
            # It plants every declared request key and fails naming any the harness never read.
            'verifies': ['harness', 'surfaces.agent_context'],
            'runner': 'builtin',
            'policy': 'on_mode',
            # DECIDED BY THE DISPATCH SHAPE ALONE. A pipeline bench exports no `invoke_agent` to
            # drive (its hook is `smoke_pipeline`, below). The mock posture is not a condition:
            # the smoke Inits `mock_mode='local'`, the SDK's in-process mock, so a mocked bench
            # is smoked exactly like a mockless one and never dials a deployed mock.
            #
            # `dispatch_shape`, not `shape`: an UNDECLARED bench runs as a dialog (the runner's
            # rule), and keyed on the declaration it would read `undeclared` and never run.
            'mode_rule': 'agent.dispatch_shape == dialog',
            'requires': ['provider_key'],
            'blocking': True,
            'witnesses': ['agent.entry'],
        },
        {
            # ONE pipeline pass per `agent.smoke_inputs` world, on the local mock. It REPORTS and
            # never blocks: a pipeline's first live call used to be the paid scored run.
            'id': 'smoke_pipeline',
            'kind': 'smoke',
            # It drives the harness with the declared request keys, so it owns their delivery (as `smoke_turn`).
            'verifies': ['harness', 'surfaces.agent_context'],
            'runner': 'builtin',
            'policy': 'on_mode',
            'mode_rule': 'agent.dispatch_shape == pipeline',
            'requires': ['smoke_inputs', 'provider_key', 'no_real_tools'],
            'witnesses': ['agent.entry'],
        },
        {'id': 'scan_contract', 'kind': 'contract', 'verifies': ['sources'], 'runner': 'builtin', 'policy': 'always', 'requires': ['checker', 'corpus_roots'], 'blocking': True},
        # The probe world Init'd and read back (`verify_smoke.py --seed-world`): seed accepted AND read back as seeded.
        # Advisory, never blocking; not_applicable until the bench has a probe world and a seed_state to take it.
        {'id': 'seed_readback', 'kind': 'smoke', 'verifies': ['harness'], 'runner': 'builtin', 'policy': 'always', 'requires': ['seeded_world']},
    ]
    if mocked := [f'surfaces.tools[{name}]' for name, mode in sorted(tools.items()) if mode != 'real']:
        # The deployed mock answers each mocked tool as the bench's own contract tests expect: one witness per
        # tool. A live check of the deployed mock, never a static verifier. Nothing blocks on it: `verify` (a scan has
        # no mock yet) and the suite's `mock-gate` alike, which warns and names the gap as a risk.
        checks.append({'id': 'mock_contract', 'kind': 'mock', 'verifies': mocked, 'witnesses': mocked, 'runner': 'builtin', 'policy': 'always',
                       'requires': ['mock_built']})
    if bridge:
        # The map against the config and the harness, then the route probe: every mapped entry reaches its mock.
        checks.append({'id': 'verify_bridge', 'kind': 'verifier', 'verifies': ['harness'], 'runner': 'builtin', 'policy': 'always',
                       'requires': ['node'], 'blocking': True})
    return checks


def _now() -> str:
    from datetime import datetime, timezone  # noqa: PLC0415 - only the writers need it

    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _plugin_version() -> str:
    data = _read_json(plugin_root() / '.claude-plugin' / 'plugin.json') or {}
    return str(data.get('version') or 'unknown')


def cmd_init(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    target = manifest_path(workdir)
    topology_path = Path(args.from_topology) if args.from_topology else workdir / 'artifacts' / 'agent_topology.json'
    topology = _read_json(topology_path)
    if not isinstance(topology, dict):
        print(f'ERROR: no topology at {topology_path} — Step 2a writes it before this runs.', file=sys.stderr)
        return 2
    config_path = workdir / 'plurai.config.yaml'
    config = _load_yaml(config_path)
    existing = _read_json(target)
    if isinstance(existing, dict) and config is None and config_path.exists():
        # A config that exists but will not parse derives NO tools and NO verbs, and the merge
        # below applies that derivation wholesale — so a momentarily broken YAML rewrote the
        # manifest to claim a tool-less mockless bench while printing that it kept everything.
        print(f'ERROR: {config_path} exists but could not be parsed, so a refresh would rewrite the '
              'surfaces from an empty derivation. Fix the config and re-run.', file=sys.stderr)
        return 2
    try:
        fresh = _topology_to_manifest(topology, config, workdir)
    except UnclassifiedTools as error:
        print(f'ERROR: {error}. {target} is unchanged.', file=sys.stderr)
        return 2
    if holes := seam_coverage_holes(config):
        fresh['coverage_holes'] = holes
    if isinstance(existing, dict) and not args.force:
        # NEVER overwrite what the analysis decided. `init` re-derives the mechanical half; the
        # judged fields (the stateless finding, the arg contracts, the extra suffixes) and every
        # check the analysis added are the operator's, and a refresh that dropped them would make
        # re-running `init` a destructive act nobody would risk.
        merged = json.loads(json.dumps(existing))
        merged['surfaces']['tools'] = fresh['surfaces']['tools']
        merged['surfaces']['host_effects'] = fresh['surfaces']['host_effects']
        # Re-derived from this init's read of the source; the analysis's decisions on an input it still finds are kept.
        merged['surfaces'].update(inventory_surfaces(fresh['surfaces'], existing['surfaces']))
        for key in ('host_effects_tools_sha256', 'host_effects_map_sha256'):
            if key in fresh['surfaces']:
                merged['surfaces'][key] = fresh['surfaces'][key]
            else:
                merged['surfaces'].pop(key, None)
        declared = {v['name']: v for v in merged['surfaces']['render_actions']}
        merged['surfaces']['render_actions'] = [declared.get(v['name'], v) for v in fresh['surfaces']['render_actions']]
        merged['harness']['run_mode'] = fresh['harness']['run_mode']
        # The config's tool_body holes are re-derived; a gap the suite accepted (`coverage-hole`) is kept.
        if holes := merged_coverage_holes(fresh.get('coverage_holes', []), existing.get('coverage_holes')):
            merged['coverage_holes'] = holes
        else:
            merged.pop('coverage_holes', None)
        # ASSIGNED, not backfilled: both are derived from the config, so a later-added `shape:`
        # must reach them, and a manifest written before `dispatch_shape` existed fails `validate`.
        merged['agent']['shape'] = fresh['agent']['shape']
        merged['agent']['dispatch_shape'] = fresh['agent']['dispatch_shape']
        # ASSIGNED: what the files say the harness is. A bridge's resume is its map's, so it follows the map too.
        merged['agent']['language'] = fresh['agent']['language']
        # ASSIGNED: where the agent's tree ends is the reader's, re-derived with the inventory.
        for key in ('agent_root', 'bench_overlay'):
            if key in fresh:
                merged[key] = fresh[key]
        merged['harness']['language'] = fresh['harness']['language']
        if 'bridge' in fresh['harness']:
            merged['harness']['bridge'] = fresh['harness']['bridge']
            if 'resume' in fresh['harness']:
                merged['harness']['resume'] = fresh['harness']['resume']
        elif merged['harness'].pop('bridge', None) is not None:
            resume = merged['harness'].get('resume')
            if isinstance(resume, dict) and str(resume.get('evidence') or '').startswith(BRIDGE_MAP):
                merged['harness']['resume'] = {'mode': 'replay'}
        # A BUILTIN check's declaration is this plugin's, not the analysis's — WHOLESALE: a kept
        # `mode_rule`, `verifies`, `blocking` or `requires` from an older build silently re-plans it.
        fresh_builtins = {c['id']: c for c in fresh['checks'] if c['runner'] == 'builtin'}
        # The mock contract is derived only while a tool is mocked: kept over an all-real config, it would claim tools nothing mocks.
        merged['checks'] = [fresh_builtins.get(c['id'], c) if c.get('runner') == 'builtin' else c for c in merged['checks']
                            if not (c.get('runner') == 'builtin' and c.get('id') in ('mock_contract', 'verify_bridge') and c['id'] not in fresh_builtins)]
        # A builtin this build added reaches an existing bench on refresh, or a rescan never runs it.
        kept = {c['id'] for c in merged['checks']}
        merged['checks'] += [c for c in fresh['checks'] if c['runner'] == 'builtin' and c['id'] not in kept]
        # BACKFILLED, not merged: a manifest written before `provider` had a sentinel carries none,
        # so `provider_key_present` answers True unconditionally and the live-turn gate's
        # prerequisite is a no-op on exactly the benches that already exist. `init` is advertised as
        # safe to re-run, so this is the path such a bench actually takes.
        merged['harness'].setdefault('provider', fresh['harness']['provider'])
        if 'tool_interception' in fresh['harness']:
            merged['harness'].setdefault('tool_interception', fresh['harness']['tool_interception'])
        if 'host_requirements' in fresh['harness']:
            merged['harness'].setdefault('host_requirements', fresh['harness']['host_requirements'])
        # A sentinel gives way to a symbol the topology now authors. The sentinel ITSELF is never
        # backfilled onto a manifest written before `entry_symbol` existed: absent validates (its
        # witness reads not_determinable), so writing `TODO` there turned a refresh — the path a
        # legacy bench takes to pick up `dispatch_shape` — into a refusal only a re-scan could lift.
        # The refresh names the line instead; the analysis authors it on the next scan.
        notes: list[str] = []
        symbol = fresh['agent'].get('entry_symbol')
        if symbol not in (None, SENTINEL) and merged['agent'].get('entry_symbol') in (None, SENTINEL):
            merged['agent']['entry_symbol'] = symbol
        elif symbol == SENTINEL and 'entry_symbol' not in merged['agent']:
            notes.append(
                'agent.entry_symbol is not declared (a manifest written before the field), so the entry witness reads '
                'not_determinable. To declare it, add to "agent": "entry_symbol": {"executes": "in_harness", "ref": '
                '"<package.module:Qual.name>"} — or {"executes": "external", "why": "<where it runs>"} — or re-run the scan'
            )
        # `agent.entry` is WITNESS-ONLY now, and a check the analysis wrote before that still names it
        # in `verifies` is refused by `validate` on every refresh. Dropping the name withdraws a claim
        # the check never proved; the builtin smokes' witnesses carry the field instead.
        for check in merged['checks']:
            if check.get('runner') != 'builtin' and isinstance(check.get('verifies'), list):
                kept_fields = [f for f in check['verifies'] if f not in WITNESS_ONLY_FIELDS]
                if len(kept_fields) != len(check['verifies']):
                    notes.append(f"checks[{check.get('id')}]: dropped {', '.join(WITNESS_ONLY_FIELDS)} from `verifies` — only a smoke's runtime witness proves it")
                    check['verifies'] = kept_fields
        merged['consent'] = _consent_from_marker(workdir) if not merged.get('consent') else merged['consent']
        # The operator's answer is kept across a re-scan; a manifest from before the grant gets the stated `unasked`.
        if isinstance(merged['consent'], dict):
            merged['consent'].setdefault('unsandboxed', {'state': 'unasked'})
        merged.setdefault('provenance', {})['updated_at'] = _now()
        merged['provenance']['plugin_version'] = _plugin_version()
        _write_json(target, merged)
        print(f'refreshed {target} (kept every analysis-authored field; --force to rewrite)')
        for note in notes:
            print(f'  NOTE {note}')
        _init_run_mode_marker(workdir, config, fresh['harness']['run_mode'])
        return 0
    _write_json(target, fresh)
    todo = _sentinel_findings(fresh)
    print(f'wrote {target}')
    for finding in todo:
        print(f'  TODO {finding}')
    _init_run_mode_marker(workdir, config, fresh['harness']['run_mode'])
    return 0


def _init_run_mode_marker(workdir: Path, config: Any, run_mode: str) -> None:
    """Keep the marker in step with the manifest's `harness.run_mode`, which `validate` compares it to. Only from a
    config that parsed: before the scan writes one, an empty derivation would stamp `mockless` on a mocked bench."""
    if isinstance(config, dict):
        print(f'run_mode: {run_mode} — {sync_run_mode_marker(workdir, run_mode)}')


def cmd_run_mode(args: argparse.Namespace) -> int:
    """Derive the mock posture from `plurai.config.yaml` and make `.run_mode` say it — the one writer of the marker."""
    workdir = resolve_workdir(args.workdir)
    config_path = workdir / 'plurai.config.yaml'
    config = _load_yaml(config_path) if config_path.is_file() else None
    if not isinstance(config, dict):
        why = 'does not parse (or this interpreter lacks PyYAML)' if config_path.is_file() else 'is absent — the scan writes it'
        print(f'ERROR: {config_path} {why}; {RUN_MODE_MARKER} is unchanged.', file=sys.stderr)
        return 2
    needs, run_mode = mock_needs(config), derive_run_mode(config)
    print(f'run_mode: {run_mode} — {sync_run_mode_marker(workdir, run_mode)}')
    print(f'  needs the mock: {", ".join(needs)}' if needs else '  nothing is mocked: no tool or world type needs the mock')
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    doc = _read_json(manifest_path(workdir))
    if not isinstance(doc, dict):
        print(f'ERROR: no readable {MANIFEST_FILENAME} in {workdir} — run `bench_manifest.py init` first.', file=sys.stderr)
        return 2
    findings = validate(doc, workdir)
    for finding in findings:
        print(f'bench-manifest: {finding}', file=sys.stderr)
    if findings:
        return 2
    if not doc['checks']:
        print('bench-manifest: this manifest declares NO checks, so verifying it proves nothing — '
              'deleting a check is not a way to make the gate green.', file=sys.stderr)
        return 2
    uncovered = [f for f, ids in coverage(doc).items() if not ids]
    print(f'bench-manifest: valid — {len(doc["checks"])} check(s) over {len(verifiable_paths(doc))} field(s)'
          + (f'; {len(uncovered)} declared-only: {", ".join(uncovered)}' if uncovered else '; every field is covered'))
    return 0


#: The checks a re-verify after an input edit runs (`verify --after-edit`). The world's checks are in it: an edit to the
#: config, the PRD or the harness is exactly when the deployed mock's contract and a live turn can stop holding, and a
#: re-verify that left them out reported a pass nobody re-measured.
REVERIFY_CHECKS = ('verify_config', 'verify_simulate', 'lint_prd', 'verify_prd_appendix', 'scan_contract', 'mock_contract', 'smoke_init',
                   'smoke_turn', 'smoke_pipeline', 'verify_bridge', 'seed_readback')
#: The checks that load the harness and the config: after a harness, bridge or config edit their record must be re-run
#: (`verify --after-edit`) before it says anything about the bench; a pass recorded on other inputs is stale
#: (`release_verdict.validation_holds`).
HARNESS_RECHECKS = ('verify_config', 'verify_simulate', 'smoke_init', 'smoke_turn', 'smoke_pipeline', 'verify_bridge', 'seed_readback')


def cmd_verify(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    doc = _read_json(manifest_path(workdir))
    if getattr(args, 'after_edit', False):
        if args.only:
            print('ERROR: --after-edit IS a selection; pass it or --only, not both.', file=sys.stderr)
            return 2
        named = {str(c.get('id')) for c in doc.get('checks', []) if isinstance(c, dict)} if isinstance(doc, dict) else set()
        args.only = [c for c in REVERIFY_CHECKS if c in named] or list(REVERIFY_CHECKS)
    if not isinstance(doc, dict):
        print(f'ERROR: no readable {MANIFEST_FILENAME} in {workdir}.', file=sys.stderr)
        # The SAME refusal the invalid-manifest branch below writes, for the same reason. Wired
        # only there, an ABSENT manifest left the previous run's record standing: `verify` exited
        # 2, and `integrity-line` then printed the old sentence — "checks 7 of 7 executed · smoke:
        # 2-turn" — and exited 0 over a scan that verified nothing.
        _invalidate(verification_path(workdir), f'there was no readable {MANIFEST_FILENAME} when this scan ran; NOTHING was verified')
        return 2
    checks = doc.get('checks')
    declared = {str(c.get('id')) for c in checks if isinstance(c, dict)} if isinstance(checks, list) else set()
    if args.only and (undeclared := sorted(set(args.only) - declared)):
        if len(undeclared) == len(set(args.only)):
            # Refused before anything is written: run as asked, it would rewrite every declared check as a deselect.
            print(f'ERROR: --only names {", ".join(undeclared)}, none of which {MANIFEST_FILENAME} declares; nothing was verified and the '
                  'record is unchanged. A builtin this plugin build adds is declared by `bench_manifest.py init`.', file=sys.stderr)
            return 2
        # The rest still run: a bench that predates a builtin keeps its multi-id re-verify.
        print(f'bench-manifest: --only ignores {", ".join(undeclared)}, which {MANIFEST_FILENAME} does not declare '
              '(`bench_manifest.py init` declares a builtin this plugin build adds).', file=sys.stderr)
    if findings := validate(doc, workdir):
        for finding in findings:
            print(f'bench-manifest: {finding}', file=sys.stderr)
        print('bench-manifest: NOTHING was verified — the manifest is invalid.', file=sys.stderr)
        # A REFUSAL IS A RESULT. Leaving the previous run's record in place let `integrity-line`
        # print last week's sentence with exit 0, and `report_guard` then REQUIRED that sentence
        # verbatim — this file's own originating failure, laundered through the gate built to end
        # it. Same posture as the contract's own sweep: no verdict must not read as an old one.
        _invalidate(verification_path(workdir), 'the manifest was invalid when this scan ran; NOTHING was verified')
        return 2

    prior = _read_json(verification_path(workdir))
    # A record this build cannot read must not decide what to skip. Degrades to "run everything",
    # which is the safe direction; `schema` was written from the first commit and read by nobody.
    prior = prior if isinstance(prior, dict) and prior.get('schema') == VERIFICATION_SCHEMA_ID else None
    decisions = plan(doc, workdir, args.mode, prior)
    by_id = {c['id']: c for c in doc['checks']}
    results: list[dict[str, Any]] = []
    # A contract's RESULT is bound to the PRD it ran over, as its artifact is (`scan_contract.sh`'s
    # `prd_sha256`): with no artifact on disk this record is all the suite's Step-0 gate has, and
    # one written over another PRD — or by a plugin that filed exit 2 as `not_run` — is STALE.
    prd_digest = sha256_path(workdir / 'simulation_prd.md')

    for decision in decisions:
        check = by_id[decision['id']]
        if args.only and decision['id'] not in args.only and decision['decision'] != STATUS_NOT_APPLICABLE:
            # A deselected check keeps its LAST result (`carry_forward`): a pass is `reused` only under
            # the reuse rule every other check obeys — its signature must still match — and `stale`
            # otherwise, never `not_run`. Carrying the prior entry verbatim (as this once did)
            # re-recorded a STALE signature and counted the check as executed; dropping the pass to
            # `not_run` (as it then did) told the overview page the scan's passing smoke never ran.
            # A check whose mode rule does not hold falls through: `not_applicable` is decided from the
            # manifest alone, selected or not, so a dialog bench's `smoke_pipeline` never reads "not run".
            was = next((r for r in (prior or {}).get('results', []) if r['id'] == decision['id']), None)
            carried = carry_forward(check, decision['signature'], was, prior)
            # A check with no pass on record that could not run here anyway records the prerequisite it lacks —
            # the true reason, whichever checks were selected — never "deselected".
            if not (carried['status'] == STATUS_NOT_RUN and decision['decision'] == STATUS_NOT_RUN):
                results.append(carried)
                continue
        bound = {'prd_sha256': prd_digest} if check['kind'] == 'contract' and prd_digest else {}
        if decision['decision'] != 'run':
            results.append({k: v for k, v in decision.items() if k != 'decision'} | {'status': decision['decision'], **bound})
            print(f'  {decision["decision"]:<15} {decision["id"]}  ({decision["reason"]})')
            continue
        outcome = execute_witnessed(check, doc, workdir, args.timeout)
        results.append({'id': decision['id'], 'signature': decision['signature'], **outcome, **bound})
        print(f'  {outcome["status"]:<15} {decision["id"]}  ({outcome.get("evidence") or outcome.get("reason") or ""})'[:200])
        for line in finding_lines(outcome):
            print(f'    {line}')

    results = [with_reason(evidenced_status(result), workdir) for result in results]
    denials = read_denials(workdir)
    toolchain = host_toolchain(doc, workdir)
    tools = mock_tools(doc, results, workdir)
    census = census_of(workdir, tools)
    payload = {
        'schema': VERIFICATION_SCHEMA_ID,
        'mode': args.mode,
        'ran_at': _now(),
        'manifest_hash': sha256_bytes(manifest_path(workdir).read_bytes()),
        'plugin_version': _plugin_version(),
        'results': results,
        'fields': field_status(doc, results),
        'denials': denials,
        'integrity_line': integrity_line(doc, results, denials, toolchain, census=census),
        'mock_tools': tools,
        'coverage_census': census,
    }
    # LOUD when the record cannot be written. `report_guard` fails OPEN on a missing
    # verification.json — correctly, for benches that predate the manifest — so an unwritable
    # artifacts dir would otherwise run every check, record nothing, and silently disarm the one
    # gate that makes the hand-off honest. Same posture as the contract's own unwritable-artifacts
    # refusal: no verdict is a refusal, never a pass.
    try:
        _write_json(verification_path(workdir), payload)
    except OSError as exc:
        print(
            f'ERROR: the checks ran but their record could not be written to {verification_path(workdir)} ({exc}). '
            'NOTHING is verified until it can be: the hand-off gate reads that file, and its absence reads as '
            '"this bench predates the manifest". Make the artifacts directory writable and re-run.',
            file=sys.stderr,
        )
        return 2
    print(payload['integrity_line'])
    route = ungranted_route(doc, workdir)
    unseen = unproven_gate(doc, results, workdir)
    held, lines = settle_run_gate([*route, *unseen], workdir, evaluated=(gate_ledger.RUN_GATE_ROUTE_UNGRANTED, gate_ledger.RUN_GATE_SMOKE_UNPROVEN))
    # A smoke that ran without a verdict keeps `verify`'s not-run posture (3) while its finding holds; once the
    # plugin overrules it, it no longer counts. The run gate stops on a held one.
    lifted = {r.finding.subject for r in unseen if r not in held}
    stuck = [*map(run_gate_line, not_runnable(doc, results)), *(r.reason for r in route if r in held)]
    for line in stuck:
        print(f'run-gate: {RUN_GATE_STATES[1]} — {line}')
    for line in lines:
        print(line)
    # The host toolchain is the RUN gate's to stop on (`run-gate`), where the plugin can overrule a measured gap;
    # the scan states it.
    for gap in toolchain.gaps():
        print(f'host toolchain: {host_gap_line(gap)}')
    return 1 if stuck else exit_code_for(doc, [r for r in results if r['id'] not in lifted])


def cmd_integrity_line(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    payload = _read_json(verification_path(workdir))
    if not isinstance(payload, dict) or not payload.get('integrity_line'):
        print(f'ERROR: no {VERIFICATION_FILENAME} — run `bench_manifest.py verify` first.', file=sys.stderr)
        return 2
    print(payload['integrity_line'])
    return 0


#: `contract-gate` states, by exit code — the scan contract's own 0/1/2/3, plus STALE.
CONTRACT_GATE_STATES = {0: 'PROCEED', 1: 'FIX FIRST', 2: 'NOT VERIFIED', 3: 'NOT RUN', 4: 'STALE'}


def contract_gate(workdir: Path) -> tuple[int, str]:
    """`(exit code, reason)` for the scan contract the suite's Step 0 is about to build on.

    Decided from RECORDED state only. An absent `scan_contract.json` is two different facts — the
    contract refused this bench's inputs (the manifest recorded `not_verified`: stop and fix) or
    it never ran (say so and go on) — and prose cannot tell them apart. A present one is a verdict
    only for the PRD it projected: `scan_contract.sh` records that PRD's `prd_sha256`.
    """
    stale_marker = workdir / '.scan_contract_stale'
    if stale_marker.is_file():
        line = stale_marker.read_text(encoding='utf-8', errors='replace').strip().splitlines()
        return 4, f'{stale_marker.name}: {line[0] if line else "(empty)"}'
    artifact_path = workdir / 'artifacts' / 'scan_contract.json'
    if artifact_path.is_file():
        artifact = _read_json(artifact_path)
        report = artifact.get('report') if isinstance(artifact, dict) else None
        if not isinstance(report, dict):
            return 4, f'{artifact_path} carries no readable `report`'
        recorded = artifact.get('prd_sha256')
        if not recorded:
            return 4, f'{artifact_path.name} records no `prd_sha256`, so the PRD it was projected from is unknown'
        prd = workdir / 'simulation_prd.md'
        current = sha256_path(prd)
        if current != recorded:
            return 4, f'simulation_prd.md changed after this contract was projected (recorded {str(recorded)[:12]}, now {(current or "absent")[:12]})'
        built = corpus_coordinate(workdir)
        if built is not None and len(built) == 1 and artifact.get('corpus_id') not in built:
            return 4, f'this bench stands on corpus {next(iter(built))} and the contract is bound to {artifact.get("corpus_id") or "no corpus"} — re-run it over the built scope'
        if report.get('proceed') is True:
            return 0, 'report.proceed is true for the current simulation_prd.md'
        return 1, f'report.proceed is not true — the held findings are `report.gate_findings` in {artifact_path} (fix-first items in {artifact_path.parent / "pre_generation_findings.md"})'
    doc = _read_json(manifest_path(workdir))
    contract_ids = {c.get('id') for c in doc.get('checks', []) if isinstance(c, dict) and c.get('kind') == 'contract'} if isinstance(doc, dict) else set()
    record = _read_json(verification_path(workdir))
    results = record.get('results', []) if isinstance(record, dict) else []
    entry = next((r for r in results if isinstance(r, dict) and r.get('id') in (contract_ids or {'scan_contract'})), None)
    # `not_applicable` is decided from the manifest alone (`plan`: a mode rule, or no corpus roots) and
    # never read the PRD, so a PRD edit cannot make it stale; it falls through to its NOT RUN line.
    if entry and entry.get('status') != STATUS_NOT_APPLICABLE and entry.get('prd_sha256') != sha256_path(workdir / 'simulation_prd.md'):
        # A record from another PRD, or from a plugin that filed the contract's exit 2 as `not_run`
        # and bound nothing, cannot say why THIS PRD has no verdict. Re-verify once.
        return 4, f'the recorded {entry["id"]} result ({entry.get("status")}) is not bound to the current simulation_prd.md'
    if entry and entry.get('status') in (STATUS_NOT_VERIFIED, STATUS_FAILED):
        # `failed` with NO artifact is a killed contract (137/143): no verdict, read like its exit 2.
        return 2, f'the last scan recorded {entry["id"]} as {entry["status"]} and left no verdict — {entry.get("evidence") or entry.get("reason") or "no evidence recorded"}'
    if entry:
        return 3, f'no scan_contract.json; the last scan recorded {entry["id"]} as {entry.get("status")} — {entry.get("reason") or entry.get("evidence") or ""}'.rstrip(' —')
    return 3, 'no scan_contract.json and no recorded contract run for this bench'


def cmd_contract_gate(args: argparse.Namespace) -> int:
    code, reason = contract_gate(resolve_workdir(args.workdir))
    print(f'contract-gate: {CONTRACT_GATE_STATES[code]} — {reason}')
    return code


#: `run-gate` states, by exit code.
RUN_GATE_STATES = {0: 'RUNNABLE', 1: 'NOT RUNNABLE', 3: 'NOT RECORDED'}


class RunGate(NamedTuple):
    """The run gate's verdict: its exit code, the reasons it prints under its state, the `finding:` lines, and —
    while it is NOT RUNNABLE — the one install block rendered from the resolver's plan (:func:`_install_block`; the
    agent's cited lines; the operator runs them)."""

    code: int
    reasons: list[str]
    findings: list[str]
    install_block: str = ''


def _install_block(held: list[GateReason], install: Mapping[str, Any], refused: set[str]) -> str:
    """The ONE copy-paste install block, rendered from the resolver's structured plan cut to the requirements whose
    measured gap still HOLDS (an overruled gap asks the operator for nothing); a name whose install line `validate`
    refuses (:func:`_install_refused`) reads as declaring none. '' when nothing is left, or the resolver wrote no plan.
    The four line shapes are the SDK's (`plurai.host_toolchain.render_install_block`): change both together."""
    keep = {r.finding.subject for r in held if r.finding.gate_id == gate_ledger.RUN_GATE_HOST_GAP}
    uncited = [str(n) for n in install.get('uncited') or () if n in keep]
    lines = []
    for line in install.get('lines') or ():
        names = [str(n) for n in line.get('names') or () if n in keep]
        uncited += [n for n in names if n in refused]
        if names := [n for n in names if n not in refused]:
            lines.append(f'{line.get("command")}  # {line.get("evidence")}: {", ".join(names)}')
    shown = {n for line in install.get('lines') or () for n in line.get('names') or () if n in keep} | set(uncited)
    interpreters = [
        f'# {i.get("name")} is not on PATH: {", ".join(needs)} need it — install it first'
        for i in install.get('interpreters') or ()
        if i.get('name') not in shown and (needs := [str(n) for n in i.get('for') or () if n in keep])
    ]
    if not (lines or uncited or interpreters):
        return ''
    return '\n'.join([str(install.get('head') or ''), *interpreters, *lines, *([f'# {", ".join(uncited)}: no install line declared'] if uncited else [])])


def shared_world_reason(workdir: Path) -> str | None:
    """The `shared_world` risk a RUNNABLE bench proceeds with, or None: the user opted into live writes on a world
    nothing resets per conversation (`verify_simulate.shared_world_writers`, the runner's own predicate). It holds
    nothing: the run carries it as a named risk and the suite caps at qualified."""
    simulate = workdir / 'simulate.py'
    if not simulate.is_file():
        return None
    path = str(plugin_root() / 'skills' / 'simulation-setup-python' / 'scripts')
    if path not in sys.path:
        sys.path.insert(0, path)
    import verify_simulate  # noqa: PLC0415 - the ONE reading of the opt-in, shared with the verifier

    writers = verify_simulate.shared_world_writers(simulate)
    if writers is None:
        return None
    return (f'shared world (`world_state.shared_world_ok: true`): Conversations share one world: {len(writers)} live writing tools '
            'with no reset between conversations; verdicts may depend on run order. The run proceeds with this named risk and '
            'the suite caps at qualified.')


def live_gate(doc: dict[str, Any], workdir: Path) -> list[GateReason]:
    """`plugin.run_gate.live_check_failed` (security) for each `kind: live` check over a tool the bench still runs for real
    that does not read its service when re-run NOW: credentials and services move between a scan and a run, so the
    recorded result is not trusted here. The remedy is the user's (the credential, the service), never an overrule."""
    real = {f'surfaces.tools[{t["name"]}]' for t in doc['surfaces']['tools'] if t.get('mode') == 'real'}
    out = []
    for check in doc['checks']:
        if check.get('kind') != 'live' or not (tools := [f[len('surfaces.tools['):-1] for f in check['verifies'] if f in real]):
            continue
        result = execute(check, doc, workdir, LIVE_TIMEOUT_S)
        if result['status'] == STATUS_VERIFIED:
            continue
        cause = str(result.get('cause') or result['status'])
        missing = [str(n) for n in result.get('missing') or []]
        remedy = ((f'set {", ".join(missing)} in the agent module .env, or ' if missing else '')
                  + 'fix the credential or start the service the error names, then re-run this gate; to mock these tools instead, re-run /simulation-scan-agent')
        said = str(result.get('evidence') or result.get('reason') or 'no evidence recorded')
        finding = gate_ledger.make_finding(gate_ledger.RUN_GATE_LIVE_CHECK_FAILED, subject=check['id'], evidence={'check_id': check['id'], 'cause': cause},
                                           observed={'tools': tools, 'missing': missing, 'evidence': said}, remedy=remedy, draft_ref=None)
        out.append(GateReason(finding, f'{check["id"]}: {", ".join(tools)} run for real and {"could not read their service" if cause == LIVE_FAILED else "could not be checked"}: "{said}" — {remedy}'))
    return out


def untyped_live_note(doc: dict[str, Any], workdir: Path) -> str | None:
    """The run gate's note for tools the bench runs for real that the agent map never typed with a `backend` (a map from
    before the fact): nothing checks their credential, so a dead one shows up only on the run's own setup line."""
    real = {str(t['name']) for t in doc['surfaces']['tools'] if t.get('mode') == 'real'}
    if untyped := sorted(n for n, backend in map_backends(workdir).items() if backend is None and n in real):
        return (f'no credential check covers {", ".join(untyped)}: the agent map does not type their `backend`; re-run '
                '/simulation-scan-agent to check them before a run')
    return None


def run_gate(workdir: Path) -> RunGate:
    """Can a run on this bench produce signal, before any spend.

    `not_runnable` over the scan's RECORDED results, never the stored `fields` block ⇒ 1, one reason per
    failed check that verifies the harness (a failure the run measures — the agent's raise, a silent turn — holds
    nothing; its recorded finding is printed). The gate's own findings (`scripts/gate_ledger.py`), settled
    against the plugin's ledger, ⇒ 1 while one holds: a blocking harness smoke that ran and reached no verdict
    (`plugin.run_gate.smoke_unproven`; `verify` records it and exits 3), an ungranted route smoke (security),
    an invalid host declaration (invariant), and a declared host requirement measured unusable where the agent
    runs it (`plugin.run_gate.host_gap`). A requirement nothing measured (`plugin.run_gate.host_unverified`)
    holds nothing. No readable record (or a refused one) and nothing holding ⇒ 3. Anything else ⇒ 0, and a reason
    names the `shared_world` risk the run proceeds with when the user opted into one (:func:`shared_world_reason`).
    Every `kind: live` check is re-run here, and one that does not read its service holds (:func:`live_gate`).
    """
    record = _read_json(verification_path(workdir))
    doc = _read_json(manifest_path(workdir))
    # the declaration is the subject's own evidence: re-checked here, never trusted to have met `validate`, and a
    # declaration that fails it runs no probe and no overrule lifts it
    invalid = host_declaration_findings(doc, workdir) if isinstance(doc, dict) else []
    host = host_declaration_gate(invalid)
    host_evaluated: tuple[str, ...] = (gate_ledger.RUN_GATE_HOST_DECLARATION_INVALID,)
    install: Mapping[str, Any] = {}
    if isinstance(doc, dict) and not invalid:
        toolchain = host_toolchain(doc, workdir)
        host, install = host_toolchain_gate(toolchain), toolchain.install
        host_evaluated = (gate_ledger.RUN_GATE_HOST_DECLARATION_INVALID, gate_ledger.RUN_GATE_HOST_GAP, gate_ledger.RUN_GATE_HOST_UNVERIFIED)
    refused = _install_refused(doc, workdir) if install else set()
    if not isinstance(record, dict) or record.get('refused') or not isinstance(doc, dict):
        held, lines = settle_run_gate(host, workdir, evaluated=host_evaluated)
        if held:
            return RunGate(1, [r.reason for r in held], lines, _install_block(held, install, refused))
        why = record.get('refused') if isinstance(record, dict) and record.get('refused') else f'no readable {VERIFICATION_FILENAME} and {MANIFEST_FILENAME}'
        return RunGate(3, [str(why)], lines)
    results = [r for r in record.get('results', []) if isinstance(r, dict) and 'id' in r and 'status' in r]
    own = [*ungranted_route(doc, workdir), *unproven_gate(doc, results, workdir), *host, *live_gate(doc, workdir)]
    evaluated = (gate_ledger.RUN_GATE_ROUTE_UNGRANTED, gate_ledger.RUN_GATE_SMOKE_UNPROVEN, gate_ledger.RUN_GATE_LIVE_CHECK_FAILED, *host_evaluated)
    held, lines = settle_run_gate(own, workdir, evaluated=evaluated)
    lines = [*recorded_finding_lines(doc, results), *lines]
    if not (stuck := [*map(run_gate_line, not_runnable(doc, results)), *(r.reason for r in held)]):
        shared = shared_world_reason(workdir)
        untyped = untyped_live_note(doc, workdir)
        return RunGate(0, ['no recorded check that verifies the harness failed or ran without a verdict', *([shared] if shared else []),
                           *([untyped] if untyped else [])], lines)
    return RunGate(1, stuck, lines, _install_block(held, install, refused))


def cmd_run_gate(args: argparse.Namespace) -> int:
    gate = run_gate(resolve_workdir(args.workdir))
    for reason in gate.reasons:
        print(f'run-gate: {RUN_GATE_STATES[gate.code]} — {reason}')
    for line in gate.findings:
        print(line)
    if gate.install_block:
        print(gate.install_block)
    return gate.code


def provider_key_state(workdir: Path) -> tuple[str, str, tuple[str, ...]]:
    """(``present`` | ``missing`` | ``undecided``, provider, the variable names) for the bench in ``workdir``.

    ``undecided``: no readable manifest, or one that names no known model provider. Presence only.
    """
    doc = _read_json(manifest_path(workdir))
    if not isinstance(doc, dict) or not isinstance(doc.get('harness'), dict):
        return 'undecided', '', ()
    provider, names = resolved_provider(doc, workdir)
    if not names:
        return 'undecided', provider, ()
    return ('present' if provider_key_present(doc, workdir) else 'missing'), provider, names


def cmd_provider_key(args: argparse.Namespace) -> int:
    """Is the agent's provider key reachable where the run reads it: 0 present, 1 missing, 3 undecided.

    The one owner of this check: no skill hands the model an inline shell version of it. Names only, never a value.
    """
    workdir = resolve_workdir(args.workdir)
    state, provider, names = provider_key_state(workdir)
    if state == 'undecided':
        print(f"provider-key: undecided — no readable {MANIFEST_FILENAME} naming a model provider; infer the provider from the agent's code and its .env")
        return 3
    wanted = ' or '.join(names)
    if state == 'present':
        print(f'provider-key: present — {provider}: {wanted} is set in the environment or the module .env, or the provider has a default credentials file')
        return 0
    doc = _read_json(manifest_path(workdir))
    env_file = env_file_label(doc, workdir) if isinstance(doc, dict) else None
    print(f'provider-key: missing — {provider}: {wanted} is set in neither the environment nor '
          f'{env_file if env_file else "an env file (none found; the module .env is where it goes)"}')
    return 1


#: A setting NAME as settings are spelled: upper case, at most 64 characters (a pasted lower-case or long token is refused).
_ENV_NAME_RE = re.compile(r'^[A-Z_][A-Z0-9_]{0,63}$')


def cmd_env_check(args: argparse.Namespace) -> int:
    """Where each setting the harness needs is found: 0 all found, 1 something missing, 2 an argument is not a name.

    Names and locations only: a value is never read out to print, and an argument that is not a variable name (a
    pasted value) is refused without being echoed. With no manifest the defaults apply (the environment, then the
    nearest `.env`). `--providers` adds one line per model provider: where its credentials are, or missing.
    """
    if not all(_ENV_NAME_RE.match(name) for name in args.names):
        print('env-check: pass variable NAMES only (upper-case letters, digits, _; at most 64); an argument was not one and is not shown', file=sys.stderr)
        return 2
    workdir = resolve_workdir(args.workdir)
    doc = _read_json(manifest_path(workdir))
    doc = doc if isinstance(doc, dict) else {}
    pairs = _env_file_pairs(doc, workdir)
    missing = False
    for name in args.names:
        where = name_source(doc, workdir, name, pairs)
        missing |= where is None
        print(f'{name}: found ({where})' if where else f'{name}: missing')
    if args.providers:
        for provider, where in provider_credentials(doc, workdir).items():
            if where and where.startswith('candidate '):
                print(f'provider {provider}: {where} — points at a credential, holds none; not found')
            else:
                print(f'provider {provider}: found ({where})' if where else f'provider {provider}: missing')
    env_file = env_file_label(doc, workdir)
    print(f'looked in: the environment Claude Code passes to commands, {env_file if env_file else "no env file (none declared, no .env found)"}'
          + (', the model providers\' default credential files' if args.providers else ''))
    return 1 if missing else 0


def cmd_coverage(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    doc = _read_json(manifest_path(workdir))
    if not isinstance(doc, dict):
        print(f'ERROR: no readable {MANIFEST_FILENAME} in {workdir}.', file=sys.stderr)
        return 2
    for field, ids in coverage(doc).items():
        print(f'{field:<45} {", ".join(ids) if ids else "(declared only — nothing checks this)"}')
    return 0


# ---------------------------------------------------------------------------
# SUITE STATE — the phase record a dead session resumes from
# ---------------------------------------------------------------------------
#
# WHY THIS FILE OWNS IT. The manifest is the bench's own declaration-plus-proof mechanism, and
# the suite's phase record is the same shape one layer up: a claim about what happened, written
# by the step that made it happen, read by whoever comes next. The alternative shipped for a year
# and is what §F2 of the register calls untyped IPC — one marker FILE per id (`.scenario_set_id`,
# `.eval_set_id`, `.corpus_id`), each written by a different skill, none carrying WHEN it was
# written, by which phase, at which round, or against which eval revision. A session that died at
# 21:50 with its final set landing at 21:57 left a `.scenario_set_id` naming the OLD set and no
# way for the next session to know a newer one existed; four suites at 3.5-4.3 h were re-entered
# from scratch or not at all.
#
# NOT IN THE MANIFEST ITSELF, deliberately. `bench_manifest.json` is COMMITTED to the customer's
# repo (it is the bench's declaration, like `plurai.config.yaml`); a phase counter is per-RUN
# state and belongs beside `verification.json` under the gitignored `artifacts/`.
#
# The markers are NOT written from here and are not retired by this commit — every one of them
# has a declared writer in another skill, and a second writer is exactly the divergence this
# record exists to end. `phase resume` READS them and REPORTS disagreement, which is the step
# that makes retiring them safe: until the typed record and the markers have been observed to
# agree on real runs, preferring one silently is a guess.

SUITE_STATE_FILENAME = 'suite_state.json'
SUITE_STATE_SCHEMA_ID = 'plurai-suite-state/1'

#: The phases a suite passes through, in order. CLOSED: a resume point is only useful if the
#: reader and the writer agree what comes next, and a free-text phase makes "where do I restart"
#: a judgement call again. `critic` and `edit` repeat — the round counter is what separates them.
#:
#: `corpus` BEFORE `world`, which is the order the recipe actually builds in and the reverse of
#: what this tuple declared for its first week. The grounding gate is Step 0; Step 0c stands the
#: world up after it, and it has to: `/build-rag-corpus` rewrites `simulation_prd.md` (its Step 6.5
#: reconciliation) and env-gen reads that PRD, so a world generated first is generated from an
#: unreconciled brief (the same ordering `hooks/tests/test_env_step_ordering.py` pins on the scan
#: side). Declared the other way, a session that died after Step 0c resumed at `corpus` — "world
#: landed" — and re-bought a corpus build it already had, with `furthest_landed` explaining the
#: backwards jump away as "normal in the critic/edit rounds". It is not normal there; those two
#: phases run once.
#:
#: EVERY PHASE HERE IS RECORDED BY THE SUITE RECIPE — with one deliberate exception. `scan` is
#: `/simulation-scan-agent`'s, a command that runs BEFORE the suite; the suite must not record a
#: phase it did not perform, so `resume` on a suite workdir simply never sees it (which is
#: harmless: `scan` is the first phase, and its successor is where a fresh suite starts anyway).
#: A phase declared and never recorded by its owner is not harmless — `final_evals` and `render`
#: were exactly that, so `resume` after `final_scenarios` named a phase nothing could land, and a
#: session dying in Step 4 or Step 5 was sent back into the eval audit step. Add a phase here
#: only together with the recipe line that records it.
SUITE_PHASES = (
    'scan',
    'corpus',
    'world',
    'scenarios',
    'evals',
    'qualification',
    'critic',
    'edit',
    'final_scenarios',
    'final_evals',
    'render',
    'report',
)

#: What comes NEXT after each phase — the only thing that makes `SUITE_PHASES`'s declared order
#: load-bearing rather than an argparse `choices` list. `resume` used to print "the phase after
#: <X>", which is not a resume point: the reader has to hold the order in their head, and the one
#: place the order is written down is here.
#:
#: Linear, and the two phases that can branch are annotated by `_phase_resume`. After `edit` the
#: loop verifies the repairs mechanically, audits what is still red, and lands — so `edit` names
#: `final_scenarios`, the converging case. The ONE reading that re-enters `qualification` instead
#: is a critic that reported RUN INVALID (the run did not measure the agent, so the repairs need a
#: re-run and a second full critic); this record does not carry that verdict, so the note says so.
#:
#: `critic` is the mirror: a DIRTY critic hands off to `edit`, which is what this map says; a CLEAN
#: one skips the repair round and leaves for `final_scenarios`. Neither verdict is in the record,
#: so both notes tell the reader what to read before choosing.
NEXT_PHASE: dict[str, str] = dict(zip(SUITE_PHASES, SUITE_PHASES[1:]))

#: An operator's answer to a gate this phase asked, TYPED so a later gate reads it without parsing `--note`
#: (loop-internal free text): the grounding gate's no-corpus answer, which the knowledge-blind check reads.
GATE_ANSWERS = ('proceed-without-corpus',)
#: The finding kind that backs a recorded answer: a sanctioned ask that came back answered
#: (`_run_driver.ASK_ANSWERED`). Stamped on the phase record, and the one thing its consumer reads.
ANSWER_BACKING = 'run_driver_ask_answered'

#: What a phase record can say about itself. `started` without a matching `landed` is what a
#: dead session leaves behind, and it is the single most useful thing the next one can read.
PHASE_STATUSES = ('started', 'landed', 'failed')

#: The record `phase reset` appends: the UNFINISHED build before it is set aside, and the next suite run is
#: a new build. Written by what makes that build stale — a scan that landed (`simulate_plan.py fingerprint`,
#: the scan's last call) and /simulate feedback that redoes the suite (`flow-window.sh simulate rewind`).
#: Not in `SUITE_PHASES`: no step of the suite performs it, and `phase record` cannot write it.
RESET_PHASE = 'reset'

#: The suite's hand-off: the phase it records last, right before `lane_pin.py anchor` writes `suite_anchor.json`.
HANDOFF_PHASE = 'report'
#: Records after a hand-off that still belong to the build handed off: `/simulation-run`'s top-up re-plants the
#: shipped set (`final_scenarios`). Any other record after a hand-off is a later build.
SAME_BUILD_AFTER_HANDOFF = frozenset({'final_scenarios'})


def handed_off(phases: list[Any]) -> bool:
    """THE finished rule: the record's current build handed off — its last `report landed` is followed only by the
    run's top-up. `reset_suite`, `phase resume`, `open_epoch` and `/simulate`'s start question all read this one."""
    records = [p for p in phases if isinstance(p, dict)]
    marks = [i for i, p in enumerate(records) if (p.get('phase'), p.get('status')) == (HANDOFF_PHASE, 'landed')]
    return bool(marks) and all(p.get('phase') in SAME_BUILD_AFTER_HANDOFF for p in records[marks[-1] + 1:])


#: The suite phases that hand the report its files: `landed` on either one is refused while `deliverables.missing`
#: names a file the page reads (the suite stage's rows), and `report` also needs a `render landed` of this set.
DELIVERED_PHASES = ('render', HANDOFF_PHASE)
#: What a `render landed` must come after for `report landed` to count it: the last re-plant of the final set or reset.
RENDER_AFTER = frozenset({'final_scenarios', RESET_PHASE})
#: `phase resume`'s exit when the build handed off without files the report reads.
EXIT_DELIVERABLE_GAP = 5


def unrendered(phases: list[Any]) -> bool:
    """No `render landed` after the last `final_scenarios` / reset record (anywhere, when there is neither)."""
    records = [p for p in phases if isinstance(p, dict)]
    cut = max((i for i, p in enumerate(records) if p.get('phase') in RENDER_AFTER), default=-1)
    return not any((p.get('phase'), p.get('status')) == ('render', 'landed') for p in records[cut + 1:])


def undelivered(workdir: Path, phase: str, phases: list[Any]) -> list[str]:
    """Why `<phase> landed` is not a hand-off of the report's files: one line per gap, [] when it is."""
    lines = [f'MISSING {path} — {step}' for path, step in deliverables.missing(workdir, 'suite')]
    if phase == HANDOFF_PHASE and unrendered(phases):
        lines.append('MISSING render landed after the last final_scenarios / reset — the suite\'s Step 5 '
                     '(build, render, then `phase record --phase render --status landed`)')
    return lines


def open_build(phases: list[Any]) -> dict[str, Any] | None:
    """The last record of a build left UNFINISHED, or None: no record, a reset last, or a build that handed off."""
    records = [p for p in phases if isinstance(p, dict)]
    if not records or records[-1].get('phase') == RESET_PHASE or handed_off(records):
        return None
    return records[-1]

#: The ids a phase can hand forward, and the marker file each one MIRRORS today. The mapping is
#: the whole point of `phase resume`: it is what lets the typed record and the untyped IPC be
#: compared instead of trusted in turn.
SUITE_IDS = {
    'scenario_set': '.scenario_set_id',
    'eval_set': '.eval_set_id',
    'corpus': '.corpus_id',
    'env': '.env_id',
    'run_upload': None,
}


def suite_state_path(workdir: Path) -> Path:
    return workdir / 'artifacts' / SUITE_STATE_FILENAME


def _suite_state(workdir: Path) -> tuple[dict[str, Any] | None, str | None]:
    """The phase record, or `(None, why)` for a file this build must NOT read as empty.

    ABSENCE IS A VALUE, exactly as in `sha256_path`. A workdir with no record has recorded
    nothing and reads as an empty doc. A record that is TRUNCATED, unreadable, or written to a
    schema this build does not know has recorded SOMETHING — and collapsing those three into a
    fresh empty doc is how an in-flight suite becomes silently unresumable: `resume` prints "no
    phase has been recorded" over a suite that recorded plenty, and the next `record` overwrites
    the file it could not read. `SUITE_STATE_SCHEMA_ID` is versioned, so the bump that does that
    to every live suite is one commit away.

    Same posture `_invalidate` gives `verification.json`: no verdict is a refusal, never an old
    verdict and never a blank one.
    """
    path = suite_state_path(workdir)
    if not path.exists():
        return {'schema': SUITE_STATE_SCHEMA_ID, 'phases': [], 'ids': {}, 'rounds': {}}, None
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError as exc:
        return None, f'it cannot be read ({exc})'
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        return None, f'it is not readable JSON ({exc}) — a truncated or half-written record'
    if not isinstance(doc, dict):
        return None, f'its top level is a {type(doc).__name__}, not the record object'
    if doc.get('schema') != SUITE_STATE_SCHEMA_ID:
        return None, (f'it declares schema {doc.get("schema")!r} and this plugin build reads '
                      f'{SUITE_STATE_SCHEMA_ID!r} — a record written by a different (probably newer) plugin')
    doc.setdefault('phases', [])
    doc.setdefault('ids', {})
    doc.setdefault('rounds', {})
    # THE CONTENTS, not just the wrapper. Refusing a non-object top level and then trusting
    # whatever `phases` holds left the same hole one level in: `_phase_resume` keeps only the
    # dict elements, so a `phases` that is a MAPPING, or a list of strings, filtered down to
    # nothing and printed "no phase has been recorded" — which SKILL.md Step 0 licenses as a
    # fresh build, i.e. re-enter from the top and overwrite the record you could not read. That
    # is the exact degradation the refusal above exists to prevent, reached by a different door.
    # `ids` and `rounds` are checked for the same reason plus a blunter one: `record` does
    # `doc['ids'].update(...)` and `resume` does `rounds.items()`, so the wrong type here is an
    # unhandled AttributeError over a suite that recorded plenty.
    for key in ('ids', 'rounds'):
        if not isinstance(doc[key], dict):
            return None, f'its {key!r} is a {type(doc[key]).__name__}, not the mapping this record keeps'
    if not isinstance(doc['phases'], list):
        return None, f'its \'phases\' is a {type(doc["phases"]).__name__}, not the list of records'
    strays = [type(p).__name__ for p in doc['phases'] if not isinstance(p, dict)]
    if strays:
        return None, f'{len(strays)} of its \'phases\' entries are not records ({", ".join(sorted(set(strays)))})'
    return doc, None


def _refuse_suite_state(workdir: Path, why: str) -> int:
    """The one refusal both phase subcommands print, so neither can quietly degrade to empty."""
    print(
        f'ERROR: {suite_state_path(workdir)} exists but {why}. NOTHING is read and nothing is '
        'written: a phase record that reads as empty is worse than no record at all — `resume` '
        'would print "no phase has been recorded" over a suite that recorded plenty, and the next '
        '`record` would overwrite what this build could not read. It is per-run state under '
        'artifacts/: read it, then either move it aside deliberately or run a plugin build that '
        'reads its schema.',
        file=sys.stderr,
    )
    return 2


def _marker(workdir: Path, name: str) -> str | None:
    try:
        value = (workdir / name).read_text(encoding='utf-8').strip()
    except OSError:
        return None
    return value or None


def cmd_phase(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    if args.phase_command == 'record':
        return _phase_record(args, workdir)
    if args.phase_command == 'job':
        return _phase_job(workdir, args.phase, args.key)
    if args.phase_command == 'reset':
        doc, unreadable = _suite_state(workdir)
        if doc is None:
            return _refuse_suite_state(workdir, str(unreadable))
        print(reset_suite(workdir, args.note) or 'suite reset: nothing to reset — no unfinished build is on record.')
        return 0
    return _phase_resume(workdir)


def _open_jobs(phases: list[dict[str, Any]], phase: str) -> dict[str | None, str]:
    """The backend jobs `phase`'s OPEN attempt started, as {key: the LATEST job recorded under it}.

    The open attempt is every `started` record since the phase's last `landed` / `failed` record and
    since the last `RESET_PHASE` record: a closed attempt's jobs are history, never adopted, and a
    set-aside build's jobs are never the next build's. The KEY (`--key`; None for a phase that starts
    one job) keeps one phase's jobs apart — one corpus per knowledge base — so a second base never
    adopts the first one's build. The latest job per key wins: a deliberate re-call, or a restart after
    a failed job, records a new id under the same key and supersedes the one before it.
    """
    jobs: dict[str | None, str] = {}
    for record in phases:
        if record.get('phase') == RESET_PHASE:
            jobs = {}
            continue
        if record.get('phase') != phase:
            continue
        if record.get('status') in ('landed', 'failed'):
            jobs = {}
        elif record.get('status') == 'started' and isinstance(record.get('job'), str):
            key = record.get('key') if isinstance(record.get('key'), str) else None
            jobs.pop(key, None)  # re-insert: the dict's order is the order the jobs were last started
            jobs[key] = record['job']
    return jobs


#: Reads of `job_status` before an unreadable answer (backend unreachable, no envelope) counts as
#: unknown, and the waits between them. A blip must not buy a second job; a backend that stays down
#: does fall back to starting anew. `failed` is an answer, never retried.
JOB_STATUS_ATTEMPTS = 3
JOB_STATUS_BACKOFF_S = (2.0, 5.0)


def _read_job_status(job_id: str) -> tuple[str | None, str]:
    """ONE read of `job_status(id=<job_id>)`'s `status` over plain HTTP (`mcp_call.py`), or (None, why)."""
    try:
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / 'mcp_call.py'), 'job_status', json.dumps({'id': job_id})],
            capture_output=True, text=True, **utf8_child_text(), timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f'job_status could not be called ({type(exc).__name__})'
    if proc.returncode != 0:
        return None, f'job_status exited {proc.returncode}'
    try:
        status = json.loads(proc.stdout).get('status')
    except (ValueError, AttributeError):
        return None, 'job_status answered no readable envelope'
    return (status, '') if isinstance(status, str) and status else (None, 'job_status answered no status')


def _job_status(job_id: str) -> tuple[str | None, str]:
    """`job_status`'s answer, read up to :data:`JOB_STATUS_ATTEMPTS` times while it cannot be read at all."""
    status, why = _read_job_status(job_id)
    for wait in JOB_STATUS_BACKOFF_S[: JOB_STATUS_ATTEMPTS - 1]:
        if status is not None:
            break
        time.sleep(wait)
        status, why = _read_job_status(job_id)
    if status is None:
        why = f'{why}, after {JOB_STATUS_ATTEMPTS} reads'
    return status, why


def _phase_job(workdir: Path, phase: str, key: str | None = None) -> int:
    """Adopt, or start: the deterministic half of "no re-paid job on resume".

    A phase that starts a backend job records its id on a `started` record the moment the start
    call returns (`phase record --status started --job <id> [--key <k>]`). Re-entering that phase, this
    polls the latest job of each key its open attempt started (only `key`'s, when given) and prints ONE
    line per job, each an instruction on its own:

    * `adopt:` — queued or running (wait on it) or done (read its result): no new job for that key;
    * `start:` — failed (an id the backend does not know reads as failed too), or still unreadable
      after the retries: start a new job for that key.

    Exit 0: every job is adopted. Exit 3: at least one `start:` line, or no job is recorded — start
    those, and adopt the `adopt:` lines beside them. Exit 2: the record cannot be read.
    """
    doc, unreadable = _suite_state(workdir)
    if doc is None:
        return _refuse_suite_state(workdir, str(unreadable))
    jobs = _open_jobs(doc['phases'], phase)
    if key is not None:
        jobs = {k: v for k, v in jobs.items() if k == key}
    named = f'{phase} ({key})' if key is not None else phase
    if not jobs:
        print(f'start: no {named} job is open in the phase record — start one, and record its id when the call returns')
        return 3
    restart = 0
    for job_key, job in jobs.items():
        what = f'{phase} ({job_key})' if job_key is not None else phase
        status, why = _job_status(job)
        if status in ('queued', 'running'):
            print(f'adopt: {job} {status} — do not start a new {what} job; wait on this one (poll_job.sh {job})')
        elif status == 'done':
            print(f'adopt: {job} done — do not start a new {what} job; read its result (job_status) and continue past the wait')
        else:
            print(f'start: {job} {status or "unknown"}{f" ({why})" if why else ""} — start a new {what} job')
            restart += 1
    return 3 if restart else 0


def reset_suite(workdir: Path, note: str) -> str | None:
    """Append a `RESET_PHASE` record when an UNFINISHED build is on record; the line to say, or None.

    Nothing to reset: no record (no build), a finished build (`handed_off`: the next suite run is already
    new), or a reset already last. A record that cannot be read is left alone —
    never written over (`_suite_state`). The ids stay: their markers are still on disk, and `resume`
    compares the two. The round counters go: they bound the build that is set aside, not the next one.
    """
    doc, _unreadable = _suite_state(workdir)
    last = open_build(doc['phases']) if doc is not None else None
    if last is None:
        return None
    record = {'phase': RESET_PHASE, 'status': 'landed', 'at': _now(), 'plugin_version': _plugin_version(), 'note': note}
    doc['phases'].append(record)
    doc['rounds'] = {}
    doc['updated_at'] = record['at']
    _write_json(suite_state_path(workdir), doc)
    return (f"suite reset: the unfinished build (stopped at {last.get('phase')} {last.get('status')}) is set aside — "
            f'the next suite run is a new build ({note}).')


def _phase_record(args: argparse.Namespace, workdir: Path) -> int:
    """Append ONE phase record, written by the step that produced it.

    Ids are recorded HERE and not inferred later: "the final set id is written by the job that
    produced it" is the acceptance criterion, and the failure it names is a set that landed
    seven minutes after its session died and was never consumed because nothing but that session
    knew it existed.
    """
    ids: dict[str, str] = {}
    for pair in args.set or []:
        kind, _, value = pair.partition('=')
        if kind not in SUITE_IDS or not value:
            print(f'ERROR: --set expects <kind>=<id> with kind in {", ".join(sorted(SUITE_IDS))}; got {pair!r}.', file=sys.stderr)
            return 2
        ids[kind] = value
    unbuilt = _unrecorded_landing(workdir, args.phase, ids.get(args.phase)) if args.status == 'landed' else None
    if unbuilt is not None:
        print(f'ERROR: --set {args.phase}={ids[args.phase]} {unbuilt}. Nothing is written: only an id its builder recorded on '
              'disk is a landed outcome — record the phase once those markers are written.', file=sys.stderr)
        return 2
    if args.key and not args.job:
        print('ERROR: --key names which of a phase\'s jobs `--job` is (one corpus per knowledge base); it needs --job. '
              'Nothing is written.', file=sys.stderr)
        return 2
    if args.job and args.status != 'started':
        print(f'ERROR: --job records the backend job a phase STARTED; it goes on a `--status started` record, not '
              f'`{args.status}`. Nothing is written.', file=sys.stderr)
        return 2
    unbacked = _unbacked_answer(workdir, args.phase) if args.answer else None
    if unbacked is not None:
        print(f'ERROR: --answer {args.answer} records an OPERATOR\'s answer, and {unbacked}. Nothing is written. Record '
              'the phase without --answer (only a landed id resolves the gate); whether a suite that tests no knowledge '
              'ships is decided at the knowledge-blind delivery check.', file=sys.stderr)
        return 2
    doc, unreadable = _suite_state(workdir)
    if doc is None:
        return _refuse_suite_state(workdir, str(unreadable))
    # `render` and `report` hand the report page its files; a landing with any of them absent is a failed
    # step, never a landed phase: the page shows what the workdir holds and says nothing of what it lacks.
    gaps = undelivered(workdir, args.phase, doc['phases']) if args.status == 'landed' and args.phase in DELIVERED_PHASES else []
    if gaps:
        print(f'ERROR: `{args.phase} landed` is refused — the report page reads files this build has not written. '
              'Nothing is written. Write each one with the step named beside it, then record the phase again:', file=sys.stderr)
        for line in gaps:
            print(f'  {line}', file=sys.stderr)
        return 2
    unrendered = _unfinished_render(workdir) if (args.phase, args.status) == ('render', 'landed') else None
    if unrendered is not None:
        print(f'ERROR: the render did not finish — {unrendered}. Nothing is written: fix what `render_suite.py` reported '
              'and run it again before this phase lands.', file=sys.stderr)
        return 2
    record: dict[str, Any] = {'phase': args.phase, 'status': args.status, 'at': _now(), 'plugin_version': _plugin_version()}
    if args.round is not None:
        record['round'] = args.round
        doc['rounds'][args.phase] = args.round
    if args.eval_revision:
        record['eval_revision'] = args.eval_revision
        doc['ids']['eval_revision'] = args.eval_revision
    if args.note:
        record['note'] = args.note
    if args.job:
        record['job'] = args.job
        if args.key:
            record['key'] = args.key
    if args.answer:
        record['answer'] = args.answer
        # The consumer (the knowledge-blind check) counts an answer only with this stamp.
        record['answer_backed_by'] = ANSWER_BACKING
    # A phase that asked for M and landed N is a PARTIAL success; "landed" alone would read a set
    # that shipped 2 of 3 exactly like one that shipped 3 of 3. When a slot is lost to an
    # unproductive repair round and the loop carries on, the lost slot may be the one holding the
    # memory probe, and the eval set built on it refuses: the first sign of the shortfall would be
    # that refusal, two jobs downstream.
    #
    # TYPED, not folded into `--note`: the note is loop-internal free text, and a consumer deciding
    # whether to re-draw a slot would have to parse English to find the numbers. `lost` is DERIVED
    # rather than a fourth flag, so the arithmetic cannot disagree with itself.
    if args.landed is not None and args.requested is not None:
        record['landed'] = args.landed
        record['requested'] = args.requested
        record['lost'] = max(args.requested - args.landed, 0)
        if args.lost_kind:
            record['lost_kind'] = args.lost_kind
        # A TOP-UP lands the same "3 of 3" as a fresh batch while being a different measurement:
        # most of it is scenarios an earlier batch already produced and the loop has already
        # judged. Without the split, the top-up of a 2-of-3 set records a second `scenarios landed`
        # entry indistinguishable from the first, and a resumed session reads a clean 3-of-3 over a
        # batch in which one slot was drawn and two were carried. `drawn` is
        # DERIVED for the same reason `lost` is — the arithmetic cannot disagree with its counts.
        if args.adopted is not None:
            record['adopted'] = args.adopted
            record['drawn'] = max(args.landed - args.adopted, 0)
    if ids:
        record['ids'] = ids
        doc['ids'].update(ids)
    doc['phases'].append(record)
    doc['updated_at'] = record['at']
    _write_json(suite_state_path(workdir), doc)
    if args.status == 'landed':
        _record_gate_outcome(workdir, args.phase, f'{args.phase}:{ids[args.phase]}' if args.phase in ids else None)
    # The STATUS LINE carries it too, because the operator reads this line and not suite_state.json.
    # "landed" alone on a short batch is the sentence that hides the shortfall.
    slots = ''
    if 'landed' in record:
        slots = f" {record['landed']} of {record['requested']}"
        if record['lost']:
            slots += f"; {record['lost']} lost" + (f" ({record['lost_kind']})" if record.get('lost_kind') else '')
        if 'adopted' in record:
            slots += f"; {record['adopted']} adopted, {record['drawn']} newly drawn"
    print(
        f'{args.phase} {args.status}'
        + slots
        + (f' round={args.round}' if args.round is not None else '')
        + (f' ids={ids}' if ids else '')
        + (f' job={args.job}' if args.job else '')
        + (f' key={args.key}' if args.key else '')
    )
    return 0


#: The stamp `render_suite.py` leaves when a render ran to the end (`render_suite.RENDER_STAMP`): the
#: sha256 of the `suite.json` it wrote.
RENDER_STAMP = '.render_ok'


def _unfinished_render(workdir: Path) -> str | None:
    """Why no finished render stands over the current ``suite.json``, or None when one does."""
    art = workdir / 'artifacts'
    try:
        stamped = (art / RENDER_STAMP).read_text(encoding='utf-8').strip()
    except OSError:
        return (f'{art / RENDER_STAMP} is absent, so `render_suite.py` has not run to the end here '
                '(it raised, or it has not run)')
    try:
        current = hashlib.sha256((art / 'suite.json').read_bytes()).hexdigest()
    except OSError as exc:
        return f'{art / "suite.json"} is unreadable ({type(exc).__name__})'
    if stamped != current:
        return f'{art / "suite.json"} changed after the last finished render'
    return None


def _unbacked_answer(workdir: Path, phase: str) -> str | None:
    """Why ``--answer`` on ``phase`` is NOT an operator's answer, or None when a person's answer backs it.

    The answer is backed only when the latest event at the gate this phase carries out
    (`_run_driver.GATE_OUTCOME_PHASE`) is `run_driver_ask_answered` — a sanctioned ask that came back
    answered. A default, a STOP, an unanswered ask, or no event at all is no person's answer.
    """
    sys.path.insert(0, str(plugin_root() / 'hooks'))
    import _run_driver  # noqa: PLC0415 - hooks/ is only on sys.path here

    gates = [gate for gate, outcome in _run_driver.GATE_OUTCOME_PHASE.items() if outcome == phase]
    if not gates:
        return f'the {phase!r} phase carries out no gate'
    bases = _run_driver.marker_bases(workdir)
    for gate in gates:
        latest = _run_driver.latest_gate_event(bases, gate)
        if latest is None:
            return f'no answer to `{gate}` is on this run\'s record'
        if latest.get('kind') == ANSWER_BACKING:
            continue
        if latest.get('command') == _run_driver.FALLBACK_COMMAND:
            # Filed with no flow window, so possibly an earlier build's row: it is not named as the cause.
            return f'no person\'s answer to `{gate}` is on this session\'s record'
        return f'`{gate}` was last met by `{latest.get("kind")}`, not by a person\'s answer'
    return None


#: The one reader of what a phase's builder recorded on disk, for the phases whose landed id
#: resolves a gate (`_run_driver.GATE_OUTCOME_PHASE`).
RECORDED_IDS = {'corpus': corpus_coordinate}


def _unrecorded_landing(workdir: Path, phase: str, landed: str | None) -> str | None:
    """Why ``landed`` is not an id this phase's builder recorded on disk, or None when it is (or none applies)."""
    reader = RECORDED_IDS.get(phase)
    if landed is None or reader is None:
        return None
    recorded = reader(workdir)
    if recorded is not None and landed in recorded:
        return None
    held = ', '.join(sorted(recorded)) if recorded else 'nothing readable'
    return f'names a {phase} no marker on disk records (the {phase} markers hold: {held})'


def _record_gate_outcome(workdir: Path, phase: str, applied: str | None) -> None:
    """File `run_driver_gate_resolved` for each gate whose applied decision IS this landed phase
    (`_run_driver.GATE_OUTCOME_PHASE`), so the run record replaces that gate's earlier default or
    stop with what was actually done. Only a LANDED id is an applied outcome; an ``--answer`` never is.
    Written by this producer, durably under `artifacts/`."""
    if applied is None:
        return
    sys.path.insert(0, str(plugin_root() / 'hooks'))
    import _run_driver  # noqa: PLC0415 - hooks/ is only on sys.path here

    art = workdir / 'artifacts'
    for gate, outcome_phase in _run_driver.GATE_OUTCOME_PHASE.items():
        if outcome_phase == phase:
            command = _run_driver.flow_command(_run_driver.marker_bases(workdir))
            _run_driver.record_durably(_run_driver.GATE_RESOLVED, session_base=art, art=art, gate=gate, command=command, applied=applied)


def _phase_resume(workdir: Path) -> int:
    """Where a dead session picks up, and what it must not trust.

    Prints the last record, the last phase that landed, the phase to re-enter (a NAME — see
    `_resume_lines`), the round counters and ids, and last the reason this is not just a counter:
    any marker file that DISAGREES with the typed record. A marker naming an older set than the
    phase record is the 21:57 orphan; a marker naming a NEWER one means something wrote outside
    this record; a marker no phase recorded at all means an id landed and nothing claims it.
    None is silently preferred here, and any of them exits 4 (see `_report_marker_disagreements`).

    A build that handed off (`handed_off`, unchanged) but lacks a file the report reads (`deliverables.missing`,
    the suite's rows) prints one `deliverable_gap: <path> — <step>` line per file and `resume_phase: render`, and
    exits 5: re-run Steps 4–6 only. 5 wins over 4; the disagreement lines still print.

    Exit: 0 the record and the markers agree · 4 they disagree · 5 the handed-off build left the report a gap ·
    2 the record cannot be read.
    """
    doc, unreadable = _suite_state(workdir)
    if doc is None:
        return _refuse_suite_state(workdir, str(unreadable))
    phases: list[dict[str, Any]] = [p for p in doc.get('phases', []) if isinstance(p, dict)]
    # Only the build after the last RESET is resumable; what precedes it was set aside.
    cut = max((i for i, p in enumerate(phases) if p.get('phase') == RESET_PHASE), default=None)
    if cut is not None:
        reset, phases = phases[cut], phases[cut + 1:]
        if not phases:
            print(f'resume_phase: none — the earlier build was reset at {reset.get("at")} ({reset.get("note")}); this is a new build')
            return _report_marker_disagreements(workdir, doc)
    if not phases:
        print('resume_phase: none — no phase has been recorded for this workdir')
        # …which is a fresh build ONLY if no id is lying around either. A workdir carrying a
        # `.scenario_set_id` nothing recorded is the shipped state this record replaces, not a
        # first run, and reading it as one is how a landed set gets abandoned.
        return _report_marker_disagreements(workdir, doc)
    last = phases[-1]
    landed = [p for p in phases if p.get('status') == 'landed']
    print(f'last_record: {last.get("phase")} {last.get("status")} at {last.get("at")}')
    print(f'last_landed: {landed[-1].get("phase") if landed else "none"}')
    # A build that handed off is over even when the run's top-up recorded after it: the next suite run is new.
    finished = [f'resume_phase: none — {HANDOFF_PHASE} landed, which is the last phase of the suite'
                + ('' if last.get('phase') == HANDOFF_PHASE else f' (then the run\'s top-up: {last.get("phase")} {last.get("status")})')]
    # …unless it handed off without files the report reads: `handed_off` stays the record's rule, so every
    # other reader still takes the build as finished; only this resume names the gap and the steps that repair it.
    gaps = deliverables.missing(workdir, 'suite') if handed_off(phases) else []
    if gaps:
        finished = [f'deliverable_gap: {path} — {step}' for path, step in gaps]
        finished.append(f'resume_phase: render — {HANDOFF_PHASE} landed without the files above; re-run Steps 4–6 only '
                        '(Step 4\'s meta, Step 5\'s bundle, build and render, `render landed`, then Step 6)')
    for line in finished if handed_off(phases) else _resume_lines(last):
        print(line)
    if last.get('status') == 'started':
        for key, job in _open_jobs(phases, str(last.get('phase'))).items():
            keyed = f' --key {key}' if key is not None else ''
            print(f'resume_job: {job} — {last.get("phase")} started this backend job{f" for {key}" if key is not None else ""}; '
                  f'before starting a new one, `phase job --phase {last.get("phase")}{keyed}` adopts it when it is running or landed')
    # The FURTHEST phase any record reached, when it is not the last one. The loop legitimately
    # goes backwards — a critic round re-enters `evals` or `scenarios` — so a backwards record is
    # not an error to refuse; but a resume point read alone would then lose the fact that a later
    # phase had already landed, which is the difference between re-running one phase and
    # re-running the tail of the suite.
    #
    # Compared by ORDER, never by record identity. `is not landed[-1]` asks whether these are the
    # same RECORD, and the critic/edit rounds land the same phases again every round: after two
    # rounds `max` returns round 1's `edit` and `landed[-1]` is round 2's, two different objects
    # naming one phase — so a converging loop printed `last_landed: edit` and `furthest_landed:
    # edit` on the same screen, contradicting itself over a run where nothing went backwards. The
    # line's whole claim is "a LATER phase landed before the last record"; that is a question
    # about position, and it is asked here as one. Both sides must be placeable: a landed phase
    # the vocabulary does not know cannot be said to be behind anything.
    order = {phase: i for i, phase in enumerate(SUITE_PHASES)}
    furthest = max((p for p in landed if p.get('phase') in order), key=lambda p: order[str(p.get('phase'))], default=None)
    last_landed_rank = order.get(str(landed[-1].get('phase'))) if landed else None
    if furthest is not None and last_landed_rank is not None and order[str(furthest.get('phase'))] > last_landed_rank:
        print(f'furthest_landed: {furthest.get("phase")} — a later phase landed BEFORE the last record; the loop '
              're-entered an earlier one (normal in the critic/edit rounds). The resume point is the last record, not this.')
    for phase, count in sorted((doc.get('rounds') or {}).items()):
        print(f'round[{phase}]: {count}')
    for key, value in sorted((doc.get('ids') or {}).items()):
        print(f'id[{key}]: {value}')
    disagreed = _report_marker_disagreements(workdir, doc)
    return EXIT_DELIVERABLE_GAP if gaps else disagreed


def _resume_lines(last: dict[str, Any]) -> list[str]:
    """`resume_phase: <a phase>` — a NAME, never "the phase after <X>".

    The reader of this line is a session deciding where to re-enter a 4-hour loop, and "the phase
    after edit" hands the ordering back to whoever is reading — which is what `SUITE_PHASES` was
    declared "in order" to stop. Where the successor is genuinely ambiguous the ambiguity is
    STATED (see `NEXT_PHASE`) rather than resolved by picking one.
    """
    phase = str(last.get('phase') or '')
    if last.get('status') == 'started':
        return [f'resume_phase: {phase} — it started and never landed; re-enter it']
    if last.get('status') == 'failed':
        return [f'resume_phase: {phase} — its last record is FAILED; re-enter it and read why before moving on']
    nxt = NEXT_PHASE.get(phase)
    if nxt is None:
        return [f'resume_phase: none — {phase} landed, which is the last phase of the suite']
    lines = [f'resume_phase: {nxt} — {phase} landed']
    if phase == 'edit':
        lines.append('  (after `edit` the loop verifies the repairs, audits what is still red, and lands — the '
                     'converging case. It re-enters `qualification` ONLY when the critic reported RUN INVALID; this '
                     'record does not carry that verdict, so read the last critic note before choosing.)')
    if phase == 'critic':
        lines.append('  (`edit` is where a DIRTY critic goes. A CLEAN critic is the loop\'s exit and leaves for '
                     '`final_scenarios` — the converging case, which every finished suite passes through. This record '
                     'does not carry the verdict either, so read the last critic report before choosing.)')
    return lines


def _report_marker_disagreements(workdir: Path, doc: dict[str, Any]) -> int:
    """Every marker file that disagrees with the typed record — and exit 4 when one does.

    FOUR quadrants, not two. The pair (marker on disk, id recorded) has four states and three of
    them are worth printing; the `on_disk and not recorded` one used to have NO branch at all,
    which is the likeliest of the three: the marker is written the moment a job returns, while
    `phase record` is a separate later call, so a session that dies between them leaves exactly
    this shape — an id on disk that no phase claims. Driving the shipped flow over the orphan
    state this whole record exists to catch printed nothing at all.

    Exit 4, on the same rule as `cmd_window`: it REPORTS, it does not block, and a caller that
    wants to branch on "the record and the markers disagree" needs a code to branch on. 0 means
    they agree, which is the only reading that may be taken as agreement.

    Every row below names the typed record as a CONCEPT — "the phase record has <id>" — and the
    path is printed only by `_refuse_suite_state`, i.e. on the branch where the file cannot be
    read, which is not the branch an operator meets. So the ledger is named once, above the rows.
    """
    disagreements = 0
    for kind, marker in sorted(SUITE_IDS.items()):
        if marker is None:
            continue
        on_disk = _marker(workdir, marker)
        recorded = (doc.get('ids') or {}).get(kind)
        if on_disk and recorded and on_disk != recorded:
            row = f'DIVERGED {marker}: the marker says {on_disk}, the phase record says {recorded} — read both before acting'
        elif recorded and not on_disk:
            row = f'MARKER-MISSING {marker}: the phase record has {recorded} and the marker file is absent'
        elif on_disk and not recorded:
            row = (f'MARKER-ONLY {marker}: the marker says {on_disk} and NO phase recorded it — an id landed outside '
                   'the record (the marker is written when the job returns; the phase record is a separate later call), '
                   'so which phase produced it, at which round, is unknown')
        else:
            continue
        # ONCE, above the rows — not appended to each of them: the path is one fact about the run,
        # and repeating it three times is the same noise that stops it being read.
        if not disagreements:
            print(f'phase_record: {suite_state_path(workdir)} — the ledger every line below compares a marker against')
        disagreements += 1
        print(row)
    return 4 if disagreements else 0


def _suite_window():  # noqa: ANN202 - a module
    """THE owner of the claim store (`hooks/_suite_window.py`) — its release runs where a suite's
    flow window closes, which is hook and shell code, so the store cannot live here."""
    sys.path.insert(0, str(plugin_root() / 'hooks'))
    import _suite_window  # noqa: PLC0415 - hooks/ is only on sys.path here

    return _suite_window


def cmd_coverage_hole(args: argparse.Namespace) -> int:
    """Record one accepted harness gap in the manifest's `coverage_holes[]` (internal only). Idempotent per mechanism+source."""
    workdir = resolve_workdir(args.workdir)
    doc = _read_json(manifest_path(workdir))
    if not isinstance(doc, dict):
        print(f'ERROR: no readable {MANIFEST_FILENAME} in {workdir} — run `bench_manifest.py init` first.', file=sys.stderr)
        return 2
    if not _CODE_CITE.match(args.source) or args.mechanism.startswith(SEAM_HOLE_PREFIX) or not args.reason.strip():
        print(f'ERROR: --source must be `<path>:<line>`, --reason non-blank, and `{SEAM_HOLE_PREFIX}*` is derived from the config '
              '(declare the tool_body seam there instead).', file=sys.stderr)
        return 2
    holes = [h for h in doc.get('coverage_holes') or [] if (h.get('mechanism'), h.get('source')) != (args.mechanism, args.source)]
    doc['coverage_holes'] = [*holes, {'mechanism': args.mechanism, 'source': args.source, 'reason': args.reason.strip()}]
    _write_json(manifest_path(workdir), doc)
    print(f'coverage hole recorded: {args.mechanism} at {args.source}')
    return 0


def cmd_window(args: argparse.Namespace) -> int:
    """One account window runs one suite — the flag half of the rule.

    Reports, never blocks. Whether a second suite is worth starting is the operator's call and
    depends on things this script cannot see (which account, how much of the window is left);
    what it could not see before is that a second suite was running at all. Exit 4 says the window
    is NOT PROVABLY FREE for this workdir — a live claim by another one, or a claim file that
    cannot be read — and names EVERY such claim. 0 is reserved for the readings that are
    positively free or positively ours. `claim` always records ours.
    """
    owner = _suite_window()
    workdir = owner.claim_key(resolve_workdir(args.workdir))
    flowdir = os.environ.get('FLOWDIR')  # resolve-workdir.sh's session dir: the window's close releases this key
    found = owner.claim(workdir, _plugin_version(), flowdir) if args.window_command == 'claim' else owner.others(workdir)
    live = [o for o in found if 'unreadable' not in o]
    for o in found:
        if 'unreadable' in o:
            print(
                f'window: UNREADABLE — {o["path"]} exists but {o["unreadable"]}. It is NOT free: a claim nobody can read is '
                'the state where a second suite in the same account window is most likely and least excluded.',
                file=sys.stderr,
            )
    undated = ' (its start time cannot be read, so the 8h expiry cannot be applied to it)'
    named = '; '.join(f'{o["workdir"]} at {o["started_at"] or "(no timestamp)"}{undated if o["undated"] else ""}' for o in live)
    if args.window_command == 'status':
        ours = owner.holds(workdir)
        if live:
            print(f'window: claimed by {named}')
        elif not found:
            print(f'window: {"claimed by " + workdir + " at " + str(ours.get("started_at")) if ours else "free"}')
        return 4 if found else 0
    if live:
        print(
            f'window: ALREADY CLAIMED by {named}. Two suites in one account window is the '
            f'configuration that killed both 09-07 night runs — they started eleven seconds apart and died on the '
            f'same reset, while the suite that ran alone consumed MORE and survived. This does not block you; it is '
            f'the fact nothing recorded before.',
            file=sys.stderr,
        )
    print(f'window: claimed by {workdir}' + (' (over an unreadable claim — see the note above)' if len(found) > len(live) else ''))
    return 4 if found else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workdir', help='defaults to $WORKDIR, else the resolver cascade')
    sub = parser.add_subparsers(dest='command', required=True)

    p_init = sub.add_parser('init', help='create or refresh the manifest from the topology + config')
    p_init.add_argument('--from-topology', help='defaults to <workdir>/artifacts/agent_topology.json')
    p_init.add_argument('--force', action='store_true', help='rewrite an existing manifest, DISCARDING analysis-authored fields')
    p_init.set_defaults(func=cmd_init)

    sub.add_parser('validate', help='schema + cross-artifact agreement + check wiring').set_defaults(func=cmd_validate)

    p_verify = sub.add_parser('verify', help='run the checks the manifest declares')
    # TWO modes, because there were only ever two behaviours: `plan` compares the mode to 'reuse'
    # and nothing else. A third name that changed nothing is a promise the runner does not keep.
    p_verify.add_argument('--mode', choices=('reuse', 'reverify'), default='reuse')
    p_verify.add_argument(
        '--only',
        nargs='+',
        help='run only these check ids; the rest keep their last result: a recorded pass is reused while its signature '
        'still holds and stale once its inputs changed, never not_run. A debug flag — never the run whose line the hand-off carries',
    )
    p_verify.add_argument(
        '--after-edit', action='store_true',
        help=f'the re-verify every input edit runs: {", ".join(REVERIFY_CHECKS)} (the ones this manifest declares)',
    )
    p_verify.add_argument('--timeout', type=int, default=_TIMEOUT_DEFAULT, help='per check, seconds')
    p_verify.set_defaults(func=cmd_verify)

    sub.add_parser('integrity-line', help='print the line the hand-off must carry').set_defaults(func=cmd_integrity_line)
    sub.add_parser('contract-gate', help='PROCEED / FIX FIRST / NOT VERIFIED / NOT RUN / STALE for the scan contract').set_defaults(func=cmd_contract_gate)
    p_mock = sub.add_parser('mock-gate', help='READY / WARN / NOT RECORDED: is every mocked tool witnessed or waived (C10); never blocks')
    p_mock.add_argument('--waive', metavar='TOOL', help='record a waiver for this mocked tool in this world first')
    p_mock.add_argument('--because', choices=MOCK_WAIVER_BECAUSE, help='with --waive: why no test can witness it')
    p_mock.add_argument('--cite', help='with --waive: the config line or code that shows it')
    p_mock.add_argument('--owner', choices=MOCK_WAIVER_OWNERS, help='with --waive: who owns closing it')
    p_mock.set_defaults(func=cmd_mock_gate)
    p_parity = sub.add_parser('local-parity', help='PROVEN / UNPROVEN: record the contract tier against the locally served world; never blocks')
    p_parity.add_argument('--exit-code', type=int, required=True, help="the contract tier's exit code")
    p_parity.add_argument('--log', required=True, help="the contract tier's output")
    p_parity.set_defaults(func=cmd_local_parity)
    p_gate = sub.add_parser('run-gate', help='RUNNABLE / NOT RUNNABLE / NOT RECORDED from the recorded harness checks and the host toolchain')
    p_gate.set_defaults(func=cmd_run_gate)
    sub.add_parser('provider-key', help='whether the agent\'s provider key is set in the environment or the module .env (names only)').set_defaults(func=cmd_provider_key)
    p_env = sub.add_parser('env-check', help='where each named setting is found: the environment or the env file the harness loads (names only)')
    p_env.add_argument('names', nargs='*', help='variable NAMES (never values)')
    p_env.add_argument('--providers', action='store_true', help='also list each model provider whose credentials are found')
    p_env.set_defaults(func=cmd_env_check)
    sub.add_parser('coverage', help='which checks verify which fields').set_defaults(func=cmd_coverage)
    sub.add_parser('run-mode', help='derive the mock posture from plurai.config.yaml and write .run_mode (mockless) or clear a stale one').set_defaults(
        func=cmd_run_mode)

    p_phase = sub.add_parser('phase', help='record / read the suite loop\'s phase, round counters and landed ids')
    phase_sub = p_phase.add_subparsers(dest='phase_command', required=True)
    p_record = phase_sub.add_parser('record', help='append one phase record, written by the step that produced it')
    p_record.add_argument('--phase', required=True, choices=SUITE_PHASES)
    p_record.add_argument('--status', required=True, choices=PHASE_STATUSES)
    p_record.add_argument('--round', type=int, help='the round counter for this phase (critic pass, edit round)')
    p_record.add_argument('--set', action='append', metavar='KIND=ID', help=f'an id this phase produced; KIND in {", ".join(sorted(SUITE_IDS))}')
    p_record.add_argument('--eval-revision', dest='eval_revision', help='the eval revision this phase left behind')
    p_record.add_argument('--note', help='one line, loop-internal')
    p_record.add_argument('--answer', choices=GATE_ANSWERS, help='the operator\'s answer to a gate this phase asked')
    p_record.add_argument('--landed', type=int, help='slots this phase actually shipped; pair with --requested')
    p_record.add_argument('--requested', type=int, help='slots this phase was asked for; pair with --landed')
    p_record.add_argument('--lost-kind', dest='lost_kind', help='why the missing slots were lost, e.g. the `gate_ids` of each slot\'s `scenario.slot_unshipped` finding')
    p_record.add_argument('--adopted', type=int, help='of --landed, how many came from an earlier set (a slot top-up); the rest were drawn here')
    p_record.add_argument('--job', help='on a `started` record: the backend job id this phase just started, so a resumed phase adopts it')
    p_record.add_argument('--key', help='with --job: which of the phase\'s jobs this is (e.g. the knowledge base a corpus is built for)')
    phase_sub.add_parser('resume', help='where a dead session picks up, any marker that disagrees (exit 4), and a handed-off build\'s report gap (exit 5)')
    p_job = phase_sub.add_parser('job', help='per job: adopt the one this phase already started, or start anew (exit 0 all adopted, 3 something to start)')
    p_job.add_argument('--phase', required=True, choices=SUITE_PHASES)
    p_job.add_argument('--key', help='only this key\'s job (e.g. one knowledge base\'s corpus)')
    p_reset = phase_sub.add_parser('reset', help='set an unfinished build aside: the next suite run is a new build')
    p_reset.add_argument('--note', required=True, help='one line: what made the build stale')
    p_phase.set_defaults(func=cmd_phase)

    p_hole = sub.add_parser('coverage-hole', help='record an accepted harness gap in coverage_holes[] (internal only)')
    p_hole.add_argument('--mechanism', required=True, help='what does not run (the guard, middleware or path)')
    p_hole.add_argument('--source', required=True, help='<path>:<line> of the agent code that does not run')
    p_hole.add_argument('--reason', required=True, help='why the suite accepts it')
    p_hole.set_defaults(func=cmd_coverage_hole)

    p_window = sub.add_parser('window', help='the account-window claim — one window runs one suite')
    window_sub = p_window.add_subparsers(dest='window_command', required=True)
    window_sub.add_parser('claim', help='claim the window for this workdir; exit 4 when another suite holds it')
    window_sub.add_parser('status', help='who holds the window; exit 4 when it is somebody else')
    p_window.set_defaults(func=cmd_window)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
