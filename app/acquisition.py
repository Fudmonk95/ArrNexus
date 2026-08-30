from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any

from .db import setting_get, setting_set
from .policy import load_policy, score_release
from . import realdebrid as rd


STRATEGIES = (
    "automatic",
    "debrid_first",
    "usenet_first",
    "debrid_only",
    "usenet_only",
    "fastest",
    "quality",
)


@dataclass
class AcquisitionSettings:
    default_strategy: str = "automatic"
    native_search_fallback: bool = True
    prefer_cached_debrid: bool = True
    max_candidates: int = 100

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_acquisition_settings() -> AcquisitionSettings:
    strategy = setting_get("acquisition.default_strategy", "automatic") or "automatic"
    if strategy not in STRATEGIES:
        strategy = "automatic"
    return AcquisitionSettings(
        default_strategy=strategy,
        native_search_fallback=setting_get("acquisition.native_search_fallback", "true").lower() in {"1","true","yes","on"},
        prefer_cached_debrid=setting_get("acquisition.prefer_cached_debrid", "true").lower() in {"1","true","yes","on"},
        max_candidates=max(10, min(300, int(setting_get("acquisition.max_candidates", "100") or 100))),
    )


def save_acquisition_settings(default_strategy: str, native_search_fallback: bool, prefer_cached_debrid: bool, max_candidates: int = 100) -> None:
    strategy = default_strategy if default_strategy in STRATEGIES else "automatic"
    setting_set("acquisition.default_strategy", strategy)
    setting_set("acquisition.native_search_fallback", "true" if native_search_fallback else "false")
    setting_set("acquisition.prefer_cached_debrid", "true" if prefer_cached_debrid else "false")
    setting_set("acquisition.max_candidates", str(max(10, min(300, int(max_candidates or 100)))))


def normalize_protocol(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"usenet", "nzb"}:
        return "usenet"
    if raw in {"torrent", "debrid", "real-debrid", "realdebrid"}:
        return "torrent"
    return raw


def _release_hash(row: dict) -> str:
    h = str(row.get("infoHash") or row.get("infohash") or row.get("hash") or "").strip()
    if h:
        return h.lower()
    magnet = str(row.get("magnetUrl") or row.get("magnet") or "")
    m = re.search(r"(?i)btih:([a-z0-9]{32,40})", magnet)
    return m.group(1).lower() if m else ""


async def annotate_debrid_cache(rows: list[dict], limit: int = 40) -> None:
    """Annotate torrent releases with Real-Debrid cache state when possible.

    A missing hash does not make a release invalid; it simply means ArrNexus
    cannot preflight the provider cache before Radarr/Sonarr grabs it.
    """
    if not rd.connected():
        return
    checked = 0
    for row in rows:
        if checked >= limit:
            break
        if normalize_protocol(row.get("protocol")) != "torrent":
            continue
        h = _release_hash(row)
        if not h:
            continue
        checked += 1
        try:
            payload = await rd.instant_availability(h)
            node = (payload or {}).get(h) or (payload or {}).get(h.lower()) or {}
            cached = False
            if isinstance(node, dict):
                cached = any(bool(v) for v in node.values())
            row["realDebridCached"] = cached
        except Exception:
            row["realDebridCached"] = False


def _allowed(row: dict) -> bool:
    return str((row.get("arrnexus_policy") or {}).get("decision") or "allowed") != "rejected"


def _sort_key(row: dict, *, prefer_cached: bool = True) -> tuple:
    p = row.get("arrnexus_policy") or {}
    return (
        1 if prefer_cached and row.get("realDebridCached") else 0,
        int(p.get("score") or 0),
        int(row.get("seeders") or 0),
        -int(row.get("size") or 0),
    )


def rank_releases(rows: list[dict], media_type: str, prefer_cached: bool | None = None) -> list[dict]:
    cfg = load_acquisition_settings()
    if prefer_cached is None:
        prefer_cached = cfg.prefer_cached_debrid
    policy = load_policy()
    ranked: list[dict] = []
    for raw in rows or []:
        row = dict(raw)
        proto = normalize_protocol(row.get("protocol"))
        row["arrnexus_protocol"] = proto
        row["arrnexus_policy"] = score_release(row, policy, media_type=media_type, pack_type="")
        ranked.append(row)
    ranked.sort(key=lambda r: _sort_key(r, prefer_cached=bool(prefer_cached)), reverse=True)
    return ranked


def _best(rows: list[dict], protocol: str | None = None, prefer_cached: bool = True) -> dict | None:
    candidates = [r for r in rows if _allowed(r) and (not protocol or r.get("arrnexus_protocol") == protocol)]
    if not candidates:
        return None
    candidates.sort(key=lambda r: _sort_key(r, prefer_cached=prefer_cached), reverse=True)
    return candidates[0]


