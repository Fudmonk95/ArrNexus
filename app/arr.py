from __future__ import annotations
import copy
import httpx
import time
from typing import Any
from .config import settings
from .paths import lidarr_root
from .connections import get_connection

_HTTP_CLIENT: httpx.AsyncClient | None = None
_GET_CACHE: dict[tuple, tuple[float, object]] = {}
_GET_CACHE_TTL = 3.0

def _http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=8.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20, keepalive_expiry=30.0),
        )
    return _HTTP_CLIENT


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
        method = method.upper()
        url = f"{self.base_url}{path}"
        cache_key = None
        if method == "GET":
            params = kwargs.get("params") or {}
            try:
                frozen_params = tuple(sorted((str(k), str(v)) for k, v in params.items()))
            except Exception:
                frozen_params = (str(params),)
            cache_key = (url, frozen_params, self.api_key[-6:])
            cached = _GET_CACHE.get(cache_key)
            if cached and time.monotonic() - cached[0] < _GET_CACHE_TTL:
                return copy.deepcopy(cached[1])
        else:
            # Any mutation can change queue/library state. Keep the tiny cache
            # honest by evicting this service's GET responses immediately.
            for key in [k for k in _GET_CACHE if str(k[0]).startswith(self.base_url)]:
                _GET_CACHE.pop(key, None)

        client = _http_client()
        r = await client.request(method, url, headers=self.headers, **kwargs)
        if r.status_code >= 400:
            body = r.text[:1200]
            raise ArrError(f"{self.name}: {r.status_code} {body}")
        if not r.content:
            data = None
        else:
            ctype = r.headers.get("content-type", "")
            if "json" in ctype:
                data = r.json()
            else:
                try:
                    data = r.json()
                except Exception:
                    data = r.text
        if cache_key is not None:
            _GET_CACHE[cache_key] = (time.monotonic(), copy.deepcopy(data))
            if len(_GET_CACHE) > 300:
                oldest = sorted(_GET_CACHE.items(), key=lambda kv: kv[1][0])[:80]
                for key, _ in oldest:
                    _GET_CACHE.pop(key, None)
        return data

    async def status(self):
        return await self.request("GET", f"/api/{self.api_version}/system/status")

    async def roots(self):
        return await self.request("GET", f"/api/{self.api_version}/rootfolder")

    async def tags(self):
        return await self.request("GET", f"/api/{self.api_version}/tag")

    async def quality_profiles(self):
        return await self.request("GET", f"/api/{self.api_version}/qualityprofile")

    async def queue(self, page_size: int = 100):
        return await self.request("GET", f"/api/{self.api_version}/queue", params={"page": 1, "pageSize": page_size, "includeUnknownMovieItems": True, "includeUnknownSeriesItems": True})

    async def command(self, payload: dict):
        return await self.request("POST", f"/api/{self.api_version}/command", json=payload)

    async def grab_release(self, release: dict):
        """Grab one interactive-search release through the Arr.

        Radarr/Sonarr then route the release to the correct protocol-specific
        download client (InfiniDysk/SAB for Usenet, Decypharr/qBittorrent for
        torrent/debrid) using their normal rules and tags.
        """
        return await self.request("POST", f"/api/{self.api_version}/release", json=release)


class RadarrClient(ArrClient):
    def __init__(self, base_url: str | None = None, api_key: str | None = None, name: str = "Radarr"):
        c = get_connection("radarr")
        super().__init__(name, base_url or c.url, api_key if api_key is not None else c.api_key, "v3")

    async def movies(self):
        return await self.request("GET", "/api/v3/movie")

    async def movie(self, movie_id: int):
        return await self.request("GET", f"/api/v3/movie/{movie_id}")

    async def lookup(self, term: str):
        return await self.request("GET", "/api/v3/movie/lookup", params={"term": term})

    async def add_movie(self, candidate: dict, root: str, search: bool = False):
        profiles = await self.quality_profiles()
        qid = pick_named_id(profiles, settings.radarr_quality_profile_name)
        payload = copy.deepcopy(candidate)
        payload.pop("id", None)
        payload["qualityProfileId"] = qid
        payload["rootFolderPath"] = root
        payload["monitored"] = True
        payload["minimumAvailability"] = payload.get("minimumAvailability") or "released"
        payload["addOptions"] = {"searchForMovie": bool(search)}
        return await self.request("POST", "/api/v3/movie", json=payload)

    async def rescan(self, movie_id: int):
        return await self.command({"name": "RescanMovie", "movieId": movie_id})

    async def search(self, movie_id: int):
        return await self.command({"name": "MoviesSearch", "movieIds": [movie_id]})

    async def releases(self, movie_id: int):
        return await self.request("GET", "/api/v3/release", params={"movieId": movie_id})


