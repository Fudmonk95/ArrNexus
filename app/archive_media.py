from __future__ import annotations

"""Review-first, media-only RAR recovery for DMM-visible media sources.

v10.4.1 deliberately treats archive health and member health separately.  A
structurally imperfect RAR may still contain independently recoverable video
members.  Only explicitly verified video members are eligible for extraction;
metadata, artwork, torrent padding and nested archives are never unpacked by the
normal recovery workflow.
"""

from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from typing import Any, Callable

from .db import cache_get, cache_set, setting_get, setting_set, log_event, add_activity
from .namespace import view_path, logical_from_view, is_within_logical
from .paths import source_root, dumb_root
from .scanner import VIDEO_EXTS, inspect_item
from . import media_identity
from .process_control import run_cancellable, CancelledOperation

ARCHIVE_EXTS = {".rar"}
NESTED_ARCHIVE_EXTS = {".rar", ".zip", ".7z", ".tar", ".gz", ".bz2", ".xz"}
_SCAN_CACHE: tuple[float, list[dict[str, Any]]] = (0.0, [])

# Torrent creators commonly add zero-value padding members so torrent pieces
# align efficiently.  These are transport artefacts, not useful archive data.
_PADDING_RE = re.compile(r"^\.*_+padding_file(?:/|$)", re.I)


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
        raise ValueError("Recovered media source root must be an absolute DUMB-visible path")
    if not is_within_logical(root, dumb_root()):
        raise ValueError(f"Recovered media source root must live under {dumb_root()} so Sonarr/Jellyfin can resolve recovered media")
    setting_set("archive_recovery.root", root)
    setting_set("archive_recovery.max_gb", str(max(1, min(2000, int(max_gb or 100)))))


def _first_volume(path: Path) -> bool:
    name = path.name.lower()
    m = re.search(r"\.part(\d+)\.rar$", name)
    return not m or int(m.group(1)) == 1


def _legacy_archive_fingerprint(actual: Path) -> str:
    """v10.4/v10.4.1 fingerprint retained only for identity migration.

    It is intentionally not used as a safety boundary because Decypharr's
    virtual /proc view can change PID/mtime while the provider object is still
    the same archive.
    """
    h = hashlib.sha256(str(actual).encode())
    try:
        st = actual.stat()
        h.update(f"{st.st_size}:{int(st.st_mtime)}".encode())
    except OSError:
        pass
    return h.hexdigest()


def _archive_fingerprint(logical_path: str | Path, actual: Path) -> str:
    """Stable provider-source identity for virtual DUMB/Decypharr files.

    Never include /proc/<pid> or mtime: both can legitimately change without
    the Real-Debrid source changing.  File size is retained so a growing or
    replaced source naturally invalidates archive caches/identity.  Content
    safety is enforced separately by ``_catalogue_signature``.
    """
    logical = str(Path(str(logical_path)))
    h = hashlib.sha256(logical.encode("utf-8", errors="replace"))
    try:
        h.update(f":{int(actual.stat().st_size)}".encode())
    except OSError:
        h.update(b":missing")
    return h.hexdigest()


def _catalogue_signature(logical_path: str | Path, entries: list[dict[str, Any]]) -> str:
    """Fingerprint the useful archive catalogue, independent of virtual FS metadata.

    Member path, listed sizes, encryption flag and CRC are sufficient to catch
    the changes that matter to selective extraction, including same-size source
    replacement. Torrent padding is excluded because it is never recovered.
    """
    h = hashlib.sha256(str(Path(str(logical_path))).encode("utf-8", errors="replace"))
    rows = []
    for row in entries or []:
        name = _normal_member(str(row.get("path") or ""))
        if not name or _is_padding_member(name):
            continue
        rows.append((
            name,
            int(row.get("size") or 0),
            int(row.get("packed_size") or 0),
            str(row.get("crc") or "").upper(),
            bool(row.get("encrypted")),
        ))
    for name, size, packed, crc, encrypted in sorted(rows):
        h.update(f"\n{name}|{size}|{packed}|{crc}|{int(encrypted)}".encode("utf-8", errors="replace"))
    return h.hexdigest()


def _archive_identity(logical_path: str, fingerprint: str, legacy_fingerprint: str = "") -> dict[str, Any] | None:
    identity = media_identity.get_identity(logical_path, fingerprint)
    if identity:
        return identity
    if legacy_fingerprint and legacy_fingerprint != fingerprint:
        identity = media_identity.get_identity(logical_path, legacy_fingerprint)
        if identity:
            # One-way migration keeps a TMDb choice made on v10.4/v10.4.1.
            media_identity.save_identity(logical_path, fingerprint, identity)
            return identity
    return None


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


def _inspection_key(fingerprint: str) -> str:
    return f"archive_recovery:inspect:v1042:{fingerprint}"


