from __future__ import annotations
import httpx
from .connections import get_connection


class SeerrError(RuntimeError):
    pass


class SeerrClient:
    def __init__(self):
        self.name = "Seerr"
        c = get_connection("seerr")
        self.base_url = c.url.rstrip("/")
        self.api_key = c.api_key

    @property
    def configured(self):
        return bool(self.base_url and self.api_key)

    @property
    def headers(self):
        return {"X-Api-Key": self.api_key, "Accept": "application/json"}

    async def request(self, path: str, params: dict | None = None):
        if not self.api_key:
            raise SeerrError("Seerr API key is not configured")
        url = f"{self.base_url}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(url, headers=self.headers, params=params)
        if r.status_code >= 400:
            raise SeerrError(f"Seerr returned HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    async def status(self):
        return await self.request("/api/v1/status")

    async def trending(self, media_type: str = "movie", time_window: str = "week", page: int = 1):
        return await self.request("/api/v1/discover/trending", {"mediaType": media_type, "timeWindow": time_window, "page": page})

    async def discover_movies(self, page: int = 1, sort_by: str = "popularity.desc"):
        return await self.request("/api/v1/discover/movies", {"page": page, "sortBy": sort_by})

    async def discover_tv(self, page: int = 1, sort_by: str = "popularity.desc"):
        return await self.request("/api/v1/discover/tv", {"page": page, "sortBy": sort_by})


def image_url(path: str | None, size: str = "w500") -> str:
    if not path:
        return ""
    if str(path).startswith("http"):
        return str(path)
    return f"https://image.tmdb.org/t/p/{size}/{str(path).lstrip('/')}"


def normalize_media(row: dict, fallback_type: str = "movie") -> dict:
    mt = row.get("mediaType") or fallback_type
    title = row.get("title") or row.get("name") or row.get("originalTitle") or row.get("originalName") or "Untitled"
    date = row.get("releaseDate") or row.get("firstAirDate") or ""
    year = None
    try:
        year = int(str(date)[:4]) if date else None
    except Exception:
        year = None
    return {
        "id": row.get("id"),
        "tmdbId": row.get("id") if mt == "movie" else None,
        "title": title,
        "year": year,
        "media_type": mt,
        "poster": image_url(row.get("posterPath")),
        "backdrop": image_url(row.get("backdropPath"), "w1280"),
        "rating": row.get("voteAverage") or row.get("vote_average") or 0,
        "overview": row.get("overview") or "",
        "raw": row,
        "mediaInfo": row.get("mediaInfo") or {},
    }


def result_rows(payload: dict, fallback_type: str) -> list[dict]:
    return [normalize_media(x, fallback_type) for x in (payload.get("results") or [])]