class SonarrClient(ArrClient):
    def __init__(self, base_url: str | None = None, api_key: str | None = None, name: str = "Sonarr"):
        c = get_connection("sonarr")
        super().__init__(name, base_url or c.url, api_key if api_key is not None else c.api_key, "v3")

    async def series(self):
        return await self.request("GET", "/api/v3/series")

    async def series_by_id(self, series_id: int):
        return await self.request("GET", f"/api/v3/series/{series_id}")

    async def lookup(self, term: str):
        return await self.request("GET", "/api/v3/series/lookup", params={"term": term})

    async def add_series(self, candidate: dict, root: str, search: bool = False):
        profiles = await self.quality_profiles()
        qid = pick_named_id(profiles, settings.sonarr_quality_profile_name)
        payload = copy.deepcopy(candidate)
        payload.pop("id", None)
        payload["qualityProfileId"] = qid
        payload["rootFolderPath"] = root
        payload["monitored"] = True
        payload["seasonFolder"] = True
        payload["seriesType"] = payload.get("seriesType") or "standard"
        payload["addOptions"] = {"monitor": "all", "searchForMissingEpisodes": bool(search)}
        return await self.request("POST", "/api/v3/series", json=payload)

    async def rescan(self, series_id: int):
        return await self.command({"name": "RescanSeries", "seriesId": series_id})

    async def search(self, series_id: int):
        return await self.command({"name": "SeriesSearch", "seriesId": series_id})

    async def releases_for_season(self, series_id: int, season_number: int):
        return await self.request("GET", "/api/v3/release", params={"seriesId": int(series_id), "seasonNumber": int(season_number)})

    async def releases_for_episode(self, episode_id: int):
        return await self.request("GET", "/api/v3/release", params={"episodeId": int(episode_id)})

    async def releases_for_series(self, series_id: int, max_seasons: int = 24):
        """Perform real interactive Sonarr searches for a show's seasons.

        Sonarr's release endpoint treats seriesId-only as RSS; adding
        seasonNumber invokes SeasonSearch. Older ArrNexus builds accidentally
        asked for RSS here, which could make TV acquisition appear random.
        """
        series = await self.series_by_id(int(series_id))
        seasons = []
        for row in (series.get("seasons") or []):
            try:
                number = int(row.get("seasonNumber"))
            except Exception:
                continue
            if number <= 0:
                continue
            if row.get("monitored") is False:
                continue
            seasons.append(number)
        seasons = sorted(set(seasons))[:max_seasons]
        if not seasons:
            # A newly-added series can briefly lack populated season metadata.
            # Falling back to the native SeriesSearch command would download
            # rather than return candidates, so keep this non-destructive.
            return []
        sem = __import__("asyncio").Semaphore(4)
        async def one(number: int):
            async with sem:
                return await self.releases_for_season(int(series_id), number)
        pages = await __import__("asyncio").gather(*(one(n) for n in seasons), return_exceptions=True)
        out = []
        seen = set()
        for page in pages:
            if isinstance(page, Exception):
                continue
            for row in page or []:
                key = str(row.get("guid") or row.get("downloadUrl") or row.get("title") or "") + "|" + str(row.get("indexerId") or row.get("indexer") or "")
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(row)
        return out

    async def releases(self, series_id: int):
        # Kept for compatibility with plugins/older callers; use the interactive
        # series implementation rather than Sonarr's seriesId-only RSS behavior.
        return await self.releases_for_series(series_id)


