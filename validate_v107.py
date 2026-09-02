#!/usr/bin/env python3
from __future__ import annotations

"""ArrNexus v10.7.0-beta one-click recovery/import acceptance validator."""

import os
import tempfile
from pathlib import Path


def require(value, message: str) -> None:
    if not value:
        raise AssertionError(message)


def main() -> int:
    root = Path(__file__).resolve().parent
    td = Path(tempfile.mkdtemp(prefix="arrnexus-v107-validate-"))
    os.environ["DB_PATH"] = str(td / "router.db")
    os.environ["DB_DIR"] = str(td)
    os.environ["SESSION_SECRET"] = "v107-validator"
    os.environ["ARRNEXUS_SELF_UPDATE"] = "0"

    from app import db, recovery_pipeline
    db.init_db()

    source = "/mnt/debrid/decypharr/__all__/season-4_202405/season-4_202405.rar"
    jid = db.create_job("recover_import", [{"source_path": source, "display_name": "The Queen's Nose", "destination_key": "auto"}])
    job, items = db.get_job(jid)
    require(job["kind"] == "recover_import" and len(items) == 1, "persistent recovery job was not created")
    recovery_pipeline.set_stage(jid, items[0]["id"], "provider_verification", "Provider verification", "Season 1.mp4")
    recovery_pipeline.log(jid, "provider_verification", "Provider CRC anomaly; direct source required")
    logs = db.recovery_job_logs(jid)
    require(len(logs) >= 2 and logs[-1]["stage"] == "provider_verification", "dedicated recovery log failed")
    require(not [x for x in db.list_logs(limit=1000) if "Provider CRC anomaly" in str(x["message"])], "detailed job output leaked into Unified Logs")

    split_result = {"outputs": [{"path": "/recovered/Season 02/S02E01.mkv"}], "superseded_source": "/recovered/.arrnexus-originals/Season 2.mkv"}
    recovery_pipeline.record_split("/recovered/Season 2.mkv", "sig-2", split_result)
    persisted = recovery_pipeline.split_state("/recovered/Season 2.mkv", "sig-2")
    require(persisted and persisted["generated_paths"] == ["/recovered/Season 02/S02E01.mkv"], "split completion did not persist")
    rows = recovery_pipeline.stage_rows("tv_splitting")
    require(any(x["key"] == "tv_splitting" and x["state"] == "active" for x in rows), "pipeline stage model failed")

    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    db_source = (root / "app" / "db.py").read_text(encoding="utf-8")
    language_source = (root / "app" / "language_guard.py").read_text(encoding="utf-8")
    tv_source = (root / "app" / "tv_recovery.py").read_text(encoding="utf-8")
    scanner_source = (root / "app" / "scanner.py").read_text(encoding="utf-8")
    archive_ui = (root / "app" / "templates" / "archive_media.html").read_text(encoding="utf-8")
    job_ui = (root / "app" / "templates" / "job.html").read_text(encoding="utf-8")
    js = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")

    for token in (
        'APP_VERSION = "10.8.1-beta"', "run_recover_import_job", "provider_verification", "direct_recovery",
        "archive_media.stage_and_reverify", "archive_media.extract_archive", "inspect_source_languages",
        "tv_recovery.analyse_source", "tv_recovery.split_plan", "recovery_pipeline.record_split",
        "invalidate_scan_cache", "import_one", "paused_tv_boundary_review", "skip_unsafe",
        'status="imported"', "job_failed", "job_cancelled",
    ):
        require(token in main_source, f"one-click pipeline contract missing: {token}")
    require("recovery_job_logs" in db_source and "current_operation" in db_source and "resume_stage" in db_source, "persistent progress/log schema missing")
    require("language_file:v107" in language_source and "st_mtime_ns" in language_source, "exact media fingerprint language cache missing")
    require(".arrnexus-originals" in tv_source and "_verify_file" in tv_source and "refreshed = inspect_item" in tv_source, "verified split persistence/reindex behavior missing")
    require(".arrnexus-originals" in scanner_source, "original combined files are not excluded")
    require("Recover & Import" in archive_ui and "Advanced / manual recovery controls" in archive_ui, "primary/advanced archive UI missing")
    for token in ("LIVE JOB", "Live Job Terminal", "Continue Job", "Retry Stage", "recoveryPercent"):
        require(token in job_ui, f"job control-centre UI missing: {token}")
    require("pollRecoveryPage" in js and "recovery_logs" in js, "live detailed progress polling missing")

    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        health = client.get("/api/health")
        require(health.status_code == 200 and health.json().get("version") == "10.8.1-beta", "v10.8 health/version failed")
        require(client.get("/").status_code == 200, "landing smoke failed")
        require(client.get("/setup").status_code == 200, "setup smoke failed")

    print("ArrNexus v10.7.0-beta validation passed")
    print("one-click pipeline, persistent stages/logs, fingerprint cache, batch split and UI contracts: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

