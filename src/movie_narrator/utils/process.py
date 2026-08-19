# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Cross-platform subprocess governance helpers for ffmpeg children.

The render pipeline shells out to ffmpeg both directly and through MoviePy.
A runaway ffmpeg can pin CPU/GPU/disk resources indefinitely, so this module
centralizes the three primitives needed to keep that bounded:

* :func:`run_ffmpeg_subprocess` — a :func:`subprocess.run` replacement that
  launches the child in its own process group (POSIX) and, on deadline,
  terminates the whole tree before raising a descriptive timeout error.
* :func:`terminate_process_tree` — force-terminates a process and all of its
  descendants on Windows and POSIX.
* :func:`terminate_processes_matching` — discovers processes by command-line
  substring and terminates each tree; used when a library (MoviePy) launches
  ffmpeg outside our direct control.

Only the standard library is used.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from typing import Sequence

logger = logging.getLogger(__name__)

#: Grace period (seconds) between SIGTERM and SIGKILL when killing a tree.
_DEFAULT_KILL_GRACE = 5.0

# ``SIGKILL`` is not defined on Windows; fall back to SIGTERM there so the
# POSIX-only code path stays importable/testable on every platform.
_SIGTERM = signal.SIGTERM
_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


class SubprocessTimeoutError(subprocess.SubprocessError):
    """Raised when a subprocess exceeds its deadline and was terminated.

    Carries the offending command, the deadline, and (when available) the
    process id so callers and logs can correlate the failure with the runaway
    child.
    """

    def __init__(
        self,
        cmd: Sequence[str],
        timeout: float,
        pid: int | None = None,
    ) -> None:
        self.cmd = list(cmd)
        self.timeout = timeout
        self.pid = pid
        super().__init__(
            f"subprocess exceeded {timeout:.1f}s deadline "
            f"(pid={pid}): {' '.join(self.cmd[:6])}"
        )


def _cmd_summary(cmd: Sequence[str], limit: int = 8) -> str:
    """Return a readable, truncated rendering of a command line."""
    rendered = " ".join(str(part) for part in cmd)
    if len(rendered) > limit * 60:
        rendered = rendered[: limit * 60] + "..."
    return rendered


def terminate_process_tree(pid: int, grace: float = _DEFAULT_KILL_GRACE) -> None:
    """Terminate ``pid`` and all of its descendant processes.

    Windows shells out to ``taskkill /PID <pid> /T /F`` (tree + force). POSIX
    signals the process group with SIGTERM and, if the tree is still alive
    after ``grace`` seconds, escalates to SIGKILL.

    Args:
        pid: Root process id of the tree to terminate. Values ``<= 0`` are
            ignored.
        grace: (POSIX only) Seconds to wait after SIGTERM before SIGKILL.

    Raises:
        OSError: Only if the platform kill helper cannot be invoked at all.
            Already-dead processes are treated as success (best-effort).
    """
    if pid is None or pid <= 0:
        return

    if os.name == "nt":
        _terminate_windows_tree(pid)
        return
    _terminate_posix_tree(pid, grace)


def _terminate_windows_tree(pid: int) -> None:
    """Force-kill a process tree on Windows via ``taskkill``."""
    try:
        subprocess.run(  # nosec B607  # taskkill is a Windows system tool resolved on PATH
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        logger.warning("taskkill failed for pid %d: %s", pid, exc)


def _signal_single(pid: int, sig: int) -> None:
    """Signal a bare process, ignoring "already dead" races."""
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        logger.warning("cannot signal process %d with signal %r", pid, sig)


def _process_group_alive(pid: int) -> bool:
    """Return ``True`` if the process group (or bare process) still exists."""
    try:
        os.killpg(pid, 0)  # type: ignore[attr-defined]  # POSIX-only
        return True
    except ProcessLookupError:
        pass
    except PermissionError:
        return True

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _terminate_posix_tree(pid: int, grace: float) -> None:
    """SIGTERM a process group, wait up to ``grace``, then SIGKILL."""
    try:
        os.killpg(pid, _SIGTERM)  # type: ignore[attr-defined]  # POSIX-only
    except ProcessLookupError:
        _signal_single(pid, _SIGTERM)
    except PermissionError:
        logger.warning("cannot SIGTERM process group %d (permission denied)", pid)
        return

    grace = max(0.0, grace)
    deadline = time.monotonic() + grace
    while _process_group_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)

    if not _process_group_alive(pid):
        return

    try:
        os.killpg(pid, _SIGKILL)  # type: ignore[attr-defined]  # POSIX-only
    except ProcessLookupError:
        _signal_single(pid, _SIGKILL)
    except PermissionError:
        logger.warning("cannot SIGKILL process group %d (permission denied)", pid)


