from __future__ import annotations
import asyncio
import time
from urllib.parse import quote_plus
import httpx
from .config import settings
from .db import cache_get, cache_set, setting_get, setting_set
from .plugins import load_catalog_plugins, plugin_search_url

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

async def internet_archive_search(term: str = "", count: int = 24) -> list[dict]:
    """No-key public audio discovery from Internet Archive metadata."""
    q = '(mediatype:audio)'
    if term.strip():
        safe = term.replace('"', ' ')
        q += f' AND (title:("{safe}") OR creator:("{safe}"))'
    else:
        q += ' AND downloads:[100 TO *]'
    params = {
        "q": q, "fl[]": ["identifier","title","creator","date","downloads","subject"],
        "rows": min(count, 50), "page": 1, "output": "json", "sort[]": "downloads desc"
    }
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            r = await client.get("https://archive.org/advancedsearch.php", params=params)
        r.raise_for_status(); docs=((r.json().get("response") or {}).get("docs") or [])
    except Exception:
        return []
    out=[]
    for x in docs[:count]:
        creator=x.get("creator") or "Unknown artist"
        if isinstance(creator,list): creator=", ".join(str(v) for v in creator[:2])
        out.append({
            "source":"Internet Archive","kind":"release","id":x.get("identifier") or "",
            "title":x.get("title") or x.get("identifier") or "Untitled","artist":str(creator),
            "date":x.get("date") or "","genre":"", "artwork":f"https://archive.org/services/img/{x.get('identifier')}" if x.get('identifier') else "",
            "external":f"https://archive.org/details/{x.get('identifier')}" if x.get('identifier') else "",
            "play_count":x.get("downloads") or 0,
        })
    return out


async def jamendo_search(term: str = "", count: int = 24) -> list[dict]:
    client_id = setting_get("music.jamendo.client_id", "")
    if not client_id:
        return []
    params={"client_id":client_id,"format":"json","limit":min(count,50),"order":"popularity_week"}
    if term.strip(): params["search"] = term.strip()
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            r=await client.get("https://api.jamendo.com/v3.0/tracks/",params=params)
        r.raise_for_status(); rows=r.json().get("results") or []
    except Exception:
        return []
    return [{
        "source":"Jamendo","kind":"track","id":str(x.get("id") or ""),"title":x.get("name") or "",
        "artist":x.get("artist_name") or "","genre":"","artwork":x.get("image") or x.get("album_image") or "",
        "external":x.get("shareurl") or "","play_count":0
    } for x in rows[:count]]


async def _soundcloud_app_token() -> str:
    cid=setting_get("music.soundcloud.client_id", "")
    secret=setting_get("music.soundcloud.client_secret", "")
    if not cid or not secret:
        return ""
    cached=cache_get("soundcloud:app_token") or {}
    if isinstance(cached,dict) and cached.get("access_token") and float(cached.get("expires_at") or 0)>time.time()+90:
        return cached["access_token"]
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            r=await client.post("https://secure.soundcloud.com/oauth/token", auth=(cid,secret), data={"grant_type":"client_credentials"}, headers={"Accept":"application/json"})
        r.raise_for_status(); data=r.json(); token=data.get("access_token") or ""
        if token:
            cache_set("soundcloud:app_token", {"access_token":token,"expires_at":time.time()+int(data.get("expires_in") or 3000)})
        return token
    except Exception:
        return ""


async def soundcloud_search(term: str, count: int = 24) -> list[dict]:
    if not term.strip(): return []
    token=await _soundcloud_app_token()
    if not token: return []
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            r=await client.get("https://api.soundcloud.com/tracks", params={"q":term,"limit":min(count,50),"access":"playable"}, headers={"Authorization":f"OAuth {token}","Accept":"application/json"})
        r.raise_for_status(); rows=r.json()
        if isinstance(rows,dict): rows=rows.get("collection") or []
    except Exception:
        return []
    out=[]
    for x in (rows or [])[:count]:
        user=x.get("user") or {}; art=x.get("artwork_url") or user.get("avatar_url") or ""
        if art: art=art.replace("-large.","-t500x500.")
        out.append({"source":"SoundCloud","kind":"track","id":str(x.get("id") or ""),"title":x.get("title") or "","artist":user.get("username") or "","genre":x.get("genre") or "","artwork":art,"external":x.get("permalink_url") or "","play_count":x.get("playback_count") or 0})
    return out


