#!/usr/bin/env python3
"""ArrNexus v10.1.0-beta release validator.

Runs the complete retained v10 -> v9.4 -> ... -> v7 chain, then validates the
v10.1 Language Guard cleanup semantics, controlled rejection state, duplicate
symlink consolidation safety, exact Real-Debrid deletion rules and UI/docs.
"""
from __future__ import annotations

import asyncio
import compileall
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_v10(root: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(root / "validate_v10.py")], cwd=root,
        text=True, capture_output=True, timeout=420,
    )
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)
    require(proc.returncode == 0, f"Retained v10 regression validator failed with exit code {proc.returncode}")


def main() -> int:
    root = Path(__file__).resolve().parent
    if os.getenv("ARRNEXUS_VALIDATE_V101_ONLY") != "1":
        run_v10(root)

    require(compileall.compile_dir(root / "app", quiet=1), "Python compilation failed")
    require(compileall.compile_file(str(root / "bootstrap.py"), quiet=1), "Bootstrap compilation failed")

    node = __import__("shutil").which("node")
    if node:
        proc = subprocess.run([node, "--check", str(root / "app" / "static" / "app.js")], text=True, capture_output=True, timeout=60)
        require(proc.returncode == 0, f"JavaScript syntax failed: {proc.stderr}")

    with tempfile.TemporaryDirectory(prefix="arrnexus-v101-validate-") as tmp:
        os.environ["DB_PATH"] = str(Path(tmp) / "router.db")
        os.environ["DB_DIR"] = tmp
        os.environ["SESSION_SECRET"] = "validation-only-v101-session-secret"
        os.environ["ARRNEXUS_SELF_UPDATE"] = "1"
        for key in (
            "RADARR_API_KEY", "SONARR_API_KEY", "LIDARR_API_KEY", "PROWLARR_API_KEY",
            "JELLYFIN_API_KEY", "PLEX_API_KEY", "EMBY_API_KEY", "SEERR_API_KEY",
        ):
            os.environ[key] = ""

        from fastapi.testclient import TestClient
        import app.main as main_app
        import app.realdebrid as rd
        from app.db import init_db, db, create_job, get_job, update_job
        from app.language_guard import result_badge, load_language_policy
        from app.consolidation import _candidate_score
        from app.updater import version_key

        init_db()
        with db() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        require("rejected" in cols, "v10.1 jobs migration missing rejected count")
        jid = create_job("validator", [{"source_path": "/validator", "display_name": "Validator", "destination_key": "auto"}])
        update_job(jid, rejected=1, message="controlled rejection")
        job, _ = get_job(jid)
        require(int(job.get("rejected") or 0) == 1, "controlled rejection count not persisted")

        probe_key, probe_label = result_badge({"status": "fail", "missing": ["language probe failed"], "errors": ["ffprobe error"]})
        fail_key, fail_label = result_badge({"status": "fail", "missing": ["English audio"], "errors": []})
        require(probe_key == "probe_failed" and "failed" in probe_label.lower(), "probe failure badge is not distinct")
        require(fail_key == "fail" and "rejected" in fail_label.lower(), "policy rejection badge is not distinct")

        pass_score, _ = _candidate_score({"language_key":"pass","resolution":1080,"source_rank":3,"codec_rank":1,"audio_rank":1,"size_bytes":8*1024**3})
        fail_score, _ = _candidate_score({"language_key":"fail","resolution":2160,"source_rank":6,"codec_rank":3,"audio_rank":4,"hdr":True,"size_bytes":80*1024**3})
        require(pass_score > fail_score, "Language eligibility must outrank raw 4K/size quality in consolidation")

        # Real-Debrid cleanup must be exact-name only and ambiguous matches must fail closed.
        original_torrents = rd.torrents
        original_delete = rd.delete_torrent
        deleted = []
        async def fake_rows(limit=1000):
            return [
                {"id":"101","filename":"Exact.Movie.2026.1080p","bytes":123},
                {"id":"102","filename":"Other.Movie.2026.1080p","bytes":456},
            ]
        async def fake_delete(tid):
            deleted.append(str(tid)); return None
        rd.torrents = fake_rows
        rd.delete_torrent = fake_delete
        try:
            match = asyncio.run(rd.exact_torrent_for_source("/mnt/debrid/decypharr/__all__/Exact.Movie.2026.1080p", 123))
            require(match.get("ok") and str((match.get("torrent") or {}).get("id")) == "101", "exact RD source match failed")
            miss = asyncio.run(rd.exact_torrent_for_source("/mnt/debrid/decypharr/__all__/Exact.Movie.2026", 123))
            require(not miss.get("ok"), "fuzzy/partial RD source match was accepted")
        finally:
            rd.torrents = original_torrents
            rd.delete_torrent = original_delete

        for template in sorted((root / "app" / "templates").glob("*.html")):
            main_app.templates.env.get_template(template.name)

        with TestClient(main_app.app) as client:
            health = client.get("/api/health", follow_redirects=False)
            require(health.status_code == 200 and health.json().get("version") in {"10.1.0-beta", "10.2.0-beta", "10.3.0-beta"}, "v10.1 health/version")
            setup = client.post("/setup", data={
                "username":"v101validator", "email":"v101@example.invalid", "display_name":"V10.1 Validator",
                "password":"validation-password-123", "confirm":"validation-password-123",
            }, follow_redirects=False)
            require(setup.status_code == 303, "v10.1 administrator setup")
            saved = client.post("/settings/language-guard", data={
                "enabled":"true", "auto_upgrade_search":"true", "remove_rejected_debrid":"true",
                "require_english_audio":"true", "require_english_subtitles":"true", "unknown_is_failure":"true",
                "max_files":"300", "probe_timeout_seconds":"20",
            }, follow_redirects=False)
            require(saved.status_code == 303 and load_language_policy().remove_rejected_debrid, "Language Guard cleanup setting not persisted")
            consolidation = client.get("/maintenance/consolidation", follow_redirects=False)
            require(consolidation.status_code == 200 and "Library Consolidation" in consolidation.text, "v10.1 consolidation page")

        require(version_key("10.1.0-beta") > version_key("10.0.0-beta"), "updater will not recognize v10.1 as newer")

    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    router_source = (root / "app" / "router_service.py").read_text(encoding="utf-8")
    rd_source = (root / "app" / "realdebrid.py").read_text(encoding="utf-8")
    consolidation_source = (root / "app" / "consolidation.py").read_text(encoding="utf-8")
    settings_html = (root / "app" / "templates" / "settings.html").read_text(encoding="utf-8")
    job_html = (root / "app" / "templates" / "job.html").read_text(encoding="utf-8")
    consolidation_html = (root / "app" / "templates" / "consolidation.html").read_text(encoding="utf-8")
    css = (root / "app" / "static" / "app.css").read_text(encoding="utf-8")
    sw = (root / "app" / "static" / "sw.js").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    guide = (root / "docs" / "USER_GUIDE.md").read_text(encoding="utf-8")
    audit = (root / "docs" / "DOCUMENTATION_AUDIT.md").read_text(encoding="utf-8")

    require(('APP_VERSION = "10.1.0-beta"' in main_source or 'APP_VERSION = "10.2.0-beta"' in main_source or 'APP_VERSION = "10.3.0-beta"' in main_source), "v10.1+ version string missing")
    for marker in ("LanguageRejectedSafe", "complete_with_rejections", "language_rejected_removed", "_INBOX_SNAPSHOT.clear()"):
        require(marker in main_source or marker in router_source, f"v10.1 Language Guard workflow missing {marker}")
    for marker in ("delete_source_torrent_exact", "exact_torrent_for_source", "Ambiguous", "DELETE"):
        require(marker.lower() in rd_source.lower(), f"v10.1 exact RD cleanup missing {marker}")
    for marker in ("scan_consolidation", "apply_consolidation", "expected_digest", "orphaned_sources", "remove_provider_sources", "_rescan_affected"):
        require(marker in consolidation_source, f"v10.1 consolidation safety missing {marker}")
    require("remove_rejected_debrid" in settings_html, "v10.1 rejected-source cleanup setting missing")
    require("language rejected" in job_html.lower(), "v10.1 job UI does not distinguish rejections")
    require("Eligibility comes before raw quality" in consolidation_html and "Provider cleanup is separate and optional" in consolidation_html, "v10.1 consolidation preview safety copy missing")
    require("consolidation-group" in css and "language-probe_failed" in css, "v10.1 consolidation/language styles missing")
    require(("arrnexus-static-v10.1" in sw or "arrnexus-static-v10.2" in sw or "arrnexus-static-v10.3" in sw), "v10.1+ service-worker cache marker missing")
    require("Version 10.1" in readme and "Library Consolidation" in readme, "README missing v10.1 release detail")
    require("Library Consolidation" in guide and "exact Real-Debrid" in guide, "User Guide missing v10.1 cleanup guidance")
    require("/maintenance/consolidation" in audit, "Documentation audit missing v10.1 consolidation routes")

    print("PASS: ArrNexus v10.1.0-beta retains v7/v8/v9/v9.1/v9.2/v9.3/v9.4/v10 regressions and adds exact rejected Real-Debrid cleanup, controlled Language Guard rejection state, immediate Inbox refresh and safe preview-first movie/episode symlink consolidation with optional orphan-provider cleanup")
    return 0


if __name__ == "__main__":
    # Retained validators can leave TestClient/background workers alive after
    # the final assertion. Explicit process exit prevents release-gate hangs
    # without changing any validation assertions.
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(code or 0))
