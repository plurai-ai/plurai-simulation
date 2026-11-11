"""The client-version handshake, plugin side: the header every transport sends and the message a refusal produces.

Every request the plugin's own code sends to the backend carries ``X-Plurai-Client: plurai-simulation/<version>``.
The Claude Code manifest sends it as a static header (a literal, checked against ``plugin.json`` by
``scripts/dev/check_version_bump.py``); every other transport in this repo builds its opener with
``_no_redirect.build``, which installs :class:`Stamp`. No skill text sets it.

The backend refuses an unidentified client, or one below its floor, with HTTP 426 and a typed
``client_upgrade_required`` body. :func:`refusal_text` is the one function that turns a refusal into what a customer
reads, for every harness and every transport (the backend check, the MCP call helper, the Codex and Copilot bridge, the
SDK call sites): what was refused, the version needed, and the running harness's one update command. Only values that
parse as versions are printed, the backend's own prose is never echoed, and no text offers a ``pip`` line: the plugin
runs only the SDK it installs, so the fix for a refused plugin or SDK is a plugin update.

Stdlib only; the python3 floor is 3.9.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sdk_exit_codes  # noqa: E402 - the SDK's exit table, the one home of its upgrade-required code

#: The name the backend keeps the plugin's floor under.
CLIENT_NAME = "plurai-simulation"
#: The name it keeps the SDK's floor under; also the PyPI distribution.
SDK_NAME = "plurai-simulation-sdk"
HEADER = "X-Plurai-Client"
UPGRADE_STATUS = 426
REFUSAL_ERROR = "client_upgrade_required"
REASONS = frozenset({"missing_header", "malformed_header", "unknown_client", "below_floor"})
#: The typed reasons that are not about the version the caller runs: the fault is in the client build or in Plurai's own
#: configuration, so an update does not fix it.
NON_VERSION_REASONS = frozenset({"missing_header", "malformed_header", "unknown_client"})
#: The exit code of a script that stops on a version refusal and prints its text.
EXIT_VERSION_REFUSED = 7
#: How much of an HTTP error answer's body is read, for the 426 (a few hundred bytes of typed refusal) and the 402 alike.
MAX_ERROR_BODY = 64 * 1024

#: Where the plugin's version is read from at run time: the first manifest of the harness that installed it.
MANIFESTS = (".claude-plugin/plugin.json", ".codex-plugin/plugin.json", ".github/plugin/plugin.json")

#: The plugin root: the directory above ``scripts/``.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent

#: The version the backend's grammar accepts: ``X.Y.Z`` plus an optional pre- or post-release suffix. A header outside
#: it is refused as malformed, so the version sent is the manifest's only when it parses.
VERSION = re.compile(r"(\d{1,9})\.(\d{1,9})\.(\d{1,9})(?:[-.]?(dev|a|b|rc)\.?(\d{1,9})|\.post(\d{1,9}))?", re.ASCII)
_PHASE = {"dev": 0, "a": 1, "b": 2, "rc": 3}
#: The rank of a release in :func:`version_key`: above every pre-release phase.
_RELEASE_RANK = 4

#: The sentinel sent when no manifest names a readable version. It sorts below every floor, so the backend answers with
#: the update message instead of an unexplained malformed-header refusal.
UNKNOWN_VERSION = "0.0.0"

#: The product token of ``User-Agent``. The hosted endpoint rejects urllib's default agent, so every caller names the plugin.
USER_AGENT_PRODUCT = "plurai-simulation-plugin"
#: Sent in place of a version when no manifest names a valid one. ``User-Agent`` is not a floor check, so it does not borrow
#: the ``0.0.0`` sentinel.
UNKNOWN_USER_AGENT_VERSION = "unknown"


def version_key(text: object) -> Optional[Tuple[int, int, int, int, int]]:
    """Order key of a version, or ``None`` when it is not one the handshake understands."""
    match = VERSION.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        return None
    major, minor, patch, phase, phase_n, post_n = match.groups()
    if phase is not None:
        rank, n = _PHASE[phase], int(phase_n)
    elif post_n is not None:
        rank, n = _RELEASE_RANK + 1, int(post_n)
    else:
        rank, n = _RELEASE_RANK, 0
    return int(major), int(minor), int(patch), rank, n


def release_key(version: object) -> Optional[Tuple[int, int, int, int, int]]:
    """:func:`version_key` of ``version`` when it parses and is a release (no ``dev``, ``a``, ``b`` or ``rc`` suffix; a
    ``post`` release is one), else ``None``."""
    key = version_key(version)
    return key if key is not None and key[3] >= _RELEASE_RANK else None


def is_dev(version: object) -> bool:
    """Whether ``version`` parses and carries the ``dev`` suffix."""
    key = version_key(version)
    return key is not None and key[3] == _PHASE["dev"]


def manifest_version(root: Optional[Path] = None) -> Optional[str]:
    """The first manifest version that parses under :data:`VERSION`, or ``None`` when no manifest names one.

    Both identifying headers read the version through this function, so a malformed manifest string reaches neither.
    """
    base = PLUGIN_ROOT if root is None else root
    for name in MANIFESTS:
        try:
            version = json.loads((base / name).read_text(encoding="utf-8")).get("version")
        except (OSError, ValueError, AttributeError):
            continue
        if version_key(version) is not None:
            return str(version)
    return None


def plugin_version(root: Optional[Path] = None) -> str:
    """The version the plugin's manifest declares, or :data:`UNKNOWN_VERSION` when none is readable."""
    return manifest_version(root) or UNKNOWN_VERSION


