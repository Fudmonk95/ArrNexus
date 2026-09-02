from __future__ import annotations

"""Persistent state helpers for the v10.7+ one-click recovery pipeline.

The worker lives in ``main`` because routing/import operations are async.  This
module owns the stable stage contract, job-local logging and exact split-state
records shared by the worker, UI and validators.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json

from .db import add_recovery_job_log, cache_get, cache_set, update_job, update_job_item


STAGES = (
    ("inspecting", "Inspect archive", 4),
    ("provider_verification", "Provider verification", 12),
    ("direct_recovery", "Direct recovery", 23),
    ("extraction", "Verified extraction", 36),
    ("indexing", "Recovered-media indexing", 44),
    ("language", "Language advisory", 53),
    ("tv_analysis", "TV analysis", 63),
    ("tv_splitting", "Safe TV splitting", 78),
    ("ready_to_import", "Build import plan", 88),
    ("importing", "Sonarr / Radarr import", 95),
    ("imported", "Imported", 100),
)
STAGE_INDEX = {key: index for index, (key, _label, _pct) in enumerate(STAGES)}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def stage_rows(current: str, *, paused: bool = False, failed: bool = False) -> list[dict[str, Any]]:
    current_index = STAGE_INDEX.get(current, -1)
    rows = []
    for index, (key, label, percent) in enumerate(STAGES):
        state = "complete" if index < current_index else "active" if index == current_index else "pending"
        if index == current_index and paused:
            state = "paused"
        if index == current_index and failed:
            state = "failed"
        rows.append({"key": key, "label": label, "percent": percent, "state": state})
    return rows


def set_stage(job_id: int, item_id: int, stage: str, operation: str, detail: str = "", *, progress: int | None = None) -> None:
    default = next((pct for key, _label, pct in STAGES if key == stage), 0)
    pct = max(0, min(100, int(default if progress is None else progress)))
    update_job(
        job_id, status="running", progress=pct, current_stage=stage,
        current_operation=operation, current_detail=detail, message=operation,
        resume_stage="",
    )
    update_job_item(item_id, status="running", stage=stage, message=detail or operation)
    add_recovery_job_log(job_id, operation + (f" - {detail}" if detail else ""), stage=stage)


def log(job_id: int, stage: str, message: str, *, level: str = "info", **context: Any) -> None:
    add_recovery_job_log(job_id, message, stage=stage, level=level, context=context)


def pause(job_id: int, item_id: int, stage: str, message: str, result: dict[str, Any]) -> None:
    resume_stage = {"naming": "ready_to_import", "tv_boundary": "tv_splitting"}.get(stage, stage)
    update_job(
        job_id, status=f"paused_{stage}_review", current_stage=f"paused_{stage}_review",
        current_operation="Review required", current_detail=message, message=message,
        reviewed=1, resume_stage=resume_stage,
    )
    update_job_item(item_id, status="review", stage=f"paused_{stage}_review", message=message, result=result)
    log(job_id, stage, message, level="warning")


def fail(job_id: int, item_id: int, stage: str, message: str, result: dict[str, Any] | None = None) -> None:
    update_job(
        job_id, status="failed", failed=1, current_stage=stage,
        current_operation="Stage failed", current_detail=message, message=message,
        resume_stage=stage, finished_at=utcnow(),
    )
    update_job_item(item_id, status="error", stage=stage, message=message, result=result or {})
    log(job_id, stage, message, level="error")


def split_key(source_path: str, source_signature: str) -> str:
    raw = json.dumps([str(source_path), str(source_signature)], separators=(",", ":"))
    return "recovery_pipeline:split:v107:" + hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def record_split(source_path: str, source_signature: str, result: dict[str, Any]) -> None:
    cache_set(split_key(source_path, source_signature), {
        "completed": True,
        "source_path": source_path,
        "source_signature": source_signature,
        "generated_paths": [str(x.get("path") or "") for x in result.get("outputs") or []],
        "superseded_source": str(result.get("superseded_source") or ""),
        "completed_at": utcnow(),
    })


def split_state(source_path: str, source_signature: str) -> dict[str, Any] | None:
    row = cache_get(split_key(source_path, source_signature))
    return row if isinstance(row, dict) and row.get("completed") else None


def active_video_paths(source_path: str, video_files) -> list[str]:
    """Always rebuild import inventory from disk; scanner excludes originals."""
    return sorted(str(path) for path in video_files(Path(source_path)))
