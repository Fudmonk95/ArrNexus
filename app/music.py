from __future__ import annotations
import asyncio
import time
from urllib.parse import quote_plus
import httpx
from .config import settings
from .db import cache_get, cache_set

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
    if kind in {"album", "release"}:
        data = await _mb_get("release-group", {"query": term, "limit": min(limit, 25)})
        out = []
        for x in data.get("release-groups", []):
            credit = x.get("artist-credit") or []
            artist = credit[0].get("name") if credit else "Unknown artist"
            mbid = x.get("id") or ""
            out.append({
                "source": "MusicBrainz", "kind": "album", "id": mbid,
                "title": x.get("title") or "Unknown album", "artist": artist,
                "date": x.get("first-release-date") or "", "genre": ", ".join(t.get("name", "") for t in (x.get("tags") or [])[:3]),
                "score": x.get("score") or 0,
                "artwork": f"https://coverartarchive.org/release-group/{mbid}/front-500" if mbid else "",
                "external": f"https://musicbrainz.org/release-group/{mbid}" if mbid else "",
            })
        return out
    data = await _mb_get("artist", {"query": term, "limit": min(limit, 25)})
    return [{
        "source": "MusicBrainz", "kind": "artist", "id": x.get("id") or "",
        "title": x.get("name") or "Unknown artist", "artist": x.get("name") or "Unknown artist",
        "country": x.get("country") or "", "disambiguation": x.get("disambiguation") or "",
        "tags": [t.get("name") for t in (x.get("tags") or [])[:5]], "score": x.get("score") or 0,
        "artwork": "", "external": f"https://musicbrainz.org/artist/{x.get('id')}" if x.get("id") else "",
    } for x in data.get("artists", [])]

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
        "source": "ListenBrainz", "kind": "artist", "id": x.get("artist_mbid") or "",
        "title": x.get("artist_name") or "Unknown artist", "artist": x.get("artist_name") or "Unknown artist",
        "listen_count": x.get("listen_count") or 0, "artwork": "",
        "external": f"https://listenbrainz.org/artist/{x.get('artist_mbid')}" if x.get("artist_mbid") else "",
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
            "source": "ListenBrainz", "kind": "album", "id": mbid,
            "title": x.get("release_name") or "Unknown release", "artist": x.get("artist_name") or "Unknown artist",
            "listen_count": x.get("listen_count") or 0,
            "artwork": f"https://coverartarchive.org/release/{mbid}/front-500" if mbid else "",
            "external": f"https://musicbrainz.org/release/{mbid}" if mbid else "",
        })
    return out

GENRES = [
    "Electronic", "House", "Tech House", "Techno", "Trance", "Drum & Bass", "Dubstep", "Hard Dance",
    "Ambient", "Hip Hop", "R&B", "Pop", "Rock", "Metal", "Indie", "Alternative", "Jazz", "Soul", "Funk",
    "Reggae", "Classical", "Country", "Folk", "Punk", "Disco"
]

async def itunes_search(term: str, entity: str = "album", limit: int = 20) -> list[dict]:
    if not term.strip():
        return []
    params = {"term": term, "country": settings.public_music_country, "media": "music", "entity": entity, "limit": min(limit, 50)}
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            r = await client.get("https://itunes.apple.com/search", params=params)
        r.raise_for_status()
        results = r.json().get("results") or []
    except Exception:
        return []
    out = []
    for x in results:
        wrapper = x.get("wrapperType")
        kind = "artist" if wrapper == "artist" else "album" if wrapper == "collection" else "track"
        out.append({
            "source": "Apple", "kind": kind, "id": str(x.get("artistId") if kind == "artist" else x.get("collectionId") or x.get("trackId") or ""),
            "title": x.get("artistName") if kind == "artist" else x.get("collectionName") or x.get("trackName") or "",
            "artist": x.get("artistName") or "", "date": x.get("releaseDate") or "", "genre": x.get("primaryGenreName") or "",
            "artwork": (x.get("artworkUrl100") or "").replace("100x100", "600x600"),
            "external": x.get("artistViewUrl") if kind == "artist" else x.get("collectionViewUrl") or x.get("trackViewUrl") or "",
        })
    return out

async def representative_artwork(artist: str) -> str:
    key = f"artistart:{artist.lower().strip()}"
    cached = cache_get(key)
    if isinstance(cached, str):
        return cached
    rows = await itunes_search(artist, "album", 1)
    art = rows[0].get("artwork", "") if rows else ""
    cache_set(key, art)
    return art

async def enrich_artist_art(rows: list[dict], limit: int = 10) -> list[dict]:
    # Apple Search is limited; enrich only the first cards and cache results.
    sem = asyncio.Semaphore(3)
    async def one(row):
        if row.get("artwork"):
            return row
        async with sem:
            row["artwork"] = await representative_artwork(row.get("artist") or row.get("title") or "")
        return row
    head = await asyncio.gather(*(one(dict(r)) for r in rows[:limit]))
    return head + [dict(r) for r in rows[limit:]]

