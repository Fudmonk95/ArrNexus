from __future__ import annotations

"""Advanced TV recovery for combined seasons and joined episode files.

v10.4.4 treats filenames as evidence, not truth.  Sonarr supplies owned-series
season counts when available; TMDb fills gaps and supplies episode-runtime
samples.  ffprobe then checks the actual video duration so a file named E06 can
still be flagged as a likely E06-E07 join when it runs for roughly two normal
episodes.
"""

import asyncio
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Callable

from .db import cache_get, cache_set, setting_get, setting_set, log_event, add_activity
from .namespace import view_path, is_within_logical
from .paths import source_root, dumb_root
from . import archive_media, media_identity
from .scanner import inspect_item, video_files, season_hints, episode_span
from .router_service import existing_match_any, _client_for_instance, primary_client
from .process_control import run_cancellable, CancelledOperation


def staging_root() -> Path:
    # Keep all generated media in the DUMB-visible recovery namespace. The
    # legacy application-volume location is migrated at runtime.
    raw = (setting_get("tv_recovery.staging_root", "") or "").strip()
    if not raw or raw == "/data/split-cache":
        raw = archive_media.extraction_root()
    path = Path(raw)
    if not path.is_absolute() or not is_within_logical(path, dumb_root()):
        raise RuntimeError("TV Recovery output root must be an absolute DUMB-visible path")
    return path


def save_staging_root(value: str) -> None:
    path = Path(str(value or "").strip())
    if not path.is_absolute() or not is_within_logical(path, dumb_root()):
        raise ValueError(f"TV Recovery output root must live under {dumb_root()}")
    setting_set("tv_recovery.staging_root", str(path))


def _probe(logical: Path, cancel_check: Callable[[], bool] | None = None) -> dict[str, Any]:
    actual = view_path(logical)
    cmd = [
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:chapter=start_time,end_time:chapter_tags=title",
        "-of", "json", str(actual),
    ]
    try:
        proc = run_cancellable(cmd, capture_output=True, text=True, timeout=90, check=False, cancel_check=cancel_check)
    except FileNotFoundError as exc:
        raise RuntimeError("ffprobe is not installed in the ArrNexus container") from exc
    if proc.returncode != 0:
        raise RuntimeError(" ".join((proc.stderr or proc.stdout or "ffprobe failed").split())[:500])
    try:
        data = json.loads(proc.stdout or "{}")
    except Exception as exc:
        raise RuntimeError("ffprobe returned invalid JSON") from exc
    duration = float(((data.get("format") or {}).get("duration") or 0) or 0)
    chapters = []
    for idx, row in enumerate(data.get("chapters") or [], start=1):
        try:
            start = float(row.get("start_time") or 0)
            end = float(row.get("end_time") or 0)
        except Exception:
            continue
        if end <= start:
            continue
        chapters.append({
            "index": idx, "start": start, "end": end, "duration": end - start,
            "title": str((row.get("tags") or {}).get("title") or f"Chapter {idx}"),
        })
    return {"duration": duration, "chapters": chapters}


def _file_signature(logical: Path, duration: float) -> str:
    """Stable-enough selected-file guard that survives other pack splits."""
    h = hashlib.sha256(str(logical).encode("utf-8", errors="replace"))
    try:
        h.update(f":{view_path(logical).stat().st_size}".encode())
    except OSError:
        h.update(b":missing")
    h.update(f":{int(float(duration or 0) * 1000)}".encode())
    return h.hexdigest()


def _season_from_file(name: str, fallback: int = 0) -> int:
    span = episode_span(name)
    if span:
        return int(span[0])
    hints = season_hints(name)
    return hints[0] if len(hints) == 1 else int(fallback or 0)


async def _sonarr_context(item) -> dict[str, Any]:
    existing, inst, client = await existing_match_any(item)
    if not existing:
        return {"matched": False, "series": None, "instance": None, "expected": {}}
    if client is None:
        client = _client_for_instance(inst) if inst and inst.api_key else primary_client("sonarr")
    try:
        series = await client.series_by_id(int(existing.get("id") or 0))
    except Exception:
        series = existing
    expected: dict[int, int] = {}
    for row in (series or {}).get("seasons") or []:
        try:
            no = int(row.get("seasonNumber") or 0)
            count = int(((row.get("statistics") or {}).get("episodeCount") or 0))
        except Exception:
            continue
        if no > 0 and count > 0:
            expected[no] = count
    return {
        "matched": True,
        "series": series,
        "instance": inst.instance if inst else "configured-main",
        "expected": expected,
    }


