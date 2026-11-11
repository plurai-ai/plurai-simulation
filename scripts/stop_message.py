#!/usr/bin/env python3
"""The three product texts a customer reads when a flow stops on our side, printed verbatim, and what is done so far
as outcomes, nothing else.

    stop_message.py contact [--done "<outcome>" …]
    stop_message.py unavailable [--done "<outcome>" …]
    stop_message.py refusal (--refusal <file | -> | --sentence "<sentence>" | --run-summary <run_summary.json>) [--continue "/simulation-run"] [--plurai <runner>] [--done "<outcome>" …]

The contact hand-off (`CONTACT_TEXT`) is a fault on Plurai's side that Plurai must look at: one line, two sentences, no
ids. The capacity stop (`CAPACITY_TEXT`) is the provider refusing Plurai's account, and the unavailable stop
(`UNAVAILABLE_TEXT`) is the backend not being reachable or saying it is unavailable. All three are plain text.

`refusal` is the one renderer for every typed refusal. It reads the tool's error text from its first `{`, or a payload
carrying the refusal under `error` / `detail` (a job's envelope, any status).

A capacity refusal is a block whose `code` is `capacity_exhausted`, or an envelope whose `capacity` key is set. It is
`CAPACITY_TEXT` whatever else it carries: `resets_at` and `reset_source` are never read.

An unavailable refusal is an `unavailable` block, or `terminal_reason: unavailable`, found where the capacity block is
found. Its `code` is never read, so the backend can add codes without a client release. It is `UNAVAILABLE_TEXT`.

A credit refusal (a block `{code, resets_at?}` with one of the kinds in `CREDIT_SENTENCES`, or a job envelope's `credit`
key) is the kind's fixed sentence, then the command that continues. The sentences are the SDK's, word for word. Only
the kinds a payment fixes name the billing page. Its address is the `billing_url` of `<runner> capabilities` when the
caller holds a runner (`--plurai`). Otherwise it is the row `BILLING_PAGES` holds for the session's backend, which needs no
runner: at the hard stop the gateway's refusal names no link, and the model may hold no runner path. A backend with no row
asks the runner `resolve_plurai.py` picks. With no address from any of them, the sentence says "the Plurai dashboard".
`credit-unavailable` is `CONTACT_TEXT` alone, with no continue sentence whatever `--continue` holds, but its halt is the
liftable `capacity` kind: the credit-state read is transient, so the customer's next message runs it again.

A script that meets a credit stop on its own prints `credit_stop_line`: `CREDIT-STOP:` and the kind's finished sentence,
as one line. `--sentence` takes that line, the bare sentence, or a runner line that ends with one (`plurai job wait`:
`[time] could not read the status of job <id> (credit): <sentence>`, or `[time] failed — job <id> (credit): <sentence>`
for a job that itself ended on credit) in place of a refusal. It reads the kind, the reset time and the billing
link back from the `CREDIT_SENTENCES` table and prints what `--refusal` prints for them; the rest of the argument is
never printed. The billing link is the one the sentence carries (a plain web address, else the backend's row, else the
dashboard); `--plurai` applies to `--refusal` only. An argument that ends in no table sentence exits 3 and asks for the
refusal itself: free text types nothing, not even a 402's `detail` ("Insufficient credit"), while `--refusal` reads the
`code` beside it.

Printing a stop also halts the session's /simulate plan (`record_stop`), with the kind the stop is (`contact`, `capacity`
for the capacity and unavailable stops, `credit`) and these arguments, so the job-start gate and the Stop hook see a stop
the model printed whether or not its final message says it. With no plan it records nothing.

A refusal that is not typed that way exits 3: take the `contact` hand-off instead, never a sentence built from the
provider's own text.

`--continue` is what the user does to go on: the slash command they typed to start the flow (``Run `/simulate` ``), or,
when they asked in words, that request (`Say "<request>" again`). Only the credit kinds a payment fixes print it, so only
they require it. It must be one line with no `"` or backtick. A skill the user cannot type (`user-invocable: false` in
its front matter) is refused, by name and wherever it sits in the value: exit 5, so the caller passes the user's own
command instead.

`--run-summary` is a scored run the runner stopped for credit (`aborted.reason: credit` in its `run_summary.json`; a
kind the table lacks reads as `credit-refused`), for capacity (`aborted.reason: capacity`) or as unavailable
(`aborted.reason: unavailable`). Its first done line is the runner's own count, `succeeded` of `total`, with the run's
folder, so the model never counts. A summary with no such stop exits 3; one whose counts are missing says the stop with
no count.

A `--done` line is an OUTCOME (what exists now, with its id). One that narrates our machinery — a waiver, a retry, a
provider error, a check that could not be measured, what we are not allowed to edit, a skill the user cannot type — is
refused (exit 4, the line named): rewrite it as an outcome, or drop it.
"""

