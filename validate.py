#!/usr/bin/env python3
"""ArrNexus v9.3.0-beta release validator.

Runs the complete carried v9.2 -> v9.1 -> v9 -> v8 -> v7 regression chain
before checking v9.3 music configuration, media-server support, targeted
performance architecture, public installation documentation and UI navigation.
"""
from __future__ import annotations

import asyncio
import compileall
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_v92(root: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(root / "validate_v92.py")],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=360,
    )
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)
    require(proc.returncode == 0, f"Retained v9.2 regression validator failed with exit code {proc.returncode}")


class _MediaProbeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_GET(self):
        if self.path.startswith("/System/Info"):
            body = json.dumps({"ServerName": "Validator Media", "Version": "1.2.3"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/health"):
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = b'<MediaContainer friendlyName="Validator Plex" version="9.9.9" machineIdentifier="validator" />'
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    root = Path(__file__).resolve().parent
    run_v92(root)
    require(compileall.compile_dir(root / "app", quiet=1), "Python compilation failed")

    with tempfile.TemporaryDirectory(prefix="arrnexus-v93-validate-") as tmp:
        os.environ["DB_PATH"] = str(Path(tmp) / "router.db")
        os.environ["DB_DIR"] = tmp
        os.environ["SESSION_SECRET"] = "validation-only-v93-session-secret"
        for key in (
            "RADARR_API_KEY", "SONARR_API_KEY", "LIDARR_API_KEY", "PROWLARR_API_KEY",
            "JELLYFIN_API_KEY", "PLEX_API_KEY", "EMBY_API_KEY", "SEERR_API_KEY",
        ):
            os.environ[key] = ""

        from fastapi.testclient import TestClient
        import app.main as main_app
        import app.media_servers as media_servers
        from app.connections import get_connection
        from app.db import setting_get
        from app.runtime_cache import StaleSnapshot
        from app.music import safe_external_url

        # Compile every Jinja template using the real ArrNexus environment.
        for template in sorted((root / "app" / "templates").glob("*.html")):
            main_app.templates.env.get_template(template.name)

        with TestClient(main_app.app) as client:
            landing = client.get("/", follow_redirects=False)
            require(landing.status_code == 200, "public landing page")
            for text in (
                "Plex", "Emby", "External media servers", "Git clone + Docker Compose source build",
                "Portainer Web editor / Stack file", "ghcr.io/&lt;GITHUB_OWNER&gt;/arrnexus:latest",
                "python3 -m venv .venv", "Music API Settings", "WHAT'S NEW IN V9.3",
            ):
                require(text in landing.text, f"v9.3 landing content missing: {text}")

            setup = client.post("/setup", data={
                "username": "v93validator",
                "email": "v93@example.invalid",
                "display_name": "V9.3 Validator",
                "password": "validation-password-123",
                "confirm": "validation-password-123",
            }, follow_redirects=False)
            require(setup.status_code == 303 and setup.headers.get("location") == "/onboarding", "administrator setup/onboarding")

            music_settings = client.get("/music/settings", follow_redirects=False)
            require(music_settings.status_code == 200, "Music API Settings page")
            for text in ("Spotify", "SoundCloud", "Jamendo", "Last.fm", "Public Home"):
                require(text in music_settings.text, f"music settings UI missing {text}")

            saved = client.post("/music/settings", data={
                "soundcloud_client_id": "validator-soundcloud-id",
                "soundcloud_client_secret": "validator-soundcloud-secret",
                "jamendo_client_id": "validator-jamendo-id",
                "lastfm_api_key": "validator-lastfm-key",
                "spotify_client_id": "validator-spotify-id",
                "spotify_client_secret": "validator-spotify-secret",
                "spotify_redirect_uri": "https://arrnexus.example.invalid/music/spotify/callback",
            }, follow_redirects=False)
            require(saved.status_code == 303, "Music API Settings save")
            require(setting_get("music.spotify.client_id") == "validator-spotify-id", "Spotify client ID not persisted")
            require(setting_get("music.soundcloud.client_id") == "validator-soundcloud-id", "SoundCloud client ID not persisted")

            # Connection writes must accept Plex and Emby without any live network dependency.
            for service, port in (("plex", 32400), ("emby", 8096)):
                response = client.post("/settings/connection", data={
                    "service": service, "instance": "main",
                    "url": f"http://{service}.example.invalid:{port}", "api_key": f"validator-{service}-token",
                }, follow_redirects=False)
                require(response.status_code == 303, f"{service} connection save")
                conn = get_connection(service)
                require(bool(conn.url and conn.api_key), f"{service} connection not persisted")

            custom = client.post("/media-servers/custom", data={
                "name": "Validator External Server",
                "url": "http://media.example.invalid:9000",
                "health_path": "/health",
                "auth_mode": "header",
                "auth_name": "X-Api-Key",
                "secret_value": "validator-external-secret",
            }, follow_redirects=False)
            require(custom.status_code == 303, "custom media server save")
            custom_rows = media_servers.list_custom(mask=True)
            require(custom_rows and custom_rows[0].get("has_secret"), "custom media server secret state missing")
            require("secret" not in custom_rows[0], "custom media server secret leaked by masked listing")

        # Validate actual media-server HTTP probe implementations against a local deterministic server.
        server = ThreadingHTTPServer(("127.0.0.1", 0), _MediaProbeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            plex = asyncio.run(media_servers.probe_builtin("plex", base, "validator-plex-token"))
            emby = asyncio.run(media_servers.probe_builtin("emby", base, "validator-emby-token"))
            require(plex.get("ok") and plex.get("name") == "Validator Plex", "Plex API probe/parser")
            require(emby.get("ok") and emby.get("name") == "Validator Media", "Emby API probe/parser")
        finally:
            server.shutdown()
            server.server_close()

        # Placeholder/example provider URLs must never be offered as catalogue links.
        require(safe_external_url("https://example.com/search?q=test") == "", "example.com external music URL was not rejected")
        require(safe_external_url("https://catalog.example.invalid/test") == "", "example.invalid external music URL was not rejected")
        require(safe_external_url("https://music.amazon.co.uk/search/test").startswith("https://"), "real HTTPS external music URL rejected")

        # Verify stale-while-revalidate semantics directly.
        async def snapshot_test():
            snap = StaleSnapshot(60)
            calls = {"n": 0}
            async def loader():
                calls["n"] += 1
                return {"value": calls["n"]}
            one, _, refreshing_one = await snap.get(loader)
            two, _, refreshing_two = await snap.get(loader)
            return one, two, calls["n"], refreshing_one, refreshing_two
        one, two, calls, r1, r2 = asyncio.run(snapshot_test())
        require(one == two == {"value": 1} and calls == 1 and not r1 and not r2, "StaleSnapshot fresh reuse")

        # Expensive Maintenance inputs must run concurrently rather than stacking
        # four filesystem/database waits on one navigation request.
        originals = (main_app.scan_broken_symlinks, main_app.scan_source, main_app.build_source_link_index, main_app.latest_import_by_source)
        def slow_broken(limit=500):
            time.sleep(0.15); return []
        def slow_items(*args, **kwargs):
            time.sleep(0.15); return []
        def slow_links(*args, **kwargs):
            time.sleep(0.15); return {}
        def slow_imports(*args, **kwargs):
            time.sleep(0.15); return {}
        main_app.scan_broken_symlinks, main_app.scan_source, main_app.build_source_link_index, main_app.latest_import_by_source = slow_broken, slow_items, slow_links, slow_imports
        main_app._BROKEN_LINK_SNAPSHOT.clear(); main_app._MAINTENANCE_SNAPSHOT.clear()
        try:
            started = time.monotonic()
            maintenance_state = asyncio.run(main_app._build_maintenance_snapshot())
            maintenance_elapsed = time.monotonic() - started
        finally:
            main_app.scan_broken_symlinks, main_app.scan_source, main_app.build_source_link_index, main_app.latest_import_by_source = originals
            main_app._BROKEN_LINK_SNAPSHOT.clear(); main_app._MAINTENANCE_SNAPSHOT.clear()
        require(not maintenance_state.get("error"), "maintenance deterministic snapshot failed")
        require(maintenance_elapsed < 0.38, f"maintenance work is not concurrent enough ({maintenance_elapsed:.3f}s)")

        # InfiniDysk health/queue/history/overview should also be one concurrent
        # bounded fan-out, not four serial upstream waits.
        original_connector_config = main_app.connector_config
        original_infini_client = main_app.InfiniDyskClient
        class FakeInfini:
            async def health(self): await asyncio.sleep(0.15); return {"ok": True}
            async def queue(self): await asyncio.sleep(0.15); return {"queue": {"slots": []}}
            async def history(self): await asyncio.sleep(0.15); return {"history": {"slots": []}}
            async def overview(self, window, media_filter): await asyncio.sleep(0.15); return {"throughput": []}
        main_app.connector_config = lambda key: {"enabled": True, "url": "http://validator.invalid"} if key == "infinidysk" else original_connector_config(key)
        main_app.InfiniDyskClient = FakeInfini
        try:
            started = time.monotonic()
            infini_state = asyncio.run(main_app._build_infinidysk_snapshot("24h"))
            infini_elapsed = time.monotonic() - started
        finally:
            main_app.connector_config = original_connector_config
            main_app.InfiniDyskClient = original_infini_client
        require(not infini_state.get("errors"), "InfiniDysk deterministic snapshot failed")
        require(infini_elapsed < 0.36, f"InfiniDysk calls are not concurrent enough ({infini_elapsed:.3f}s)")

        # Music artist independent work must run concurrently.  Four independent
        # 150ms calls plus one dependent album call should be ~300ms, not ~750ms.
        original_lidarr = main_app.LidarrClient
        original_mb = main_app.search_musicbrainz
        original_art = main_app.representative_artwork

        class FakeLidarr:
            async def artists(self):
                await asyncio.sleep(0.15)
                return [{"id": 7, "artistName": "Validator Artist"}]
            async def artist_lookup(self, name):
                await asyncio.sleep(0.15)
                return [{"artistName": name, "foreignArtistId": "x"}]
            async def albums(self, artist_id):
                await asyncio.sleep(0.15)
                return [{"id": 1, "title": "Validator Album"}]

        async def fake_mb(*args, **kwargs):
            await asyncio.sleep(0.15)
            return [{"artist": "Validator Artist"}]

        async def fake_art(*args, **kwargs):
            await asyncio.sleep(0.15)
            return "https://images.example.invalid/art.jpg"

        main_app.LidarrClient = FakeLidarr
        main_app.search_musicbrainz = fake_mb
        main_app.representative_artwork = fake_art
        try:
            started = time.monotonic()
            artist_state = asyncio.run(main_app._build_music_artist_state("Validator Artist"))
            elapsed = time.monotonic() - started
        finally:
            main_app.LidarrClient = original_lidarr
            main_app.search_musicbrainz = original_mb
            main_app.representative_artwork = original_art
        require(artist_state.get("existing") and artist_state.get("albums"), "music artist state did not merge Lidarr/metadata")
        require(elapsed < 0.58, f"music artist independent calls are not concurrent enough ({elapsed:.3f}s)")

    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    music_source = (root / "app" / "music.py").read_text(encoding="utf-8")
    media_source = (root / "app" / "media_servers.py").read_text(encoding="utf-8")
    base_source = (root / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    arrs_source = (root / "app" / "templates" / "arrs.html").read_text(encoding="utf-8")
    landing_source = (root / "app" / "templates" / "landing.html").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    css = (root / "app" / "static" / "app.css").read_text(encoding="utf-8")
    sw = (root / "app" / "static" / "sw.js").read_text(encoding="utf-8")

    require('APP_VERSION = "9.3.0-beta"' in main_source, "v9.3 version string missing")
    for marker in ("_MAINTENANCE_SNAPSHOT", "_PROBLEMS_SNAPSHOT", "_READINESS_SNAPSHOT", "_INBOX_SNAPSHOT", "_MUSIC_ARTIST_SNAPSHOTS"):
        require(marker in main_source, f"targeted v9.3 performance snapshot missing: {marker}")
    require("asyncio.gather" in main_source and "_build_music_artist_state" in main_source, "concurrent v9.3 music architecture missing")
    require("safe_external_url" in music_source and "example.com" in music_source, "safe external music URL guard missing")
    require("Plex" in media_source and "Emby" in media_source and "X-Plex-Token" in media_source and "X-Emby-Token" in media_source, "Plex/Emby media-server implementation missing")
    require("Music API Settings" in base_source and "Public Home / About" in base_source, "persistent settings/home navigation missing")
    require("X-Plex-Token" in arrs_source and "Emby" in arrs_source and "External / custom media server" in arrs_source, "media-server connection UI incomplete")
    for marker in ("Git clone + Docker Compose source build", "Portainer Web editor / Stack file", "ghcr.io/&lt;GITHUB_OWNER&gt;/arrnexus:latest", "python3 -m venv .venv"):
        require(marker in landing_source, f"public deployment guide missing {marker}")
        require(marker.replace("&lt;", "<").replace("&gt;", ">") in readme or marker in readme, f"README deployment guide missing {marker}")
    require("ArrNexus v9.3" in css and "v93-snapshot-bar" in css, "v9.3 product/performance CSS missing")
    require("arrnexus-static-v9.3" in sw, "v9.3 service-worker cache version missing")

    print("PASS: ArrNexus v9.3.0-beta retains v7/v8/v9/v9.1/v9.2 regressions and adds targeted slow-route snapshots, repaired Music API configuration/artist loading, Plex/Emby/custom media servers, persistent Public Home navigation and complete source/Git/Portainer/GHCR deployment documentation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
