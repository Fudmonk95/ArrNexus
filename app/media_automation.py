"""Server-independent collection automation for ArrNexus v10.8.

The normalized definition is authoritative.  Adapters translate it for Plex
through Kometa and for Jellyfin/Emby through their native collection APIs.
Sync is additive by default: preview reports removals, but ArrNexus never
removes collection members unless a future explicit destructive mode is added.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable
from urllib.parse import quote

import httpx

from .connections import get_connection
from .db import db, setting_get, setting_set
from . import lists as media_lists


ENGINES = {"auto", "kometa", "arrnexus_native"}
SERVER_TYPES = {"plex", "jellyfin", "emby"}
SOURCE_TYPES = {"manual": "Manual provider IDs", "mdblist": "MDBList", **media_lists.SOURCE_TYPES}

PRESETS: tuple[dict[str, Any], ...] = (
    {"slug": "trending-movies", "name": "Trending Movies", "media_type": "movie", "source_type": "tmdb", "source_ref": "https://www.themoviedb.org/trending/movie/week"},
    {"slug": "trending-tv", "name": "Trending TV", "media_type": "tv", "source_type": "tmdb", "source_ref": "https://www.themoviedb.org/trending/tv/week"},
    {"slug": "popular-movies", "name": "Popular Movies", "media_type": "movie", "source_type": "tmdb", "source_ref": "https://www.themoviedb.org/movie"},
    {"slug": "top-rated-movies", "name": "Top Rated Movies", "media_type": "movie", "source_type": "tmdb", "source_ref": "https://www.themoviedb.org/movie/top-rated"},
    {"slug": "top-rated-tv", "name": "Top Rated TV", "media_type": "tv", "source_type": "tmdb", "source_ref": "https://www.themoviedb.org/tv/top-rated"},
    {"slug": "upcoming-movies", "name": "Upcoming Movies", "media_type": "movie", "source_type": "tmdb", "source_ref": "https://www.themoviedb.org/movie/upcoming"},
    {"slug": "now-playing", "name": "Now Playing", "media_type": "movie", "source_type": "tmdb", "source_ref": "https://www.themoviedb.org/movie/now-playing"},
    {"slug": "plex-watchlist", "name": "My Plex Watchlist", "media_type": "mixed", "source_type": "plex_watchlist", "source_ref": "watchlist"},
    {"slug": "trakt-watchlist", "name": "My Trakt Watchlist", "media_type": "mixed", "source_type": "trakt_watchlist", "source_ref": "watchlist"},
    {"slug": "imdb-list", "name": "IMDb Curated List", "media_type": "mixed", "source_type": "imdb", "source_ref": "https://www.imdb.com/list/ls000000000/"},
    {"slug": "mdblist", "name": "MDBList Collection", "media_type": "mixed", "source_type": "mdblist", "source_ref": "https://mdblist.com/lists/user/list"},
    {"slug": "custom", "name": "Custom Collection", "media_type": "mixed", "source_type": "manual", "source_ref": ""},
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed
    except Exception:
        return fallback


def _row(row: Any) -> dict[str, Any] | None:
    if not row:
        return None
    out = dict(row)
    out["definition"] = _loads(out.pop("definition_json", "{}"), {})
    return out


def list_definitions() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM media_automations ORDER BY name COLLATE NOCASE,id").fetchall()
        out = []
        for raw in rows:
            item = _row(raw) or {}
            item["targets"] = [dict(x) for x in conn.execute("SELECT * FROM media_automation_targets WHERE automation_id=? ORDER BY id", (int(item["id"]),)).fetchall()]
            latest = conn.execute("SELECT * FROM media_automation_runs WHERE automation_id=? ORDER BY id DESC LIMIT 1", (int(item["id"]),)).fetchone()
            item["latest_run"] = dict(latest) if latest else None
            out.append(item)
    return out


def get_definition(automation_id: int) -> dict[str, Any] | None:
    with db() as conn:
        raw = conn.execute("SELECT * FROM media_automations WHERE id=?", (int(automation_id),)).fetchone()
        item = _row(raw)
        if item:
            item["targets"] = [dict(x) for x in conn.execute("SELECT * FROM media_automation_targets WHERE automation_id=? ORDER BY id", (int(automation_id),)).fetchall()]
    return item


def save_definition(*, automation_id: int | None, name: str, media_type: str, source_type: str,
                    source_ref: str, engine: str, schedule_hours: int, enabled: bool,
                    acquire_missing: bool, summary: str = "", sort: str = "source",
                    artwork_url: str = "", manual_items: str = "", targets: list[dict] | None = None) -> int:
    name = str(name or "").strip()
    if not name:
        raise ValueError("Collection name is required")
    if media_type not in {"movie", "tv", "mixed"}:
        raise ValueError("Media type must be movie, TV or mixed")
    if source_type not in SOURCE_TYPES:
        raise ValueError("Unsupported collection source")
    if engine not in ENGINES:
        engine = "auto"
    definition = {"summary": str(summary or "").strip(), "sort": str(sort or "source"),
                  "artwork_url": str(artwork_url or "").strip(), "manual_items": str(manual_items or "").strip(),
                  "non_destructive": True, "schema": 1}
    values = (name, media_type, source_type, str(source_ref or "").strip(), json.dumps(definition, sort_keys=True),
              engine, int(bool(enabled)), max(1, min(24 * 30, int(schedule_hours or 24))), int(bool(acquire_missing)), _utcnow())
    with db() as conn:
        if automation_id:
            conn.execute("UPDATE media_automations SET name=?,media_type=?,source_type=?,source_ref=?,definition_json=?,engine=?,enabled=?,schedule_hours=?,acquire_missing=?,updated_at=? WHERE id=?", values + (int(automation_id),))
            ident = int(automation_id)
            conn.execute("DELETE FROM media_automation_targets WHERE automation_id=?", (ident,))
        else:
            cur = conn.execute("INSERT INTO media_automations(name,media_type,source_type,source_ref,definition_json,engine,enabled,schedule_hours,acquire_missing,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", values)
            ident = int(cur.lastrowid)
        for target in targets or []:
            server_type = str(target.get("server_type") or "").lower()
            if server_type not in SERVER_TYPES or not target.get("enabled", True):
                continue
            target_engine = str(target.get("engine") or engine)
            if target_engine not in ENGINES:
                target_engine = "auto"
            conn.execute("INSERT INTO media_automation_targets(automation_id,server_type,library_name,collection_name,engine,enabled) VALUES(?,?,?,?,?,1)",
                         (ident, server_type, str(target.get("library_name") or ""), str(target.get("collection_name") or name), target_engine))
    return ident


def delete_definition(automation_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM media_automations WHERE id=?", (int(automation_id),))


def create_run(automation_id: int, job_id: int | None, preview: bool = False) -> int:
    with db() as conn:
        cur = conn.execute("INSERT INTO media_automation_runs(automation_id,job_id,preview,status) VALUES(?,?,?,'running')",
                           (int(automation_id), int(job_id) if job_id else None, int(bool(preview))))
        return int(cur.lastrowid)


def finish_run(run_id: int, status: str, result: dict[str, Any]) -> None:
    with db() as conn:
        conn.execute("UPDATE media_automation_runs SET status=?,result_json=?,finished_at=? WHERE id=?",
                     (str(status), json.dumps(result, sort_keys=True), _utcnow(), int(run_id)))


def create_from_preset(slug: str) -> int:
    preset = next((x for x in PRESETS if x["slug"] == slug), None)
    if not preset:
        raise ValueError("Unknown preset")
    return save_definition(automation_id=None, name=preset["name"], media_type=preset["media_type"],
                           source_type=preset["source_type"], source_ref=preset["source_ref"], engine="auto",
                           schedule_hours=24, enabled=False, acquire_missing=False, targets=[])


def import_kometa_yaml(text: str, *, library_name: str = "") -> list[int]:
    """Import the safe collection/ID subset of a Kometa YAML document.

    This intentionally does not execute tags, templates or custom builders.
    Imported definitions are disabled and preview-first.
    """
    lines = str(text or "").replace("\t", "  ").splitlines()
    in_collections = False; current = ""; provider = ""; found: dict[str, list[str]] = {}
    for raw in lines:
        clean = raw.split("#", 1)[0].rstrip()
        if not clean.strip():
            continue
        indent = len(clean) - len(clean.lstrip(" ")); value = clean.strip()
        if indent == 0:
            in_collections = value == "collections:"
            current = ""; provider = ""
            continue
        if not in_collections:
            continue
        if indent == 2 and value.endswith(":"):
            current = value[:-1].strip().strip("'\"")[:160]; provider = ""
            if current: found.setdefault(current, [])
        elif current and indent == 4 and value.endswith(":"):
            key = value[:-1].strip()
            provider = key if key in {"imdb_id", "tmdb_movie", "tvdb_show"} else ""
        elif current and provider and value.startswith("-"):
            ident = value[1:].strip().strip("'\"")
            if provider == "imdb_id" and re.fullmatch(r"tt\d{5,12}", ident, re.I):
                found[current].append(ident.lower())
            elif ident.isdigit() and provider == "tmdb_movie":
                found[current].append(f"movie:tmdb:{ident}")
            elif ident.isdigit() and provider == "tvdb_show":
                found[current].append(f"tv:tvdb:{ident}")
    created = []
    for name, ids in found.items():
        created.append(save_definition(automation_id=None, name=name, media_type="mixed", source_type="manual", source_ref="Imported from Kometa YAML",
                                       engine="kometa", schedule_hours=24, enabled=False, acquire_missing=False,
                                       summary="Imported safely from Kometa; templates and executable builders were not imported.", manual_items="\n".join(ids),
                                       targets=[{"server_type": "plex", "library_name": library_name, "collection_name": name, "engine": "kometa"}]))
    return created


def save_kometa_settings(executable: str, config_path: str, managed_path: str) -> None:
    setting_set("media_automation.kometa.executable", str(executable or "").strip())
    setting_set("media_automation.kometa.config_path", str(config_path or "").strip())
    setting_set("media_automation.kometa.managed_path", str(managed_path or "").strip())


def save_source_settings(mdblist_api_key: str) -> None:
    if mdblist_api_key and mdblist_api_key not in {"********", "••••••••"}:
        setting_set("media_automation.mdblist.api_key", mdblist_api_key.strip(), True)


def source_provider_state() -> dict[str, Any]:
    return {"mdblist_configured": bool(setting_get("media_automation.mdblist.api_key", ""))}


def kometa_state() -> dict[str, Any]:
    exe = setting_get("media_automation.kometa.executable", "")
    config = setting_get("media_automation.kometa.config_path", "")
    managed = setting_get("media_automation.kometa.managed_path", "")
    detected = bool(exe and Path(exe).is_file())
    return {"executable": exe, "config_path": config, "managed_path": managed,
            "detected": detected, "configured": bool(detected and config),
            "detail": "Kometa executable and config detected" if detected and config else "Add the Kometa executable and config path to enable Plex sync"}


async def server_capabilities() -> list[dict[str, Any]]:
    out = []
    for kind in ("plex", "jellyfin", "emby"):
        conn = get_connection(kind)
        state = {"server_type": kind, "configured": bool(conn.url and conn.api_key), "url": conn.url,
                 "engine": "kometa" if kind == "plex" else "arrnexus_native", "ok": False,
                 "collections": kind in {"jellyfin", "emby"}, "smartlists": False}
        if not state["configured"]:
            state["detail"] = "Connection not configured"
            out.append(state); continue
        try:
            headers = {"X-Plex-Token": conn.api_key} if kind == "plex" else {"X-Emby-Token": conn.api_key}
            endpoint = "/" if kind == "plex" else "/System/Info"
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                response = await client.get(conn.url.rstrip("/") + endpoint, headers=headers)
                response.raise_for_status()
                state["ok"] = True
                state["detail"] = "Connected"
                if kind == "jellyfin":
                    plugins = await client.get(conn.url.rstrip("/") + "/Plugins", headers=headers)
                    if plugins.is_success:
                        rows = plugins.json() if isinstance(plugins.json(), list) else []
                        state["smartlists"] = any("smartlist" in str(x.get("Name") or "").lower().replace(" ", "") for x in rows)
                        if state["smartlists"]:
                            state["detail"] = "Connected; SmartLists plugin detected (native fallback remains available)"
        except Exception as exc:
            state["detail"] = str(exc)
        if kind == "plex":
            ks = kometa_state(); state["kometa"] = ks; state["collections"] = bool(ks["configured"])
            state["detail"] = ks["detail"] if state["ok"] else state["detail"]
        out.append(state)
    return out


def _manual_items(text: str, default_media_type: str) -> list[dict[str, Any]]:
    out = []
    for line in re.split(r"[\r\n,]+", text or ""):
        value = line.strip()
        if not value:
            continue
        media_type = "movie" if default_media_type == "mixed" else default_media_type
        row: dict[str, Any] = {"media_type": media_type, "title": value, "year": None, "imdb_id": "", "tmdb_id": None, "tvdb_id": None}
        match = re.match(r"^(movie|tv)\s*:\s*(tmdb|tvdb|imdb)\s*:\s*(.+)$", value, re.I)
        if match:
            row["media_type"] = match.group(1).lower(); provider = match.group(2).lower(); ident = match.group(3).strip()
            row[provider + "_id"] = int(ident) if provider in {"tmdb", "tvdb"} and ident.isdigit() else ident
            row["title"] = value
        elif re.fullmatch(r"tt\d{5,12}", value, re.I):
            row["imdb_id"] = value.lower()
        out.append(row)
    return out


async def resolve_source(definition: dict[str, Any]) -> list[dict[str, Any]]:
    detail = definition.get("definition") or {}
    if definition.get("source_type") == "manual":
        return _manual_items(str(detail.get("manual_items") or definition.get("source_ref") or ""), str(definition.get("media_type") or "mixed"))
    if definition.get("source_type") == "mdblist":
        key = setting_get("media_automation.mdblist.api_key", "")
        if not key:
            raise RuntimeError("MDBList API key is not configured under Media Automation > Servers & engines")
        reference = str(definition.get("source_ref") or "").strip().rstrip("/")
        match = re.search(r"mdblist\.com/lists/(.+)$", reference, re.I)
        if not match:
            raise RuntimeError("MDBList source must be a https://mdblist.com/lists/... URL")
        list_path = match.group(1).strip("/")
        endpoint = f"https://api.mdblist.com/lists/{list_path}/items/"
        rows: list[dict[str, Any]] = []; offset = 0
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "ArrNexus/10.8"}) as client:
            while True:
                response = await client.get(endpoint, params={"apikey": key, "unified": "true", "limit": 1000, "offset": offset})
                response.raise_for_status(); payload = response.json()
                page = payload.get("items") or payload.get("movies") or payload.get("shows") or [] if isinstance(payload, dict) else payload
                if not isinstance(page, list): page = []
                for item in page:
                    media_type = "tv" if str(item.get("mediatype") or item.get("type") or "movie").lower() in {"show", "series", "tv"} else "movie"
                    tmdb = item.get("id") or item.get("tmdbid") or item.get("tmdb_id")
                    ids = item.get("ids") or {}
                    rows.append({"media_type": media_type, "title": str(item.get("title") or item.get("name") or f"TMDb {tmdb or ''}").strip(),
                                 "year": item.get("release_year") or item.get("year"), "imdb_id": str(item.get("imdb_id") or ids.get("imdb") or ""),
                                 "tmdb_id": int(tmdb) if str(tmdb or "").isdigit() else None, "tvdb_id": int(ids.get("tvdb")) if str(ids.get("tvdb") or "").isdigit() else None})
                offset += len(page)
                if not page or str(response.headers.get("X-Has-More") or "false").lower() != "true": break
        return rows
    list_def = {"source_type": definition.get("source_type"), "source_ref": definition.get("source_ref"), "media_type": definition.get("media_type")}
    return [item.dict() for item in await media_lists.fetch_items(list_def)]


def _provider_key(row: dict[str, Any]) -> str:
    for key in ("tmdb_id", "tvdb_id", "imdb_id"):
        if row.get(key):
            return f"{key}:{str(row[key]).lower()}"
    return "title:" + str(row.get("title") or "").strip().casefold()


class NativeCollectionAdapter:
    def __init__(self, server_type: str):
        self.server_type = server_type
        self.connection = get_connection(server_type)
        self.base = self.connection.url.rstrip("/")
        self.headers = {"X-Emby-Token": self.connection.api_key, "Accept": "application/json", "User-Agent": "ArrNexus/10.8"}

    async def _json(self, method: str, path: str, **kwargs) -> Any:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=self.headers) as client:
            response = await client.request(method, self.base + path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}

    async def libraries(self) -> list[dict[str, str]]:
        rows = await self._json("GET", "/Library/VirtualFolders")
        return [{"id": str(x.get("ItemId") or ""), "name": str(x.get("Name") or "")} for x in rows or []]

    async def inventory(self, library_name: str) -> list[dict[str, Any]]:
        libraries = await self.libraries()
        library = next((x for x in libraries if x["name"].casefold() == library_name.casefold()), None) if library_name else None
        params = {"Recursive": "true", "IncludeItemTypes": "Movie,Series", "Fields": "ProviderIds", "Limit": 100000}
        if library and library["id"]:
            params["ParentId"] = library["id"]
        data = await self._json("GET", "/Items", params=params)
        out = []
        for item in data.get("Items") or []:
            ids = item.get("ProviderIds") or {}
            out.append({"id": str(item.get("Id") or ""), "title": item.get("Name"), "media_type": "tv" if item.get("Type") == "Series" else "movie",
                        "tmdb_id": ids.get("Tmdb"), "tvdb_id": ids.get("Tvdb"), "imdb_id": ids.get("Imdb")})
        return out

    async def _collection(self, name: str) -> dict[str, Any] | None:
        data = await self._json("GET", "/Items", params={"Recursive": "true", "IncludeItemTypes": "BoxSet", "SearchTerm": name, "Limit": 1000})
        return next((x for x in data.get("Items") or [] if str(x.get("Name") or "").casefold() == name.casefold()), None)

    async def member_ids(self, collection_id: str) -> set[str]:
        data = await self._json("GET", "/Items", params={"ParentId": collection_id, "Recursive": "true", "Limit": 100000})
        return {str(x.get("Id") or "") for x in data.get("Items") or []}

    async def apply(self, collection_name: str, library_name: str, desired: list[dict[str, Any]], log: Callable[[str], None]) -> dict[str, Any]:
        inventory = await self.inventory(library_name)
        by_provider = {_provider_key(x): x for x in inventory if _provider_key(x) != "title:"}
        matched = [by_provider[_provider_key(x)] for x in desired if _provider_key(x) in by_provider]
        missing = [x for x in desired if _provider_key(x) not in by_provider]
        collection = await self._collection(collection_name)
        ids = [x["id"] for x in matched if x.get("id")]
        if not collection:
            libraries = await self.libraries(); library = next((x for x in libraries if x["name"].casefold() == library_name.casefold()), None) if library_name else None
            created = await self._json("POST", "/Collections", params={"Name": collection_name, "ParentId": (library or {}).get("id", ""), "Ids": ",".join(ids), "IsLocked": "false"})
            collection_id = str(created.get("Id") or created.get("id") or "")
            added = len(ids)
            log(f"Created {collection_name} with {added} matched item(s)")
        else:
            collection_id = str(collection.get("Id") or "")
            existing = await self.member_ids(collection_id)
            to_add = [x for x in ids if x not in existing]
            if to_add:
                await self._json("POST", f"/Collections/{quote(collection_id)}/Items", params={"Ids": ",".join(to_add)})
            added = len(to_add)
            log(f"Updated {collection_name}; added {added} new item(s), removed none")
        return {"ok": True, "collection_id": collection_id, "matched": len(matched), "added": added, "missing": missing, "non_destructive": True}


async def preview_definition(definition: dict[str, Any], desired: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    desired = desired if desired is not None else await resolve_source(definition)
    results = []
    for target in definition.get("targets") or []:
        kind = str(target.get("server_type") or "")
        row = {"target_id": target.get("id"), "server_type": kind, "collection_name": target.get("collection_name") or definition["name"], "desired": len(desired), "add": 0, "present": 0, "missing": len(desired), "remove": 0, "ok": False}
        try:
            if kind == "plex":
                state = kometa_state(); row.update(ok=bool(state["configured"]), engine="kometa", detail=state["detail"], add=len(desired) if state["configured"] else 0)
            else:
                adapter = NativeCollectionAdapter(kind)
                inventory = await adapter.inventory(str(target.get("library_name") or ""))
                by_key = {_provider_key(x): x for x in inventory}; matched = [by_key[_provider_key(x)] for x in desired if _provider_key(x) in by_key]
                collection = await adapter._collection(str(target.get("collection_name") or definition["name"]))
                member_ids = await adapter.member_ids(str(collection.get("Id") or "")) if collection else set()
                already = sum(1 for x in matched if str(x.get("id") or "") in member_ids)
                row.update(ok=True, engine="arrnexus_native", present=already, add=len(matched) - already, missing=len(desired) - len(matched), detail="Non-destructive preview; existing collection members will not be removed")
        except Exception as exc:
            row["detail"] = str(exc)
        results.append(row)
    return {"ok": any(x["ok"] for x in results) if results else False, "desired": desired, "targets": results, "non_destructive": True}


def _kometa_yaml(definition: dict[str, Any], desired: list[dict[str, Any]]) -> str:
    name = str(definition["name"]).replace('"', "'")
    lines = ["collections:", f'  "{name}":', "    sync_mode: append", "    item_label: ArrNexus", "    collection_order: custom"]
    ids = [str(x.get("imdb_id") or "") for x in desired if x.get("imdb_id")]
    tmdb = [str(x.get("tmdb_id") or "") for x in desired if x.get("tmdb_id") and x.get("media_type") == "movie"]
    tvdb = [str(x.get("tvdb_id") or "") for x in desired if x.get("tvdb_id")]
    if ids: lines.extend(["    imdb_id:"] + [f"      - {x}" for x in ids])
    if tmdb: lines.extend(["    tmdb_movie:"] + [f"      - {x}" for x in tmdb])
    if tvdb: lines.extend(["    tvdb_show:"] + [f"      - {x}" for x in tvdb])
    summary = str((definition.get("definition") or {}).get("summary") or "").strip()
    if summary: lines.append("    summary: " + json.dumps(summary, ensure_ascii=False))
    return "\n".join(lines) + "\n"


async def _apply_kometa(definition: dict[str, Any], target: dict[str, Any], desired: list[dict[str, Any]], log: Callable[[str], None], cancel_check: Callable[[], bool]) -> dict[str, Any]:
    state = kometa_state()
    if not state["configured"]:
        raise RuntimeError(state["detail"])
    root = Path(state["managed_path"] or Path(state["config_path"]).parent / "arrnexus-collections")
    root.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(definition["name"])).strip("-").lower() or f"collection-{definition['id']}"
    path = root / f"arrnexus-{definition['id']}-{safe}.yml"
    path.write_text(_kometa_yaml(definition, desired), encoding="utf-8", newline="\n")
    log(f"Generated Kometa collection file {path.name}")
    command = [state["executable"]]
    if str(state["executable"]).lower().endswith(".py"):
        command.insert(0, sys.executable)
    process = await asyncio.create_subprocess_exec(*command, "--config", state["config_path"], "--run", "--run-files", str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    communication = asyncio.create_task(process.communicate())
    while not communication.done():
        await asyncio.sleep(.5)
        if cancel_check():
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                process.kill(); await process.wait()
            await communication
            raise asyncio.CancelledError("Kometa sync cancelled")
    output, _ = await communication
    for line in output.decode("utf-8", "replace").splitlines()[-80:]:
        if line.strip(): log(line.strip())
    if process.returncode:
        raise RuntimeError(f"Kometa exited with code {process.returncode}")
    return {"ok": True, "engine": "kometa", "generated_path": str(path), "matched": len(desired), "added": len(desired), "missing": []}


async def sync_definition(definition: dict[str, Any], *, log: Callable[[str, str], None], cancel_check: Callable[[], bool]) -> dict[str, Any]:
    desired = await resolve_source(definition)
    log("resolving", f"Resolved {len(desired)} normalized source title(s)")
    results = []
    for target in definition.get("targets") or []:
        if cancel_check():
            raise asyncio.CancelledError("Media automation cancelled")
        kind = str(target.get("server_type") or "")
        log("syncing", f"Syncing {target.get('collection_name') or definition['name']} to {kind.title()}")
        try:
            if kind == "plex":
                result = await _apply_kometa(definition, target, desired, lambda message: log("kometa", message), cancel_check)
            else:
                result = await NativeCollectionAdapter(kind).apply(str(target.get("collection_name") or definition["name"]), str(target.get("library_name") or ""), desired, lambda message: log("syncing", message))
            result.update(server_type=kind, target_id=target.get("id"))
            with db() as conn:
                conn.execute("UPDATE media_automation_targets SET last_collection_id=?,last_status='complete',last_error='',last_sync_at=? WHERE id=?", (str(result.get("collection_id") or ""), _utcnow(), int(target["id"])))
        except Exception as exc:
            result = {"ok": False, "server_type": kind, "target_id": target.get("id"), "error": str(exc)}
            with db() as conn:
                conn.execute("UPDATE media_automation_targets SET last_status='failed',last_error=?,last_sync_at=? WHERE id=?", (str(exc), _utcnow(), int(target["id"])))
            log("error", f"{kind.title()} target failed: {exc}")
        results.append(result)
    return {"ok": any(x.get("ok") for x in results), "partial": any(x.get("ok") for x in results) and any(not x.get("ok") for x in results), "desired_count": len(desired), "targets": results}