from __future__ import annotations

import argparse
import functools
import http
import json
import re
import subprocess
import sys
import urllib.error
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit

from _stdio import utf8_child_text  # sibling script: UTF-8 children on Windows

#: The contact hand-off: a fault on Plurai's side. One line, two sentences, plain text, no ids. `CONTACT_CHANNELS` is
#: where a customer reaches Plurai; a per-scenario row that is not a stop names the same channels in its own sentence.
CONTACT_EMAIL = 'support@plurai.ai'
CONTACT_DISCORD = 'https://discord.gg/bg34gh76uJ'
CONTACT_CHANNELS = f'{CONTACT_EMAIL} or on Discord: {CONTACT_DISCORD}'
CONTACT_TEXT = f"We hit an issue that needs a closer look from the Plurai team. Reach out at {CONTACT_CHANNELS} and we'll dig into it."
#: The capacity stop: the provider refused Plurai's account. A fixed wait, whatever the refusal carries.
CAPACITY_TEXT = "Plurai is in high demand right now and we couldn't fit your run in. Please try again in about 20 minutes."
#: The unavailable stop: the backend could not be reached, or said it is unavailable.
UNAVAILABLE_TEXT = 'Plurai is unavailable right now. Please try again in a few minutes.'
#: The `code` of a typed capacity refusal.
CAPACITY_CODE = 'capacity_exhausted'
#: The `aborted.reason` of a run stopped for credit, for capacity, and as unavailable.
CREDIT_REASON = 'credit'
CAPACITY_REASON = 'capacity'
UNAVAILABLE_REASON = 'unavailable'
DONE_HEAD = '**Done so far:**'
#: The done line of a scored run stopped for credit: the runner's `succeeded` of `total`, and the run's folder.
RUN_COUNT_TEXT = '{finished} of {total} scenarios finished, in {run}'
#: What exit 3 says for `--run-summary`: the run stopped for another reason, which the run's own exit code covers.
NOT_A_CREDIT_STOP_HINT = ('stop_message: this run summary records no stop on credit, capacity or availability (`aborted.reason` is '
                          'none of `credit`, `capacity`, `unavailable`) — handle the run by its exit code, not with a stop message')

#: What a sentence points the user at when the environment has no billing page of its own.
BILLING_FALLBACK = 'the Plurai dashboard'
#: The billing page of each backend a release talks to, by the host of its API root: the rows of the SDK's environment
#: table (`plurai/login/_environments.py`) that have a page, for a session that holds no runner to ask.
#: `test_credit_sentence_parity.py` fails on a difference, in the cross-repo lane.
BILLING_PAGES: Mapping[str, str] = MappingProxyType({
    'sandbox.plurai.ai': 'https://simulation.plurai.ai/packages',
})
#: The same rows for development backends, `{host: page}`, in a folder the release export leaves out, as the SDK's export
#: leaves out its own: a public install knows production alone.
DEV_BILLING_PAGES = Path(__file__).resolve().parent / 'dev' / 'billing_pages.json'
#: The one sentence per credit kind, as a template: the SDK's table, word for word. Placeholders: `{billing}` the
#: billing page or `BILLING_FALLBACK`; `{until}` ` until <time> UTC` when the free tier's reset time is known, else
#: nothing; `{after}` `after that` or `when it resets` to match. Only the kinds a payment fixes carry the billing page.
CREDIT_SENTENCES: Mapping[str, str] = MappingProxyType({
    'insufficient-credit': "Your organization has no credit left. Add credit at {billing}, or ask your organization's admin to.",
    'new-run-refused': (
        "Your organization's credit only allows a few runs at once, and work already running, which can include your "
        'own earlier steps, is using them. Add credit or raise the pay-as-you-go limit at {billing}, or wait for that '
        'work to finish and run fewer scenarios at a time.'
    ),
    'free-tier-ceiling': 'The free tier is used up{until}. Continue {after}, or upgrade at {billing}.',
    'credit-unavailable': CONTACT_TEXT,
    'credit-refused': 'Credit was refused. Please try again later.',
})
#: The kind for a credit refusal that names none the table holds.
CREDIT_UNKNOWN = 'credit-refused'
#: The credit kind that prints the contact hand-off, with no continue sentence. Its halt is still the liftable one.
CREDIT_CONTACT = 'credit-unavailable'
#: Every script that meets a credit stop prints one line: this marker, a space, the finished sentence.
CREDIT_STOP_MARKER = 'CREDIT-STOP:'
#: The credit sentence is followed by this one, except for the `CREDIT_CONTACT` kind.
CONTINUE_TEXT = '{action} to continue from where it stopped.'
#: The `{action}` for a slash command, and for a request the user made in words.
RUN_ACTION = 'Run `{command}`'
SAY_ACTION = 'Say "{command}" again'
#: The plugin's skills, beside this script's folder in the installed plugin.
SKILLS_DIR = Path(__file__).resolve().parent.parent / 'skills'
#: What exit 5 says on stderr, after the skill's name.
INTERNAL_SKILL_HINT = ('is a step the user cannot run. Pass --continue the command the user typed to start this flow '
                       '(for example `/simulate`), or their request in words.')