async def _tmdb_context(identity: dict[str, Any] | None, seasons: list[int]) -> dict[str, Any]:
    tmdb_id = int((identity or {}).get("tmdb_id") or 0)
    if not tmdb_id or str((identity or {}).get("media_type") or "") != "tv":
        return {"configured": media_identity.tmdb_configured(), "tmdb_id": None, "seasons": {}}
    wanted = sorted({int(x) for x in seasons if int(x) > 0})
    results = await asyncio.gather(
        *(media_identity.tmdb_tv_season(tmdb_id, season) for season in wanted),
        return_exceptions=True,
    )
    out: dict[int, dict[str, Any]] = {}
    for season, result in zip(wanted, results):
        if isinstance(result, dict) and result:
            out[season] = result
    return {"configured": media_identity.tmdb_configured(), "tmdb_id": tmdb_id, "seasons": out}


def _runtime_multiple(duration: float, typical_minutes: float) -> tuple[int, float]:
    """Return likely episode count and ratio to normal episode runtime."""
    if duration <= 0 or typical_minutes <= 0:
        return 0, 0.0
    ratio = duration / (typical_minutes * 60.0)
    likely = max(1, min(24, int(round(ratio))))
    return likely, ratio


def _boundaries(duration: float, count: int, chapters: list[dict], *, episode_start: int = 1, estimate_confidence: int = 55) -> tuple[str, int, list[dict]]:
    if count > 0 and len(chapters) == count:
        return "chapters", 98, [
            {"episode": episode_start + i, "start": ch["start"], "end": ch["end"], "source": ch.get("title") or f"Chapter {i+1}"}
            for i, ch in enumerate(chapters)
        ]
    if count > 0 and duration > 0:
        step = duration / count
        return "runtime_estimate", estimate_confidence, [
            {
                "episode": episode_start + i,
                "start": step * i,
                "end": duration if i == count - 1 else step * (i + 1),
                "source": "runtime estimate — confirm before splitting",
            }
            for i in range(count)
        ]
    if chapters:
        return "chapters_unmatched", 70, [
            {"episode": episode_start + i, "start": ch["start"], "end": ch["end"], "source": ch.get("title") or f"Chapter {i+1}"}
            for i, ch in enumerate(chapters)
        ]
    return "manual", 0, []


def _file_analysis(*, logical: Path, probe: dict[str, Any], season: int, span: tuple[int, int, int] | None, expected_season: int, typical_minutes: float) -> dict[str, Any]:
    duration = float(probe.get("duration") or 0)
    chapters = list(probe.get("chapters") or [])
    likely_count, ratio = _runtime_multiple(duration, typical_minutes)
    episode_start = int(span[1]) if span else 1
    explicit_count = int(span[2] - span[1] + 1) if span else 0
    detection = "season_combined" if not span else "single_episode"
    expected_split = 0
    estimate_confidence = 55

    if span and explicit_count > 1:
        detection = "explicit_multi_episode"
        expected_split = explicit_count
        estimate_confidence = 88 if typical_minutes and abs(ratio - explicit_count) <= 0.45 else 78
    elif span and explicit_count == 1:
        # Filename says one episode, but runtime can prove that it deserves
        # joined-episode review. Do not silently trust E06 when it lasts ~2x.
        remaining = max(0, int(expected_season or 0) - episode_start + 1)
        if typical_minutes and likely_count >= 2 and ratio >= 1.60 and (not remaining or likely_count <= remaining):
            detection = "runtime_multi_episode"
            expected_split = likely_count
            estimate_confidence = 82 if abs(ratio - likely_count) <= 0.35 else 68
        else:
            return {
                "path": str(logical), "name": logical.name, "season": season,
                "duration": duration, "chapters": chapters,
                "expected_episodes": expected_season, "typical_runtime_minutes": typical_minutes,
                "runtime_ratio": ratio, "detected_episode_count": 1,
                "episode_start": episode_start, "episode_end": episode_start,
                "detection": "single_episode", "mode": "single", "confidence": 96 if typical_minutes else 90,
                "boundaries": [], "needs_split": False,
            }
    else:
        expected_split = int(expected_season or 0)
        episode_start = 1
        if expected_split <= 0:
            return {
                "path": str(logical), "name": logical.name, "season": season,
                "duration": duration, "chapters": chapters,
                "expected_episodes": 0, "typical_runtime_minutes": typical_minutes,
                "runtime_ratio": ratio, "detected_episode_count": 0,
                "episode_start": 1, "episode_end": 0,
                "detection": "season_combined", "mode": "manual", "confidence": 0,
                "boundaries": [], "needs_split": True,
            }
        if typical_minutes:
            total_ratio = duration / (typical_minutes * 60.0 * expected_split)
            estimate_confidence = 68 if 0.75 <= total_ratio <= 1.30 else 55

    mode, confidence, boundaries = _boundaries(
        duration, expected_split, chapters,
        episode_start=episode_start,
        estimate_confidence=estimate_confidence,
    )
    episode_end = episode_start + expected_split - 1 if expected_split else 0
    return {
        "path": str(logical), "name": logical.name, "season": season,
        "duration": duration, "chapters": chapters,
        "expected_episodes": expected_season, "typical_runtime_minutes": typical_minutes,
        "runtime_ratio": ratio, "detected_episode_count": expected_split,
        "episode_start": episode_start, "episode_end": episode_end,
        "detection": detection, "mode": mode, "confidence": confidence,
        "boundaries": boundaries, "needs_split": True,
    }


