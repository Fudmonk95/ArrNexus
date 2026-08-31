from __future__ import annotations

"""Internet Archive rescue workflow for hard-to-find Sonarr and Radarr media.

Prowlarr remains the search adapter. ArrNexus adds the orchestration Prowlarr
intentionally does not provide: scan monitored missing TV/movie items, isolate
Internet Archive results, inspect the returned .torrent, and hand selected
files to Real-Debrid.
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from .arr import ProwlarrClient
from .db import cache_get, cache_set, log_event, add_activity
from .instances import discover_instances
from .router_service import client_for_instance
from . import realdebrid as rd
from . import rescue as arr_rescue

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".m2ts", ".webm"}


def _release_token(row: dict[str, Any]) -> str:
    raw = str(row.get("downloadUrl") or row.get("magnetUrl") or row.get("guid") or row.get("title") or "")
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:32]


async def internet_archive_indexers() -> list[dict[str, Any]]:
    rows = await ProwlarrClient().indexers()
    out = []
    for row in rows or []:
        haystack = " ".join(str(row.get(k) or "") for k in ("name", "implementation", "implementationName", "definitionName", "configContract")).lower()
        if "internet archive" in haystack or "internetarchive" in haystack:
            out.append(dict(row))
    return out


async def scan_missing_sonarr() -> list[dict[str, Any]]:
    return await arr_rescue.scan_missing_sonarr()


async def scan_missing_radarr() -> list[dict[str, Any]]:
    return await arr_rescue.scan_missing_radarr()


def _is_archive_result(row: dict[str, Any], ids: set[int], names: set[str]) -> bool:
    try:
        if int(row.get("indexerId") or 0) in ids:
            return True
    except Exception:
        pass
    name = str(row.get("indexer") or row.get("indexerName") or "").strip().lower()
    return bool(name and (name in names or "internet archive" in name or "internetarchive" in name))


async def search_archive(query: str, limit: int = 60) -> list[dict[str, Any]]:
    query = str(query or "").strip()
    if not query:
        return []
    prowlarr = ProwlarrClient()
    indexers = await internet_archive_indexers()
    ids = {int(x.get("id") or 0) for x in indexers if x.get("id")}
    names = {str(x.get("name") or "").strip().lower() for x in indexers if x.get("name")}
    if not ids and not names:
        raise RuntimeError("Internet Archive is not configured/enabled in Prowlarr")
    rows = await prowlarr.search(query, limit=max(20, min(200, int(limit))))
    out = []
    for raw in rows or []:
        row = dict(raw)
        if not _is_archive_result(row, ids, names):
            continue
        token = _release_token(row)
        cache_set(f"archive_rescue:release:{token}", row)
        out.append({
            "token": token,
            "title": row.get("title") or row.get("guid") or "Internet Archive result",
            "indexer": row.get("indexer") or row.get("indexerName") or "Internet Archive",
            "size": int(row.get("size") or 0),
            "downloads": int(row.get("grabs") or row.get("downloads") or 0),
            "seeders": row.get("seeders"),
            "protocol": row.get("protocol") or "torrent",
            "download_url": row.get("downloadUrl") or "",
            "guid": row.get("guid") or "",
        })
    return out


async def search_missing_archive(limit: int = 20, service: str = "sonarr") -> list[dict[str, Any]]:
    service = str(service or "sonarr").lower()
    if service not in {"sonarr", "radarr"}:
        raise ValueError("Archive Rescue supports Sonarr or Radarr")
    missing = (await arr_rescue.scan_missing(service))[:max(1, min(50, int(limit)))]
    sem = asyncio.Semaphore(4)

    async def one(row):
        if row.get("error"):
            return row
        async with sem:
            try:
                query = str(row.get("title") or "")
                if service == "radarr" and row.get("year"):
                    query = f"{query} {row['year']}"
                results = await search_archive(query, 40)
                return {**row, "archive_results": results[:5], "archive_count": len(results)}
            except Exception as exc:
                return {**row, "archive_results": [], "archive_count": 0, "archive_error": str(exc)}

    return list(await asyncio.gather(*(one(row) for row in missing)))


class BencodeError(ValueError):
    pass


def _bdecode(data: bytes) -> Any:
    """Small strict bencode decoder sufficient for torrent manifests."""
    pos = 0
    n = len(data)
    def parse():
        nonlocal pos
        if pos >= n:
            raise BencodeError("Unexpected end of torrent metadata")
        c = data[pos:pos+1]
        if c == b"i":
            pos += 1; end = data.find(b"e", pos)
            if end < 0: raise BencodeError("Invalid integer")
            raw = data[pos:end]; pos = end + 1
            return int(raw)
        if c == b"l":
            pos += 1; out = []
            while data[pos:pos+1] != b"e": out.append(parse())
            pos += 1; return out
        if c == b"d":
            pos += 1; out = {}
            while data[pos:pos+1] != b"e":
                key = parse()
                if not isinstance(key, bytes): raise BencodeError("Dictionary key is not bytes")
                out[key] = parse()
            pos += 1; return out
        if c.isdigit():
            colon = data.find(b":", pos)
            if colon < 0: raise BencodeError("Invalid byte string")
            length = int(data[pos:colon]); pos = colon + 1
            end = pos + length
            if end > n: raise BencodeError("Byte string exceeds payload")
            value = data[pos:end]; pos = end; return value
        raise BencodeError(f"Unsupported bencode token at {pos}")
    value = parse()
    return value


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def torrent_files(payload: bytes) -> list[dict[str, Any]]:
    meta = _bdecode(payload)
    if not isinstance(meta, dict) or b"info" not in meta or not isinstance(meta[b"info"], dict):
        raise BencodeError("Torrent metadata has no info dictionary")
    info = meta[b"info"]
    root_name = _text(info.get(b"name") or "")
    rows = []
    files = info.get(b"files")
    if isinstance(files, list):
        for idx, entry in enumerate(files, start=1):
            if not isinstance(entry, dict):
                continue
            parts = [_text(x) for x in (entry.get(b"path") or [])]
            path = "/".join([root_name, *parts]) if root_name else "/".join(parts)
            rows.append({"index": idx, "path": path, "name": parts[-1] if parts else path, "size": int(entry.get(b"length") or 0), "video": Path(path).suffix.lower() in VIDEO_EXTS})
    else:
        rows.append({"index": 1, "path": root_name, "name": root_name, "size": int(info.get(b"length") or 0), "video": Path(root_name).suffix.lower() in VIDEO_EXTS})
    return rows


async def release_manifest(token: str) -> dict[str, Any]:
    row = cache_get(f"archive_rescue:release:{token}")
    if not isinstance(row, dict):
        raise RuntimeError("Archive Rescue result expired; search again")
    download_url = str(row.get("downloadUrl") or "")
    if not download_url:
        raise RuntimeError("Prowlarr result does not expose a .torrent download URL")
    fetched = await ProwlarrClient().download_release(download_url)
    if fetched.get("magnet"):
        raise RuntimeError("Internet Archive returned a magnet. Enable 'Download using .torrent only. No Magnets.' on the Prowlarr Internet Archive indexer for file-level rescue.")
    payload = bytes(fetched.get("content") or b"")
    if not payload:
        raise RuntimeError("Prowlarr returned an empty torrent payload")
    files = torrent_files(payload)
    return {"token": token, "release": row, "files": files, "video_count": sum(1 for f in files if f["video"]), "size": sum(int(f["size"]) for f in files)}


def _norm_path(value: str) -> str:
    return "/".join(x for x in str(value or "").replace("\\", "/").strip("/").split("/") if x).casefold()


async def send_release_to_realdebrid(token: str, selected_paths: list[str]) -> dict[str, Any]:
    if not rd.connected():
        raise RuntimeError("Real-Debrid is not connected in ArrNexus")
    row = cache_get(f"archive_rescue:release:{token}")
    if not isinstance(row, dict):
        raise RuntimeError("Archive Rescue result expired; search again")
    fetched = await ProwlarrClient().download_release(str(row.get("downloadUrl") or ""))
    if fetched.get("magnet"):
        added = await rd.add_magnet(str(fetched["magnet"]))
    else:
        payload = bytes(fetched.get("content") or b"")
        if not payload:
            raise RuntimeError("Prowlarr returned an empty torrent payload")
        added = await rd.add_torrent_file(payload)
    torrent_id = str((added or {}).get("id") or "")
    if not torrent_id:
        raise RuntimeError("Real-Debrid did not return a torrent ID")

    info = await rd.torrent_info(torrent_id)
    rd_files = list((info or {}).get("files") or [])
    wanted = {_norm_path(x) for x in selected_paths if str(x).strip()}
    ids = []
    if wanted:
        for f in rd_files:
            path = _norm_path(f.get("path") or f.get("filename") or "")
            if path in wanted or any(path.endswith("/" + w) or w.endswith("/" + path) for w in wanted if w and path):
                if f.get("id") is not None:
                    ids.append(str(f["id"]))
    if wanted and not ids:
        await rd.delete_torrent(torrent_id)
        raise RuntimeError("Selected Archive files could not be matched to Real-Debrid file IDs; the newly-added RD torrent was removed")
    if ids:
        await rd.select_files(torrent_id, ids)
    else:
        await rd.select_all(torrent_id)
    title = str(row.get("title") or "Internet Archive release")
    log_event("info", "archive_rescue", "sent_to_realdebrid", f"{title} sent to Real-Debrid", {"torrent_id": torrent_id, "selected_files": len(ids) or "all"})
    add_activity("archive_rescue", title, f"Sent Internet Archive torrent to Real-Debrid ({len(ids) or 'all'} files)")
    return {"ok": True, "torrent_id": torrent_id, "selected_count": len(ids), "status": (info or {}).get("status"), "title": title}
