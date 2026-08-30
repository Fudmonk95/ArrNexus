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


def connected() -> bool:
    return bool(setting_get("rd.refresh_token") or setting_get("rd.access_token"))

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

async def torrent_info(torrent_id: str) -> dict:
    return await request("GET", f"torrents/info/{torrent_id}")

async def instant_availability(info_hash: str):
    return await request("GET", f"torrents/instantAvailability/{info_hash}")
