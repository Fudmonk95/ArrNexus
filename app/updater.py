from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

DEFAULT_REPOSITORY = "Fudmonk95/ArrNexus"
DATA_DIR = Path(os.getenv("DB_DIR", "/data")).resolve()
RUNTIME_DIR = DATA_DIR / "runtime"
RELEASES_DIR = RUNTIME_DIR / "releases"
VENVS_DIR = RUNTIME_DIR / "venvs"
STATUS_PATH = DATA_DIR / "update-status.json"
RESTART_REQUEST_PATH = RUNTIME_DIR / "restart-request.json"
SELF_UPDATE_CAPABLE = os.getenv("ARRNEXUS_SELF_UPDATE", "0").lower() in {"1", "true", "yes", "on"}

_LOCK = threading.Lock()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def status() -> dict[str, Any]:
    try:
        data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _set_status(state: str, message: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "state": state,
        "message": message,
        "updated_at": _utcnow(),
        **extra,
    }
    _write_json(STATUS_PATH, payload)
    return payload


def normalize_repo(value: str | None) -> str:
    raw = (value or "").strip() or DEFAULT_REPOSITORY
    match = re.search(r"(?:github\.com/)?([^/\s]+)/([^/\s]+?)(?:\.git)?$", raw.rstrip("/"), flags=re.I)
    if not match:
        raise ValueError("Use owner/repository or a GitHub repository URL")
    return f"{match.group(1)}/{match.group(2)}"


def version_key(value: str | None) -> tuple[int, int, int, int]:
    raw = (value or "").strip().lower().lstrip("v")
    nums = [int(x) for x in re.findall(r"\d+", raw)[:3]]
    nums += [0] * (3 - len(nums))
    # Stable is considered newer than a prerelease with the same numeric tuple.
    prerelease = 0 if any(x in raw for x in ("alpha", "beta", "rc", "dev")) else 1
    return nums[0], nums[1], nums[2], prerelease


def _choose_release(rows: list[dict[str, Any]], channel: str) -> dict[str, Any] | None:
    candidates = [row for row in rows if isinstance(row, dict) and not row.get("draft")]
    if channel == "stable":
        candidates = [row for row in candidates if not row.get("prerelease")]
    elif channel == "beta":
        # Beta users may move to either a newer stable or prerelease.
        candidates = candidates
    else:  # development
        candidates = candidates
    if not candidates:
        return None
    return max(candidates, key=lambda row: version_key(str(row.get("tag_name") or row.get("name") or "0")))


def _assets_map(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for asset in release.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name"):
            out[str(asset["name"])] = asset
    return out


async def check_for_update(current_version: str, repository: str | None, channel: str = "beta") -> dict[str, Any]:
    repo = normalize_repo(repository)
    channel = channel if channel in {"stable", "beta", "development"} else "beta"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"ArrNexus/{current_version} self-update-checker",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=headers) as client:
        response = await client.get(f"https://api.github.com/repos/{repo}/releases", params={"per_page": 30})
        response.raise_for_status()
        rows = response.json()
    if not isinstance(rows, list):
        raise RuntimeError("GitHub returned an unexpected release response")
    release = _choose_release(rows, channel)
    if not release:
        return {
            "configured": True,
            "repository": repo,
            "channel": channel,
            "current": current_version,
            "latest": current_version,
            "update_available": False,
            "self_update_capable": SELF_UPDATE_CAPABLE,
        }
    latest = str(release.get("tag_name") or release.get("name") or "").lstrip("v")
    assets = _assets_map(release)
    expected_zip = f"arrnexus-v{latest}.zip"
    zip_asset = assets.get(expected_zip)
    if not zip_asset:
        # Be tolerant of a release title such as v10.0.0-beta but an asset named arrnexus-v10.0.zip.
        zip_asset = next((a for n, a in assets.items() if n.lower().startswith("arrnexus-v") and n.lower().endswith(".zip")), None)
    checksum_asset = None
    if zip_asset:
        checksum_asset = assets.get(str(zip_asset.get("name")) + ".sha256")
    if not checksum_asset:
        checksum_asset = next((a for n, a in assets.items() if n.lower().endswith(".sha256")), None)
    available = bool(latest and version_key(latest) > version_key(current_version))
    return {
        "configured": True,
        "repository": repo,
        "channel": channel,
        "current": current_version,
        "latest": latest or current_version,
        "update_available": available,
        "release_url": release.get("html_url") or "",
        "published_at": release.get("published_at") or "",
        "notes": (release.get("body") or "")[:6000],
        "zip_name": str(zip_asset.get("name") or "") if zip_asset else "",
        "zip_url": str(zip_asset.get("browser_download_url") or "") if zip_asset else "",
        "sha256_url": str(checksum_asset.get("browser_download_url") or "") if checksum_asset else "",
        "installable": bool(available and zip_asset and checksum_asset and SELF_UPDATE_CAPABLE),
        "self_update_capable": SELF_UPDATE_CAPABLE,
        "reason": "" if zip_asset and checksum_asset else "GitHub Release must contain the ArrNexus ZIP and matching .sha256 asset",
    }


def _download(url: str, destination: Path) -> None:
    if not url.lower().startswith("https://"):
        raise ValueError("Update downloads must use HTTPS")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, timeout=60.0, follow_redirects=True, headers={"User-Agent": "ArrNexus-self-updater/10"}) as response:
        response.raise_for_status()
        with destination.open("wb") as fh:
            for chunk in response.iter_bytes():
                fh.write(chunk)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_checksum(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"\b([a-fA-F0-9]{64})\b", text)
    if not match:
        raise ValueError("Checksum asset did not contain a SHA-256 digest")
    return match.group(1).lower()


