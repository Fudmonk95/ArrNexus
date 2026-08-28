from __future__ import annotations

"""Safe duplicate-symlink consolidation for ArrNexus v10.1.

The scanner walks the managed movie/TV library and groups only symlinks that
represent the same movie part or the same TV episode.  It never removes a
backing provider object during preview.  Apply removes only redundant symlinks;
optional Real-Debrid cleanup is limited to source folders that became
unreferenced because of that exact apply operation and can be mapped to one
exact RD torrent ID.
"""

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import math
import os
import re
from typing import Any

from .importer import all_library_roots
from .library import build_source_link_index, invalidate_library_cache
from .namespace import view_path, logical_from_view
from .paths import source_root
from .scanner import inspect_item, quality_from_name, video_files, invalidate_scan_cache
from .language_guard import inspect_source_languages, result_badge
from . import realdebrid as rd


_EP_TOKEN = re.compile(r"(?i)(S\d{1,2}E\d{1,3}(?:E\d{1,3})*|\d{1,2}x\d{1,3})")
_PART_TOKEN = re.compile(r"(?i)(?:\bpart[ ._-]?(\d{1,2})\b|\bcd[ ._-]?(\d{1,2})\b)")


def _source_for_target(target: str) -> str:
    prefix = source_root().rstrip("/") + "/"
    if not str(target).startswith(prefix):
        return ""
    rel = str(target)[len(prefix):]
    folder = rel.split("/", 1)[0]
    return prefix.rstrip("/") + "/" + folder if folder else ""


def _movie_key(root_key: str, logical_root: str, symlink_logical: str) -> str:
    rel = Path(symlink_logical).relative_to(Path(logical_root))
    parts = rel.parts
    movie_dir = parts[0].casefold() if parts else Path(symlink_logical).parent.name.casefold()
    m = _PART_TOKEN.search(Path(symlink_logical).stem)
    part = next((x for x in (m.groups() if m else ()) if x), "whole")
    return f"{root_key}:movie:{movie_dir}:part:{part}"


def _tv_key(root_key: str, logical_root: str, symlink_logical: str) -> str:
    rel = Path(symlink_logical).relative_to(Path(logical_root))
    series = rel.parts[0].casefold() if rel.parts else "series"
    m = _EP_TOKEN.search(Path(symlink_logical).name)
    if not m:
        # Do not consolidate files whose episode identity is uncertain.
        return ""
    return f"{root_key}:tv:{series}:{m.group(1).upper()}"


def _source_quality_rank(name: str) -> tuple[int, str]:
    low = name.lower()
    for rank, label, needles in (
        (6, "Remux", ("remux",)),
        (5, "BluRay", ("bluray", "blu-ray")),
        (4, "WEB-DL", ("web-dl", "webdl")),
        (3, "WEBRip", ("webrip",)),
        (2, "HDTV", ("hdtv",)),
        (1, "Other", ()),
    ):
        if not needles or any(n in low for n in needles):
            return rank, label
    return 1, "Other"


def _codec_rank(name: str) -> tuple[int, str]:
    low = name.lower()
    if "av1" in low:
        return 3, "AV1"
    if any(x in low for x in ("x265", "h265", "hevc")):
        return 2, "HEVC/x265"
    if any(x in low for x in ("x264", "h264", "avc")):
        return 1, "H.264/x264"
    return 0, "Unknown codec"


def _audio_rank(name: str) -> tuple[int, str]:
    low = name.lower()
    if "atmos" in low or "truehd" in low:
        return 4, "TrueHD/Atmos"
    if "dts-hd" in low or "dtshd" in low:
        return 3, "DTS-HD"
    if "dts" in low:
        return 2, "DTS"
    if any(x in low for x in ("ddp", "eac3", "ac3")):
        return 1, "DD/DD+"
    return 0, "Unknown audio"