_SHOWN_TIME = '%Y-%m-%d %H:%M UTC'
#: A billing page address is printed only when it is a plain web address: `http(s)://` and 1-2000 characters of the RFC 3986
#: unreserved set plus `: / ? # @ ! $ & + , ; = %`. Nothing else (no space, quote, bracket, parenthesis, backtick or
#: non-ASCII) can end the sentence it sits in or open markup in the model's reply.
_WEB_ADDRESS = re.compile(r"https?://[A-Za-z0-9._~:/?#@!$&+,;=%-]{1,2000}")
_CAPABILITIES_TIMEOUT = 30
#: What exit 3 says for `--refusal`: the payload is not one of the typed refusals this module renders.
NOT_TYPED_HINT = 'stop_message: not a typed capacity, unavailable or credit refusal — use the contact hand-off'
#: What exit 3 says for `--sentence`: the text is not a typed refusal, but the payload it came from can be one, and a
#: credit stop sent to the contact hand-off tells a user who only needs credit that the fault is ours.
NOT_A_SENTENCE_HINT = ("stop_message: this text holds no credit sentence (a refusal's `detail` is not one). Pass the refusal's "
                       "JSON with --refusal, alone or inside the tool's error text. With no JSON to pass, a payment refusal "
                       "follows ${CLAUDE_PLUGIN_ROOT}/docs/credit-refusal.md, and anything else takes the contact hand-off.")
#: Machinery a `--done` line may not narrate: our gates and waivers, retries and limits, provider errors, unresolved
#: checks, and what we may not edit. Each has leaked into a stop message.
_MECHANICS = re.compile(
    r'\bwaiv|\bmock gate\b|\bcontract tests?\b|\bretr(?:y|ied|ies)\b|\bHTTP\s*\d{3}\b|\b429\b|\bquota\b|\busage limit\b|'
    r'\bweekly limit\b|\brate limit\b|\bnot allowed\b|\bcan\'?t be measured\b|\bcannot be measured\b|\bunresolved\b|'
    r'\bbackend fixes\b|\bpersonas? (?:were|was) writing\b|\bSDK turn\b|\brelease gate\b|\bre-?verified\b',
    re.IGNORECASE)


class InternalSkillError(ValueError):
    """A `--continue` that names a skill the user cannot type."""

    def __init__(self, skill: str) -> None:
        self.skill = skill
        super().__init__(skill)


@functools.cache
def internal_skills() -> frozenset[str]:
    """The names of the skills whose front matter says `user-invocable: false`: the folder name and any `name:`."""
    names: set[str] = set()
    for path in SKILLS_DIR.glob('*/SKILL.md'):
        head = path.read_text(encoding='utf-8').split('\n---', 1)[0]
        if re.search(r'^user-invocable:\s*false\b', head, re.MULTILINE):
            names.add(path.parent.name)
            if named := re.search(r'^name:\s*([\w-]+)', head, re.MULTILINE):
                names.add(named.group(1))
    return frozenset(names)


def internal_skill_in(text: str) -> str | None:
    """The first skill of `internal_skills` that `text` names as a whole word (`/edit-evals`, `edit-evals`), or None."""
    return next((skill for skill in sorted(internal_skills()) if re.search(rf'(?<![\w-]){re.escape(skill)}(?![\w-])', text)), None)


def continue_action(command: str) -> str:
    """What the user does to continue, for `CONTINUE_TEXT`. Raises `InternalSkillError` when
    `command` names a skill the user cannot type, ValueError when it cannot sit in one sentence."""
    command = command.strip()
    if not command or '\n' in command or '"' in command or '`' in command:
        raise ValueError(f'--continue must be one line with no " or backtick, got {command!r}')
    if skill := internal_skill_in(command):
        raise InternalSkillError(skill)
    return (RUN_ACTION if command.startswith('/') else SAY_ACTION).format(command=command)


