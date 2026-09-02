#!/usr/bin/env python3
from __future__ import annotations

"""ArrNexus v10.8.0-beta acceptance validator."""

import asyncio
import os
import tempfile
from pathlib import Path


def require(value, message: str) -> None:
    if not value:
        raise AssertionError(message)


def main() -> int:
    root = Path(__file__).resolve().parent
    td = Path(tempfile.mkdtemp(prefix="arrnexus-v108-validate-"))
    os.environ["DB_PATH"] = str(td / "router.db")
    os.environ["DB_DIR"] = str(td)
    os.environ["SESSION_SECRET"] = "v108-validator"
    os.environ["ARRNEXUS_SELF_UPDATE"] = "0"

    from app import db, media_automation
    db.init_db()
    with db.db() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    require({"media_automations", "media_automation_targets", "media_automation_runs"} <= tables, "media automation migration missing")

    ident = media_automation.save_definition(
        automation_id=None, name="Queen's Nose Collection", media_type="mixed", source_type="manual", source_ref="",
        engine="auto", schedule_hours=24, enabled=False, acquire_missing=False,
        manual_items="movie:tmdb:550\ntv:tvdb:81189\ntt0137523",
        targets=[{"server_type": "plex", "library_name": "Movies", "collection_name": "Queen's Nose Collection", "engine": "kometa"}],
    )
    definition = media_automation.get_definition(ident)
    require(definition and len(definition["targets"]) == 1, "normalized definition/target persistence failed")
    resolved = asyncio.run(media_automation.resolve_source(definition))
    require(len(resolved) == 3 and resolved[0]["tmdb_id"] == 550 and resolved[1]["tvdb_id"] == 81189, "manual provider-ID normalization failed")
    preview = asyncio.run(media_automation.preview_definition(definition, resolved))
    require(preview["non_destructive"] and preview["targets"][0]["remove"] == 0, "preview is not non-destructive")
    imported = media_automation.import_kometa_yaml("collections:\n  Imported Films:\n    tmdb_movie:\n      - 603\n    imdb_id:\n      - tt0133093\n", library_name="Movies")
    require(len(imported) == 1 and "movie:tmdb:603" in media_automation.get_definition(imported[0])["definition"]["manual_items"], "safe Kometa YAML import failed")

    jid = db.create_job("archive_inspect", [{"source_path": "/test/archive.rar", "display_name": "Generic background job"}])
    job, items = db.get_job(jid)
    db.update_job(jid, status="running", current_stage="resolving", current_operation="Resolving source", current_detail="3 provider identities")
    db.update_job_item(items[0]["id"], status="running", stage="resolving", message="Provider identities ready")
    logs = db.recovery_job_logs(jid)
    require(any("provider identities" in str(row["message"]).lower() for row in logs), "generic live job terminal did not capture progress")
    require(not [x for x in db.list_logs(limit=1000) if "Provider identities ready" in str(x["message"])], "detailed terminal output leaked into Unified Logs")

    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    router_source = (root / "app" / "router_service.py").read_text(encoding="utf-8")
    module_source = (root / "app" / "media_automation.py").read_text(encoding="utf-8")
    job_ui = (root / "app" / "templates" / "job.html").read_text(encoding="utf-8")
    base_ui = (root / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    js = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")
    require('APP_VERSION = "10.8.1-beta"' in main_source, "v10.8 version marker missing")
    for token in ("Language Guard advisory-only", "automatic_scan", "clear_language_block_states"):
        require(token in router_source or token in main_source, f"advisory language contract missing: {token}")
    require("raise LanguageRejectedSafe" not in router_source, "automatic import can still raise a Language Guard block")
    for token in ("NativeCollectionAdapter", "_apply_kometa", "preview_definition", "sync_definition", "non_destructive", "smartlists"):
        require(token in module_source, f"media automation engine missing: {token}")
    for route in ("/media-automation", "/media-automation/collections", "/media-automation/presets", "/media-automation/servers"):
        require(route in main_source, f"route missing: {route}")
    require("Live Job Terminal" in job_ui and "recovery_job_logs(job_id" in main_source, "generic job terminal missing")
    require("app.css?v=10.8.1" in base_ui and "background:#030304" in base_ui, "no-flash theme bootstrap missing")
    require("jobItems" in js and "pollRecoveryPage" in js, "live job state polling missing")

    from app.main import app, templates
    for name in ("media_automation.html", "media_automation_edit.html", "media_automation_preview.html", "media_automation_presets.html", "media_automation_servers.html", "job.html"):
        templates.env.get_template(name)
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        health = client.get("/api/health")
        require(health.status_code == 200 and health.json().get("version") == "10.8.1-beta", "v10.8 health/version failed")
        require(client.get("/").status_code == 200, "landing smoke failed")
        require(client.get("/setup").status_code == 200, "setup smoke failed")
        setup = client.post("/setup", data={"username": "v108validator", "email": "v108@example.invalid", "display_name": "V10.8 Validator", "password": "validation-password-123", "confirm": "validation-password-123"}, follow_redirects=False)
        require(setup.status_code == 303, "administrator setup failed")
        for path in ("/media-automation", "/media-automation/collections/new", f"/media-automation/collections/{ident}", f"/media-automation/collections/{ident}/preview", "/media-automation/presets", "/media-automation/servers", f"/jobs/{jid}", f"/api/jobs/{jid}"):
            response = client.get(path)
            require(response.status_code == 200, f"authenticated route smoke failed: {path} -> {response.status_code}")

    print("ArrNexus v10.8.0-beta validation passed")
    print("language advisory, live job terminal, normalized automation, previews, adapters and UI: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