def header_value(root: Optional[Path] = None) -> str:
    return f"{CLIENT_NAME}/{plugin_version(root)}"


def user_agent(root: Optional[Path] = None) -> str:
    """``plurai-simulation-plugin/<manifest version>`` for ``User-Agent``, or ``.../unknown`` when no version is readable."""
    return f"{USER_AGENT_PRODUCT}/{manifest_version(root) or UNKNOWN_USER_AGENT_VERSION}"


class Stamp(urllib.request.BaseHandler):
    """Adds ``X-Plurai-Client`` to every request the opener sends (not to a redirect's target, which is never followed)."""

    def http_request(self, request: urllib.request.Request) -> urllib.request.Request:
        request.add_unredirected_header(HEADER, header_value())
        return request

    https_request = http_request


@dataclass(frozen=True)
class Refusal:
    """A 426 from the backend, reduced to the versions that parse."""

    reason: Optional[str] = None
    client: Optional[str] = None
    #: The lowest parseable version the answer named, per client name.
    minimum_versions: dict[str, str] = field(default_factory=dict)

    def floor_of(self, client: str) -> Optional[str]:
        """The lowest version of ``client`` the backend serves, if the answer said."""
        return self.minimum_versions.get(client)


def _version_or_none(value: object) -> Optional[str]:
    return value if isinstance(value, str) and version_key(value) is not None else None


def _floors(raw: object) -> dict[str, str]:
    """The floors of a ``minimum_versions`` object that parse as versions, for the plugin and the SDK."""
    if not isinstance(raw, dict):
        return {}
    return {name: floor for name in (CLIENT_NAME, SDK_NAME) if (floor := _version_or_none(raw.get(name))) is not None}


def refusal_from(status: int, body: Any) -> Optional[Refusal]:  # noqa: ANN401 - whatever JSON the backend answered
    """The refusal a status and parsed body make, or ``None`` when the answer is not a 426.

    A 426 whose body is not the typed refusal still refuses, with no versions.
    """
    if status != UPGRADE_STATUS:
        return None
    if not isinstance(body, dict) or body.get("error") != REFUSAL_ERROR:
        return Refusal()
    reason = body.get("reason")
    client = body.get("client")
    client = client if client in (CLIENT_NAME, SDK_NAME) else None
    floors = _floors(body.get("minimum_versions"))
    # A top-level ``minimum_version`` is the floor of the refused client and wins over the per-client object.
    if client is not None and (floor := _version_or_none(body.get("minimum_version"))) is not None:
        floors[client] = floor
    return Refusal(reason=reason if isinstance(reason, str) and reason in REASONS else None, client=client, minimum_versions=floors)


def pinned_sdk_version() -> Optional[str]:
    """The SDK release this plugin installs and resolves, or ``None`` when the resolver does not say."""
    try:
        import resolve_plurai  # noqa: PLC0415 - heavy and optional for the callers of this module
    except Exception:  # noqa: BLE001 - a message without the SDK line beats no message
        return None
    version = getattr(resolve_plurai, "PINNED_SDK_VERSION", None)
    return version if version_key(version) is not None else None


def _at_least(version: Optional[str], floor: Optional[str]) -> bool:
    have, need = version_key(version), version_key(floor)
    return have is not None and need is not None and have >= need


