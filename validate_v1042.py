#!/usr/bin/env python3
from __future__ import annotations

"""ArrNexus v10.4.2-beta release validator.

Focused on stable archive identity for Decypharr/DUMB virtual mounts while
retaining v10.4.1's media-only, member-level partial RAR recovery boundary.
"""

import compileall
import os
from pathlib import Path
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
    require(compileall.compile_file(str(root / "validate_v1042.py"), quiet=1), "Compilation failed: validate_v1042.py")
    node = __import__("shutil").which("node")
    if node:
        proc = subprocess.run([node, "--check", str(root / "app" / "static" / "app.js")], text=True, capture_output=True, timeout=60)
        require(proc.returncode == 0, f"JavaScript syntax failed: {proc.stderr}")

    validation_tmp = tempfile.mkdtemp(prefix="arrnexus-v1042-validate-")
    os.environ["DB_PATH"] = str(Path(validation_tmp) / "router.db")
    os.environ["DB_DIR"] = validation_tmp
    os.environ["SESSION_SECRET"] = "v1042-validation-session-secret"
    os.environ["ARRNEXUS_SELF_UPDATE"] = "0"
    for key in ("RADARR_API_KEY", "SONARR_API_KEY", "LIDARR_API_KEY", "PROWLARR_API_KEY", "JELLYFIN_API_KEY", "SEERR_API_KEY"):
        os.environ[key] = ""

    from app.archive_media import (
        _archive_fingerprint,
        _catalogue_signature,
        _parse_7z_test_output,
        _is_padding_member,
    )

    # Decypharr can refresh virtual mtimes while the provider source is
    # unchanged. A stable fingerprint must survive that refresh.
    with tempfile.TemporaryDirectory(prefix="arrnexus-v1042-fp-") as td:
        actual = Path(td) / "source.rar"
        actual.write_bytes(b"A" * 4096)
        logical = "/mnt/debrid/decypharr/__all__/show/source.rar"
        fp1 = _archive_fingerprint(logical, actual)
        now = time.time() + 120
        os.utime(actual, (now, now))
        fp2 = _archive_fingerprint(logical, actual)
        require(fp1 == fp2, "stable archive fingerprint changed on mtime-only refresh")
        actual.write_bytes(b"A" * 4097)
        fp3 = _archive_fingerprint(logical, actual)
        require(fp3 != fp2, "archive fingerprint did not change when provider-visible size changed")

    catalogue = [
        {"path": ".____padding_file/13", "size": 100, "packed_size": 100, "crc": "AAAA", "encrypted": False},
        {"path": "Season 4.mp4", "size": 518705428, "packed_size": 518705428, "crc": "C1F7DDAA", "encrypted": False},
        {"path": "Season 1.mp4", "size": 464554440, "packed_size": 464554440, "crc": "1D2B3634", "encrypted": False},
    ]
    sig1 = _catalogue_signature("/mnt/debrid/decypharr/__all__/season-4_202405/season-4_202405.rar", catalogue)
    sig2 = _catalogue_signature("/mnt/debrid/decypharr/__all__/season-4_202405/season-4_202405.rar", list(reversed(catalogue)))
    require(sig1 == sig2, "catalogue signature depends on listing order")
    changed = [dict(x) for x in catalogue]
    changed[2]["crc"] = "DEADBEEF"
    sig3 = _catalogue_signature("/mnt/debrid/decypharr/__all__/season-4_202405/season-4_202405.rar", changed)
    require(sig3 != sig1, "same-size archive member CRC change was not detected")
    padding_changed = [dict(x) for x in catalogue]
    padding_changed[0]["crc"] = "BBBB"
    sig4 = _catalogue_signature("/mnt/debrid/decypharr/__all__/season-4_202405/season-4_202405.rar", padding_changed)
    require(sig4 == sig1 and _is_padding_member(padding_changed[0]["path"]), "ignored torrent padding incorrectly changes recovery catalogue identity")

    # Retain the exact v10.4.1 Queen's Nose recovery boundary: 16 good media,
    # one CRC failure, archive-level exit 2.
    media = [{"path": f"Season 6 episode {i}.mp4", "size": 1000 + i} for i in range(1, 7)]
    media += [{"path": f"Season 7 episode {i}.mp4", "size": 2000 + i} for i in range(1, 7)]
    media += [{"path": f"Season {i}.mp4", "size": 3000 + i} for i in range(1, 6)]
    stdout = "\n".join("T " + row["path"] for row in media) + "\nWarnings: 1\nErrors: 1\n"
    stderr = "ERRORS:\nUnexpected end of archive\n\nERROR: CRC Failed : Season 1.mp4\n"
    partial = _parse_7z_test_output(stdout, stderr, media, 2)
    require(partial["verified_count"] == 16 and partial["failed_count"] == 1 and partial["untested_count"] == 0, "v10.4.1 partial-RAR recovery boundary regressed")

    # Language Guard algorithm changes must invalidate pre-v10.4.2 cached
    # decisions. Old false `fail` results must never survive the upgrade.
    from app.language_guard import _cache_key, LanguagePolicy, evaluate_probe_payload
    policy = LanguagePolicy()
    lang_key = _cache_key("/mnt/debrid/decypharr/__all__/Bernards Watch", "abc123", policy)
    require("language:v1042:" in lang_key, "Language Guard cache schema was not bumped for v10.4.2")
    unknown_probe = {"streams": [{"codec_type": "audio", "tags": {"language": "und"}, "disposition": {"default": 1}}]}
    unknown_result = evaluate_probe_payload(unknown_probe, policy)
    require(unknown_result["status"] == "unknown" and not unknown_result["destructive_safe"], "undefined English-candidate metadata was promoted to rejection")

    # Reusing an already-owned Arr item by external ID is idempotent. This is
    # the Blade Runner 2049 field regression: MovieExistsValidator is not a
    # failed import when the TMDb item is already present.
    import asyncio
    from app.router_service import _existing_target_external
    class FakeRadarr:
        async def movies(self):
            return [{"id": 77, "title": "Blade Runner 2049", "tmdbId": 335984, "path": "/movies/Blade Runner 2049 (2017)"}]
    existing_movie = asyncio.run(_existing_target_external(FakeRadarr(), "radarr", {"tmdbId": 335984}))
    require(existing_movie and existing_movie.get("id") == 77, "existing Radarr movie was not resolved by TMDb ID")

    from app.db import init_db
    from app.updater import version_key
    import app.main as main_app
    from fastapi.testclient import TestClient

    init_db()
    from app.db import create_job, get_job
    jid = create_job("import", [{"source_path": "/tmp/example", "display_name": "example"}])
    job_row, _ = get_job(jid)
    require("reviewed" in job_row and int(job_row.get("reviewed") or 0) == 0, "jobs schema does not separate Manual Review")
    for template in sorted((root / "app" / "templates").glob("*.html")):
        main_app.templates.env.get_template(template.name)
    with TestClient(main_app.app) as client:
        health = client.get("/api/health")
        require(health.status_code == 200 and health.json().get("version") == "10.4.2-beta", "v10.4.2 health/version")
        setup = client.post("/setup", data={"username": "v1042validator", "email": "v1042@example.invalid", "display_name": "V10.4.2 Validator", "password": "validation-password-123", "confirm": "validation-password-123"}, follow_redirects=False)
        require(setup.status_code == 303, "v10.4.2 administrator setup")
        page = client.get("/maintenance/archives", follow_redirects=False)
        require(page.status_code == 200 and "torrent padding are never extracted" in page.text, "Archive Recovery UI missing")

    require(version_key("10.4.2-beta") > version_key("10.4.1-beta"), "Updater will not recognize v10.4.2")

    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    archive_source = (root / "app" / "archive_media.py").read_text(encoding="utf-8")
    router_source = (root / "app" / "router_service.py").read_text(encoding="utf-8")
    db_source = (root / "app" / "db.py").read_text(encoding="utf-8")
    language_source = (root / "app" / "language_guard.py").read_text(encoding="utf-8")
    job_template = (root / "app" / "templates" / "job.html").read_text(encoding="utf-8")
    sw = (root / "app" / "static" / "sw.js").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")

    require('APP_VERSION = "10.4.2-beta"' in main_source, "v10.4.2 application marker missing")
    require("language:v1042:" in language_source, "Language Guard cache invalidation marker missing")
    require("reviewed INTEGER" in db_source and "manual review" in job_template.lower(), "Manual Review job counter markers missing")
    require("_existing_target_external" in router_source and "MovieExistsValidator" in router_source, "idempotent existing-Arr import marker missing")
    for marker in ("_catalogue_signature", "virtual /proc view", "catalogue changed during verification", "catalogue changed since verification"):
        require(marker.lower() in archive_source.lower(), f"v10.4.2 archive safety marker missing: {marker}")
    require("arrnexus-static-v10.4.2" in sw, "v10.4.2 service worker marker missing")
    require("Version 10.4.2" in readme and "Stable Archive Identity Hotfix" in readme, "README missing v10.4.2")
    require("10.4.2-beta — Stable Archive Identity Hotfix" in changelog, "CHANGELOG missing v10.4.2")
    require((root / "docs" / "RELEASE_NOTES_v10.4.2.md").exists(), "v10.4.2 release notes missing")

    print("PASS: ArrNexus v10.4.2-beta retains selective media-only partial RAR recovery, stabilizes Decypharr archive identity, invalidates stale Language Guard decisions, separates Manual Review from true rejection, and treats existing Arr external IDs as idempotent imports")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