async def lastfm_search(term: str = "", kind: str = "artist", count: int = 24) -> list[dict]:
    api_key=setting_get("music.lastfm.api_key", "")
    if not api_key: return []
    method="album.search" if kind in {"album","release"} else "artist.search"
    params={"method":method,"api_key":api_key,"format":"json","limit":min(count,50)}
    params["album" if method=="album.search" else "artist"] = term.strip()
    if not term.strip(): return []
    try:
        async with httpx.AsyncClient(timeout=25.0,follow_redirects=True) as client:
            r=await client.get("https://ws.audioscrobbler.com/2.0/",params=params)
        r.raise_for_status(); data=r.json()
    except Exception: return []
    root=(data.get("results") or {})
    rows=((root.get("albummatches") or {}).get("album") if method=="album.search" else (root.get("artistmatches") or {}).get("artist")) or []
    out=[]
    for x in rows[:count]:
        artist=x.get("artist") if method=="album.search" else x.get("name")
        title=x.get("name") if method=="album.search" else x.get("name")
        images=x.get("image") or []; art=next((i.get("#text") for i in reversed(images) if i.get("#text")),"")
        out.append({"source":"Last.fm","kind":"album" if method=="album.search" else "artist","id":x.get("mbid") or x.get("url") or "","title":title or artist or "","artist":artist or "","genre":"","artwork":art,"external":x.get("url") or "","play_count":int(x.get("listeners") or 0) if str(x.get("listeners") or "").isdigit() else 0})
    return out

async def lastfm_top(count: int = 24) -> list[dict]:
    api_key=setting_get("music.lastfm.api_key", "")
    if not api_key: return []
    try:
        async with httpx.AsyncClient(timeout=25.0,follow_redirects=True) as client:
            r=await client.get("https://ws.audioscrobbler.com/2.0/",params={"method":"chart.gettoptracks","api_key":api_key,"format":"json","limit":min(count,50)})
        r.raise_for_status(); rows=((r.json().get("tracks") or {}).get("track") or [])
    except Exception: return []
    out=[]
    for x in rows[:count]:
        a=x.get("artist") or {}; images=x.get("image") or []; art=next((i.get("#text") for i in reversed(images) if i.get("#text")),"")
        out.append({"source":"Last.fm","kind":"track","id":x.get("mbid") or x.get("url") or "","title":x.get("name") or "","artist":a.get("name") or "","genre":"","artwork":art,"external":x.get("url") or "","play_count":int(x.get("playcount") or 0) if str(x.get("playcount") or "").isdigit() else 0})
    return out

def provider_catalog() -> list[dict]:
    builtins = [
        {"key":"unified","name":"For You","mode":"native","description":"ListenBrainz + Apple + Audius + MusicBrainz + open catalogs"},
        {"key":"listenbrainz","name":"ListenBrainz","mode":"native","description":"Public sitewide trends; no listener account required"},
        {"key":"apple","name":"Apple / iTunes","mode":"native","description":"Public iTunes catalog search; no Apple Music account required"},
        {"key":"audius","name":"Audius","mode":"native","description":"Open music discovery, trending and search"},
        {"key":"musicbrainz","name":"MusicBrainz","mode":"native","description":"Open music metadata and catalog search"},
        {"key":"archive","name":"Internet Archive","mode":"native","description":"No-key public audio/archive discovery"},
        {"key":"jamendo","name":"Jamendo","mode":"optional","description":"Public music catalog; optional free developer client ID"},
        {"key":"soundcloud","name":"SoundCloud","mode":"optional","description":"Catalog search with optional app credentials; no listener account linking"},
        {"key":"spotify","name":"Spotify","mode":"external","description":"Catalog launch/search without linking a personal Spotify account"},
        {"key":"amazon","name":"Amazon Music","mode":"external","description":"Amazon Music catalog launcher"},
        {"key":"beatport","name":"Beatport","mode":"external","description":"Electronic music catalog launcher"},
        {"key":"bandcamp","name":"Bandcamp","mode":"external","description":"Independent music discovery launcher"},
        {"key":"lastfm","name":"Last.fm","mode":"optional","description":"Global charts/search with an optional application API key; no listener login required"},
        {"key":"discogs","name":"Discogs","mode":"external","description":"Release database launcher"},
    ]
    return builtins + load_catalog_plugins()


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
        "soundcloud": f"https://soundcloud.com/search?q={q}",
        "musicbrainz": f"https://musicbrainz.org/search?query={q}&type=release_group&method=indexed",
        "archive": f"https://archive.org/search?query={q}+AND+mediatype%3Aaudio",
        "jamendo": f"https://www.jamendo.com/search?q={q}",
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