def sdk_refusal_from_contracts(body: Any, sdk_version: Optional[str]) -> Optional[Refusal]:  # noqa: ANN401 - whatever JSON the backend answered
    """The refusal a 200 answer of the contracts route makes for ``sdk_version``, or ``None`` when it is at or above the SDK floor.

    The answer carries both floors in its handshake block. Nothing is refused when the answer does not say, or when
    ``sdk_version`` is not a version: a check that cannot compare says nothing.
    """
    try:
        floors = body["blocks"]["client_version_handshake"]["value"]["minimum_versions"]
    except (KeyError, TypeError):
        return None
    parsed = _floors(floors)
    sdk_floor = parsed.get(SDK_NAME)
    if sdk_floor is None or version_key(sdk_version) is None or _at_least(sdk_version, sdk_floor):
        return None
    return Refusal(reason="below_floor", client=SDK_NAME, minimum_versions=parsed)


#: The version the SDK names as the floor, in its ``pip`` line (``plurai-simulation-sdk>=<floor>``) or its first line
#: (``it needs <floor> or later``).
_SDK_FLOOR = re.compile(re.escape(SDK_NAME) + r">=([0-9][0-9A-Za-z.+-]*)|it needs ([0-9][0-9A-Za-z.+-]*) or later")
#: The reason the SDK names when the backend could not read its identity: ``sends (<reason, spaces for underscores>)``.
_SDK_REASON = re.compile(r"sends \((missing header|malformed header|unknown client)\)")
#: The sentence a customer reads when the SDK's refusal could not be turned into the full text, without its closing mark: a
#: period when nothing follows, a colon before the update command of the running harness.
_FALLBACK_SENTENCE = "Plurai refused the SDK this plugin installs, and the update text could not be produced. Update the plugin in a terminal, then start a new session"


def sdk_refusal_from_output(output: str) -> Refusal:
    """The refusal an SDK command's captured output makes when it exited ``sdk_exit_codes.UPGRADE_REQUIRED``.

    The SDK's own words are never kept: only the reason it names (``below_floor`` when it names none) and a floor that
    parses as a version are read. The SDK sends its own identity, so the refused party is the SDK.
    """
    reason = _SDK_REASON.search(output)
    found = _SDK_FLOOR.search(output)
    floor = _version_or_none(found.group(1) or found.group(2)) if found else None
    return Refusal(
        reason=reason.group(1).replace(" ", "_") if reason else "below_floor",
        client=SDK_NAME,
        minimum_versions={SDK_NAME: floor} if floor else {},
    )


def _hooks_module(name: str) -> Any:  # a hooks module, imported where it lives
    """``hooks/<name>``, imported on first use so the callers of this module that never need it do not pay for it; raises
    when this copy of the plugin has no such module."""
    if str(PLUGIN_ROOT / "hooks") not in sys.path:
        sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))
    return importlib.import_module(name)


def _run_driver_module() -> Any:  # noqa: ANN401 - the hooks module, imported where it lives
    """``hooks/_run_driver``, the one list of the stoppers that file a refusal; raises when this copy of the plugin has none."""
    return _hooks_module("_run_driver")


def record_refusal(source: str, text: str) -> None:
    """File ``text`` as the run record's version refusal when the run driver is headless, so the headless driver ends its
    phase on it; never raises. ``source`` is one of ``_run_driver.REFUSAL_SOURCES``, named by its ``SOURCE_*`` constant. Says
    on stderr when the row landed nowhere, because the driver learns of the refusal from that row alone.

    The one entry every stopper uses, so none of them imports the run driver unguarded: a plugin copy without it still says its text.
    """
    try:
        why = _run_driver_module().record_version_refusal(source, text)
    except Exception as exc:  # noqa: BLE001 - the text is already said; a record that cannot be made is said too
        why = f"{type(exc).__name__}: {exc}"
    if why is not None:
        print(f"{source}: the refusal could not be recorded for the run driver ({why}).", file=sys.stderr)


def record_halt(source: str, text: str) -> None:
    """File ``text`` as the run record's contact halt when the run driver is headless (the headless driver ends its phase
    on exit 5, not on a version refusal's 7); never raises. ``source`` names the stopper in the message that says when the
    row landed nowhere."""
    try:
        why = _run_driver_module().record_halt(_run_driver_module().STOP_CONTACT, text)
    except Exception as exc:  # noqa: BLE001 - the text is already said; a record that cannot be made is said too
        why = f"{type(exc).__name__}: {exc}"
    if why is not None:
        print(f"{source}: the stop could not be recorded for the run driver ({why}).", file=sys.stderr)


def record_for(refusal: Refusal, source: str, text: str) -> None:
    """File ``text`` for the headless driver as ``refusal`` earns: a version refusal, or the contact halt for a refusal that
    is not about the version (a fault on Plurai's side, not a version to update)."""
    if refusal.reason in NON_VERSION_REASONS:
        record_halt(source, text)
    else:
        record_refusal(source, text)


