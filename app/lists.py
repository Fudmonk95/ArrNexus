from __future__ import annotations

"""External Lists & Watchlists automation for ArrNexus v10.2.

List adapters deliberately stop at normalized media identities.  ArrNexus then
uses the same Radarr/Sonarr discovery, routing and acquisition code used by the
rest of the product; a list is another request source, not a parallel importer.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import time
from typing import Any
from urllib.parse import urlencode, urlparse
import xml.etree.ElementTree as ET

import httpx

from .db import db, setting_get, setting_set, setting_delete, log_event
from .connections import get_connection
from .paths import movie_roots, tv_roots
from .router_service import discover_lookup, discover_add, client_for_destination
from .acquisition import plan_and_grab, STRATEGIES

TRAKT_API = "https://api.trakt.tv"
TRAKT_AUTH = "https://auth.trakt.tv"
TMDB_API = "https://api.themoviedb.org/3"
SIMKL_API = "https://api.simkl.com"
PLEX_DISCOVER = "https://discover.provider.plex.tv"

SOURCE_TYPES = {
    "trakt_watchlist": "Trakt Watchlist",
    "trakt_list": "Trakt List",
    "imdb": "IMDb public list",
    "tmdb": "TMDb list",
    "plex_watchlist": "Plex Watchlist",
    "simkl": "Simkl Watchlist",
    "rss": "RSS / Atom",
    "json": "Custom JSON",
}
MEDIA_TYPES = {"movie", "tv", "mixed"}


@dataclass(frozen=True)
class NormalizedItem:
    media_type: str
    title: str
    year: int | None = None
    imdb_id: str = ""
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    source_id: str = ""

    def key(self) -> str:
        for kind, value in (("tmdb", self.tmdb_id), ("tvdb", self.tvdb_id), ("imdb", self.imdb_id), ("source", self.source_id)):
            if value:
                return f"{self.media_type}:{kind}:{value}"
        return f"{self.media_type}:title:{self.title.casefold()}:{self.year or ''}"

    def lookup_term(self) -> str:
        if self.media_type == "movie" and self.tmdb_id:
            return f"tmdb:{self.tmdb_id}"
        if self.media_type == "tv" and self.tvdb_id:
            return f"tvdb:{self.tvdb_id}"
        if self.imdb_id:
            return f"imdb:{self.imdb_id}"
        return f"{self.title} {self.year or ''}".strip()

    def dict(self) -> dict[str, Any]:
        return {
            "media_type": self.media_type, "title": self.title, "year": self.year,
            "imdb_id": self.imdb_id, "tmdb_id": self.tmdb_id, "tvdb_id": self.tvdb_id,
            "source_id": self.source_id, "key": self.key(), "lookup_term": self.lookup_term(),
        }


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def list_definitions() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM media_lists ORDER BY name COLLATE NOCASE, id").fetchall()
        out = [dict(r) for r in rows]
        for row in out:
            latest = conn.execute("SELECT * FROM media_list_runs WHERE list_id=? ORDER BY id DESC LIMIT 1", (int(row["id"]),)).fetchone()
            row["latest_run"] = dict(latest) if latest else None
    return out


def get_definition(list_id: int) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM media_lists WHERE id=?", (int(list_id),)).fetchone()
    return dict(row) if row else None


def save_definition(
    *, list_id: int | None, name: str, source_type: str, source_ref: str,
    media_type: str, movie_destination: str, tv_destination: str,
    acquisition_strategy: str, monitor: bool, search_automatically: bool,
    enabled: bool, sync_interval_hours: int,
) -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("List name is required")
    if source_type not in SOURCE_TYPES:
        raise ValueError("Unsupported list source")
    if media_type not in MEDIA_TYPES:
        raise ValueError("Unsupported media type")
    if acquisition_strategy not in STRATEGIES:
        acquisition_strategy = "automatic"
    if movie_destination != "auto" and movie_destination not in movie_roots():
        raise ValueError(f"Unknown Radarr destination: {movie_destination}")
    if tv_destination != "auto" and tv_destination not in tv_roots():
        raise ValueError(f"Unknown Sonarr destination: {tv_destination}")
    interval = max(1, min(24 * 30, int(sync_interval_hours or 12)))
    values = (
        name, source_type, (source_ref or "").strip(), media_type,
        movie_destination or "auto", tv_destination or "auto", acquisition_strategy,
        int(bool(monitor)), int(bool(search_automatically)), int(bool(enabled)), interval, _utcnow(),
    )
    with db() as conn:
        if list_id:
            exists = conn.execute("SELECT id FROM media_lists WHERE id=?", (int(list_id),)).fetchone()
            if not exists:
                raise ValueError("List definition no longer exists")
            conn.execute(
                """UPDATE media_lists SET name=?,source_type=?,source_ref=?,media_type=?,movie_destination=?,tv_destination=?,
                acquisition_strategy=?,monitor=?,search_automatically=?,enabled=?,sync_interval_hours=?,updated_at=? WHERE id=?""",
                values + (int(list_id),),
            )
            return int(list_id)
        cur = conn.execute(
            """INSERT INTO media_lists(name,source_type,source_ref,media_type,movie_destination,tv_destination,acquisition_strategy,
            monitor,search_automatically,enabled,sync_interval_hours,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            values,
        )
        return int(cur.lastrowid)