def _safe_extract(zip_path: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    dest_resolved = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if not members:
            raise ValueError("Release ZIP is empty")
        for member in members:
            name = member.filename.replace("\\", "/")
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
                raise ValueError(f"Unsafe absolute ZIP path: {name}")
            target = (destination / name).resolve()
            if target != dest_resolved and dest_resolved not in target.parents:
                raise ValueError(f"Unsafe ZIP traversal path: {name}")
        archive.extractall(destination)
    roots = [p for p in destination.iterdir() if p.name != "__MACOSX"]
    root = roots[0] if len(roots) == 1 and roots[0].is_dir() else destination
    for required in ("app/main.py", "requirements.txt", "validate.py", "Dockerfile", "docker-compose.yml"):
        if not (root / required).exists():
            raise ValueError(f"Release ZIP missing required file: {required}")
    return root


def _backup_database(label: str) -> Path:
    db_path = DATA_DIR / "router.db"
    if os.getenv("DB_PATH"):
        db_path = Path(os.environ["DB_PATH"]).resolve()
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    destination = backup_dir / f"arrnexus-before-{label}-{stamp}.db"
    if not db_path.exists():
        return destination
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(destination))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return destination


def _release_version(root: Path) -> str:
    source = (root / "app" / "main.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', source, flags=re.M)
    if not match:
        raise ValueError("Release does not declare APP_VERSION")
    return match.group(1).strip().lstrip("v")


def _create_release_venv(version: str, root: Path) -> Path:
    venv_dir = VENVS_DIR / version
    python = venv_dir / "bin" / "python"
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(venv_dir)], check=True, timeout=180)
    subprocess.run(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(root / "requirements.txt")],
        cwd=root,
        check=True,
        timeout=600,
    )
    return python


def _run_validation(python: Path, root: Path) -> None:
    env = os.environ.copy()
    temp_db_dir = Path(tempfile.mkdtemp(prefix="arrnexus-update-validate-", dir=str(DATA_DIR)))
    env["DB_DIR"] = str(temp_db_dir)
    env["DB_PATH"] = str(temp_db_dir / "validator.db")
    env["SESSION_SECRET"] = "arrnexus-v10-update-validation-only"
    try:
        proc = subprocess.run([str(python), str(root / "validate.py")], cwd=root, env=env, text=True, capture_output=True, timeout=900)
        output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        if proc.returncode != 0:
            raise RuntimeError("Downloaded release validator failed:\n" + output[-8000:])
    finally:
        shutil.rmtree(temp_db_dir, ignore_errors=True)


def _copy_release(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, symlinks=False)


def install_release(metadata: dict[str, Any], current_version: str) -> None:
    if not SELF_UPDATE_CAPABLE:
        raise RuntimeError("This ArrNexus container was not built with the v10 self-update bootstrap")
    target_version = str(metadata.get("latest") or "").lstrip("v")
    if not target_version or version_key(target_version) <= version_key(current_version):
        raise ValueError("No newer release is available")
    zip_url = str(metadata.get("zip_url") or "")
    sha_url = str(metadata.get("sha256_url") or "")
    if not zip_url or not sha_url:
        raise ValueError("The selected GitHub Release does not provide both ZIP and SHA-256 assets")

    if not _LOCK.acquire(blocking=False):
        raise RuntimeError("An ArrNexus update is already running")
    work_dir = Path(tempfile.mkdtemp(prefix="arrnexus-update-", dir=str(DATA_DIR)))
    try:
        _set_status("downloading", f"Downloading ArrNexus {target_version}", current=current_version, target=target_version, progress=10)
        zip_path = work_dir / "release.zip"
        sha_path = work_dir / "release.zip.sha256"
        _download(zip_url, zip_path)
        _download(sha_url, sha_path)

        _set_status("verifying", "Verifying SHA-256 checksum", current=current_version, target=target_version, progress=25)
        expected = _parse_checksum(sha_path)
        actual = _sha256(zip_path)
        if actual != expected:
            raise RuntimeError(f"SHA-256 mismatch: expected {expected}, got {actual}")

        extract_dir = work_dir / "extract"
        release_root = _safe_extract(zip_path, extract_dir)
        declared = _release_version(release_root)
        if version_key(declared) != version_key(target_version):
            raise RuntimeError(f"Downloaded package declares {declared}, GitHub Release advertised {target_version}")

        _set_status("backup", "Creating a transaction-safe database backup", current=current_version, target=target_version, progress=35)
        backup = _backup_database(target_version.replace("/", "-"))

        _set_status("dependencies", "Preparing isolated release dependencies", current=current_version, target=target_version, progress=48, backup=str(backup))
        python = _create_release_venv(target_version, release_root)

        _set_status("validating", "Running the full ArrNexus regression validator", current=current_version, target=target_version, progress=65, backup=str(backup))
        _run_validation(python, release_root)

        _set_status("staging", "Staging the verified release", current=current_version, target=target_version, progress=82, backup=str(backup))
        destination = RELEASES_DIR / target_version
        _copy_release(release_root, destination)

        request = {
            "target": target_version,
            "previous": current_version,
            "requested_at": _utcnow(),
            "backup": str(backup),
        }
        _write_json(RESTART_REQUEST_PATH, request)
        _set_status("restarting", "Update verified. ArrNexus is restarting into the new release.", current=current_version, target=target_version, progress=95, backup=str(backup))
        time.sleep(1.0)
        os.kill(os.getpid(), signal.SIGTERM)
    except Exception as exc:
        _set_status("failed", str(exc), current=current_version, target=target_version, progress=0)
        raise
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        _LOCK.release()


def start_install(metadata: dict[str, Any], current_version: str) -> None:
    thread = threading.Thread(target=install_release, args=(metadata, current_version), name="arrnexus-self-update", daemon=True)
    thread.start()