async def audius_trending(count: int = 24, genre: str = "") -> list[dict]:
    params = {"limit": min(count, 100), "time": "week"}
    if genre:
        params["genre"] = genre
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            r = await client.get("https://api.audius.co/v1/tracks/trending", params=params)
        r.raise_for_status()
        data = r.json().get("data") or []
    except Exception:
        return []
    out = []
    for x in data[:count]:
        user = x.get("user") or {}
        artwork = x.get("artwork") or {}
        out.append({
            "source": "Audius", "kind": "track", "id": x.get("id") or "", "title": x.get("title") or "Unknown track",
            "artist": user.get("name") or user.get("handle") or "Unknown artist", "genre": x.get("genre") or "",
            "artwork": artwork.get("1000x1000") or artwork.get("480x480") or artwork.get("150x150") or "",
            "external": x.get("permalink") and f"https://audius.co{x.get('permalink')}" or "",
            "play_count": x.get("play_count") or 0,
        })
    return out

async def audius_search(term: str, count: int = 30) -> list[dict]:
    if not term.strip(): return []
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            r = await client.get("https://api.audius.co/v1/tracks/search", params={"query": term, "limit": min(count, 100)})
        r.raise_for_status(); data = r.json().get("data") or []
    except Exception:
        return []
    out=[]
    for x in data[:count]:
        user=x.get("user") or {}; art=x.get("artwork") or {}
        out.append({"source":"Audius","kind":"track","id":x.get("id") or "","title":x.get("title") or "","artist":user.get("name") or user.get("handle") or "","genre":x.get("genre") or "","artwork":art.get("1000x1000") or art.get("480x480") or art.get("150x150") or "","external":x.get("permalink") and f"https://audius.co{x.get('permalink')}" or "","play_count":x.get("play_count") or 0})
    return out

def provider_catalog() -> list[dict]:
    return [
        {"key":"unified","name":"For You","mode":"native","description":"ListenBrainz + Apple + Audius + MusicBrainz"},
        {"key":"listenbrainz","name":"ListenBrainz","mode":"native","description":"Public sitewide trends; no account required"},
        {"key":"apple","name":"Apple / iTunes","mode":"native","description":"Public iTunes catalog search; no Apple account required"},
        {"key":"audius","name":"Audius","mode":"native","description":"Open music discovery, trending and search"},
        {"key":"musicbrainz","name":"MusicBrainz","mode":"native","description":"Open music metadata and catalog search"},
        {"key":"spotify","name":"Spotify","mode":"external","description":"Open public Spotify search without connecting your account"},
        {"key":"amazon","name":"Amazon Music","mode":"external","description":"Open Amazon Music catalog search"},
        {"key":"beatport","name":"Beatport","mode":"external","description":"Electronic music catalog/search; official API access is gated"},
        {"key":"bandcamp","name":"Bandcamp","mode":"external","description":"Independent music discovery; public catalog API is gated"},
        {"key":"lastfm","name":"Last.fm","mode":"external","description":"Charts and discovery; full API needs an app key"},
        {"key":"discogs","name":"Discogs","mode":"external","description":"Release database search; API search generally needs authentication"},
    ]

def external_music_links(artist: str, album: str = "") -> dict[str, str]:
    query = " ".join(x for x in [artist, album] if x).strip(); q=quote_plus(query)
    return {
        "spotify": f"https://open.spotify.com/search/{q}",
        "apple": f"https://music.apple.com/gb/search?term={q}",
        "amazon": f"https://music.amazon.co.uk/search/{q}",
        "beatport": f"https://www.beatport.com/search?q={q}",
        "bandcamp": f"https://bandcamp.com/search?q={q}",
        "lastfm": f"https://www.last.fm/search?q={q}",
        "discogs": f"https://www.discogs.com/search/?q={q}&type=all",
        "musicbrainz": f"https://musicbrainz.org/search?query={q}&type=release_group&method=indexed",
    }

async def enrich_release_art(rows: list[dict], limit: int = 8) -> list[dict]:
    """Use the public Apple/iTunes catalog as an artwork fallback for the
    first few trending releases, with SQLite caching to avoid repeated calls."""
    sem = asyncio.Semaphore(2)
    async def one(row):
        row = dict(row)
        artist = row.get("artist") or ""
        title = row.get("title") or ""
        key = f"releaseart:{artist.lower().strip()}:{title.lower().strip()}"
        cached = cache_get(key)
        if isinstance(cached, str) and cached:
            row["artwork"] = cached
            return row
        async with sem:
            found = await itunes_search(f"{artist} {title}".strip(), "album", 3)
        if found:
            # Prefer a close title match, otherwise the first catalog result.
            from .scanner import normalize_title
            wanted = normalize_title(title)
            pick = next((x for x in found if normalize_title(x.get("title") or "") == wanted), found[0])
            art = pick.get("artwork") or row.get("artwork") or ""
            if art:
                row["artwork"] = art
                cache_set(key, art)
        return row
    head = await asyncio.gather(*(one(r) for r in rows[:limit]))
    return head + [dict(r) for r in rows[limit:]]
