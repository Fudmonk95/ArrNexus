#!/usr/bin/env python3
from __future__ import annotations

"""ArrNexus v10.2.0-beta release validator.

Runs the complete retained v10.1 -> v7 chain first, then verifies the v10.2
list automation, Language Guard uncertainty safety, dependency-protected
provider cleanup, AIOMetadata integration and two-appearance UI.
"""

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


def run_v101(root: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(root / "validate_v101.py")], cwd=root,
        text=True, capture_output=True, timeout=480,
    )
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)
    require(proc.returncode == 0, f"Retained v10.1 regression validator failed with exit code {proc.returncode}")


def main() -> int:
    root = Path(__file__).resolve().parent
    if os.getenv("ARRNEXUS_VALIDATE_V102_ONLY") != "1":
        run_v101(root)

    require(compileall.compile_dir(root / "app", quiet=1), "Python compilation failed")
    for name in ("bootstrap.py", "validate_v102.py", "validate_v101.py"):
        require(compileall.compile_file(str(root / name), quiet=1), f"Compilation failed: {name}")

    node = __import__("shutil").which("node")
    if node:
        proc = subprocess.run([node, "--check", str(root / "app" / "static" / "app.js")], text=True, capture_output=True, timeout=60)
        require(proc.returncode == 0, f"JavaScript syntax failed: {proc.stderr}")

    with tempfile.TemporaryDirectory(prefix="arrnexus-v102-validate-") as tmp:
        os.environ["DB_PATH"] = str(Path(tmp) / "router.db")
        os.environ["DB_DIR"] = tmp
        os.environ["SESSION_SECRET"] = "validation-only-v102-session-secret"
        os.environ["ARRNEXUS_SELF_UPDATE"] = "0"
        for key in (
            "RADARR_API_KEY", "SONARR_API_KEY", "LIDARR_API_KEY", "PROWLARR_API_KEY",
            "JELLYFIN_API_KEY", "PLEX_API_KEY", "EMBY_API_KEY", "SEERR_API_KEY",
        ):
            os.environ[key] = ""

        from fastapi.testclient import TestClient
        import app.main as main_app
        import app.lists as media_lists
        import app.aiometadata as aiometadata
        from app.db import init_db, db
        from app.language_guard import load_language_policy, evaluate_probe_payload, result_badge
        from app.updater import version_key

        init_db()
        with db() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        require({"media_lists", "media_list_runs"}.issubset(tables), "v10.2 list tables missing")

        policy = load_language_policy()
        require(policy.require_english_audio is True, "English audio is not the v10.2 blocking default")
        require(policy.require_english_subtitles is False, "English subtitles are not optional by default")
        spanish = evaluate_probe_payload({"streams":[{"codec_type":"audio","tags":{"language":"spa"}}]}, policy)
        require(spanish.get("status") == "fail" and spanish.get("destructive_safe") is True, "confirmed non-English audio not classified as a safe policy rejection")
        unknown = evaluate_probe_payload({"streams":[{"codec_type":"audio","tags":{}}]}, policy)
        require(unknown.get("status") == "unknown" and unknown.get("destructive_safe") is False, "unknown language metadata is not non-destructive Manual review")
        key, label = result_badge(unknown)
        require(key == "unknown" and "manual" in label.lower(), "Manual review badge missing")

        # List definition CRUD and preview/sync use existing request/routing machinery.
        list_id = media_lists.save_definition(
            list_id=None, name="Validator List", source_type="json", source_ref="https://example.invalid/list.json",
            media_type="mixed", movie_destination="auto", tv_destination="auto", acquisition_strategy="automatic",
            monitor=False, search_automatically=False, enabled=False, sync_interval_hours=12,
        )
        definition = media_lists.get_definition(list_id)
        require(definition and definition["name"] == "Validator List", "list definition did not persist")

        original_fetch = media_lists.fetch_items
        original_resolve = media_lists.resolve_item
        original_owned = media_lists._already_owned
        original_add = media_lists.discover_add
        captured = []
        async def fake_fetch(_defn):
            return [
                media_lists.NormalizedItem("movie", "New Movie", 2026, tmdb_id=101),
                media_lists.NormalizedItem("tv", "Existing Show", 2025, tvdb_id=202),
                media_lists.NormalizedItem("movie", "Unmatched Movie", 2024, imdb_id="tt0000001"),
            ]
        async def fake_resolve(item):
            if item.title == "Unmatched Movie":
                return None
            return {"title": item.title, "year": item.year, "tmdbId": item.tmdb_id, "tvdbId": item.tvdb_id}
        async def fake_owned(candidate, media_type):
            if candidate.get("title") == "Existing Show":
                return {"destination":"bbc", "arr_id":55, "has_file":True}
            return None
        async def fake_add(candidate, media_type, destination_key="auto", search=True, user_id=None, monitored=True):
            captured.append({"title":candidate.get("title"), "media_type":media_type, "destination":destination_key, "monitored":monitored})
            return {"item":{"id":777, "title":candidate.get("title")}, "destination":"default", "instance":"main"}
        media_lists.fetch_items = fake_fetch
        media_lists.resolve_item = fake_resolve
        media_lists._already_owned = fake_owned
        media_lists.discover_add = fake_add
        try:
            preview = asyncio.run(media_lists.preview_definition(definition))
            require((preview["total"], preview["existing"], preview["would_add"], preview["unmatched"]) == (3,1,1,1), "list preview classification failed")
            result = asyncio.run(media_lists.sync_definition(definition, preview=False, user_id=1))
            require(result["added"] == 1 and captured and captured[0]["monitored"] is False, "list sync did not add only the new title or preserve monitor setting")
        finally:
            media_lists.fetch_items = original_fetch
            media_lists.resolve_item = original_resolve
            media_lists._already_owned = original_owned
            media_lists.discover_add = original_add

        masked = aiometadata.sanitize({"password":"secret", "nested":{"apiKey":"abc"}, "name":"safe"})
        require(masked["password"] == "********" and masked["nested"]["apiKey"] == "********" and masked["name"] == "safe", "AIOMetadata secret masking failed")

        for template in sorted((root / "app" / "templates").glob("*.html")):
            main_app.templates.env.get_template(template.name)

        with TestClient(main_app.app) as client:
            health = client.get("/api/health", follow_redirects=False)
            require(health.status_code == 200 and version_key(str(health.json().get("version") or "0")) >= version_key("10.2.0-beta"), "v10.2+ health/version")
            setup = client.post("/setup", data={
                "username":"v102validator", "email":"v102@example.invalid", "display_name":"V10.2 Validator",
                "password":"validation-password-123", "confirm":"validation-password-123",
            }, follow_redirects=False)
            require(setup.status_code == 303, "v10.2 administrator setup")
            for path, marker in (
                ("/lists", "External lists become routed ArrNexus requests"),
                ("/aiometadata", "AIOMetadata"),
                ("/maintenance/provider-cleanup", "Provider Duplicate Cleanup"),
                ("/providers", "PROVIDER REGISTRY"),
                ("/libraries", "Registered media roots"),
            ):
                page = client.get(path, follow_redirects=False)
                require(page.status_code == 200 and marker in page.text, f"v10.2 page failed: {path}")

        require(version_key("10.2.0-beta") > version_key("10.1.0-beta"), "updater will not recognize v10.2 as newer")

    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    lists_source = (root / "app" / "lists.py").read_text(encoding="utf-8")
    language_source = (root / "app" / "language_guard.py").read_text(encoding="utf-8")
    router_source = (root / "app" / "router_service.py").read_text(encoding="utf-8")
    cleanup_source = (root / "app" / "provider_cleanup.py").read_text(encoding="utf-8")
    aiometa_source = (root / "app" / "aiometadata.py").read_text(encoding="utf-8")
    base = (root / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    providers = (root / "app" / "templates" / "providers.html").read_text(encoding="utf-8")
    libraries = (root / "app" / "templates" / "libraries.html").read_text(encoding="utf-8")
    css = (root / "app" / "static" / "app.css").read_text(encoding="utf-8")
    js = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")
    sw = (root / "app" / "static" / "sw.js").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    guide = (root / "docs" / "USER_GUIDE.md").read_text(encoding="utf-8")
    audit = (root / "docs" / "DOCUMENTATION_AUDIT.md").read_text(encoding="utf-8")

    require(any(f'APP_VERSION = \"{v}\"' in main_source for v in ('10.2.0-beta','10.3.0-beta','10.4.0-beta','10.4.1-beta','10.4.2-beta','10.4.3-beta','10.4.4-beta')), "v10.2+ version string missing")
    for marker in ("trakt_watchlist", "trakt_list", "imdb", "tmdb", "plex_watchlist", "simkl", "rss", "json"):
        require(marker in lists_source, f"list adapter missing: {marker}")
    require("_atomic_settings" in lists_source and "refresh_token" in lists_source, "atomic Trakt refresh-token handling missing")
    require("destructive_safe" in language_source and "Manual review" in language_source, "Language Guard v2 uncertainty safety missing")
    require("manual_review" in router_source and "destructive_safe" in router_source, "router destructive cleanup guard missing")
    for marker in ("expected_digest", "surviving_links", "delete_source_torrent_exact", "Refused"):
        require(marker in cleanup_source, f"provider cleanup safety missing: {marker}")
    for marker in ("/health", "/api/config/load/", "sanitize", "aiostreams_relationship"):
        require(marker in aiometa_source, f"AIOMetadata integration missing: {marker}")
    require('data-appearance-toggle' in base and '/lists' in base and '/aiometadata' in base, "v10.2 navigation/appearance controls missing")
    require('data-theme' not in css and 'dracula' not in css.lower() and 'nord' not in css.lower() and 'cyber' not in css.lower(), "obsolete multi-theme CSS remains")
    require('data-appearance="dark"' in css and 'data-appearance="light"' in css and '--bg:#fafafa' in css and '--bg:#030304' in css, "true Dark/Light appearance CSS missing")
    require("arrnexus:appearance" in js, "appearance persistence JavaScript missing")
    require("<details" in providers and "<details" in libraries and " open" not in libraries, "Providers/Libraries are not collapsed summary-first")
    require(("arrnexus-static-v10.2" in sw) or ("arrnexus-static-v10.3" in sw) or ("arrnexus-static-v10.4" in sw) or ("arrnexus-static-v10.4.1" in sw), "v10.2+ service-worker cache marker missing")
    require("Version 10.2" in readme and "Lists & Watchlists" in readme and "Provider Duplicate Cleanup" in readme, "README missing v10.2 detail")
    require("Lists & Watchlists" in guide and "AIOMetadata" in guide and "Provider Duplicate Cleanup" in guide, "User Guide missing v10.2 workflows")
    for route in ("/lists", "/api/lists", "/aiometadata", "/api/aiometadata/status", "/maintenance/provider-cleanup"):
        require(route in audit, f"Documentation audit missing {route}")
    require((root / "docs" / "RELEASE_NOTES_v10.2.md").exists(), "v10.2 release notes missing")

    print("PASS: ArrNexus v10.2.0-beta retains v7/v8/v9/v9.1/v9.2/v9.3/v9.4/v10/v10.1 regressions and adds routed Trakt/IMDb/TMDb/Plex/Simkl/RSS/JSON list automation, non-destructive Language Guard Manual review, dependency-protected provider cleanup, managed AIOMetadata, collapsed Providers/Libraries and true Dark/Light appearances")
    return 0


if __name__ == "__main__":
    # Retained validators can leave TestClient/background workers alive after
    # the final assertion. Explicit process exit prevents release-gate hangs
    # without changing any validation assertions.
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(code or 0))