def _candidate_score(candidate: dict[str, Any]) -> tuple[int, list[str]]:
    lang = str(candidate.get("language_key") or "unchecked")
    # Eligibility dominates every raw quality field. A verified English source
    # should beat a higher-resolution source that failed the language policy.
    lang_rank = {"pass": 5, "disabled": 4, "unchecked": 3, "unknown": 2, "probe_failed": 1, "fail": 0}.get(lang, 2)
    resolution = int(candidate.get("resolution") or 0)
    source_rank = int(candidate.get("source_rank") or 0)
    codec_rank = int(candidate.get("codec_rank") or 0)
    audio_rank = int(candidate.get("audio_rank") or 0)
    hdr = 1 if candidate.get("hdr") else 0
    size_gb = float(candidate.get("size_bytes") or 0) / (1024 ** 3)

    score = (
        lang_rank * 10_000_000
        + resolution * 10_000
        + source_rank * 100_000
        + hdr * 50_000
        + codec_rank * 20_000
        + audio_rank * 10_000
        + int(min(size_gb, 250.0) * 100)
    )
    reasons = [candidate.get("language_label") or "Language unchecked"]
    if resolution:
        reasons.append(f"{resolution}p")
    reasons.append(str(candidate.get("source_label") or "Other source"))
    if candidate.get("hdr"):
        reasons.append("HDR/Dolby Vision")
    if candidate.get("codec_label"):
        reasons.append(str(candidate["codec_label"]))
    if candidate.get("audio_label"):
        reasons.append(str(candidate["audio_label"]))
    reasons.append(f"{size_gb:.1f} GB")
    return score, reasons


def _build_candidate(root_key: str, logical_root: str, p: Path) -> dict[str, Any] | None:
    try:
        target = os.readlink(p)
    except OSError:
        return None
    source = _source_for_target(target)
    if not source:
        return None
    logical_link = str(logical_from_view(p))
    try:
        source_item = inspect_item(source)
        language = inspect_source_languages(source, source_item.fingerprint, False)
    except Exception as exc:
        source_item = None
        language = {"status": "unknown", "summary": f"Language inspection unavailable: {exc}"}
    language_key, language_label = result_badge(language)
    combined_name = f"{Path(source).name} {Path(target).name} {p.name}"
    source_rank, source_label = _source_quality_rank(combined_name)
    codec_rank, codec_label = _codec_rank(combined_name)
    audio_rank, audio_label = _audio_rank(combined_name)
    resolution = max(
        quality_from_name(combined_name),
        int(source_item.quality or 0) if source_item else 0,
    )
    try:
        size_bytes = int(view_path(target).stat().st_size)
    except Exception:
        size_bytes = 0
    low = combined_name.lower()
    candidate = {
        "root": root_key,
        "link": logical_link,
        "target": target,
        "source": source,
        "source_name": Path(source).name,
        "resolution": resolution,
        "size_bytes": size_bytes,
        "language_key": language_key,
        "language_label": language_label,
        "language_summary": language.get("summary") or language_label,
        "source_rank": source_rank,
        "source_label": source_label,
        "codec_rank": codec_rank,
        "codec_label": codec_label,
        "audio_rank": audio_rank,
        "audio_label": audio_label,
        "hdr": any(x in low for x in (" hdr", ".hdr", "hdr10", "dolby vision", " dovi", ".dv.")),
    }
    candidate["score"], candidate["reasons"] = _candidate_score(candidate)
    return candidate


def _digest(groups: list[dict[str, Any]]) -> str:
    payload = [
        {
            "key": g["key"],
            "keep": g["keep"]["link"],
            "remove": sorted(x["link"] for x in g["remove"]),
        }
        for g in groups
    ]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def scan_consolidation() -> dict[str, Any]:
    """Scan every managed movie/TV symlink and score only duplicate groups."""
    raw_groups: dict[str, list[tuple[str, str, Path]]] = {}
    symlink_count = 0
    for root_key, logical_root in all_library_roots().items():
        if not (root_key.startswith("radarr:") or root_key.startswith("sonarr:")):
            continue
        try:
            actual_root = view_path(logical_root)
            if not actual_root.exists():
                continue
            for p in actual_root.rglob("*"):
                if not p.is_symlink():
                    continue
                symlink_count += 1
                logical_link = str(logical_from_view(p))
                key = _movie_key(root_key, logical_root, logical_link) if root_key.startswith("radarr:") else _tv_key(root_key, logical_root, logical_link)
                if key:
                    raw_groups.setdefault(key, []).append((root_key, logical_root, p))
        except (OSError, PermissionError):
            continue

    duplicate_raw = {k: v for k, v in raw_groups.items() if len(v) > 1}
    groups: list[dict[str, Any]] = []
    for key, rows in duplicate_raw.items():
        candidates = [c for c in (_build_candidate(root_key, logical_root, p) for root_key, logical_root, p in rows) if c]
        if len(candidates) < 2:
            continue
        candidates.sort(key=lambda c: (int(c["score"]), int(c["size_bytes"])), reverse=True)
        keep = candidates[0]
        groups.append({
            "key": key,
            "kind": "movie" if ":movie:" in key else "episode",
            "keep": keep,
            "remove": candidates[1:],
            "candidates": candidates,
        })

    groups.sort(key=lambda g: g["key"])
    removals = sum(len(g["remove"]) for g in groups)
    return {
        "symlinks_scanned": symlink_count,
        "duplicate_groups": len(groups),
        "recommended_removals": removals,
        "groups": groups,
        "digest": _digest(groups),
    }




