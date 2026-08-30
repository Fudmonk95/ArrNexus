from __future__ import annotations

"""Combined-season analysis and conservative stream-copy splitting."""

import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from .db import cache_get, cache_set, setting_get, setting_set, log_event, add_activity
from .namespace import view_path, is_within_logical
from .paths import source_root
from .scanner import inspect_item, video_files, season_hints
from .router_service import existing_match_any, _client_for_instance, primary_client


def staging_root() -> Path:
    raw = (setting_get("tv_recovery.staging_root", "/data/split-cache") or "/data/split-cache").strip()
    path = Path(raw)
    if not path.is_absolute():
        raise RuntimeError("TV Recovery staging root must be an absolute path")
    return path


def save_staging_root(value: str) -> None:
    path = Path(str(value or "").strip())
    if not path.is_absolute():
        raise ValueError("Staging root must be an absolute path")
    setting_set("tv_recovery.staging_root", str(path))


def _probe(logical: Path) -> dict[str, Any]:
    actual = view_path(logical)
    cmd = [
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:chapter=start_time,end_time:chapter_tags=title",
        "-of", "json", str(actual),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45, check=False)
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
            start = float(row.get("start_time") or 0); end = float(row.get("end_time") or 0)
        except Exception:
            continue
        if end <= start:
            continue
        chapters.append({"index": idx, "start": start, "end": end, "duration": end-start, "title": str((row.get("tags") or {}).get("title") or f"Chapter {idx}")})
    return {"duration": duration, "chapters": chapters}


def _season_from_file(name: str, fallback: int = 0) -> int:
    hints = season_hints(name)
    return hints[0] if hints else int(fallback or 0)


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
    expected = {}
    for row in (series or {}).get("seasons") or []:
        try:
            no = int(row.get("seasonNumber") or 0)
        except Exception:
            continue
        if no <= 0:
            continue
        stats = row.get("statistics") or {}
        count = int(stats.get("episodeCount") or 0)
        if count:
            expected[no] = count
    return {"matched": True, "series": series, "instance": inst.instance if inst else "configured-main", "expected": expected}


def _boundaries(duration: float, expected: int, chapters: list[dict]) -> tuple[str, int, list[dict]]:
    if expected > 0 and len(chapters) == expected:
        return "chapters", 98, [{"episode": i+1, "start": ch["start"], "end": ch["end"], "source": ch.get("title") or f"Chapter {i+1}"} for i,ch in enumerate(chapters)]
    if expected > 0 and duration > 0:
        step = duration / expected
        return "runtime_estimate", 55, [{"episode": i+1, "start": step*i, "end": duration if i == expected-1 else step*(i+1), "source": "equal-runtime estimate"} for i in range(expected)]
    if chapters:
        return "chapters_unmatched", 70, [{"episode": i+1, "start": ch["start"], "end": ch["end"], "source": ch.get("title") or f"Chapter {i+1}"} for i,ch in enumerate(chapters)]
    return "manual", 0, []


async def analyse_source(source_path: str) -> dict[str, Any]:
    if not is_within_logical(source_path, source_root()):
        raise RuntimeError("TV Recovery only analyses DMM source paths")
    item = inspect_item(source_path)
    if item.media_type != "tv":
        raise RuntimeError("This source is not currently recognised as TV")
    sonarr = await _sonarr_context(item)
    fallback_season = item.season_numbers[0] if len(item.season_numbers) == 1 else 0
    files = []
    for logical in video_files(source_path):
        hints = season_hints(logical.name)
        # Individual episode files do not need splitting.
        from .scanner import episode_identity
        if episode_identity(logical.name):
            continue
        season = _season_from_file(logical.name, fallback_season)
        if not season:
            continue
        probe = _probe(logical)
        expected = int((sonarr.get("expected") or {}).get(season) or 0)
        mode, confidence, boundaries = _boundaries(probe["duration"], expected, probe["chapters"])
        files.append({
            "path": str(logical), "name": logical.name, "season": season,
            "duration": probe["duration"], "chapters": probe["chapters"],
            "expected_episodes": expected, "mode": mode, "confidence": confidence,
            "boundaries": boundaries,
        })
    payload = {"source_path": source_path, "item": item.dict(), "sonarr": sonarr, "files": files, "staging_root": str(staging_root())}
    raw = json.dumps({"source": source_path, "fingerprint": item.fingerprint, "files": files, "staging": str(staging_root())}, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    payload["digest"] = digest
    cache_set(f"tv_recovery:plan:{digest}", payload)
    return payload


def _safe_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._ -]+", "", str(value or "")).strip().rstrip(".")
    return text or "TV Recovery"


