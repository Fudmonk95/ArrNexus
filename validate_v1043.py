#!/usr/bin/env python3
from __future__ import annotations

"""ArrNexus v10.4.3-beta release validator.

Covers independent RAR member verification, unified recovered-media output,
and Language Guard/Language Inbox field regressions.
"""

import compileall
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace


def require(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def main() -> int:
    root = Path(__file__).resolve().parent
    require(compileall.compile_dir(root / "app", quiet=1), "Python compilation failed")
    require(compileall.compile_file(str(root / "validate_v1043.py"), quiet=1), "Compilation failed: validate_v1043.py")
    node = __import__("shutil").which("node")
    if node:
        proc = subprocess.run([node, "--check", str(root / "app" / "static" / "app.js")], capture_output=True, text=True, timeout=60)
        require(proc.returncode == 0, f"JavaScript syntax failed: {proc.stderr}")

    td = tempfile.mkdtemp(prefix="arrnexus-v1043-validate-")
    os.environ["DB_PATH"] = str(Path(td) / "router.db")
    os.environ["DB_DIR"] = td
    os.environ["SESSION_SECRET"] = "v1043-validation-session-secret"
    os.environ["ARRNEXUS_SELF_UPDATE"] = "0"
    for key in ("RADARR_API_KEY", "SONARR_API_KEY", "LIDARR_API_KEY", "PROWLARR_API_KEY", "JELLYFIN_API_KEY", "SEERR_API_KEY"):
        os.environ[key] = ""

    from app.db import init_db, setting_set
    init_db()

    # Independent RAR member verification: one bad member must not terminate
    # testing of the remaining recoverable media.
    import app.archive_media as am
    calls: list[list[str]] = []
    original_run = am.run_cancellable

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        member = str(cmd[-1])
        if member == "Season 1.mp4":
            return SimpleNamespace(returncode=2, stdout="T Season 1.mp4\n", stderr="ERROR: CRC Failed : Season 1.mp4\n")
        return SimpleNamespace(returncode=2, stdout=f"T {member}\n", stderr="ERRORS:\nUnexpected end of archive\n")

    try:
        am.run_cancellable = fake_run
        media = [
            {"path": "Season 1.mp4", "size": 101},
            {"path": "Season 2.mp4", "size": 102},
            {"path": "Season 3.mp4", "size": 103},
        ]
        result = am._verify_media_members_independently("7z", "7z", Path("/fake/source.rar"), media)
    finally:
        am.run_cancellable = original_run
    require(len(calls) == 3 and [x[-1] for x in calls] == [x["path"] for x in media], "RAR verification is not one command per media member")
    require(result["verified_count"] == 2 and result["failed_count"] == 1 and result["untested_count"] == 0, "independent RAR verification did not isolate the CRC-broken member")
    require(result.get("verification_mode") == "per_member", "RAR verification mode marker missing")

    # Bernard's Watch field case: mixed explicit fail + undefined audio is
    # uncertainty at source level, not a confirmed/destructive rejection.
    import app.language_guard as lg
    require(any(marker in lg._cache_key("/mnt/debrid/decypharr/__all__/Bernards Watch", "fp", lg.LanguagePolicy()) for marker in ("language:v1043:", "language:v105:")), "Language Guard cache namespace is older than v10.4.3")
    original_files, original_probe, original_sidecar = lg.video_files, lg._ffprobe, lg._matching_external_english_subtitle
    try:
        lg.video_files = lambda p: [Path("/virtual/E01.mkv"), Path("/virtual/E02.mkv")]
        def fake_probe(path, timeout, **kwargs):
            if path.name == "E01.mkv":
                return {"streams": [{"codec_type": "audio", "tags": {"language": "fra"}, "disposition": {"default": 1}}]}
            return {"streams": [{"codec_type": "audio", "tags": {"language": "und"}, "disposition": {"default": 1}}]}
        lg._ffprobe = fake_probe
        lg._matching_external_english_subtitle = lambda path: False
        mixed = lg.inspect_source_languages("/mnt/debrid/decypharr/__all__/Bernards Watch", "mixed-fp", True)
    finally:
        lg.video_files, lg._ffprobe, lg._matching_external_english_subtitle = original_files, original_probe, original_sidecar
    require(mixed["status"] == "unknown" and not mixed["destructive_safe"], "mixed unknown/fail source was promoted to Language rejected")
    require("Manual review" in mixed["summary"], "mixed-language uncertainty does not explain Manual Review")

    # Unified TV Recovery output: legacy /data default migrates to the
    # DUMB-visible recovered-media root.
    from app import tv_recovery
    setting_set("tv_recovery.staging_root", "/data/split-cache")
    require(str(tv_recovery.staging_root()) == "/mnt/debrid/arrnexus-extracted", "legacy split-cache default was not migrated to recovered-media root")

    import app.main as main_app
    from app.updater import version_key
    from fastapi.testclient import TestClient

    require(not main_app._language_attention_row({"language_badge_key": "pass"}), "current-policy pass still qualifies for Language Inbox")
    require(main_app._language_attention_row({"language_badge_key": "unknown"}), "Manual Review source missing from Language Inbox")
    require(main_app._language_attention_row({"language_badge_key": "fail"}), "confirmed rejection missing from Language Inbox")
    require(main_app._language_attention_row({"language_badge_key": "recheck_required"}), "stale language source missing from Language Inbox")

    for template in sorted((root / "app" / "templates").glob("*.html")):
        main_app.templates.env.get_template(template.name)
    with TestClient(main_app.app) as client:
        health = client.get("/api/health")
        require(health.status_code == 200 and health.json().get("version") in {"10.4.3-beta", "10.4.4-beta", "10.5.0-beta", "10.5.1-beta"}, "v10.4.3 health/version")
        setup = client.post("/setup", data={"username": "v1043validator", "email": "v1043@example.invalid", "display_name": "V10.4.3 Validator", "password": "validation-password-123", "confirm": "validation-password-123"}, follow_redirects=False)
        require(setup.status_code == 303, "v10.4.3 administrator setup")
        landing = client.get("/", follow_redirects=False)
        require(landing.status_code == 200, "v10.4.3 landing page")

    require(version_key("10.4.3-beta") > version_key("10.4.2-beta"), "Updater will not recognize v10.4.3")

    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    archive_source = (root / "app" / "archive_media.py").read_text(encoding="utf-8")
    tv_source = (root / "app" / "tv_recovery.py").read_text(encoding="utf-8")
    language_source = (root / "app" / "language_guard.py").read_text(encoding="utf-8")
    sw = (root / "app" / "static" / "sw.js").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")

    require(any(v in main_source for v in ('APP_VERSION = "10.4.3-beta"', 'APP_VERSION = "10.4.4-beta"', 'APP_VERSION = "10.5.0-beta"', 'APP_VERSION = "10.5.1-beta"')), "v10.4.3+ application marker missing")
    require("_verify_media_members_independently" in archive_source and '"verification_mode": "per_member"' in archive_source, "per-member RAR verification markers missing")
    require(("language:v1043:" in language_source) or ("language:v105:" in language_source), "v10.4.3+ Language Guard cache marker missing")
    require("unknown or truncated or errors" in language_source, "unknown-first Language Guard aggregation marker missing")
    require("_language_attention_row" in main_source and "language_enriched = dedupe_rows" in main_source, "Language Inbox unresolved-before-grouping marker missing")
    require("archive_media.extraction_root()" in tv_source and "outdir_logical" in tv_source and "view_path(outdir_logical)" in tv_source, "DUMB-visible split-output markers missing")
    require("/data/split-cache" in tv_source and "legacy" in tv_source.lower(), "legacy split-cache migration marker missing")
    require((("arrnexus-static-v10.4.3" in sw) or (("arrnexus-static-v10.4.4" in sw) or ("arrnexus-static-v10.5.0" in sw))) or ("arrnexus-static-v10.5.1" in sw), "v10.4.3+ service worker marker missing")
    require("Version 10.4.3" in readme and "Recovery Pipeline & Language Inbox Hotfix" in readme, "README missing v10.4.3")
    require("10.4.3-beta — Recovery Pipeline & Language Inbox Hotfix" in changelog, "CHANGELOG missing v10.4.3")
    require((root / "docs" / "RELEASE_NOTES_v10.4.3.md").exists(), "v10.4.3 release notes missing")

    print("PASS: ArrNexus v10.4.3-beta independently verifies every RAR media member, keeps split episodes in the DUMB-visible recovery tree, removes passed copies from Language view, and keeps mixed/unknown language evidence in Manual Review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
