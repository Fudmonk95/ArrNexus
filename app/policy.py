from __future__ import annotations

import re
from dataclasses import dataclass

from .db import setting_get


@dataclass
class ReleasePolicy:
    preferred_resolution: int = 1080
    minimum_resolution: int = 720
    max_size_gb: float = 35.0
    max_movie_size_gb: float = 45.0
    max_episode_size_gb: float = 15.0
    max_season_size_gb: float = 100.0
    max_series_size_gb: float = 400.0
    prefer_hevc: bool = True
    prefer_cached_debrid: bool = True
    minimum_seeders: int = 2
    reject_terms: tuple[str, ...] = ("cam", "telesync", "telecine", "hdcam", "ts")


def _bool(key: str, default: bool) -> bool:
    raw = setting_get(key, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _int(key: str, default: int) -> int:
    try:
        return int(setting_get(key, str(default)) or default)
    except Exception:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(setting_get(key, str(default)) or default)
    except Exception:
        return default


def load_policy() -> ReleasePolicy:
    reject = tuple(
        x.strip().lower()
        for x in setting_get("policy.reject_terms", "cam,telesync,telecine,hdcam,ts").split(",")
        if x.strip()
    )
    return ReleasePolicy(
        preferred_resolution=_int("policy.preferred_resolution", 1080),
        minimum_resolution=_int("policy.minimum_resolution", 720),
        max_size_gb=_float("policy.max_size_gb", 35.0),
        max_movie_size_gb=_float("policy.max_movie_size_gb", 45.0),
        max_episode_size_gb=_float("policy.max_episode_size_gb", 15.0),
        max_season_size_gb=_float("policy.max_season_size_gb", 100.0),
        max_series_size_gb=_float("policy.max_series_size_gb", 400.0),
        prefer_hevc=_bool("policy.prefer_hevc", True),
        prefer_cached_debrid=_bool("policy.prefer_cached_debrid", True),
        minimum_seeders=_int("policy.minimum_seeders", 2),
        reject_terms=reject or ReleasePolicy.reject_terms,
    )


def _resolution(title: str) -> int:
    t = title.lower()
    if "2160p" in t or "4k" in t or "uhd" in t:
        return 2160
    if "1080p" in t:
        return 1080
    if "720p" in t:
        return 720
    if "576p" in t:
        return 576
    if "480p" in t:
        return 480
    return 0


def max_size_for(policy: ReleasePolicy, media_type: str = "", pack_type: str = "") -> float:
    if media_type == "movie":
        return policy.max_movie_size_gb or policy.max_size_gb
    if media_type == "tv":
        if pack_type == "full_series":
            return policy.max_series_size_gb or policy.max_size_gb
        if pack_type == "season_pack":
            return policy.max_season_size_gb or policy.max_size_gb
        if pack_type in {"episode", "episode_bundle"}:
            return policy.max_episode_size_gb or policy.max_size_gb
    return policy.max_size_gb


def score_release(release: dict, policy: ReleasePolicy | None = None, media_type: str = "", pack_type: str = "") -> dict:
    """Return a transparent heuristic score and explanation for a release.

    The score is explainable and can account for TV pack type so a full-series
    pack is not incorrectly rejected by the same size ceiling as one episode.
    """
    p = policy or load_policy()
    title = str(release.get("title") or "")
    lower = title.lower()
    protocol = str(release.get("protocol") or "").lower()
    size = int(release.get("size") or 0)
    size_gb = size / (1024 ** 3) if size else 0.0
    try:
        seeders = int(release.get("seeders") or 0)
    except Exception:
        seeders = 0
    resolution = _resolution(title)
    reasons: list[str] = []
    score = 50
    rejected = False

    for term in p.reject_terms:
        if re.search(rf"(^|[ ._\-]){re.escape(term)}($|[ ._\-])", lower):
            reasons.append(f"Rejected term: {term.upper()}")
            score -= 100
            rejected = True
            break

    size_limit = max_size_for(p, media_type, pack_type)
    if size_limit > 0 and size_gb > size_limit:
        reasons.append(f"Over {pack_type.replace('_',' ') or media_type or 'release'} size limit ({size_gb:.1f} GB > {size_limit:g} GB)")
        score -= 45
        rejected = True
    elif size_gb:
        reasons.append(f"Size {size_gb:.1f} GB within policy")
        score += 4

    if resolution:
        if resolution < p.minimum_resolution:
            reasons.append(f"Below minimum resolution ({resolution}p)")
            score -= 55
            rejected = True
        elif resolution == p.preferred_resolution:
            reasons.append(f"Preferred resolution {resolution}p")
            score += 24
        elif resolution > p.preferred_resolution:
            reasons.append(f"Higher than preferred resolution ({resolution}p)")
            score += 14
        else:
            reasons.append(f"Acceptable resolution {resolution}p")
            score += 8
    else:
        reasons.append("Resolution not identified from release name")

    if p.prefer_hevc:
        if any(x in lower for x in ("x265", "h265", "hevc")):
            score += 14
            reasons.append("HEVC/x265 preferred")
        elif any(x in lower for x in ("x264", "h264", "avc")):
            score += 5
            reasons.append("H.264/AVC acceptable")

    if protocol == "torrent":
        if seeders >= max(1, p.minimum_seeders):
            bonus = min(12, 3 + seeders // 5)
            score += bonus
            reasons.append(f"{seeders} seeders")
        elif seeders == 0:
            reasons.append("Seeder count unavailable/zero")
        else:
            score -= 12
            reasons.append(f"Low seeders ({seeders})")

    cached = bool(release.get("cached") or release.get("isCached") or release.get("instantAvailability") or release.get("realDebridCached"))
    if p.prefer_cached_debrid and cached:
        score += 25
        reasons.append("Cached on Real-Debrid")
    elif p.prefer_cached_debrid and protocol == "torrent":
        reasons.append("Real-Debrid cache not confirmed")

    if media_type == "tv" and pack_type == "full_series":
        score += 10
        reasons.append("Complete/multi-season pack")
    elif media_type == "tv" and pack_type == "season_pack":
        score += 5
        reasons.append("Season pack")

    score = max(0, min(100, score))
    decision = "rejected" if rejected else "preferred" if score >= 78 else "allowed"
    return {
        "score": score,
        "decision": decision,
        "reasons": reasons,
        "resolution": resolution,
        "size_gb": round(size_gb, 2),
        "cached": cached,
        "size_limit_gb": size_limit,
    }
