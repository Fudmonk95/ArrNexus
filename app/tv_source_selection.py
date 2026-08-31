from __future__ import annotations

"""Season-aware TV source selection for grouped ArrNexus imports.

The selector never deletes provider media. It only decides which episode file is
preferred when several DMM/recovered packs represent the same series, and marks
inferior packs as superseded for the UI/import planner.
"""

from pathlib import Path
from typing import Any, Callable

from .scanner import video_files, episode_span, season_hints, inspect_item
from .namespace import is_within_logical
from . import archive_media
from .process_control import CancelledOperation


def _is_recovered(source_path: str) -> bool:
    return is_within_logical(source_path, archive_media.extraction_root())


def source_inventory(source_path: str) -> dict[str, Any]:
    item = inspect_item(source_path)
    recovered = _is_recovered(source_path)
    individual: dict[int, dict[int, str]] = {}
    recovery_required: dict[int, list[str]] = {}
    unknown: list[str] = []
    for logical in video_files(source_path):
        span = episode_span(logical.name)
        if span and span[1] == span[2]:
            individual.setdefault(int(span[0]), {})[int(span[1])] = str(logical)
            continue
        seasons = [int(span[0])] if span else [int(x) for x in season_hints(logical.name)]
        if seasons:
            for season in seasons:
                recovery_required.setdefault(season, []).append(str(logical))
        else:
            unknown.append(str(logical))
    return {
        "path": source_path,
        "item": item,
        "recovered": recovered,
        "provenance": "Extracted RAR" if recovered else "DMM / provider source",
        "individual": individual,
        "recovery_required": recovery_required,
        "unknown": unknown,
        "seasons": sorted(set(individual) | set(recovery_required) | set(item.season_numbers or [])),
        "individual_count": sum(len(x) for x in individual.values()),
        "recovery_count": sum(len(x) for x in recovery_required.values()),
    }


def _static_candidate_rank(inv: dict[str, Any], season: int) -> tuple[int, int, int, int]:
    eps = inv.get("individual", {}).get(season, {})
    # Recovered individual episodes are preferred, then provider individuals.
    # Combined/recovery-required files are intentionally far below either.
    tier = 6 if inv.get("recovered") and eps else 5 if eps else 2 if inv.get("recovery_required", {}).get(season) else 1
    item = inv.get("item")
    return (tier, len(eps), int(getattr(item, "quality", 0) or 0), int(getattr(item, "size_bytes", 0) or 0))


def describe_group_sources(source_paths: list[str]) -> dict[str, Any]:
    inventories = [source_inventory(x) for x in dict.fromkeys(str(p) for p in source_paths if str(p))]
    seasons = sorted({s for inv in inventories for s in inv.get("seasons", [])})
    preferred_by_season: dict[int, str] = {}
    for season in seasons:
        candidates = [inv for inv in inventories if inv.get("individual", {}).get(season)]
        if candidates:
            best = max(candidates, key=lambda inv: _static_candidate_rank(inv, season))
            preferred_by_season[season] = str(best["path"])

    rows: list[dict[str, Any]] = []
    for inv in inventories:
        preferred: list[int] = []
        superseded: list[int] = []
        recovery: list[int] = []
        for season in inv.get("seasons", []):
            best_path = preferred_by_season.get(season)
            own_eps = set((inv.get("individual", {}).get(season) or {}).keys())
            if best_path == inv["path"]:
                preferred.append(season)
                continue
            if inv.get("recovery_required", {}).get(season):
                recovery.append(season)
            if best_path:
                best_inv = next((x for x in inventories if x["path"] == best_path), None)
                best_eps = set(((best_inv or {}).get("individual", {}).get(season) or {}).keys())
                if (own_eps and own_eps.issubset(best_eps)) or (not own_eps and inv.get("recovery_required", {}).get(season) and best_eps):
                    superseded.append(season)
        all_seasons = set(inv.get("seasons", []))
        status = "preferred" if preferred else "superseded" if all_seasons and set(superseded) >= all_seasons else "mixed"
        rows.append({
            "path": inv["path"],
            "preferred_seasons": preferred,
            "superseded_seasons": superseded,
            "recovery_seasons": recovery,
            "selection_status": status,
            "individual_count": inv["individual_count"],
            "recovery_count": inv["recovery_count"],
        })
    return {"sources": rows, "preferred_by_season": preferred_by_season, "seasons": seasons}


def _runtime_source_rank(source_path: str, quality: int, recovered: bool) -> tuple[int, int]:
    # Generated/split files live in the recovered tree and are already ffprobe
    # verified by TV Recovery. They therefore share the highest safe tier with
    # independently recovered episode files.
    return (6 if recovered else 5, int(quality or 0))


