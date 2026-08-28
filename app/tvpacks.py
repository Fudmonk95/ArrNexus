from __future__ import annotations

import re
from dataclasses import dataclass


_EPISODE_RE = re.compile(r"(?i)\bS(\d{1,2})E(\d{1,3})(?:E(\d{1,3}))?\b")
_SEASON_RANGE_RE = re.compile(r"(?i)\bS(\d{1,2})\s*[-–]\s*S?(\d{1,2})\b")
_SEASON_WORD_RANGE_RE = re.compile(r"(?i)\bseasons?\s*(\d{1,2})\s*[-–]\s*(\d{1,2})\b")
_SEASON_SINGLE_RE = re.compile(r"(?i)\bS(\d{1,2})(?!E\d)\b")
_SEASON_WORD_SINGLE_RE = re.compile(r"(?i)\bseason\s*(\d{1,2})\b")

FULL_SERIES_TERMS = (
    "complete series", "complete show", "complete collection", "all seasons",
    "the complete series", "series complete", "complete seasons",
)
SEASON_PACK_TERMS = ("complete season", "season pack", "complete s", "full season")


@dataclass(frozen=True)
class PackInfo:
    kind: str
    label: str
    seasons: tuple[int, ...]
    episodes: tuple[tuple[int, int], ...]
    confidence: int

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "seasons": list(self.seasons),
            "episodes": [list(x) for x in self.episodes],
            "confidence": self.confidence,
        }


def _range(a: int, b: int) -> tuple[int, ...]:
    if a <= 0 or b <= 0:
        return ()
    lo, hi = sorted((a, b))
    if hi - lo > 60:
        return ()
    return tuple(range(lo, hi + 1))


def classify_release(title: str) -> PackInfo:
    raw = str(title or "")
    scan_text = raw.replace("_", " ").replace(".", " ")
    lower = scan_text.lower()
    episodes = []
    for m in _EPISODE_RE.finditer(scan_text):
        season = int(m.group(1)); e1 = int(m.group(2)); episodes.append((season, e1))
        if m.group(3):
            e2 = int(m.group(3))
            for ep in range(min(e1, e2) + 1, max(e1, e2) + 1):
                episodes.append((season, ep))

    seasons: set[int] = {s for s, _ in episodes}
    ranges: list[tuple[int, ...]] = []
    for rx in (_SEASON_RANGE_RE, _SEASON_WORD_RANGE_RE):
        for m in rx.finditer(scan_text):
            r = _range(int(m.group(1)), int(m.group(2)))
            ranges.append(r); seasons.update(r)
    for rx in (_SEASON_SINGLE_RE, _SEASON_WORD_SINGLE_RE):
        for m in rx.finditer(scan_text):
            seasons.add(int(m.group(1)))

    has_full_term = any(term in lower for term in FULL_SERIES_TERMS)
    has_season_pack_term = any(term in lower for term in SEASON_PACK_TERMS)
    has_multi_season_range = any(len(r) >= 2 for r in ranges)

    if has_full_term or has_multi_season_range:
        label = "Full series" if not seasons else f"Full series · S{min(seasons):02d}–S{max(seasons):02d}"
        return PackInfo("full_series", label, tuple(sorted(seasons)), tuple(sorted(set(episodes))), 96 if has_full_term else 90)
    if episodes:
        if len(set(episodes)) == 1:
            s, e = episodes[0]
            return PackInfo("episode", f"Episode · S{s:02d}E{e:02d}", tuple(sorted(seasons)), tuple(sorted(set(episodes))), 99)
        # Multi-episode bundles remain episode bundles unless the name says season/complete.
        if has_season_pack_term:
            s = min(seasons) if seasons else 0
            return PackInfo("season_pack", f"Season pack · S{s:02d}", tuple(sorted(seasons)), tuple(sorted(set(episodes))), 92)
        return PackInfo("episode_bundle", f"Episode bundle · {len(set(episodes))} episodes", tuple(sorted(seasons)), tuple(sorted(set(episodes))), 85)
    if seasons:
        if len(seasons) == 1:
            s = next(iter(seasons))
            return PackInfo("season_pack", f"Season pack · S{s:02d}", (s,), (), 88 if has_season_pack_term else 78)
        return PackInfo("full_series", f"Multi-season pack · S{min(seasons):02d}–S{max(seasons):02d}", tuple(sorted(seasons)), (), 84)
    return PackInfo("unknown", "Unknown pack type", (), (), 35)


def pack_matches(mode: str, info: PackInfo) -> bool:
    mode = (mode or "any").lower()
    if mode == "any":
        return True
    if mode == "full_series":
        return info.kind == "full_series"
    if mode == "season_pack":
        return info.kind == "season_pack"
    if mode == "episode":
        return info.kind in {"episode", "episode_bundle"}
    return True


def coverage_summary(info: PackInfo, expected_seasons: list[int] | None = None) -> dict:
    expected = sorted({int(x) for x in (expected_seasons or []) if int(x) > 0})
    got = sorted({int(x) for x in info.seasons if int(x) > 0})
    missing = [x for x in expected if x not in got]
    complete = bool(expected and not missing and all(x in got for x in expected))
    return {
        "expected": expected,
        "covered": got,
        "missing": missing,
        "complete": complete,
        "text": "Complete coverage" if complete else ("Covers " + ", ".join(f"S{x:02d}" for x in got) if got else "Season coverage unknown"),
    }


def choose_best_complete(releases: list[dict], expected_seasons: list[int] | None = None) -> dict | None:
    expected = sorted({int(x) for x in (expected_seasons or []) if int(x) > 0})
    candidates = []
    for row in releases:
        info = row.get("arrnexus_pack") or classify_release(row.get("title") or "").as_dict()
        if info.get("kind") != "full_series":
            continue
        covered = set(info.get("seasons") or [])
        if expected and covered and not set(expected).issubset(covered):
            continue
        if (row.get("arrnexus_policy") or {}).get("decision") == "rejected":
            continue
        candidates.append(row)
    if not candidates:
        return None
    return max(candidates, key=lambda x: (bool(x.get("realDebridCached")), int((x.get("arrnexus_policy") or {}).get("score") or 0), int(x.get("seeders") or 0)))


def choose_best_season_packs(releases: list[dict], expected_seasons: list[int] | None = None) -> tuple[list[dict], list[int]]:
    expected = sorted({int(x) for x in (expected_seasons or []) if int(x) > 0})
    by_season: dict[int, list[dict]] = {}
    for row in releases:
        info = row.get("arrnexus_pack") or classify_release(row.get("title") or "").as_dict()
        if info.get("kind") != "season_pack":
            continue
        seasons = info.get("seasons") or []
        if len(seasons) != 1:
            continue
        if (row.get("arrnexus_policy") or {}).get("decision") == "rejected":
            continue
        by_season.setdefault(int(seasons[0]), []).append(row)
    wanted = expected or sorted(by_season)
    chosen = []
    missing = []
    for season in wanted:
        rows = by_season.get(season) or []
        if not rows:
            missing.append(season); continue
        chosen.append(max(rows, key=lambda x: (bool(x.get("realDebridCached")), int((x.get("arrnexus_policy") or {}).get("score") or 0), int(x.get("seeders") or 0))))
    return chosen, missing
