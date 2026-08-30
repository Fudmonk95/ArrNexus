from __future__ import annotations

"""Managed AIOMetadata connection for ArrNexus v10.2.

AIOMetadata exposes a public /health endpoint and per-user configuration APIs.
ArrNexus keeps the remote service authoritative: it verifies, loads and masks
configuration for visibility but does not invent or overwrite provider settings.
"""

import json
import re
from typing import Any
from urllib.parse import urlparse
import httpx

from .db import setting_get, setting_set
from . import aiostreams

URL_KEY = "aiometadata.url"
USER_KEY = "aiometadata.user_uuid"
PASSWORD_KEY = "aiometadata.password"
MANIFEST_KEY = "aiometadata.manifest_url"

_SENSITIVE = re.compile(r"(?:password|secret|token|api[_-]?key|apikey|authorization|credential|cookie)", re.I)


def _base(value: str) -> str:
    raw=(value or "").strip().rstrip("/")
    if not raw: return ""
    p=urlparse(raw)
    if p.scheme not in {"http","https"} or not p.netloc:
        raise ValueError("AIOMetadata URL must be a complete http(s) URL")
    if p.username or p.password:
        raise ValueError("Do not embed credentials in the AIOMetadata URL")
    return raw


def save_connection(url: str, user_uuid: str = "", password: str = "", manifest_url: str = "") -> None:
    clean=_base(url)
    setting_set(URL_KEY,clean)
    setting_set(USER_KEY,(user_uuid or "").strip())
    if password: setting_set(PASSWORD_KEY,password,True)
    if manifest_url:
        m=(manifest_url or "").strip()
        if not m.startswith(("http://","https://")):
            raise ValueError("Manifest URL must be a complete http(s) URL")
        setting_set(MANIFEST_KEY,m,True)


def connection(mask: bool = True) -> dict[str,Any]:
    user=setting_get(USER_KEY)
    return {
        "url":setting_get(URL_KEY),
        "user_uuid": (user[:4]+"…"+user[-4:]) if mask and len(user)>10 else ("••••••••" if mask and user else user),
        "configured":bool(setting_get(URL_KEY)),
        "has_user":bool(user),"has_password":bool(setting_get(PASSWORD_KEY)),
        "has_manifest":bool(setting_get(MANIFEST_KEY)),
    }


def sanitize(value: Any, key: str = "") -> Any:
    if _SENSITIVE.search(str(key or "")):
        return "********" if value not in (None,"",False) else value
    if isinstance(value,dict): return {k:sanitize(v,k) for k,v in value.items()}
    if isinstance(value,list): return [sanitize(v,key) for v in value]
    if isinstance(value,str) and len(value)>5000: return value[:5000]+"…"
    return value


async def health() -> dict[str,Any]:
    base=_base(setting_get(URL_KEY))
    if not base: return {"configured":False,"ok":False,"reason":"AIOMetadata URL is not configured"}
    async with httpx.AsyncClient(timeout=20.0,follow_redirects=True) as client:
        try:r=await client.get(base+"/health",headers={"Accept":"application/json"})
        except Exception as exc:return {"configured":True,"ok":False,"reason":str(exc)}
    body=None
    try:body=r.json()
    except Exception:body=(r.text or "")[:500]
    return {"configured":True,"ok":r.status_code<400,"status_code":r.status_code,"body":sanitize(body),"reason":"" if r.status_code<400 else f"HTTP {r.status_code}"}


async def load_user_config() -> dict[str,Any]:
    base=_base(setting_get(URL_KEY)); user=setting_get(USER_KEY).strip(); password=setting_get(PASSWORD_KEY)
    if not base or not user: return {"ok":False,"reason":"Configure AIOMetadata URL and user UUID/alias first"}
    payload={}
    if password: payload["password"]=password
    async with httpx.AsyncClient(timeout=30.0,follow_redirects=True) as client:
        r=await client.post(f"{base}/api/config/load/{user}",json=payload,headers={"Accept":"application/json"})
    if r.status_code>=400:
        return {"ok":False,"status_code":r.status_code,"reason":f"AIOMetadata config API returned HTTP {r.status_code}","detail":(r.text or "")[:300]}
    try:data=r.json()
    except Exception:return {"ok":False,"status_code":r.status_code,"reason":"AIOMetadata config API returned non-JSON data"}
    return {"ok":True,"status_code":r.status_code,"config":sanitize(data)}


async def manifest() -> dict[str,Any]:
    url=setting_get(MANIFEST_KEY).strip()
    if not url:
        return {"ok":False,"reason":"No explicit AIOMetadata manifest URL is saved. ArrNexus will not invent a compressed config URL."}
    async with httpx.AsyncClient(timeout=30.0,follow_redirects=True) as client:
        r=await client.get(url,headers={"Accept":"application/json"})
    if r.status_code>=400:return {"ok":False,"status_code":r.status_code,"reason":f"Manifest returned HTTP {r.status_code}"}
    try:data=r.json()
    except Exception:return {"ok":False,"reason":"Manifest did not return JSON"}
    return {"ok":True,"status_code":r.status_code,"manifest":sanitize(data)}


def _find_urls(value: Any) -> list[str]:
    out=[]
    if isinstance(value,dict):
        for v in value.values():out.extend(_find_urls(v))
    elif isinstance(value,list):
        for v in value:out.extend(_find_urls(v))
    elif isinstance(value,str) and value.startswith(("http://","https://")):
        out.append(value)
    return out


async def aiostreams_relationship() -> dict[str,Any]:
    """Report whether the current AIOStreams user config references AIOMetadata."""
    aio=aiostreams.connection_settings(mask=False)
    base=_base(setting_get(URL_KEY)) if setting_get(URL_KEY) else ""
    if not aio.get("configured"):
        return {"configured":False,"linked":False,"reason":"AIOStreams is not configured in ArrNexus"}
    try:remote=await aiostreams.get_user(raw=True)
    except Exception as exc:return {"configured":True,"linked":False,"reason":f"AIOStreams config could not be read: {exc}"}
    urls=_find_urls(remote)
    host=urlparse(base).netloc.casefold() if base else ""
    matches=[u for u in urls if host and urlparse(u).netloc.casefold()==host]
    return {"configured":True,"linked":bool(matches),"matches":[sanitize(x) for x in matches[:10]],"reason":"AIOMetadata URL found in AIOStreams user configuration" if matches else "No AIOMetadata URL found in the current AIOStreams user configuration"}


async def page_state() -> dict[str,Any]:
    h=await health()
    cfg={"ok":False,"reason":"User config not requested"}
    if h.get("ok") and setting_get(USER_KEY):
        try:cfg=await load_user_config()
        except Exception as exc:cfg={"ok":False,"reason":str(exc)}
    rel=await aiostreams_relationship()
    return {"connection":connection(),"health":h,"user_config":cfg,"relationship":rel,"manifest_saved":bool(setting_get(MANIFEST_KEY))}
