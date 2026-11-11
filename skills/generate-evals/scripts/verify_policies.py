#!/usr/bin/env python3
"""Standalone validator for plugin-generated policies_config.yaml.

Same role as scripts/verify.py for scenario_variables: stdlib + pyyaml only,
no backend dependency. Runs locally inside the plugin's skill execution loop so the
LLM gets fast feedback on shape errors before the backend ever sees the file.

The accepted shape is the simplified input format consumed by
the backend's policy loader. Each top-level key is a policy name, each value is
a mapping with the policy's fields (no separate `agents:` block — the backend
derives that at load time).

Two tiers, and the line between them is the backend's policy LOADER:

* an ERROR (`✗`, exit 2) is something the loader refuses — the YAML does not parse or is not a
  non-empty mapping, a field it requires is missing (`confidence_threshold` included), a
  closed-set value (`type`, `metric_type`, `evaluation_scope`, `severity`, `untriggered_verdict`)
  is off its set, a `violated` form does not fit its `metric_type`, a type's own body field is
  missing, a `code_eval` expression does not compile, a `beat_resolution` slice does not pair
  with its `metric_type` and polarity, a numeric violation has no threshold. A set with one does
  not load. Two are deliberately stricter than the loader: a missing `type` (it loads as
  `custom`, a code-generated agent no eval set carries) and `debate_system` (out of scope for
  the generated path). Not every loader refusal is mirrored yet — `verdict_tier` /
  `applies_to_polarity` values, `terminate_values` types, a `file:` prompt that is missing or
  escapes the config dir, and the scalar types of the remaining optional fields reach the
  backend's own load gate unchecked here.
* everything else is ADVISORY (`⚠`, exit 0): a sandbox name the box does not bind, a binding that
  is vacuous at its scope, a dead guard, a judge-prompt template variable, a polarity shape. The
  set loads; each is evidence the caller decides on (`docs/gate-findings.md`). The simpleeval
  sandbox blocks what it blocks at runtime — this file never refuses a file over it.

Usage:
    python verify_policies.py /path/to/policies_config.yaml
    python verify_policies.py /path/to/policies_config.yaml --sandbox-names live.json
    python verify_policies.py /path/to/policies_config.yaml --flow-ids flows.json

The code_eval sandbox namespace it grades against is NOT written here. With a backend
configured (`PLURAI_URL` and `PLURAI_TOKEN`, or the saved login; the token is optional for a loopback
URL, as everywhere in the plugin) it reads it LIVE from
`GET /sandbox-helpers` — the box a policy will actually run in. With no backend, an
unreachable one, or a bad response, `sandbox_namespace.json` beside this script stands in, and a
NOTE on stderr says the live read was skipped and why;
that file is a snapshot of the backend's namespace, shipped with the plugin, and is the same
payload the route serves. `--sandbox-names` takes a capture of that endpoint and overrides
both for one run.

The run's FLOW VOCABULARY is read over the same lane, from `GET /artifacts/{scenario_set_id}`
(`detail.scenarios[].target_flows`), and the dead-guard lint grades against it. `--flow-ids` beats
that read; with neither, the lint falls back to a `_flow`-suffix heuristic AND SAYS SO, because a
customer whose flows are named `refund_request` otherwise gets a silent no-op.

Stdlib only, and still runnable with no backend at all: every live read is one urllib GET with a
short timeout whose every failure falls back with a note, so nothing here can turn an offline
machine into a failing verification.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'hooks'))

import _no_redirect  # noqa: E402 - a redirect must not carry the bearer on
import backend_identity_marker  # noqa: E402 - after the sys.path lines it depends on
import url_guard  # noqa: E402 - the one answer to "may the bearer go to this URL"
from stop_message import CONTACT_TEXT  # noqa: E402 - the contact hand-off has one home

try:
    import yaml
except ImportError:
    print(
        f'ERROR: pyyaml is required to run verify_policies.py but is not importable '
        f'from {sys.executable}. Install it into that interpreter: '
        f'{sys.executable} -m pip install pyyaml',
        file=sys.stderr,
    )
    sys.exit(3)


# Mirrors the backend diagnostic_agent.models.PolicyType — the types the backend
# emits + indexes — PLUS beat_resolution, which this tuple LEADS: the installed
# plugin is a cached snapshot, so a backend emitting a type the snapshot rejects
# would fail verification for every customer who has not updated. A diff against
# the backend's PolicyType is expected to show this one entry until that side ships.
#
# retrieval_metric is the knowledge retrieval pair the backend MANDATES for
# retrieval-tool agents; rejecting it here fails every generated set that
# contains one.
#
# Every type here needs its own branch in `_validate_policy`'s field chain — that
# chain is the only place a type's OWN body field is checked; see its closing
# branch for what happens when one does not.
ACCEPTED_TYPES = ('code_eval', 'llm_as_judge', 'hybrid', 'retrieval_metric', 'beat_resolution')
ACCEPTED_METRIC_TYPES = ('boolean', 'int', 'float', 'categorical')
ACCEPTED_SCOPES = ('turn_based', 'end_to_end')
#: The ADVISORY floor for a rich agent. It is not a hard error, because it cannot be one: a
#: genuinely small agent — two flows and one guard — has no legal 5-policy config to write, and
#: refusing its 3-policy set makes this lint the thing that stops a correct suite from shipping.
#: The real floor scales with the agent (`_policy_floor`), and BELOW it this warns rather than
#: refuses. Only an EMPTY set is an error: that is not a small agent, it is no eval set.
MIN_POLICIES = 5


def _policy_floor(flow_count: int | None) -> int:
    """`min(5, 2 + flow_count)` — the floor this agent can actually meet.

    Two policies is the smallest set that can say anything (one behaviour, one guard), and each
    flow adds one. `None` (no flow vocabulary was resolved) keeps the advisory 5: with nothing
    known about the agent's size, the generic expectation is the honest one.
    """
    return MIN_POLICIES if flow_count is None else min(MIN_POLICIES, 2 + flow_count)
#: The fields the backend's loader refuses a policy without (`metric_type` has no default, a missing
#: `type` loads as `custom` — a code-generated agent no eval set carries — and
#: `eval_metric_description` is required when the judge agent is built).
REQUIRED_FIELDS = (
    'metric_type',
    'type',
    'eval_metric_description',
)
#: The scope the backend gives a policy that declares none.
DEFAULT_SCOPE = 'turn_based'
#: Fields the loader defaults, with what an absent one loads as: advisory, never refused.
EXPECTED_FIELDS = {
    'display_name': 'with no human-readable name',
    'evaluation_scope': f'evaluation_scope defaults to {DEFAULT_SCOPE}',
}
#: The sandbox-evaluated fields. The loader joins a multi-line scalar into one line before it
#: parses one (the backend `expression_text.normalize_expression_text`), so this file does too.
EXPRESSION_FIELDS = ('expression', 'applicability_guard', 'reasoning_expression')
#: Every key the backend's simplified policy model reads (`SimplifiedPolicyInput` over `PolicyYamlData`).
#: The model forbids any other key, but this list is a snapshot of it, so an unknown key here is
#: an advisory: a backend newer than this plugin may already read it.
POLICY_KEYS = frozenset({
    'uuid', 'display_name', 'description', 'tags', 'confidence_threshold', 'metric_type', 'evaluation_scope',
    'labels', 'violated', 'violation_threshold', 'terminate_values', 'flow_filter', 'target_complications',
    'target_flows', 'provenance', 'applicability_guard', 'untriggered_verdict', 'grades_scenario_target',
    'violation_is_absence', 'grades_requirement_conflict', 'grades_content_fidelity', 'grounded_on', 'world_needs',
    'grades_source_fidelity', 'verdict_tier', 'severity', 'applies_to_polarity', 'defers_to_source_fidelity',
    'defers_to', 'eval_metric_description', 'type', 'generate_graph', 'system_prompt', 'debate_config',
    'additional_params', 'expression', 'reasoning_expression', 'hybrid_module', 'hybrid_entrypoint', 'hybrid_subcalls',
    'backend_rule', 'reads_source_grounding', 'counts_calls', 'prompt_summary', 'rule', 'group',
})
#: The loader's closed sets for the optional enum fields it validates.
CLOSED_FIELDS = {
    'severity': ('high', 'medium', 'low'),
    'untriggered_verdict': ('not_applicable', 'pass', 'fail'),
    # The backend standing rule a judge owns: its fixed definition is injected into that judge's prompt.
    'backend_rule': ('assured_consequence_backed',),
    # The policy's unit of tool call: the calls the agent's own message issued, or every call the system made (default).
    'counts_calls': ('top_level', 'all'),
}
#: The types the loader builds at runtime; `generate_graph: true` on one of them refuses.
DYNAMIC_TYPES = ('llm_as_judge', 'debate_system', 'code_eval', 'retrieval_metric', 'beat_resolution')

# Builtins the code_eval sandbox (simpleeval) HARD-BLOCKS: calling any of them
# raises FeatureNotAvailable at runtime, so the policy never scores. Reported here
# as an advisory — the sandbox's own block is the boundary, and the set still loads.
SANDBOX_FORBIDDEN_BUILTINS = frozenset({
    'type', 'isinstance', 'eval', 'getattr', 'setattr',
    'repr', 'compile', 'open', 'exec', 'help',
})


#: Findings that do not fail the file but must not vanish into scrollback. The caller (the
#: pre-run gate in simulation-generate-suite) reads this script's OUTPUT to decide whether a
#: repair round is needed, so an uncounted warning is a finding nobody acts on.
_WARNINGS: list[str] = []


def _warn(message: str) -> None:
    _WARNINGS.append(message)
    print(f'  ⚠ {message}', file=sys.stderr)


def _is_nonempty_str(v: object) -> bool:
    return isinstance(v, str) and bool(v.strip())


# THE SANDBOX NAMESPACE — vendored from the backend, never hand-written here.
#
# `sandbox_namespace.json` beside this file is generated from the backend's own
# `code_eval_factory` (names + functions off `_create_safe_evaluator`, plus
# `NAMES_BY_SCOPE` / `HELPER_READS`), and is the
# byte-identical payload `GET /sandbox-helpers` serves. It is the one vendored copy of that
# namespace: a mirror of a server-side contract is a permanent drift bug, and a stale copy
# lacking a backend helper would reject a guard the sandbox accepts, silently, on a run where
# the live fetch times out.
#
# Precedence: `--sandbox-names` (a capture the caller names) beats the LIVE box, which beats
# the vendored snapshot, which beats nothing — there is no fourth, editable-by-hand tier. An
# explicitly-passed file that will not parse is an ERROR rather than a downgrade: the caller
# asked to grade against a specific backend, and quietly grading against another namespace is
# how a stale list keeps passing policies the sandbox rejects. A live read that fails is the
# opposite case — nobody named that namespace — so it falls back with a note.
#
# The live read is the DEFAULT because the snapshot is only ever as fresh as its last regen,
# and a regen is a human action nobody schedules. A snapshot missing a helper the backend ships
# rejects a correct policy set with errors against expressions the box would accept.
_VENDORED_NAMESPACE = Path(__file__).with_name('sandbox_namespace.json')
# All FOUR blocks are required. names/functions alone tell a typo from a helper; the scope
# matrix is what tells a live check from one whose every binding is empty at its own scope,
# and accepting a two-block file would disable that lint with no message — the same
# degrade-and-report-success shape this whole change removes. `regen_sandbox_namespace.sh`
# refuses to install such a file for the same reason.
_VENDORED_BLOCKS = ('names', 'functions', 'names_by_scope', 'helper_reads')


class VendoredNamespaceError(RuntimeError):
    """The vendored namespace is missing or unusable.

    A plain exception, NOT SystemExit: it is raised at MODULE SCOPE, and a SystemExit there
    aborts the importing process — under pytest that is an INTERNALERROR and "no tests ran"
    for the whole session rather than a collection error naming this file. Nothing catches
    it, and nothing should: the artifact is checked in, so its absence is a broken checkout
    and the traceback names the file and the regen script.
    """


def _as_names(value: object) -> frozenset[str] | None:
    """A name/function list as a frozenset, or None when it is not one.

    Accepts a list or a dict keyed by name — the backend holds these as name->value mappings
    in-process and both spellings reach this file.
    """
    if isinstance(value, dict):
        value = list(value)
    if not isinstance(value, list) or not value:
        return None
    if not all(isinstance(v, str) for v in value):
        return None
    return frozenset(value)


def _as_scope_matrix(value: object) -> dict[str, dict[str, str]] | None:
    if not isinstance(value, dict) or not value:
        return None
    cleaned = {str(name): {str(scope): str(verdict) for scope, verdict in scopes.items()} for name, scopes in value.items() if isinstance(scopes, dict) and scopes}
    return cleaned or None


def _as_helper_reads(value: object) -> dict[str, tuple[str, ...]] | None:
    if not isinstance(value, dict) or not value:
        return None
    cleaned = {str(helper): tuple(str(n) for n in reads) for helper, reads in value.items() if isinstance(reads, (list, tuple)) and reads}
    return cleaned or None


def _namespace_from(payload: object) -> tuple[frozenset[str] | None, frozenset[str] | None, dict[str, dict[str, str]] | None, dict[str, tuple[str, ...]] | None]:
    """Parse a `{names, functions, names_by_scope, helper_reads}` payload, defensively.

    ONE parser for both sources — the vendored artifact and a live `--sandbox-names` capture.
    A second parser with different strictness would build a frozenset of CHARACTERS from a JSON
    string, or raise AttributeError on a malformed scope map, where this one rejects both. Two
    parsers for one shape drift, like two copies of the namespace.
    """
    if not isinstance(payload, dict):
        return None, None, None, None
    return (
        _as_names(payload.get('names')),
        _as_names(payload.get('functions')),
        _as_scope_matrix(payload.get('names_by_scope')),
        _as_helper_reads(payload.get('helper_reads')),
    )


def _read_vendored_namespace() -> tuple[frozenset[str], frozenset[str], dict[str, dict[str, str]], dict[str, tuple[str, ...]]]:
    hint = 'It ships with the plugin; without it this script cannot tell a typo from a real backend helper. Reinstall or update the plugin.'
    try:
        payload = json.loads(_VENDORED_NAMESPACE.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise VendoredNamespaceError(f'{_VENDORED_NAMESPACE} is missing or unreadable ({exc}). {hint}') from exc
    parsed = _namespace_from(payload)
    # No `strict=`: this runs at import under the tooling interpreter, which can be 3.9; the two are
    # the same four blocks by construction (`_namespace_from`).
    missing = [block for block, value in zip(_VENDORED_BLOCKS, parsed) if value is None]
    if missing:
        raise VendoredNamespaceError(f'{_VENDORED_NAMESPACE} is missing, empty or malformed for: {", ".join(missing)}. Reinstall or update the plugin.')
    return parsed  # type: ignore[return-value]  # every element proven non-None above


SANDBOX_KNOWN_NAMES, SANDBOX_KNOWN_FUNCTIONS, SANDBOX_NAMES_BY_SCOPE, SANDBOX_HELPER_READS = _read_vendored_namespace()
"""The code_eval sandbox namespace, vendored from the backend and never hand-written here.