# ---------------------------------------------------------------------------
# v4 provider-isolated discovery helpers
# ---------------------------------------------------------------------------

async def apple_top_albums(count: int = 24) -> list[dict]:
    """Public Apple RSS/marketing feed. No Apple Music listener account needed."""
    country = (settings.public_music_country or "GB").lower()
    urls = [
        f"https://rss.marketingtools.apple.com/api/v2/{country}/music/most-played/{min(count,100)}/albums.json",
        f"https://rss.applemarketingtools.com/api/v2/{country}/music/most-played/{min(count,100)}/albums.json",
    ]
    data = None
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for url in urls:
            try:
                r = await client.get(url, headers={"Accept":"application/json","User-Agent":settings.music_user_agent})
                if r.status_code < 400:
                    data = r.json(); break
            except Exception:
                continue
    if not data:
        return []
    rows = ((data.get("feed") or {}).get("results") or [])
    out=[]
    for x in rows[:count]:
        genres=x.get("genres") or []
        art=x.get("artworkUrl100") or ""
        if art: art=art.replace("100x100", "600x600")
        out.append({
            "source":"Apple","kind":"album","id":str(x.get("id") or ""),
            "title":x.get("name") or "","artist":x.get("artistName") or "",
            "genre":", ".join(g.get("name","") if isinstance(g,dict) else str(g) for g in genres[:2]),
            "date":x.get("releaseDate") or "","artwork":art,"external":x.get("url") or "",
            "rank":len(out)+1,
        })
    return out


async def musicbrainz_recent(count: int = 24, genre: str = "") -> list[dict]:
    """Recent MusicBrainz release groups; deliberately not ListenBrainz data."""
    from datetime import date, timedelta
    end=date.today(); start=end-timedelta(days=120)
    query=f"firstreleasedate:[{start.isoformat()} TO {end.isoformat()}]"
    if genre.strip():
        query += f' AND tag:"{genre.strip()}"'
    try:
        data=await _mb_get("release-group", {"query":query,"limit":min(count,25)})
    except Exception:
        return []
    out=[]
    for x in data.get("release-groups",[])[:count]:
        credit=x.get("artist-credit") or []; artist=credit[0].get("name") if credit else "Unknown artist"; mbid=x.get("id") or ""
        out.append({"source":"MusicBrainz","kind":"album","id":mbid,"title":x.get("title") or "Unknown release","artist":artist,"date":x.get("first-release-date") or "","genre":", ".join(t.get("name","") for t in (x.get("tags") or [])[:3]),"artwork":f"https://coverartarchive.org/release-group/{mbid}/front-500" if mbid else "","external":f"https://musicbrainz.org/release-group/{mbid}" if mbid else ""})
    return await enrich_release_art(out, min(8,len(out)))


async def deezer_chart(count: int = 24) -> list[dict]:
    """Deezer public chart endpoint. No personal listener account is used."""
    try:
        async with httpx.AsyncClient(timeout=20.0,follow_redirects=True) as client:
            r=await client.get("https://api.deezer.com/chart/0/albums",params={"limit":min(count,100)})
        r.raise_for_status(); rows=(r.json().get("data") or [])
    except Exception:
        return []
    out=[]
    for x in rows[:count]:
        a=x.get("artist") or {}
        out.append({"source":"Deezer","kind":"album","id":str(x.get("id") or ""),"title":x.get("title") or "","artist":a.get("name") or "","genre":"","artwork":x.get("cover_xl") or x.get("cover_big") or x.get("cover_medium") or "","external":x.get("link") or "","rank":len(out)+1})
    return out


async def deezer_search(term: str, kind: str = "album", count: int = 24) -> list[dict]:
    if not term.strip(): return []
    endpoint="artist" if kind=="artist" else "album"
    try:
        async with httpx.AsyncClient(timeout=20.0,follow_redirects=True) as client:
            r=await client.get(f"https://api.deezer.com/search/{endpoint}",params={"q":term,"limit":min(count,100)})
        r.raise_for_status(); rows=(r.json().get("data") or [])
    except Exception:
        return []
    out=[]
    for x in rows[:count]:
        a=x.get("artist") or {}; is_artist=endpoint=="artist"
        artwork = (x.get("picture_xl") or x.get("picture_big") or x.get("picture_medium") or "") if is_artist else (x.get("cover_xl") or x.get("cover_big") or x.get("cover_medium") or "")
        out.append({"source":"Deezer","kind":"artist" if is_artist else "album","id":str(x.get("id") or ""),"title":(x.get("name") or "") if is_artist else (x.get("title") or ""),"artist":(x.get("name") or "") if is_artist else (a.get("name") or ""),"genre":"","artwork":artwork,"external":x.get("link") or ""})
    return out


