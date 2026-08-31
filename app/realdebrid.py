from __future__ import annotations
import time
import httpx
from .db import setting_get, setting_set, setting_delete

MASTER_CLIENT_ID = "X245A4XAIBGVM"  # Official Real-Debrid opensource-app client ID.
OAUTH_BASE = "https://api.real-debrid.com/oauth/v2"
API_BASE = "https://api.real-debrid.com/rest/1.0"
DEVICE_GRANT = "http://oauth.net/grant_type/device/1.0"

class RealDebridError(RuntimeError):
    pass


def _manual_api_token() -> str:
    # Reuse an existing ArrNexus provider/AIOStreams Real-Debrid token when the
    # operator has not completed the dedicated OAuth flow.  Real-Debrid accepts
    # private API tokens as bearer credentials; the value is never logged.
    for key in ("realdebrid.api_key", "realdebrid.token", "aiostreams.realdebrid.api_key"):
        value = str(setting_get(key, "") or "").strip()
        if value:
            return value
    return ""


def connected() -> bool:
    return bool(setting_get("rd.refresh_token") or setting_get("rd.access_token") or _manual_api_token())

async def begin_oauth() -> dict:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        r = await client.get(f"{OAUTH_BASE}/device/code", params={"client_id": MASTER_CLIENT_ID, "new_credentials": "yes"})
    if r.status_code >= 400:
        raise RealDebridError(f"Real-Debrid OAuth: {r.status_code} {r.text[:500]}")
    data = r.json()
    setting_set("rd.pending_device_code", data.get("device_code", ""), True)
    setting_set("rd.pending_user_code", data.get("user_code", ""), True)
    setting_set("rd.pending_verification_url", data.get("verification_url", "https://real-debrid.com/device"), False)
    setting_set("rd.pending_expires_at", str(time.time() + int(data.get("expires_in") or 1800)), False)
    return data

async def poll_oauth() -> dict:
    device_code = setting_get("rd.pending_device_code")
    if not device_code:
        return {"status": "none"}
    try:
        expires = float(setting_get("rd.pending_expires_at", "0") or 0)
    except Exception:
        expires = 0
    if expires and time.time() > expires:
        clear_pending()
        return {"status": "expired"}

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        r = await client.get(f"{OAUTH_BASE}/device/credentials", params={"client_id": MASTER_CLIENT_ID, "code": device_code})
    if r.status_code >= 400:
        # Real-Debrid returns an error until the user authorises the device.
        return {"status": "waiting"}
    creds = r.json()
    cid, secret = creds.get("client_id"), creds.get("client_secret")
    if not cid or not secret:
        return {"status": "waiting"}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        token = await client.post(f"{OAUTH_BASE}/token", data={
            "client_id": cid,
            "client_secret": secret,
            "code": device_code,
            "grant_type": DEVICE_GRANT,
        })
    if token.status_code >= 400:
        raise RealDebridError(f"Real-Debrid token exchange: {token.status_code} {token.text[:500]}")
    t = token.json()
    setting_set("rd.client_id", cid, True)
    setting_set("rd.client_secret", secret, True)
    setting_set("rd.access_token", t.get("access_token", ""), True)
    setting_set("rd.refresh_token", t.get("refresh_token", ""), True)
    setting_set("rd.expires_at", str(time.time() + max(60, int(t.get("expires_in") or 3600) - 60)), False)
    clear_pending()
    return {"status": "connected"}


def clear_pending():
    for key in ("rd.pending_device_code", "rd.pending_user_code", "rd.pending_verification_url", "rd.pending_expires_at"):
        setting_delete(key)


def disconnect():
    for key in ("rd.client_id", "rd.client_secret", "rd.access_token", "rd.refresh_token", "rd.expires_at"):
        setting_delete(key)
    clear_pending()

