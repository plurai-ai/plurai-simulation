#!/usr/bin/env python3
"""Start a run that outlives the shell call that starts it, and stops when the session that started it ends.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run_start.py" -- "$RUNNER" <runner flags> --out <run-dir> ...
    python3 run_start.py --alive <pid>     # exit 0 while process <pid> lives (run_stage.sh's owner check on Windows)

WHY. A shell call the harness tracks has a wall-clock limit: Claude Code stops a background command after 30 minutes by
default and 2 hours at most, and a headless session waits only so long for background work. A full run takes longer,
and a run that IS that call is killed with it (exit 143) mid-scenario. So the run is not the call: this starts
`run_stage.sh -- <runner command>` in a session and process group of its own, its output in `.<run-name>.runner_output`
beside the run dir (never inside it: a run dir is what an upload packs), and returns as soon as `run_stage.sh` has
written `<run-dir>/.runner_pid`. The session waits on the run with `run_wait.sh <run-dir>`, a bounded read-only poll,
and stops it with `kill "$(cat <run-dir>/.runner_pid)"`. A bare `nohup … &` detaches too, but it neither ties the run
to its session (THE OWNER) nor keeps a resumed session from starting it twice (ATTACH), and on Windows it does not leave
the harness's job, which ends the run with the call anyway.

THE OWNER. The run is tied to the harness process this call runs under: the nearest ancestor that is not a shell or
a command wrapper (`uv run`, `xargs`, `script`, a wrapper script; `RUN_OWNER_PID` names it instead when set). Never
"the first process above the nearest shell": a shell often execs the call's last command, and the nearest shell is
then the terminal's, above the harness. With none found (no harness, or
no readable process table) the run is not tied, and the launch says so in one line. `run_stage.sh` checks every 2 s that it lives and stops
the run when it is gone (exit 129), so a session that ends takes its run with it, as it did while the call was the run.
A sub-agent shares its session's process, so a run it starts outlives the sub-agent.

ATTACH. A run already playing in the same run dir (its `.runner_pid` is a live `run_stage.sh` and no `.runner_exit` has
landed) is attached to when its recorded command (`.<run-name>.runner_command`) is this one, so a resumed session never
starts the run twice; with any other command this refuses and starts nothing.

EXIT CODES
  0   the run is playing (started, or attached to)
  1   it did not start (the output's last lines are printed), or a different run is playing in that run dir
  64  usage error (no runner command after `--`, or no `--out`)
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WINDOWS = os.name == 'nt'
#: The shells a harness runs a call in.
SHELLS = frozenset({'sh', 'bash', 'zsh', 'dash', 'ksh', 'mksh', 'fish', 'tcsh', 'csh', 'pwsh', 'powershell', 'cmd'})
#: Wrappers that run a call (and end with it) rather than own the session. A wrapper SCRIPT reads as its interpreter,
#: a shell: a process is named by its `argv[0]`, which for a script is the interpreter its shebang names.
WRAPPERS = frozenset({
    'env', 'timeout', 'gtimeout', 'nohup', 'nice', 'stdbuf', 'time', 'sandbox-exec', 'bwrap', 'script', 'xargs',
    'uv', 'uvx', 'make', 'just',
})  # fmt: skip
#: How long the launch waits for `run_stage.sh` to write its pid, in seconds.
START_SECONDS = 10.0


def _kernel32():  # noqa: ANN202 - a ctypes library
    """kernel32 with the signatures this script calls declared, so a handle is never truncated to a C int."""
    import ctypes  # noqa: PLC0415 - Windows only
    from ctypes import wintypes  # noqa: PLC0415

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    for name, args, result in (
        ('OpenProcess', (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD), wintypes.HANDLE),
        ('GetExitCodeProcess', (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)), wintypes.BOOL),
        ('CloseHandle', (wintypes.HANDLE,), wintypes.BOOL),
        ('CreateToolhelp32Snapshot', (wintypes.DWORD, wintypes.DWORD), wintypes.HANDLE),
        ('Process32FirstW', (wintypes.HANDLE, ctypes.c_void_p), wintypes.BOOL),
        ('Process32NextW', (wintypes.HANDLE, ctypes.c_void_p), wintypes.BOOL),
    ):
        function = getattr(kernel32, name)
        function.argtypes, function.restype = args, result
    return kernel32


def alive(pid: int) -> bool:
    """Whether process ``pid`` exists. On Windows ``os.kill(pid, 0)`` TERMINATES the process, so it asks the kernel."""
    if pid <= 0:
        return False
    if WINDOWS:
        import ctypes  # noqa: PLC0415 - Windows only
        from ctypes import wintypes  # noqa: PLC0415

        kernel32 = _kernel32()
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: it exists, under another account
        try:
            code = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _processes(timeout: float = 10) -> dict[int, tuple[int, str]]:
    """``{pid: (parent pid, argv[0] or executable)}`` for every process this user can see; ``{}`` when unreadable or past ``timeout``."""
    table: dict[int, tuple[int, str]] = {}
    if WINDOWS:
        import ctypes  # noqa: PLC0415 - Windows only
        from ctypes import wintypes  # noqa: PLC0415

        class Entry(ctypes.Structure):
            _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                        ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                        ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD),
                        ('szExeFile', ctypes.c_wchar * 260)]  # fmt: skip

        kernel32 = _kernel32()
        snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if not snapshot or snapshot == wintypes.HANDLE(-1).value:
            return table
        try:
            entry = Entry()
            entry.dwSize = ctypes.sizeof(Entry)
            more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                table[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
                more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return table
    try:
        out = subprocess.run(['ps', '-A', '-ww', '-o', 'pid=,ppid=,args='], capture_output=True, text=True, encoding='utf-8', errors='replace',
                             timeout=timeout, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return table
    for row in out.splitlines():
        parts = row.split(None, 3)
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2])
    return table


def _name(executable: str) -> str:
    name = executable.replace('\\', '/').rsplit('/', 1)[-1].lstrip('-').lower()
    return name[:-4] if name.endswith('.exe') else name


def owner_pid(timeout: float = 10) -> int | None:
    """The harness process this call runs under (see THE OWNER), or None outside one or with no process table read in ``timeout``."""
    declared = os.environ.get('RUN_OWNER_PID', '').strip()
    if declared.isdigit():
        return int(declared)
    table = _processes(timeout)
    pid = os.getppid()
    for _ in range(32):
        if pid <= 1 or pid not in table:
            return None
        parent, executable = table[pid]
        if _name(executable) not in SHELLS and _name(executable) not in WRAPPERS:
            return pid
        pid = parent
    return None


def _out(argv: list[str]) -> str | None:
    for index, arg in enumerate(argv):
        if arg == '--out' and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith('--out='):
            return arg[len('--out='):]
    return None


def _beside(out: Path, suffix: str) -> Path:
    """``.<run-name>.<suffix>`` beside the run dir, where `run_stage.sh` keeps the run's command."""
    return out.parent / f'.{out.name}.{suffix}'