async def analyse_source(source_path: str, cancel_check: Callable[[], bool] | None = None) -> dict[str, Any]:
    if not (
        is_within_logical(source_path, source_root())
        or is_within_logical(source_path, archive_media.extraction_root())
        or is_within_logical(source_path, staging_root())
    ):
        raise RuntimeError("TV Recovery only analyses DMM provider sources or ArrNexus recovered-media sources")

    detected_item = inspect_item(source_path)
    item, identity = media_identity.apply_to_item(detected_item)
    if item.media_type != "tv":
        raise RuntimeError("This source is not currently recognised as TV")

    all_files = video_files(source_path)
    seasons = set(item.season_numbers or [])
    for logical in all_files:
        if cancel_check and cancel_check():
            raise CancelledOperation("TV recovery analysis cancelled")
        span = episode_span(logical.name)
        if span:
            seasons.add(span[0])
        seasons.update(season_hints(logical.name))

    sonarr = await _sonarr_context(item)
    tmdb = await _tmdb_context(identity, sorted(seasons))
    fallback_season = item.season_numbers[0] if len(item.season_numbers) == 1 else 0
    files: list[dict[str, Any]] = []

    for logical in all_files:
        if cancel_check and cancel_check():
            raise CancelledOperation("TV recovery analysis cancelled")
        span = episode_span(logical.name)
        season = _season_from_file(logical.name, fallback_season)
        if not season:
            continue
        probe = _probe(logical, cancel_check=cancel_check)
        sonarr_expected = int((sonarr.get("expected") or {}).get(season) or 0)
        tmdb_season = ((tmdb.get("seasons") or {}).get(season) or {})
        tmdb_expected = int(tmdb_season.get("episode_count") or 0)
        expected = sonarr_expected or tmdb_expected
        typical = float(tmdb_season.get("typical_runtime_minutes") or 0)
        row = _file_analysis(
            logical=logical, probe=probe, season=season, span=span,
            expected_season=expected, typical_minutes=typical,
        )
        row["source_signature"] = _file_signature(logical, probe.get("duration") or 0)
        row["episode_count_source"] = "sonarr" if sonarr_expected else "tmdb" if tmdb_expected else "unknown"
        row["runtime_source"] = "tmdb" if typical else "unknown"
        files.append(row)

    payload = {
        "source_path": source_path,
        "item": item.dict(),
        "detected_item": detected_item.dict(),
        "identity": identity,
        "sonarr": sonarr,
        "tmdb": tmdb,
        "files": files,
        "staging_root": str(staging_root()),
    }
    raw = json.dumps(
        {"source": source_path, "fingerprint": item.fingerprint, "files": files, "staging": str(staging_root())},
        sort_keys=True, separators=(",", ":"),
    )
    digest = hashlib.sha256(raw.encode()).hexdigest()
    payload["digest"] = digest
    cache_set(f"tv_recovery:plan:{digest}", payload)
    return payload


def _safe_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._ -]+", "", str(value or "")).strip().rstrip(".")
    return text or "TV Recovery"


def _verify_file(path: Path, cancel_check: Callable[[], bool] | None = None) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    proc = run_cancellable(cmd, capture_output=True, text=True, timeout=60, check=False, cancel_check=cancel_check)
    if proc.returncode != 0:
        raise RuntimeError(f"Generated file failed ffprobe: {path.name}")
    try:
        duration = float((proc.stdout or "0").strip())
    except Exception:
        duration = 0
    if duration < 1:
        raise RuntimeError(f"Generated file has an invalid duration: {path.name}")
    return duration


