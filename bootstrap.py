#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SEED = Path("/opt/arrnexus-seed")
DATA = Path(os.getenv("DB_DIR", "/data")).resolve()
RUNTIME = DATA / "runtime"
RELEASES = RUNTIME / "releases"
VENVS = RUNTIME / "venvs"
CURRENT = RUNTIME / "current"
ACTIVE = RUNTIME / "active.json"
RESTART_REQUEST = RUNTIME / "restart-request.json"
STATUS = DATA / "update-status.json"
PORT = int(os.getenv("PORT", "8000"))


def version_of(root: Path) -> str:
    source = (root / "app" / "main.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', source, flags=re.M)
    if not match:
        raise RuntimeError(f"Could not determine ArrNexus version from {root}")
    return match.group(1).strip().lstrip("v")


def vkey(value: str) -> tuple[int, int, int, int]:
    raw = value.lower().lstrip("v")
    nums = [int(x) for x in re.findall(r"\d+", raw)[:3]] + [0, 0, 0]
    stable = 0 if any(x in raw for x in ("alpha", "beta", "rc", "dev")) else 1
    return nums[0], nums[1], nums[2], stable


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def set_status(state: str, message: str, **extra) -> None:
    write_json(STATUS, {"state": state, "message": message, "updated_at": time.time(), **extra})


def copy_seed(version: str) -> Path:
    destination = RELEASES / version
    if not destination.exists():
        shutil.copytree(SEED, destination)
    return destination


def set_current(version: str, previous: str = "") -> None:
    destination = RELEASES / version
    if not destination.exists():
        raise RuntimeError(f"Staged ArrNexus release is missing: {version}")
    if CURRENT.exists() or CURRENT.is_symlink():
        CURRENT.unlink()
    CURRENT.symlink_to(destination, target_is_directory=True)
    write_json(ACTIVE, {"version": version, "previous": previous, "activated_at": time.time()})


def ensure_runtime() -> str:
    RELEASES.mkdir(parents=True, exist_ok=True)
    VENVS.mkdir(parents=True, exist_ok=True)
    seed_version = version_of(SEED)
    copy_seed(seed_version)
    active = read_json(ACTIVE)
    active_version = str(active.get("version") or "")
    if not active_version or not (RELEASES / active_version).exists():
        set_current(seed_version)
        return seed_version
    # A Docker image rebuild may intentionally contain a newer bootstrap/runtime.
    if vkey(seed_version) > vkey(active_version):
        set_current(seed_version, active_version)
        return seed_version
    if not CURRENT.exists():
        set_current(active_version, str(active.get("previous") or ""))
    return active_version


def python_for(version: str) -> str:
    candidate = VENVS / version / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def wait_for_health(proc: subprocess.Popen, seconds: float = 35.0) -> bool:
    deadline = time.monotonic() + seconds
    url = f"http://127.0.0.1:{PORT}/api/health"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                if 200 <= response.status < 300:
                    return True
        except Exception:
            pass
        time.sleep(0.6)
    return False


def launch(version: str) -> subprocess.Popen:
    root = RELEASES / version
    env = os.environ.copy()
    env["ARRNEXUS_SELF_UPDATE"] = "1"
    env["ARRNEXUS_RUNTIME_VERSION"] = version
    env["PYTHONPATH"] = str(root)
    cmd = [python_for(version), "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(PORT)]
    return subprocess.Popen(cmd, cwd=root, env=env)


def main() -> int:
    current = ensure_runtime()
    while True:
        request = read_json(RESTART_REQUEST)
        if request.get("target"):
            target = str(request["target"])
            previous = str(request.get("previous") or current)
            set_current(target, previous)
            try:
                RESTART_REQUEST.unlink()
            except FileNotFoundError:
                pass
            current = target

        active = read_json(ACTIVE)
        current = str(active.get("version") or current)
        previous = str(active.get("previous") or "")
        proc = launch(current)
        healthy = wait_for_health(proc)
        if not healthy:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try: proc.kill()
                except Exception: pass
            if previous and previous != current and (RELEASES / previous).exists():
                failed = current
                set_current(previous)
                current = previous
                set_status("rolled_back", f"ArrNexus {failed} did not become healthy. Rolled back automatically to {previous}.", failed=failed, active=previous)
                continue
            return 1

        set_status("running", f"ArrNexus {current} is healthy.", active=current, progress=100)
        rc = proc.wait()
        # A self-update asks the child to terminate, then leaves restart-request.json.
        if RESTART_REQUEST.exists():
            continue
        # For an unexpected application exit, retry the same release. Docker's
        # restart policy remains a second safety net if the bootstrap itself exits.
        time.sleep(1.5)
        if rc == 0:
            continue


if __name__ == "__main__":
    raise SystemExit(main())