def say_refusal(
    refusal: Refusal,
    source: str,
    *,
    harness: str | None = None,
    env: Mapping[str, str] | None = None,
    sdk_version: str | None = None,
    newer_release: str | None = None,
) -> str:
    """The text a customer reads for ``refusal``, filed as the run record's version refusal under ``source`` (a
    ``_run_driver.SOURCE_*`` constant) on the way: the one recipe every transport follows, so none can say a refusal without
    filing it. The caller prints the text, verbatim and alone, on its own stream.

    The SDK's release is read from the resolver for a refusal of the SDK that comes with none.
    """
    if sdk_version is None and refusal.client == SDK_NAME:
        sdk_version = pinned_sdk_version()
    text = refusal_text(refusal, sdk_version=sdk_version, newer_release=newer_release, harness=harness, env=env)
    record_for(refusal, source, text)
    return text


def sdk_refusal_message(output: str) -> str:
    """What a customer reads when an SDK command exited ``sdk_exit_codes.UPGRADE_REQUIRED`` printing ``output``; never raises.

    Every SDK call site says this text, so it is also where the refusal is filed for a headless run driver.
    """
    refusal = Refusal()
    try:
        refusal = sdk_refusal_from_output(output)
        text = refusal_text(refusal, sdk_version=pinned_sdk_version())
    except Exception:  # noqa: BLE001 - a refusal is always said
        text = sdk_refusal_fallback()
    try:
        source = _run_driver_module().SOURCE_SDK_CALL
    except Exception as exc:  # noqa: BLE001 - the text is said below; a record that cannot be made is said too
        print(f"sdk-call: the refusal could not be recorded for the run driver ({type(exc).__name__}: {exc}).", file=sys.stderr)
    else:
        record_for(refusal, source, text)
    return text


class VersionRefused(Exception):
    """The backend refused a call; ``str(error)`` is the text the customer reads."""


def raise_if_refused(done: Any) -> None:  # noqa: ANN401 - a finished `subprocess.CompletedProcess` of text output
    """Raise :class:`VersionRefused` with the plugin's text when the finished SDK command ``done`` exited upgrade-required.

    The one place a Python caller that runs the SDK itself turns that exit into the text a customer reads: the SDK's own
    words (and its ``pip`` line) are never kept.
    """
    if done.returncode == sdk_exit_codes.UPGRADE_REQUIRED:
        raise VersionRefused(sdk_refusal_message(done.stdout + done.stderr))


def refusal_main(run: Callable[[], int]) -> int:
    """``run()``'s exit code, or the plugin's refusal code after printing the text of the :class:`VersionRefused` it raised."""
    try:
        return run()
    except VersionRefused as refusal:
        print(refusal)
        return EXIT_VERSION_REFUSED


# --------------------------------------------------------------------------- the running harness and its update command

HARNESSES = ("claude", "copilot", "codex")
CLAUDE, COPILOT, CODEX = HARNESSES


def running_harness(env: Mapping[str, str] | None = None) -> str:
    """The harness whose session variable the environment holds, ``claude`` when none: the hooks' one answer, so a refusal
    and the readiness check never name different harnesses."""
    try:
        return str(_hooks_module("_workdir").harness(env))
    except Exception:  # noqa: BLE001 - a refusal is always said; a copy of the plugin without its hooks is Claude Code's default
        return CLAUDE


def update_command(harness: str, marketplaces: Sequence[str] = ()) -> str:
    """The one line that makes the update land, for ``harness``; ``marketplaces`` is the install record's, this install's first.

    Claude Code refreshes the marketplace clone first, joined on one line: the update alone reports a stale clone as
    current. Without a recorded name the refresh covers every marketplace (``marketplace update`` with no name does).
    With more than one marketplace providing the plugin the command names the one this install came from.
    """
    market = marketplaces[0] if marketplaces else ""
    several = len(marketplaces) > 1
    if harness == COPILOT:
        return "copilot plugin update " + (f"{CLIENT_NAME}@{market}" if several else CLIENT_NAME)
    if harness == CODEX:
        return "codex plugin marketplace upgrade" + (f" {market}" if several else "")
    refresh = "claude plugin marketplace update" + (f" {market}" if market else "")
    return f"{refresh} && claude plugin update " + (f"{CLIENT_NAME}@{market}" if several else CLIENT_NAME)


def sdk_refusal_fallback() -> str:
    """What a customer reads when the SDK's refusal could not be rendered in full: the sentence and the update command of the
    running harness. A shell with no python3 to ask prints the sentence alone (``PLURAI_SDK_REFUSAL_FALLBACK`` in
    ``sdk-exit-codes.sh``), because only a running interpreter can tell which harness started the session."""
    return f"{_FALLBACK_SENTENCE}:\n{update_command(running_harness())}"