def _bash() -> str:
    # On Windows a bare `bash` can resolve to System32's WSL launcher before the PATH's Git Bash.
    return shutil.which('bash') or 'bash'


def _stage_alive(pid: int) -> bool:
    """Whether ``pid`` is a live `run_stage.sh` call: alive, and its command line still names the script (a stale pid
    file's pid can belong to an unrelated process by now). Asked in the shell's own terms, where the pid was written:
    a Git Bash pid is not a Windows one. Where `ps` cannot print a command line, liveness alone answers."""
    probe = 'kill -0 "$1" 2>/dev/null || exit 1; a="$(ps -ww -o args= -p "$1" 2>/dev/null)" || exit 0; case "$a" in "" | *run_stage.sh*) exit 0 ;; esac; exit 1'
    try:
        return subprocess.run([_bash(), '-c', probe, '_', str(pid)], capture_output=True, timeout=10, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _first_int(path: Path) -> int | None:
    try:
        text = path.read_text(encoding='utf-8').strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def _command(argv: list[str]) -> str:
    """The line `run_stage.sh` records for this command (its `shlex.join`, led by the run's observability)."""
    observability = os.environ.get('PLURAI_OBSERVABILITY')
    return shlex.join(([f'PLURAI_OBSERVABILITY={observability}'] if observability else []) + argv)


def _tail(path: Path, lines: int = 20) -> str:
    try:
        return '\n'.join(path.read_text(encoding='utf-8', errors='replace').splitlines()[-lines:])
    except OSError:
        return ''


def start(argv: list[str]) -> int:
    raw = _out(argv)
    if not argv or not raw:
        print('usage: run_start.py -- <runner command> ... --out <run-dir> ...', file=sys.stderr)
        return 64
    out = Path(raw.rstrip('/\\') or raw)
    out.mkdir(parents=True, exist_ok=True)
    pid_file, exit_file, output = out / '.runner_pid', out / '.runner_exit', _beside(out, 'runner_output')
    playing = _first_int(pid_file)
    if playing is not None and not exit_file.exists() and _stage_alive(playing):
        recorded = _beside(out, 'runner_command')
        try:
            same = recorded.read_text(encoding='utf-8').strip() == _command(argv)
        except OSError:
            same = False
        if not same:
            print(f'run_start: a different run is playing in {out} (pid {playing}); nothing started. Wait for it, stop it with '
                  f'kill {playing}, or give this run another --out.')
            return 1
        print(f'run_start: this run is already playing in {out} (pid {playing}); output in {output}. Wait on it with run_wait.sh {out}')
        return 0
    # Nothing plays here: an earlier run's markers would read as this run's pid or exit.
    for stale in (pid_file, exit_file):
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            pass
    env = dict(os.environ)
    owner = owner_pid()
    if owner is not None:
        env['RUN_OWNER_PID'] = str(owner)
        env['RUN_OWNER_PYTHON'] = sys.executable
    command = [_bash(), str(HERE / 'run_stage.sh'), '--', *argv]
    with output.open('wb') as sink:
        kwargs: dict = {'stdin': subprocess.DEVNULL, 'stdout': sink, 'stderr': subprocess.STDOUT, 'env': env, 'close_fds': True}
        if WINDOWS:
            flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            try:  # out of the harness's job object (CREATE_BREAKAWAY_FROM_JOB) where the job allows it
                proc = subprocess.Popen(command, creationflags=flags | 0x01000000, **kwargs)
            except OSError:
                proc = subprocess.Popen(command, creationflags=flags, **kwargs)
        else:
            proc = subprocess.Popen(command, start_new_session=True, **kwargs)
    deadline = time.monotonic() + START_SECONDS
    while time.monotonic() < deadline:
        pid = _first_int(pid_file)
        if pid is not None:
            print(f'run_start: the run is playing in {out} (pid {pid}); output in {output}. Wait on it with run_wait.sh {out}')
            if owner is None:
                print(f'run_start: not tied to a session (no harness process found): it plays to its end; stop it with kill {pid}')
            return 0
        if proc.poll() is not None:
            print(f'run_start: the run did not start: run_stage.sh exited {proc.returncode}.\n{_tail(output)}'.rstrip())
            return 1
        time.sleep(0.1)
    print(f'run_start: no run in {out} after {START_SECONDS:.0f} s (no .runner_pid); its output is in {output}')
    return 1


def main(argv: list[str]) -> int:
    if argv[:1] == ['--alive']:
        return 0 if len(argv) == 2 and argv[1].isdigit() and alive(int(argv[1])) else 1
    if argv[:1] == ['--']:
        argv = argv[1:]
    return start(argv)


if __name__ == '__main__':
    sys.path.insert(0, str(HERE))
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main(sys.argv[1:]))