def _archive_superseded_recovered_source(source: Path) -> str | None:
    """Hide a successfully split recovered combined file from future scans.

    The original provider RAR is still retained, and the recovered combined
    file remains available in ``.arrnexus-originals`` for rollback.  Scanner
    code intentionally excludes this directory so only generated episodes enter
    Inbox/import flows.
    """
    if not is_within_logical(source, archive_media.extraction_root()):
        return None
    actual = view_path(source)
    if not actual.is_file():
        return None
    hidden_logical = source.parent / ".arrnexus-originals" / source.name
    hidden_actual = view_path(hidden_logical)
    hidden_actual.parent.mkdir(parents=True, exist_ok=True)
    if hidden_actual.exists():
        return str(hidden_logical)
    shutil.move(str(actual), str(hidden_actual))
    return str(hidden_logical)


def split_plan(digest: str, file_path: str, allow_estimated: bool = False, cancel_check: Callable[[], bool] | None = None) -> dict[str, Any]:
    plan = cache_get(f"tv_recovery:plan:{digest}")
    if not isinstance(plan, dict):
        raise RuntimeError("TV Recovery plan expired; analyse the source again")
    row = next((x for x in plan.get("files") or [] if str(x.get("path")) == str(file_path)), None)
    if not row:
        raise RuntimeError("Selected TV file is not part of this recovery plan")
    source = Path(str(row["path"]))
    current_probe = _probe(source, cancel_check=cancel_check)
    if _file_signature(source, current_probe.get("duration") or 0) != str(row.get("source_signature") or ""):
        raise RuntimeError("Selected TV file changed after analysis; analyse it again before splitting")
    if not row.get("needs_split") or str(row.get("mode") or "") == "single":
        raise RuntimeError("This file is already a normal single episode and does not need splitting")

    mode = str(row.get("mode") or "manual")
    if mode == "runtime_estimate" and not allow_estimated:
        raise RuntimeError("Runtime-estimated boundaries require explicit confirmation before splitting")
    if mode not in {"chapters", "runtime_estimate", "chapters_unmatched"} or not row.get("boundaries"):
        raise RuntimeError("This file does not have usable automatic split boundaries; manual boundary editing is required")

    actual = view_path(source)
    season = int(row.get("season") or 0)
    series = ((plan.get("sonarr") or {}).get("series") or {})
    identity = plan.get("identity") if isinstance(plan.get("identity"), dict) else None
    show = _safe_name(str((identity or {}).get("title") or series.get("title") or (plan.get("item") or {}).get("title_guess") or "TV Recovery"))

    if is_within_logical(source, archive_media.extraction_root()):
        outdir_logical = source.parent / f"Season {season:02d}"
    else:
        outdir_logical = staging_root() / show / f"Season {season:02d}"
    outdir = view_path(outdir_logical)
    outdir.mkdir(parents=True, exist_ok=True)

    outputs = []
    for b in row.get("boundaries") or []:
        if cancel_check and cancel_check():
            raise CancelledOperation("TV episode split cancelled")
        ep = int(b.get("episode") or 0)
        start = float(b.get("start") or 0)
        end = float(b.get("end") or 0)
        if ep <= 0 or end <= start:
            continue
        suffix = source.suffix.lower() or ".mkv"
        output_name = f"{show} - S{season:02d}E{ep:02d}{suffix}"
        output_logical = outdir_logical / output_name
        output = outdir / output_name
        tmp = output.with_name(output.stem + ".partial" + output.suffix)
        cmd = [
            "ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(actual),
            "-t", f"{end-start:.3f}", "-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero", str(tmp),
        ]
        try:
            proc = run_cancellable(cmd, capture_output=True, text=True, timeout=max(180, int((end - start) * 1.75)), check=False, cancel_check=cancel_check)
        except CancelledOperation:
            tmp.unlink(missing_ok=True)
            raise
        if proc.returncode != 0:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"FFmpeg split failed for S{season:02d}E{ep:02d}: {' '.join((proc.stderr or '').split())[:500]}")
        duration = _verify_file(tmp, cancel_check=cancel_check)
        tmp.replace(output)
        outputs.append({"episode": ep, "path": str(output_logical), "duration": duration, "expected_duration": end - start})

    if not outputs:
        raise RuntimeError("No split outputs were generated")

    superseded = _archive_superseded_recovered_source(source)
    refreshed = inspect_item(str(plan.get("source_path") or source.parent))
    if identity:
        media_identity.save_identity(refreshed.path, refreshed.fingerprint, identity)

    log_event("info", "tv_recovery", "episode_split", f"Generated {len(outputs)} episode file(s) for {show} S{season:02d}", {
        "source": str(source), "mode": mode, "staging": str(outdir_logical), "superseded": superseded,
    })
    add_activity("tv_recovery", show, f"Split {source.name} into {len(outputs)} recovered episode files", str(source))
    return {
        "ok": True, "show": show, "season": season, "mode": mode,
        "outputs": outputs, "staging": str(outdir_logical),
        "source_retained": True, "superseded_source": superseded,
    }
