#!/usr/bin/env python3
from __future__ import annotations

"""ArrNexus v10.4.4-beta release validator.

Covers background RAR inspection, recovered-source Inbox aggregation,
TMDb/Sonarr TV runtime fallback, and joined-episode preservation/detection.
"""

import compileall
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def require(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def main() -> int:
    root = Path(__file__).resolve().parent
    require(compileall.compile_dir(root / "app", quiet=1), "Python compilation failed")
    require(compileall.compile_file(str(root / "validate_v1044.py"), quiet=1), "Compilation failed: validate_v1044.py")
    node = __import__("shutil").which("node")
    if node:
        proc = subprocess.run([node, "--check", str(root / "app" / "static" / "app.js")], capture_output=True, text=True, timeout=60)
        require(proc.returncode == 0, f"JavaScript syntax failed: {proc.stderr}")

    td = tempfile.mkdtemp(prefix="arrnexus-v1044-validate-")
    os.environ["DB_PATH"] = str(Path(td) / "router.db")
    os.environ["DB_DIR"] = td
    os.environ["SESSION_SECRET"] = "v1044-validation-session-secret"
    os.environ["ARRNEXUS_SELF_UPDATE"] = "0"
    for key in ("RADARR_API_KEY", "SONARR_API_KEY", "LIDARR_API_KEY", "PROWLARR_API_KEY", "JELLYFIN_API_KEY", "SEERR_API_KEY"):
        os.environ[key] = ""

    from app.db import init_db
    init_db()

    from app.scanner import episode_span
    require(episode_span("Show.S03E06-7.mp4") == (3, 6, 7), "S03E06-7 range not preserved")
    require(episode_span("Show S03E06-E07.mkv") == (3, 6, 7), "S03E06-E07 range not preserved")
    require(episode_span("Show.S03E06E07.mkv") == (3, 6, 7), "S03E06E07 range not preserved")
    require(episode_span("Show.3x06-07.mkv") == (3, 6, 7), "3x06-07 range not preserved")
    require(episode_span("Show.S03E06.mkv") == (3, 6, 6), "single episode parser regressed")

    from app import tv_recovery
    joined = tv_recovery._file_analysis(
        logical=Path("/mnt/debrid/arrnexus-extracted/Show/Show - S03E06.mp4"),
        probe={"duration": 54 * 60, "chapters": []},
        season=3, span=(3, 6, 6), expected_season=13, typical_minutes=27.0,
    )
    require(joined["needs_split"] and joined["detection"] == "runtime_multi_episode", "2x-runtime single filename was not flagged as joined")
    require(joined["episode_start"] == 6 and joined["episode_end"] == 7 and joined["detected_episode_count"] == 2, "runtime join did not map E06-E07")

    explicit = tv_recovery._file_analysis(
        logical=Path("/mnt/debrid/arrnexus-extracted/Show/Show - S03E06-E07.mp4"),
        probe={"duration": 53.5 * 60, "chapters": []},
        season=3, span=(3, 6, 7), expected_season=13, typical_minutes=27.0,
    )
    require(explicit["needs_split"] and explicit["episode_start"] == 6 and explicit["episode_end"] == 7, "explicit joined episode range was not offered for split")
    require(explicit["boundaries"][0]["episode"] == 6 and explicit["boundaries"][1]["episode"] == 7, "split boundaries lost episode offset")

    season_pack = tv_recovery._file_analysis(
        logical=Path("/mnt/debrid/arrnexus-extracted/Show/Show - Season 04.mp4"),
        probe={"duration": 99.0 * 60, "chapters": []},
        season=4, span=None, expected_season=6, typical_minutes=16.5,
    )
    require(season_pack["needs_split"] and season_pack["detected_episode_count"] == 6, "combined season did not use metadata episode count")
    require(season_pack["mode"] == "runtime_estimate" and len(season_pack["boundaries"]) == 6, "combined season runtime plan missing")

    from app import media_identity
    named = media_identity.canonical_media_name({"media_type": "tv", "title": "The Story of Tracy Beaker"}, "S03E06-7.mp4")
    require("S03E06-E07" in named, "canonical naming collapsed joined episode range")

    import app.main as main_app
    from types import SimpleNamespace
    fake_rows = []
    for season, provenance in [(1, "Extracted RAR"), (2, "Extracted RAR"), (3, "Extracted RAR"), (4, "DMM / provider source"), (5, "DMM / provider source")]:
        fake_rows.append({
            "canonical_key": "tv:title:thestoryoftracybeaker",
            "item": SimpleNamespace(path=f"/pack{season}", name=f"Season {season}", media_type="tv", season_numbers=[season], video_count=10+season, quality=720, size_bytes=1000+season),
            "language_badge_key": "unchecked", "language_badge_label": "Language unchecked",
            "state": "waiting", "linked_paths": [], "existing_resolution": 0, "changed": False,
            "provenance": provenance, "existing": None, "instance": None, "display_title": "The Story of Tracy Beaker",
        })
    grouped = main_app.dedupe_rows(fake_rows)
    require(len(grouped) == 1 and grouped[0]["source_pack_count"] == 5, "five Tracy Beaker source packs did not collapse to one series card")
    require(grouped[0]["series_seasons"] == [1, 2, 3, 4, 5], "Tracy Beaker season coverage did not union to 1-5")
    require(sum(1 for x in grouped[0]["series_sources"] if x.get("provenance") == "Extracted RAR") == 3, "recovered pack provenance was lost during grouping")
    from app.updater import version_key
    from fastapi.testclient import TestClient

    for template in sorted((root / "app" / "templates").glob("*.html")):
        main_app.templates.env.get_template(template.name)
    with TestClient(main_app.app) as client:
        health = client.get("/api/health")
        require(health.status_code == 200 and health.json().get("version") in {"10.4.4-beta", "10.5.0-beta", "10.5.1-beta", "10.6.0-beta", "10.6.1-beta", "10.6.2-beta", "10.6.3-beta", "10.7.0-beta", "10.8.0-beta", "10.8.1-beta"}, "v10.4.4 health/version")
        setup = client.post("/setup", data={"username": "v1044validator", "email": "v1044@example.invalid", "display_name": "V10.4.4 Validator", "password": "validation-password-123", "confirm": "validation-password-123"}, follow_redirects=False)
        require(setup.status_code == 303, "v10.4.4 administrator setup")
        landing = client.get("/", follow_redirects=False)
        require(landing.status_code == 200, "v10.4.4 landing page")

    require(version_key("10.4.4-beta") > version_key("10.4.3-beta"), "Updater will not recognize v10.4.4")

    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    archive_source = (root / "app" / "archive_media.py").read_text(encoding="utf-8")
    scanner_source = (root / "app" / "scanner.py").read_text(encoding="utf-8")
    tv_source = (root / "app" / "tv_recovery.py").read_text(encoding="utf-8")
    identity_source = (root / "app" / "media_identity.py").read_text(encoding="utf-8")
    router_source = (root / "app" / "router_service.py").read_text(encoding="utf-8")
    inbox_template = (root / "app" / "templates" / "inbox.html").read_text(encoding="utf-8")
    archive_template = (root / "app" / "templates" / "archive_media.html").read_text(encoding="utf-8")
    sw = (root / "app" / "static" / "sw.js").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")

    require(any(v in main_source for v in ('APP_VERSION = \"10.4.4-beta\"', 'APP_VERSION = \"10.5.0-beta\"', 'APP_VERSION = \"10.5.1-beta\"', 'APP_VERSION = \"10.6.0-beta\"', 'APP_VERSION = \"10.6.1-beta\"', 'APP_VERSION = \"10.6.2-beta\"', 'APP_VERSION = \"10.6.3-beta\"')), "v10.4.4+ application marker missing")
    require("run_archive_inspect_job" in main_source and '@app.post("/maintenance/archives/inspect")' in main_source, "background archive inspection route missing")
    require("cached_inspection" in archive_source and "timeout=1800" in archive_source, "large-archive cached inspection markers missing")
    require("scan_media_root" in scanner_source and "archive_media.extraction_root()" in main_source, "recovered media is not merged into Inbox inventory")
    require('"provenance": r.get("provenance")' in main_source and "RAR recovered" in inbox_template, "source-pack provenance aggregation missing")
    require("MULTI_EPISODE_PATTERNS" in scanner_source and "episode_span" in scanner_source, "multi-episode filename parser missing")
    require("tmdb_tv_season" in identity_source and "typical_runtime_minutes" in identity_source, "TMDb runtime fallback missing")
    require("runtime_multi_episode" in tv_source and "episode_count_source" in tv_source, "runtime joined-episode detection missing")
    require("TV Recovery review required before Sonarr import" in router_source, "TV runtime recovery pre-import gate missing")
    require("/maintenance/archives/inspect" in archive_template and "Open inspection" in archive_template, "archive UI still uses synchronous inspect")
    require(((("arrnexus-static-v10.4.4" in sw) or ("arrnexus-static-v10.5.0" in sw)) or ("arrnexus-static-v10.5.0" in sw)) or ("arrnexus-static-v10.5.1" in sw) or ("arrnexus-static-v10.6.0" in sw) or ("arrnexus-static-v10.6.1" in sw) or ("arrnexus-static-v10.6.2" in sw) or ("arrnexus-static-v10.6.3" in sw), "v10.4.4+ service worker marker missing")
    require("Version 10.4.4" in readme and "Unified Recovery & TV Intelligence" in readme, "README missing v10.4.4")
    require("10.4.4-beta — Unified Recovery & TV Intelligence" in changelog, "CHANGELOG missing v10.4.4")
    require((root / "docs" / "RELEASE_NOTES_v10.4.4.md").exists(), "v10.4.4 release notes missing")

    print("PASS: ArrNexus v10.4.4-beta backgrounds large RAR inspection, merges recovered packs into the Inbox, preserves multi-episode ranges, and uses Sonarr/TMDb runtime evidence before TV import")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