def delete_definition(list_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM media_lists WHERE id=?", (int(list_id),))


def list_runs(list_id: int | None = None, limit: int = 40) -> list[dict[str, Any]]:
    with db() as conn:
        if list_id is None:
            rows = conn.execute("SELECT * FROM media_list_runs ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM media_list_runs WHERE list_id=? ORDER BY id DESC LIMIT ?", (int(list_id), int(limit))).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["detail_json"] = json.loads(item.get("detail") or "{}")
        except Exception:
            item["detail_json"] = {}
        out.append(item)
    return out


def save_trakt_app(client_id: str, client_secret: str) -> None:
    setting_set("lists.trakt.client_id", (client_id or "").strip())
    if client_secret:
        setting_set("lists.trakt.client_secret", client_secret.strip(), True)


def save_tmdb(api_key: str) -> None:
    if api_key:
        setting_set("lists.tmdb.api_key", api_key.strip(), True)


def save_simkl(client_id: str, access_token: str) -> None:
    setting_set("lists.simkl.client_id", (client_id or "").strip())
    if access_token:
        setting_set("lists.simkl.access_token", access_token.strip(), True)


def provider_state() -> dict[str, Any]:
    plex = get_connection("plex")
    return {
        "trakt_client_id": setting_get("lists.trakt.client_id"),
        "trakt_client_configured": bool(setting_get("lists.trakt.client_id") and setting_get("lists.trakt.client_secret")),
        "trakt_connected": bool(setting_get("lists.trakt.access_token")),
        "trakt_user": setting_get("lists.trakt.username"),
        "trakt_pending": bool(setting_get("lists.trakt.pending_device_code")),
        "trakt_user_code": setting_get("lists.trakt.pending_user_code"),
        "trakt_verification_url": setting_get("lists.trakt.pending_verification_url", "https://trakt.tv/activate"),
        "trakt_pending_expires_at": setting_get("lists.trakt.pending_expires_at"),
        "tmdb_configured": bool(setting_get("lists.tmdb.api_key")),
        "simkl_client_id": setting_get("lists.simkl.client_id"),
        "simkl_configured": bool(setting_get("lists.simkl.client_id") and setting_get("lists.simkl.access_token")),
        "plex_configured": bool(plex.api_key),
    }


def _atomic_settings(values: dict[str, tuple[str, bool]]) -> None:
    """Save a related credential set in one SQLite transaction.

    Trakt refresh tokens are single-use.  The access/refresh pair must therefore
    be replaced together so a process interruption cannot persist only half of
    a successful refresh response.
    """
    with db() as conn:
        now = _utcnow()
        for key, (value, secret) in values.items():
            conn.execute(
                "INSERT INTO app_settings(key,value,secret,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,secret=excluded.secret,updated_at=excluded.updated_at",
                (key, value or "", int(bool(secret)), now),
            )


def _clear_trakt_pending() -> None:
    for key in (
        "lists.trakt.pending_device_code", "lists.trakt.pending_user_code",
        "lists.trakt.pending_verification_url", "lists.trakt.pending_expires_at",
        "lists.trakt.pending_interval", "lists.trakt.pending_next_poll",
    ):
        setting_delete(key)


async def trakt_device_begin() -> dict[str, Any]:
    """Start Trakt Device OAuth for self-hosted ArrNexus installations."""
    client_id = setting_get("lists.trakt.client_id")
    client_secret = setting_get("lists.trakt.client_secret")
    if not client_id or not client_secret:
        raise RuntimeError(
            "ArrNexus needs Trakt application credentials before Device OAuth can start. "
            "Trakt currently restricts creation of new API applications on some accounts; "
            "open Advanced Trakt application setup for details."
        )
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        r = await client.post(f"{TRAKT_AUTH}/oauth/device/code", json={"client_id": client_id})
    if r.status_code >= 400:
        raise RuntimeError(f"Trakt Device OAuth could not start: {r.status_code} {r.text[:400]}")
    data = r.json() if r.content else {}
    device_code = str(data.get("device_code") or "")
    user_code = str(data.get("user_code") or "")
    if not device_code or not user_code:
        raise RuntimeError("Trakt Device OAuth response did not contain a device code and user code")
    expires_in = max(60, int(data.get("expires_in") or 600))
    interval = max(2, int(data.get("interval") or 5))
    verification = str(data.get("verification_url") or "https://trakt.tv/activate")
    _atomic_settings({
        "lists.trakt.pending_device_code": (device_code, True),
        "lists.trakt.pending_user_code": (user_code, True),
        "lists.trakt.pending_verification_url": (verification, False),
        "lists.trakt.pending_expires_at": (str(time.time() + expires_in), False),
        "lists.trakt.pending_interval": (str(interval), False),
        "lists.trakt.pending_next_poll": ("0", False),
    })
    return {"status": "pending", "user_code": user_code, "verification_url": verification, "expires_in": expires_in, "interval": interval}


async def _load_trakt_username() -> None:
    """Best-effort account identity load after a successful Device OAuth token exchange."""
    try:
        who = await _trakt_get("/users/settings")
        username = str(((who.get("user") or {}).get("username") or "")) if isinstance(who, dict) else ""
        if username:
            setting_set("lists.trakt.username", username)
    except Exception:
        # Token success is authoritative; username decoration must never make a
        # successful account link fail.
        pass


async def trakt_device_poll() -> dict[str, Any]:
    device_code = setting_get("lists.trakt.pending_device_code")
    if not device_code:
        return {"status": "none", "message": "No Trakt device authorization is pending"}
    try:
        expires = float(setting_get("lists.trakt.pending_expires_at", "0") or 0)
    except Exception:
        expires = 0
    if expires and time.time() >= expires:
        _clear_trakt_pending()
        return {"status": "expired", "message": "The Trakt device code expired. Start a new connection."}
    try:
        next_poll = float(setting_get("lists.trakt.pending_next_poll", "0") or 0)
    except Exception:
        next_poll = 0
    if next_poll and time.time() < next_poll:
        return {"status": "waiting", "message": "Trakt asked ArrNexus to wait before checking again"}

    payload = {
        "code": device_code,
        "client_id": setting_get("lists.trakt.client_id"),
        "client_secret": setting_get("lists.trakt.client_secret"),
    }
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        r = await client.post(f"{TRAKT_AUTH}/oauth/device/token", json=payload)

    interval = max(2, int(setting_get("lists.trakt.pending_interval", "5") or 5))
    setting_set("lists.trakt.pending_next_poll", str(time.time() + interval))
    if r.status_code == 200:
        await _save_trakt_token_response(r.json())
        _clear_trakt_pending()
        await _load_trakt_username()
        return {"status": "connected"}
    if r.status_code == 400:
        return {"status": "waiting", "message": "Authorization is still pending in Trakt"}
    if r.status_code == 429:
        setting_set("lists.trakt.pending_interval", str(interval + 5))
        setting_set("lists.trakt.pending_next_poll", str(time.time() + interval + 5))
        return {"status": "waiting", "message": "Trakt requested slower polling; wait a few seconds and try again"}
    if r.status_code == 418:
        _clear_trakt_pending()
        return {"status": "denied", "message": "Trakt authorization was denied"}
    if r.status_code == 410:
        _clear_trakt_pending()
        return {"status": "expired", "message": "The Trakt device code expired"}
    if r.status_code in {404, 409}:
        _clear_trakt_pending()
        return {"status": "invalid", "message": "The Trakt device code is invalid or has already been used"}
    raise RuntimeError(f"Trakt Device OAuth failed: {r.status_code} {r.text[:400]}")


def trakt_authorize_url(redirect_uri: str, state: str) -> str:
    client_id = setting_get("lists.trakt.client_id")
    if not client_id or not setting_get("lists.trakt.client_secret"):
        raise RuntimeError("Configure the Trakt client ID and client secret first")
    return f"{TRAKT_AUTH}/oauth/authorize?" + urlencode({
        "response_type": "code", "client_id": client_id,
        "redirect_uri": redirect_uri, "state": state,
    })


async def _save_trakt_token_response(data: dict[str, Any]) -> None:
    access = str(data.get("access_token") or "")
    refresh = str(data.get("refresh_token") or "")
    if not access or not refresh:
        raise RuntimeError("Trakt token response did not contain both access and refresh tokens")
    expires_in = max(60, int(data.get("expires_in") or 604800))
    _atomic_settings({
        "lists.trakt.access_token": (access, True),
        "lists.trakt.refresh_token": (refresh, True),
        "lists.trakt.expires_at": (str(time.time() + expires_in - 60), False),
    })


async def trakt_exchange_code(code: str, redirect_uri: str) -> None:
    payload = {
        "code": code, "client_id": setting_get("lists.trakt.client_id"),
        "client_secret": setting_get("lists.trakt.client_secret"),
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        r = await client.post(f"{TRAKT_AUTH}/oauth/token", json=payload)
    if r.status_code >= 400:
        raise RuntimeError(f"Trakt OAuth exchange failed: {r.status_code} {r.text[:400]}")
    await _save_trakt_token_response(r.json())
    try:
        who = await _trakt_get("/users/settings")
        username = str(((who.get("user") or {}).get("username") or ""))
        if username:
            setting_set("lists.trakt.username", username)
    except Exception:
        pass


async def _trakt_access_token() -> str:
    token = setting_get("lists.trakt.access_token")
    try:
        expires = float(setting_get("lists.trakt.expires_at", "0") or 0)
    except Exception:
        expires = 0
    if token and (not expires or expires > time.time()):
        return token
    refresh = setting_get("lists.trakt.refresh_token")
    if not refresh:
        raise RuntimeError("Trakt is not connected")
    payload = {
        "refresh_token": refresh, "client_id": setting_get("lists.trakt.client_id"),
        "client_secret": setting_get("lists.trakt.client_secret"),
        "redirect_uri": setting_get("lists.trakt.redirect_uri"), "grant_type": "refresh_token",
    }
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        r = await client.post(f"{TRAKT_AUTH}/oauth/token", json=payload)
    if r.status_code >= 400:
        raise RuntimeError(f"Trakt token refresh failed: {r.status_code} {r.text[:400]}")
    await _save_trakt_token_response(r.json())
    return setting_get("lists.trakt.access_token")


def trakt_disconnect() -> None:
    for key in ("lists.trakt.access_token", "lists.trakt.refresh_token", "lists.trakt.expires_at", "lists.trakt.username"):
        setting_delete(key)
    _clear_trakt_pending()


async def _trakt_get(path: str) -> Any:
    token = await _trakt_access_token()
    client_id = setting_get("lists.trakt.client_id")
    headers = {
        "trakt-api-key": client_id, "trakt-api-version": "2",
        "Authorization": f"Bearer {token}", "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        r = await client.get(f"{TRAKT_API}{path}", headers=headers)
    if r.status_code >= 400:
        raise RuntimeError(f"Trakt API: {r.status_code} {r.text[:500]}")
    return r.json()


async def trakt_personal_lists() -> list[dict[str, Any]]:
    rows = await _trakt_get("/users/me/lists")
    out = []
    for row in rows or []:
        ids = row.get("ids") or {}
        out.append({"name": row.get("name") or "Untitled", "id": ids.get("trakt") or ids.get("slug"), "slug": ids.get("slug") or ""})
    return out


def _trakt_item(row: dict[str, Any], kind: str) -> NormalizedItem | None:
    obj = row.get("movie" if kind == "movie" else "show") or row
    ids = obj.get("ids") or {}
    title = str(obj.get("title") or "").strip()
    if not title:
        return None
    return NormalizedItem(
        kind, title, int(obj.get("year")) if obj.get("year") else None,
        str(ids.get("imdb") or ""), int(ids["tmdb"]) if ids.get("tmdb") else None,
        int(ids["tvdb"]) if ids.get("tvdb") else None, str(ids.get("trakt") or ""),
    )


async def _fetch_trakt(defn: dict[str, Any]) -> list[NormalizedItem]:
    media = defn.get("media_type") or "mixed"
    kinds = ["movie", "tv"] if media == "mixed" else [media]
    out: list[NormalizedItem] = []
    for kind in kinds:
        endpoint_kind = "movies" if kind == "movie" else "shows"
        if defn["source_type"] == "trakt_watchlist":
            path = f"/sync/watchlist/{endpoint_kind}?extended=full"
        else:
            ref = str(defn.get("source_ref") or "").strip()
            if not ref:
                raise RuntimeError("Trakt list ID/slug is required")
            if "/" in ref:
                user, list_ref = ref.split("/", 1)
            else:
                user, list_ref = "me", ref
            path = f"/users/{user}/lists/{list_ref}/items/{endpoint_kind}?extended=full"
        rows = await _trakt_get(path)
        for row in rows or []:
            item = _trakt_item(row, kind)
            if item:
                out.append(item)
    return _dedupe(out)


def _dedupe(items: list[NormalizedItem]) -> list[NormalizedItem]:
    out: list[NormalizedItem] = []
    seen: set[str] = set()
    for item in items:
        key = item.key()
        if key in seen:
            continue
        seen.add(key); out.append(item)
    return out


async def _fetch_tmdb(defn: dict[str, Any]) -> list[NormalizedItem]:
    key = setting_get("lists.tmdb.api_key")
    if not key:
        raise RuntimeError("TMDb API key is not configured")
    ref = str(defn.get("source_ref") or "").strip()
    if not re.fullmatch(r"\d+", ref):
        raise RuntimeError("TMDb source must be a numeric list ID")
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        r = await client.get(f"{TMDB_API}/list/{ref}", params={"api_key": key, "language": "en-GB"})
    if r.status_code >= 400:
        raise RuntimeError(f"TMDb list: {r.status_code} {r.text[:400]}")
    data = r.json(); out = []
    wanted = defn.get("media_type") or "mixed"
    for row in data.get("items") or []:
        kind = "tv" if row.get("media_type") == "tv" or (row.get("name") and not row.get("title")) else "movie"
        if wanted != "mixed" and wanted != kind:
            continue
        title = str(row.get("title") or row.get("name") or "").strip()
        date = str(row.get("release_date") or row.get("first_air_date") or "")
        year = int(date[:4]) if re.match(r"^\d{4}", date) else None
        if title:
            out.append(NormalizedItem(kind, title, year, tmdb_id=int(row["id"]) if kind == "movie" and row.get("id") else None, source_id=str(row.get("id") or "")))
    return _dedupe(out)


async def _fetch_imdb(defn: dict[str, Any]) -> list[NormalizedItem]:
    ref = str(defn.get("source_ref") or "").strip()
    if not ref:
        raise RuntimeError("IMDb list URL or ls… ID is required")
    if re.fullmatch(r"ls\d+", ref, re.I):
        url = f"https://www.imdb.com/list/{ref}/"
    elif ref.startswith("https://www.imdb.com/") or ref.startswith("http://www.imdb.com/"):
        url = ref
    else:
        raise RuntimeError("IMDb source must be an imdb.com list URL or list ID")
    headers = {"User-Agent": "Mozilla/5.0 ArrNexus/10.2", "Accept-Language": "en-GB,en;q=0.8"}
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True, headers=headers) as client:
        r = await client.get(url)
    if r.status_code >= 400:
        raise RuntimeError(f"IMDb list: {r.status_code}")
    text = r.text
    ids = []
    for value in re.findall(r"(?:/title/|\\\"id\\\":\\\")(?P<id>tt\d{7,10})", text):
        if value not in ids:
            ids.append(value)
    # Modern IMDb pages keep title text near the canonical title id in embedded JSON.
    title_by_id: dict[str, str] = {}
    for match in re.finditer(r'"id":"(tt\d{7,10})".{0,800}?"text":"([^"\\]{1,180})"', text, re.S):
        title_by_id.setdefault(match.group(1), match.group(2))
    kind = "movie" if defn.get("media_type") == "movie" else "tv" if defn.get("media_type") == "tv" else "movie"
    return [NormalizedItem(kind, title_by_id.get(i, i), imdb_id=i, source_id=i) for i in ids[:1000]]


async def _fetch_feed(defn: dict[str, Any]) -> list[NormalizedItem]:
    url = str(defn.get("source_ref") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError("RSS/Atom source must be an http(s) URL")
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": "ArrNexus/10.2"})
    if r.status_code >= 400:
        raise RuntimeError(f"Feed: {r.status_code} {r.text[:300]}")
    try:
        root = ET.fromstring(r.content)
    except Exception as exc:
        raise RuntimeError(f"Feed XML is invalid: {exc}") from exc
    out = []
    wanted = defn.get("media_type") or "mixed"
    default_kind = "tv" if wanted == "tv" else "movie"
    nodes = list(root.findall(".//item")) + list(root.findall(".//{http://www.w3.org/2005/Atom}entry"))
    for node in nodes[:2000]:
        title = node.findtext("title") or node.findtext("{http://www.w3.org/2005/Atom}title") or ""
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue
        year_match = re.search(r"(?:^|\D)(19\d{2}|20\d{2})(?:\D|$)", title)
        year = int(year_match.group(1)) if year_match else None
        out.append(NormalizedItem(default_kind, title, year, source_id=hashlib.sha1(title.encode()).hexdigest()[:16]))
    return _dedupe(out)


async def _fetch_json(defn: dict[str, Any]) -> list[NormalizedItem]:
    url = str(defn.get("source_ref") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError("Custom JSON source must be an http(s) URL")
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": "ArrNexus/10.2", "Accept": "application/json"})
    if r.status_code >= 400:
        raise RuntimeError(f"JSON list: {r.status_code} {r.text[:300]}")
    data = r.json(); rows = data.get("items") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise RuntimeError("Custom JSON must be an array or an object containing an items array")
    out=[]; wanted=defn.get("media_type") or "mixed"
    for row in rows[:5000]:
        if not isinstance(row, dict): continue
        kind = str(row.get("media_type") or row.get("type") or ("movie" if wanted == "mixed" else wanted)).lower()
        if kind in {"show", "series"}: kind = "tv"
        if kind not in {"movie", "tv"}: continue
        if wanted != "mixed" and wanted != kind: continue
        title=str(row.get("title") or row.get("name") or "").strip()
        if not title: continue
        def _ival(v):
            try: return int(v) if v not in (None, "") else None
            except Exception: return None
        out.append(NormalizedItem(kind,title,_ival(row.get("year")),str(row.get("imdb") or row.get("imdb_id") or ""),_ival(row.get("tmdb") or row.get("tmdb_id")),_ival(row.get("tvdb") or row.get("tvdb_id")),str(row.get("id") or "")))
    return _dedupe(out)


async def _fetch_plex(defn: dict[str, Any]) -> list[NormalizedItem]:
    token = get_connection("plex").api_key
    if not token:
        raise RuntimeError("Plex token is not configured in Connections")
    headers = {"X-Plex-Token": token, "Accept": "application/json", "X-Plex-Product": "ArrNexus", "X-Plex-Client-Identifier": "arrnexus-v10.2"}
    params = {"includeCollections": 1, "includeExternalMedia": 1, "includeAdvanced": 1, "X-Plex-Container-Start": 0, "X-Plex-Container-Size": 1000}
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        r = await client.get(f"{PLEX_DISCOVER}/library/sections/watchlist/all", headers=headers, params=params)
    if r.status_code >= 400:
        raise RuntimeError(f"Plex Watchlist: {r.status_code} {r.text[:300]}")
    data=r.json(); mc=data.get("MediaContainer") or {}
    rows=mc.get("Metadata") or mc.get("Video") or []
    if isinstance(rows, dict): rows=[rows]
    out=[]; wanted=defn.get("media_type") or "mixed"
    for row in rows:
        kind="tv" if str(row.get("type") or "").lower() in {"show","series"} else "movie"
        if wanted != "mixed" and wanted != kind: continue
        ids={}
        for g in row.get("Guid") or []:
            gid=str(g.get("id") or "")
            if "://" in gid:
                k,v=gid.split("://",1); ids[k]=v
        title=str(row.get("title") or "").strip()
        if title:
            try: year=int(row.get("year")) if row.get("year") else None
            except Exception: year=None
            out.append(NormalizedItem(kind,title,year,ids.get("imdb", ""),int(ids["tmdb"]) if ids.get("tmdb","").isdigit() and kind=="movie" else None,int(ids["tvdb"]) if ids.get("tvdb","").isdigit() else None,str(row.get("ratingKey") or "")))
    return _dedupe(out)


async def _fetch_simkl(defn: dict[str, Any]) -> list[NormalizedItem]:
    client_id=setting_get("lists.simkl.client_id"); token=setting_get("lists.simkl.access_token")
    if not client_id or not token:
        raise RuntimeError("Simkl client ID/access token are not configured")
    headers={"simkl-api-key":client_id,"Authorization":f"Bearer {token}","Accept":"application/json"}
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        r=await client.get(f"{SIMKL_API}/sync/all-items", headers=headers, params={"extended":"full"})
    if r.status_code >= 400:
        raise RuntimeError(f"Simkl: {r.status_code} {r.text[:300]}")
    data=r.json(); out=[]; wanted=defn.get("media_type") or "mixed"
    groups=(("movies","movie"),("shows","tv"),("anime","tv"))
    for bucket,kind in groups:
        if wanted != "mixed" and wanted != kind: continue
        for row in data.get(bucket) or []:
            status=str(row.get("status") or "").lower()
            if status not in {"plantowatch","watching","hold","notinteresting",""}: continue
            obj=row.get("movie") or row.get("show") or row
            ids=obj.get("ids") or {}; title=str(obj.get("title") or "").strip()
            if not title: continue
            def _ival(v):
                try:return int(v) if v else None
                except Exception:return None
            out.append(NormalizedItem(kind,title,_ival(obj.get("year")),str(ids.get("imdb") or ""),_ival(ids.get("tmdb")) if kind=="movie" else None,_ival(ids.get("tvdb")),str(ids.get("simkl") or "")))
    return _dedupe(out)


async def fetch_items(defn: dict[str, Any]) -> list[NormalizedItem]:
    kind = str(defn.get("source_type") or "")
    if kind in {"trakt_watchlist", "trakt_list"}: return await _fetch_trakt(defn)
    if kind == "tmdb": return await _fetch_tmdb(defn)
    if kind == "imdb": return await _fetch_imdb(defn)
    if kind == "rss": return await _fetch_feed(defn)
    if kind == "json": return await _fetch_json(defn)
    if kind == "plex_watchlist": return await _fetch_plex(defn)
    if kind == "simkl": return await _fetch_simkl(defn)
    raise RuntimeError("Unsupported list source")


def _candidate_matches(candidate: dict[str, Any], item: NormalizedItem) -> bool:
    if item.media_type == "movie":
        if item.tmdb_id and int(candidate.get("tmdbId") or 0) == int(item.tmdb_id): return True
    else:
        if item.tvdb_id and int(candidate.get("tvdbId") or 0) == int(item.tvdb_id): return True
    imdb = str(candidate.get("imdbId") or candidate.get("imdb") or "")
    if item.imdb_id and imdb and imdb == item.imdb_id: return True
    title = str(candidate.get("title") or "").strip().casefold()
    if title and title == item.title.casefold():
        cy = candidate.get("year")
        return not item.year or not cy or int(cy) == int(item.year)
    return False


async def resolve_item(item: NormalizedItem) -> dict[str, Any] | None:
    terms = [item.lookup_term()]
    plain = f"{item.title} {item.year or ''}".strip()
    if plain not in terms: terms.append(plain)
    for term in terms:
        try:
            rows = await discover_lookup(term, item.media_type)
        except Exception:
            continue
        for candidate in rows:
            if _candidate_matches(candidate, item):
                return candidate
        if rows and len(rows) == 1:
            return rows[0]
    return None


async def _already_owned(candidate: dict[str, Any], media_type: str) -> dict[str, Any] | None:
    if candidate.get("arrnexus_request"):
        return candidate.get("arrnexus_request")
    service = "radarr" if media_type == "movie" else "sonarr"
    dests = movie_roots() if media_type == "movie" else tv_roots()
    id_key = "tmdbId" if media_type == "movie" else "tvdbId"
    ext = candidate.get(id_key)
    for dest in dests:
        try:
            client, _inst = client_for_destination(service, dest)
            rows = await (client.movies() if media_type == "movie" else client.series())
            hit = next((r for r in rows if ext and r.get(id_key) == ext), None)
            if hit:
                return {"destination": dest, "arr_id": hit.get("id"), "has_file": bool(hit.get("hasFile") or (hit.get("statistics") or {}).get("episodeFileCount"))}
        except Exception:
            continue
    return None


async def preview_definition(defn: dict[str, Any], limit: int = 500) -> dict[str, Any]:
    items = (await fetch_items(defn))[:max(1, min(5000, int(limit)))]
    rows=[]; counts={"total":len(items),"existing":0,"would_add":0,"unmatched":0,"requested":0}
    sem=asyncio.Semaphore(5)
    async def one(item: NormalizedItem):
        async with sem:
            candidate=await resolve_item(item)
            if not candidate:
                return {**item.dict(),"state":"unmatched","candidate":None}
            owned=await _already_owned(candidate,item.media_type)
            if owned:
                state="existing" if owned.get("has_file") else "requested"
                return {**item.dict(),"state":state,"candidate":candidate,"owned":owned}
            return {**item.dict(),"state":"new","candidate":candidate}
    resolved=await asyncio.gather(*(one(i) for i in items), return_exceptions=True)
    for item,result in zip(items,resolved):
        if isinstance(result,Exception):
            row={**item.dict(),"state":"unmatched","error":str(result)}
        else: row=result
        rows.append(row)
        if row["state"]=="new": counts["would_add"]+=1
        elif row["state"]=="existing": counts["existing"]+=1
        elif row["state"]=="requested": counts["requested"]+=1
        else: counts["unmatched"]+=1
    return {**counts,"rows":rows,"source_count":len(items)}


async def sync_definition(defn: dict[str, Any], *, preview: bool = False, user_id: int | None = None) -> dict[str, Any]:
    preview_data=await preview_definition(defn, 5000)
    added=[]; errors=[]
    if not preview:
        for row in preview_data["rows"]:
            if row.get("state") != "new" or not row.get("candidate"):
                continue
            kind=row["media_type"]
            destination=defn.get("movie_destination") if kind=="movie" else defn.get("tv_destination")
            try:
                result=await discover_add(row["candidate"],kind,destination or "auto",search=False,user_id=user_id,monitored=bool(defn.get("monitor")))
                arr_id=int((result.get("item") or {}).get("id") or 0)
                if bool(defn.get("search_automatically")) and arr_id:
                    service="radarr" if kind=="movie" else "sonarr"
                    client,_inst=client_for_destination(service,result.get("destination") or destination or "auto")
                    strategy=str(defn.get("acquisition_strategy") or "automatic")
                    try:
                        plan=await plan_and_grab(client,kind,arr_id,strategy)
                    except Exception as exc:
                        # Preserve the Arr add even if advanced acquisition cannot find a candidate.
                        try:
                            await client.search(arr_id)
                            plan={"fallback":"native Arr search","error":str(exc)}
                        except Exception:
                            plan={"error":str(exc)}
                    result["acquisition"]=plan
                added.append({"title":row["title"],"media_type":kind,"result":result})
            except Exception as exc:
                errors.append({"title":row.get("title"),"media_type":kind,"error":str(exc)})
    detail={"rows":preview_data["rows"][:250],"added":added[:250],"errors":errors[:250]}
    status="preview" if preview else ("complete_with_errors" if errors else "complete")
    list_id=int(defn["id"])
    with db() as conn:
        conn.execute(
            """INSERT INTO media_list_runs(list_id,status,preview,total,existing_count,added_count,unmatched_count,error_count,detail)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (list_id,status,int(preview),preview_data["total"],preview_data["existing"]+preview_data["requested"],0 if preview else len(added),preview_data["unmatched"],len(errors),_json(detail)),
        )
        if not preview:
            conn.execute("UPDATE media_lists SET last_sync_at=?,last_error=?,last_count=?,updated_at=? WHERE id=?",(_utcnow(), errors[0]["error"] if errors else "", preview_data["total"], _utcnow(), list_id))
    log_event("warning" if errors else "info","lists","preview" if preview else "sync",f"{defn['name']}: {preview_data['total']} items, {len(added)} added, {len(errors)} errors",{"list_id":list_id})
    return {**preview_data,"added":len(added),"errors":errors,"status":status}


async def run_due_lists() -> list[dict[str, Any]]:
    now=time.time(); results=[]
    for defn in list_definitions():
        if not defn.get("enabled"): continue
        last=defn.get("last_sync_at")
        due=True
        if last:
            try: due=(now-datetime.fromisoformat(str(last).replace("Z","+00:00")).timestamp()) >= int(defn.get("sync_interval_hours") or 12)*3600
            except Exception: due=True
        if not due: continue
        try: results.append({"id":defn["id"],"result":await sync_definition(defn,preview=False,user_id=None)})
        except Exception as exc:
            with db() as conn: conn.execute("UPDATE media_lists SET last_sync_at=?,last_error=?,updated_at=? WHERE id=?",(_utcnow(),str(exc)[:800],_utcnow(),int(defn["id"])))
            log_event("error","lists","scheduled_sync_failed",f"{defn['name']}: {exc}",{"list_id":defn["id"]})
            results.append({"id":defn["id"],"error":str(exc)})
    return results


async def scheduler_loop() -> None:
    while True:
        try:
            await asyncio.sleep(90)
            await run_due_lists()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_event("warning","lists","scheduler_error",str(exc)[:800])
            await asyncio.sleep(300)