async def spotify_search(term: str, kind: str = "album", count: int = 20) -> list[dict]:
    """Optional app-only Spotify catalog access via client credentials.

    No personal Spotify account is linked to ArrNexus. Spotify app access is
    optional because Development Mode eligibility/rules can change.
    """
    cid=setting_get("music.spotify.client_id", ""); secret=setting_get("music.spotify.client_secret", "")
    if not cid or not secret or not term.strip(): return []
    token_cache=cache_get("spotify:app_token") or {}
    token=""
    if isinstance(token_cache,dict) and token_cache.get("access_token") and float(token_cache.get("expires_at") or 0)>time.time()+90:
        token=token_cache["access_token"]
    if not token:
        try:
            async with httpx.AsyncClient(timeout=20.0,follow_redirects=True) as client:
                tr=await client.post("https://accounts.spotify.com/api/token",auth=(cid,secret),data={"grant_type":"client_credentials"})
            tr.raise_for_status(); td=tr.json(); token=td.get("access_token") or ""
            if token: cache_set("spotify:app_token",{"access_token":token,"expires_at":time.time()+int(td.get("expires_in") or 3600)})
        except Exception: return []
    typ="artist" if kind=="artist" else "album"
    out=[]; offset=0
    while len(out)<min(count,30):
        try:
            async with httpx.AsyncClient(timeout=20.0,follow_redirects=True) as client:
                r=await client.get("https://api.spotify.com/v1/search",params={"q":term,"type":typ,"market":(settings.public_music_country or "GB").upper(),"limit":min(10,count-len(out)),"offset":offset},headers={"Authorization":f"Bearer {token}"})
            r.raise_for_status(); payload=r.json(); rows=((payload.get("artists") if typ=="artist" else payload.get("albums")) or {}).get("items") or []
        except Exception: break
        if not rows: break
        for x in rows:
            images=x.get("images") or []; art=images[0].get("url") if images else ""
            artists=x.get("artists") or []
            artist=x.get("name") if typ=="artist" else (artists[0].get("name") if artists else "")
            out.append({"source":"Spotify","kind":typ,"id":x.get("id") or "","title":x.get("name") or "","artist":artist,"genre":", ".join((x.get("genres") or [])[:3]),"date":x.get("release_date") or "","artwork":art,"external":((x.get("external_urls") or {}).get("spotify") or "")})
        offset += len(rows)
    return out[:count]


async def provider_featured(source: str, genre: str = "", count: int = 24) -> tuple[list[dict], str]:
    """Return truly source-specific browse content and a human status note."""
    source=(source or "unified").lower()
    if source=="listenbrainz":
        rows=await trending_releases(count,"this_week"); return await enrich_release_art(rows,min(10,len(rows))), "ListenBrainz sitewide releases"
    if source=="apple":
        rows=await apple_top_albums(count); return rows, "Apple public top albums"
    if source=="audius":
        return await audius_trending(count,genre), "Audius trending tracks"
    if source=="musicbrainz":
        return await musicbrainz_recent(count,genre), "Recent MusicBrainz release groups"
    if source=="archive":
        return await internet_archive_search(genre,count), "Popular Internet Archive audio"
    if source=="jamendo":
        return await jamendo_search(genre,count), "Jamendo popular music" if setting_get("music.jamendo.client_id","") else "Configure a Jamendo developer client ID to browse Jamendo"
    if source=="soundcloud":
        if setting_get("music.soundcloud.client_id","") and setting_get("music.soundcloud.client_secret",""):
            return [], "SoundCloud app access is configured — search the SoundCloud catalogue above"
        return [], "Configure SoundCloud application credentials in Settings; no listener account is linked"
    if source=="deezer":
        return await deezer_chart(count), "Deezer chart albums"
    if source=="spotify":
        if setting_get("music.spotify.client_id","") and setting_get("music.spotify.client_secret",""):
            return [], "Spotify app access is configured — search the Spotify catalogue above"
        return [], "Optional Spotify application credentials enable catalog search without linking a listener account"
    if source=="lastfm":
        return await lastfm_top(count), "Last.fm top tracks" if setting_get("music.lastfm.api_key","") else "Configure a Last.fm application API key to browse charts"
    if source=="unified":
        lb,apple,audius,dz=await asyncio.gather(trending_releases(8),apple_top_albums(8),audius_trending(8,genre),deezer_chart(8))
        seen=set(); out=[]
        for row in lb+apple+audius+dz:
            key=((row.get("artist") or "").lower(),(row.get("title") or "").lower())
            if key in seen: continue
            seen.add(key); out.append(row)
        return out[:count], "A mixed, deduplicated view across open/account-free catalogues"
    return [], "This provider is an external catalogue launcher; ArrNexus does not fake its recommendations with another provider's data"


