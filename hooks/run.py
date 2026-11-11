"""The hook entry point: ``run.py --harness <harness> <hook>`` or ``run.py --harness <harness> --bundle <a,b,...>``.

Reads the harness's payload from stdin, decodes it into an ``Event`` (``_harness/<harness>.py``),
calls the hook's ``handle(event) -> Decision``, and writes the decision in the shape that harness
reads. Hook logic never sees a harness.

A bundle runs every named hook on the one decoded event, in the order named, in this one process, and
writes the merge of their decisions (``_harness.model.merge``: a deny wins). Each hook keeps its own
fail posture, so a closed hook that fails denies whatever the others decide. The handlers share the
deadline: each gets what is left of it, and one that overruns answers by its own posture. The hook log
gets a line per hook. The harness is named by the command, never inferred from
the environment, so a session that sets another harness's variables cannot change the encoding.

The hook's fail posture is declared in ``registry.py``: a closed hook denies when it cannot decide,
every other hook decides nothing (``_harness/failpost.py``). The process exits 0 on every path that
reached a decision, and 64 for a command line that names no harness or hook.

``PLURAI_HOOK_LOG`` names a file that receives one JSON line per invocation: what the hook decided and
what went on the wire, never the tool input or the prompt. It is the oracle of the live conformance
runs and is written only when the variable is set, so a shipped session never writes it.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

#: Seconds a handler may take. Registered hook timeouts are 10, so the process answers before the
#: harness would kill it. ``--deadline`` overrides it for tests; generated hook commands omit it.
DEADLINE_SECONDS = 8.0

#: The least a handler of a bundle is given when the earlier ones used the whole deadline.
_MIN_HANDLER_SECONDS = 0.05

#: The environment variable that turns the hook log on.
HOOK_LOG_ENV = "PLURAI_HOOK_LOG"

#: Characters of a deny or block reason the hook log keeps.
_LOG_REASON_CHARS = 300

_NAME = re.compile(r"[a-z][a-z0-9_]*")
_USAGE = "usage: run.py --harness <harness> [--deadline SECONDS] (<hook> | --bundle <hook>,<hook>...)"


def _parse(argv: list[str]) -> tuple[str, tuple[str, ...], float] | None:
    """``(harness, hooks, deadline)``, or None for a malformed command line. One hook is named
    positionally, several with ``--bundle a,b``."""
    deadline = DEADLINE_SECONDS
    harness = ""
    bundle: tuple[str, ...] | None = None
    rest = list(argv)
    try:
        while rest and rest[0].startswith("--"):
            flag = rest.pop(0)
            if flag == "--harness":
                harness = rest.pop(0)
            elif flag == "--deadline":
                deadline = float(rest.pop(0))
            elif flag == "--bundle":
                bundle = tuple(rest.pop(0).split(","))
            else:
                return None
    except (IndexError, ValueError):
        return None
    hooks = bundle if bundle is not None else tuple(rest)
    if bundle is not None and rest or not hooks or (bundle is None and len(rest) != 1):
        return None
    if not _NAME.fullmatch(harness) or not all(_NAME.fullmatch(hook) for hook in hooks):
        return None
    return harness, hooks, deadline


def _closed(hook: str) -> bool:
    """Whether the registry marks ``hook`` closed. An unreadable registry marks nothing closed: the
    codecs read the same file, so the install is damaged for every hook, and answering "closed" would
    block every tool call a hook is registered on."""
    try:
        import registry

        return registry.is_closed(hook)
    except Exception:  # noqa: BLE001 - see the docstring
        return False


def _wire(out: str) -> dict[str, object]:
    """What the answer ``out`` asks the harness to do: the permission it carries, whether it rewrites
    the input, and its size. The facts of an answer a codec chose to leave empty are the point: a
    rewrite Codex drops shows as ``bytes: 0``."""
    permission = None
    updated = False
    try:
        nested = json.loads(out).get("hookSpecificOutput") if out else None
        if isinstance(nested, dict):
            permission = nested.get("permissionDecision")
            updated = "updatedInput" in nested
    except ValueError:
        pass
    return {"permission": permission, "updated_input": updated, "bytes": len(out.encode("utf-8"))}


def _log_hook(harness: str, hook: str, started: float, **fields: object) -> None:
    """Append one line to the hook log when ``PLURAI_HOOK_LOG`` names one. Nothing here may change the
    hook's answer, so every failure to write is dropped."""
    path = os.environ.get(HOOK_LOG_ENV)
    if not path:
        return
    with contextlib.suppress(Exception):  # see the docstring
        record = {"t": round(time.time(), 3), "harness": harness, "hook": hook, **fields}
        record["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.:-]")


