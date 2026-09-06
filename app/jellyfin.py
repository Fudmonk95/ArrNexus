from __future__ import annotations

from urllib.parse import quote
import httpx

from .connections import get_connection

_HTTP: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _HTTP
    if _HTTP is None or _HTTP.is_closed:
        _HTTP = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=6.0), follow_redirects=True)
    return _HTTP


class JellyfinError(RuntimeError):
    pass


class JellyfinClient:
    def __init__(self):
        c = get_connection("jellyfin")
        self.base_url = c.url.rstrip("/")
        self.api_key = c.api_key

    @property
    def configured(self):
        return bool(self.base_url and self.api_key)

    async def request(self, method: str, path: str, **kwargs):
        if not self.configured:
            raise JellyfinError("Jellyfin is not configured")
        r = await _client().request(method, self.base_url + path, headers={"Authorization": f'MediaBrowser Token="{self.api_key}"'}, **kwargs)
        if r.status_code >= 400:
            raise JellyfinError(f"Jellyfin: HTTP {r.status_code}: {r.text[:500]}")
        if not r.content:
            return {}
        try: return r.json()
        except Exception: return {}

    async def status(self):
        return await self.request("GET", "/System/Info/Public")

    async def libraries(self):
        data = await self.request("GET", "/Library/VirtualFolders")
        return [{"name": x.get("Name") or "", "id": x.get("ItemId") or ""} for x in data or []]

    async def search(self, term: str, limit: int = 20):
        data = await self.request("GET", "/Items", params={"SearchTerm": term, "Recursive": "true", "Limit": limit, "Fields": "ProviderIds,Path"})
        return data.get("Items") or []

    async def inventory(self, library_name: str = ""):
        libraries = await self.libraries()
        library = next((x for x in libraries if x["name"].casefold() == library_name.casefold()), None) if library_name else None
        params = {"Recursive":"true","IncludeItemTypes":"Movie,Series","Fields":"ProviderIds","Limit":100000}
        if library and library.get("id"): params["ParentId"] = library["id"]
        data = await self.request("GET", "/Items", params=params)
        out=[]
        for item in data.get("Items") or []:
            ids=item.get("ProviderIds") or {}
            out.append({
                "id":str(item.get("Id") or ""),
                "title":item.get("Name") or "",
                "media_type":"tv" if item.get("Type") == "Series" else "movie",
                "tmdb_id":ids.get("Tmdb"),"tvdb_id":ids.get("Tvdb"),"imdb_id":ids.get("Imdb"),
            })
        return out

    async def collection(self, name: str):
        data=await self.request("GET", "/Items", params={"Recursive":"true","IncludeItemTypes":"BoxSet","SearchTerm":name,"Limit":1000})
        return next((x for x in data.get("Items") or [] if str(x.get("Name") or "").casefold()==name.casefold()), None)

    async def member_ids(self, collection_id: str):
        data=await self.request("GET", "/Items", params={"ParentId":collection_id,"Recursive":"true","Limit":100000})
        return {str(x.get("Id") or "") for x in data.get("Items") or []}

    async def apply_collection(self, name: str, library_name: str, desired: list[dict]):
        inventory=await self.inventory(library_name)
        def key(x):
            if x.get("tmdb_id"): return f"tmdb:{x['tmdb_id']}"
            if x.get("tvdb_id"): return f"tvdb:{x['tvdb_id']}"
            if x.get("imdb_id"): return f"imdb:{x['imdb_id']}"
            return "title:" + str(x.get("title") or "").casefold()
        by={key(x):x for x in inventory}
        matched=[by[key(x)] for x in desired if key(x) in by]
        missing=[x for x in desired if key(x) not in by]
        ids=[x["id"] for x in matched if x.get("id")]
        collection=await self.collection(name)
        if not collection:
            libraries=await self.libraries(); library=next((x for x in libraries if x["name"].casefold()==library_name.casefold()),None) if library_name else None
            created=await self.request("POST", "/Collections", params={"Name":name,"ParentId":(library or {}).get("id", ""),"Ids":",".join(ids),"IsLocked":"false"})
            collection_id=str(created.get("Id") or created.get("id") or "")
            added=len(ids)
        else:
            collection_id=str(collection.get("Id") or "")
            existing=await self.member_ids(collection_id)
            to_add=[x for x in ids if x not in existing]
            if to_add:
                await self.request("POST", f"/Collections/{quote(collection_id)}/Items", params={"Ids":",".join(to_add)})
            added=len(to_add)
        return {"ok":True,"collection_id":collection_id,"matched":len(matched),"added":added,"missing":missing}
