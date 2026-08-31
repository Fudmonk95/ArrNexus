from __future__ import annotations

"""Cooperative subprocess control for cancellable ArrNexus background jobs."""

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

    Cancellation first sends terminate(), then kill() only if the child does not
    exit within ``terminate_timeout``. The function intentionally mirrors the
    useful subset of ``subprocess.run`` used by ArrNexus.
    """
    command = [str(x) for x in cmd]
    if _cancelled(cancel_check):
        raise CancelledOperation("Operation cancelled before subprocess start")

    popen_kwargs: dict[str, Any] = dict(kwargs)
    popen_kwargs.update({"cwd": cwd, "env": env, "text": text})
    if capture_output:
        popen_kwargs["stdout"] = subprocess.PIPE
        popen_kwargs["stderr"] = subprocess.PIPE

    started = time.monotonic()
    proc = subprocess.Popen(command, **popen_kwargs)
    stdout = stderr = None
    while True:
        if _cancelled(cancel_check):
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                stdout, stderr = proc.communicate(timeout=max(0.1, float(terminate_timeout)))
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                stdout, stderr = proc.communicate()
            raise CancelledOperation("Operation cancelled")

        if timeout is not None and time.monotonic() - started > float(timeout):
            try:
                proc.kill()
            finally:
                stdout, stderr = proc.communicate()
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