def choose_release(rows: list[dict], strategy: str, *, prefer_cached: bool = True) -> tuple[dict | None, list[str]]:
    strategy = strategy if strategy in STRATEGIES else "automatic"
    reasons: list[str] = []
    usenet = [r for r in rows if r.get("arrnexus_protocol") == "usenet" and _allowed(r)]
    torrent = [r for r in rows if r.get("arrnexus_protocol") == "torrent" and _allowed(r)]

    if strategy == "usenet_only":
        reasons.append(f"Usenet only: {len(usenet)} acceptable candidate(s)")
        return _best(rows, "usenet", prefer_cached=False), reasons
    if strategy == "debrid_only":
        reasons.append(f"Debrid only: {len(torrent)} acceptable torrent candidate(s)")
        return _best(rows, "torrent", prefer_cached=prefer_cached), reasons
    if strategy == "usenet_first":
        first = _best(rows, "usenet", prefer_cached=False)
        if first:
            reasons.append(f"Usenet-first selected from {len(usenet)} acceptable Usenet result(s)")
            return first, reasons
        reasons.append("No acceptable Usenet release; falling back to Debrid/torrent")
        return _best(rows, "torrent", prefer_cached=prefer_cached), reasons
    if strategy == "debrid_first":
        first = _best(rows, "torrent", prefer_cached=prefer_cached)
        if first:
            reasons.append(f"Debrid-first selected from {len(torrent)} acceptable torrent result(s)")
            return first, reasons
        reasons.append("No acceptable Debrid/torrent release; falling back to Usenet")
        return _best(rows, "usenet", prefer_cached=False), reasons
    if strategy == "fastest":
        cached = [r for r in torrent if r.get("realDebridCached")]
        if cached:
            cached.sort(key=lambda r: _sort_key(r, prefer_cached=True), reverse=True)
            reasons.append("Fastest mode preferred an instantly cached Real-Debrid release")
            return cached[0], reasons
        if usenet:
            reasons.append("No verified RD-cached result; fastest mode fell back to the best Usenet candidate")
            return _best(rows, "usenet", prefer_cached=False), reasons
        reasons.append("No Usenet result; using best available torrent candidate")
        return _best(rows, "torrent", prefer_cached=prefer_cached), reasons

    # quality and automatic both compare the whole candidate set. Automatic
    # may favour cached RD according to the configured policy; Quality removes
    # the cache bonus and lets release score dominate.
    chosen = _best(rows, None, prefer_cached=(prefer_cached and strategy != "quality"))
    if chosen:
        reasons.append(
            "Quality mode selected the highest scoring acceptable release"
            if strategy == "quality"
            else "Automatic mode compared Usenet and torrent candidates and selected the best scored release"
        )
    return chosen, reasons


async def plan_and_grab(client: Any, media_type: str, arr_id: int, strategy: str | None = None) -> dict[str, Any]:
    cfg = load_acquisition_settings()
    strategy = strategy if strategy in STRATEGIES else cfg.default_strategy
    if media_type == "movie":
        raw = await client.releases(int(arr_id))
    else:
        raw = await client.releases(int(arr_id))
    rows = list(raw or [])[: cfg.max_candidates]
    await annotate_debrid_cache(rows)
    ranked = rank_releases(rows, media_type, cfg.prefer_cached_debrid)
    chosen, reasons = choose_release(ranked, strategy, prefer_cached=cfg.prefer_cached_debrid)
    counts = {
        "total": len(ranked),
        "usenet": sum(1 for r in ranked if r.get("arrnexus_protocol") == "usenet"),
        "torrent": sum(1 for r in ranked if r.get("arrnexus_protocol") == "torrent"),
        "rd_cached": sum(1 for r in ranked if r.get("arrnexus_protocol") == "torrent" and r.get("realDebridCached")),
        "rejected": sum(1 for r in ranked if not _allowed(r)),
    }
    if not chosen:
        return {
            "ok": False,
            "strategy": strategy,
            "counts": counts,
            "reasons": reasons + ["No acceptable release matched the selected acquisition strategy"],
            "releases": ranked[:20],
        }
    await client.grab_release(chosen)
    return {
        "ok": True,
        "strategy": strategy,
        "counts": counts,
        "reasons": reasons,
        "selected": chosen,
        "protocol": chosen.get("arrnexus_protocol") or normalize_protocol(chosen.get("protocol")),
        "indexer": chosen.get("indexer") or chosen.get("indexerName") or "",
        "title": chosen.get("title") or "",
        "score": int((chosen.get("arrnexus_policy") or {}).get("score") or 0),
        "cached": bool(chosen.get("realDebridCached")),
        "releases": ranked[:20],
    }