def run_ffmpeg_subprocess(
    cmd: Sequence[str],
    *,
    timeout: float,
    capture_output: bool = True,
    text: bool = True,
    encoding: str = "utf-8",
    errors: str = "replace",
    grace: float = _DEFAULT_KILL_GRACE,
) -> subprocess.CompletedProcess:
    """Run ``cmd`` with a wall-clock deadline and process-tree cleanup.

    On POSIX the child is started with ``start_new_session=True`` so it leads
    its own process group — the whole group can then be terminated together.
    On Windows the child is killed via ``taskkill /T`` (tree).

    Args:
        cmd: Command line to execute (``cmd[0]`` is the ffmpeg binary path).
        timeout: Deadline in seconds for the child to exit.
        capture_output: Mirror of :func:`subprocess.run` — capture stdout and
            stderr into the returned :class:`subprocess.CompletedProcess`.
        text: Decode captured output as text (``str``) when ``True``.
        encoding: Text decoding to use when ``text`` is ``True``.
        errors: Error handling for text decoding.
        grace: (POSIX only) Seconds between SIGTERM and SIGKILL after timeout.

    Returns:
        A :class:`subprocess.CompletedProcess` describing the finished child.

    Raises:
        SubprocessTimeoutError: If the child exceeded ``timeout`` and its
            process tree was terminated.
        OSError: If the binary cannot be started.
    """
    start = time.monotonic()
    # Detach the child into its own session/process group on POSIX so
    # ``terminate_process_tree`` can signal every descendant at once.
    # Windows uses its own process-tree kill path and does not need it.
    # ``capture_output`` is a subprocess.run() convenience; Popen needs the
    # explicit stdout/stderr pipes.
    stdout_pipe = subprocess.PIPE if capture_output else None
    stderr_pipe = subprocess.PIPE if capture_output else None
    proc = subprocess.Popen(  # nosec B607  # cmd[0] is an ffmpeg path resolved by ffmpeg_bin
        list(cmd),
        stdout=stdout_pipe,
        stderr=stderr_pipe,
        text=text,
        encoding=encoding,
        errors=errors,
        start_new_session=(os.name != "nt"),
    )

    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.error(
            "subprocess timed out after %.1fs (pid=%d): %s",
            time.monotonic() - start,
            proc.pid,
            _cmd_summary(cmd),
        )
        terminate_process_tree(proc.pid, grace=grace)
        try:
            proc.wait(timeout=max(1.0, grace))
        except subprocess.TimeoutExpired:
            logger.warning("subprocess pid=%d not reaped after tree kill", proc.pid)
        raise SubprocessTimeoutError(cmd, timeout, proc.pid) from None

    return subprocess.CompletedProcess(list(cmd), proc.returncode, out, err)


def _find_processes_posix(substring: str) -> list[int]:
    """Find PIDs whose command line contains ``substring`` on POSIX."""
    try:
        proc = subprocess.run(  # nosec B607  # ps is a POSIX system tool resolved on PATH
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    pids: list[int] = []
    for line in proc.stdout.splitlines():
        parts: list[str] = line.strip().split(None, 1)
        if len(parts) != 2 or substring not in parts[1]:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        pids.append(pid)
    return pids


def _find_processes_windows(substring: str) -> list[int]:
    """Find PIDs whose command line contains ``substring`` on Windows."""
    escaped = substring.replace("'", "''")
    ps_cmd = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        (
            "Get-CimInstance Win32_Process | "
            f"Where-Object {{ $_.CommandLine -like '*{escaped}*' }} | "
            "Select-Object -ExpandProperty ProcessId"
        ),
    ]
    try:
        proc = subprocess.run(  # nosec B607  # powershell is a Windows system tool on PATH
            ps_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    pids: list[int] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        pids.append(pid)
    return pids


def find_processes_by_cmdline(substring: str) -> list[int]:
    """Return PIDs of running processes whose command line contains ``substring``.

    Best-effort by design: any failure (missing tool, permission, parse error)
    yields an empty list rather than raising, because this is called on the
    failure path where the original render error must remain primary.

    Args:
        substring: Case-sensitive substring to match against each process's
            full command line.

    Returns:
        List of integer PIDs (possibly empty) whose command line contains
        ``substring``.
    """
    if not substring:
        return []
    if os.name == "nt":
        return _find_processes_windows(substring)
    return _find_processes_posix(substring)


def terminate_processes_matching(
    substring: str,
    grace: float = _DEFAULT_KILL_GRACE,
) -> None:
    """Terminate every process tree whose command line contains ``substring``.

    Used to kill an ffmpeg worker launched by a library (MoviePy) where the
    calling code never receives its :class:`~subprocess.Popen` handle.

    Args:
        substring: Command-line substring identifying the runaway worker
            (e.g. the artifact path it is writing).
        grace: (POSIX only) Seconds between SIGTERM and SIGKILL.
    """
    pids = find_processes_by_cmdline(substring)
    if not pids:
        return
    logger.info(
        "terminating %d process tree(s) matching %r",
        len(pids),
        substring,
    )
    for pid in pids:
        terminate_process_tree(pid, grace=grace)