def _verify_file(path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"Generated file failed ffprobe: {path.name}")
    try: duration = float((proc.stdout or "0").strip())
    except Exception: duration = 0
    if duration < 1:
        raise RuntimeError(f"Generated file has an invalid duration: {path.name}")
    return duration


def split_plan(digest: str, file_path: str, allow_estimated: bool = False) -> dict[str, Any]:
    plan = cache_get(f"tv_recovery:plan:{digest}")
    if not isinstance(plan, dict):
        raise RuntimeError("TV Recovery plan expired; analyse the source again")
    current = inspect_item(str(plan.get("source_path") or ""))
    if current.fingerprint != str((plan.get("item") or {}).get("fingerprint") or ""):
        raise RuntimeError("Source changed after analysis; refusing a stale split plan")
    row = next((x for x in plan.get("files") or [] if str(x.get("path")) == str(file_path)), None)
    if not row:
        raise RuntimeError("Selected combined-season file is not part of this plan")
    mode = str(row.get("mode") or "manual")
    if mode == "runtime_estimate" and not allow_estimated:
        raise RuntimeError("Runtime-estimated boundaries require explicit confirmation before splitting")
    if mode not in {"chapters", "runtime_estimate", "chapters_unmatched"} or not row.get("boundaries"):
        raise RuntimeError("This file does not have usable automatic split boundaries; manual boundary editing is required")

    source = Path(str(row["path"])); actual = view_path(source)
    season = int(row.get("season") or 0)
    series = ((plan.get("sonarr") or {}).get("series") or {})
    show = _safe_name(str(series.get("title") or (plan.get("item") or {}).get("title_guess") or "TV Recovery"))
    outdir = staging_root() / show / f"Season {season:02d}"
    outdir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for b in row.get("boundaries") or []:
        ep = int(b.get("episode") or 0); start = float(b.get("start") or 0); end = float(b.get("end") or 0)
        if ep <= 0 or end <= start:
            continue
        suffix = source.suffix.lower() or ".mkv"
        output = outdir / f"{show} - S{season:02d}E{ep:02d}{suffix}"
        tmp = output.with_name(output.stem + ".partial" + output.suffix)
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(actual), "-t", f"{end-start:.3f}", "-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero", str(tmp)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=max(120, int((end-start)*1.5)), check=False)
        if proc.returncode != 0:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"FFmpeg split failed for S{season:02d}E{ep:02d}: {' '.join((proc.stderr or '').split())[:500]}")
        duration = _verify_file(tmp)
        tmp.replace(output)
        outputs.append({"episode": ep, "path": str(output), "duration": duration, "expected_duration": end-start})
    if not outputs:
        raise RuntimeError("No split outputs were generated")
    log_event("info", "tv_recovery", "season_split", f"Generated {len(outputs)} episode file(s) for {show} S{season:02d}", {"source": str(source), "mode": mode, "staging": str(outdir)})
    add_activity("tv_recovery", show, f"Split combined Season {season} into {len(outputs)} staged episode files", str(source))
    return {"ok": True, "show": show, "season": season, "mode": mode, "outputs": outputs, "staging": str(outdir), "source_retained": True}