class LidarrClient(ArrClient):
    def __init__(self, base_url: str | None = None, api_key: str | None = None, name: str = "Lidarr"):
        c = get_connection("lidarr")
        super().__init__(name, base_url or c.url, api_key if api_key is not None else c.api_key, "v1")

    async def artists(self):
        return await self.request("GET", "/api/v1/artist")

    async def artist(self, artist_id: int):
        return await self.request("GET", f"/api/v1/artist/{artist_id}")

    async def artist_lookup(self, term: str):
        return await self.request("GET", "/api/v1/artist/lookup", params={"term": term})

    async def albums(self, artist_id: int | None = None):
        params = {"artistId": artist_id} if artist_id else None
        return await self.request("GET", "/api/v1/album", params=params)

    async def album_lookup(self, term: str):
        return await self.request("GET", "/api/v1/album/lookup", params={"term": term})

    async def metadata_profiles(self):
        return await self.request("GET", "/api/v1/metadataprofile")

    async def add_artist(self, candidate: dict, root: str | None = None, search: bool = False):
        profiles = await self.quality_profiles()
        metadata_profiles = await self.metadata_profiles()
        qid = pick_named_id(profiles, settings.lidarr_quality_profile_name)
        mid = pick_named_id(metadata_profiles, settings.lidarr_metadata_profile_name)
        payload = copy.deepcopy(candidate)
        payload.pop("id", None)
        payload["qualityProfileId"] = qid
        payload["metadataProfileId"] = mid
        payload["rootFolderPath"] = root or lidarr_root()
        payload["monitored"] = True
        payload["monitorNewItems"] = payload.get("monitorNewItems") or "all"
        payload["addOptions"] = {"monitor": "all", "searchForMissingAlbums": bool(search)}
        return await self.request("POST", "/api/v1/artist", json=payload)

    async def monitor_album(self, album_id: int, monitored: bool = True):
        albums = await self.albums()
        album = next((x for x in albums if int(x.get("id", 0)) == int(album_id)), None)
        if not album:
            raise ArrError("Album not found in Lidarr")
        payload = copy.deepcopy(album)
        payload["monitored"] = bool(monitored)
        return await self.request("PUT", f"/api/v1/album/{album_id}", json=payload)

    async def releases_for_album(self, album_id: int):
        return await self.request("GET", "/api/v1/release", params={"albumId": album_id})

    async def releases_for_artist(self, artist_id: int):
        return await self.request("GET", "/api/v1/release", params={"artistId": artist_id})

    async def grab_release(self, release: dict):
        return await self.request("POST", "/api/v1/release", json=release)

    async def search_artist(self, artist_id: int):
        return await self.command({"name": "ArtistSearch", "artistId": artist_id})

    async def search_album(self, album_id: int):
        return await self.command({"name": "AlbumSearch", "albumIds": [album_id]})


class ProwlarrClient(ArrClient):
    def __init__(self, base_url: str | None = None, api_key: str | None = None, name: str = "Prowlarr"):
        c = get_connection("prowlarr")
        super().__init__(name, base_url or c.url, api_key if api_key is not None else c.api_key, "v1")

    async def indexers(self):
        return await self.request("GET", "/api/v1/indexer")

    async def indexer(self, indexer_id: int):
        return await self.request("GET", f"/api/v1/indexer/{int(indexer_id)}")

    async def update_indexer(self, indexer_id: int, changes: dict):
        current = await self.indexer(int(indexer_id))
        if not isinstance(current, dict):
            raise ArrError("Prowlarr returned an invalid indexer payload")
        payload = copy.deepcopy(current)
        for key in ("enable", "priority", "enableRss", "enableAutomaticSearch", "enableInteractiveSearch"):
            if key in changes:
                payload[key] = changes[key]
        return await self.request("PUT", f"/api/v1/indexer/{int(indexer_id)}", json=payload)

    async def search(self, query: str, categories: list[int] | None = None, limit: int = 100):
        params = {"query": query, "type": "search", "limit": limit, "offset": 0}
        if categories:
            params["categories"] = categories
        return await self.request("GET", "/api/v1/search", params=params)

    async def download_release(self, download_url: str) -> dict:
        """Resolve a Prowlarr release URL through its download proxy.

        Returns either a magnet URI or raw torrent bytes. Many indexers only
        expose a Prowlarr download URL and redirect to the magnet at grab time.
        """
        if not self.api_key:
            raise ArrError("Prowlarr API key is not configured")
        if not download_url:
            raise ArrError("Release does not contain a download URL")
        url = download_url if download_url.startswith("http") else f"{self.base_url}/{download_url.lstrip('/')}"
        client = _http_client()
        r = await client.get(url, headers=self.headers, timeout=90.0)
        if r.status_code >= 400:
            raise ArrError(f"Prowlarr release download failed: {r.status_code} {r.text[:500]}")
        final_url = str(r.url)
        text = ""
        ctype = (r.headers.get("content-type") or "").lower()
        if "text" in ctype or "magnet" in ctype or len(r.content) < 4096:
            try:
                text = r.text.strip()
            except Exception:
                text = ""
        magnet = final_url if final_url.startswith("magnet:") else text if text.startswith("magnet:") else ""
        return {"magnet": magnet, "content": r.content, "content_type": ctype, "url": final_url}


def pick_named_id(items: list[dict], preferred_name: str) -> int:
    if not items:
        raise ArrError("No profiles returned")
    wanted = preferred_name.strip().lower()
    for item in items:
        if str(item.get("name", "")).strip().lower() == wanted:
            return int(item["id"])
    return int(items[0]["id"])


def poster_url(item: dict | None) -> str:
    if not item:
        return ""
    for image in item.get("images") or []:
        if str(image.get("coverType", "")).lower() == "poster":
            return image.get("remoteUrl") or image.get("url") or ""
    return ""


def fanart_url(item: dict | None) -> str:
    if not item:
        return ""
    for image in item.get("images") or []:
        if str(image.get("coverType", "")).lower() in {"fanart", "banner"}:
            return image.get("remoteUrl") or image.get("url") or ""
    return ""
