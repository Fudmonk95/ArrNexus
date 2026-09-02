#!/usr/bin/env python3
from __future__ import annotations

"""ArrNexus v10.5.0-beta deterministic release validator."""

import asyncio
import compileall
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time


def require(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def main() -> int:
    root = Path(__file__).resolve().parent
    require(compileall.compile_dir(root / "app", quiet=1), "Python compilation failed")
    require(compileall.compile_file(str(root / "validate_v105.py"), quiet=1), "Compilation failed: validate_v105.py")
    node = shutil.which("node")
    if node:
        proc = subprocess.run([node, "--check", str(root / "app" / "static" / "app.js")], capture_output=True, text=True, timeout=60)
        require(proc.returncode == 0, f"JavaScript syntax failed: {proc.stderr}")

    td = Path(tempfile.mkdtemp(prefix="arrnexus-v105-validate-"))
    db_path = td / "router.db"
    os.environ["DB_PATH"] = str(db_path)
    os.environ["DB_DIR"] = str(td)
    os.environ["SESSION_SECRET"] = "v105-validation-session-secret"
    os.environ["ARRNEXUS_SELF_UPDATE"] = "0"
    for key in ("RADARR_API_KEY", "SONARR_API_KEY", "LIDARR_API_KEY", "PROWLARR_API_KEY", "JELLYFIN_API_KEY", "SEERR_API_KEY"):
        os.environ[key] = ""

    # Exercise migration from pre-v10.5 jobs tables rather than only a fresh DB.
    conn = sqlite3.connect(db_path)
    conn.executescript("""
      CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', total INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0, rejected INTEGER NOT NULL DEFAULT 0, reviewed INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
      CREATE TABLE job_items (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL, source_path TEXT NOT NULL, display_name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', stage TEXT NOT NULL DEFAULT 'queued', destination_key TEXT NOT NULL DEFAULT '', message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    """)
    conn.commit(); conn.close()

    from app import db
    db.init_db()
    conn = sqlite3.connect(db_path)
    job_cols = {x[1] for x in conn.execute("PRAGMA table_info(jobs)")}
    item_cols = {x[1] for x in conn.execute("PRAGMA table_info(job_items)")}
    conn.close()
    require("cancel_requested" in job_cols, "jobs migration missing cancel_requested")
    require("result_json" in item_cols, "job_items migration missing result_json")

    # Persistent job control: running -> cancelling -> cancelled and history deletion only.
    jid = db.create_job("import", [{"source_path": "/mnt/debrid/test", "display_name": "test", "destination_key": "auto"}])
    db.update_job(jid, status="running")
    require(db.request_job_cancel(jid), "running job did not accept cancellation")
    require(db.get_job(jid)[0]["status"] == "cancelling" and db.job_cancel_requested(jid), "running job did not enter cancelling state")
    db.mark_remaining_job_items_cancelled(jid)
    db.update_job(jid, status="cancelled")
    require(db.remove_job(jid), "finished cancelled job could not be removed")
    j2 = db.create_job("test", [{"source_path": "/x", "display_name": "x"}]); db.update_job(j2, status="complete")
    require(db.clear_finished_jobs() >= 1, "clear finished jobs did not remove history")

    # Child-process cooperative cancellation terminates a long process promptly.
    from app.process_control import run_cancellable, CancelledOperation
    started = time.monotonic()
    try:
        run_cancellable([sys.executable, "-c", "import time; time.sleep(30)"], capture_output=True, text=True, timeout=35, cancel_check=lambda: time.monotonic() - started > 0.25, poll_seconds=0.05)
        raise AssertionError("cancellable subprocess unexpectedly completed")
    except CancelledOperation:
        require(time.monotonic() - started < 5, "cancellable subprocess did not terminate promptly")

    # Language master OFF must return before ffprobe, and ON still evaluates normal English metadata.
    from app import language_guard
    original_ffprobe = language_guard._ffprobe
    original_video_files = language_guard.video_files
    language_guard.set_language_checks_enabled(False)
    language_guard._ffprobe = lambda *a, **k: (_ for _ in ()).throw(AssertionError("ffprobe ran while Language Checks OFF"))
    disabled = language_guard.inspect_source_languages("/mnt/debrid/legacy", "legacy-fp", force=True)
    require(disabled["status"] == "disabled" and disabled["compliant"], "Language Checks OFF did not bypass")
    language_guard.set_language_checks_enabled(True)
    language_guard.video_files = lambda _p: [Path("/mnt/debrid/legacy/S01E01.mp4")]
    language_guard._ffprobe = lambda *a, **k: {"streams": [{"codec_type": "audio", "tags": {"language": "eng"}, "disposition": {"default": 1}}]}
    enabled = language_guard.inspect_source_languages("/mnt/debrid/legacy", "legacy-fp-on", force=True)
    require(enabled["status"] == "pass" and enabled["compliant"], "Language Checks ON English pass regressed")
    language_guard._ffprobe = original_ffprobe; language_guard.video_files = original_video_files

    # Scanner must continue excluding recovery originals from import/inbox inventory.
    from app import scanner
    scan_root = td / "scan"
    (scan_root / ".arrnexus-originals").mkdir(parents=True)
    (scan_root / "Show.S03E01.mp4").write_bytes(b"ok")
    (scan_root / ".arrnexus-originals" / "Season3.mp4").write_bytes(b"old")
    old_view, old_logical = scanner.view_path, scanner.logical_from_view
    scanner.view_path = lambda p: Path(p)
    scanner.logical_from_view = lambda p: Path(p)
    files = scanner.video_files(scan_root)
    scanner.view_path, scanner.logical_from_view = old_view, old_logical
    require(len(files) == 1 and files[0].name == "Show.S03E01.mp4", ".arrnexus-originals leaked into scanning")

    # Recovered-media symlink targets must index back to the recovery source pack.
    from app import library
    if os.name != "nt":
        lib_actual = td / "library"; lib_actual.mkdir()
        os.symlink("/mnt/debrid/arrnexus-extracted/The Queen's Nose/S03E01.mp4", lib_actual / "S03E01.mp4")
        old_roots, old_view, old_logical, old_managed = library.all_library_roots, library.view_path, library.logical_from_view, library._managed_source_roots
        library.all_library_roots = lambda: {"tv:test": "/logical/library"}
        library.view_path = lambda p: lib_actual if str(p) == "/logical/library" else Path(p)
        library.logical_from_view = lambda p: Path("/logical/library") / Path(p).name
        library._managed_source_roots = lambda: ["/mnt/debrid/decypharr/__all__", "/mnt/debrid/arrnexus-extracted"]
        library.invalidate_library_cache()
        idx = library.build_source_link_index(force=True)
        library.all_library_roots, library.view_path, library.logical_from_view, library._managed_source_roots = old_roots, old_view, old_logical, old_managed
        library.invalidate_library_cache()
        require("/mnt/debrid/arrnexus-extracted/The Queen's Nose" in idx, "recovered symlink target not recognised as imported source")

    # Season-aware Queen's Nose style plan: import safe S1/S3/S6/S7 while S2/S4/S5 require recovery.
    from app.scanner import ScanItem
    from app import tv_source_selection, tv_recovery
    recovered_s1 = "/mnt/debrid/arrnexus-extracted/Queens Nose S1 recovered"
    recovered_big = "/mnt/debrid/arrnexus-extracted/Queens Nose big recovered"
    provider_bad = "/mnt/debrid/decypharr/__all__/Queens Nose S1 combined"
    old_inspect = tv_source_selection.inspect_item
    old_analyse = tv_recovery.analyse_source
    def fake_item(path: str):
        return ScanItem(Path(path).name, path, "tv", "The Queen's Nose", 1995, 6, [], 1000, 720, "fp-" + Path(path).name)
    tv_source_selection.inspect_item = fake_item
    def safe_rows(season, count=6):
        return [{"path": f"/src/S{season:02d}E{e:02d}.mp4", "name": f"S{season:02d}E{e:02d}.mp4", "season": season, "episode_start": e, "episode_end": e, "expected_episodes": count, "needs_split": False} for e in range(1, count + 1)]
    async def fake_analyse(path: str, cancel_check=None):
        if path == recovered_s1:
            return {"files": safe_rows(1)}
        if path == recovered_big:
            rows = safe_rows(3) + safe_rows(6) + safe_rows(7)
            rows += [{"path": f"/src/Season{s}.mp4", "name": f"Season{s}.mp4", "season": s, "episode_start": 1, "episode_end": 6, "expected_episodes": 6, "needs_split": True} for s in (2, 4, 5)]
            return {"files": rows}
        return {"files": [{"path": "/src/Season1-combined.mp4", "name": "Season1-combined.mp4", "season": 1, "episode_start": 1, "episode_end": 6, "expected_episodes": 6, "needs_split": True}]}
    tv_recovery.analyse_source = fake_analyse
    plan = asyncio.run(tv_source_selection.build_import_plan([provider_bad, recovered_s1, recovered_big]))
    tv_source_selection.inspect_item = old_inspect; tv_recovery.analyse_source = old_analyse
    ready = set(plan["ready_seasons"]); recovery = set(plan["recovery_seasons"])
    require({1, 3, 6, 7}.issubset(ready), f"safe seasons missing from grouped plan: {ready}")
    require({2, 4, 5}.issubset(recovery), f"combined seasons not held for TV Recovery: {recovery}")
    require(provider_bad not in plan["selected_by_source"], "inferior combined Season 1 hijacked grouped import")

    # Local CRC staging: a provider failure that passes on local bytes is upgraded, never ignored.
    from app import archive_media
    provider_dir = td / "provider"; provider_dir.mkdir()
    provider_rar = provider_dir / "show.rar"; provider_rar.write_bytes(b"rar-bytes-for-validation")
    recovery_dir = td / "recovery"; recovery_dir.mkdir()
    logical_rar = "/mnt/debrid/decypharr/__all__/show.rar"
    old_view = archive_media.view_path; old_inspect_archive = archive_media.inspect_archive
    old_verify = archive_media._verify_media_members_independently; old_storage = archive_media.storage_state; old_extractor = archive_media._extractor
    def arc_view(path):
        text = str(path)
        if text == logical_rar: return provider_rar
        prefix = "/mnt/debrid/arrnexus-extracted"
        if text.startswith(prefix): return recovery_dir / text[len(prefix):].lstrip("/")
        return Path(text)
    archive_media.view_path = arc_view
    archive_media.inspect_archive = lambda path, force=True, cancel_check=None: {"logical_path": logical_rar, "fingerprint": "crc-fp", "catalogue_signature": "sig", "media": [{"path": "S01E03.mp4", "size": 123}], "safe": True, "password_protected": False}
    archive_media.storage_state = lambda required=0: {"free": 10**9, "total": 2*10**9, "required": required, "enough": True, "root": "/mnt/debrid/arrnexus-extracted"}
    archive_media._extractor = lambda: ("7z", "fake-7z")
    archive_media._verify_media_members_independently = lambda *a, **k: {"members": [{"path": "S01E03.mp4", "size": 123, "status": "verified", "error": ""}], "verified_count": 1, "failed_count": 0, "untested_count": 0, "issues": [], "exit_code": 0}
    db.cache_set(archive_media._verification_key("crc-fp"), {"fingerprint": "crc-fp", "catalogue_signature": "sig", "members": [{"path": "S01E03.mp4", "size": 123, "status": "failed", "error": "CRC Failed"}]})
    staged = archive_media.stage_and_reverify(logical_rar, expected_fingerprint="crc-fp")
    archive_media.view_path, archive_media.inspect_archive = old_view, old_inspect_archive
    archive_media._verify_media_members_independently, archive_media.storage_state, archive_media._extractor = old_verify, old_storage, old_extractor
    require(staged["classification"] == "virtual_source_read_path", "local CRC pass was not classified as provider/virtual read-path issue")
    require(staged["recovered_locally"] == ["S01E03.mp4"], "local CRC pass did not upgrade only the failed member")
    require(provider_rar.exists(), "provider archive was modified/deleted by local staging")

    # Template compilation and real application routes against the migrated temporary DB.
    import app.main as main_app
    from fastapi.testclient import TestClient
    async def _empty_inbox_snapshot():
        return {"rows": [], "raw_rows": [], "built_at": time.time()}
    main_app._build_inbox_snapshot = _empty_inbox_snapshot
    main_app._INBOX_SNAPSHOT.clear()
    for template in sorted((root / "app" / "templates").glob("*.html")):
        main_app.templates.env.get_template(template.name)
    with TestClient(main_app.app) as client:
        health = client.get("/api/health")
        require(health.status_code == 200 and health.json().get("version") in {"10.5.0-beta", "10.5.1-beta", "10.6.0-beta", "10.6.1-beta", "10.6.2-beta", "10.6.3-beta", "10.7.0-beta", "10.8.0-beta", "10.8.1-beta"}, "v10.5 health/version")
        require(client.get("/").status_code == 200, "landing route failed")
        setup = client.post("/setup", data={"username": "v105validator", "email": "v105@example.invalid", "display_name": "V10.5 Validator", "password": "validation-password-123", "confirm": "validation-password-123"}, follow_redirects=False)
        require(setup.status_code == 303, "administrator setup failed")
        require(client.get("/settings").status_code == 200, "Settings template/route failed")
        require(client.get("/jobs").status_code == 200, "Import Jobs template/route failed")
        require(client.get("/inbox").status_code == 200, "DMM Inbox template/route failed")
        off = client.post("/settings/language-checks", data={"enabled": "false", "return_to": "/inbox"}, follow_redirects=False)
        require(off.status_code == 303 and not language_guard.language_checks_enabled(), "Language Checks OFF UI route failed")
        on = client.post("/settings/language-checks", data={"enabled": "true", "return_to": "/settings"}, follow_redirects=False)
        require(on.status_code == 303 and language_guard.language_checks_enabled(), "Language Checks ON UI route failed")

    from app.updater import version_key
    require(version_key("10.5.0-beta") > version_key("10.4.4-beta"), "Updater will not recognize v10.5")

    main_source = (root / "app/main.py").read_text(encoding="utf-8")
    router_source = (root / "app/router_service.py").read_text(encoding="utf-8")
    archive_source = (root / "app/archive_media.py").read_text(encoding="utf-8")
    library_source = (root / "app/library.py").read_text(encoding="utf-8")
    inbox = (root / "app/templates/inbox.html").read_text(encoding="utf-8")
    jobs = (root / "app/templates/jobs.html").read_text(encoding="utf-8")
    archive_tpl = (root / "app/templates/archive_media.html").read_text(encoding="utf-8")
    sw = (root / "app/static/sw.js").read_text(encoding="utf-8")
    require(any(v in main_source for v in ('APP_VERSION = \"10.5.0-beta\"', 'APP_VERSION = \"10.5.1-beta\"', 'APP_VERSION = \"10.6.0-beta\"', 'APP_VERSION = \"10.6.1-beta\"', 'APP_VERSION = \"10.6.2-beta\"', 'APP_VERSION = \"10.6.3-beta\"')), "v10.5 application marker missing")
    require("Language Checks OFF — imports will bypass Language Guard" in router_source, "import-time language bypass marker missing")
    require("import_grouped_tv_sources" in router_source and "tv_source_selection" in main_source, "season-aware grouped import missing")
    require("archive_recovery:local_stage:v105" in archive_source and "stage_and_reverify" in archive_source, "local CRC staging core missing")
    require("arrnexus-extracted" in library_source and "_managed_source_roots" in library_source, "recovered source-link indexing missing")
    require("Turn Language Checks" in inbox, "Inbox Language Checks master toggle missing")
    require("Cancel job" in jobs and "Clear finished jobs" in jobs, "job management UI missing")
    require(("Retry from local staging" in archive_tpl) or ("Verify original directly" in archive_tpl), "CRC local/direct staging UI missing")
    require(("arrnexus-static-v10.5.0" in sw) or ("arrnexus-static-v10.5.1" in sw) or ("arrnexus-static-v10.6.0" in sw) or ("arrnexus-static-v10.6.1" in sw) or ("arrnexus-static-v10.6.2" in sw) or ("arrnexus-static-v10.6.3" in sw), "v10.5 service-worker cache marker missing")
    require((root / "docs/RELEASE_NOTES_v10.5.0.md").exists(), "v10.5 release notes missing")
    require("Version 10.5.0" in (root / "README.md").read_text(encoding="utf-8"), "README missing v10.5")
    require("10.5.0-beta — Recovery Control & Reliable Imports" in (root / "CHANGELOG.md").read_text(encoding="utf-8"), "CHANGELOG missing v10.5")

    print("PASS: ArrNexus v10.5.0-beta adds user-controlled Language Checks, cancellable recovery jobs, season-aware partial TV imports, recovered-link state detection, and explicit local CRC staging without weakening provider/media safety")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