async def _access_token() -> str:
    token = setting_get("rd.access_token")
    refresh = setting_get("rd.refresh_token")
    try:
        expires = float(setting_get("rd.expires_at", "0") or 0)
    except Exception:
        expires = 0
    if token and (not expires or expires > time.time()):
        return token
    if not refresh:
        manual = _manual_api_token()
        if manual:
            return manual
        raise RealDebridError("Real-Debrid is not connected")
    cid, secret = setting_get("rd.client_id"), setting_get("rd.client_secret")
    if not cid or not secret:
        raise RealDebridError("Real-Debrid OAuth credentials are incomplete")
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        r = await client.post(f"{OAUTH_BASE}/token", data={
            "client_id": cid,
            "client_secret": secret,
            "code": refresh,
            "grant_type": DEVICE_GRANT,
        })
    if r.status_code >= 400:
        raise RealDebridError(f"Real-Debrid refresh failed: {r.status_code} {r.text[:300]}")
    t = r.json()
    setting_set("rd.access_token", t.get("access_token", ""), True)
    if t.get("refresh_token"):
        setting_set("rd.refresh_token", t["refresh_token"], True)
    setting_set("rd.expires_at", str(time.time() + max(60, int(t.get("expires_in") or 3600) - 60)), False)
    return t.get("access_token", "")

async def request(method: str, path: str, **kwargs):
    token = await _access_token()
    headers = dict(kwargs.pop("headers", {}) or {})
    headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        r = await client.request(method, f"{API_BASE}/{path.lstrip('/')}", headers=headers, **kwargs)
    if r.status_code >= 400:
        raise RealDebridError(f"Real-Debrid: {r.status_code} {r.text[:700]}")
    if not r.content:
        return None
    try:
        return r.json()
    except Exception:
        return r.text

async def user() -> dict:
    return await request("GET", "user")

async def torrents(limit: int = 250) -> list[dict]:
    out = []
    page = 1
    while len(out) < limit:
        rows = await request("GET", "torrents", params={"page": page, "limit": min(100, limit-len(out))})
        rows = rows or []
        if not rows:
            break
        out.extend(rows)
        if len(rows) < 100:
            break
        page += 1
    return out[:limit]

async def add_magnet(magnet: str) -> dict:
    return await request("POST", "torrents/addMagnet", data={"magnet": magnet})

async def add_torrent_file(payload: bytes) -> dict:
    if not payload:
        raise RealDebridError("Torrent payload is empty")
    return await request("PUT", "torrents/addTorrent", content=payload, headers={"Content-Type": "application/x-bittorrent"})

async def select_all(torrent_id: str):
    return await request("POST", f"torrents/selectFiles/{torrent_id}", data={"files": "all"})

async def select_files(torrent_id: str, file_ids: list[str] | list[int]):
    ids = [str(x).strip() for x in (file_ids or []) if str(x).strip().isdigit()]
    if not ids:
        raise RealDebridError("No valid Real-Debrid file IDs were selected")
    return await request("POST", f"torrents/selectFiles/{torrent_id}", data={"files": ",".join(ids)})

async def torrent_info(torrent_id: str) -> dict:
    return await request("GET", f"torrents/info/{torrent_id}")

async def instant_availability(info_hash: str):
    return await request("GET", f"torrents/instantAvailability/{info_hash}")


def _normalise_torrent_name(value: str) -> str:
    """Conservative name normalisation used only for exact source cleanup matching."""
    import unicodedata
    text = unicodedata.normalize("NFKC", str(value or "")).strip().rstrip("/\\")
    return " ".join(text.split()).casefold()


async def delete_torrent(torrent_id: str):
    """Delete one exact Real-Debrid torrent by provider ID."""
    tid = str(torrent_id or "").strip()
    import re
    if not tid or not re.fullmatch(r"[A-Za-z0-9_-]+", tid):
        raise RealDebridError("Refusing to delete a Real-Debrid torrent without an exact provider torrent ID")
    return await request("DELETE", f"torrents/delete/{tid}")


async def exact_torrent_for_source(source_path: str, source_size_bytes: int = 0) -> dict:
    """Resolve a DMM/Decypharr source folder to exactly one RD torrent.

    This intentionally does not use fuzzy title matching.  The source folder's
    basename must equal the RD torrent filename after harmless unicode/case/
    whitespace normalisation.  If more than one exact-name torrent exists, an
    exact byte-size match may disambiguate it.  Anything else is treated as
    ambiguous and left untouched.
    """
    from pathlib import Path

    source_name = Path(str(source_path or "")).name
    wanted = _normalise_torrent_name(source_name)
    if not wanted:
        return {"ok": False, "reason": "Source folder has no usable name"}

    rows = await torrents(limit=1000)
    exact = [
        row for row in rows
        if _normalise_torrent_name(str(row.get("filename") or row.get("name") or "")) == wanted
    ]
    if len(exact) == 1:
        return {"ok": True, "torrent": exact[0], "matched_by": "exact filename"}

    if len(exact) > 1 and int(source_size_bytes or 0) > 0:
        size_matches = []
        for row in exact:
            try:
                if int(row.get("bytes") or 0) == int(source_size_bytes):
                    size_matches.append(row)
            except Exception:
                pass
        if len(size_matches) == 1:
            return {"ok": True, "torrent": size_matches[0], "matched_by": "exact filename + exact byte size"}

    if not exact:
        return {"ok": False, "reason": f"No exact Real-Debrid torrent matched source folder '{source_name}'"}
    return {"ok": False, "reason": f"{len(exact)} exact-name Real-Debrid torrents matched '{source_name}'; cleanup is ambiguous"}