def _verification_key(fingerprint: str) -> str:
    return f"archive_recovery:verify:v1042:{fingerprint}"


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
            fp = _archive_fingerprint(str(logical), actual)
            legacy_fp = _legacy_archive_fingerprint(actual)
            verify = cache_get(_verification_key(fp))
            rows.append({
                "logical_path": str(logical), "name": actual.name, "parent": str(logical.parent),
                "fingerprint": fp, "size": int(actual.stat().st_size), "volumes": _volume_count(actual),
                "ignored": is_ignored(str(logical), fp),
                "identity": _archive_identity(str(logical), fp, legacy_fp),
                "verification": verify if isinstance(verify, dict) else None,
                "inspection_cached": isinstance(cache_get(_inspection_key(fp)), dict),
                "extracted": cache_get(f"archive_recovery:extracted:{fp}"),
            })
    except (OSError, PermissionError):
        pass
    rows.sort(key=lambda x: (x["ignored"], x["name"].lower()))
    _SCAN_CACHE = (time.monotonic(), list(rows))
    return rows


def cached_inspection(logical_path: str) -> dict[str, Any] | None:
    """Return a previously completed inspection without touching archive data.

    Large cloud-backed archives must never be synchronously listed on an HTTP
    request.  The background inspect job populates this cache; the review page
    only reads it.
    """
    if not is_within_logical(logical_path, source_root()):
        return None
    actual = view_path(logical_path)
    if not actual.is_file() or actual.suffix.lower() != ".rar":
        return None
    fp = _archive_fingerprint(logical_path, actual)
    cached = cache_get(_inspection_key(fp))
    if not isinstance(cached, dict) or cached.get("fingerprint") != fp:
        return None
    cached = dict(cached)
    cached["identity"] = _archive_identity(logical_path, fp, _legacy_archive_fingerprint(actual))
    verify = cache_get(_verification_key(fp))
    cached["verification"] = verify if isinstance(verify, dict) and verify.get("catalogue_signature") == cached.get("catalogue_signature") else None
    cached["cached"] = True
    return cached


def _extractor() -> tuple[str, str]:
    for name in ("7zz", "7z"):
        path = shutil.which(name)
        if path:
            return "7z", path
    unrar = shutil.which("unrar")
    if unrar:
        return "unrar", unrar
    raise RuntimeError("No RAR extractor is installed. Rebuild the v10.4+ container so 7zip/unrar is available.")


def extractor_state() -> dict[str, Any]:
    try:
        kind, path = _extractor()
        return {"available": True, "kind": kind, "path": path}
    except Exception as exc:
        return {"available": False, "kind": "", "path": "", "error": str(exc)}


def _normal_member(value: str) -> str:
    text = str(value or "").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def _is_padding_member(name: str) -> bool:
    return bool(_PADDING_RE.match(_normal_member(name)))


def _is_media_member(name: str) -> bool:
    return Path(_normal_member(name)).suffix.lower() in VIDEO_EXTS


def _safe_member(name: str) -> bool:
    clean = str(name or "").replace("\\", "/")
    if not clean or "\x00" in clean or "\n" in clean or "\r" in clean or clean.startswith("/") or re.match(r"^[A-Za-z]:/", clean):
        return False
    parts = [p for p in clean.split("/") if p not in {"", "."}]
    return ".." not in parts