async def provider_search(source: str, term: str, kind: str = "artist", count: int = 30) -> tuple[list[dict], str]:
    source=(source or "unified").lower(); term=term.strip()
    if not term: return [], ""
    if source=="apple": return await itunes_search(term,"album" if kind=="album" else "musicArtist",count), ""
    if source=="audius": return await audius_search(term,count), ""
    if source in {"musicbrainz","listenbrainz"}: return await search_musicbrainz(term,kind,count), ""
    if source=="archive": return await internet_archive_search(term,count), ""
    if source=="jamendo": return await jamendo_search(term,count), ""
    if source=="soundcloud": return await soundcloud_search(term,count), ""
    if source=="lastfm": return await lastfm_search(term,kind,count), ""
    if source=="deezer": return await deezer_search(term,kind,count), ""
    if source=="spotify": return await spotify_search(term,kind,count), ""
    if source.startswith("plugin-"):
        plugin=next((p for p in provider_catalog() if p.get("key")==source),None)
        return [], plugin_search_url(plugin or {},term) if plugin else ""
    if source in {"amazon","beatport","bandcamp","discogs"}:
        return [], external_music_links(term).get(source,"")
    # For You searches multiple *real* providers then dedupes.
    mb,apple,au,dz,arc=await asyncio.gather(search_musicbrainz(term,kind,12),itunes_search(term,"album" if kind=="album" else "musicArtist",12),audius_search(term,12),deezer_search(term,kind,12),internet_archive_search(term,8))
    seen=set(); rows=[]
    for row in mb+apple+au+dz+arc:
        key=((row.get("artist") or "").lower(),(row.get("title") or "").lower())
        if key in seen: continue
        seen.add(key); rows.append(row)
    return rows[:count], ""


# Replace provider catalog with capability-aware v4 list.
def provider_catalog() -> list[dict]:
    builtins = [
        {"key":"unified","name":"For You","mode":"native","description":"Mixed open/account-free discovery, deduplicated across sources"},
        {"key":"listenbrainz","name":"ListenBrainz","mode":"native","description":"Public sitewide listening trends"},
        {"key":"apple","name":"Apple / iTunes","mode":"native","description":"Public iTunes search and Apple public chart feed; no Apple Music listener login"},
        {"key":"audius","name":"Audius","mode":"native","description":"Open music trending and track search"},
        {"key":"musicbrainz","name":"MusicBrainz","mode":"native","description":"Open music metadata, search and recent release groups"},
        {"key":"deezer","name":"Deezer","mode":"native","description":"Public chart and catalogue search without linking a listener account"},
        {"key":"archive","name":"Internet Archive","mode":"native","description":"Public audio/archive discovery"},
        {"key":"jamendo","name":"Jamendo","mode":"optional","description":"Optional developer client ID; no listener account linking"},
        {"key":"soundcloud","name":"SoundCloud","mode":"optional","description":"Optional application credentials; no listener account linking"},
        {"key":"spotify","name":"Spotify","mode":"optional","description":"Optional app client credentials for public catalog search; no user OAuth"},
        {"key":"amazon","name":"Amazon Music","mode":"external","description":"External catalogue launcher; no personal account connected"},
        {"key":"beatport","name":"Beatport","mode":"external","description":"Electronic music catalogue launcher"},
        {"key":"bandcamp","name":"Bandcamp","mode":"external","description":"Independent music discovery launcher"},
        {"key":"lastfm","name":"Last.fm","mode":"optional","description":"Optional app API key for global charts/search"},
        {"key":"discogs","name":"Discogs","mode":"external","description":"Release database launcher"},
    ]
    return builtins + load_catalog_plugins()