`names`/`functions` say what EXISTS. `names_by_scope` says what a binding can DECIDE at each
`evaluation_scope` — see `KNOWN_SCOPE_TOKENS`: 'populated' / 'partial', and the two VACUOUS ones,
'empty' (no data there) and 'constant_false' (bound and non-empty, but fixed by every scoring
path at that scope). Every name is always bound, so an expression reading only vacuous bindings
does not raise — it returns a verdict computed from nothing. `helper_reads` follows a helper to
the binding it reads implicitly.

Precedence: `--sandbox-names` beats the live `GET /sandbox-helpers` read, which beats this
snapshot, which beats nothing — there is no fourth, editable-by-hand tier.
"""


def _adopt_namespace(payload: object, source: str) -> bool:
    """Replace the vendored namespace with ``payload``, or leave it standing and return False.

    ONE adoption for both live sources — the flag and the box — so the drift note, the partial
    capture rule and the receipt line cannot differ between them.
    """
    global SANDBOX_KNOWN_NAMES, SANDBOX_KNOWN_FUNCTIONS, SANDBOX_NAMES_BY_SCOPE, SANDBOX_HELPER_READS

    names, functions, by_scope, reads = _namespace_from(payload)
    if names is None or functions is None:
        return False
    drifted = sorted((names | functions) ^ (SANDBOX_KNOWN_NAMES | SANDBOX_KNOWN_FUNCTIONS))
    # …and the VALUES, not just the name sets. A retype leaves both sets identical: a binding
    # that changes from `populated` to `constant_false` (as `final_step.turn_based` did) adds and
    # removes no name, so a name-only comparison stays silent on a LIVE read of a current box. The
    # matrix is what the vacuity lint grades against, so a value that moved is exactly the drift
    # worth saying out loud.
    if by_scope is not None:
        drifted += [
            f'{name}.{scope}: {SANDBOX_NAMES_BY_SCOPE.get(name, {}).get(scope, "(absent)")} -> {token}'
            for name, row in sorted(by_scope.items())
            for scope, token in sorted(row.items())
            if SANDBOX_NAMES_BY_SCOPE.get(name, {}).get(scope) != token
        ]
    SANDBOX_KNOWN_NAMES = names
    SANDBOX_KNOWN_FUNCTIONS = functions
    # Scope data is OPTIONAL in the capture: a box predating it returns names/functions only,
    # and the VENDORED matrix must then stand rather than silently disabling the vacuity lint.
    # This is the one place a partial capture is legitimate — the box really does not have the
    # data, as opposed to the fetch having failed.
    if by_scope is not None:
        SANDBOX_NAMES_BY_SCOPE = by_scope
    if reads is not None:
        SANDBOX_HELPER_READS = reads
    # ONE line saying what actually landed. The capture has TWO name blocks and an
    # operator who reads only `names` concludes a real helper does not exist — the endpoint is
    # right, the shape is unlabeled. Printed on EVERY live capture, not only on drift: "the read
    # was honoured, and here is how much of a namespace it brought" is the fact a silent success
    # cannot convey, and a truncated capture is otherwise indistinguishable from a rich one.
    print(f'live sandbox namespace from {source}: {len(names)} variables / {len(functions)} functions', file=sys.stderr)
    if drifted:
        # The live box and the vendored snapshot disagree. Not an error — the box may simply be
        # newer, and the live capture wins by design — but it is the ONLY moment this drift is
        # observable, and staying silent is how a stale artifact survives indefinitely.
        print(
            f'NOTE: the vendored sandbox namespace differs from {source} on: {", ".join(drifted)}. '
            'Grading against the live box. Update the plugin to refresh the '
            'bundled copy.',
            file=sys.stderr,
        )
    return True


def _load_sandbox_names(path: Path) -> bool:
    """Override the namespace with a capture the CALLER named.

    Passing this flag is a statement: "grade against the box I am actually talking to."
    Honouring it silently-or-not-at-all is what made a stale list invisible, so an
    explicitly-passed file that cannot be used is a hard error, not a downgrade to the
    snapshot — unlike the unprompted live read below, which nobody asked for by name.
    """
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise SystemExit(
            f'verify_policies: --sandbox-names {path} is unreadable ({exc}). Omit the flag to grade '
            'against the live backend or the vendored namespace, or re-fetch GET /sandbox-helpers — '
            'grading against a different namespace than the one you named is how a stale list passes '
            'policies the sandbox rejects.'
        ) from exc
    if not _adopt_namespace(payload, str(path)):
        raise SystemExit(
            f'verify_policies: --sandbox-names {path} has no usable "names"/"functions" lists. An '
            'empty or truncated capture is a failed fetch, not an empty namespace; omit the flag to '
            'grade against the live backend or the vendored namespace.'
        )
    return True


#: Short on purpose. This read is an improvement on the snapshot, never a precondition for
#: running, so a slow or wedged box must cost seconds and then get out of the way.
_LIVE_NAMESPACE_TIMEOUT = 10


@lru_cache(maxsize=1)
def _is_a_split(base: str) -> bool:
    """Is `base` a backend this session is NOT bound to? Said ONCE, by name, when it is.

    The live reads below are the sharpest thing this script does and the quietest when they go
    wrong: every failure is a deliberate fallback with a NOTE, so an UNREACHABLE wrong box reads
    as "could not read" and a HEALTHY wrong box reads as nothing at all — the file is then graded
    against another deployment's sandbox namespace and flow vocabulary, and passes or fails on a
    vocabulary the run will never see. Naming it is the minimum; the vendored snapshot is a real
    answer and staying runnable offline is this script's contract, so it is not an error.
    """
    refusal = backend_identity_marker.split(base, caller='verify_policies')
    if refusal is None:
        return False
    print(refusal, file=sys.stderr)
    print('NOTE: skipping every LIVE read for that reason and grading against the vendored '
          'sandbox snapshot + the flow-suffix heuristic. Fix the split and re-run to grade '
          'against the box these policies will actually run in.', file=sys.stderr)
    return True


def _token(url: str) -> str:
    """The bearer for ``url``: the environment's token, else the credential saved for its origin."""
    return url_guard.bearer(url)


def _backend_skip_reason() -> str | None:
    """Why no backend is configured for a live read, or None when one is.

    A loopback URL needs no bearer — a local the backend is tokenless everywhere else in the plugin —
    so only a non-loopback URL without a token counts as unconfigured.
    """
    base = url_guard.server_url()
    if not base:
        return url_guard.configuration_problem('verify_policies')
    if not _token(base) and not url_guard.is_loopback(base):
        return f'{base} is not loopback and no token is available'
    return None


def _backend_root() -> str | None:
    """The configured box's REST root, or None when no backend is configured — or the wrong one.

    ONE derivation for every plain-HTTP read this script makes (the sandbox namespace, the run's
    flow vocabulary): two copies of "strip /mcp, keep the bearer" is two chances to point one
    read at a different box than the other. That makes it the one place a wrong box has to be
    caught as well, since every read downstream of it treats its own failure as a fallback.
    """
    if _backend_skip_reason() is not None:
        return None
    base = url_guard.server_url()
    if _is_a_split(base):
        return None
    return base


def _get_json(url: str, timeout: int) -> object | None:
    """One authenticated GET, parsed — or None with the failure named on stderr.

    Every failure is a FALLBACK, never an error: this script must stay runnable with no backend
    at all, and turning an unreachable box into a failed verification would buy no truth.
    """
    request = urllib.request.Request(url, method='GET')
    if token := _token(url):
        # The bearer goes only to a backend the session was bound to (the session pin, hooks/session_pin.py).
        if not url_guard.is_backend_url(url):
            print('NOTE: ' + url_guard.refused_message(url, ', so the bearer would not be sent there; it was not read.'), file=sys.stderr)
            return None
        request.add_header('Authorization', f'Bearer {token}')
    try:
        with _no_redirect.OPENER.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as exc:  # noqa: BLE001 - any failure here is a fallback, and the note names it
        print(f'NOTE: could not read {url} ({type(exc).__name__}: {exc}).', file=sys.stderr)
        return None


def _live_namespace_url() -> str | None:
    """The configured box's ``GET /sandbox-helpers``, or None when no backend is configured."""
    root = _backend_root()
    return None if root is None else f'{root}/sandbox-helpers'


def _fetch_live_namespace() -> bool:
    """Grade against the namespace of the box the policies are headed for, when there is one.

    This is what retires the hand-written `curl … > sandbox.json && --sandbox-names sandbox.json`
    preamble the skills carried in three places. Three copies of a fetch is three chances to
    write the failure branch differently — one of them truncated the file on a failed curl, and
    the verifier read the truncation as an empty namespace.

    EVERY failure is a fallback, never an error: no backend configured, a 401, a timeout, an
    HTML error page. The vendored snapshot is a real answer, and turning an unreachable box into
    a failed verification would make this script unusable offline for no gain in truth.
    """
    url = _live_namespace_url()
    if url is None:
        # A backend split has already said so, by name, in `_is_a_split`.
        reason = _backend_skip_reason()
        if reason is not None:
            print(
                f'NOTE: skipping the live GET /sandbox-helpers read ({reason}); grading against the '
                'vendored sandbox snapshot, which may be older than the backend.',
                file=sys.stderr,
            )
        return False
    payload = _get_json(url, _LIVE_NAMESPACE_TIMEOUT)
    if payload is None:
        print(
            'NOTE: grading against the vendored sandbox snapshot, which may be older than the backend.',
            file=sys.stderr,
        )
        return False
    if not _adopt_namespace(payload, url):
        print(
            f'NOTE: {url} returned no usable "names"/"functions" lists; grading against the '
            'vendored snapshot.',
            file=sys.stderr,
        )
        return False
    return True