def _parse_7z_listing(text: str, archive_path: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current: dict[str, str] = {}
    archive_name = Path(archive_path).name if archive_path else ""
    for raw in (text or "").splitlines() + [""]:
        line = raw.strip("\r")
        if not line.strip():
            if current.get("Path"):
                p = current.get("Path", "")
                # The first -slt record can describe the archive itself.
                if p != archive_path and not (archive_name and Path(p).name == archive_name and not rows and "Type" in current):
                    try:
                        size = int(current.get("Size") or 0)
                    except Exception:
                        size = 0
                    rows.append({
                        "path": p,
                        "size": size,
                        "packed_size": int(current.get("Packed Size") or 0) if str(current.get("Packed Size") or "").isdigit() else 0,
                        "attributes": current.get("Attributes", ""),
                        "encrypted": str(current.get("Encrypted") or "-") not in {"", "-"},
                        "crc": str(current.get("CRC") or ""),
                    })
            current = {}
            continue
        if " = " in line:
            key, value = line.split(" = ", 1)
            current[key.strip()] = value.strip()
    return rows


def _archive_issue_lines(stdout: str, stderr: str) -> list[str]:
    """Extract useful 7-Zip warnings/errors without mistaking CRC metadata for errors."""
    lines: list[str] = []
    seen: set[str] = set()
    for raw in ((stderr or "") + "\n" + (stdout or "")).splitlines():
        line = " ".join(raw.strip().split())
        lower = line.lower()
        if not line:
            continue
        if lower.startswith("error:") or lower.startswith("errors:") or lower.startswith("warning:") or lower.startswith("warnings:"):
            # Header-only lines are not useful by themselves.
            if line.endswith(":"):
                continue
        elif not any(token in lower for token in (
            "unexpected end of archive", "data after the end of archive", "crc failed", "data error", "headers error",
            "cannot open", "wrong password", "password", "is not archive", "unexpected end of data",
        )):
            continue
        if line not in seen:
            lines.append(line)
            seen.add(line)
    return lines[:40]


def identity_required_for_name(value: str) -> bool:
    stem = Path(str(value or "")).stem.replace("_", " ").replace(".", " ").strip().lower()
    if re.fullmatch(r"(?:season|series)[ -]*\d{1,2}(?:[ -]*\d{4,8})?", stem):
        return True
    if re.fullmatch(r"s\d{1,2}(?:[ -]*complete)?", stem):
        return True
    return len(re.sub(r"[^a-z0-9]+", "", stem)) < 6


def inspect_archive(logical_path: str, *, force: bool = False, cancel_check: Callable[[], bool] | None = None) -> dict[str, Any]:
    """List an archive without extracting it.

    A non-zero 7-Zip status no longer destroys a useful listing.  If media
    members can be enumerated safely, the result is returned as ``partial`` and
    those members can be independently verified before extraction.
    """
    if not is_within_logical(logical_path, source_root()):
        raise RuntimeError("Archive is outside the configured DMM source root")
    actual = view_path(logical_path)
    if not actual.is_file() or actual.suffix.lower() != ".rar":
        raise RuntimeError("RAR source is not available")
    fp = _archive_fingerprint(logical_path, actual)
    legacy_fp = _legacy_archive_fingerprint(actual)
    if not force:
        cached = cache_get(_inspection_key(fp))
        if isinstance(cached, dict) and cached.get("fingerprint") == fp:
            cached["identity"] = _archive_identity(logical_path, fp, legacy_fp)
            verify = cache_get(_verification_key(fp))
            if isinstance(verify, dict) and verify.get("catalogue_signature") == cached.get("catalogue_signature"):
                cached["verification"] = verify
            else:
                cached["verification"] = None
            cached["cached"] = True
            return cached

    kind, exe = _extractor()
    started = time.monotonic()
    if kind == "7z":
        proc = run_cancellable([exe, "l", "-slt", "-ba", str(actual)], capture_output=True, text=True, timeout=1800, check=False, cancel_check=cancel_check)
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        entries = _parse_7z_listing(proc.stdout or "", str(actual))
    else:
        proc = run_cancellable([exe, "lb", "-c-", str(actual)], capture_output=True, text=True, timeout=1800, check=False, cancel_check=cancel_check)
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        entries = [{"path": x.strip(), "size": 0, "packed_size": 0, "attributes": "", "encrypted": False, "crc": ""} for x in (proc.stdout or "").splitlines() if x.strip()]

    elapsed = round(time.monotonic() - started, 2)
    password = "password" in combined.lower() and proc.returncode != 0
    padding = [x for x in entries if _is_padding_member(x.get("path", ""))]
    visible = [x for x in entries if not _is_padding_member(x.get("path", ""))]
    unsafe = [x for x in visible if not _safe_member(x.get("path", ""))]
    media = [x for x in visible if _is_media_member(x.get("path", ""))]
    nested = [x for x in visible if Path(x.get("path", "")).suffix.lower() in NESTED_ARCHIVE_EXTS]
    non_media = [x for x in visible if x not in media and x not in nested]
    media_size = sum(max(0, int(x.get("size") or 0)) for x in media)
    issues = _archive_issue_lines(proc.stdout or "", proc.stderr or "")

    if password:
        classification = "password"
        health = "failed"
    elif media:
        classification = "media"
        health = "clean" if proc.returncode == 0 and not issues else "partial"
    elif nested:
        classification = "nested"
        health = "failed" if proc.returncode else "clean"
    else:
        classification = "non_media"
        health = "failed" if proc.returncode else "clean"

    # No member list at all is a genuine inspection failure.  A useful media
    # listing with structural warnings is intentionally preserved for per-file
    # verification (the Queen's Nose field case).
    if proc.returncode != 0 and not entries and not password:
        detail = "; ".join(issues) or "7-Zip returned no usable member listing"
        raise RuntimeError(f"RAR inspection failed (exit {proc.returncode}): {detail}")

    catalogue_signature = _catalogue_signature(logical_path, entries)
    identity = _archive_identity(logical_path, fp, legacy_fp)
    verify = cache_get(_verification_key(fp))
    if not isinstance(verify, dict) or verify.get("catalogue_signature") != catalogue_signature:
        verify = None
    result = {
        "logical_path": logical_path,
        "name": actual.name,
        "fingerprint": fp,
        "catalogue_signature": catalogue_signature,
        "entries": visible[:2000],
        "entry_count": len(visible),
        "archive_entry_count": len(entries),
        "padding_count": len(padding),
        "non_media_count": len(non_media),
        "media": media[:1000],
        "media_count": len(media),
        "nested_count": len(nested),
        # v10.4 templates used unpacked_size.  In v10.4.1 this deliberately
        # means media-only output because non-media members are never extracted.
        "unpacked_size": media_size,
        "media_size": media_size,
        "safe": not unsafe,
        "unsafe_entries": unsafe[:20],
        "password_protected": password,
        "classification": classification,
        "health": health,
        "exit_code": int(proc.returncode),
        "issues": issues,
        "inspection_seconds": elapsed,
        "volumes": _volume_count(actual),
        "identity": identity,
        "identity_required": identity_required_for_name(actual.name),
        "verification": verify,
        "cached": False,
    }
    cache_set(_inspection_key(fp), result)
    return result


def _parse_7z_test_output(stdout: str, stderr: str, media: list[dict[str, Any]], exit_code: int) -> dict[str, Any]:
    """Convert 7-Zip test output into member-level verified/failed status."""
    by_norm = {_normal_member(x.get("path", "")): dict(x) for x in media}
    tested: set[str] = set()
    failed: dict[str, str] = {}

    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if line.startswith("T "):
            name = _normal_member(line[2:].strip())
            if name in by_norm:
                tested.add(name)

    fail_patterns = (
        re.compile(r"(?i)(?:ERROR:\s*)?CRC Failed\s*:\s*(.+)$"),
        re.compile(r"(?i)(?:ERROR:\s*)?Data Error\s*:\s*(.+)$"),
        re.compile(r"(?i)Unexpected end of data\s*:\s*(.+)$"),
    )
    for raw in ((stderr or "") + "\n" + (stdout or "")).splitlines():
        line = raw.strip()
        for pattern in fail_patterns:
            m = pattern.search(line)
            if not m:
                continue
            name = _normal_member(m.group(1).strip())
            # 7-Zip sometimes prefixes paths.  Match exact normalized member or
            # an unambiguous suffix.
            match = name if name in by_norm else next((key for key in by_norm if key.endswith("/" + name) or key == name), "")
            if match:
                failed[match] = line
            break

    if int(exit_code) == 0:
        verified = set(by_norm) - set(failed)
    else:
        # With a structurally damaged archive, only members explicitly reached
        # by 7-Zip's test pass can be trusted.  This is the safety boundary that
        # permits partial recovery without blessing untested tail members.
        verified = tested - set(failed)

    members: list[dict[str, Any]] = []
    for key, row in by_norm.items():
        item = dict(row)
        if key in failed:
            item.update({"status": "failed", "error": failed[key]})
        elif key in verified:
            item.update({"status": "verified", "error": ""})
        else:
            item.update({"status": "untested", "error": "Not independently verified"})
        members.append(item)

    return {
        "members": members,
        "verified_count": sum(1 for x in members if x["status"] == "verified"),
        "failed_count": sum(1 for x in members if x["status"] == "failed"),
        "untested_count": sum(1 for x in members if x["status"] == "untested"),
        "issues": _archive_issue_lines(stdout, stderr),
        "exit_code": int(exit_code),
    }


def _verify_media_members_independently(kind: str, exe: str, actual: Path, media: list[dict[str, Any]], progress=None, cancel_check: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Verify each video member in its own extractor invocation.

    Damaged RARs can terminate a multi-member ``7z t`` pass after one bad tail
    member, leaving later/earlier good media untested.  Independent member tests
    isolate that failure: a CRC-broken Season 1 cannot prevent Seasons 2-7 from
    proving themselves recoverable.
    """
    members: list[dict[str, Any]] = []
    issues: list[str] = []
    exit_codes: list[int] = []
    total = len(media)

    for index, row in enumerate(media, start=1):
        if cancel_check and cancel_check():
            raise CancelledOperation("Archive verification cancelled")
        member = str(row.get("path") or "")
        status_row = {**row, "status": "untested", "error": "Not independently verified", "test_exit_code": None}
        try:
            if kind == "7z":
                cmd = [exe, "t", "-bb1", "-spd", str(actual), member]
                proc = run_cancellable(cmd, capture_output=True, text=True, timeout=60 * 60 * 2, check=False, cancel_check=cancel_check)
                parsed = _parse_7z_test_output(proc.stdout or "", proc.stderr or "", [row], proc.returncode)
                parsed_row = (parsed.get("members") or [status_row])[0]
                status_row = {**parsed_row, "test_exit_code": int(proc.returncode)}
                issues.extend(parsed.get("issues") or [])
                exit_codes.append(int(proc.returncode))
            else:
                cmd = [exe, "t", "-c-", str(actual), member]
                proc = run_cancellable(cmd, capture_output=True, text=True, timeout=60 * 60 * 2, check=False, cancel_check=cancel_check)
                ok = proc.returncode == 0
                status_row = {
                    **row,
                    "status": "verified" if ok else "untested",
                    "error": "" if ok else "unrar did not return a clean verification result for this member",
                    "test_exit_code": int(proc.returncode),
                }
                issues.extend(_archive_issue_lines(proc.stdout or "", proc.stderr or ""))
                exit_codes.append(int(proc.returncode))
        except subprocess.TimeoutExpired:
            status_row = {**row, "status": "untested", "error": "Verification timed out for this media member", "test_exit_code": None}
            issues.append(f"Verification timed out: {member}")
        except Exception as exc:
            status_row = {**row, "status": "untested", "error": str(exc)[:500], "test_exit_code": None}
            issues.append(f"Verification failed for {member}: {str(exc)[:180]}")

        members.append(status_row)
        if progress:
            try:
                progress(index, total, member, str(status_row.get("status") or "untested"))
            except Exception:
                pass

    unique_issues: list[str] = []
    seen: set[str] = set()
    for issue in issues:
        if issue and issue not in seen:
            seen.add(issue)
            unique_issues.append(issue)

    return {
        "members": members,
        "verified_count": sum(1 for x in members if x.get("status") == "verified"),
        "failed_count": sum(1 for x in members if x.get("status") == "failed"),
        "untested_count": sum(1 for x in members if x.get("status") == "untested"),
        "issues": unique_issues[:80],
        "exit_code": max(exit_codes, default=0),
        "verification_mode": "per_member",
    }


def verify_archive_media(logical_path: str, *, expected_fingerprint: str = "", progress=None, cancel_check: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Test every video member independently and cache recovery eligibility."""
    plan = inspect_archive(logical_path, force=True, cancel_check=cancel_check)
    if expected_fingerprint and plan.get("fingerprint") != expected_fingerprint:
        raise RuntimeError("RAR source size/path changed after preview; inspect it again")
    if plan.get("password_protected"):
        raise RuntimeError("Password-protected RAR requires manual review")
    if not plan.get("safe"):
        raise RuntimeError("RAR contains unsafe paths and cannot be verified automatically")
    media = list(plan.get("media") or [])
    if not media:
        raise RuntimeError("RAR contains no recognised video media")

    actual = view_path(logical_path)
    kind, exe = _extractor()
    started = time.monotonic()
    parsed = _verify_media_members_independently(kind, exe, actual, media, progress=progress, cancel_check=cancel_check)

    # Decypharr can legitimately change virtual mtime/PID while the same source
    # is mounted. Re-list after the potentially long verification and compare
    # the archive catalogue itself instead of virtual filesystem metadata.
    final_plan = inspect_archive(logical_path, force=True, cancel_check=cancel_check)
    if final_plan.get("catalogue_signature") != plan.get("catalogue_signature"):
        raise RuntimeError("RAR media catalogue changed during verification; inspect and verify it again")

    result = {
        "logical_path": logical_path,
        "fingerprint": plan["fingerprint"],
        "catalogue_signature": plan["catalogue_signature"],
        "tested_at": time.time(),
        "seconds": round(time.monotonic() - started, 2),
        **parsed,
    }
    cache_set(_verification_key(plan["fingerprint"]), result)
    log_event("info", "archive_recovery", "verified", f"Verified media in {Path(logical_path).name}", {
        "verified": result["verified_count"], "failed": result["failed_count"], "untested": result["untested_count"], "exit_code": result["exit_code"], "mode": "per_member",
    })
    return result



def _local_stage_key(fingerprint: str) -> str:
    return f"archive_recovery:local_stage:v105:{fingerprint}"


def _archive_volume_actual_paths(first: Path) -> list[Path]:
    """Return the physical volume set required to read the archive locally."""
    name = first.name
    part = re.match(r"(?i)^(.*)\.part0*1\.rar$", name)
    if part:
        rows = sorted(first.parent.glob(part.group(1) + ".part*.rar"), key=lambda x: x.name.lower())
        return rows or [first]
    if first.suffix.lower() == ".rar":
        stem = first.name[:-4]
        legacy = sorted(
            [p for p in first.parent.iterdir() if p.is_file() and re.fullmatch(re.escape(stem) + r"\.r\d\d", p.name, re.I)],
            key=lambda x: x.name.lower(),
        )
        return [first, *legacy]
    return [first]


def local_stage_state(logical_path: str) -> dict[str, Any]:
    if not is_within_logical(logical_path, source_root()):
        raise RuntimeError("Archive is outside the configured DMM source root")
    actual = view_path(logical_path)
    if not actual.is_file() or actual.suffix.lower() != ".rar":
        raise RuntimeError("RAR source is not available")
    fp = _archive_fingerprint(logical_path, actual)
    volumes = _archive_volume_actual_paths(actual)
    total = sum(int(p.stat().st_size) for p in volumes if p.exists())
    storage = storage_state(total)
    cached = cache_get(_local_stage_key(fp))
    staged = str((cached or {}).get("staged_archive") or "") if isinstance(cached, dict) else ""
    return {
        "fingerprint": fp,
        "size": total,
        "volume_count": len(volumes),
        "free": int(storage.get("free") or 0),
        "required": total,
        "enough": bool(storage.get("enough")),
        "staged_archive": staged,
        "staged_available": bool(staged and view_path(staged).is_file()),
        "classification": str((cached or {}).get("classification") or "") if isinstance(cached, dict) else "",
    }


def stage_and_reverify(
    logical_path: str,
    *,
    expected_fingerprint: str = "",
    progress=None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Copy a cloud/virtual RAR to recovery storage and re-test it locally.

    A local pass upgrades only the affected member's verification eligibility;
    the original provider source is retained and never modified or deleted.
    """
    plan = inspect_archive(logical_path, force=True, cancel_check=cancel_check)
    if expected_fingerprint and plan.get("fingerprint") != expected_fingerprint:
        raise RuntimeError("RAR source changed after preview; inspect it again")
    actual = view_path(logical_path)
    volumes = _archive_volume_actual_paths(actual)
    total = sum(int(p.stat().st_size) for p in volumes if p.exists())
    space = storage_state(total)
    if not space.get("enough"):
        raise RuntimeError("Not enough free recovery storage to stage this archive locally")

    stage_root_logical = Path(extraction_root()) / ".arrnexus-staging" / str(plan["fingerprint"])[:16]
    stage_root_actual = view_path(stage_root_logical)
    stage_root_actual.mkdir(parents=True, exist_ok=True)
    copied = 0
    try:
        for src in volumes:
            if cancel_check and cancel_check():
                raise CancelledOperation("Local archive staging cancelled")
            dst = stage_root_actual / src.name
            tmp = dst.with_name(dst.name + ".partial")
            tmp.unlink(missing_ok=True)
            with src.open("rb") as rf, tmp.open("wb") as wf:
                while True:
                    if cancel_check and cancel_check():
                        raise CancelledOperation("Local archive staging cancelled")
                    chunk = rf.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    wf.write(chunk)
                    copied += len(chunk)
                    if progress:
                        try:
                            progress(copied, total, src.name, "copy")
                        except Exception:
                            pass
                wf.flush()
                os.fsync(wf.fileno())
            if tmp.stat().st_size != src.stat().st_size:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"Local staging size mismatch for {src.name}")
            tmp.replace(dst)
    except CancelledOperation:
        shutil.rmtree(stage_root_actual, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(stage_root_actual, ignore_errors=True)
        raise

    staged_first = stage_root_actual / actual.name
    if not staged_first.is_file():
        shutil.rmtree(stage_root_actual, ignore_errors=True)
        raise RuntimeError("Local staging did not produce the first RAR volume")

    kind, exe = _extractor()
    local = _verify_media_members_independently(
        kind, exe, staged_first, list(plan.get("media") or []), progress=None, cancel_check=cancel_check,
    )
    original = cache_get(_verification_key(plan["fingerprint"]))
    original_members = {
        str(x.get("path") or ""): dict(x)
        for x in ((original or {}).get("members") or [])
    } if isinstance(original, dict) else {}
    local_members = {str(x.get("path") or ""): dict(x) for x in (local.get("members") or [])}

    merged: list[dict[str, Any]] = []
    recovered_locally: list[str] = []
    still_failed: list[str] = []
    for media_row in plan.get("media") or []:
        member = str(media_row.get("path") or "")
        old = original_members.get(member) or {**media_row, "status": "untested", "error": "Not previously verified"}
        retry = local_members.get(member) or {**media_row, "status": "untested", "error": "Local verification unavailable"}
        if old.get("status") == "verified":
            chosen = {**old, "verification_source": old.get("verification_source") or "provider"}
        elif retry.get("status") == "verified":
            chosen = {**retry, "status": "verified", "error": "", "verification_source": "local_staging", "provider_status": old.get("status")}
            recovered_locally.append(member)
        else:
            chosen = {**retry, "verification_source": "local_staging", "provider_status": old.get("status")}
            if retry.get("status") == "failed" or old.get("status") == "failed":
                still_failed.append(member)
        merged.append(chosen)

    merged_result = {
        "logical_path": logical_path,
        "fingerprint": plan["fingerprint"],
        "catalogue_signature": plan["catalogue_signature"],
        "tested_at": time.time(),
        "seconds": 0,
        "members": merged,
        "verified_count": sum(1 for x in merged if x.get("status") == "verified"),
        "failed_count": sum(1 for x in merged if x.get("status") == "failed"),
        "untested_count": sum(1 for x in merged if x.get("status") == "untested"),
        "issues": list(local.get("issues") or []),
        "exit_code": int(local.get("exit_code") or 0),
        "verification_mode": "provider_plus_local_staging",
        "local_staging": True,
        "recovered_locally": recovered_locally,
        "still_failed": still_failed,
    }
    cache_set(_verification_key(plan["fingerprint"]), merged_result)
    staged_logical = stage_root_logical / actual.name
    classification = "virtual_source_read_path" if recovered_locally else "genuine_crc_or_archive_damage"
    result = {
        "ok": True,
        "source": logical_path,
        "fingerprint": plan["fingerprint"],
        "staged_archive": str(staged_logical),
        "staged_root": str(stage_root_logical),
        "size": total,
        "volume_count": len(volumes),
        "classification": classification,
        "recovered_locally": recovered_locally,
        "still_failed": still_failed,
        "verification": merged_result,
        "source_retained": True,
    }
    cache_set(_local_stage_key(plan["fingerprint"]), result)
    log_event("info" if recovered_locally else "warning", "archive_recovery", "local_stage_reverify", f"Local staging re-test completed for {Path(logical_path).name}", {
        "classification": classification, "recovered_locally": len(recovered_locally), "still_failed": len(still_failed), "size": total,
    })
    return result

def _target_logical(logical_path: str, fingerprint: str, identity: dict[str, Any] | None) -> Path:
    title = (identity or {}).get("title") or Path(logical_path).stem
    clean = re.sub(r'[\\/:*?"<>|]+', " ", str(title)).strip().rstrip(".") or "Recovered Media"
    token = fingerprint[:12]
    return Path(extraction_root()) / f"{clean} [{token}]"


def storage_state(required_bytes: int = 0) -> dict[str, Any]:
    root = Path(extraction_root())
    if not is_within_logical(root, dumb_root()):
        raise RuntimeError("Configured recovered media source root is outside the DUMB-visible filesystem")
    actual = view_path(root)
    actual.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(actual)
    return {"root": str(root), "free": usage.free, "total": usage.total, "required": int(required_bytes or 0), "enough": usage.free > int(required_bytes or 0) + 512 * 1024**2}


def _post_extract_rename(actual_root: Path, identity: dict[str, Any] | None) -> list[dict[str, str]]:
    if not identity:
        return []
    changes: list[dict[str, str]] = []
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
        file.rename(target)
        changes.append({"from": file.name, "to": target.name})
    return changes


def _ffprobe_media(path: Path, cancel_check: Callable[[], bool] | None = None) -> tuple[bool, str]:
    exe = shutil.which("ffprobe")
    if not exe:
        return False, "ffprobe is not installed"
    try:
        proc = run_cancellable(
            [exe, "-v", "error", "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120, check=False, cancel_check=cancel_check,
        )
    except subprocess.TimeoutExpired:
        return False, "ffprobe timed out"
    if proc.returncode != 0:
        return False, "ffprobe could not read the recovered file"
    try:
        payload = json.loads(proc.stdout or "{}")
    except Exception:
        return False, "ffprobe returned invalid metadata"
    if not any(str(x.get("codec_type") or "") == "video" for x in payload.get("streams") or []):
        return False, "No video stream was found"
    return True, ""


def _unique_target(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for n in range(2, 1000):
        candidate = path.with_name(f"{stem} (recovered {n}){suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not choose a unique recovered filename for {path.name}")


def extract_archive(logical_path: str, *, expected_fingerprint: str = "", selected_media: list[str] | None = None, cancel_check: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Extract only explicitly verified video members.

    Archive-level exit codes are not used as the final success criterion for a
    partial recovery.  Every produced file must match the listed size (when
    available) and pass ffprobe before it is committed to persistent recovery
    storage.
    """
    plan = inspect_archive(logical_path, force=True, cancel_check=cancel_check)
    if expected_fingerprint and plan["fingerprint"] != expected_fingerprint:
        raise RuntimeError("RAR source size/path changed after preview; scan/inspect it again")
    if plan.get("password_protected"):
        raise RuntimeError("Password-protected RAR requires manual review")
    if not plan.get("safe"):
        raise RuntimeError("RAR contains unsafe paths and will not be extracted")
    if plan.get("classification") != "media":
        raise RuntimeError("RAR does not directly contain recognised video media. Nested/non-media archives are not recursively unpacked.")
    if plan.get("identity_required") and not plan.get("identity"):
        raise RuntimeError("This archive name is ambiguous. Resolve its movie/TV identity before recovery so recovered media is named safely.")

    verification = cache_get(_verification_key(plan["fingerprint"]))
    if not isinstance(verification, dict) or verification.get("fingerprint") != plan["fingerprint"]:
        raise RuntimeError("Verify the archive's media files before recovery")
    if verification.get("catalogue_signature") != plan.get("catalogue_signature"):
        raise RuntimeError("RAR media catalogue changed since verification; inspect and verify it again")
    verified_rows = {str(x.get("path") or ""): x for x in verification.get("members") or [] if x.get("status") == "verified"}
    if not verified_rows:
        raise RuntimeError("No independently verified media files are available for recovery")

    requested = [str(x) for x in (selected_media or []) if str(x)]
    if not requested:
        requested = list(verified_rows)
    unknown = [x for x in requested if x not in verified_rows]
    if unknown:
        raise RuntimeError("Recovery selection contains unverified or failed media: " + ", ".join(unknown[:5]))

    required = sum(max(0, int(verified_rows[x].get("size") or 0)) for x in requested)
    if required and required > max_extract_bytes():
        raise RuntimeError(f"Selected media exceeds the configured {max_extract_bytes()/1024**3:.0f} GB recovery safety limit")
    space = storage_state(required)
    if not space["enough"]:
        raise RuntimeError("Not enough free space in the recovered media source root")

    actual_archive = view_path(logical_path)
    local_stage = cache_get(_local_stage_key(plan["fingerprint"]))
    if any(str(verified_rows[x].get("verification_source") or "") == "local_staging" for x in requested):
        staged_logical = str((local_stage or {}).get("staged_archive") or "") if isinstance(local_stage, dict) else ""
        staged_actual = view_path(staged_logical) if staged_logical else None
        if not staged_actual or not staged_actual.is_file():
            raise RuntimeError("Locally staged verification is no longer available; retry local staging before recovery")
        actual_archive = staged_actual
    identity = plan.get("identity")
    target_logical = _target_logical(logical_path, plan["fingerprint"], identity)
    target_actual = view_path(target_logical)
    partial = target_actual.with_name(target_actual.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True, exist_ok=True)

    kind, exe = _extractor()
    if kind == "7z":
        cmd = [exe, "x", "-y", "-spd", f"-o{partial}", str(actual_archive)] + requested
    else:
        cmd = [exe, "x", "-o+", str(actual_archive)] + requested + [str(partial) + os.sep]
    try:
        proc = run_cancellable(cmd, capture_output=True, text=True, timeout=60 * 60 * 6, check=False, cancel_check=cancel_check)
    except CancelledOperation:
        # Only the operation-local staging directory is removed. Provider RARs,
        # committed recovered files and existing library symlinks are untouched.
        shutil.rmtree(partial, ignore_errors=True)
        raise
    extract_issues = _archive_issue_lines(proc.stdout or "", proc.stderr or "")

    resolved = partial.resolve()
    for p in partial.rglob("*"):
        if p.is_symlink():
            shutil.rmtree(partial, ignore_errors=True)
            raise RuntimeError("RAR created a symlink; recovery was discarded")
        try:
            p.resolve().relative_to(resolved)
        except ValueError:
            shutil.rmtree(partial, ignore_errors=True)
            raise RuntimeError("RAR attempted path traversal; recovery was discarded")

    # Validate exactly the requested media outputs.  A global 7-Zip exit 2 can
    # coexist with good independent members, so commit only members that prove
    # themselves here.
    good_files: list[Path] = []
    failed_after_extract: list[dict[str, str]] = []
    for member in requested:
        row = verified_rows[member]
        candidate = partial.joinpath(*Path(_normal_member(member)).parts)
        if not candidate.is_file():
            failed_after_extract.append({"path": member, "error": "7-Zip did not produce this file"})
            continue
        expected_size = int(row.get("size") or 0)
        if expected_size and candidate.stat().st_size != expected_size:
            failed_after_extract.append({"path": member, "error": f"Recovered size {candidate.stat().st_size} does not match expected {expected_size}"})
            candidate.unlink(missing_ok=True)
            continue
        ok, reason = _ffprobe_media(candidate, cancel_check=cancel_check)
        if not ok:
            failed_after_extract.append({"path": member, "error": reason})
            candidate.unlink(missing_ok=True)
            continue
        good_files.append(candidate)

    # Never persist transport/support members even if a future extractor emits
    # extras unexpectedly.
    for p in list(partial.rglob("*")):
        if p.is_file() and p not in good_files:
            p.unlink(missing_ok=True)

    if not good_files:
        shutil.rmtree(partial, ignore_errors=True)
        detail = "; ".join(x["error"] for x in failed_after_extract[:3]) or "; ".join(extract_issues[:3]) or f"7-Zip exit {proc.returncode}"
        raise RuntimeError("RAR recovery produced no valid media files: " + detail)

    renamed = _post_extract_rename(partial, identity)
    good_files = [p for p in partial.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS]
    target_actual.mkdir(parents=True, exist_ok=True)
    committed: list[str] = []
    for file in good_files:
        rel = file.relative_to(partial)
        dest = target_actual / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.stat().st_size == file.stat().st_size:
            file.unlink(missing_ok=True)
            committed.append(str(dest.relative_to(target_actual)))
            continue
        dest = _unique_target(dest)
        shutil.move(str(file), str(dest))
        committed.append(str(dest.relative_to(target_actual)))
    shutil.rmtree(partial, ignore_errors=True)

    item = inspect_item(target_logical)
    # Recovered media is a managed ArrNexus source pack. Carry the archive's
    # resolved identity onto the recovered folder's own fingerprint so the DMM
    # Inbox can merge Season 1/2/3 recovery packs with existing provider packs
    # for the same series instead of displaying separate hash-suffixed cards.
    if identity:
        media_identity.save_identity(str(target_logical), item.fingerprint, identity)
    total_videos = len([p for p in target_actual.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS])
    log_event("info", "archive_recovery", "extracted", f"Recovered media from {Path(logical_path).name}", {
        "target": str(target_logical), "recovered": len(committed), "videos": total_videos, "skipped": len(failed_after_extract), "7z_exit": proc.returncode,
    })
    add_activity("archive_recovery", (identity or {}).get("title") or Path(logical_path).stem, f"Recovered {len(committed)} verified media file(s)", str(target_logical))
    result = {
        "ok": True,
        "source": logical_path,
        "target": str(target_logical),
        "videos": total_videos,
        "recovered": len(committed),
        "committed": committed,
        "failed_after_extract": failed_after_extract,
        "archive_exit_code": int(proc.returncode),
        "archive_issues": extract_issues,
        "renamed": renamed,
        "item": item.dict(),
        "identity": identity,
        "source_retained": True,
        "media_only": True,
    }
    cache_set(f"archive_recovery:extracted:{plan['fingerprint']}", result)
    return result
