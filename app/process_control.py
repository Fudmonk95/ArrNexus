from __future__ import annotations

"""Cooperative subprocess control for cancellable ArrNexus background jobs."""

import os
import signal
import subprocess
import time
from typing import Callable, Iterable, Mapping, Any


class CancelledOperation(RuntimeError):
    """Raised when an ArrNexus job requests cooperative cancellation."""


def _cancelled(cancel_check: Callable[[], bool] | None) -> bool:
    try:
        return bool(cancel_check and cancel_check())
    except Exception:
        return False


def _signal_tree(proc: subprocess.Popen, sig: int) -> None:
    """Signal the complete child process group on POSIX, or the child on Windows."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, sig)
        elif sig == signal.SIGTERM:
            proc.terminate()
        else:
            proc.kill()
    except ProcessLookupError:
        pass
    except Exception:
        try:
            proc.terminate() if sig == signal.SIGTERM else proc.kill()
        except Exception:
            pass


def _stop_tree(proc: subprocess.Popen, terminate_timeout: float) -> tuple[Any, Any]:
    """TERM the child tree, then KILL it after a bounded grace period."""
    _signal_tree(proc, signal.SIGTERM)
    try:
        return proc.communicate(timeout=max(0.1, float(terminate_timeout)))
    except subprocess.TimeoutExpired:
        _signal_tree(proc, signal.SIGKILL)
        return proc.communicate()


def run_cancellable(
    cmd: Iterable[str],
    *,
    cancel_check: Callable[[], bool] | None = None,
    timeout: float | None = None,
    terminate_timeout: float = 5.0,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    text: bool = True,
    capture_output: bool = True,
    check: bool = False,
    poll_seconds: float = 0.35,
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    """Run a child process while periodically checking a cancellation callback.

    Every POSIX child starts in its own session, allowing cancellation and timeout
    handling to terminate the complete process tree rather than only the direct
    ffmpeg/ffprobe/unrar process. Cancellation is TERM -> bounded grace -> KILL.
    """
    command = [str(x) for x in cmd]
    if _cancelled(cancel_check):
        raise CancelledOperation("Operation cancelled before subprocess start")

    popen_kwargs: dict[str, Any] = dict(kwargs)
    popen_kwargs.update({"cwd": cwd, "env": env, "text": text})
    if capture_output:
        popen_kwargs["stdout"] = subprocess.PIPE
        popen_kwargs["stderr"] = subprocess.PIPE
    if os.name == "posix" and "start_new_session" not in popen_kwargs:
        popen_kwargs["start_new_session"] = True

    started = time.monotonic()
    proc = subprocess.Popen(command, **popen_kwargs)
    stdout = stderr = None
    while True:
        if _cancelled(cancel_check):
            stdout, stderr = _stop_tree(proc, terminate_timeout)
            raise CancelledOperation("Operation cancelled")

        if timeout is not None and time.monotonic() - started > float(timeout):
            stdout, stderr = _stop_tree(proc, terminate_timeout)
            raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)

        try:
            stdout, stderr = proc.communicate(timeout=max(0.05, float(poll_seconds)))
            break
        except subprocess.TimeoutExpired:
            continue

    completed = subprocess.CompletedProcess(command, int(proc.returncode), stdout, stderr)
    if check and completed.returncode:
        raise subprocess.CalledProcessError(completed.returncode, command, stdout, stderr)
    return completed
