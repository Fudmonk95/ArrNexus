from __future__ import annotations

"""TMDb-backed source identity overrides and pre-import naming previews.

Identity is deliberately attached to the exact source fingerprint.  A renamed or
changed provider source cannot silently inherit an administrator's old choice.
"""

from pathlib import Path
import hashlib
import re
from typing import Any

import httpx

from .db import cache_get, cache_set, setting_get, setting_set
from .scanner import normalize_title, episode_identity, season_hints, video_files

TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMAGE = "https://image.tmdb.org/t/p/w342"


def tmdb_api_key() -> str:
    # Reuse the Lists credential for backwards compatibility, but expose a
    # product-wide Metadata API setting from v10.4 onward.
    return setting_get("metadata.tmdb.api_key") or setting_get("lists.tmdb.api_key")


def save_tmdb_api_key(value: str) -> None:
    value = str(value or "").strip()
    if value:
        setting_set("metadata.tmdb.api_key", value, True)


def tmdb_configured() -> bool:
    return bool(tmdb_api_key())


def _identity_key(source_path: str, fingerprint: str) -> str:
    ident = hashlib.sha256(str(source_path).encode("utf-8", errors="replace")).hexdigest()[:24]
    return f"media_identity:v104:{ident}:{(fingerprint or 'nofingerprint')[:32]}"


def get_identity(source_path: str, fingerprint: str) -> dict[str, Any] | None:
    row = cache_get(_identity_key(source_path, fingerprint))
    return row if isinstance(row, dict) and row.get("title") else None


def save_identity(source_path: str, fingerprint: str, identity: dict[str, Any]) -> dict[str, Any]:
    media_type = str(identity.get("media_type") or "").lower()
    if media_type not in {"movie", "tv"}:
        raise ValueError("Media identity must be movie or TV")
    title = str(identity.get("title") or "").strip()
    if not title:
        raise ValueError("Media identity requires a title")
    payload = {
        "media_type": media_type,
        "title": title,
        "year": int(identity.get("year") or 0) or None,
        "tmdb_id": int(identity.get("tmdb_id") or 0) or None,
        "poster": str(identity.get("poster") or ""),
        "overview": str(identity.get("overview") or "")[:800],
        "source": str(identity.get("source") or "tmdb"),
        "confidence": int(identity.get("confidence") or 100),
    }
    cache_set(_identity_key(source_path, fingerprint), payload)
    return payload


def clear_identity(source_path: str, fingerprint: str) -> None:
    # cache_set has no delete primitive; an empty payload is treated as absent.
    cache_set(_identity_key(source_path, fingerprint), {})


def match_confidence(query: str, title: str, year: int | None = None, expected_year: int | None = None) -> int:
    q = normalize_title(query or "")
    t = normalize_title(title or "")
    if not q or not t:
        return 0
    score = 100 if q == t else 82 if q in t or t in q else 55
    if year and expected_year:
        score += 8 if int(year) == int(expected_year) else -12
    return max(0, min(100, score))


async def search_tmdb(query: str, media_type: str = "tv", *, expected_year: int | None = None, limit: int = 12) -> list[dict[str, Any]]:
    key = tmdb_api_key()
    if not key:
        raise RuntimeError("TMDb API key is not configured. Open Archived Media Recovery → Metadata API settings.")
    media_type = "movie" if str(media_type).lower() == "movie" else "tv"
    query = str(query or "").strip()
    if not query:
        return []
    params: dict[str, Any] = {"api_key": key, "query": query, "language": "en-GB", "include_adult": "false"}
    if expected_year:
        params["year" if media_type == "movie" else "first_air_date_year"] = int(expected_year)
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        response = await client.get(f"{TMDB_API}/search/{media_type}", params=params)
    if response.status_code != 200:
        raise RuntimeError(f"TMDb search failed: HTTP {response.status_code}")
    out: list[dict[str, Any]] = []
    for row in (response.json().get("results") or [])[:max(1, min(30, int(limit)))]:
        title = str(row.get("title") if media_type == "movie" else row.get("name") or "").strip()
        date = str(row.get("release_date") if media_type == "movie" else row.get("first_air_date") or "")
        try:
            year = int(date[:4]) if len(date) >= 4 else None
        except Exception:
            year = None
        poster_path = str(row.get("poster_path") or "")
        out.append({
            "media_type": media_type,
            "title": title,
            "year": year,
            "tmdb_id": int(row.get("id") or 0) or None,
            "poster": f"{TMDB_IMAGE}{poster_path}" if poster_path else "",
            "overview": str(row.get("overview") or "")[:800],
            "source": "tmdb",
            "confidence": match_confidence(query, title, year, expected_year),
        })
    return out


def _safe_title(value: str) -> str:
    text = re.sub(r'[\\/:*?"<>|]+', " ", str(value or "")).strip().rstrip(".")
    return re.sub(r"\s+", " ", text) or "Recovered Media"


def canonical_media_name(identity: dict[str, Any], original_name: str, fallback_seasons: list[int] | None = None) -> str:
    """Return a safe canonical filename while never inventing episode numbers."""
    title = _safe_title(str(identity.get("title") or "Recovered Media"))
    media_type = str(identity.get("media_type") or "tv")
    suffix = Path(original_name).suffix.lower()
    if media_type == "movie":
        year = int(identity.get("year") or 0)
        return f"{title} ({year}){suffix}" if year else f"{title}{suffix}"
    ident = episode_identity(original_name)
    if ident:
        return f"{title} - S{ident[0]:02d}E{ident[1]:02d}{suffix}"
    hints = season_hints(original_name) or list(fallback_seasons or [])
    if len(hints) == 1:
        return f"{title} - Season {hints[0]:02d}{suffix}"
    return f"{title} - {Path(original_name).stem}{suffix}"


def naming_preview(source_path: str, fingerprint: str, identity: dict[str, Any] | None = None) -> list[dict[str, str]]:
    identity = identity or get_identity(source_path, fingerprint)
    if not identity:
        return []
    rows = []
    for logical in video_files(source_path):
        rows.append({"from": logical.name, "to": canonical_media_name(identity, logical.name)})
    return rows[:100]


def apply_to_item(item):
    """Return a dataclass-replaced ScanItem when an exact source override exists."""
    from dataclasses import replace
    identity = get_identity(item.path, item.fingerprint)
    if not identity:
        return item, None
    return replace(
        item,
        media_type=str(identity.get("media_type") or item.media_type),
        title_guess=str(identity.get("title") or item.title_guess),
        year_guess=identity.get("year") or item.year_guess,
    ), identity
