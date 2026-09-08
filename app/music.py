from __future__ import annotations

import asyncio
import time
from urllib.parse import urlencode, quote_plus, urlparse

import httpx

from .db import cache_get, cache_set, setting_delete, setting_get, setting_set

SPOTIFY_USER_SCOPES = (
    "user-library-read",
    "user-top-read",
    "user-read-recently-played",
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-read-private",
)




def spotify_redirect_uri(public_url: str = "", request_url: str = "") -> str:
    base = str(public_url or "").strip().rstrip("/")
    if not base and request_url:
        parsed = urlparse(str(request_url))
        base = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return f"{base}/music/spotify/callback" if base else ""


def spotify_redirect_validation(redirect_uri: str) -> tuple[bool, str]:
    uri = str(redirect_uri or "").strip()
    if not uri:
        return False, "Set an ArrNexus public URL before linking Spotify."
    try:
        parsed = urlparse(uri)
    except Exception:
        return False, "The Spotify redirect URI is invalid."
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and host:
        return True, "Ready"
    if parsed.scheme == "http" and host in {"127.0.0.1", "localhost", "::1"}:
        return True, "Ready (loopback HTTP)"
    if parsed.scheme == "http":
        return False, "Spotify requires HTTPS for non-loopback callback addresses. Configure an HTTPS ArrNexus public URL."
    return False, "Spotify requires an HTTPS callback URL (or an HTTP loopback URL)."

def beatport_search_url(query: str) -> str:
    return "https://www.beatport.com/search?q=" + quote_plus(str(query or "").strip())


def spotify_app_configured() -> bool:
    return bool(setting_get("music.spotify.client_id", "") and setting_get("music.spotify.client_secret", ""))


def spotify_user_linked(user_id: int) -> bool:
    return bool(setting_get(f"music.spotify.user.{int(user_id)}.refresh_token", ""))


def spotify_authorize_url(user_id: int, state: str, redirect_uri: str) -> str:
    cid = setting_get("music.spotify.client_id", "")
    if not cid:
        raise RuntimeError("Spotify Client ID is not configured")
    if not redirect_uri:
        raise RuntimeError("Spotify redirect URI is not configured")
    return "https://accounts.spotify.com/authorize?" + urlencode({
        "client_id": cid,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": " ".join(SPOTIFY_USER_SCOPES),
        "show_dialog": "false",
    })


async def _spotify_app_token() -> str:
    cid = setting_get("music.spotify.client_id", "")
    secret = setting_get("music.spotify.client_secret", "")
    if not cid or not secret:
        return ""
    cached = cache_get("spotify:app_token", 3600) or {}
    if isinstance(cached, dict) and cached.get("access_token") and float(cached.get("expires_at") or 0) > time.time() + 90:
        return str(cached["access_token"])
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        r = await client.post("https://accounts.spotify.com/api/token", auth=(cid, secret), data={"grant_type": "client_credentials"})
    if r.status_code >= 400:
        return ""
    data = r.json(); token = str(data.get("access_token") or "")
    if token:
        cache_set("spotify:app_token", {"access_token": token, "expires_at": time.time() + int(data.get("expires_in") or 3600)})
    return token


async def spotify_exchange_code(user_id: int, code: str, redirect_uri: str) -> dict:
    cid = setting_get("music.spotify.client_id", ""); secret = setting_get("music.spotify.client_secret", "")
    if not cid or not secret:
        raise RuntimeError("Spotify application credentials are not configured")
    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        r = await client.post("https://accounts.spotify.com/api/token", auth=(cid, secret), data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        })
    if r.status_code >= 400:
        raise RuntimeError(f"Spotify authorization failed: {r.text[:300]}")
    data = r.json(); refresh = str(data.get("refresh_token") or ""); access = str(data.get("access_token") or "")
    if refresh: setting_set(f"music.spotify.user.{int(user_id)}.refresh_token", refresh, True)
    setting_set(f"music.spotify.user.{int(user_id)}.scope", str(data.get("scope") or ""))
    if access:
        cache_set(f"spotify:user_token:{int(user_id)}", {"access_token": access, "expires_at": time.time() + int(data.get("expires_in") or 3600)})
    return data


async def spotify_user_access_token(user_id: int, force_refresh: bool = False) -> str:
    uid = int(user_id); cached = cache_get(f"spotify:user_token:{uid}", 3600) or {}
    if not force_refresh and isinstance(cached, dict) and cached.get("access_token") and float(cached.get("expires_at") or 0) > time.time() + 90:
        return str(cached["access_token"])
    refresh = setting_get(f"music.spotify.user.{uid}.refresh_token", "")
    cid = setting_get("music.spotify.client_id", ""); secret = setting_get("music.spotify.client_secret", "")
    if not refresh or not cid or not secret: return ""
    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        r = await client.post("https://accounts.spotify.com/api/token", auth=(cid, secret), data={"grant_type": "refresh_token", "refresh_token": refresh})
    if r.status_code >= 400: raise RuntimeError(f"Spotify token refresh failed (HTTP {r.status_code})")
    data = r.json(); access = str(data.get("access_token") or "")
    if data.get("refresh_token"): setting_set(f"music.spotify.user.{uid}.refresh_token", str(data["refresh_token"]), True)
    if access: cache_set(f"spotify:user_token:{uid}", {"access_token": access, "expires_at": time.time() + int(data.get("expires_in") or 3600)})
    return access