async def build_import_plan(source_paths: list[str], cancel_check: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Analyse every pack and choose the best safe file for each episode."""
    from . import tv_recovery  # late import avoids router_service cycle

    sources: list[dict[str, Any]] = []
    for source_path in dict.fromkeys(str(p) for p in source_paths if str(p)):
        if cancel_check and cancel_check():
            raise CancelledOperation("Grouped TV source analysis cancelled")
        item = inspect_item(source_path)
        analysis = await tv_recovery.analyse_source(source_path, cancel_check=cancel_check)
        safe: list[dict[str, Any]] = []
        recovery: list[dict[str, Any]] = []
        expected: dict[int, int] = {}
        for row in analysis.get("files") or []:
            season = int(row.get("season") or 0)
            if season <= 0:
                continue
            exp = int(row.get("expected_episodes") or 0)
            if exp:
                expected[season] = max(expected.get(season, 0), exp)
            first = int(row.get("episode_start") or 0)
            last = int(row.get("episode_end") or 0)
            if not row.get("needs_split") and first > 0 and first == last:
                safe.append({**row, "episode": first})
            else:
                recovery.append(dict(row))
        sources.append({
            "path": source_path,
            "item": item,
            "analysis": analysis,
            "safe": safe,
            "recovery": recovery,
            "expected": expected,
            "recovered": _is_recovered(source_path),
        })

    candidates: dict[tuple[int, int], list[dict[str, Any]]] = {}
    recovery_by_season: dict[int, list[dict[str, Any]]] = {}
    expected_by_season: dict[int, int] = {}
    for source in sources:
        for season, count in source["expected"].items():
            expected_by_season[season] = max(expected_by_season.get(season, 0), count)
        for row in source["safe"]:
            key = (int(row["season"]), int(row["episode"]))
            candidates.setdefault(key, []).append({"source": source, "row": row})
        for row in source["recovery"]:
            recovery_by_season.setdefault(int(row.get("season") or 0), []).append({"source": source, "row": row})

    selected_by_source: dict[str, list[str]] = {}
    selected_episodes: dict[int, set[int]] = {}
    selected_owner: dict[tuple[int, int], str] = {}
    for key, rows in candidates.items():
        best = max(rows, key=lambda x: _runtime_source_rank(x["source"]["path"], x["source"]["item"].quality, x["source"]["recovered"]))
        path = str(best["row"].get("path") or "")
        if not path:
            continue
        source_path = str(best["source"]["path"])
        selected_by_source.setdefault(source_path, []).append(path)
        selected_episodes.setdefault(key[0], set()).add(key[1])
        selected_owner[key] = source_path

    seasons = sorted(set(expected_by_season) | set(recovery_by_season) | set(selected_episodes))
    outcomes: list[dict[str, Any]] = []
    for season in seasons:
        episodes = sorted(selected_episodes.get(season, set()))
        expected = int(expected_by_season.get(season) or 0)
        recovery_rows = recovery_by_season.get(season, [])
        missing = max(0, expected - len(episodes)) if expected else 0
        if episodes and not recovery_rows and (not expected or len(episodes) >= expected):
            status = "ready"
            detail = f"{len(episodes)} episode(s) selected from preferred source(s)"
        elif episodes:
            status = "partial"
            detail = f"{len(episodes)} episode(s) ready"
            if recovery_rows:
                detail += f"; {len(recovery_rows)} combined/joined source file(s) need TV Recovery"
            if missing:
                detail += f"; {missing} expected episode(s) still unavailable"
        elif recovery_rows:
            status = "recovery_required"
            detail = "TV Recovery required before this season can import"
        else:
            status = "unavailable"
            detail = "No verified individual episode source is available"
        outcomes.append({
            "season": season,
            "status": status,
            "selected_episodes": episodes,
            "selected_count": len(episodes),
            "expected_count": expected,
            "missing_count": missing,
            "recovery_count": len(recovery_rows),
            "detail": detail,
        })

    source_summaries: list[dict[str, Any]] = []
    for source in sources:
        own_safe_keys = {(int(x["season"]), int(x["episode"])) for x in source["safe"]}
        chosen_keys = {key for key, owner in selected_owner.items() if owner == source["path"]}
        lost_keys = {key for key in own_safe_keys if key in selected_owner and selected_owner[key] != source["path"]}
        source_summaries.append({
            "path": source["path"],
            "selected_files": selected_by_source.get(source["path"], []),
            "selected_episode_count": len(chosen_keys),
            "superseded_episode_count": len(lost_keys),
            "superseded": bool(not chosen_keys and lost_keys),
            "recovered": source["recovered"],
        })

    return {
        "source_paths": [x["path"] for x in sources],
        "selected_by_source": selected_by_source,
        "seasons": outcomes,
        "sources": source_summaries,
        "ready_seasons": [x["season"] for x in outcomes if x["status"] in {"ready", "partial"} and x["selected_count"]],
        "recovery_seasons": [x["season"] for x in outcomes if x["status"] in {"partial", "recovery_required"}],
        "unavailable_seasons": [x["season"] for x in outcomes if x["status"] == "unavailable"],
    }


def plan_summary(plan: dict[str, Any], *, imported: bool = False) -> str:
    bits: list[str] = []
    for row in plan.get("seasons") or []:
        season = int(row.get("season") or 0)
        status = str(row.get("status") or "")
        if status == "ready":
            bits.append(f"Season {season} {'imported' if imported else 'ready'}")
        elif status == "partial":
            bits.append(f"Season {season} {'partially imported' if imported else 'partially ready'}")
        elif status == "recovery_required":
            bits.append(f"Season {season} needs recovery")
        else:
            bits.append(f"Season {season} unavailable")
    return " · ".join(bits)
