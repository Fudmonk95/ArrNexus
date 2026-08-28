from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import zipfile
from pathlib import Path

_LOCK = threading.RLock()
_CACHE: dict[str, object] = {}

_EXCLUDED_DIRS = {
    "data", ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
    "node_modules", "backups", "aiostreams_backups", ".mypy_cache", ".ruff_cache",
}
_EXCLUDED_NAMES = {".env", "session_secret"}
_EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3"}


def _safe_file(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    if any(part in _EXCLUDED_DIRS for part in rel.parts):
        return False
    if path.name in _EXCLUDED_NAMES:
        return False
    if path.suffix.lower() in _EXCLUDED_SUFFIXES:
        return False
    if path.name.lower().endswith((".zip", ".tar", ".tgz", ".tar.gz")):
        return False
    return path.is_file()


def build_public_release(project_root: Path, version: str) -> dict:
    """Create a source-only public release archive from the running install.

    Persistent runtime data is intentionally excluded. The archive is cached in
    the OS temporary directory and rebuilt only when the process version changes.
    """
    project_root = project_root.resolve()
    filename = f"arrnexus-v{version}.zip"
    cache_key = f"{project_root}:{version}"
    with _LOCK:
        cached = _CACHE.get(cache_key)
        if isinstance(cached, dict) and Path(str(cached.get("path", ""))).exists():
            return dict(cached)

        out_dir = Path(tempfile.gettempdir()) / "arrnexus-public-release"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        if tmp_path.exists():
            tmp_path.unlink()

        root_name = f"arrnexus-v{version}"
        files = [x for x in project_root.rglob("*") if _safe_file(x, project_root)]
        files.sort(key=lambda x: str(x.relative_to(project_root)).lower())

        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for path in files:
                rel = path.relative_to(project_root)
                arcname = str(Path(root_name) / rel)
                info = zipfile.ZipInfo.from_file(path, arcname=arcname)
                # Preserve ordinary file permissions but never make packaged
                # source unexpectedly group/world writable.
                mode = path.stat().st_mode & 0o755
                mode &= ~0o022
                info.external_attr = (mode & 0xFFFF) << 16
                with path.open("rb") as src:
                    zf.writestr(info, src.read(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

        os.replace(tmp_path, out_path)
        digest = hashlib.sha256()
        with out_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        result = {
            "path": str(out_path),
            "filename": filename,
            "sha256": digest.hexdigest(),
            "size": out_path.stat().st_size,
            "files": len(files),
        }
        _CACHE[cache_key] = result
        return dict(result)
