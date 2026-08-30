from __future__ import annotations
import httpx
from typing import Any
from .config import settings


class ArrError(RuntimeError):
    pass


class ArrClient:
    def __init__(self, name: str, base_url: str, api_key: str, api_version: str):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_version = api_version

    @property
    def headers(self):
        return {"X-Api-Key": self.api_key}

    async def request(self, method: str, path: str, **kwargs) -> Any:
        if not self.api_key:
            raise ArrError(f"{self.name} API key is not configured")
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
            r = await client.request(method, url, headers=self.headers, **kwargs)
        if r.status_code >= 400:
            body = r.text[:800]
            raise ArrError(f"{self.name}: {r.status_code} {body}")
        if not r.content:
            return None
        return r.json()

    async def status(self):
        return await self.request("GET", f"/api/{self.api_version}/system/status")

    async def roots(self):
        return await self.request("GET", f"/api/{self.api_version}/rootfolder")

    async def tags(self):
        return await self.request("GET", f"/api/{self.api_version}/tag")

    async def quality_profiles(self):
        return await self.request("GET", f"/api/{self.api_version}/qualityprofile")

    async def command(self, payload: dict):
        return await self.request("POST", f"/api/{self.api_version}/command", json=payload)


class RadarrClient(ArrClient):
    def __init__(self):
        super().__init__("Radarr", settings.radarr_url, settings.radarr_api_key, "v3")

    async def movies(self):
        return await self.request("GET", "/api/v3/movie")

    async def lookup(self, term: str):
        return await self.request("GET", "/api/v3/movie/lookup", params={"term": term})

    async def add_movie(self, candidate: dict, root: str):
        profiles = await self.quality_profiles()
        qid = pick_named_id(profiles, settings.radarr_quality_profile_name)
        payload = dict(candidate)
        payload.pop("id", None)
        payload["qualityProfileId"] = qid
        payload["rootFolderPath"] = root
        payload["monitored"] = True
        payload["minimumAvailability"] = payload.get("minimumAvailability") or "released"
        payload["addOptions"] = {"searchForMovie": False}
        return await self.request("POST", "/api/v3/movie", json=payload)

    async def rescan(self, movie_id: int):
        return await self.command({"name": "RescanMovie", "movieId": movie_id})


class SonarrClient(ArrClient):
    def __init__(self):
        super().__init__("Sonarr", settings.sonarr_url, settings.sonarr_api_key, "v3")

    async def series(self):
        return await self.request("GET", "/api/v3/series")

    async def lookup(self, term: str):
        return await self.request("GET", "/api/v3/series/lookup", params={"term": term})

    async def add_series(self, candidate: dict, root: str):
        profiles = await self.quality_profiles()
        qid = pick_named_id(profiles, settings.sonarr_quality_profile_name)
        payload = dict(candidate)
        payload.pop("id", None)
        payload["qualityProfileId"] = qid
        payload["rootFolderPath"] = root
        payload["monitored"] = True
        payload["seasonFolder"] = True
        payload["seriesType"] = payload.get("seriesType") or "standard"
        payload["addOptions"] = {"monitor": "all", "searchForMissingEpisodes": False}
        return await self.request("POST", "/api/v3/series", json=payload)

    async def rescan(self, series_id: int):
        return await self.command({"name": "RescanSeries", "seriesId": series_id})


class LidarrClient(ArrClient):
    def __init__(self):
        super().__init__("Lidarr", settings.lidarr_url, settings.lidarr_api_key, "v1")

    async def artists(self):
        return await self.request("GET", "/api/v1/artist")

    async def artist_lookup(self, term: str):
        return await self.request("GET", "/api/v1/artist/lookup", params={"term": term})

    async def albums(self, artist_id: int | None = None):
        params = {"artistId": artist_id} if artist_id else None
        return await self.request("GET", "/api/v1/album", params=params)

    async def releases_for_album(self, album_id: int):
        return await self.request("GET", "/api/v1/release", params={"albumId": album_id})

    async def releases_for_artist(self, artist_id: int):
        return await self.request("GET", "/api/v1/release", params={"artistId": artist_id})

    async def grab_release(self, release: dict):
        return await self.request("POST", "/api/v1/release", json=release)


def pick_named_id(items: list[dict], preferred_name: str) -> int:
    if not items:
        raise ArrError("No profiles returned")
    wanted = preferred_name.strip().lower()
    for item in items:
        if str(item.get("name", "")).strip().lower() == wanted:
            return int(item["id"])
    return int(items[0]["id"])
