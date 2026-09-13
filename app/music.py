from __future__ import annotations

import asyncio
import ipaddress
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


def _loopback_host(host: str) -> bool:
    host = str(host or "").strip("[]").lower()
    if host in {"127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except Exception:
        return False


def _dns_hostname(host: str) -> bool:
    host = str(host or "").strip("[]").lower()
    if not host or host == "localhost":
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except Exception:
        return "." in host


def _valid_spotify_base(base: str) -> str:
    value = str(base or "").strip().rstrip("/")
    if not value:
        return ""
    try:
        parsed = urlparse(value)
    except Exception:
        return ""
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and host:
        return value
    if parsed.scheme == "http" and _loopback_host(host):
        return value
    return ""


def spotify_redirect_uri(public_url: str = "", request_url: str = "") -> str:
    """Return a Spotify-compliant callback URI.

    Spotify requires HTTPS for every non-loopback callback. ArrNexus may sit
    behind Cloudflare/reverse proxies where the internal request arrives over
    HTTP, so an insecure LAN/public_url must never win over the configured
    dashboard public domain or a public DNS hostname.
    """
    explicit = _valid_spotify_base(public_url)
    if explicit:
        return f"{explicit}/music/spotify/callback"

    domain = setting_get("dashboard.public_domain", "").strip().lower()
    if domain:
        domain = domain.removeprefix("https://").removeprefix("http://").strip("/")
        if domain:
            return f"https://arrnexus.{domain}/music/spotify/callback"

    if request_url:
        try:
            parsed = urlparse(str(request_url))
            host = (parsed.hostname or "").lower()
            if parsed.scheme == "https" and parsed.netloc:
                return f"https://{parsed.netloc}/music/spotify/callback"
            if parsed.scheme == "http" and _loopback_host(host) and parsed.netloc:
                return f"http://{parsed.netloc}/music/spotify/callback"
            # Reverse proxies commonly terminate TLS before ArrNexus. If the
            # browser reached a proper DNS hostname, prefer the external HTTPS
            # form rather than leaking the internal HTTP scheme into OAuth.
            if parsed.netloc and _dns_hostname(host):
                return f"https://{parsed.netloc}/music/spotify/callback"
        except Exception:
            pass
    return ""


def spotify_redirect_validation(redirect_uri: str) -> tuple[bool, str]:
    uri = str(redirect_uri or "").strip()
    if not uri:
        return False, "Set an HTTPS ArrNexus public URL or Dashboard public domain before linking Spotify."
    try:
        parsed = urlparse(uri)
    except Exception:
        return False, "The Spotify redirect URI is invalid."
    host = (parsed.hostname or "").lower()
    if parsed.path.rstrip("/") != "/music/spotify/callback":
        return False, "The Spotify callback path must be /music/spotify/callback."
    if parsed.scheme == "https" and host:
        return True, "Ready — register this exact HTTPS URI in Spotify."
    if parsed.scheme == "http" and _loopback_host(host):
        return True, "Ready (loopback HTTP)."
    if host == "localhost":
        return False, "Spotify no longer accepts localhost; use 127.0.0.1 for loopback testing or your HTTPS ArrNexus hostname."
    if parsed.scheme == "http":
        return False, "Spotify requires HTTPS for non-loopback callback addresses. Configure your ArrNexus Cloudflare/reverse-proxy hostname."
    return False, "Spotify requires an HTTPS callback URL (or an HTTP loopback IP literal)."


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
    ok, message = spotify_redirect_validation(redirect_uri)
    if not ok:
        raise RuntimeError(message)
    return "https://accounts.spotify.com/authorize?" + urlencode({
        "client_id": cid,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": " ".join(SPOTIFY_USER_SCOPES),
        "show_dialog": "false",
    })


async def spotify_app_diagnostics() -> dict:
    """Validate the Spotify application credentials without exposing them."""
    cid = setting_get("music.spotify.client_id", "").strip()
    secret = setting_get("music.spotify.client_secret", "").strip()
    if not cid or not secret:
        return {"ok": False, "status": "not_configured", "message": "Spotify Client ID and Client Secret are required."}
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.post(
                "https://accounts.spotify.com/api/token",
                auth=(cid, secret),
                data={"grant_type": "client_credentials"},
            )
        if response.status_code >= 400:
            detail = ""
            try:
                payload = response.json()
                detail = str(payload.get("error_description") or payload.get("error") or "")
            except Exception:
                detail = response.text[:180]
            return {
                "ok": False,
                "status": "credentials_rejected",
                "http_status": response.status_code,
                "message": detail or f"Spotify rejected the application credentials (HTTP {response.status_code}).",
            }
        payload = response.json()
        return {
            "ok": bool(payload.get("access_token")),
            "status": "ready",
            "message": "Spotify application credentials are valid.",
            "expires_in": int(payload.get("expires_in") or 0),
        }
    except Exception as exc:
        return {"ok": False, "status": "network_error", "message": f"Could not reach Spotify: {exc}"}


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
    ok, message = spotify_redirect_validation(redirect_uri)
    if not ok:
        raise RuntimeError(message)
    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        r = await client.post("https://accounts.spotify.com/api/token", auth=(cid, secret), data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        })
    if r.status_code >= 400:
        detail = r.text[:300]
        try:
            payload = r.json()
            detail = str(payload.get("error_description") or payload.get("error") or detail)
        except Exception:
            pass
        raise RuntimeError(f"Spotify authorization failed: {detail}")
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