def _locally_bound_names(tree: ast.AST) -> set[str]:
    """Names bound INSIDE the expression (comprehension targets + lambda params) —
    legal bindings, not sandbox names. Lambda params matter even though lambdas are
    rejected outright below: recognizing them keeps the error TARGETED ('lambda is
    unsupported') instead of the misleading 'seg is unbound' the backend gate
    disagrees with — a lambda parameter is bound at runtime; the lambda ITSELF is
    the defect."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.comprehension):
            for t in ast.walk(node.target):
                if isinstance(t, ast.Name):
                    bound.add(t.id)
        elif isinstance(node, ast.Lambda):
            for arg in (*node.args.args, *node.args.posonlyargs, *node.args.kwonlyargs):
                bound.add(arg.arg)
    return bound


def _one_line(text: str) -> str:
    """The backend's `normalize_expression_text`: non-empty lines stripped and joined with one space;
    text with no newline is returned unchanged. Line joining, never whitespace collapsing — the
    spacing inside a string literal is part of what a membership test matches."""
    if '\n' not in text:
        return text
    return ' '.join(line.strip() for line in text.splitlines() if line.strip())


def _compile_error(expression: str) -> str | None:
    """Why the loader refuses this `code_eval` expression, or None. It runs
    `compile(expression, '<expression>', 'eval')` on the (line-joined, UNSTRIPPED) text, so leading
    whitespace and a top-level `await` refuse here even though `ast.parse` takes the latter."""
    try:
        compile(expression, '<expression>', 'eval')
    except (SyntaxError, ValueError) as exc:
        parse = [e for e in _expression_errors(expression) if e.startswith('expression is not a valid single')]
        return parse[0] if parse else (
            f'expression does not compile ({type(exc).__name__}: {exc}) — the loader compiles it in eval mode '
            f'and refuses the file.'
        )
    return None


def _expression_errors(expression: str) -> list[str]:
    """Static sandbox-contract errors for a code_eval expression (or guard).

    simpleeval evaluates ONE Python expression: statements (`import`, `=`) are
    syntax errors in eval mode, so ast.parse(mode='eval') is exactly the gate the
    runtime applies. Each message is a runtime error of the policy; which tier it
    reports at is the caller's (`_validate_policy`).
    """
    try:
        tree = ast.parse(expression, mode='eval')
    except SyntaxError as e:
        hint = ''
        stripped = expression.lstrip()
        if stripped.startswith('import ') or '\nimport ' in expression:
            hint = (
                ' `import` is a statement — simpleeval evaluates a single '
                'expression and has no modules (no `re`); use str methods, '
                'comprehensions and the sandbox helpers.'
            )
        elif '\n' in expression.strip():
            hint = (
                ' simpleeval evaluates ONE expression — multiple statements/'
                'lines cannot work; fold the logic into a single expression.'
            )
        return [f'expression is not a valid single Python expression ({e.msg}).{hint}']

    errors: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in SANDBOX_FORBIDDEN_BUILTINS
        ):
            errors.append(
                f'expression calls {node.func.id!r}, which the simpleeval sandbox '
                f'forbids (raises FeatureNotAvailable at runtime, so the policy never '
                f'scores). Use a sandbox helper instead '
                f'(is_list/is_dict/is_str/is_number/is_bool for type checks).'
            )

    # Mirrors the backend's static probe (code_eval_uses_lambda): simpleeval never
    # evaluates Lambda nodes, and a lambda parked behind a short-circuit evades every
    # execution-based probe until the first real transcript reaches it.
    if any(isinstance(node, ast.Lambda) for node in ast.walk(tree)):
        errors.append(
            'expression contains a `lambda`, which the simpleeval sandbox does NOT '
            'support (FeatureNotAvailable at runtime, even when a probe run '
            'short-circuits around it). Inline the logic with comprehensions instead.'
        )

    known = SANDBOX_KNOWN_NAMES | SANDBOX_KNOWN_FUNCTIONS | _locally_bound_names(tree)
    unknown = sorted({
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in known
    })
    if unknown:
        errors.append(
            f'expression references name(s) the sandbox does not bind: {unknown} — '
            f'each raises NameNotDefined at runtime (errored policy, never scores). '
            f'Available variables: {sorted(SANDBOX_KNOWN_NAMES)}; helpers: '
            f'{sorted(SANDBOX_KNOWN_FUNCTIONS)}. (If one of these IS a real the backend '
            f'helper, this list has drifted — re-run with --sandbox-names from the '
            f'backend\'s GET /sandbox-helpers, or sync it with code_eval_factory.)'
        )
    return errors


#: Bindings whose ELEMENTS are message OBJECTS, not strings — the shape the backend documents in
#: `dialogs/utils.py`. `agent_messages_raw` is here too: raw is about provenance, not type.
MESSAGE_BINDINGS = frozenset({
    'agent_messages', 'agent_messages_raw', 'chatbot_messages', 'messages', 'user_messages',
})


def _message_membership_warnings(expression: str) -> list[str]:
    """`"<literal>" in <message binding>` — a test that is DETERMINISTICALLY False.

    Verified empirically on a live customer set, and it closes the "guard context differs" mystery:
    the guard and expression namespaces are identical, so nothing about the CONTEXT explained
    why a widened guard stayed inert. `'₪' in m` over a pydantic message object iterates the
    model's field tuples and can never match a substring; `'₪' in agent_messages` asks whether a
    string is one of the message OBJECTS. Both are silently False — no error, no warning, a
    policy that reads as correctly scoped and grades nothing.

    Three shapes, because the operator hit the third: membership over the binding itself, over a
    slice/index of it, and over a comprehension variable that iterates it.
    """
    try:
        tree = ast.parse(expression, mode='eval')
    except SyntaxError:
        return []   # the parse error is reported by _expression_errors

    def _root(node: ast.AST) -> str | None:
        """The message binding this expression is rooted at, if any — `agent_messages[-1]`,
        `agent_messages[1:]` and `agent_messages` all root at `agent_messages`."""
        while isinstance(node, ast.Subscript):
            node = node.value
        return node.id if isinstance(node, ast.Name) and node.id in MESSAGE_BINDINGS else None

    # Comprehension variables that iterate a message binding: `for m in agent_messages[-1:]`.
    #: Names only — a tuple target is not a message.
    iterating: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            for gen in node.generators:
                binding = _root(gen.iter)
                if binding and isinstance(gen.target, ast.Name):
                    iterating[gen.target.id] = binding

    found: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)):
            continue
        if not (isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)):
            continue
        for comparator in node.comparators:
            # `.content` (or any other attribute) is the CORRECT form — never flag it.
            if isinstance(comparator, ast.Attribute):
                continue
            binding = _root(comparator)
            if binding is None and isinstance(comparator, ast.Name):
                binding = iterating.get(comparator.id)
            if binding:
                found.append(
                    f'`{node.left.value!r} in <{binding}…>` tests a string against a MESSAGE OBJECT (or the list of '
                    f'them), which is deterministically False at runtime — the check never fires and the policy '
                    f'grades nothing while reading as correctly scoped. Use `contains_term(...)` , or take the text '
                    f'first (`.content`).'
                )
                break
    return found


#: Projections that carry ONE ENTRY PER TURN — dense, never a position in an interleaved list.
#: ``agent_messages`` / ``agent_messages_raw`` are the ASSISTANT-only views and the scored thread
#: carries exactly one reply per driven turn; ``user_messages`` is the USER-only view, dense over
#: the turns the USER SPOKE ON (:data:`USER_SIDE_BINDINGS` — a different coordinate, see there);
#: ``agent_states`` is not a message projection at all and the backend ASSERTS its density at
#: reconstruction (``simulation/score_saved_run``: ``len(turns) == len(agent_states) ==
#: len(turn_chains) == len(turn_actions)``).
#:
#: WHAT THIS SET DOES NOT CLAIM: "entry ``i`` GENUINELY belongs to user turn
#: ``i``, one per driven turn". That is one sentence about two coordinates, true of the agent side
#: and false of the user side — the opening user message is OPTIONAL initial state, so a
#: bench that declares ``agent.io.may_open`` spends driven turn 0 on the AGENT's own opening and
#: that turn has no user message at all.
TURN_DENSE_BINDINGS = frozenset({'agent_messages', 'agent_messages_raw', 'user_messages', 'agent_states'})

#: The turn-dense projection whose coordinate is the ROLE rather than the driven turn.
#: ``user_messages`` holds one entry per turn THE USER SPOKE ON, which is the driven-turn
#: coordinate only while the user opens the conversation. On an agent-first run (the
#: scenario declares no opener and the harness declares ``agent.io.may_open``) driven turn 0 is the
#: agent's greeting, its user row is absent, and every entry is therefore shifted: entry ``i`` is
#: the user message of driven turn ``i + 1`` and the list is one shorter than the turn count.
#:
#: MEASURED, not reasoned: ``scripts/tests/fixtures/agent_first_run/`` holds two transcripts
#: written through a real backend orchestration server and a real SDK ``Orchestrator`` — agent-first, 4 driven
#: turns and 3 user messages with ``turns[0].user_message: null``, beside the user-first control at
#: 4 and 4.
#:
#: DECLARED as a set rather than spelled ``binding == 'user_messages'`` at the one call site,
#: because the claim is about a CLASS: a second user-side projection added later would otherwise
#: inherit the agent side's "entry i is driven turn i" silently.
USER_SIDE_BINDINGS = frozenset({'user_messages'})

#: Projections that are NOT per-turn dense and must never be described as such. ``messages`` and
#: ``chatbot_messages`` are bound to the SAME object — the FULL INTERLEAVED transcript — which
#: the backend states in ``dialogs/utils.agent_side_messages`` ("``chatbot_messages`` is the FULL
#: interleaved transcript — the name predates that shape") and whose consequence the authoring guide
#: states at ``deterministic_check_guide.md:56``: ``agent_messages[k]`` is the reply to user turn
#: ``k``, "while ``chatbot_messages[1]`` is NOT (it's the first reply in the interleaved list)". An
#: absolute index into them names a POSITION, not a turn and not even a reliable SPEAKER — a superset
#: of the defect rather than an exemption, so they are still flagged with a different remedy.
INTERLEAVED_TRANSCRIPT_BINDINGS = frozenset({'chatbot_messages', 'messages'})

def _indexed_transcript_bindings() -> frozenset[str]:
    """Every classified binding, resolved from the three declaration sets at CALL time.

    DECLARE AND DISPATCH: adding a binding to :data:`USER_SIDE_BINDINGS` is the whole edit — it
    becomes indexed here and the dispatch in :func:`_turn_coordinate_warnings` reads the same set,
    so the new binding cannot be classified in one place and unreachable in the other. Resolved by
    call rather than frozen at import because a set that is only read once cannot be the single
    place a classification lives.
    """
    return TURN_DENSE_BINDINGS | INTERLEAVED_TRANSCRIPT_BINDINGS | USER_SIDE_BINDINGS


#: Every binding for which a non-negative absolute subscript is an AUTHORED POSITION.
#:
#: NOT NAMED ``PER_TURN_LIST_BINDINGS``, because that name would claim all six are dense per-turn
#: lists, which two of them are not. ``MESSAGE_BINDINGS`` is still checked
#: against the split, as COVERAGE — a message binding added there and classified nowhere raises,
#: rather than being inherited here and silently called per-turn dense.
#:
#: ``turn_tool_events`` is bound and is deliberately absent: it is a MAPPING, so an int subscript of
#: it is a key lookup and not a coordinate. ``turn_actions`` / ``turn_chains`` are per-turn too and
#: are not bound in the sandbox at all, so no authored expression can reach them.
INDEXED_TRANSCRIPT_BINDINGS = _indexed_transcript_bindings()
if not MESSAGE_BINDINGS <= INDEXED_TRANSCRIPT_BINDINGS:  # pragma: no cover - a vendoring error, not a runtime path
    raise AssertionError(
        f'MESSAGE_BINDINGS gained {sorted(MESSAGE_BINDINGS - INDEXED_TRANSCRIPT_BINDINGS)}, which the turn-coordinate '
        f'lint has not classified. Decide whether each one is per-turn dense over the DRIVEN TURN '
        f'(TURN_DENSE_BINDINGS), dense over the turns the USER SPOKE ON (USER_SIDE_BINDINGS — a different '
        f'coordinate, and the one a second user-side projection belongs in), or an interleaved transcript '
        f'(INTERLEAVED_TRANSCRIPT_BINDINGS). Add it to the set; never give it its own arm. A two-way choice '
        f'sent the user side into the dense set by default, which is how this rule came to call '
        f'chatbot_messages a dense per-turn list.'
    )

#: The bindings that carry AUTHORED ground truth, and therefore the bindings a turn NUMBER can be
#: smuggled through. WHICH SURFACE binds each one is the whole point, and a ctx-only rule was blind
#: on the surface this script actually lints:
#:
#: * ``ctx`` is bound by the scenario's ``success_criteria.deterministic`` slice ONLY — the backend
#:   marks it ``{'turn_based': 'empty', 'end_to_end': 'empty'}`` and says in place "an e2e code_eval
#:   POLICY never receives it" — so a ``ctx['*_turn']`` read inside a POLICY is inert at grade time
#:   (KeyError via ``[]``, None via ``.get``) and is flagged as an authoring defect, not as a live
#:   coordinate.
#: * ``scenario_metadata`` and ``metadata`` are ``populated`` at both scopes (the backend binds both
#:   from ``state['scenario_metadata']``), so they are the ONLY route by which a policy expression or
#:   an ``applicability_guard`` can author a turn — and they are exactly what the shipped
#:   ``policies_schema_reference.md`` prescribes ("``c.get('turn') ==
#:   scenario_metadata.get('ask_turn')`` … reading the turn from ``scenario_metadata`` (NOT from
#:   ``ctx``, which is ``{}`` in a policy)"). A rule that read only `ctx` scored ZERO on
#:   every one of those.
AUTHORED_METADATA_BINDINGS = frozenset({'ctx', 'scenario_metadata', 'metadata'})

#: The turn-indexed reply accessor. Reading ONE turn through it is correct; NAMING that turn with a
#: literal is the defect, because the literal is authored and the turn is not.
_TURN_REPLY_ACCESSOR = 'agent_reply'

#: The remedy for a coordinate that names the AGENT'S REPLY to a turn. Kept in the backend's
#: vocabulary (``reply_to_beat`` / ``beat_delivered``, the wording
#: ``code_eval_selftest.deterministic_check_defect`` uses) rather than a fourth phrasing of the same
#: rule — this script and the two backend gates apply ONE rule and are pinned to one shared vector
#: file (`turn_index_lint_vectors.json`), so they must also teach one spelling.
#:
#: SCOPED, because one remedy does not fit every finding:
#: ``reply_to_beat`` returns the agent's reply TEXT. It is wrong for a coordinate into the user's own
#: words, wrong for an interleaved position, and wrong for a cross-turn ORDERING claim about a tool
#: call — see the three constants below.
_BEAT_COORDINATE_REMEDY = (
    "Name the BEAT instead: `reply_to_beat('<beat id>')` reads that beat's reply, and "
    "`beat_delivered('<beat id>')` guards it so a run that never delivered the beat is SKIPPED "
    'rather than graded against the neighbouring turn. A beat id plus its plan order is the only '
    'turn coordinate an author writes; the user-turn NUMBER is a runtime coordinate that only the '
    'simulator writes, through `delivered_beats`.'
)

#: For a coordinate into the USER-side projection: `user_messages[1]` is the user's own scripted
#: words, so `reply_to_beat` — which reads the AGENT's reply — is not a substitute for it.
_USER_SIDE_REMEDY = (
    'Note WHICH datum that is: `user_messages` is the USER\'s own words, which the scenario itself '
    "authored, so `reply_to_beat('<beat id>')` is NOT a substitute — it reads the AGENT's reply. If "
    'the assertion is really about what the user said, it is testing the scenario file and not the '
    "agent; if it is about the reply to that turn, name the beat (`reply_to_beat('<beat id>')`, "
    "guarded by `beat_delivered('<beat id>')`) and read the agent side."
)

#: For a coordinate into an INTERLEAVED projection, where the index names no speaker either.
_INTERLEAVED_INDEX_REMEDY = (
    'This one is worse than a turn pin: the index names a POSITION in an interleaved list, so it '
    'does not identify a turn OR a speaker — the entry can be the user\'s utterance or the agent\'s '
    'reply depending on how many messages the run produced. Move to the speaker-specific projection '
    'first (`agent_messages` for what the agent said, `user_messages` for what the user said), then '
    'name the beat rather than an index.'
)

#: For a cross-turn ORDERING claim — "the call happened on the turn the user asked". THE CLASS A
#: SINGLE REMEDY CANNOT SERVE: the shipped `policies_schema_reference.md` prescribes
#: `any(c['tool'] == '<t>' and c.get('turn') == scenario_metadata.get('ask_turn') … for c in
#: tool_calls)` for every UNPROMPTED / sequence target, and `reply_to_beat` / `beat_delivered` read
#: reply TEXT off the ledger and cannot express it at all. There IS an accessor for it —
#: `beat_turn(<beat id>)`, in the sandbox vocabulary — so this says so rather than
#: prescribing an accessor for a different datum.
_BEAT_ORDERING_REMEDY = (
    'IF the coordinate is binding a TOOL CALL to a turn — the UNPROMPTED / "A then B" idiom — the '
    'reply accessors do NOT express that: they read reply TEXT, which is a different datum from a '
    "record's `turn`. Read the turn out of the ledger instead: `c.get('turn') == beat_turn('<beat "
    'id>\')` for "on that beat\'s turn" and `>` for "any turn after it", with '
    "`beat_delivered('<beat id>')` as the applicability_guard — `beat_turn` answers None for a beat "
    'with no delivery on record, which selects nothing under `==` and RAISES under `>`, so the guard '
    'is not optional. (On a `mode:real` agent every record is recovered and carries no `turn` at '
    'all, so the ordering is inexpressible here either way: set is_deterministic=false / leave it '
    'to the judge.)'
)

#: Empty-container literals and constructors — the INERT half of a defensive wrapper (`or []`,
#: `else None`, `or dict()`).
_EMPTY_CONSTRUCTORS = frozenset({'list', 'dict', 'set', 'tuple', 'frozenset'})


def _is_empty_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return not node.value
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _EMPTY_CONSTRUCTORS and not (node.args or node.keywords)


def _wrapper_operands(node: ast.AST) -> list[ast.expr] | None:
    """The operands a DEFENSIVE WRAPPER guards, or None when ``node`` is not one.

    Authors write `(xs or [])` / `(xs if xs else [])` / `[*xs]` to survive a missing key, and the
    wrapper changes the SPELLING without changing what the value IS. Every matcher below keys on
    spelling, so an unwrapped matcher hands the author's defensiveness a way to switch a rule off —
    the class the backend closes across its own resolvers. Mirrors the backend's
    `_wrapper_operands` operand-for-operand, because the two gates are asserted against one shared
    vector file and a wrapper form one of them resolves has to be a form both resolve.
    """
    if isinstance(node, ast.BoolOp) and node.values:
        # `xs or []` evaluates to `xs` whenever `xs` is truthy. `guard and xs` can only evaluate to
        # the LAST operand or to a falsy short-circuit, so only the last one can be the value.
        return list(node.values) if isinstance(node.op, ast.Or) else node.values[-1:]
    if isinstance(node, ast.IfExp):
        return [node.body, node.orelse]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and node.elts and all(isinstance(e, ast.Starred) for e in node.elts):
        return [e.value for e in node.elts if isinstance(e, ast.Starred)]
    return None


def _through_wrapper(node: ast.AST, resolve) -> str | None:
    """Resolve a wrapper to what its guarded operand names, or None.

    CONSERVATIVE: every operand that is not an inert fallback must resolve, and they must AGREE —
    `(agent_messages or user_messages)[1]` mixes projections and resolves to nothing rather than
    lending one of them the other's remedy.
    """
    operands = _wrapper_operands(node)
    if not operands:
        return None
    live = [resolve(o) for o in operands if not _is_empty_literal(o)]
    if not live or None in live or len(set(live)) != 1:
        return None
    return live[0]


def _metadata_base(node: ast.AST) -> str | None:
    """The AUTHORED-metadata binding a top-level key read is rooted at, through a wrapper.

    TOP LEVEL only — it does NOT recurse through a subscript, matching the backend's
    `_record_base_name`: `c['response']['orders']` descends into a mock PAYLOAD whose keys belong to
    the mock author, and policing those would reject the documented way to read a tool's body. The
    starred form is excluded for the same reason the backend excludes it: `[*ctx]` is a list of keys
    and emphatically not the mapping `ctx`.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    return _through_wrapper(node, _metadata_base)