# --------------------------------------------------------------------------- the one function that says it

#: What the customer reads to restart after the update: the harness reads the plugin's servers once, at session start.
_UPDATE_ASK = "Update it in a terminal, then start a new session:"


def refusal_from_http_error(error: urllib.error.HTTPError, limit: int = MAX_ERROR_BODY) -> Optional[Refusal]:
    """The refusal an HTTP error answer makes, or ``None`` when it is not a 426; a body that cannot be read names no versions."""
    if error.code != UPGRADE_STATUS:
        return None
    try:
        raw = error.read(limit)
        body = json.loads(raw.decode("utf-8", "replace")) if raw else None
    except Exception:  # noqa: BLE001 - a body that cannot be read or parsed is a refusal with no versions
        body = None
    return refusal_from(error.code, body)


def _need(floor: Optional[str]) -> str:
    return f"it needs {floor} or later" if floor else "it needs a newer release"


def refusal_text(
    refusal: Refusal,
    *,
    sdk_version: Optional[str] = None,
    newer_release: Optional[str] = None,
    harness: Optional[str] = None,
    marketplaces: Optional[Sequence[str]] = None,
    env: Optional[Mapping[str, str]] = None,
    root: Optional[Path] = None,
) -> str:
    """Everything a customer reads about a version refusal, one text per case:

    * the plugin is below its floor (or the refusal names no floor): what was refused, the version needed, then the update;
    * the refusal is typed with a reason that is not about the version: the contact hand-off alone, one line, no id and no
      update command (a fault on Plurai's side reads the same wherever it is met);
    * the SDK the plugin installs (``sdk_version``) is below the SDK's floor: the same ask, and a newer plugin release
      named only when ``newer_release`` (the newest tag the plugin has seen) is given.

    ``harness`` and ``marketplaces`` default to the running harness and its install record. No line is a ``pip`` line,
    no line repeats the backend's own sentence, and only values that parse as versions are printed.
    """
    if refusal.reason in NON_VERSION_REASONS:
        import stop_message  # noqa: PLC0415 - the contact text has one home; it pulls in argparse and subprocess, which only a refusal that renders it should pay for

        return stop_message.contact_message([])
    version = plugin_version(root)
    kind = harness or running_harness(env)
    if marketplaces is None:
        import _install_record  # noqa: PLC0415 - read only here: the standalone copies of this module carry no install record reader

        marketplaces = _install_record.installed_marketplaces(kind, CLIENT_NAME, PLUGIN_ROOT if root is None else root, os.environ if env is None else env)
    command = update_command(kind, marketplaces)
    plugin_floor = refusal.floor_of(CLIENT_NAME)
    plugin_meets_floor = _at_least(version, plugin_floor)
    sdk_floor = refusal.floor_of(SDK_NAME)
    sdk_below_floor = sdk_floor is not None and version_key(sdk_version) is not None and not _at_least(sdk_version, sdk_floor)
    # An answer that names no refused client and shows the plugin at its floor is a refusal of the SDK pin when the pin is below its floor.
    if refusal.client == SDK_NAME or (refusal.client is None and plugin_meets_floor and sdk_below_floor):
        shown = f" ({SDK_NAME} {sdk_version})" if version_key(sdk_version) is not None else ""
        if version_key(newer_release) is not None and not _at_least(version, newer_release):
            ask = f"A newer plugin release is available ({newer_release}). Update the plugin in a terminal, then start a new session:"
        else:
            ask = "Plurai needs a newer plugin release than the one installed. Check for an update:"
        return "\n".join([f"Plurai refused the SDK this plugin installs{shown}: {_need(sdk_floor)}.", ask, command])
    # A floor the plugin already meets is never printed as the thing it needs.
    return "\n".join([f"Plurai refused this plugin version ({CLIENT_NAME} {version}): {_need(None if plugin_meets_floor else plugin_floor)}.", _UPDATE_ASK, command])


if __name__ == "__main__":
    # Run under `python3 -I`, which leaves the script's own folder off `sys.path`.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _stdio import utf8_stdio

    utf8_stdio()
    # `sdk-refusal`: what to say when an SDK command exited `sdk_exit_codes.UPGRADE_REQUIRED`, its captured output on stdin.
    if sys.argv[1:] == ["sdk-refusal"]:
        print(sdk_refusal_message(sys.stdin.read()))
        sys.exit(0)
    print("usage: _client_handshake.py sdk-refusal", file=sys.stderr)
    sys.exit(64)