def _record_refusal(hook: str, decision: object, event: object, *, failed: bool) -> None:
    """Leave a local support reference for a denied tool call, so a refusal the backend never saw can still be
    quoted. The summary names the hook, the event and the tool, never the reason, the input or the prompt. It
    runs after the answer is out and nothing it does can change the answer or the exit code."""
    if getattr(decision, "kind", "") != "deny":
        return
    with contextlib.suppress(Exception):  # see the docstring
        import _support_refs

        tool = _SAFE_NAME.sub("", str(getattr(event, "tool", "") or ""))[:80]
        what = " ".join(part for part in (hook, "denied", str(getattr(event, "kind", "")), tool) if part)
        _support_refs.record("hook_refused", what + (" (hook failed)" if failed else ""))


def _enter_session_directory(cwd: str) -> None:
    """Make the process's working directory the session's.

    Copilot starts a hook process in the plugin directory, but the workdir a hook looks for is the one
    under the directory the session works in, which is the payload's ``cwd``. The hooks resolve it from the
    process's directory, so the entry point is the one place that aligns the two. A ``cwd`` that is not an
    absolute, existing directory changes nothing.

    Only for a harness that starts hooks elsewhere (``Event.starts_in_session_dir`` is False). Claude Code
    and Codex start a hook in the session's directory already, and their payload's ``cwd`` is where the
    model's last ``cd`` left the shell, which a model can steer: moving the process there would let it
    choose the directory the hooks resolve their workdir from.
    """
    if not os.path.isabs(cwd):
        return
    with contextlib.suppress(OSError):
        os.chdir(cwd)


def _serve(harness: str, hooks: tuple[str, ...], deadline: float, started: float) -> int:
    from _harness import failpost, model

    codec = importlib.import_module("_harness." + harness)
    event = codec.decode(sys.stdin.buffer.read())
    if not event.starts_in_session_dir:
        _enter_session_directory(event.cwd)
    # (hook, closed, decision, failures) per handler, in bundle order. Each handler gets what is left of
    # the deadline, so the process still answers before the harness would kill it.
    ran: list[tuple[str, bool, model.Decision, list[str]]] = []
    for hook in hooks:
        closed = _closed(hook)
        failures: list[str] = []
        left = max(deadline - (time.monotonic() - started), _MIN_HANDLER_SECONDS)
        decision = failpost.guard(
            lambda hook=hook: importlib.import_module(hook).handle(event), hook, closed, left, failures.append
        )
        ran.append((hook, closed, decision, failures))
    merged = model.merge([decision for _hook, _closed_flag, decision, _failures in ran])
    delivered = True
    try:
        out = codec.encode(merged, event)
    except Exception as exc:  # noqa: BLE001 - an unencodable decision is a failed hook
        delivered = False  # the work belongs to answers that were not sent
        for _hook, _closed_flag, _decision, failures in ran:
            failures.append("encode")
        merged = failpost.failure_decision(", ".join(hooks), exc, any(closed for _hook, closed, _d, _f in ran))
        out = codec.encode(merged, event)
    sys.stdout.write(out)
    sys.stdout.flush()
    for hook, _closed_flag, decision, _failures in ran if delivered else ():
        failpost.after_delivery(decision, hook)
    for hook, _closed_flag, decision, failures in ran:
        _record_refusal(hook, decision, event, failed=bool(failures))
    for hook, closed, decision, failures in ran:
        reason = decision.text[:_LOG_REASON_CHARS] if decision.kind in ("deny", "block_stop") else ""
        _log_hook(
            harness,
            hook,
            started,
            event=event.kind,
            session_id=event.session_id,
            tool=event.tool,
            decision=decision.kind,
            reason=reason,
            context_chars=len(decision.text) if decision.kind == "context" else 0,
            posture="closed" if closed else "open",
            outcome="failed" if failures else "decided",
            failure=failures[0] if failures else "",
            wire=_wire(out),
        )
    return 0


def main(argv: list[str]) -> int:
    from _utf8_stdio import utf8_stdio  # noqa: PLC0415 - first, so every line this process prints is UTF-8 on Windows

    utf8_stdio()  # also when a caller drives ``main`` without ``__main__``; a no-op off Windows and when already UTF-8
    parsed = _parse(argv)
    if parsed is None:
        print(_USAGE, file=sys.stderr)
        return 64
    harness, hooks, deadline = parsed
    started = time.monotonic()
    try:
        return _serve(harness, hooks, deadline, started)
    except BaseException as exc:  # noqa: BLE001 - the runner itself is broken, so there is no codec to answer with
        names = ", ".join(hooks)
        print(
            f"plurai-hook: run.py could not run {names} for {harness} ({type(exc).__name__}: {exc}) under "
            f"{sys.executable} (Python {sys.version.split()[0]}).",
            file=sys.stderr,
        )
        any_closed = any(_closed(hook) for hook in hooks)
        for hook in hooks:
            _log_hook(harness, hook, started, event="", decision="none", outcome="failed", failure="runner_broken", posture="closed" if _closed(hook) else "open")
        # Exit 2 blocks the tool call on every harness: the only closed answer left without a codec
        # to encode a deny.
        return 2 if any_closed else 0


if __name__ == "__main__":
    from _utf8_stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main(sys.argv[1:]))