async def delete_source_torrent_exact(source_path: str, source_size_bytes: int = 0) -> dict:
    """Safely delete the RD torrent backing one source folder when identification is exact."""
    if not connected():
        return {"ok": False, "deleted": False, "reason": "Real-Debrid is not connected in ArrNexus"}
    matched = await exact_torrent_for_source(source_path, source_size_bytes)
    if not matched.get("ok"):
        return {"ok": False, "deleted": False, "reason": matched.get("reason") or "Exact torrent match failed"}
    torrent = matched.get("torrent") or {}
    torrent_id = str(torrent.get("id") or "")
    if not torrent_id:
        return {"ok": False, "deleted": False, "reason": "Matched Real-Debrid torrent did not contain an ID"}
    await delete_torrent(torrent_id)
    return {
        "ok": True,
        "deleted": True,
        "torrent_id": torrent_id,
        "filename": str(torrent.get("filename") or torrent.get("name") or ""),
        "matched_by": matched.get("matched_by") or "exact match",
    }


async def unrestrict_link(link: str) -> dict:
    """Return an authenticated Real-Debrid direct-download link."""
    value = str(link or "").strip()
    if not value:
        raise RealDebridError("Real-Debrid link is empty")
    row = await request("POST", "unrestrict/link", data={"link": value})
    if not isinstance(row, dict) or not str(row.get("download") or "").strip():
        raise RealDebridError("Real-Debrid did not return a direct download URL")
    return row


def _normalise_rd_file_path(value: str) -> str:
    return "/".join(x for x in str(value or "").replace("\\", "/").strip("/").split("/") if x).casefold()


def _rd_exact_name_variants(value: str) -> set[str]:
    """Return conservative exact-equivalent names for RD source resolution.

    Decypharr can expose a single-file torrent as a directory named after the
    archive stem (``season-4_202405``) while Real-Debrid reports the torrent
    filename as ``season-4_202405.rar``.  Treating those two names as exact
    equivalents is safe because the requested archive file must also resolve
    uniquely inside the selected torrent before the candidate can be used.
    """
    from pathlib import Path

    name = Path(str(value or "").replace("\\", "/").rstrip("/")).name
    normal = _normalise_torrent_name(name)
    if not normal:
        return set()
    out = {normal}
    suffix = Path(name).suffix.casefold()
    if suffix in {".rar", ".zip", ".7z"}:
        stem = _normalise_torrent_name(Path(name).stem)
        if stem:
            out.add(stem)
    return out


def _rd_all_files(info: dict) -> list[dict]:
    return [dict(x) for x in ((info or {}).get("files") or []) if isinstance(x, dict)]


def _selected_rd_files(info: dict) -> list[dict]:
    """Return Real-Debrid files that participate in the generated link list.

    Real-Debrid normally marks selected files with ``selected=1``. Some
    single-file responses omit/normalise that marker while still returning one
    file and one link. In that unambiguous case the sole file is authoritative.
    """
    all_files = _rd_all_files(info)
    selected: list[dict] = []
    for row in all_files:
        try:
            is_selected = int(row.get("selected") or 0) == 1
        except Exception:
            is_selected = str(row.get("selected") or "").strip().casefold() in {"true", "yes", "selected"}
        if is_selected:
            selected.append(row)
    links = [str(x) for x in ((info or {}).get("links") or []) if str(x).strip()]
    if selected:
        return selected
    if len(all_files) == 1 and len(links) == 1:
        return all_files
    return []