def spotify_disconnect_user(user_id: int) -> None:
    uid = int(user_id)
    for suffix in ("refresh_token", "scope", "profile_id", "profile_name"):
        setting_delete(f"music.spotify.user.{uid}.{suffix}")
    cache_set(f"spotify:user_token:{uid}", {"access_token": "", "expires_at": 0})


async def _spotify_user_get(user_id: int, path: str, params: dict | None = None) -> dict:
    token = await spotify_user_access_token(user_id)
    if not token: raise RuntimeError("Spotify account is not linked")
    async with httpx.AsyncClient(timeout=22.0, follow_redirects=True) as client:
        r = await client.get("https://api.spotify.com/v1" + path, params=params or {}, headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 401:
        token = await spotify_user_access_token(user_id, True)
        async with httpx.AsyncClient(timeout=22.0, follow_redirects=True) as client:
            r = await client.get("https://api.spotify.com/v1" + path, params=params or {}, headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 429: raise RuntimeError("Spotify rate limit reached; try again shortly")
    if r.status_code >= 400: raise RuntimeError(f"Spotify API failed (HTTP {r.status_code})")
    data = r.json(); return data if isinstance(data, dict) else {}


def _images(row: dict) -> str:
    images = row.get("images") or []; return str((images[0] or {}).get("url") or "") if images else ""


def _track(track: dict, section: str = "") -> dict:
    album=track.get("album") or {}; artists=", ".join(str(x.get("name") or "") for x in (track.get("artists") or []) if x.get("name"))
    return {"kind":"track","title":track.get("name") or "Unknown track","artist":artists,"artwork":_images(album),"external":((track.get("external_urls") or {}).get("spotify") or ""),"section":section}


def _album(album: dict, section: str = "") -> dict:
    artists=", ".join(str(x.get("name") or "") for x in (album.get("artists") or []) if x.get("name"))
    return {"kind":"album","title":album.get("name") or "Unknown album","artist":artists,"artwork":_images(album),"external":((album.get("external_urls") or {}).get("spotify") or ""),"section":section}


def _artist(artist: dict, section: str = "") -> dict:
    return {"kind":"artist","title":artist.get("name") or "Unknown artist","artist":artist.get("name") or "","artwork":_images(artist),"external":((artist.get("external_urls") or {}).get("spotify") or ""),"section":section}


async def spotify_user_hub(user_id: int) -> dict:
    if not spotify_user_linked(user_id):
        return {"linked":False,"profile":{},"saved_tracks":[],"saved_albums":[],"playlists":[],"top_tracks":[],"top_artists":[],"recent":[],"errors":[]}
    tasks = {
        "profile": _spotify_user_get(user_id, "/me"),
        "saved_tracks": _spotify_user_get(user_id, "/me/tracks", {"limit": 20}),
        "saved_albums": _spotify_user_get(user_id, "/me/albums", {"limit": 20}),
        "playlists": _spotify_user_get(user_id, "/me/playlists", {"limit": 20}),
        "top_tracks": _spotify_user_get(user_id, "/me/top/tracks", {"time_range":"medium_term","limit":20}),
        "top_artists": _spotify_user_get(user_id, "/me/top/artists", {"time_range":"medium_term","limit":20}),
        "recent": _spotify_user_get(user_id, "/me/player/recently-played", {"limit":20}),
    }
    names=list(tasks); vals=await asyncio.gather(*(tasks[n] for n in names), return_exceptions=True); raw=dict(zip(names, vals)); errors=[]
    for name,value in list(raw.items()):
        if isinstance(value,Exception): errors.append(f"{name}: {value}"); raw[name]={}
    profile=raw["profile"] or {}
    return {
        "linked":True,"profile":profile,
        "saved_tracks":[_track(x.get("track") or {},"saved") for x in raw["saved_tracks"].get("items",[]) if (x.get("track") or {}).get("id")],
        "saved_albums":[_album(x.get("album") or {},"saved") for x in raw["saved_albums"].get("items",[]) if (x.get("album") or {}).get("id")],
        "playlists":[{"kind":"playlist","title":x.get("name") or "Playlist","artist":((x.get("owner") or {}).get("display_name") or ""),"artwork":_images(x),"external":((x.get("external_urls") or {}).get("spotify") or "")} for x in raw["playlists"].get("items",[]) if isinstance(x,dict)],
        "top_tracks":[_track(x,"top") for x in raw["top_tracks"].get("items",[])],
        "top_artists":[_artist(x,"top") for x in raw["top_artists"].get("items",[])],
        "recent":[_track(x.get("track") or {},"recent") for x in raw["recent"].get("items",[]) if (x.get("track") or {}).get("id")],
        "errors":errors,
    }


async def spotify_search(term: str, kind: str = "album", count: int = 20) -> list[dict]:
    term=str(term or "").strip()
    if not term: return []
    token=await _spotify_app_token()
    if not token: return []
    typ="artist" if kind=="artist" else "album"
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        r=await client.get("https://api.spotify.com/v1/search", params={"q":term,"type":typ,"market":setting_get("music.spotify.market","GB"),"limit":min(50,count)}, headers={"Authorization":f"Bearer {token}"})
    if r.status_code>=400: return []
    data=r.json(); rows=((data.get("artists") if typ=="artist" else data.get("albums")) or {}).get("items") or []
    return [(_artist(x) if typ=="artist" else _album(x)) for x in rows[:count]]
