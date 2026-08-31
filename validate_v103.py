#!/usr/bin/env python3
from __future__ import annotations

"""ArrNexus v10.3.0-beta release validator."""

import asyncio
import compileall
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def require(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def run_retained(root: Path) -> None:
    """Run every retained validator in a fully isolated process group.

    Historical TestClient layers can leave background workers alive after their
    final assertion. Child stdout/stderr therefore go to temporary files rather
    than inheriting this validator's pipes, and any leftover process-group
    members are terminated after the validator process exits. Assertions and
    PASS criteria are unchanged.
    """
    import signal
    layers = [
        ("validate_v7.py", "v7", {"ARRNEXUS_VALIDATE_LAYER_ONLY": "1"}),
        ("validate_v8.py", "v8", {"ARRNEXUS_VALIDATE_LAYER_ONLY": "1"}),
        ("validate_v9.py", "v9", {"ARRNEXUS_VALIDATE_LAYER_ONLY": "1"}),
        ("validate_v91.py", "v9.1", {"ARRNEXUS_VALIDATE_LAYER_ONLY": "1"}),
        ("validate_v92.py", "v9.2", {"ARRNEXUS_VALIDATE_LAYER_ONLY": "1"}),
        ("validate_v93.py", "v9.3", {"ARRNEXUS_VALIDATE_LAYER_ONLY": "1"}),
        ("validate_v94.py", "v9.4", {"ARRNEXUS_VALIDATE_LAYER_ONLY": "1"}),
        ("validate_v10.py", "v10", {"ARRNEXUS_VALIDATE_V10_ONLY": "1"}),
        ("validate_v101.py", "v10.1", {"ARRNEXUS_VALIDATE_V101_ONLY": "1"}),
        ("validate_v102.py", "v10.2", {"ARRNEXUS_VALIDATE_V102_ONLY": "1"}),
    ]
    for script, label, extra in layers:
        print(f"[retained] starting {label}", flush=True)
        env = os.environ.copy()
        env.update(extra)
        with tempfile.TemporaryDirectory(prefix=f"arrnexus-retained-{label.replace('.', '')}-") as td:
            out_path = Path(td) / "stdout.log"
            err_path = Path(td) / "stderr.log"
            with out_path.open("wb") as out, err_path.open("wb") as err:
                proc = subprocess.Popen(
                    [sys.executable, str(root / script)],
                    cwd=root, env=env, stdout=out, stderr=err, start_new_session=True,
                )
                try:
                    rc = proc.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait(timeout=10)
                    stdout = out_path.read_text(encoding="utf-8", errors="replace") if out_path.exists() else ""
                    stderr = err_path.read_text(encoding="utf-8", errors="replace") if err_path.exists() else ""
                    if stdout:
                        print(stdout.rstrip())
                    if stderr:
                        print(stderr.rstrip(), file=sys.stderr)
                    raise AssertionError(f"Retained {label} validator timed out")
                finally:
                    # Kill only leftover descendants in the validator's private
                    # process group. The direct validator has already exited.
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            stdout = out_path.read_text(encoding="utf-8", errors="replace")
            stderr = err_path.read_text(encoding="utf-8", errors="replace")
            if stdout:
                print(stdout.rstrip())
            if stderr:
                print(stderr.rstrip(), file=sys.stderr)
            require(rc == 0, f"Retained {label} validator failed with exit code {rc}")
            print(f"[retained] {label} complete", flush=True)


def main() -> int:
    root = Path(__file__).resolve().parent
    if os.getenv("ARRNEXUS_VALIDATE_V103_ONLY") != "1":
        run_retained(root)

    require(compileall.compile_dir(root / "app", quiet=1), "Python compilation failed")
    for name in ("bootstrap.py", "validate_v103.py", "validate_v102.py"):
        require(compileall.compile_file(str(root / name), quiet=1), f"Compilation failed: {name}")

    node = __import__("shutil").which("node")
    if node:
        proc = subprocess.run([node, "--check", str(root / "app" / "static" / "app.js")], text=True, capture_output=True, timeout=60)
        require(proc.returncode == 0, f"JavaScript syntax failed: {proc.stderr}")

    # Import the application only after binding it to an isolated fresh DB.
    with tempfile.TemporaryDirectory(prefix="arrnexus-v103-validate-") as tmp:
        os.environ["DB_PATH"] = str(Path(tmp) / "router.db")
        os.environ["DB_DIR"] = tmp
        os.environ["SESSION_SECRET"] = "validation-only-v103-session-secret"
        os.environ["ARRNEXUS_SELF_UPDATE"] = "0"
        for key in (
            "RADARR_API_KEY", "SONARR_API_KEY", "LIDARR_API_KEY", "PROWLARR_API_KEY",
            "JELLYFIN_API_KEY", "PLEX_API_KEY", "EMBY_API_KEY", "SEERR_API_KEY",
        ):
            os.environ[key] = ""

        from app.scanner import episode_identity, season_hints, parse_title_year
        from app.archive_rescue import torrent_files
        from app.tv_recovery import _boundaries
        from app.db import init_db, setting_set, setting_get
        from app.updater import version_key
        import app.lists as media_lists
        import app.main as main_app
        from fastapi.testclient import TestClient

        # Pure TV/archive parser checks.
        require(episode_identity("Season 6 episode 1.mp4") == (6, 1), "Archive-style Season N episode N parsing failed")
        require(episode_identity("Series 7 Episode 06.mkv") == (7, 6), "Series-style episode parsing failed")
        require(episode_identity("S06E03.mkv") == (6, 3), "Standard SxxExx parsing regressed")
        require(season_hints("The Queen's Nose (1995) 720p Season 1 S01 Complete") == [1], "Combined-season hint parsing failed")
        title, year = parse_title_year("The Queen's Nose (1995) 720p Season 1 S01 Complete")
        require(title == "The Queen's Nose" and year == 1995, "Queen's Nose title/year cleanup failed")

        payload = b"d4:infod5:filesld6:lengthi10e4:pathl12:Season 1.mp4eeee4:name4:showee"
        manifest = torrent_files(payload)
        require(len(manifest) == 1 and manifest[0]["name"] == "Season 1.mp4" and manifest[0]["video"], "Archive torrent manifest parsing failed")

        chapters = [
            {"start": 0.0, "end": 750.0, "title": "Episode 1"},
            {"start": 750.0, "end": 1500.0, "title": "Episode 2"},
        ]
        mode, confidence, boundaries = _boundaries(1500.0, 2, chapters)
        require(mode == "chapters" and confidence == 98 and len(boundaries) == 2, "Chapter-perfect TV split plan failed")
        mode, confidence, boundaries = _boundaries(1500.0, 3, [])
        require(mode == "runtime_estimate" and confidence < 80 and len(boundaries) == 3, "Runtime-assisted TV split planning failed")

        init_db()
        for template in sorted((root / "app" / "templates").glob("*.html")):
            main_app.templates.env.get_template(template.name)

        # Device OAuth start: app credentials are advanced setup; callback URI is not required.
        setting_set("lists.trakt.client_id", "validator-client")
        setting_set("lists.trakt.client_secret", "validator-secret", True)
        original_async_client = media_lists.httpx.AsyncClient

        class FakeResponse:
            status_code = 200
            content = b"{}"
            text = "{}"
            def json(self):
                return {"device_code":"dev-code", "user_code":"ABCD1234", "verification_url":"https://trakt.tv/activate", "expires_in":600, "interval":5}

        class FakeAsyncClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, *a, **kw): return FakeResponse()

        media_lists.httpx.AsyncClient = FakeAsyncClient
        try:
            state = asyncio.run(media_lists.trakt_device_begin())
            require(state.get("status") == "pending" and state.get("user_code") == "ABCD1234", "Trakt Device OAuth start failed")
            require(setting_get("lists.trakt.pending_device_code") == "dev-code", "Trakt pending device code did not persist")
        finally:
            media_lists.httpx.AsyncClient = original_async_client

        with TestClient(main_app.app) as client:
            health = client.get("/api/health", follow_redirects=False)
            require(health.status_code == 200 and version_key(str(health.json().get("version") or "0")) >= version_key("10.3.0-beta"), "v10.3+ health/version")
            setup = client.post("/setup", data={
                "username":"v103validator", "email":"v103@example.invalid", "display_name":"V10.3 Validator",
                "password":"validation-password-123", "confirm":"validation-password-123",
            }, follow_redirects=False)
            require(setup.status_code == 303, "v10.3 administrator setup")
            for path, marker in (
                ("/lists", "TRAKT ACCOUNT"),
                ("/archive-rescue", "Archive Rescue"),
                ("/maintenance/provider-cleanup", "Provider Duplicate Cleanup"),
            ):
                page = client.get(path, follow_redirects=False)
                require(page.status_code == 200 and marker in page.text, f"v10.3 page failed: {path}")

        require(version_key("10.3.0-beta") > version_key("10.2.0-beta"), "updater will not recognize v10.3 as newer")

    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    scanner_source = (root / "app" / "scanner.py").read_text(encoding="utf-8")
    router_source = (root / "app" / "router_service.py").read_text(encoding="utf-8")
    archive_source = (root / "app" / "archive_rescue.py").read_text(encoding="utf-8")
    tv_source = (root / "app" / "tv_recovery.py").read_text(encoding="utf-8")
    lists_source = (root / "app" / "lists.py").read_text(encoding="utf-8")
    inbox = (root / "app" / "templates" / "inbox.html").read_text(encoding="utf-8")
    lists_tpl = (root / "app" / "templates" / "lists.html").read_text(encoding="utf-8")
    base = (root / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    css = (root / "app" / "static" / "app.css").read_text(encoding="utf-8")
    sw = (root / "app" / "static" / "sw.js").read_text(encoding="utf-8")
    guide = (root / "docs" / "USER_GUIDE.md").read_text(encoding="utf-8")
    audit = (root / "docs" / "DOCUMENTATION_AUDIT.md").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")

    require("APP_VERSION" in main_source and version_key(main_source.split('APP_VERSION = "',1)[1].split('"',1)[0]) >= version_key("10.3.0-beta"), "v10.3+ version string missing")
    for marker in ("tv:default", "tv:kids", "tv:bbc", "movie:default", "movie:kids"):
        require(marker in inbox, f"Typed DMM route missing: {marker}")
    require("media_type_override" in router_source and "Combined-season video detected" in router_source, "Manual media-type override or combined-season guard missing")
    require("ARCHIVE_EPISODE_RE" in scanner_source and "combined_season" in scanner_source, "Archive TV parsing/combined-season detection missing")

    for marker in ("/inbox/language-scan", "/inbox/language-delete", "recheck_required", "run_language_cleanup_job"):
        require(marker in main_source, f"DMM Language workflow missing: {marker}")
    require("Check all unchecked" in inbox and "Force re-check all" in inbox and "Delete selected rejected" in inbox, "DMM Language bulk controls missing")

    for marker in ("trakt_device_begin", "trakt_device_poll", "pending_device_code", "_atomic_settings", "_load_trakt_username"):
        require(marker in lists_source, f"Trakt Device OAuth marker missing: {marker}")
    require("Advanced Trakt application setup" in lists_tpl and "Connect Trakt" in lists_tpl, "Trakt Device OAuth UI missing")

    for marker in ("scan_missing_sonarr", "search_archive", "release_manifest", "send_release_to_realdebrid", "torrent_files"):
        require(marker in archive_source, f"Archive Rescue implementation missing: {marker}")
    require("select_files" in (root / "app" / "realdebrid.py").read_text(encoding="utf-8"), "Selective Real-Debrid file selection missing")
    for route in ("/archive-rescue", "/archive-rescue/release/{token}", "/archive-rescue/send-rd", "/tv-recovery/analyse", "/tv-recovery/split"):
        require(route in main_source, f"v10.3 route missing: {route}")

    require("runtime_estimate" in tv_source and "ffprobe" in tv_source and "source_retained" in tv_source and ".partial" in tv_source, "TV Recovery split safety missing")
    require("arrnexus:appearance" in base, "early appearance preference application missing from document head")
    for marker in ('html[data-appearance="light"]', '--ink:#111113', '--surface-0:#fafafa', '.nx-sidebar', '.nx-topbar', 'Dashboard: remove the old navy surfaces'):
        require(marker in css, f"v10.3 appearance contract missing: {marker}")
    require("arrnexus-static-v10.3" in sw or "arrnexus-static-v10.4" in sw or "arrnexus-static-v10.4.1" in sw or "arrnexus-static-v10.5.0" in sw, "v10.3+ service-worker cache marker missing")

    require("Version 10.3" in readme and "Archive Rescue" in readme and "Advanced TV Recovery" in readme, "README missing v10.3 workflows")
    for text in ("Archive Rescue", "Advanced TV Recovery", "Trakt Device OAuth", "Check all unchecked"):
        require(text in guide, f"User Guide missing v10.3 documentation: {text}")
    for route in ("/archive-rescue", "/tv-recovery", "/inbox", "/lists"):
        require(route in audit, f"Documentation audit missing {route}")
    require((root / "docs" / "RELEASE_NOTES_v10.3.md").exists(), "v10.3 release notes missing")

    print("PASS: ArrNexus v10.3.0-beta retains v7-v10.2 regressions and adds typed DMM TV routing, bulk/current-policy Language Guard cleanup, Trakt Device OAuth, Internet Archive Rescue with selective Real-Debrid hand-off, Advanced TV Recovery and a product-wide Dark/Light appearance contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