def _stream_root(node: ast.AST) -> str | None:
    """The binding a value is rooted at, through nested subscripts AND through a wrapper.

    `agent_messages[1]`, `agent_messages[1:][0]`, `(agent_messages or [])[1]` and
    `[*agent_messages][1]` all root at `agent_messages`. Unlike `_metadata_base` this DOES root
    through the starred form: `[*xs]` keeps the stream's members.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Subscript):
        return _stream_root(node.value)
    return _through_wrapper(node, _stream_root)


def _authored_turn_key(node: ast.AST) -> tuple[str, str] | None:
    """``(binding, key)`` for an authored ``*_turn`` metadata read this node makes, or None.

    Covers ``x['k']`` and ``x.get('k')`` and ``(x or {})['k']``, so
    neither defensive spelling is a way out. The backend resolves its ctx base through a
    wrapper helper, so this copy must too: with only a bare ``ast.Name``, `(ctx or {})['probe_turn']`
    would be 1 finding there and 0 here — a disagreement between two gates that the shared vector
    file is meant to rule out.

    THE BINDING is any of :data:`AUTHORED_METADATA_BINDINGS`, not ``ctx`` alone — see that constant
    for why a ctx-only matcher was blind on the POLICY surface this script lints.

    THE SUFFIX is measured, not remembered: every authored ``*_turn`` read in real policy sets
    is ``ask_turn`` or ``diff_turn``, and ``probe_turn`` / ``storage_turn`` /
    ``language_switch_turn`` do not occur there. Detection does not depend on a list of names.
    The bare ``turn`` is in as the identical defect rather than by the suffix rule, since
    which turn a behaviour lands on is written by the simulator alone.
    """
    if isinstance(node, ast.Subscript):
        base, key = node.value, node.slice
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get' and node.args:
        base, key = node.func.value, node.args[0]
    else:
        return None
    binding = _metadata_base(base)
    if binding not in AUTHORED_METADATA_BINDINGS:
        return None
    if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
        return None
    if not (key.value == 'turn' or key.value.endswith('_turn')):
        return None
    return (binding, key.value)


def _is_absolute_index(node: ast.AST) -> bool:
    """True when a subscript's ``slice`` pins an ABSOLUTE position.

    FIRES on a non-negative int literal and on an authored ``*_turn`` metadata read. EXEMPT, each
    for a reason rather than as an allow-list entry: an ``ast.Slice`` (``[-1:]`` — the commonest
    correct idiom in the shipped sets — and ``[1:]`` are WINDOW reads that name no turn), a NEGATIVE
    literal, and anything else (a Name, a Call, a subscript of something that is not an authored
    metadata binding: the index came from the run).

    MECHANISM for the negative literal, because the obvious reading is wrong: ``-1`` is not a
    negative ``ast.Constant``, it is ``UnaryOp(USub, Constant(1))``, and ``ast.parse`` folds
    nothing — so a tail read never reaches the ``Constant`` branch and is exempted by the catch-all.
    The ``>= 0`` guard is belt-and-braces for a HAND-BUILT (or folded) negative ``Constant``, not
    what makes ``agent_messages[-1]`` pass.

    ``bool`` is excluded from the literal arm because ``True`` is an ``int`` and ``xs[True]`` is a
    typo, not a coordinate.
    """
    if isinstance(node, ast.Constant):
        return isinstance(node.value, int) and not isinstance(node.value, bool) and node.value >= 0
    return _authored_turn_key(node) is not None


def _reads_record_turn_field(tree: ast.AST) -> bool:
    """True when the expression reads a ``turn`` field off a RECORD (``c['turn']`` /
    ``c.get('turn')``) — the signature of a cross-turn ORDERING claim rather than a claim about one
    turn's reply text. Authored metadata bindings are excluded: ``ctx['turn']`` IS the authored
    coordinate, not a comparison against one."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            base, key = node.value, node.slice
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get' and node.args:
            base, key = node.func.value, node.args[0]
        else:
            continue
        binding = _metadata_base(base)
        if binding is not None and binding not in AUTHORED_METADATA_BINDINGS and isinstance(key, ast.Constant) and key.value == 'turn':
            return True
    return False


def _turn_coordinate_warnings(expression: str) -> list[str]:
    """ONE rule, four shapes: every ABSOLUTE turn coordinate an author can write.

    1. an absolute index into an indexed transcript projection — ``agent_messages[1]``,
       ``user_messages[1]``, ``agent_states[2]``, ``chatbot_messages[2]``,
       ``agent_messages[ctx['probe_turn']]``, ``(agent_messages or [])[1]``. The REMEDY splits three
       ways by what the projection holds — see :data:`TURN_DENSE_BINDINGS`,
       :data:`USER_SIDE_BINDINGS` and :data:`INTERLEAVED_TRANSCRIPT_BINDINGS`;
    2. a ``ctx['*_turn']`` read ANYWHERE, subscript or not — ``len(agent_messages) >
       ctx['probe_turn']`` carries the coordinate with no subscript in sight, and it is a pinned
       position all the same;
    3. a ``scenario_metadata['*_turn']`` / ``metadata['*_turn']`` read — THE POLICY SURFACE. ``ctx``
       is ``{}`` in a policy, so this is the only binding through which a policy expression or an
       applicability_guard can author a turn, and it is what the shipped policy reference
       prescribes. A ctx-only rule scored ZERO on every occurrence of it;
    4. ``agent_reply(<non-negative int literal>)`` — the same pin wearing the sanctioned helper.

    THE SAME RULE the backend applies in ``deterministic_check_validation.
    turn_coordinate_findings``, and ``turn_index_lint_vectors.json`` is asserted against BOTH so
    the two cannot drift about what counts as a finding — per vector, including the shape and the
    remedy each finding carries, since truthiness over shared vectors pins neither.

    WARNING, not an error, and plugin-FIRST on purpose: the plugin ships on version and live-reads
    the backend's vocabulary, so it warns a release ahead of the backend refusing. The authoring
    prompts still TEACH these spellings, and erroring here would block an /edit-evals round over a
    policy the operator did not come to change. (On the SCENARIO side a literal message-binding
    index is already an ERROR in the backend gate, refused by ``code_eval_selftest`` before this
    lint existed — "warn" is this lint's own contribution, not a statement about the whole gate.)

    One message per DISTINCT offending coordinate, sorted — per rendered subscript/call for shapes 1
    and 4, per ``<binding>['<key>']`` for shapes 2 and 3. So two bare reads of one key are ONE
    warning, while a guard reading the key and a subscript indexing with it are TWO: they are two
    surfaces the author has to rewrite.
    """
    try:
        tree = ast.parse(expression, mode='eval')
    except SyntaxError:
        return []   # the parse error is reported by _expression_errors

    def render(node: ast.AST) -> str:
        try:
            return ast.unparse(node)
        except Exception:   # noqa: BLE001 - a warning line must never be the thing that raises
            return '<the expression>'

    ordering_claim = _reads_record_turn_field(tree)
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            binding = _stream_root(node.value)
            if binding in _indexed_transcript_bindings() and _is_absolute_index(node.slice):
                shown = render(node)
                if binding in INTERLEAVED_TRANSCRIPT_BINDINGS:
                    shape = f'`{binding}` is the FULL INTERLEAVED transcript (its name predates that shape), so entry i is NOT turn i'
                    remedy = _INTERLEAVED_INDEX_REMEDY
                elif binding in USER_SIDE_BINDINGS:
                    shape = (
                        f'`{binding}` is dense over the turns the USER SPOKE ON, which is the driven-turn coordinate only '
                        f'while the user opens: against a bench that declares `agent.io.may_open` the agent speaks first, '
                        f'driven turn 0 carries no user message, and entry i is the user message of driven turn i+1'
                    )
                    remedy = _USER_SIDE_REMEDY
                else:
                    shape = f'`{binding}` is a per-turn dense projection, one entry per driven turn'
                    remedy = _BEAT_COORDINATE_REMEDY
                found[shown] = (
                    f'`{shown}` pins an ABSOLUTE turn index into `{binding}`. {shape}. Which turn a behaviour lands on '
                    f'is a property of the RUN — one extra clarifying user turn, a padded turn or a redelivery moves '
                    f'it — so the cell grades the neighbouring entry and reports a confident verdict about ordering '
                    f'nobody authored. {remedy} (A tail read — `{binding}[-1]`, `{binding}[-1:]` — and any slice are '
                    f'run-relative and are NOT what this flags.)'
                )
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == _TURN_REPLY_ACCESSOR and node.args:
            index = node.args[0]
            if isinstance(index, ast.Constant) and isinstance(index.value, int) and not isinstance(index.value, bool) and index.value >= 0:
                shown = render(node)
                found[shown] = (
                    f'`{shown}` reads a turn by ABSOLUTE number through `{_TURN_REPLY_ACCESSOR}`. The accessor is the '
                    f'right way to read ONE turn and the wrong way to NAME one: the literal is authored, so it moves '
                    f'out from under the behaviour the moment the run takes a different number of turns. '
                    f'{_BEAT_COORDINATE_REMEDY}'
                )

    # Shapes 2/3 per distinct BINDING+KEY rather than per node: a key read twice is ONE coordinate
    # to rewrite. It does NOT collapse with a subscript that indexes with the same key —
    # `agent_messages[ctx['probe_turn']]` is two warnings, because the key and the subscript around
    # it are two surfaces the author has to change.
    for node in ast.walk(tree):
        authored = _authored_turn_key(node)
        if authored is None:
            continue
        binding, key = authored
        where = (
            "the check's `ctx` metadata (bound by the success_criteria.deterministic slice only, so it is `{}` here)"
            if binding == 'ctx'
            else f'the scenario metadata a POLICY reads (`{binding}`)'
        )
        found.setdefault(
            f'{binding}[{key!r}]',
            f"`{binding}['{key}']` carries an authored TURN NUMBER in {where}. A turn number is the one coordinate a "
            f'scenario may not write: the simulator decides it at run time and records it in `delivered_beats`. Store '
            f'the BEAT id instead (`{{beat: <id>}}`) and resolve it at eval time. '
            + (_BEAT_ORDERING_REMEDY if ordering_claim else _BEAT_COORDINATE_REMEDY),
        )

    return [found[k] for k in sorted(found)]


