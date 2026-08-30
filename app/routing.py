from __future__ import annotations
from dataclasses import dataclass
from .config import settings


@dataclass
class RouteDecision:
    key: str
    root: str
    reason: str
    confidence: int


CHRISTMAS_WORDS = {
    "christmas", "xmas", "santa", "noel", "mistletoe", "holiday", "nativity"
}
EASTER_WORDS = {"easter", "bunny", "resurrection"}
HALLOWEEN_WORDS = {
    "halloween", "scream", "dracula", "frankenstein", "vampire", "werewolf",
    "exorcist", "haunting", "horror", "nightmare", "slasher"
}
KIDS_GENRES = {"family", "animation", "children", "kids"}
HORROR_GENRES = {"horror"}


def lower_set(values):
    return {str(v).strip().lower() for v in (values or []) if str(v).strip()}


def decide_movie(title: str, metadata: dict | None = None) -> RouteDecision:
    text = title.lower()
    genres = lower_set((metadata or {}).get("genres"))

    if any(word in text for word in CHRISTMAS_WORDS):
        return RouteDecision("christmas", settings.radarr_christmas_root, "Christmas keyword", 95)
    if any(word in text for word in EASTER_WORDS):
        return RouteDecision("easter", settings.radarr_easter_root, "Easter keyword", 95)
    if any(word in text for word in HALLOWEEN_WORDS) or genres & HORROR_GENRES:
        return RouteDecision("halloween", settings.radarr_halloween_root, "Horror/Halloween match", 85)
    if genres & KIDS_GENRES:
        return RouteDecision("kids", settings.radarr_kids_root, "Family/animation genre", 80)
    return RouteDecision("default", settings.radarr_default_root, "Default movie library", 60)


def decide_tv(title: str, metadata: dict | None = None) -> RouteDecision:
    metadata = metadata or {}
    network = str(metadata.get("network") or "").lower()
    genres = lower_set(metadata.get("genres"))

    if genres & KIDS_GENRES or any(k in network for k in ["nickelodeon", "cartoon network", "cbbc", "cbeebies"]):
        return RouteDecision("kids", settings.sonarr_kids_root, "Kids/family series", 85)
    if "netflix" in network:
        return RouteDecision("netflix", settings.sonarr_netflix_root, "Netflix network", 95)
    if "disney+" in network or "disney plus" in network:
        return RouteDecision("disney", settings.sonarr_disney_root, "Disney+ network", 95)
    if "amazon" in network or "prime video" in network:
        return RouteDecision("amazon", settings.sonarr_amazon_root, "Amazon/Prime network", 95)
    if "apple tv+" in network or "apple tv" in network:
        return RouteDecision("apple", settings.sonarr_apple_root, "Apple TV+ network", 95)
    if "bbc" in network or "cbbc" in network or "cbeebies" in network:
        return RouteDecision("bbc", settings.sonarr_bbc_root, "BBC network", 95)
    return RouteDecision("default", settings.sonarr_default_root, "Default TV library", 60)