class LeakError(ValueError):
    """`--done` lines that narrate our machinery."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        super().__init__('; '.join(lines))


def _done(lines: list[str], measured: list[str] | None = None) -> list[str]:
    """The done lines: `measured` (lines the plugin wrote from the runner's own record) first, then the caller's `lines`,
    which alone are checked for narration: our machinery, or a skill the user cannot type, offered as a way on."""
    leaks = [line for line in lines if _MECHANICS.search(line) or internal_skill_in(line)]
    if leaks:
        raise LeakError(leaks)
    shown = [*(measured or []), *(line.strip() for line in lines)]
    return ['', DONE_HEAD, *(f'- {line}' for line in shown)] if shown else []


def refusal_of(text: str) -> object:
    """The refusal object in a tool's error text: the whole text when it is JSON, else the JSON from its first `{`
    (`Error executing tool <tool>: {…}`). Raises ValueError when there is none."""
    try:
        return json.loads(text)
    except ValueError:
        start = text.find('{')
        if start < 0:
            raise
        return json.JSONDecoder().raw_decode(text[start:])[0]


def _nodes(refusal: object) -> list[dict]:
    """The dicts a typed refusal can sit in: the payload itself and what it holds under `error` / `detail`."""
    nested = [refusal.get(key) for key in ('error', 'detail')] if isinstance(refusal, dict) else []
    return [node for node in (refusal, *nested) if isinstance(node, dict)]


def is_capacity(refusal: object) -> bool:
    """Whether `refusal` is a typed capacity refusal: a block whose `code` is `CAPACITY_CODE`, or an envelope whose
    `capacity` key is set. The block's other fields are never read."""
    return any(node.get('code') == CAPACITY_CODE or bool(node.get('capacity')) for node in _nodes(refusal))


def is_unavailable(refusal: object) -> bool:
    """Whether `refusal` is a typed unavailable refusal: an envelope whose `unavailable` key holds a block, or
    `terminal_reason: unavailable`. The block's `code` is never read, so a code the backend adds needs no client release."""
    return any(isinstance(node.get('unavailable'), dict) or node.get('terminal_reason') == UNAVAILABLE_REASON for node in _nodes(refusal))


_SAFE_TOKEN = re.compile(r'[A-Za-z0-9_:.+-]{1,64}')
_TYPED_SCALARS = ('status', 'terminal_reason', 'code')
_TYPED_BLOCKS = ('capacity', 'unavailable', 'credit')
_TYPED_NESTED = ('error', 'detail')


def _safe_token(value: object) -> str | int | None:
    """`value` when it is a short token of the strict charset (or an HTTP status), else None: nothing free-form passes."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 100 <= value <= 599 else None
    return value if isinstance(value, str) and _SAFE_TOKEN.fullmatch(value) else None


def _typed_fields(node: dict, *, nested: bool = True) -> dict:
    out: dict = {}
    for key in _TYPED_SCALARS:
        if (value := _safe_token(node.get(key))) is not None:
            out[key] = value
    if (stamp := _safe_token(node.get('resets_at') or node.get('resetsAt'))) is not None:
        out['resets_at'] = stamp  # a gateway answer carries the reset time beside its code
    for key in _TYPED_BLOCKS:
        block = node.get(key)
        if not block:
            if isinstance(block, dict) and key == 'unavailable':
                out[key] = {}  # an unavailable block is a block however empty
            continue
        kept: dict = {}
        if isinstance(block, dict):
            if (code := _safe_token(block.get('code'))) is not None:
                kept['code'] = code
            if (stamp := _safe_token(block.get('resets_at') or block.get('resetsAt'))) is not None:
                kept['resets_at'] = stamp
        if key == 'capacity' and 'code' not in kept:
            kept['code'] = CAPACITY_CODE  # a set capacity key is a capacity refusal whatever else it carries
        out[key] = kept
    if nested:
        for key in _TYPED_NESTED:
            if isinstance(node.get(key), dict) and (inner := _typed_fields(node[key], nested=False)):
                out[key] = inner
    return out


def typed_refusal(refusal: object) -> dict | None:
    """The typed fields of `refusal` and nothing else, or None when `refusal` is not a refusal object this module reads the
    same way after the rebuild. Only `status`, `terminal_reason`, `code`, `resets_at` and the `capacity` / `unavailable` /
    `credit` blocks (`code` and `resets_at`), also under `error` / `detail`, survive, each a token of `[A-Za-z0-9_:.+-]` or
    an HTTP status. Backend text, which can carry an instruction, never does, so the result is safe to hand to a model as
    an argument: `json.dumps(..., ensure_ascii=True)` of it holds no quote or backtick beyond JSON's own."""
    if not isinstance(refusal, dict):
        return None
    typed = _typed_fields(refusal)
    if (is_capacity(typed), is_unavailable(typed), credit_block(typed)) != (is_capacity(refusal), is_unavailable(refusal), credit_block(refusal)):
        return None
    return typed


def text_message(text: str, done: list[str], *, measured: list[str] | None = None) -> str:
    """A product text, verbatim, then the done lines (`measured` first, see `_done`)."""
    return '\n'.join([text, *_done(done, measured)])


def credit_block(refusal: object) -> tuple[str, str | None] | None:
    """`(kind, resets_at)` of a credit refusal in `refusal`, or None when it holds none.

    A block with one of the `CREDIT_SENTENCES` kinds counts at the top level or under `error` / `detail` / `capacity`.
    A job envelope's `credit` key (or `terminal_reason: credit` alone) is a credit refusal whatever its kind: one the
    table does not hold reads as `CREDIT_UNKNOWN`, as the SDK reads it.
    """
    nested = [refusal.get(k) for k in ('error', 'detail', 'capacity')] if isinstance(refusal, dict) else []
    nodes = [node for node in (refusal, *nested) if isinstance(node, dict)]

    def reset_of(node: dict) -> str | None:
        # The wire name is `resets_at`; the gateway's own answer spells it `resetsAt`.
        stamp = node.get('resets_at') or node.get('resetsAt')
        return stamp.strip() if isinstance(stamp, str) and stamp.strip() else None

    def kind_of(node: dict) -> str | None:
        code = node.get('code')
        return code if isinstance(code, str) and code in CREDIT_SENTENCES else None

    for node in nodes:
        if (kind := kind_of(node)) is not None:
            return kind, reset_of(node)
    for node in nodes:
        if isinstance(block := node.get('credit'), dict):
            return kind_of(block) or CREDIT_UNKNOWN, reset_of(block)
    if any(node.get('terminal_reason') == 'credit' for node in nodes):
        return CREDIT_UNKNOWN, None
    return None


def _utc_shown(stamp: str) -> str | None:
    """`stamp` (ISO 8601 with a UTC offset: `Z`, `.000Z`, `+00:00`, `+02:00`) in UTC as `_SHOWN_TIME`, or None when it
    has no offset or is not a time."""
    try:
        parsed = datetime.fromisoformat(stamp)
        return parsed.astimezone(timezone.utc).strftime(_SHOWN_TIME) if parsed.tzinfo else None
    except (ValueError, OverflowError):  # no time, or an offset that carries it past year 1 / 9999
        return None


def credit_sentence(code: str, *, resets_at: str | None = None, billing_url: str = '') -> str:
    """The sentence for a credit refusal of kind `code`. `resets_at` matters to the free tier alone; a time that cannot be
    read as an offset-bearing ISO 8601 stamp is left out, and the sentence says "when it resets"."""
    template = CREDIT_SENTENCES.get(code) or CREDIT_SENTENCES[CREDIT_UNKNOWN]
    reset = _utc_shown(resets_at.strip()) if resets_at else None
    return template.format(
        billing=billing_url.strip() or BILLING_FALLBACK,
        until=f' until {reset}' if reset else '',
        after='after that' if reset else 'when it resets',
    )


def names_billing_page(code: str) -> bool:
    """Whether the sentence for kind `code` points at the billing page."""
    return '{billing}' in CREDIT_SENTENCES.get(code, CREDIT_SENTENCES[CREDIT_UNKNOWN])


def stop_line(sentence: str) -> str:
    """`CREDIT_STOP_MARKER` and a finished sentence: the one line a script prints for a credit stop, for the model to say."""
    return f'{CREDIT_STOP_MARKER} {sentence}'


def credit_stop_line(code: str, *, resets_at: str | None = None, billing_url: str = '') -> str:
    """`stop_line` of the kind's own sentence."""
    return stop_line(credit_sentence(code, resets_at=resets_at, billing_url=billing_url))


#: The exit code of a script that stops on a credit refusal and prints its `CREDIT-STOP:` line.
EXIT_CREDIT_REFUSED = 8


def credit_stop_line_of(error: urllib.error.HTTPError, limit: int) -> str | None:
    """The `CREDIT-STOP:` line when `error` is the gateway's HTTP 402 and its body names a credit kind, else None.

    The gateway's own words never leave this function: only the kind and the reset time are read, and the sentence is
    the kind's. A body that cannot be read leaves the failure as the transport's. ``limit`` is how many bytes of it are read.
    """
    if error.code != http.HTTPStatus.PAYMENT_REQUIRED:
        return None
    try:
        block = credit_block(json.loads(error.read(limit).decode('utf-8', 'replace')))
    except Exception:  # noqa: BLE001 - a body that cannot be read is not a credit refusal
        return None
    if block is None:
        return None
    kind, resets_at = block
    link = resolved_billing_url() if names_billing_page(kind) else ''
    return credit_stop_line(kind, resets_at=resets_at, billing_url=link)


def billing_pages() -> dict[str, str]:
    """`BILLING_PAGES` and, in a development install, the rows of `DEV_BILLING_PAGES`."""
    try:
        development = json.loads(DEV_BILLING_PAGES.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        development = {}
    return {**(development if isinstance(development, dict) else {}), **BILLING_PAGES}


def _backend_url() -> str:
    """The API root of the session's backend: the resolved pair's, else the one the MCP server dials, which holds when the
    login cannot complete the pair. Only its host is used, and only to pick a row, so a url a command sets for itself
    can select a listed page and can never name a new one."""
    hooks = str(Path(__file__).resolve().parent.parent / 'hooks')
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    try:
        import _login_store  # noqa: PLC0415 - found through the line above; only a credit stop pays for it

        return _login_store.resolve_backend().url or _login_store.effective_mcp_url()
    except Exception:  # noqa: BLE001 - a store that crashed leaves the sentence on the dashboard fallback
        return ''


def backend_billing_url() -> str:
    """The billing page of the session's backend from `billing_pages`, by host as the SDK selects a row, or ''."""
    try:
        host = (urlsplit(_backend_url()).hostname or '').lower()
    except ValueError:
        return ''
    return _web_address(billing_pages().get(host))


def _picked_runner() -> str:
    """The runner `resolve_plurai.py` picks, or ''. Quiet: the resolver's notes are dropped."""
    import contextlib  # noqa: PLC0415 - only a credit stop pays for the resolver
    import io  # noqa: PLC0415

    try:
        import resolve_plurai  # noqa: PLC0415

        with contextlib.redirect_stderr(io.StringIO()):
            runner, _ = resolve_plurai.resolve()
    except Exception:  # noqa: BLE001 - a resolver that crashed leaves the sentence on the dashboard fallback
        return ''
    return runner or ''


def resolved_billing_url(plurai: str = '') -> str:
    """The billing page of the session's backend, or ''. A runner the caller holds answers first: its table is the source of
    the plugin's. Otherwise the backend's row, and a runner is looked for only on a backend with no row."""
    if plurai:
        return billing_url_of(plurai) or backend_billing_url()
    if page := backend_billing_url():
        return page
    runner = _picked_runner()
    return billing_url_of(runner) if runner else ''


def _web_address(url: object) -> str:
    """`url` stripped when it is a plain web address (`_WEB_ADDRESS`), else ''. Every address from a table or a runner
    passes here: `credit_sentence` prints what it is given."""
    return url.strip() if isinstance(url, str) and _WEB_ADDRESS.fullmatch(url.strip()) else ''


def billing_url_of(plurai: str) -> str:
    """The `billing_url` of `<plurai> capabilities`, or '' when it is unset, unusable, or the runner does not answer."""
    try:
        proc = subprocess.run([plurai, 'capabilities'], capture_output=True, text=True, **utf8_child_text(), timeout=_CAPABILITIES_TIMEOUT, check=False)  # noqa: S603
        payload = json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return ''
    return _web_address(payload.get('billing_url') if isinstance(payload, dict) else None)


def _sentence_pattern(template: str) -> re.Pattern[str]:
    """The pattern for a finished sentence of `template`, ending its line: each placeholder becomes a group (`billing`,
    and `until` for the free tier's reset time) and every other character is literal."""
    groups = {
        'billing': r'(?P<billing>[^\n]{1,2100}?)',
        'until': r'(?: until (?P<until>\d{4}-\d\d-\d\d \d\d:\d\d) UTC)?',
        'after': '(?:after that|when it resets)',
    }
    parts = re.split(r'\{(\w+)\}', template)
    body = ''.join(groups[part] if i % 2 else re.escape(part) for i, part in enumerate(parts))
    return re.compile(rf'(?<!\S){body}$')


_SENTENCE_PATTERNS = MappingProxyType({kind: _sentence_pattern(template) for kind, template in CREDIT_SENTENCES.items()})


class NotACreditSentence(ValueError):
    """`--sentence` text that ends in none of the `CREDIT_SENTENCES`."""


def credit_sentence_in(text: str) -> tuple[tuple[str, str | None], str] | None:
    """`((kind, resets_at), billing_url)` read back from the table sentence that ends `text`, or None when none does.
    `text` may carry a prefix (`CREDIT-STOP:`, a runner's timestamp and job id). The billing url is '' unless it is a
    plain web address (`_web_address`)."""
    text = text.strip()
    for kind, pattern in _SENTENCE_PATTERNS.items():
        if match := pattern.search(text):
            found = match.groupdict()
            until = found.get('until')
            return (kind, f'{until.replace(" ", "T")}:00+00:00' if until else None), _web_address(found.get('billing'))
    return None


def sentence_message(sentence: str, command: str | None, done: list[str]) -> str:
    """The message for a credit sentence that ends `sentence` (see `credit_sentence_in`): the table's rendering of what
    it names, never `sentence` itself. Raises `NotACreditSentence` when it ends in none, ValueError when it is not one
    line."""
    if not sentence.strip() or '\n' in sentence.strip():
        raise ValueError('--sentence must be one line')
    found = credit_sentence_in(sentence)
    if found is None:
        raise NotACreditSentence(sentence)
    block, link = found
    # Only the plugin's table is read: whatever wrote this sentence with no link already looked for a page, a runner included.
    if not link and names_billing_page(block[0]):
        link = backend_billing_url()
    return credit_message(block, command, done, billing_url=link)


def credit_message(block: tuple[str, str | None], command: str | None, done: list[str], *, billing_url: str = '',
                   measured: list[str] | None = None) -> str:
    """The credit sentence, the continue command and the done lines (`measured` first, see `_done`). `billing_url` is
    printed as given: the caller validates it (`_web_address`). `credit-unavailable` is the contact text alone, so it takes
    no command; every other kind needs one (ValueError without)."""
    code, resets_at = block
    sentence = credit_sentence(code, resets_at=resets_at, billing_url=billing_url)
    if code == CREDIT_CONTACT:
        return '\n'.join([sentence, *_done(done, measured)])
    if command is None:
        raise ValueError(f'--continue is required for a credit stop of kind {code}: the command the user typed to start the flow, or their request in words')
    return '\n'.join([f'{sentence} {CONTINUE_TEXT.format(action=continue_action(command))}', *_done(done, measured)])


def _non_negative_int(value: object) -> int | None:
    """`value` when it is an int of 0 or more (a JSON `true` is not one), else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _run_summary(path: str) -> tuple[dict, dict | None]:
    """The summary, and its `aborted` block when it has one."""
    doc = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(doc, dict):
        return {}, None
    aborted = doc.get('aborted')
    return doc, aborted if isinstance(aborted, dict) else None


def _run_count(doc: dict, path: str) -> list[str]:
    """`RUN_COUNT_TEXT`, left out unless `succeeded` and `total` are counts the runner could have written
    (`0 <= succeeded <= total`, `total > 0`)."""
    finished, total = _non_negative_int(doc.get('succeeded')), _non_negative_int(doc.get('total'))
    if finished is None or total is None or total == 0 or finished > total:
        return []
    return [RUN_COUNT_TEXT.format(finished=finished, total=total, run=Path(path).resolve().parent)]


def run_summary_stop(path: str) -> tuple[str, tuple[str, str | None] | None, list[str]] | None:
    """`(reason, credit block, measured lines)` of a run the runner stopped for credit, for capacity or as unavailable, from its
    `run_summary.json`, or None when the summary records none of them. `reason` is `aborted.reason`; the credit block is
    the kind and its reset time for `credit` alone. The one measured line is the run's count (`_run_count`)."""
    doc, aborted = _run_summary(path)
    reason = aborted.get('reason') if aborted is not None else None
    if reason == CREDIT_REASON and aborted is not None:
        return reason, credit_block(aborted) or (CREDIT_UNKNOWN, None), _run_count(doc, path)
    if reason in (CAPACITY_REASON, UNAVAILABLE_REASON):
        return reason, None, _run_count(doc, path)
    return None


def _billing_url_for(block: tuple[str, str | None], plurai: str | None) -> str:
    """The billing page for a refusal's sentence, or '' for a kind whose sentence names none, so no runner is asked."""
    return resolved_billing_url(plurai or '') if names_billing_page(block[0]) else ''


def contact_message(done: list[str]) -> str:
    return text_message(CONTACT_TEXT, done)


#: The halt kinds a printed stop records in a /simulate plan (`hooks/_run_driver.py` STOP_*): the capacity kind also holds
#: the unavailable stop and `credit-unavailable`, whose text is the contact hand-off but whose halt the next typed message
#: lifts.
HALT_CONTACT, HALT_CAPACITY, HALT_CREDIT = 'contact', 'capacity', 'credit'


def _credit_halt(kind: str) -> str:
    return HALT_CAPACITY if kind == CREDIT_CONTACT else HALT_CREDIT


def record_stop(kind: str, args: list[str]) -> None:
    """Halt the session's /simulate plan on the stop this script just printed (`simulate_continue.record_printed_stop`), so
    the job-start gate and the Stop hook see a stop whether or not the model's final message says it. Best-effort: a
    session with no plan, or a store that cannot be read, leaves the printed text as the only effect."""
    hooks = str(Path(__file__).resolve().parent.parent / 'hooks')
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    try:
        import _workdir  # noqa: PLC0415 - found through the line above; only a stop pays for it
        import simulate_continue  # noqa: PLC0415

        simulate_continue.record_printed_stop(_workdir.marker_dirs(), kind, args)
    except Exception:  # noqa: BLE001 - the stop's text is already printed
        pass


def _refusal_text(value: str) -> str:
    """The payload `--refusal` names. The posture passes the JSON (or the tool's error text around it) as the value
    itself; a value holding a `{` is that text, since no payload file the flow writes has one in its path."""
    if value == '-':
        return sys.stdin.read()
    if '{' in value:
        return value
    return Path(value).read_text(encoding='utf-8')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    contact = sub.add_parser('contact', help='the contact hand-off')
    unavailable = sub.add_parser('unavailable', help='the unavailable stop')
    refusal = sub.add_parser('refusal', help='the capacity, unavailable or credit stop, from the typed refusal')
    source = refusal.add_mutually_exclusive_group(required=True)
    source.add_argument('--refusal', help='the refusal payload: the JSON or error text itself, a JSON file, or - for stdin')
    source.add_argument('--sentence', help='a `CREDIT-STOP:` line, a credit sentence, or a runner line that ends with one; the table\'s own rendering is printed')
    source.add_argument('--run-summary', help="a stopped run's run_summary.json; its count of finished scenarios leads the done lines")
    refusal.add_argument('--continue', dest='command',
                         help='the slash command the user typed to start the flow, or their request in words; required by the credit kinds a payment fixes')
    refusal.add_argument('--plurai', help='the plurai runner, when you hold it; its `capabilities` names the billing page')
    for p in (contact, unavailable, refusal):
        p.add_argument('--done', action='append', default=[], help='one outcome done so far; repeatable')
    args = parser.parse_args(argv)
    spoken = list(sys.argv[1:] if argv is None else argv)

    def stopped(kind: str) -> int:
        record_stop(kind, spoken)
        return 0

    try:
        if args.cmd == 'contact':
            print(contact_message(args.done))
            return stopped(HALT_CONTACT)
        if args.cmd == 'unavailable':
            print(text_message(UNAVAILABLE_TEXT, args.done))
            return stopped(HALT_CAPACITY)
        if args.sentence is not None:
            try:
                print(sentence_message(args.sentence, args.command, args.done))
            except NotACreditSentence:
                print(NOT_A_SENTENCE_HINT, file=sys.stderr)
                return 3
            kind = (credit_sentence_in(args.sentence) or ((CREDIT_UNKNOWN, None), ''))[0][0]
            return stopped(_credit_halt(kind))
        if args.run_summary is not None:
            stop = run_summary_stop(args.run_summary)
            if stop is None:
                print(NOT_A_CREDIT_STOP_HINT, file=sys.stderr)
                return 3
            reason, block, measured = stop
            if block is None:
                print(text_message(CAPACITY_TEXT if reason == CAPACITY_REASON else UNAVAILABLE_TEXT, args.done, measured=measured))
                return stopped(HALT_CAPACITY)
            print(credit_message(block, args.command, args.done, billing_url=_billing_url_for(block, args.plurai), measured=measured))
            return stopped(_credit_halt(block[0]))
        raw = _refusal_text(args.refusal)
        try:
            typed = refusal_of(raw)
        except ValueError:  # no JSON at all: prose is never a typed refusal
            typed = None
        # A credit block wins over the others: one nested under `capacity` is still a credit refusal.
        block = credit_block(typed)
        if block is not None:
            print(credit_message(block, args.command, args.done, billing_url=_billing_url_for(block, args.plurai)))
            return stopped(_credit_halt(block[0]))
        if is_capacity(typed):
            print(text_message(CAPACITY_TEXT, args.done))
        elif is_unavailable(typed):
            print(text_message(UNAVAILABLE_TEXT, args.done))
        else:
            print(NOT_TYPED_HINT, file=sys.stderr)
            return 3
        return stopped(HALT_CAPACITY)
    except InternalSkillError as exc:
        print(f'stop_message: `{exc.skill}` {INTERNAL_SKILL_HINT}', file=sys.stderr)
        return 5
    except LeakError as exc:
        print('stop_message: these --done lines narrate our machinery; rewrite each as an outcome or drop it:', file=sys.stderr)
        for line in exc.lines:
            print(f'  {line}', file=sys.stderr)
        return 4
    except (OSError, ValueError) as exc:
        print(f'stop_message: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