def _bindings_read(tree: ast.AST) -> set[str]:
    """Sandbox bindings an expression depends on — named directly, or reached through a
    helper that reads one implicitly (`tool_call_count()` depends on `tool_calls`)."""
    read: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in SANDBOX_NAMES_BY_SCOPE:
            read.add(node.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            read.update(SANDBOX_HELPER_READS.get(node.func.id, ()))
    return read


#: The scope tokens `names_by_scope` may carry, split by what each means for a verdict.
#:
#: VACUOUS is the set that decides the verdict before the agent speaks. `empty` is a binding with
#: no data at that scope; `constant_false` is a binding that IS bound and non-empty and still
#: grades nothing, because every scoring path at that scope hardcodes the same value — the case
#: the two-token vocabulary could not state, retyped onto `final_step`.
#:
#: THE SET IS CLOSED AND CHECKED, which is the half that matters. `constant_false` satisfied
#: neither `<= {'empty'}` nor `<= {'empty', 'partial'}`, so it fell through every branch below to
#: a CLEAN verdict — this verifier passed `turn_based` policies reading `final_step` that
#: the backend's own `scope_vacuity_offenders` then refused at `end_edit_session`. A token this file
#: does not know must be LOUD, never permissive: an unrecognised one is reported and the
#: expression is left ungraded on this axis, rather than silently blessed.
VACUOUS_SCOPE_TOKENS = frozenset({'empty', 'constant_false'})
KNOWN_SCOPE_TOKENS = frozenset({'populated', 'partial'}) | VACUOUS_SCOPE_TOKENS

#: How each vacuous token reads in the finding, so the remedy matches the cause. "It is empty"
#: sends an author to look for data; "it is bound and constant" sends them to the scope.
_VACUITY_READING = {
    'empty': 'EMPTY',
    'constant_false': 'bound but CONSTANT (hardcoded the same value by every scoring path at this scope)',
}


def _scope_vacuity_findings(expression: str, scope: object) -> tuple[list[str], list[str]]:
    """(vacuous, partial) findings for an expression whose bindings cannot decide anything at its scope.

    The failure this catches is silent by construction: the binding exists, so nothing
    raises; it merely cannot vary, so the verdict is decided before the agent speaks. Only
    an expression where EVERY binding is vacuous lands in the first list — a single populated
    binding means the verdict can still depend on the run. The set loads either way, so
    `_validate_policy` reports both as advisories; the split keeps the two readings apart.
    """
    if scope not in ACCEPTED_SCOPES:
        return [], []  # a bad scope is already its own error; don't pile on
    try:
        tree = ast.parse(expression, mode='eval')
    except SyntaxError:
        return [], []  # the parse error is reported by _expression_errors
    read = _bindings_read(tree)
    if not read:
        return [], []  # references no bindings at all — _constant_verdict_warning's job
    by_name = {name: SANDBOX_NAMES_BY_SCOPE.get(name, {}).get(str(scope), 'populated') for name in read}
    verdicts = set(by_name.values())
    if unknown := verdicts - KNOWN_SCOPE_TOKENS:
        # A token from a backend newer than this file. Reported rather than graded: guessing
        # `populated` is what let `constant_false` through, and guessing vacuous would reject
        # valid policies on a token whose meaning nobody here knows.
        return [], [
            f'the scope matrix types {sorted(n for n, v in by_name.items() if v in unknown)} with '
            f'{sorted(unknown)} at evaluation_scope={scope!r}, which this verifier does not know, so its '
            'bindings were NOT graded for vacuity. Update the plugin; '
            'until then this expression is unchecked on that axis.'
        ]
    if verdicts <= VACUOUS_SCOPE_TOKENS:
        named = ', '.join(f'{name} ({_VACUITY_READING[by_name[name]]})' for name in sorted(read))
        return [
            f'every binding it reads is vacuous at evaluation_scope={scope!r} — {named} — '
            f'so the verdict cannot depend on the run: it is the same on every scenario. '
            f'Move the policy to end_to_end, or rewrite it against a binding populated at '
            f'{scope!r} (the transcript, scenario_metadata, agent_state).'
        ], []
    if verdicts <= {'empty', 'partial', 'constant_false'}:
        return [], [
            f'the bindings it reads ({sorted(read)}) are only PARTIALLY populated at '
            f'evaluation_scope={scope!r} — they carry some data there but not the authoritative '
            f'set, so a check over them can be quietly incomplete rather than wrong. Confirm the '
            f'part you rely on is present at this scope, or author it at the scope where the '
            f'binding is fully populated.'
        ]
    return [], []


def _constant_verdict_warning(expression: str) -> str | None:
    """Flag expressions whose value is fixed regardless of any binding.

    Deliberately narrow — only shapes that are unconditionally constant, never a guess
    about what the data will look like. A check that cannot fail reads as a green cell
    indistinguishable from a real pass.
    """
    try:
        top = ast.parse(expression, mode='eval').body
    except SyntaxError:
        return None
    # `len(...) >= 0` is always True; `len(...) < 0` always False.
    if isinstance(top, ast.Compare) and len(top.ops) == 1 and len(top.comparators) == 1:
        left, op, right = top.left, top.ops[0], top.comparators[0]
        is_len_call = isinstance(left, ast.Call) and isinstance(left.func, ast.Name) and left.func.id == 'len'
        is_zero = isinstance(right, ast.Constant) and right.value == 0
        if is_len_call and is_zero and isinstance(op, ast.GtE):
            return 'expression is `len(...) >= 0`, which is True for every input — the check can never fail.'
        if is_len_call and is_zero and isinstance(op, ast.Lt):
            return 'expression is `len(...) < 0`, which is False for every input — the check can never pass.'
        # Both sides literal: the verdict is authored, not measured.
        if isinstance(left, ast.Constant) and isinstance(right, ast.Constant):
            return f'expression compares two literals ({left.value!r} vs {right.value!r}) — the verdict is fixed at authoring time.'
    # all([]) is vacuously True, any([]) vacuously False.
    if isinstance(top, ast.Call) and isinstance(top.func, ast.Name) and top.func.id in ('all', 'any') and len(top.args) == 1:
        arg = top.args[0]
        if isinstance(arg, (ast.List, ast.Tuple, ast.Set)) and not arg.elts:
            fixed = 'True' if top.func.id == 'all' else 'False'
            return f'expression is `{top.func.id}([])`, which is always {fixed} — the check can never fail.'
    return None


# A flow id in a `current_elements` membership test is dead on arrival: `current_elements`
# carries the scenario's COMPLICATION names, and flow ids ride `target_flows`. The guard is
# then false on every scenario, the policy is skipped set-wide, and it grades nothing while
# reading as a policy that exists.
#
# THE HEURISTIC IS THE LAST RESORT, NOT THE RULE. The `_flow` suffix is only an INSTRUCTION the
# backend gives its generator in a pydantic `Field(description=…)`; the backend's own `_slugify`
# never appends it, and `isalnum()` is Unicode-aware, so it keeps a Hebrew or Japanese id whole.
# A customer whose flows are named `refund_request` / `escalate_to_human` therefore has this
# lint DARK — the dead guard ships and the eval grades nothing — and the run looks identical to
# a clean one. So the vocabulary is read from THIS RUN when it can be: an explicit
# `--flow-ids <file>`, else the scenario set's own `target_flows`. The suffix stands in only
# when neither is available, and then it prints a receipt saying what it is not checking.
_FLOW_ID = re.compile(r'_flow$')

#: The run's REAL flow ids, or None when nothing supplied them. `frozenset()` (empty, not None)
#: is a legitimate answer — a scenario set whose scenarios declare no target flow — and is
#: distinct from "we never looked", which is what the receipt below reports.
_FLOW_VOCABULARY: frozenset[str] | None = None

#: Same posture as the namespace read above: an improvement, never a precondition.
_FLOW_VOCABULARY_TIMEOUT = 10


def _flow_ids_from_file(path: Path) -> frozenset[str]:
    """Flow ids the CALLER named — a JSON list, or one id per line.

    A hard error when unusable, for the same reason `--sandbox-names` is: passing the flag is a
    statement about which vocabulary to grade against, and quietly grading against another one is
    how this lint goes dark.
    """
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError as exc:
        raise SystemExit(
            f'verify_policies: --flow-ids {path} is unreadable ({exc}). Omit the flag to read the '
            f"vocabulary off the scenario set, or point it at a file listing this run's flow ids."
        ) from exc
    try:
        loaded = json.loads(raw)
    except ValueError:
        loaded = [line.strip() for line in raw.splitlines()]
    if isinstance(loaded, dict):
        loaded = loaded.get('flow_ids') or loaded.get('target_flows') or []
    ids = frozenset(str(x).strip() for x in loaded if isinstance(x, (str, int)) and str(x).strip())
    if not ids:
        raise SystemExit(
            f'verify_policies: --flow-ids {path} lists no flow ids. An empty file is a failed '
            f'export, not an empty vocabulary; omit the flag to read the set, or fix the file.'
        )
    return ids


def _flow_ids_from_backend(scenario_set_id: str) -> frozenset[str] | None:
    """The scenario set's own `target_flows`, over the same plain-HTTP lane as the namespace read.

    `GET /artifacts/{id}` serves the META envelope, which is the only place `detail.scenarios`
    is reachable — `get_artifact_meta` is an MCP tool and this script is stdlib-only, so the REST
    route is the lane. Every failure returns None and falls through to the receipt.
    """
    root = _backend_root()
    if root is None:
        return None
    payload = _get_json(f'{root}/artifacts/{scenario_set_id}', _FLOW_VOCABULARY_TIMEOUT)
    if not isinstance(payload, dict):
        return None
    detail = payload.get('detail')
    scenarios = detail.get('scenarios') if isinstance(detail, dict) else None
    if not isinstance(scenarios, list):
        return None
    ids = {
        str(flow).strip()
        for scenario in scenarios
        if isinstance(scenario, dict)
        for flow in (scenario.get('target_flows') or [])
        if isinstance(flow, str) and flow.strip()
    }
    # An artifact that IS a scenario set but declares no target flow anywhere is an EMPTY
    # vocabulary, not a failed read: `detail.scenarios` being a list is what proves the envelope
    # was read (an unknown id 404s and never reaches here). "Empty" and "we never looked" mean
    # opposite things to the lint, so they must not collapse into one value.
    return frozenset(ids)


def _resolve_flow_vocabulary(flow_ids: Path | None, scenario_set_id: str | None, base_dir: Path) -> None:
    """Set :data:`_FLOW_VOCABULARY`, and SAY which of the three tiers answered.

    Precedence: `--flow-ids` (the caller named it) > the live scenario set > nothing. The third
    tier is the one that has to be loud: a suffix heuristic that checks nothing renders exactly
    like a set with no dead guards in it.
    """
    global _FLOW_VOCABULARY  # noqa: PLW0603 - one module-level switch, resolved once from argv

    if flow_ids is not None:
        _FLOW_VOCABULARY = _flow_ids_from_file(flow_ids)
        print(f'flow-scope lint: {len(_FLOW_VOCABULARY)} flow id(s) from {flow_ids}.', file=sys.stderr)
        return
    # The marker lives beside the policies file under BOTH layouts — the promoted
    # `plurai_sim/policies_config.yaml` and `/edit-evals`' scratch draft in the same directory.
    set_id = (scenario_set_id or '').strip()
    if not set_id:
        marker = base_dir / '.scenario_set_id'
        try:
            set_id = marker.read_text(encoding='utf-8').strip()
        except OSError:
            set_id = ''
    if set_id:
        _FLOW_VOCABULARY = _flow_ids_from_backend(set_id)
        if _FLOW_VOCABULARY is not None:
            print(f'flow-scope lint: {len(_FLOW_VOCABULARY)} flow id(s) from scenario set {set_id}.', file=sys.stderr)
            return
    print(
        "flow-scope lint: no flow vocabulary available, falling back to the '_flow' suffix "
        "heuristic — ids without it are NOT checked. Pass --flow-ids <file>, or run with "
        '$PLURAI_URL set and a `.scenario_set_id` beside the policies, to check the '
        "run's real flow ids.",
        file=sys.stderr,
    )


def _looks_like_a_flow_id(name: str) -> bool:
    return name in _FLOW_VOCABULARY if _FLOW_VOCABULARY is not None else bool(_FLOW_ID.search(name))


def _guard_axis_error(guard: str) -> str | None:
    try:
        tree = ast.parse(guard, mode='eval')
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Compare) and any(isinstance(op, ast.In) for op in node.ops)):
            continue
        if not (isinstance(node.left, ast.Constant) and isinstance(node.left.value, str) and _looks_like_a_flow_id(node.left.value)):
            continue
        haystack = ast.dump(ast.Expression(body=node.comparators[0])) if node.comparators else ''
        if 'current_elements' in haystack:
            # With the real vocabulary in hand the finding names WHERE the id came from, because
            # "it ends in _flow" and "it is a flow this run actually walks" are different claims
            # and only the second one is proof.
            provenance = (
                "this run's flow vocabulary" if _FLOW_VOCABULARY is not None
                else "the '_flow' suffix heuristic — no flow vocabulary was available"
            )
            return (
                f'guard tests whether the flow id {node.left.value!r} is in `current_elements`, '
                f'but `current_elements` carries the scenario COMPLICATION names — flow ids never '
                f'appear there. The guard is false on every scenario, so the policy is skipped '
                f'set-wide and grades nothing. Declare the flow on the policy instead '
                f'(`target_flows: [{node.left.value}]` for dialog, `flow_filter:` for pipeline). '
                f'(Identified as a flow id by {provenance}.)'
            )
    return None


# Judge system_prompts are rendered as Jinja templates at scoring time, with StrictUndefined,
# against a step_info context that is EMPTY on every scoring path — so ANY {{ variable }} raises
# UndefinedError and the policy returns an error cell on every scenario instead of a verdict.
# The backend commit gate proves this by actually rendering the prompt; locally we approximate
# CONSERVATIVELY: only clear identifier references match ({{ scenario_target }}, {{ x.y }},
# {{ x | upper }}), so literal braces in prose or a JSON example never false-positive — the
# backend's real render gate has the final word on anything subtler.
_JINJA_VARIABLE = re.compile(r'\{\{\s*([A-Za-z_][A-Za-z0-9_]*)[^{}]*\}\}')


def _judge_prompt_errors(prompt: str, base_dir: Path | None = None) -> list[str]:
    """Static scoring-contract errors for an llm_as_judge system_prompt.

    A ``file:`` prompt (a supported form — the backend resolves it relative to the config)
    is READ and scanned, because the defect is exactly as fatal there and scanning the
    literal reference would find nothing and pass it. An unreadable reference is left to
    the backend rather than refused here: this verifier must never block a config it
    merely could not open.
    """
    text = prompt.strip()
    if text.startswith('file:'):
        ref = text[len('file:') :].strip()
        target = Path(ref) if Path(ref).is_absolute() else ((base_dir or Path()) / ref)
        try:
            text = target.read_text(encoding='utf-8')
        except OSError:
            return []
    variables = sorted({m.group(1) for m in _JINJA_VARIABLE.finditer(text)})
    if not variables:
        return []
    return [
        f'llm_as_judge system_prompt references Jinja variable(s) {variables} — judge prompts are '
        f'rendered with StrictUndefined against a scoring-time context that is EMPTY on every '
        f'scoring path, so each raises UndefinedError and the policy returns an error cell on '
        f'every scenario instead of a verdict. Write the text literally; per-scenario ground '
        f'truth (scenario target, memory anchors) reaches the judge as a server-injected system '
        f'message, never as a template variable.'
    ]


def _beat_resolution_errors(policy: dict) -> list[str]:
    """Field rules for `beat_resolution` — the per-beat knowledge / absence verdict.

    One backend judge resolves, per scenario, what happened to every planted beat (a knowledge
    question the simulated user asks, or an absence probe about something the agent's world does
    not hold). The rubric, the delivery window and the typed not-applicable verdict all live in
    the backend, so the POLICY has no body: it only names which slice of that resolution it reports,
    in agent-level `additional_params`. The slice and the `metric_type` are therefore checked as
    a PAIR — apart, a fraction mistyped `boolean` validates clean and skips every threshold rule.

    What is NOT checked here, so nobody trusts it to be: the `violated` polarity (`['below']` for
    a fraction, `['false']` for a probe) and the `abs_` prefix on a beat id — both are conventions
    the backend owns.
    """
    errors: list[str] = []

    raw_params = policy.get('additional_params')
    params = raw_params if isinstance(raw_params, dict) else {}
    metric, beat_id = params.get('metric'), params.get('beat_id')
    metric_type = policy.get('metric_type')

    if (metric is None) == (beat_id is None):
        errors.append(
            f"beat_resolution names its slice in EXACTLY ONE of `additional_params.metric` "
            f"('coverage' | 'accuracy') or `additional_params.beat_id` ('abs_<fact_id>'); got "
            f'metric={metric!r}, beat_id={beat_id!r}'
        )
        # No slice named, so there is nothing to pair the metric_type against — but the type has
        # only ever two shapes, so say that much rather than nothing.
        if metric_type not in ('float', 'boolean'):
            errors.append(f"beat_resolution: metric_type={metric_type!r} not in ('float', 'boolean')")
    elif metric is not None:
        if metric not in ('coverage', 'accuracy'):
            errors.append(f"beat_resolution: additional_params.metric={metric!r} not in ('coverage', 'accuracy')")
        if metric_type != 'float':
            errors.append(
                f'beat_resolution: a {metric!r} slice is a fraction over the planted beats, so '
                f'metric_type must be float, got {metric_type!r}'
            )
        # Required off the SLICE, not off metric_type: the generic int/float rule in
        # `_validate_policy` fires only when `violated` names 'above'/'below' AND the metric_type
        # is numeric, so a fraction that declares no polarity — or was mistyped boolean — would
        # otherwise carry no threshold at all and never be compared to anything. On the ordinary
        # `['below']` shape both rules speak, and that duplicate is ACCEPTED: suppressing this one
        # when the generic one fires means reproducing both of its conditions here, and getting
        # that coupling subtly wrong reopens the hole in silence. Two messages naming one field
        # cost a reader a second; a missing threshold costs a policy that compares to nothing.
        if 'violation_threshold' not in policy:
            errors.append(
                f'beat_resolution: a coverage/accuracy slice requires `violation_threshold: '
                f"<number>` — the fraction below which the slice is a violation (with violated: ['below'])"
            )
    else:
        if not _is_nonempty_str(beat_id):
            errors.append(f'beat_resolution: additional_params.beat_id must be a non-empty string, got {beat_id!r}')
        if metric_type != 'boolean':
            errors.append(
                f'beat_resolution: a beat_id slice reports ONE beat outcome, so metric_type must '
                f'be boolean, got {metric_type!r}'
            )

    # The loader's polarity rule for this type: a beat verdict is TRUE when the agent stayed honest,
    # so a beat_id slice is the compliance form, and a fraction violates below its threshold.
    violated = policy.get('violated', [])
    if metric is not None and beat_id is None and violated != ['below']:
        errors.append(f"beat_resolution: a {metric!r} slice must use violated: ['below'], got {violated!r}")
    if beat_id is not None and metric is None and violated != ['false']:
        errors.append(f"beat_resolution: a beat_id slice must use the compliance form violated: ['false'], got {violated!r}")

    # The loader refuses a non-empty `system_prompt` here; an `expression` it ignores with a
    # warning, so that one is `_validate_policy`'s advisory.
    if policy.get('system_prompt'):
        errors.append(
            'beat_resolution must not carry `system_prompt` — the resolution judge lives in the '
            'backend and this policy only names its slice; a body field here grades nothing.'
        )

    return errors