def _file_name_match_indices(files: list[dict], relative_file: str) -> tuple[list[int], str]:
    """Return unique-match candidates using exact path then archive-name variants."""
    wanted = _normalise_rd_file_path(relative_file)
    if not wanted:
        return [], ""

    exact: list[int] = []
    for idx, row in enumerate(files):
        candidate = _normalise_rd_file_path(row.get("path") or "")
        if candidate == wanted or candidate.endswith("/" + wanted):
            exact.append(idx)
    if len(exact) == 1:
        return exact, "exact selected-file path"

    wanted_base = wanted.rsplit("/", 1)[-1]
    wanted_variants = _rd_exact_name_variants(wanted_base)
    variants: list[int] = []
    for idx, row in enumerate(files):
        candidate_base = _normalise_rd_file_path(row.get("path") or "").rsplit("/", 1)[-1]
        if _rd_exact_name_variants(candidate_base) & wanted_variants:
            variants.append(idx)
    if len(variants) == 1:
        return variants, "exact selected-file archive-name equivalent"
    return [], ""


def _match_selected_file(info: dict, relative_file: str, *, exact_torrent_identity: bool = False) -> tuple[dict, str, str] | None:
    """Resolve the requested archive to exactly one Real-Debrid link.

    This stays deliberately conservative while handling real single-file RD
    representations that Decypharr can expose differently:

    * ``archive`` and ``archive.rar`` are treated as exact archive-name
      equivalents;
    * if selection flags are absent but there is only one generated link, a
      unique exact file-name match across the RD file list is accepted;
    * if the torrent itself exactly identifies the requested archive and RD
      exposes exactly one file/link, that sole file is authoritative even when
      RD rewrites the internal file path;
    * if an exact single-file torrent returns one link but omits ``files``
      metadata entirely, the torrent byte count is used as authoritative
      metadata for that sole archive.

    A multi-file ambiguity is always rejected.
    """
    info = info or {}
    links = [str(x) for x in (info.get("links") or []) if str(x).strip()]
    if not links:
        return None

    selected = _selected_rd_files(info)
    if selected and len(selected) == len(links):
        matches, reason = _file_name_match_indices(selected, relative_file)
        if len(matches) == 1:
            idx = matches[0]
            return selected[idx], links[idx], reason
        if exact_torrent_identity and len(selected) == 1 and len(links) == 1:
            return selected[0], links[0], "exact single-file torrent identity"

    # Some RD responses have one link but omit usable selection markers. If
    # the requested archive uniquely identifies one file in the full list, the
    # sole link can only represent that selected file.
    all_files = _rd_all_files(info)
    if len(links) == 1 and all_files:
        matches, reason = _file_name_match_indices(all_files, relative_file)
        if len(matches) == 1:
            return all_files[matches[0]], links[0], reason + " (single RD link)"
        if exact_torrent_identity and len(all_files) == 1:
            return all_files[0], links[0], "exact single-file torrent identity"

    # Last safe single-file form: exact archive torrent + one generated link,
    # but RD omitted file rows. This is still unambiguous because the torrent
    # identity itself is exact and there is only one downloadable link.
    if exact_torrent_identity and len(links) == 1 and not all_files:
        try:
            byte_count = int(info.get("bytes") or info.get("original_bytes") or 0)
        except Exception:
            byte_count = 0
        return {
            "id": 0,
            "path": "/" + str(relative_file or "").lstrip("/"),
            "bytes": byte_count,
            "selected": 1,
        }, links[0], "exact single-file torrent identity (RD file metadata omitted)"

    return None


