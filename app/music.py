from __future__ import annotations
import asyncio
import time
from urllib.parse import quote_plus
import httpx
from .config import settings

_mb_lock = asyncio.Lock()
_mb_last = 0.0


async def _mb_get(path: str, params: dict) -> dict:
    global _mb_last
    async with _mb_lock:
        gap = time.monotonic() - _mb_last
        if gap < 1.05:
            await asyncio.sleep(1.05 - gap)
        headers = {"User-Agent": settings.music_user_agent, "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=headers) as client:
            r = await client.get(f"{settings.musicbrainz_base.rstrip('/')}/{path.lstrip('/')}", params={**params, "fmt": "json"})
        _mb_last = time.monotonic()
        r.raise_for_status()
        return r.json()


async def search_musicbrainz(term: str, kind: str = "artist", limit: int = 20) -> list[dict]:
    term = term.strip()
    if not term:
        return []
    if kind == "album":
        data = await _mb_get("release-group", {"query": term, "limit": min(limit, 25)})
        out = []
        for x in data.get("release-groups", []):
            artist_credit = x.get("artist-credit") or []
            artist_name = artist_credit[0].get("name") if artist_credit else ""
            mbid = x.get("id")
            out.append({
                "kind": "album",
                "id": mbid,
                "title": x.get("title") or "Unknown album",
                "artist": artist_name or "Unknown artist",
                "date": x.get("first-release-date") or "",
                "primary_type": x.get("primary-type") or "",
                "score": x.get("score") or 0,
                "artwork": f"https://coverartarchive.org/release-group/{mbid}/front-250" if mbid else "",
            })
        return out
    data = await _mb_get("artist", {"query": term, "limit": min(limit, 25)})
    out = []
    for x in data.get("artists", []):
        out.append({
            "kind": "artist",
            "id": x.get("id"),
            "title": x.get("name") or "Unknown artist",
            "artist": x.get("name") or "Unknown artist",
            "country": x.get("country") or "",
            "disambiguation": x.get("disambiguation") or "",
            "tags": [t.get("name") for t in (x.get("tags") or [])[:5]],
            "score": x.get("score") or 0,
            "artwork": "",
        })
    return out


async def trending_artists(count: int = 24, range_name: str = "this_week") -> list[dict]:
    url = f"{settings.listenbrainz_base.rstrip('/')}/1/stats/sitewide/artists"
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            r = await client.get(url, params={"range": range_name, "count": min(count, 100)})
        r.raise_for_status()
        artists = ((r.json().get("payload") or {}).get("artists") or [])
    except Exception:
        return []
    return [{
        "kind": "artist",
        "id": x.get("artist_mbid") or "",
        "title": x.get("artist_name") or "Unknown artist",
        "artist": x.get("artist_name") or "Unknown artist",
        "listen_count": x.get("listen_count") or 0,
        "artwork": "",
    } for x in artists[:count]]


async def trending_releases(count: int = 24, range_name: str = "this_week") -> list[dict]:
    url = f"{settings.listenbrainz_base.rstrip('/')}/1/stats/sitewide/releases"
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            r = await client.get(url, params={"range": range_name, "count": min(count, 100)})
        r.raise_for_status()
        releases = ((r.json().get("payload") or {}).get("releases") or [])
    except Exception:
        return []
    out = []
    for x in releases[:count]:
        mbid = x.get("release_mbid") or ""
        out.append({
            "kind": "album",
            "id": mbid,
            "title": x.get("release_name") or "Unknown release",
            "artist": x.get("artist_name") or "Unknown artist",
            "listen_count": x.get("listen_count") or 0,
            "artwork": f"https://coverartarchive.org/release/{mbid}/front-250" if mbid else "",
        })
    return out


GENRES = [
    "Electronic", "House", "Tech House", "Techno", "Trance", "Drum & Bass",
    "Dubstep", "Hard Dance", "Ambient", "Hip Hop", "R&B", "Pop", "Rock",
    "Metal", "Indie", "Alternative", "Jazz", "Soul", "Funk", "Reggae",
    "Classical", "Country", "Folk", "Punk", "Disco"
]


async def itunes_search(term: str, entity: str = "album", limit: int = 20) -> list[dict]:
    if not settings.enable_itunes_search or not term.strip():
        return []
    params = {
        "term": term,
        "country": settings.public_music_country,
        "media": "music",
        "entity": entity,
        "limit": min(limit, 50),
    }
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            r = await client.get("https://itunes.apple.com/search", params=params)
        r.raise_for_status()
        results = r.json().get("results") or []
    except Exception:
        return []
    out = []
    for x in results:
        out.append({
            "kind": "album" if x.get("wrapperType") == "collection" else "track",
            "id": str(x.get("collectionId") or x.get("trackId") or ""),
            "title": x.get("collectionName") or x.get("trackName") or "",
            "artist": x.get("artistName") or "",
            "date": x.get("releaseDate") or "",
            "genre": x.get("primaryGenreName") or "",
            "artwork": (x.get("artworkUrl100") or "").replace("100x100", "600x600"),
            "external": x.get("collectionViewUrl") or x.get("trackViewUrl") or "",
        })
    return out


def external_music_links(artist: str, album: str = "") -> dict[str, str]:
    query = " ".join(x for x in [artist, album] if x).strip()
    q = quote_plus(query)
    return {
        "spotify": f"https://open.spotify.com/search/{quote_plus(query)}",
        "apple": f"https://music.apple.com/gb/search?term={q}",
        "amazon": f"https://music.amazon.co.uk/search/{q}",
        "beatport": f"https://www.beatport.com/search?q={q}",
        "musicbrainz": f"https://musicbrainz.org/search?query={q}&type=release_group&method=indexed",
    }