def _lax_float(value: object) -> float | None:
    """`value` as the loader's (pydantic, lax) `float` field reads it — a number, a bool, or a string
    `float()` parses (`'0.5'`) — or None when it would refuse it."""
    if isinstance(value, (bool, int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _loader_shape_errors(name: str, policy: dict) -> list[str]:
    """The loader's field rules that are not a type's own body: `confidence_threshold`, the
    closed-set fields, `generate_graph`, the `violated` forms, `eval_metric_description`,
    `hybrid_entrypoint`. Each is a refusal there, so each is an error here. An unknown key is
    the one advisory (see `POLICY_KEYS`)."""
    errors: list[str] = []
    unknown = sorted(str(key) for key in policy if key not in POLICY_KEYS)
    if unknown:
        _warn(
            f'{name}: carries key(s) {unknown} this plugin does not know. The backend forbids keys its policy model '
            f'does not declare, so unless the backend is newer than this plugin the set will not load — drop them.'
        )

    threshold = _lax_float(policy.get('confidence_threshold'))
    if 'confidence_threshold' not in policy:
        errors.append(f"{name}: missing required field 'confidence_threshold' (a number in [0, 1])")
    elif threshold is None or not 0 <= threshold <= 1:
        errors.append(f"{name}: confidence_threshold must be a number in [0, 1], got {policy.get('confidence_threshold')!r}")
    if policy.get('violation_threshold') is not None and _lax_float(policy['violation_threshold']) is None:
        errors.append(f"{name}: violation_threshold must be a number, got {policy['violation_threshold']!r}")

    description = policy.get('eval_metric_description')
    if 'eval_metric_description' in policy and not (isinstance(description, str) and description):
        errors.append(f'{name}: eval_metric_description must be a non-empty string, got {description!r}')

    for field, allowed in CLOSED_FIELDS.items():
        value = policy.get(field)
        if value is not None and value not in allowed:
            errors.append(f'{name}: {field}={value!r} not in {allowed}')
    if policy.get('counts_calls') is not None and policy.get('type') == 'code_eval' and policy.get('evaluation_scope', DEFAULT_SCOPE) == 'turn_based':
        _warn(
            f'{name}: counts_calls is set on a per-turn code_eval, where `tool_calls` is empty, so it changes nothing. It '
            f'binds an end_to_end code_eval\'s `tool_calls` (or a judge\'s unit sentence): rescope the check or drop the key.'
        )

    if policy.get('generate_graph') is True and policy.get('type') in DYNAMIC_TYPES:
        errors.append(f"{name}: type={policy.get('type')!r} cannot use generate_graph: true — it is built at runtime; omit the key")
    if policy.get('type') == 'retrieval_metric' and policy.get('metric_type') != 'float':
        errors.append(f"{name}: retrieval_metric scores precision/recall, so metric_type must be float, got {policy.get('metric_type')!r}")
    if policy.get('type') == 'hybrid' and 'hybrid_entrypoint' in policy and not policy.get('hybrid_entrypoint'):
        errors.append(f'{name}: hybrid_entrypoint is empty — omit it (the default is `evaluate`) or name the entrypoint')

    labels = policy.get('labels', [])
    if not (isinstance(labels, list) and all(isinstance(label, str) for label in labels)):
        errors.append(f'{name}: labels must be a list of strings, got {labels!r}')
        labels = []
    violated = policy.get('violated', [])
    metric_type = policy.get('metric_type')
    if not (isinstance(violated, list) and all(isinstance(v, str) for v in violated)):
        errors.append(f'{name}: violated must be a list of strings, got {violated!r}')
    elif violated:  # empty = observation-only, which the loader accepts for every metric type
        if metric_type == 'boolean':
            if labels:
                errors.append(f'{name}: a boolean metric takes no labels, got {labels!r}')
            if violated not in (['true'], ['false']):
                errors.append(f"{name}: a boolean metric's violated is ['true'] or ['false'], got {violated!r}")
        elif metric_type in ('int', 'float') and violated not in (['above'], ['below']):
            errors.append(f"{name}: a numeric metric's violated is ['above'] or ['below'], got {violated!r}")
        elif metric_type == 'categorical' and labels and [v for v in violated if v not in labels]:
            errors.append(f'{name}: violated names label(s) not in labels: {[v for v in violated if v not in labels]}')
    return errors


def _validate_policy(name: str, policy: object, base_dir: Path | None = None) -> list[str]:
    """The policy's load ERRORS, returned; its advisories go to `_warn`."""
    errors: list[str] = []
    if not isinstance(policy, dict):
        return [f'{name}: policy must be a mapping, got {type(policy).__name__}']
    policy = {**policy, **{key: _one_line(policy[key]) for key in EXPRESSION_FIELDS if isinstance(policy.get(key), str)}}

    for field in REQUIRED_FIELDS:
        if field not in policy:
            errors.append(f'{name}: missing required field {field!r}')
    for field, loads_as in EXPECTED_FIELDS.items():
        if field not in policy:
            _warn(f'{name}: declares no {field!r}. It loads ({loads_as}), but say it explicitly so nobody reads a default as a decision.')
    errors.extend(_loader_shape_errors(name, policy))

    # Polarity-coherence heuristic (WARNING, not error — the backend's benign-run
    # self-test is authoritative and DROPS inverted policies): a compliance-shaped
    # expression (top-level all(...) or a pure <=/</== comparison) paired with
    # violated: ["true"] flags every CLEAN run as a failure: an inverted code_eval
    # (`<= 1`, `all(url in allowed)`) is anti-correlated with reality. Convention: violation form = expression True
    # <=> the BAD thing happened + violated:["true"]; compliance form = True <=>
    # good + violated:["false"].
    if policy.get('type') == 'code_eval' and policy.get('violated') == ['true'] and policy.get('metric_type') == 'boolean':
        expr_text = policy.get('expression')
        if isinstance(expr_text, str) and expr_text.strip():
            try:
                top = ast.parse(expr_text.strip(), mode='eval').body
            except SyntaxError:
                top = None
            # Comparing against ZERO/empty asserts the ABSENCE of something expected, which is
            # the BAD thing happening — correct violation form, not an inversion. Without this
            # carve-out the operator-form `len(operation_types()) == 0` with violated: ['true']
            # (the agent called no tools at all) would be flagged as "likely INVERTED", and an
            # operator who acted on the advice would invert a CORRECT policy. The backend agrees
            # with the policy: it drops nothing and warns about nothing.
            absence_shaped = isinstance(top, ast.Compare) and all(
                isinstance(c, ast.Constant) and c.value in (0, False) or isinstance(c, (ast.List, ast.Tuple, ast.Set, ast.Dict)) and not getattr(c, 'elts', getattr(c, 'keys', []))
                for c in top.comparators
            )
            compliance_shaped = not absence_shaped and (
                (isinstance(top, ast.Call) and isinstance(top.func, ast.Name) and top.func.id == 'all')
                or (isinstance(top, ast.Compare) and all(isinstance(op, (ast.LtE, ast.Lt, ast.Eq)) for op in top.ops))
            )
            if compliance_shaped:
                # _warn, not a bare print: this is a finding the caller must act on, and an
                # uncounted one let the summary below still say "✓ … is valid".
                _warn(
                    f"{name}: compliance-shaped expression (all(...) / <=,<,== comparison) with "
                    f"violated: ['true'] — CHECK THE POLARITY. Convention: violation form = expression "
                    f"True <=> the BAD thing happened. If this expression is True on a CLEAN run, use "
                    f"violated: ['false'] or restate it as the violation condition. (Comparisons "
                    f'against zero/empty are already exempt — they assert an absence, which IS the '
                    f'violation.)'
                )

    p_type = policy.get('type')
    if p_type not in ACCEPTED_TYPES:
        errors.append(
            f'{name}: type={p_type!r} not in accepted set {ACCEPTED_TYPES}. '
            f'(debate_system and custom are out of scope for the auto-generated path.)'
        )

    metric_type = policy.get('metric_type')
    if metric_type not in ACCEPTED_METRIC_TYPES:
        errors.append(f'{name}: metric_type={metric_type!r} not in {ACCEPTED_METRIC_TYPES}')

    scope = policy.get('evaluation_scope', DEFAULT_SCOPE)
    if scope not in ACCEPTED_SCOPES:
        errors.append(f'{name}: evaluation_scope={scope!r} not in {ACCEPTED_SCOPES}')

    # Type-specific required body field
    if p_type == 'code_eval':
        expression = policy.get('expression')
        if not _is_nonempty_str(expression):
            errors.append(f'{name}: code_eval requires non-empty `expression: <str>`')
        elif (compile_error := _compile_error(expression)) is not None:
            # The one expression finding the loader refuses on: it compiles the text.
            errors.append(f'{name}: {compile_error}')
        else:
            # Everything else here is a runtime error cell or a verdict that cannot move — the set
            # still loads, so each is advisory.
            for w in _expression_errors(expression):
                _warn(f'{name}: {w}')
            scope_errors, scope_warnings = _scope_vacuity_findings(expression, scope)
            for w in (*scope_errors, *scope_warnings):
                _warn(f'{name}: {w}')
            constant = _constant_verdict_warning(expression)
            if constant:
                _warn(f'{name}: {constant} A cell that cannot fail is indistinguishable from a real pass in the '
                    f'headline; either bind it to something the run decides, or drop it.')
            for w in _message_membership_warnings(expression):
                _warn(f'{name}: {w}')
            for w in _turn_coordinate_warnings(expression):
                _warn(f'{name}: {w}')
    elif p_type == 'llm_as_judge':
        if not _is_nonempty_str(policy.get('system_prompt')):
            errors.append(
                f'{name}: llm_as_judge requires non-empty `system_prompt: <str>` '
                f'(or `system_prompt: file:relative/path.md`)'
            )
        else:
            for w in _judge_prompt_errors(policy['system_prompt'], base_dir):
                _warn(f'{name}: {w}')
        # The judge MODEL reliably inverts the violation-form double-negative. The loader takes
        # the pairing, so it is advisory here; the backend's authoring gate reports it too.
        if policy.get('metric_type') == 'boolean' and policy.get('violated') == ['true']:
            _warn(
                f"{name}: boolean llm_as_judge uses violated: ['true'] (violation form) — the "
                f"judge MODEL reliably INVERTS the 'return true if the bad thing happened' "
                f'double-negative and scores compliant behavior as a violation. Use the '
                f"COMPLIANCE form: violated: ['false'] + 'Return true if <the behavior is "
                f"good/correct>'."
            )
    elif p_type == 'hybrid':
        # Produced by the backend optimizer (/optimize-eval) — a generated judge
        # program decomposing an llm_as_judge into regex + cheap-LLM sub-calls.
        if not _is_nonempty_str(policy.get('hybrid_module')):
            errors.append(
                f'{name}: hybrid requires non-empty `hybrid_module: <path>` pointing '
                f'at the generated judge program (relative to the eval-set out/ dir).'
            )
    elif p_type == 'retrieval_metric':
        # The knowledge retrieval pair (retrieval_precision / retrieval_recall).
        # Runtime scores across ALL tool calls when tool_name is absent — almost
        # never intended. The loader only warns, and so does this.
        params = policy.get('additional_params')
        tool_name = params.get('tool_name') if isinstance(params, dict) else None
        if not (_is_nonempty_str(tool_name) or (isinstance(tool_name, list) and tool_name)):
            _warn(
                f'{name}: retrieval_metric names no retrieval tool in '
                f'`additional_params.tool_name` — without it, retrieval is scored '
                f'across ALL tool calls.'
            )
    elif p_type == 'beat_resolution':
        errors.extend(f'{name}: {e}' for e in _beat_resolution_errors(policy))
        if 'expression' in policy:
            _warn(
                f'{name}: beat_resolution carries `expression`, which the backend ignores — the resolution '
                f'judge lives in the backend and this policy only names its slice; drop it.'
            )
    elif p_type in ACCEPTED_TYPES:
        # UNREACHABLE while every accepted type has a branch above — which is the point: with
        # no closing branch, a type added to ACCEPTED_TYPES with no field rules would validate as
        # "type accepted, body unread". `test_every_accepted_type_has_field_rules` catches that
        # before release; this says it out loud if one ever ships. The message is addressed to
        # the OPERATOR because the defect is in the plugin, not in their YAML. (An unaccepted
        # type was already reported above — erroring again would print one defect twice.)
        errors.append(
            f'{name}: type={p_type!r} is accepted by this validator, which has no field rules for '
            f"it — the policy's body would ship unverified. This is a defect in the PLUGIN, not in "
            f'your file. Update the plugin first. If this message persists after that: {CONTACT_TEXT}'
        )

    # The loader types the guard `str | None` and never compiles it; `guard_filter` reads a raising
    # guard as does-not-apply. So only a non-string refuses, and every other guard finding — a
    # parse error included — is advisory: the policy loads and is skipped where the guard fails.
    guard = policy.get('applicability_guard')
    if guard is not None:
        if not isinstance(guard, str):
            errors.append(f'{name}: applicability_guard must be a string when present, got {type(guard).__name__}')
        elif not guard.strip():
            _warn(f'{name}: applicability_guard is empty — drop the key, or write the condition it was meant to carry')
        else:
            for w in _expression_errors(guard):
                _warn(f'{name} (applicability_guard): {w}')
            axis_error = _guard_axis_error(guard)
            if axis_error:
                _warn(f'{name} (applicability_guard): {axis_error}')
            # A guard runs in the SAME namespace as the policy, so it is vacuous under the
            # same rule — and a guard reading only empty bindings is worse than a vacuous
            # check: it silently decides whether the policy runs at all.
            guard_scope_errors, guard_scope_warnings = _scope_vacuity_findings(guard, scope)
            for w in (*guard_scope_errors, *guard_scope_warnings):
                _warn(f'{name} (applicability_guard): {w}')
            # A guard is where this one actually bit: an inert guard skips the policy set-wide.
            for w in _message_membership_warnings(guard):
                _warn(f'{name} (applicability_guard): {w}')
            # A guard carries the coordinate as often as the expression does — `len(agent_messages)
            # > ctx['probe_turn']` is a GUARD shape — and a guard pinned to a turn the run reached
            # differently skips the policy instead of grading it.
            for w in _turn_coordinate_warnings(guard):
                _warn(f'{name} (applicability_guard): {w}')

    # The THIRD sandbox-evaluated surface. A reasoning_expression that raises (`enumerate`, say)
    # is otherwise invisible: the runtime swallows the raise and replaces every scenario's
    # reasoning text with an error note (the score still computes, so nothing else surfaces it).
    #
    # WARNING, not an error, matching the backend gate's tier (eval_jobs' `reasoning fidelity`
    # warning): the VERDICT is unaffected, so this must not delete a healthy policy. Only a
    # non-string refuses — the loader types the field `str | None`.
    reasoning = policy.get('reasoning_expression')
    if reasoning is not None:
        if not isinstance(reasoning, str):
            errors.append(f'{name}: reasoning_expression must be a string when present, got {type(reasoning).__name__}')
        elif not reasoning.strip():
            _warn(f'{name}: reasoning_expression is empty — drop the key, or write the evidence sentence it was meant to carry')
        else:
            for e in _expression_errors(reasoning):
                _warn(f'{name} (reasoning_expression): {e} The verdict still computes — the sandbox swallows this '
                    f'raise — but every result row ships "(reasoning_expression failed: ...)" instead of the evidence '
                    f'this field carries. Use a comprehension for iteration, never enumerate/zip/range.')

    # Metric-specific shape rules
    if metric_type == 'categorical':
        labels = policy.get('labels')
        if not labels:  # a non-list is `_loader_shape_errors`' refusal
            # The loader defaults `labels` to [] and loads it; the verdict then has no label set
            # to land in, so it is advisory.
            _warn(f'{name}: categorical metric declares no `labels: [<value>, ...]` — the verdict has no label set to land in')
    if metric_type in ('int', 'float'):
        violated = policy.get('violated', [])
        if violated and any(v in ('above', 'below') for v in violated):
            if policy.get('violation_threshold') is None:
                errors.append(
                    f"{name}: metric_type={metric_type} with violated containing 'above'/'below' "
                    f"requires `violation_threshold: <number>`"
                )

    return errors


def verify(yaml_path: Path) -> list[str]:
    if not yaml_path.is_file():
        return [f'File not found: {yaml_path}']
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding='utf-8'))
    except yaml.YAMLError as e:
        return [f'Invalid YAML at {yaml_path}: {e}']

    if not isinstance(data, dict) or not data:
        return [f'{yaml_path}: top-level YAML must be a non-empty mapping of <policy_name> → <policy>']

    # A count floor is an OPINION about how rich the agent is, and is not enforced as a fact: at a
    # hard 5, an agent with two flows and one guard has no legal config to write, and the lint
    # that exists to catch hollow sets becomes the thing that stops a correct one from shipping.
    # So the floor scales with the agent and only ZERO is an error — an empty mapping is already
    # refused above, and a set that cannot clear its own floor is reported to the operator instead
    # of to the exit code.
    floor = _policy_floor(len(_FLOW_VOCABULARY) if _FLOW_VOCABULARY is not None else None)
    if len(data) < floor:
        _warn(
            f'{yaml_path}: declares {len(data)} policies, below the floor of {floor} for this agent '
            f'({"2 + " + str(len(_FLOW_VOCABULARY)) + " flow(s)" if _FLOW_VOCABULARY is not None else "the generic expectation; no flow vocabulary was resolved"}). '
            f'A thin set is not refused here — a genuinely small agent has no richer one to write — '
            f'but it is also not confirmed adequate by this script, and THIS SCRIPT IS NOT THE LAST '
            f'FENCE. Named so they can be checked rather than trusted: '
            f'`eval_coverage_audit(eval_set_id=…, scenario_set_id=…)` reports every scenario target '
            f'and obligation no policy grades; `suite_diagnostics_start(run_stage=\'final\')` '
            f'reports `release_gate`, which BLOCKS a final run when unmet. Both run '
            f'after this one — which is why this exits 0 instead of refusing the file. Read them '
            f'before scoring; this warning is not a licence to skip them.'
        )

    errors: list[str] = []
    for name, policy in data.items():
        errors.extend(_validate_policy(name, policy, yaml_path.parent))
    _warn_duplicate_rubric_paragraphs(data)
    _warn_requirement_under_carve_out(data)
    return errors


