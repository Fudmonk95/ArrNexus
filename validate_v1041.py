#!/usr/bin/env python3
from __future__ import annotations

"""ArrNexus v10.4.1-beta release validator.

The current layer focuses on the field hotfix that separates RAR archive health
from individual media-member health and only recovers independently verified
video files.
"""

import compileall
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


def require(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def _run_layer(root: Path, script: str, label: str, extra: dict[str, str], timeout: int = 90) -> None:
    print(f"[retained] starting {label}", flush=True)
    env = os.environ.copy(); env.update(extra); env["PYTHONUNBUFFERED"] = "1"
    with tempfile.TemporaryDirectory(prefix=f"arrnexus-v1041-{label.replace('.','')}-") as td:
        outp, errp = Path(td)/"out.log", Path(td)/"err.log"
        with outp.open("wb") as out, errp.open("wb") as err:
            proc = subprocess.Popen([sys.executable, str(root/script)], cwd=root, env=env, stdout=out, stderr=err, start_new_session=True)
            deadline = time.monotonic() + timeout
            pass_seen_at = None
            rc = None
            while time.monotonic() < deadline:
                rc = proc.poll()
                if rc is not None:
                    break
                text = outp.read_text(encoding="utf-8", errors="replace") if outp.exists() else ""
                if "PASS:" in text:
                    if pass_seen_at is None:
                        pass_seen_at = time.monotonic()
                    elif time.monotonic() - pass_seen_at >= 2.0:
                        try: os.killpg(proc.pid, signal.SIGTERM)
                        except ProcessLookupError: pass
                        try: proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            try: os.killpg(proc.pid, signal.SIGKILL)
                            except ProcessLookupError: pass
                            proc.wait(timeout=5)
                        rc = 0
                        break
                time.sleep(0.2)
            if rc is None:
                try: os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                proc.wait(timeout=10)
                rc = -9
        stdout = outp.read_text(encoding="utf-8", errors="replace")
        stderr = errp.read_text(encoding="utf-8", errors="replace")
        if rc != 0 or "PASS:" not in stdout:
            print(stdout, end="", flush=True)
            print(stderr, end="", file=sys.stderr, flush=True)
        require(rc == 0 and "PASS:" in stdout, f"Retained {label} validator failed/timed out with {rc}")
    print(f"[retained] {label} complete", flush=True)


def run_retained(root: Path) -> None:
    # v10.4's runner already executes v7-v10.3 with the same process-isolation
    # handling.  Run it directly, then run the v10.4 current layer against this
    # forward-compatible patch release.
    import validate_v104
    validate_v104.run_retained(root)
    _run_layer(root, "validate_v104.py", "v10.4", {"ARRNEXUS_VALIDATE_V104_ONLY":"1"}, timeout=100)


def main() -> int:
    root = Path(__file__).resolve().parent
    if os.getenv("ARRNEXUS_VALIDATE_V1041_ONLY") != "1":
        run_retained(root)

    require(compileall.compile_dir(root/"app", quiet=1), "Python compilation failed")
    require(compileall.compile_file(str(root/"validate_v1041.py"), quiet=1), "Compilation failed: validate_v1041.py")
    node = __import__("shutil").which("node")
    if node:
        proc = subprocess.run([node, "--check", str(root/"app"/"static"/"app.js")], text=True, capture_output=True, timeout=60)
        require(proc.returncode == 0, f"JavaScript syntax failed: {proc.stderr}")

    validation_tmp = tempfile.mkdtemp(prefix="arrnexus-v1041-validate-")
    os.environ["DB_PATH"] = str(Path(validation_tmp)/"router.db")
    os.environ["DB_DIR"] = validation_tmp
    os.environ["SESSION_SECRET"] = "v1041-validation-session-secret"
    os.environ["ARRNEXUS_SELF_UPDATE"] = "0"
    for key in ("RADARR_API_KEY","SONARR_API_KEY","LIDARR_API_KEY","PROWLARR_API_KEY","JELLYFIN_API_KEY","SEERR_API_KEY"):
        os.environ[key] = ""

    from app.archive_media import (
        _archive_issue_lines, _is_padding_member, _parse_7z_listing,
        _parse_7z_test_output, _safe_member,
    )

    require(_is_padding_member(".____padding_file/13"), "four-underscore torrent padding was not filtered")
    require(_is_padding_member(".___padding_file/7"), "three-underscore torrent padding was not filtered")
    require(not _is_padding_member("Season 4.mp4"), "real media was mistaken for torrent padding")
    require(_safe_member("Season 1/episode.mkv") and not _safe_member("../escape.mkv"), "archive traversal safety regression")

    listing = """Path = .____padding_file/13\nSize = 1859222\nPacked Size = 1859222\nCRC = 471CFE1A\n\nPath = Season 4.mp4\nSize = 518705428\nPacked Size = 518705428\nCRC = C1F7DDAA\n\nPath = Season 1.mp4\nSize = 464554440\nPacked Size = 464554440\nCRC = 1D2B3634\n\n"""
    parsed = _parse_7z_listing(listing)
    require(len(parsed) == 3 and parsed[-1]["size"] == 464554440, "RAR listing parser lost media members")
    issues = _archive_issue_lines("CRC = 471CFE1A\nWARNINGS:\nThere are data after the end of archive\n", "ERRORS:\nUnexpected end of archive\n")
    require("Unexpected end of archive" in issues and "There are data after the end of archive" in issues, "archive structural diagnostics were not retained")
    require(not any("471CFE1A" in x for x in issues), "normal CRC metadata was misclassified as an error")

    # Reproduce the exact field pattern: 17 members are reached by 7-Zip, one
    # member fails CRC, archive exits 2.  The other 16 must remain recoverable.
    media = [{"path": f"Season 6 episode {i}.mp4", "size": 1000+i} for i in range(1,7)]
    media += [{"path": f"Season 7 episode {i}.mp4", "size": 2000+i} for i in range(1,7)]
    media += [{"path": f"Season {i}.mp4", "size": 3000+i} for i in range(1,6)]
    stdout = "\n".join("T " + row["path"] for row in media) + "\nWarnings: 1\nErrors: 1\n"
    stderr = "ERRORS:\nUnexpected end of archive\n\nERROR: CRC Failed : Season 1.mp4\n"
    result = _parse_7z_test_output(stdout, stderr, media, 2)
    require(result["verified_count"] == 16, f"partial RAR should preserve 16 good members, got {result['verified_count']}")
    require(result["failed_count"] == 1 and result["untested_count"] == 0, "CRC-failed member classification is wrong")
    failed = [x for x in result["members"] if x["status"] == "failed"]
    require(len(failed) == 1 and failed[0]["path"] == "Season 1.mp4", "wrong member was marked failed")

    # If exit 2 occurs before a member is reached, that member must remain
    # unverified rather than being assumed good.
    short_stdout = "T Season 2.mp4\nT Season 3.mp4\n"
    partial_media = [{"path":"Season 2.mp4","size":2},{"path":"Season 3.mp4","size":3},{"path":"Season 4.mp4","size":4}]
    short = _parse_7z_test_output(short_stdout, "Unexpected end of archive", partial_media, 2)
    require(short["verified_count"] == 2 and short["untested_count"] == 1, "untested tail member was incorrectly trusted")

    from app.db import init_db
    from app.updater import version_key
    import app.main as main_app
    from fastapi.testclient import TestClient
    init_db()
    for template in sorted((root/"app"/"templates").glob("*.html")):
        main_app.templates.env.get_template(template.name)
    with TestClient(main_app.app) as client:
        health = client.get("/api/health")
        require(health.status_code == 200 and health.json().get("version") in {"10.4.1-beta", "10.4.2-beta", "10.4.3-beta", "10.4.4-beta", "10.5.0-beta", "10.5.1-beta", "10.6.0-beta"}, "v10.4.1 health/version")
        setup = client.post("/setup", data={"username":"v1041validator","email":"v1041@example.invalid","display_name":"V10.4.1 Validator","password":"validation-password-123","confirm":"validation-password-123"}, follow_redirects=False)
        require(setup.status_code == 303, "v10.4.1 administrator setup")
        page = client.get("/maintenance/archives", follow_redirects=False)
        require(page.status_code == 200 and "torrent padding are never extracted" in page.text, "v10.4.1 media-only Archive Recovery UI missing")
    require(version_key("10.4.1-beta") > version_key("10.4.0-beta"), "Updater will not recognize v10.4.1")

    main_source = (root/"app"/"main.py").read_text(encoding="utf-8")
    archive_source = (root/"app"/"archive_media.py").read_text(encoding="utf-8")
    archive_tpl = (root/"app"/"templates"/"archive_media.html").read_text(encoding="utf-8")
    job_tpl = (root/"app"/"templates"/"job.html").read_text(encoding="utf-8")
    sw = (root/"app"/"static"/"sw.js").read_text(encoding="utf-8")
    readme = (root/"README.md").read_text(encoding="utf-8")
    guide = (root/"docs"/"USER_GUIDE.md").read_text(encoding="utf-8")
    audit = (root/"docs"/"DOCUMENTATION_AUDIT.md").read_text(encoding="utf-8")

    require(any(v in main_source for v in ('APP_VERSION = "10.4.1-beta"', 'APP_VERSION = "10.4.2-beta"', 'APP_VERSION = "10.4.3-beta"', 'APP_VERSION = "10.4.4-beta"', 'APP_VERSION = "10.5.0-beta"', 'APP_VERSION = "10.5.1-beta"', 'APP_VERSION = "10.6.0-beta"')), "v10.4.1+ version marker missing")
    for marker in ('run_archive_verify_job', '/maintenance/archives/verify', 'selected_media = [str(x) for x in form.getlist("media_path")'):
        require(marker in main_source, f"v10.4.1 main marker missing: {marker}")
    for marker in ('_is_padding_member', '_parse_7z_test_output', 'verify_archive_media', 'verified video members', '_ffprobe_media', 'media_only'):
        require(marker.lower() in archive_source.lower(), f"v10.4.1 archive marker missing: {marker}")
    for marker in ('Media-only archive catalogue', 'Verify media files', 'Recover selected verified media', 'Recovered media source root', 'torrent padding'):
        require(marker in archive_tpl, f"v10.4.1 Archive Recovery UI marker missing: {marker}")
    require('Back to Archived Media Recovery' in job_tpl, "archive background jobs lack a return path")
    require((('arrnexus-static-v10.4.1' in sw) or ('arrnexus-static-v10.4.2' in sw) or ('arrnexus-static-v10.4.3' in sw) or ('arrnexus-static-v10.4.4' in sw) or ('arrnexus-static-v10.5.0' in sw)) or ("arrnexus-static-v10.5.1" in sw) or ("arrnexus-static-v10.6.0" in sw), "v10.4.1+ service-worker cache marker missing")
    require('Version 10.4.1' in readme and 'Selective RAR Recovery Hotfix' in readme, "README missing v10.4.1")
    require('Verify media files' in guide and '/maintenance/archives/verify' in audit, "docs missing v10.4.1 verification workflow")
    require((root/"docs"/"RELEASE_NOTES_v10.4.1.md").exists(), "v10.4.1 release notes missing")

    print("PASS: ArrNexus v10.4.1-beta retains v7-v10.4 regressions and adds fingerprint-cached partial RAR inspection, background per-member video verification, CRC-aware partial recovery, media-only selective extraction and ffprobe-validated recovered outputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
