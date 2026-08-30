from __future__ import annotations
from dataclasses import dataclass
from .config import settings
from .paths import movie_roots, tv_roots
from .db import list_rules, increment_rule_hit
from .scanner import normalize_title


@dataclass
class RouteDecision:
    key: str
    root: str
    reason: str
    confidence: int
    rule_id: int | None = None


CHRISTMAS_WORDS = {"christmas", "xmas", "santa", "noel", "mistletoe", "holiday", "nativity", "hallmark"}
EASTER_WORDS = {"easter", "bunny", "resurrection"}
HALLOWEEN_WORDS = {
    "halloween", "scream", "dracula", "frankenstein", "vampire", "werewolf",
    "exorcist", "haunting", "horror", "nightmare", "slasher", "hellraiser", "amityville"
}
KIDS_GENRES = {"family", "animation", "children", "kids"}
HORROR_GENRES = {"horror"}


def lower_set(values):
    return {str(v).strip().lower() for v in (values or []) if str(v).strip()}


def _root(media_type: str, key: str) -> str:
    roots = movie_roots() if media_type == "movie" else tv_roots()
    return roots.get(key, roots["default"])


def _custom(media_type: str, title: str, metadata: dict) -> RouteDecision | None:
    title_l = title.lower()
    norm = normalize_title(title)
    genres = " ".join(str(x).lower() for x in (metadata.get("genres") or []))
    network = str(metadata.get("network") or "").lower()
    studio = str(metadata.get("studio") or metadata.get("network") or "").lower()
    values = {
        "title": title_l,
        "normalized_title": norm,
        "genre": genres,
        "network": network,
        "studio": studio,
    }
    for row in list_rules(media_type):
        if not row["enabled"]:
            continue
        field = row["field"]
        pattern = str(row["pattern"]).lower()
        value = values.get(field, "")
        matched = value == pattern if field == "normalized_title" else pattern in value
        if matched:
            increment_rule_hit(int(row["id"]))
            return RouteDecision(
                row["destination_key"],
                _root(media_type, row["destination_key"]),
                f"{'Learned' if row['learned'] else 'Custom'} rule: {field} contains {row['pattern']}",
                min(100, int(row["weight"])),
                int(row["id"]),
            )
    return None


def decide_movie(title: str, metadata: dict | None = None) -> RouteDecision:
    metadata = metadata or {}
    custom = _custom("movie", title, metadata)
    if custom:
        return custom
    text = title.lower()
    genres = lower_set(metadata.get("genres"))
    studio = str(metadata.get("studio") or "").lower()

    if any(word in text for word in CHRISTMAS_WORDS) or "hallmark" in studio:
        return RouteDecision("christmas", settings.radarr_christmas_root, "Christmas/Hallmark match", 95)
    if any(word in text for word in EASTER_WORDS):
        return RouteDecision("easter", settings.radarr_easter_root, "Easter keyword", 95)
    if any(word in text for word in HALLOWEEN_WORDS) or genres & HORROR_GENRES:
        return RouteDecision("halloween", settings.radarr_halloween_root, "Horror/Halloween match", 88)
    if genres & KIDS_GENRES:
        return RouteDecision("kids", settings.radarr_kids_root, "Family/animation genre", 85)
    return RouteDecision("default", settings.radarr_default_root, "Default movie library", 60)


def decide_tv(title: str, metadata: dict | None = None) -> RouteDecision:
    metadata = metadata or {}
    custom = _custom("tv", title, metadata)
    if custom:
        return custom
    network = str(metadata.get("network") or "").lower()
    genres = lower_set(metadata.get("genres"))

    if genres & KIDS_GENRES or any(k in network for k in ["nickelodeon", "cartoon network", "cbbc", "cbeebies"]):
        return RouteDecision("kids", settings.sonarr_kids_root, "Kids/family series", 88)
    if "netflix" in network:
        return RouteDecision("netflix", settings.sonarr_netflix_root, "Netflix network", 97)
    if "disney+" in network or "disney plus" in network:
        return RouteDecision("disney", settings.sonarr_disney_root, "Disney+ network", 97)
    if "amazon" in network or "prime video" in network:
        return RouteDecision("amazon", settings.sonarr_amazon_root, "Amazon/Prime network", 97)
    if "apple tv+" in network or "apple tv" in network:
        return RouteDecision("apple", settings.sonarr_apple_root, "Apple TV+ network", 97)
    if "bbc" in network or "cbbc" in network or "cbeebies" in network:
        return RouteDecision("bbc", settings.sonarr_bbc_root, "BBC network", 97)
    return RouteDecision("default", settings.sonarr_default_root, "Default TV library", 60)