def _inline_judge_rubrics(data: dict) -> list[tuple[str, str]]:
    """`(policy name, system_prompt)` for every judge whose rubric is INLINE.

    A `code_eval` has no rubric and a `file:` reference is text this validator does not hold,
    so both cross-policy rubric lints below read exactly this list.
    """
    rubrics: list[tuple[str, str]] = []
    for name, policy in data.items():
        if not isinstance(policy, dict) or policy.get('type') != 'llm_as_judge':
            continue
        prompt = policy.get('system_prompt')
        if _is_nonempty_str(prompt) and not prompt.strip().startswith('file:'):
            rubrics.append((name, prompt))
    return rubrics


# ── Permit-vs-forbid contradiction ────────────────────────────────────────────────────────
# The five predicate FAMILIES are the backend's own carve-out vocabulary
# (`diagnostic_agent/jobs/kinds/eval_jobs.py`, `_CARVE_OUT_*`) — a second vocabulary would
# drift from the one the backend repairs against and the two would disagree about the same
# rubric. The GUARDS around them are this copy's own, and they exist because the two copies
# run at different moments against a different cost:
#
# The backend runs the families at GENERATION, where a false positive costs a repair turn
# inside a session that was authoring the rubric anyway. This copy runs after an EDIT, where
# `/edit-evals` treats any `⚠` as "not clean — fix it in this session": a false positive here
# buys a whole edit round on a policy the operator never came to touch. And because the
# backend already ran these families at generation, a hit on an UNEDITED policy is by
# construction either a false positive or a pre-gate legacy set — neither worth a round.
# The bare families fire on realistic correct rubric shapes.

#: A rubric SECTION heading: a markdown heading, or a bold-only line used as one (the shape
#: the recipe's own worked examples head their sections with).
_RUBRIC_HEADING = re.compile(r'^\s{0,3}(?:#{1,6}\s+(?P<hash>.+?)|\*\*(?P<bold>[^*]+)\*\*:?)\s*$')

#: A bold line is a heading only when it is TITLE-shaped. A bold SENTENCE is emphasis, and
#: reading one as a heading is how `**A late correction is not a violation.**` opened a
#: carve-out section that then swallowed every requirement in the rest of the rubric.
#:
#: Two tells, and NOT the presence of a verb — the canonical heading `**What is NOT a
#: violation**` carries one. A sentence ends in terminal punctuation, or opens with an article
#: or demonstrative, because it asserts something about a PARTICULAR thing; a heading is a
#: label ("Exemptions", "What is NOT a violation", "Carve-outs"). Ambiguous shapes
#: (`**These are not violations**`) fall to the sentence side deliberately: not opening a
#: section under-fires, and the backend still runs these families at generation, whereas
#: over-firing costs an edit round on a correct rubric.
#: Terminal punctuation always means sentence; the article/demonstrative tell yields to
#: carve-out title vocabulary — ``**These are not violations**`` is the canonical heading's
#: plural, and the article tell alone read it as emphasis, leaving the section unread.
_BOLD_TERMINAL = re.compile(r'[.!?]\s*$')
_BOLD_ARTICLE = re.compile(r'^(?:a|an|the|this|that|these|those)\b', re.IGNORECASE)

#: The article tell yields to carve-out vocabulary only on a PLURAL/LIST head:
#: ``**These are not violations**`` / ``**The following are not violations**`` are the
#: canonical heading's plurals, while a singular article asserts about a PARTICULAR
#: thing (``**A late correction is not a violation**``) and stays emphasis even when it
#: names the vocabulary.
_BOLD_PLURAL_HEAD = re.compile(r'^(?:these|those|the\s+following)\b', re.IGNORECASE)

#: A heading that opens a FORGIVENESS section — everything under it inherits an exempting
#: polarity, so nothing under it can state a requirement.
_CARVE_OUT_TITLE = re.compile(
    r'not\s+a\s+violation|not\s+violations|not\s+a\s+failure|carve[- ]?outs?|exemptions?|forgiv\w*|what\s+to\s+excuse',
    re.IGNORECASE,
)

#: …unless the SAME title also names failure conditions. `Failure conditions — no exemptions`
#: matches `exemptions?` above while being the exact opposite of a carve-out, and reading it
#: as one inverts the whole section. Spelled precisely so `What is NOT a violation` (which
#: contains "violation") is not caught by it.
_FAILURE_TITLE = re.compile(r'\bfailure conditions?\b|\bfailure modes?\b|\bwhat fails\b|\bviolation conditions?\b', re.IGNORECASE)

#: …which in turn yields to an EXPLICIT forgiveness phrase: ``Forgiven failure modes`` and
#: ``Exemptions to the failure conditions`` are carve-outs that merely NAME what they exempt.
_FORGIVENESS_OVERRIDE = re.compile(r'\bforgiv\w*|\bnot\s+(?:a\s+)?(?:violations?|failures?)\b|\bexemptions?\s+to\b|\bcarve[- ]?outs?\b|\bexcus\w*', re.IGNORECASE)

#: A REQUIREMENT stated inside such a section. Four families, each unambiguous regardless of
#: its object: a prohibition; an assertion that something IS a violation/failure (the copula
#: lookahead spares the section's own legitimate "X is NOT a violation" lines); an
#: anti-exemption clause; and a FAIL verdict.
_CARVE_OUT_REQUIREMENT = re.compile(
    r'\b(?:must|shall|may)\s+(?:not|never)\b'
    r'|\b(?:is|are|was|were|becomes?)\s+(?!not\b|never\b|no\b)(?:an?\s+)?(?:\w+[-\s]){0,2}(?:violation|failure|breach)\b'
    r'|\bcounts?\s+as\s+(?:an?\s+)?(?:violation|failure)\b'
    r'|\b(?:does|do)\s+not\s+excuse\b|\bexcuses?\s+it\b|\bis\s+not\s+excused\b'
    r'|\breturn\s+false\b|\bfalse\s+otherwise\b',
    re.IGNORECASE,
)

#: The fifth family: a GRADING DIRECTIVE in the imperative. A forgiveness list says what to
#: excuse; it does not tell the judge how to grade.
_CARVE_OUT_GRADING_DIRECTIVE = re.compile(r'^\s*(?:[-*]\s*)?(?:grade|treat|score|fail|flag|penali[sz]e|judge|mark)\b', re.IGNORECASE)

#: Vetoes the grading-directive family: a directive whose own sentence names an EXEMPTING
#: outcome is the legitimate shape ("treat it as MET"). Each word here covers a false
#: positive the family would otherwise raise — `satisfying` against a vocabulary that pins only
#: `satisfied`, `compliant` against one that has neither.
_EXEMPTING_OUTCOME = re.compile(
    r'\b(?:met|pass(?:es|ed)?|true|satisf\w*|forgiv\w*|excus\w*|exempt\w*|compliant|allowed|permitted|'
    r'immaterial|correct|acceptable|fine|ok)\b|\bwithin\s+policy\b',
    re.IGNORECASE,
)

#: Vetoes the REQUIREMENT families, narrowly. NOT the full exempting vocabulary: "no line in
#: this list excuses it" is a real requirement that legitimately mentions exemption, and a
#: blanket veto would drop it. Each shape below INVERTS its family rather than merely
#: mentioning forgiveness:
#:   * an instruction NOT to fail ("do not return false merely because …");
#:   * a negative SUBJECT ("None of the following is a policy failure.");
#:   * the rubric's own output contract, whose `false otherwise` tail is not a requirement
#:     ("Return true if the reply is complete; false otherwise.") — scoped to the contract's
#:     shape, because bare `return true` is a bigram real anti-exemptions carry ("do not
#:     return true merely because …; an offer counts as a violation");
#:   * an EXEMPTION DIRECTIVE addressed to the judge. This is a distinct shape:
#:     `It must never be failed by this policy.` under a well-formed carve-out. The
#:     grammar is what tells them apart — a REQUIREMENT says the agent must not DO something,
#:     an EXEMPTION says the judge must not FAIL something, so the object is the policy's
#:     verdict rather than the agent's behaviour. Passive voice ("must never be failed"),
#:     an imperative to the judge ("do not flag this"), or a negated grading copula
#:     ("never counts as a violation") all carry that inversion.
#:
#: `fail TO <verb>` is excluded from the imperative branch: there the AGENT is the one
#: failing ("the agent must never fail to include the disclaimer"), which is a requirement.
_GRADING_VERB = r'fail(?:ed|s)?|flag(?:ged|s)?|penali[sz]e[ds]?|mark(?:ed|s)?|count(?:ed|s)?|scor(?:ed|es)?'
_REQUIREMENT_VETO = re.compile(
    r'\b(?:do not|don\'t|never)\s+return\s+false\b'
    r'|^\s*(?:[-*]\s*)?(?:none|neither|nothing)\b'
    r'|\breturn\s+true\b[^.!?]*\bfalse\s+otherwise\b'
    # the SPLIT output contract — "Return true when X. False otherwise." / "Otherwise return
    # false." — puts the identifying `return true` in the PREVIOUS sentence, so the tail is
    # recognized by its own whole-sentence shape (sentence-initial, nothing but the idiom).
    r'|^\s*(?:[-*]\s*)?`?false`?\s+otherwise\b'
    r'|^\s*(?:[-*]\s*)?otherwise,?\s+(?:return\s+)?`?false`?\b'
    # passive, judge as actor: "must never be failed by this policy"
    rf'|\b(?:must|shall|should|may|will|would|can)\s+(?:not|never)\s+be\s+(?:{_GRADING_VERB})\b'
    # imperative to the judge: "do not fail this", "never flag it" — but not "fail TO do X"
    rf'|\b(?:do not|don\'t|never)\s+(?:{_GRADING_VERB})\b(?!\s+to\b)'
    # negated grading copula: "is never a failure", "never counts as a violation"
    r'|\bnever\s+(?:counts?\s+as|is|are)\b'
    r'|\b(?:is|are)\s+never\s+(?:an?\s+)?(?:violation|failure|breach)\b',
    re.IGNORECASE,
)

#: Sentence granularity — the unit a veto must scope to.
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+|\n(?=\s*[-*]\s)')


