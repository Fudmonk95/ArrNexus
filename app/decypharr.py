from __future__ import annotations
from typing import Any
import httpx
from .ecosystem import connector_config

class DecypharrError(RuntimeError): pass

class DecypharrClient:
    def __init__(self):
        cfg=connector_config('decypharr'); self.url=str(cfg.get('url') or '').rstrip('/'); self.token=str(cfg.get('api_key') or ''); self.enabled=bool(cfg.get('enabled'))
    def _require(self):
        if not self.enabled: raise DecypharrError('Decypharr connector is disabled')
        if not self.url: raise DecypharrError('Decypharr URL is not configured')
        if not self.token: raise DecypharrError('Decypharr API token is not configured')
    @property
    def headers(self): return {'Authorization':f'Bearer {self.token}','User-Agent':'ArrNexus/6.1'}
    async def get(self,path:str):
        self._require()
        async with httpx.AsyncClient(timeout=15.0,follow_redirects=True,headers=self.headers) as c: r=await c.get(self.url+path)
        if r.status_code in {401,403}: raise DecypharrError('Decypharr rejected the API token')
        if r.status_code>=400: raise DecypharrError(f'Decypharr HTTP {r.status_code}: {r.text[:250]}')
        try: return r.json()
        except Exception: return {'result':r.text}
    async def version(self):
        # version is intentionally public, but the page also calls protected APIs.
        if not self.url: raise DecypharrError('Decypharr URL is not configured')
        async with httpx.AsyncClient(timeout=8.0,follow_redirects=True,headers={'User-Agent':'ArrNexus/6.1'}) as c: r=await c.get(self.url+'/version')
        if r.status_code>=400: raise DecypharrError(f'Version HTTP {r.status_code}')
        try: return r.json()
        except Exception: return {'version':r.text.strip()}
    async def torrents(self): return await self.get('/api/torrents')
    async def repair_status(self): return await self.get('/api/repair/status')
    async def arrs(self): return await self.get('/api/arrs')
    async def repair_health(self): return await self.get('/api/repair/health')
