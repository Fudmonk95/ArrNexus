from __future__ import annotations

import copy
import time
from typing import Any

import httpx

from .config import settings
from .connections import get_connection

_HTTP: httpx.AsyncClient | None = None
_CACHE: dict[tuple, tuple[float, Any]] = {}
_CACHE_TTL = 2.0


def _client() -> httpx.AsyncClient:
    global _HTTP
    if _HTTP is None or _HTTP.is_closed:
        _HTTP = httpx.AsyncClient(
            timeout=httpx.Timeout(45.0, connect=8.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=15),
        )
    return _HTTP


class ArrError(RuntimeError):
    pass


class ArrClient:
    def __init__(self, name: str, base_url: str, api_key: str, api_version: str):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_version = api_version

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def request(self, method: str, path: str, **kwargs) -> Any:
        if not self.configured:
            raise ArrError(f"{self.name} is not configured")
        method = method.upper()
        url = f"{self.base_url}{path}"
        cache_key = None
        if method == "GET":
            params = tuple(sorted((str(k), str(v)) for k, v in (kwargs.get("params") or {}).items()))
            cache_key = (self.name, url, params, self.api_key[-6:])
            cached = _CACHE.get(cache_key)
            if cached and time.monotonic() - cached[0] < _CACHE_TTL:
                return copy.deepcopy(cached[1])
        else:
            for key in [x for x in _CACHE if x[0] == self.name]:
                _CACHE.pop(key, None)
        response = await _client().request(method, url, headers={"X-Api-Key": self.api_key}, **kwargs)
        if response.status_code >= 400:
            raise ArrError(f"{self.name}: HTTP {response.status_code}: {response.text[:800]}")
        if not response.content:
            data = None
        else:
            try:
                data = response.json()
            except Exception:
                data = response.text
        if cache_key is not None:
            _CACHE[cache_key] = (time.monotonic(), copy.deepcopy(data))
        return data

    async def status(self):
        return await self.request("GET", f"/api/{self.api_version}/system/status")

    async def roots(self):
        return await self.request("GET", f"/api/{self.api_version}/rootfolder")

    async def quality_profiles(self):
        return await self.request("GET", f"/api/{self.api_version}/qualityprofile")

    async def queue(self, page_size: int = 200):
        return await self.request(
            "GET",
            f"/api/{self.api_version}/queue",
            params={"page": 1, "pageSize": page_size, "includeUnknownMovieItems": True, "includeUnknownSeriesItems": True},
        )

    async def command(self, payload: dict):
        return await self.request("POST", f"/api/{self.api_version}/command", json=payload)


class RadarrClient(ArrClient):
    def __init__(self):
        c = get_connection("radarr")
        super().__init__("Radarr", c.url, c.api_key, "v3")

    async def movies(self): return await self.request("GET", "/api/v3/movie")
    async def movie(self, item_id: int): return await self.request("GET", f"/api/v3/movie/{int(item_id)}")
    async def lookup(self, term: str): return await self.request("GET", "/api/v3/movie/lookup", params={"term": term})
    async def search(self, item_id: int): return await self.command({"name": "MoviesSearch", "movieIds": [int(item_id)]})
    async def history(self, page_size: int = 200):
        return await self.request("GET", "/api/v3/history", params={"page": 1, "pageSize": page_size, "sortKey": "date", "sortDirection": "descending"})

    async def add_movie(self, candidate: dict, root: str, search: bool = True, monitored: bool = True):
        payload = copy.deepcopy(candidate)
        payload.pop("id", None)
        payload["qualityProfileId"] = pick_named_id(await self.quality_profiles(), settings.radarr_quality_profile_name)
        payload["rootFolderPath"] = root
        payload["monitored"] = bool(monitored)
        payload["minimumAvailability"] = payload.get("minimumAvailability") or "released"
        payload["addOptions"] = {"searchForMovie": bool(search)}
        return await self.request("POST", "/api/v3/movie", json=payload)


class SonarrClient(ArrClient):
    def __init__(self):
        c = get_connection("sonarr")
        super().__init__("Sonarr", c.url, c.api_key, "v3")

    async def series(self): return await self.request("GET", "/api/v3/series")
    async def series_by_id(self, item_id: int): return await self.request("GET", f"/api/v3/series/{int(item_id)}")
    async def lookup(self, term: str): return await self.request("GET", "/api/v3/series/lookup", params={"term": term})
    async def search(self, item_id: int): return await self.command({"name": "SeriesSearch", "seriesId": int(item_id)})
    async def history(self, page_size: int = 200):
        return await self.request("GET", "/api/v3/history", params={"page": 1, "pageSize": page_size, "sortKey": "date", "sortDirection": "descending"})

    async def add_series(self, candidate: dict, root: str, search: bool = True, monitored: bool = True):
        payload = copy.deepcopy(candidate)
        payload.pop("id", None)
        payload["qualityProfileId"] = pick_named_id(await self.quality_profiles(), settings.sonarr_quality_profile_name)
        payload["rootFolderPath"] = root
        payload["monitored"] = bool(monitored)
        payload["seasonFolder"] = True
        payload["seriesType"] = payload.get("seriesType") or "standard"
        payload["addOptions"] = {"monitor": "all" if monitored else "none", "searchForMissingEpisodes": bool(search)}
        return await self.request("POST", "/api/v3/series", json=payload)


class LidarrClient(ArrClient):
    def __init__(self):
        c = get_connection("lidarr")
        super().__init__("Lidarr", c.url, c.api_key, "v1")

    async def artists(self): return await self.request("GET", "/api/v1/artist")
    async def artist_lookup(self, term: str): return await self.request("GET", "/api/v1/artist/lookup", params={"term": term})
    async def albums(self, artist_id: int | None = None):
        return await self.request("GET", "/api/v1/album", params={"artistId": artist_id} if artist_id else None)
    async def metadata_profiles(self): return await self.request("GET", "/api/v1/metadataprofile")

    async def add_artist(self, candidate: dict, root: str, search: bool = True):
        payload = copy.deepcopy(candidate)
        payload.pop("id", None)
        payload["qualityProfileId"] = pick_named_id(await self.quality_profiles(), settings.lidarr_quality_profile_name)
        payload["metadataProfileId"] = pick_named_id(await self.metadata_profiles(), settings.lidarr_metadata_profile_name)
        payload["rootFolderPath"] = root
        payload["monitored"] = True
        payload["monitorNewItems"] = payload.get("monitorNewItems") or "all"
        payload["addOptions"] = {"monitor": "all", "searchForMissingAlbums": bool(search)}
        return await self.request("POST", "/api/v1/artist", json=payload)


class ProwlarrClient(ArrClient):
    def __init__(self):
        c = get_connection("prowlarr")
        super().__init__("Prowlarr", c.url, c.api_key, "v1")

    async def indexers(self): return await self.request("GET", "/api/v1/indexer")


def pick_named_id(items: list[dict], preferred_name: str) -> int:
    if not items:
        raise ArrError("No profiles returned")
    wanted = preferred_name.strip().lower()
    for item in items:
        if str(item.get("name", "")).strip().lower() == wanted:
            return int(item["id"])
    return int(items[0]["id"])