def _heading_title(line: str) -> str | None:
    """The TITLE when this line is a section heading, else ``None``.

    A markdown heading always counts; a bold-only line counts only when TITLE-shaped
    (terminal punctuation means sentence; an article/demonstrative opener means sentence
    unless the line carries carve-out title vocabulary) — the same rule for opening AND
    closing a section, so a bold emphasis line inside a carve-out neither ends it nor starts
    a phantom one."""
    heading = _RUBRIC_HEADING.match(line)
    if heading is None:
        return None
    bold = heading.group('bold')
    if bold is not None:
        text = bold.strip()
        if _BOLD_TERMINAL.search(text):
            return None  # emphasis, not a heading
        if _BOLD_ARTICLE.match(text) and not (_BOLD_PLURAL_HEAD.match(text) and _CARVE_OUT_TITLE.search(text)):
            return None
    return heading.group('hash') or bold or ''


def _carve_out_sections(system_prompt: str) -> list[str]:
    """The body text under every FORGIVENESS heading — the reading a judge MODEL gives it.

    A section runs from its heading to the next heading of any level (bold lines counting
    only when title-shaped), the SAME boundary the backend's generation-gate copy reads
    (``eval_jobs.py``, ``_carve_out_section_bodies``) — the two produce identical section
    bodies for one rubric. (The per-SENTENCE guards still differ by tier on purpose; see the
    module comment above the families.) DELIBERATELY not to the next paragraph break: the check's motivating defect is
    requirement PROSE resuming after the carve-out list — a judge reads it as forgiven — and
    a paragraph rule would be blind to exactly that shape. The false
    positive a paragraph rule exists to avoid, the rubric's closing "Return true if …; false otherwise."
    output contract, is recognized by CONTENT instead (``return true`` in
    :data:`_REQUIREMENT_VETO`): layout cannot tell those two paragraphs apart and content
    can."""
    bodies: list[str] = []
    current: list[str] | None = None
    for line in system_prompt.splitlines():
        title = _heading_title(line)
        if title is None:
            if current is not None:
                current.append(line)
            continue
        if current is not None:
            bodies.append('\n'.join(current))
            current = None
        if _CARVE_OUT_TITLE.search(title) and not (_FAILURE_TITLE.search(title) and not _FORGIVENESS_OVERRIDE.search(title)):
            current = []
    if current is not None:
        bodies.append('\n'.join(current))
    return bodies


def _sentence_states_a_requirement(sentence: str) -> bool:
    """Whether ONE sentence, read under a forgiveness heading, states a requirement."""
    if _REQUIREMENT_VETO.search(sentence):
        return False
    if _CARVE_OUT_REQUIREMENT.search(sentence):
        return True
    return bool(_CARVE_OUT_GRADING_DIRECTIVE.match(sentence.lstrip())) and not _EXEMPTING_OUTCOME.search(sentence)


def _first_requirement_under_a_carve_out(system_prompt: str) -> str | None:
    """The first sentence in this rubric that states a requirement under a forgiveness heading.

    The FIRST is all the warning needs: one moved paragraph fixes the policy, and five lines
    about it is noise.
    """
    for section in _carve_out_sections(system_prompt):
        for sentence in _SENTENCE_SPLIT.split(section):
            if _sentence_states_a_requirement(sentence):
                return sentence
    return None


def _rubric_heading_census(rubrics: list[tuple[str, str]]) -> tuple[list[str], int]:
    """`(every heading title parsed, how many were recognised as carve-out / failure sections)`.

    THE COVERAGE RECEIPT'S TWO NUMBERS (`len(titles)` parsed, `recognised`). Every family above
    is English-keyed — `not a violation`, `exemptions`, `failure conditions`, `forgiv…` — and a
    rubric written in another language matches none of them, so `_carve_out_sections` returns nothing and the lint reports ZERO
    warnings. That is byte-identical to a clean set — a did-it-run conflation — and Hebrew suites ship.

    Internationalizing the vocabulary is not the answer: a rubric is prose in the customer's
    language and a regex per language is a lexicon nobody maintains. Saying what was READ is.
    """
    titles: list[str] = []
    recognised = 0
    for _name, prompt in rubrics:
        for line in prompt.splitlines():
            title = _heading_title(line)
            if title is None:
                continue
            titles.append(title)
            if _CARVE_OUT_TITLE.search(title) or _FAILURE_TITLE.search(title) or _FORGIVENESS_OVERRIDE.search(title):
                recognised += 1
    # The TITLES ride out with the counts because the readability question below has to be asked
    # of the same text the families were pointed at — see `_reads_as_latin_script`.
    return titles, recognised


#: Below this share of Latin-script letters among the letters MEASURED, an English-keyed regex
#: cannot match anything whatever the text says — the families are not merely finding nothing,
#: they are unable to look. A SCRIPT test rather than a language one: it needs no lexicon, it
#: cannot itself go dark, and it answers exactly the question the receipt is asked.
_LATIN_SHARE_FLOOR = 0.5


@lru_cache(maxsize=None)
def _is_latin_letter(ch: str) -> bool:
    """Is this character a letter of the LATIN script? Derived from the UCD, never a table.

    `ch.isascii()` answers a different question — a CODEPAGE one. It is
    wrong in both directions. Latin-script languages with diacritics (`añadió`, `phải`, `Gürültü`)
    are letters an English-keyed regex CAN sit beside, and calling them foreign script would fire
    the inert warning on a Spanish or Vietnamese set that the families really did read. And ASCII
    letters carry every non-Latin rubric's scaffolding — `AXIS:`, tool ids, `Return JSON` — so
    an ASCII test would call a foreign-script rubric Latin (see the caller).
    """
    try:
        return unicodedata.name(ch).startswith('LATIN ')
    except ValueError:
        return False  # unnamed in the UCD; not a letter this claims to read


def _reads_as_latin_script(texts: list[str]) -> bool:
    """Can the English-keyed families read `texts` at all?

    MEASURED OVER THE HEADING TITLES, not whole rubrics — the titles are the only thing the
    carve-out / failure families are ever pointed at, and measuring the whole prompt asks a
    question nobody depends on. It also gets the answer wrong, unspaced scripts worst of all: a
    generated Japanese rubric carries a lot of English scaffolding
    (`AXIS: refund_disclosure`, `refund_request`, `Return JSON: {"verdict": …}`, an evidence
    instruction) around a handful of dense CJK headings and clauses, so the whole prompt measures
    mostly Latin and the INERT receipt is DEMOTED TO A NOTE on the set that most needs it.
    Space-separated, letter-dense prose such as Hebrew keeps the same scaffold under the floor.
    """
    letters = [ch for text in texts for ch in text if ch.isalpha()]
    if not letters:
        return True  # nothing to read; not a language finding
    return sum(_is_latin_letter(ch) for ch in letters) / len(letters) >= _LATIN_SHARE_FLOOR


#: Policy names THIS session edited, when the caller knows them (`--edited`). `None` means
#: "not told" — every finding warns, which is what the pre-run gate over a whole generated set
#: wants. When it IS set, a carve-out finding on a policy this turn did not touch drops to a
#: NOTE: the backend ran these same families at generation, so such a hit is by construction a
#: false positive or a pre-gate legacy set, and `/edit-evals` treats any counted `⚠` as "fix it
#: in this session" — which would buy an edit round on a policy nobody came to edit.
_EDITED_POLICIES: set[str] | None = None


def _warn_requirement_under_carve_out(data: dict) -> None:
    """Warn when a rubric PERMITS and FORBIDS the same surface — a requirement written under
    a "what is NOT a violation"-class heading.

    Every line under a forgiveness heading inherits its exempting polarity, so a judge model
    reads "the agent must not X" written there as "an agent that does X is excused" — the
    rubric grades the opposite of what its author meant, and the verdict looks like a
    considered PASS. An edit round is where this arrives: a fix appended to the nearest
    heading lands under the carve-out, and nothing between the edit and the next `score_run`
    reads polarity. A warning, never an error — the fix is moving a paragraph, and the judge
    is wanted.
    """
    rubrics = _inline_judge_rubrics(data)
    # THE COVERAGE RECEIPT, always — never only on a hit: the SCANNED counts are what
    # separate "this rubric set is clean" from "these families read nothing here".
    titles, recognised = _rubric_heading_census(rubrics)
    parsed = len(titles)
    print(
        f'rubric carve-out lint: {len(rubrics)} inline judge rubric(s), {parsed} heading(s) parsed, '
        f'{recognised} recognised as a carve-out / failure section.',
        file=sys.stderr,
    )
    if parsed and not recognised:
        inert = (
            f'this lint appears INERT for this eval set: {parsed} rubric heading(s) parsed and NONE '
            f'matched the carve-out / failure-section vocabulary, which is English-keyed. A clean '
            f'result here is therefore not evidence about polarity — the permit-vs-forbid check '
            f'read no section.'
        )
        # A COUNTED warning only when the HEADINGS are not in a script these families can match
        # at all: that is a set whose clean result is meaningless, and it needs a decision. A
        # Latin-script set with no forgiveness heading is the ORDINARY case — the check really did
        # look and there was nothing to read — and warning about it would buy an edit round on
        # every well-formed rubric that simply has no carve-out section. Spanish sits on the
        # ORDINARY side on purpose: the line is SCRIPT, not language, because a script test needs
        # no lexicon and cannot itself go dark the way the families did.
        if _reads_as_latin_script(titles):
            print(f'  NOTE: {inert} No rubric in this set opens a forgiveness section.', file=sys.stderr)
        else:
            _warn(
                f'{inert} These rubric headings are not written in the Latin script the families '
                f'are keyed to, so they could not be read at all. Review the forgiveness sections '
                f'by hand, or head them in English ("What is NOT a violation", "Failure '
                f'conditions") and re-run.'
            )
    for name, prompt in rubrics:
        offending = _first_requirement_under_a_carve_out(prompt)
        if offending is None:
            continue
        quoted = ' '.join(offending.split())[:180]
        message = (
            f'{name}: rubric PERMITS and FORBIDS the same surface — a requirement sits under a '
            f'"what is NOT a violation" heading: "{quoted}". Every line under a forgiveness heading '
            'inherits its EXEMPTING polarity, so the judge reads that requirement as an excuse and '
            'returns a confident PASS on the behaviour it names. Move the clause into the failure '
            'conditions as its own step (present, past AND offer tense), and leave under the '
            'carve-out heading only genuine exemptions. A neutral cross-reference '
            '("Requirements: see step 2.") is fine there; an inverse rule ("everything not '
            'listed here is a violation") is itself a requirement and belongs in that step.'
        )
        if _EDITED_POLICIES is not None and name not in _EDITED_POLICIES:
            print(f'  NOTE: {message} (pre-existing — this turn did not edit it, so it is not this round\'s work)', file=sys.stderr)
        else:
            _warn(message)


# Long enough that sharing it verbatim is copy-paste, not two rubrics happening to phrase a
# short instruction the same way.
_SHARED_PARAGRAPH_MIN_CHARS = 120


def _warn_duplicate_rubric_paragraphs(data: dict) -> None:
    """Warn when two judges carry the SAME rubric paragraph verbatim.

    A paragraph shared between judges makes both grade the same axis: one contradictory
    reply is then scored as two separate failures, and the policy named for the axis never
    gets exercised on its own terms. Cross-policy, so it lives here rather than in the
    per-policy pass.
    """
    seen: dict[str, list[str]] = {}
    for name, prompt in _inline_judge_rubrics(data):
        for block in re.split(r'\n\s*\n', prompt):
            normalized = ' '.join(block.split())
            if len(normalized) >= _SHARED_PARAGRAPH_MIN_CHARS:
                seen.setdefault(normalized, []).append(name)
    for paragraph, names in seen.items():
        owners = sorted(set(names))
        if len(owners) < 2:
            continue
        excerpt = paragraph[:100] + ('…' if len(paragraph) > 100 else '')
        _warn(f'{", ".join(owners)}: share a rubric paragraph verbatim — "{excerpt}". '
            f'Both judges then grade the same axis, so one bad reply is counted as two failures '
            f'and neither policy is exercised on the axis it is named for. Keep the paragraph in '
            f'the policy that owns the axis and state the precedence in the other.')


def main() -> int:
    parser = argparse.ArgumentParser(description='Validate a plugin-generated policies_config.yaml.')
    parser.add_argument('policies', type=Path, help='path to policies_config.yaml')
    parser.add_argument(
        '--sandbox-names',
        type=Path,
        metavar='JSON',
        help='JSON {"names": [...], "functions": [...]} captured from the backend\'s live '
        'sandbox namespace; overrides both the live read and the vendored sandbox_namespace.json '
        'for this run. Omit it and the namespace is read from PLURAI_URL when one is '
        'configured, falling back to the vendored copy — an unusable file passed HERE is an '
        'error, not a fallback, because you named it.',
    )
    parser.add_argument(
        '--edited',
        action='append',
        metavar='POLICY',
        help='a policy name THIS edit turn changed (repeatable). Scopes the permit-vs-forbid '
        'finding: on a policy you did not edit it prints as a NOTE instead of a counted warning, '
        'because the backend already ran that check at generation. Omit it and every finding warns.',
    )
    parser.add_argument(
        '--flow-ids',
        type=Path,
        metavar='FILE',
        help="this run's flow ids — a JSON list, or one id per line. Beats the live read. Without "
        'either, the dead-guard lint falls back to the `_flow` suffix heuristic and says so: a '
        'customer whose flows are named `refund_request` has that lint dark, and a dead guard '
        'skips its policy set-wide while reading as a policy that exists.',
    )
    parser.add_argument(
        '--scenario-set-id',
        metavar='ID',
        help="the scenario set whose `target_flows` are this run's flow vocabulary. Defaults to "
        '`.scenario_set_id` beside the policies file; read over the same bearer-authenticated '
        'GET as the sandbox namespace.',
    )
    args = parser.parse_args()
    if args.edited:
        global _EDITED_POLICIES  # noqa: PLW0603 - one module-level switch, set once from argv
        _EDITED_POLICIES = set(args.edited)
    if args.sandbox_names is not None:
        _load_sandbox_names(args.sandbox_names)
    else:
        _fetch_live_namespace()
    _resolve_flow_vocabulary(args.flow_ids, args.scenario_set_id, args.policies.parent)
    errors = verify(args.policies)
    if errors:
        for e in errors:
            print(f'  ✗ {e}', file=sys.stderr)
        print(f'\n{len(errors)} validation error(s), {len(_WARNINGS)} warning(s).', file=sys.stderr)
        return 2
    if _WARNINGS:
        # Not a failure — but "valid" with four warnings above it reads as clean.
        print(f'{args.policies} has no errors, but {len(_WARNINGS)} warning(s) above need a decision.')
    else:
        # The PATH, not the conventional basename: /edit-evals verifies a scratch draft and the
        # pre-run gate verifies an env-scoped copy, and naming a file that was never read is how
        # a run against the wrong path reads as a clean one.
        print(f'✓ {args.policies} is valid')
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