async def _exact_rd_torrent_file(source_pack_path: str, relative_file: str) -> dict:
    """Resolve one archive using only exact/equivalent names plus exact file match.

    This is intentionally more tolerant than provider cleanup matching, because
    Decypharr may strip ``.rar`` from the displayed source-pack directory.  It
    remains non-fuzzy: a candidate must use an exact name variant and contain
    exactly one selected file matching the requested relative path/basename.
    Multiple valid candidates are rejected rather than guessed.
    """
    from pathlib import Path

    pack_name = Path(str(source_pack_path or "").replace("\\", "/").rstrip("/")).name
    file_name = Path(str(relative_file or "").replace("\\", "/")).name
    pack_variants = _rd_exact_name_variants(pack_name)
    file_variants = _rd_exact_name_variants(file_name)
    wanted_names = pack_variants | file_variants
    if not wanted_names:
        raise RealDebridError("Archive source has no usable exact Real-Debrid identity")

    rows = await torrents(limit=2000)
    candidates: list[tuple[int, dict]] = []
    for row in rows:
        torrent_name = str(row.get("filename") or row.get("name") or "")
        normal = _normalise_torrent_name(torrent_name)
        variants = _rd_exact_name_variants(torrent_name)
        if normal not in wanted_names and not (variants & wanted_names):
            continue
        # Prefer the visible pack name, then the exact archive filename, then
        # the extension-equivalent stem form.
        score = 1
        if normal in pack_variants:
            score = 3
        elif normal in file_variants:
            score = 2
        candidates.append((score, row))

    if not candidates:
        raise RealDebridError(
            f"No exact Real-Debrid torrent matched source pack '{pack_name}' or archive '{file_name}'"
        )

    resolved: list[dict] = []
    lookup_errors: list[str] = []
    for score, torrent in candidates:
        tid = str(torrent.get("id") or "")
        if not tid:
            continue
        try:
            info = await torrent_info(tid)
            info_name = str((info or {}).get("filename") or torrent.get("filename") or torrent.get("name") or "")
            info_variants = _rd_exact_name_variants(info_name)
            exact_torrent_identity = bool(info_variants & file_variants)
            match = _match_selected_file(
                info or {}, relative_file, exact_torrent_identity=exact_torrent_identity
            )
            if match is None:
                selected_rows = _selected_rd_files(info or {})
                selected_paths = [str(x.get("path") or "") for x in selected_rows]
                links = [str(x) for x in ((info or {}).get("links") or []) if str(x).strip()]
                lookup_errors.append(
                    f"torrent {tid} '{info_name}': selected={selected_paths or ['<none>']} links={len(links)}"
                )
                continue
            file_row, restricted_link, file_match = match
            resolved.append({
                "score": score,
                "torrent": torrent,
                "info": info or {},
                "file": file_row,
                "restricted_link": restricted_link,
                "file_match": file_match,
            })
        except Exception as exc:
            lookup_errors.append(str(exc))

    if not resolved:
        detail = f" ({lookup_errors[0]})" if lookup_errors else ""
        raise RealDebridError(
            f"Exact Real-Debrid torrent candidate(s) did not contain a unique selected file matching '{relative_file}'{detail}"
        )

    best_score = max(int(x["score"]) for x in resolved)
    best = [x for x in resolved if int(x["score"]) == best_score]
    if len(best) != 1:
        ids = ", ".join(str((x.get("torrent") or {}).get("id") or "?") for x in best[:5])
        raise RealDebridError(
            f"{len(best)} exact Real-Debrid torrents matched archive '{relative_file}' ({ids}); direct recovery is ambiguous"
        )
    return best[0]


async def direct_file_metadata_for_source_file(source_pack_path: str, relative_file: str) -> dict:
    """Resolve one exact DMM/Decypharr source-pack file to RD metadata.

    The resolver accepts only exact source-pack/archive-name equivalents and an
    exact selected-file match.  This covers Decypharr's common stem-directory
    representation without introducing fuzzy title matching.
    """
    resolved = await _exact_rd_torrent_file(source_pack_path, relative_file)
    torrent = resolved.get("torrent") or {}
    info = resolved.get("info") or {}
    file_row = resolved.get("file") or {}
    tid = str(torrent.get("id") or "")
    return {
        "torrent_id": tid,
        "torrent_filename": str(info.get("filename") or torrent.get("filename") or torrent.get("name") or ""),
        "file_path": str(file_row.get("path") or relative_file),
        "file_bytes": int(file_row.get("bytes") or 0),
        "restricted_link": str(resolved.get("restricted_link") or ""),
        "matched_by": "exact source/archive name + " + str(resolved.get("file_match") or "exact selected file"),
    }


async def direct_download_for_source_file(source_pack_path: str, relative_file: str) -> dict:
    """Resolve one exact DMM source-pack file to an RD HTTPS download.

    Safety is intentionally strict: the backing torrent must match the source
    pack exactly and the requested file must resolve uniquely.  Ambiguity is a
    hard failure rather than a fuzzy guess.
    """
    meta = await direct_file_metadata_for_source_file(source_pack_path, relative_file)
    unrestricted = await unrestrict_link(str(meta.get("restricted_link") or ""))
    return {
        "torrent_id": meta["torrent_id"],
        "torrent_filename": meta["torrent_filename"],
        "file_path": meta["file_path"],
        "file_bytes": int(meta.get("file_bytes") or unrestricted.get("filesize") or 0),
        "download": str(unrestricted.get("download") or ""),
        "download_id": str(unrestricted.get("id") or ""),
        "matched_by": meta["matched_by"],
    }