def _arr_owner(candidate: dict[str, Any]) -> tuple[str, str, str]:
    root_key = str(candidate.get("root") or "")
    if ":" not in root_key:
        return "", "", ""
    service, destination = root_key.split(":", 1)
    logical_root = all_library_roots().get(root_key) or ""
    if not logical_root:
        return service, destination, ""
    try:
        rel = Path(str(candidate.get("link") or "")).relative_to(Path(logical_root))
    except Exception:
        return service, destination, ""
    if not rel.parts:
        return service, destination, ""
    # Radarr item path is the movie directory; Sonarr item path is the series directory.
    return service, destination, str(Path(logical_root) / rel.parts[0])


async def _rescan_affected(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from .router_service import client_for_destination
    affected = sorted({_arr_owner(g.get("keep") or {}) for g in groups})
    affected = [x for x in affected if x[0] in {"radarr", "sonarr"} and x[2]]
    results: list[dict[str, Any]] = []
    for service, destination, logical_path in affected:
        try:
            client, _inst = client_for_destination(service, destination)
            rows = await (client.movies() if service == "radarr" else client.series())
            norm = logical_path.rstrip("/").casefold()
            match = next((r for r in (rows or []) if str(r.get("path") or "").rstrip("/").casefold() == norm), None)
            if not match or not match.get("id"):
                results.append({"service": service, "destination": destination, "path": logical_path, "ok": False, "reason": "Owning Arr item not found by exact library path"})
                continue
            await client.rescan(int(match["id"]))
            results.append({"service": service, "destination": destination, "path": logical_path, "ok": True, "arr_id": int(match["id"])})
        except Exception as exc:
            results.append({"service": service, "destination": destination, "path": logical_path, "ok": False, "reason": str(exc)})
    return results

def apply_consolidation(expected_digest: str, remove_provider_sources: bool = False) -> dict[str, Any]:
    current = scan_consolidation()
    if not expected_digest or current.get("digest") != expected_digest:
        raise RuntimeError("Library changed after the consolidation preview. Run a fresh preview before applying cleanup.")

    removed_links: list[str] = []
    errors: list[str] = []
    candidate_sources: set[str] = set()
    for group in current.get("groups") or []:
        for candidate in group.get("remove") or []:
            logical = str(candidate.get("link") or "")
            source = str(candidate.get("source") or "")
            if source:
                candidate_sources.add(source)
            try:
                actual = view_path(logical)
                if not actual.is_symlink():
                    raise RuntimeError("path is no longer a symlink")
                current_target = os.readlink(actual)
                if current_target != candidate.get("target"):
                    raise RuntimeError("symlink target changed since preview")
                actual.unlink()
                removed_links.append(logical)
            except Exception as exc:
                errors.append(f"{logical}: {exc}")

    invalidate_library_cache()
    invalidate_scan_cache()
    # Ask the owning Arr instances to reconcile their file state after link
    # removal. Failure is reported but does not cause provider deletion to
    # broaden or become destructive.
    try:
        rescan_results = __import__("asyncio").run(_rescan_affected(current.get("groups") or []))
    except Exception as exc:
        rescan_results = [{"ok": False, "reason": str(exc)}]
    links_after = build_source_link_index(force=True)
    orphaned = sorted(source for source in candidate_sources if not links_after.get(source))

    provider_results: list[dict[str, Any]] = []
    if remove_provider_sources:
        # Only sources made unreferenced by this exact operation are eligible.
        for source in orphaned:
            try:
                item = inspect_item(source)
                result = __import__("asyncio").run(rd.delete_source_torrent_exact(source, item.size_bytes))
            except Exception as exc:
                result = {"ok": False, "deleted": False, "reason": str(exc)}
            provider_results.append({"source": source, **result})
        invalidate_scan_cache()

    return {
        "removed_links": removed_links,
        "removed_count": len(removed_links),
        "errors": errors,
        "orphaned_sources": orphaned,
        "provider_cleanup": provider_results,
        "arr_rescans": rescan_results,
    }
