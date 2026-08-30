from __future__ import annotations

"""Review-first RAR discovery and recovery for DMM-visible media sources."""

from pathlib import Path
import hashlib
import os
import re
import shutil
import subprocess
import time
from typing import Any

from .db import cache_get, cache_set, setting_get, setting_set, log_event, add_activity
from .namespace import view_path, logical_from_view, is_within_logical
from .paths import source_root, dumb_root
from .scanner import VIDEO_EXTS, inspect_item, episode_identity, season_hints
from . import media_identity

ARCHIVE_EXTS = {".rar"}
NESTED_ARCHIVE_EXTS = {".rar", ".zip", ".7z", ".tar", ".gz", ".bz2", ".xz"}
_SCAN_CACHE: tuple[float, list[dict[str, Any]]] = (0.0, [])


def extraction_root() -> str:
    return (setting_get("archive_recovery.root", "/mnt/debrid/arrnexus-extracted") or "/mnt/debrid/arrnexus-extracted").strip()


def max_extract_bytes() -> int:
    try:
        gb = max(1, min(2000, int(setting_get("archive_recovery.max_gb", "100") or 100)))
    except Exception:
        gb = 100
    return gb * 1024**3


def save_settings(*, root: str, max_gb: int) -> None:
    root = str(root or "").strip()
    if not root.startswith("/"):
        raise ValueError("Archive extraction root must be an absolute DUMB-visible path")
    if not is_within_logical(root, dumb_root()):
        raise ValueError(f"Archive extraction root must live under {dumb_root()} so Sonarr/Jellyfin can resolve recovered media")
    setting_set("archive_recovery.root", root)
    setting_set("archive_recovery.max_gb", str(max(1, min(2000, int(max_gb or 100)))))


def _first_volume(path: Path) -> bool:
    name = path.name.lower()
    m = re.search(r"\.part(\d+)\.rar$", name)
    return not m or int(m.group(1)) == 1


def _archive_fingerprint(actual: Path) -> str:
    h = hashlib.sha256(str(actual).encode())
    try:
        st = actual.stat(); h.update(f"{st.st_size}:{int(st.st_mtime)}".encode())
    except OSError:
        pass
    return h.hexdigest()


def _volume_count(actual: Path) -> int:
    name = actual.name
    lower = name.lower()
    parent = actual.parent
    part = re.search(r"(?i)^(.*)\.part0*1\.rar$", name)
    if part:
        return max(1, len(list(parent.glob(part.group(1) + ".part*.rar"))))
    if lower.endswith(".rar"):
        stem = name[:-4]
        legacy = [p for p in parent.iterdir() if p.is_file() and re.match(re.escape(stem) + r"\.r\d\d$", p.name, re.I)]
        return 1 + len(legacy)
    return 1


def _ignored_key(logical_path: str, fingerprint: str) -> str:
    return f"archive_recovery:ignored:{hashlib.sha256(logical_path.encode()).hexdigest()[:20]}:{fingerprint[:24]}"


def set_ignored(logical_path: str, fingerprint: str, ignored: bool = True) -> None:
    cache_set(_ignored_key(logical_path, fingerprint), {"ignored": bool(ignored)})


def is_ignored(logical_path: str, fingerprint: str) -> bool:
    row = cache_get(_ignored_key(logical_path, fingerprint))
    return bool(isinstance(row, dict) and row.get("ignored"))


def scan_archives(force: bool = False, limit: int = 500) -> list[dict[str, Any]]:
    global _SCAN_CACHE
    now = time.monotonic()
    if not force and _SCAN_CACHE[1] and now - _SCAN_CACHE[0] < 60:
        return list(_SCAN_CACHE[1])
    root_logical = Path(source_root())
    root_actual = view_path(root_logical)
    rows: list[dict[str, Any]] = []
    if not root_actual.exists():
        return []
    try:
        for actual in root_actual.rglob("*"):
            if len(rows) >= max(1, min(5000, int(limit))):
                break
            if not actual.is_file() or actual.suffix.lower() != ".rar" or not _first_volume(actual):
                continue
            logical = logical_from_view(actual)
            fp = _archive_fingerprint(actual)
            rows.append({
                "logical_path": str(logical), "name": actual.name, "parent": str(logical.parent),
                "fingerprint": fp, "size": int(actual.stat().st_size), "volumes": _volume_count(actual),
                "ignored": is_ignored(str(logical), fp),
                "identity": media_identity.get_identity(str(logical), fp),
                "extracted": cache_get(f"archive_recovery:extracted:{fp}"),
            })
    except (OSError, PermissionError):
        pass
    rows.sort(key=lambda x: (x["ignored"], x["name"].lower()))
    _SCAN_CACHE = (time.monotonic(), list(rows))
    return rows


def _extractor() -> tuple[str, str]:
    for name in ("7zz", "7z"):
        path = shutil.which(name)
        if path:
            return "7z", path
    unrar = shutil.which("unrar")
    if unrar:
        return "unrar", unrar
    raise RuntimeError("No RAR extractor is installed. Rebuild the v10.4 container so 7zip/unrar is available.")


def extractor_state() -> dict[str, Any]:
    try:
        kind, path = _extractor()
        return {"available": True, "kind": kind, "path": path}
    except Exception as exc:
        return {"available": False, "kind": "", "path": "", "error": str(exc)}


def _safe_member(name: str) -> bool:
    clean = str(name or "").replace("\\", "/")
    if not clean or clean.startswith("/") or re.match(r"^[A-Za-z]:/", clean):
        return False
    parts = [p for p in clean.split("/") if p not in {"", "."}]
    return ".." not in parts


def _parse_7z_listing(text: str, archive_path: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current: dict[str, str] = {}
    for raw in (text or "").splitlines() + [""]:
        line = raw.strip("\r")
        if not line.strip():
            if current.get("Path"):
                p = current.get("Path", "")
                if p != archive_path and not (archive_path and Path(p).name == Path(archive_path).name and not rows):
                    try: size = int(current.get("Size") or 0)
                    except Exception: size = 0
                    rows.append({"path": p, "size": size, "attributes": current.get("Attributes", "")})
            current = {}
            continue
        if " = " in line:
            key, value = line.split(" = ", 1); current[key.strip()] = value.strip()
    return rows


def identity_required_for_name(value: str) -> bool:
    stem = Path(str(value or "")).stem.replace("_", " ").replace(".", " ").strip().lower()
    if re.fullmatch(r"(?:season|series)[ -]*\d{1,2}(?:[ -]*\d{4,8})?", stem):
        return True
    if re.fullmatch(r"s\d{1,2}(?:[ -]*complete)?", stem):
        return True
    return len(re.sub(r"[^a-z0-9]+", "", stem)) < 6


def inspect_archive(logical_path: str) -> dict[str, Any]:
    if not is_within_logical(logical_path, source_root()):
        raise RuntimeError("Archive is outside the configured DMM source root")
    actual = view_path(logical_path)
    if not actual.is_file() or actual.suffix.lower() != ".rar":
        raise RuntimeError("RAR source is not available")
    fp = _archive_fingerprint(actual)
    kind, exe = _extractor()
    if kind == "7z":
        proc = subprocess.run([exe, "l", "-slt", "-ba", str(actual)], capture_output=True, text=True, timeout=90, check=False)
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if "password" in combined.lower() and proc.returncode != 0:
            return {"logical_path": logical_path, "fingerprint": fp, "password_protected": True, "entries": [], "safe": False, "classification": "password"}
        if proc.returncode != 0:
            raise RuntimeError("RAR inspection failed: " + " ".join(combined.split())[:600])
        entries = _parse_7z_listing(proc.stdout or "", str(actual))
    else:
        proc = subprocess.run([exe, "lb", "-c-", str(actual)], capture_output=True, text=True, timeout=90, check=False)
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if proc.returncode != 0:
            if "password" in combined.lower():
                return {"logical_path": logical_path, "fingerprint": fp, "password_protected": True, "entries": [], "safe": False, "classification": "password"}
            raise RuntimeError("RAR inspection failed: " + " ".join(combined.split())[:600])
        entries = [{"path": x.strip(), "size": 0, "attributes": ""} for x in (proc.stdout or "").splitlines() if x.strip()]

    unsafe = [x for x in entries if not _safe_member(x.get("path", ""))]
    media = [x for x in entries if Path(x.get("path", "")).suffix.lower() in VIDEO_EXTS]
    nested = [x for x in entries if Path(x.get("path", "")).suffix.lower() in NESTED_ARCHIVE_EXTS]
    unpacked = sum(max(0, int(x.get("size") or 0)) for x in entries)
    classification = "media" if media else "nested" if nested else "non_media"
    identity = media_identity.get_identity(logical_path, fp)
    result = {
        "logical_path": logical_path, "name": actual.name, "fingerprint": fp,
        "entries": entries[:2000], "entry_count": len(entries), "media": media[:500], "media_count": len(media),
        "nested_count": len(nested), "unpacked_size": unpacked, "safe": not unsafe,
        "unsafe_entries": unsafe[:20], "password_protected": False, "classification": classification,
        "volumes": _volume_count(actual), "identity": identity,
        "identity_required": identity_required_for_name(actual.name),
    }
    cache_set(f"archive_recovery:inspect:{fp}", result)
    return result


def _target_logical(logical_path: str, fingerprint: str, identity: dict[str, Any] | None) -> Path:
    title = (identity or {}).get("title") or Path(logical_path).stem
    clean = re.sub(r'[\\/:*?"<>|]+', " ", str(title)).strip().rstrip(".") or "Recovered Media"
    token = fingerprint[:12]
    return Path(extraction_root()) / f"{clean} [{token}]"


def storage_state(required_bytes: int = 0) -> dict[str, Any]:
    root = Path(extraction_root())
    if not is_within_logical(root, dumb_root()):
        raise RuntimeError("Configured extraction root is outside the DUMB-visible filesystem")
    actual = view_path(root)
    actual.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(actual)
    return {"root": str(root), "free": usage.free, "total": usage.total, "required": int(required_bytes or 0), "enough": usage.free > int(required_bytes or 0) + 512 * 1024**2}


def _post_extract_rename(actual_root: Path, identity: dict[str, Any] | None) -> list[dict[str, str]]:
    if not identity:
        return []
    changes = []
    fallback_seasons: list[int] = []
    for file in sorted(actual_root.rglob("*")):
        if not file.is_file() or file.suffix.lower() not in VIDEO_EXTS:
            continue
        new_name = media_identity.canonical_media_name(identity, file.name, fallback_seasons)
        if new_name == file.name:
            continue
        target = file.with_name(new_name)
        if target.exists():
            continue
        file.rename(target); changes.append({"from": file.name, "to": target.name})
    return changes


def extract_archive(logical_path: str, *, expected_fingerprint: str = "") -> dict[str, Any]:
    plan = inspect_archive(logical_path)
    if expected_fingerprint and plan["fingerprint"] != expected_fingerprint:
        raise RuntimeError("RAR source changed after preview; scan/inspect it again")
    if plan.get("password_protected"):
        raise RuntimeError("Password-protected RAR requires manual review")
    if not plan.get("safe"):
        raise RuntimeError("RAR contains unsafe paths and will not be extracted")
    if plan.get("classification") != "media":
        raise RuntimeError("RAR does not directly contain recognised video media. Nested/non-media archives require manual review and are not recursively unpacked.")
    if plan.get("identity_required") and not plan.get("identity"):
        raise RuntimeError("This archive name is ambiguous. Resolve its movie/TV identity before extraction so recovered media is named safely.")
    required = int(plan.get("unpacked_size") or 0)
    if required and required > max_extract_bytes():
        raise RuntimeError(f"Archive expands beyond the configured {max_extract_bytes()/1024**3:.0f} GB safety limit")
    space = storage_state(required)
    if not space["enough"]:
        raise RuntimeError("Not enough free space in the archive recovery root")

    actual_archive = view_path(logical_path)
    identity = plan.get("identity")
    target_logical = _target_logical(logical_path, plan["fingerprint"], identity)
    target_actual = view_path(target_logical)
    partial = target_actual.with_name(target_actual.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True, exist_ok=True)

    kind, exe = _extractor()
    if kind == "7z":
        cmd = [exe, "x", "-y", f"-o{partial}", str(actual_archive)]
    else:
        cmd = [exe, "x", "-o+", str(actual_archive), str(partial) + os.sep]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60 * 60 * 4, check=False)
    if proc.returncode != 0:
        raise RuntimeError("RAR extraction failed: " + " ".join(((proc.stderr or proc.stdout) or "unknown error").split())[:700])

    resolved = partial.resolve()
    for p in partial.rglob("*"):
        if p.is_symlink():
            shutil.rmtree(partial, ignore_errors=True); raise RuntimeError("RAR created a symlink; extraction was discarded")
        try:
            p.resolve().relative_to(resolved)
        except ValueError:
            shutil.rmtree(partial, ignore_errors=True); raise RuntimeError("RAR attempted path traversal; extraction was discarded")

    renamed = _post_extract_rename(partial, identity)
    videos = [p for p in partial.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS]
    if not videos:
        raise RuntimeError("Extraction completed but no recognised video files were produced")
    if target_actual.exists():
        shutil.rmtree(target_actual, ignore_errors=True)
    partial.rename(target_actual)
    item = inspect_item(target_logical)
    log_event("info", "archive_recovery", "extracted", f"Extracted {Path(logical_path).name}", {"target": str(target_logical), "videos": len(videos)})
    add_activity("archive_recovery", (identity or {}).get("title") or Path(logical_path).stem, f"Extracted {len(videos)} media file(s)", str(target_logical))
    result = {"ok": True, "source": logical_path, "target": str(target_logical), "videos": len(videos), "renamed": renamed, "item": item.dict(), "identity": identity}
    cache_set(f"archive_recovery:extracted:{plan['fingerprint']}", result)
    return result
