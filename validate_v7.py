#!/usr/bin/env python3
"""Offline ArrNexus v7.0.0 package validator.

Run inside the project folder after dependencies are installed:
    python validate.py

The test suite does not contact live Arr, Seerr, music, Prowlarr or Real-Debrid
services. Network-facing views are smoke-tested with deterministic in-process
fakes so template/route regressions are caught without user credentials.
"""
from __future__ import annotations
import sys

import compileall
import os
import tempfile
from pathlib import Path


def require(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def main() -> int:
    root = Path(__file__).resolve().parent
    require(compileall.compile_dir(root / "app", quiet=1), "Python compilation failed")

    with tempfile.TemporaryDirectory(prefix="arrnexus-v7-validate-") as tmp:
        os.environ["DB_PATH"] = str(Path(tmp) / "router.db")
        os.environ["DB_DIR"] = tmp
        os.environ["SESSION_SECRET"] = "validation-only-session-secret"

        from fastapi.testclient import TestClient
        import app.main as main_app
        from app.policy import ReleasePolicy, score_release
        from app.tvpacks import (
            classify_release,
            pack_matches,
            choose_best_complete,
            choose_best_season_packs,
        )

        # Every template must compile with the same Jinja environment used by
        # the app, including custom filters such as human_size.
        for template in sorted((root / "app" / "templates").glob("*.html")):
            main_app.templates.env.get_template(template.name)

        with TestClient(main_app.app) as client:
            require(client.get("/api/health").status_code == 200, "health endpoint")
            require(client.get("/setup").status_code == 200, "setup page")
            response = client.post(
                "/setup",
                data={
                    "username": "validator",
                    "email": "validator@example.invalid",
                    "display_name": "Validator",
                    "password": "validation-password-123",
                    "confirm": "validation-password-123",
                },
                follow_redirects=False,
            )
            require(response.status_code == 303, "administrator setup")

            # Baseline authenticated pages must degrade gracefully even when
            # the validation host has no DUMB namespace or live Arr services.
            for url in (
                "/", "/settings", "/profile", "/logs", "/jobs", "/rules",
                "/libraries", "/arrs", "/queue", "/scraping", "/maintenance",
                "/problems", "/timeline?title=Validation", "/discover", "/music",
                "/debrid", "/ecosystem", "/infinidysk", "/indexers", "/quality-lab", "/self-healing", "/static/manifest.webmanifest", "/static/sw.js",
            ):
                r = client.get(url, follow_redirects=False)
                require(r.status_code < 500, f"{url}: HTTP {r.status_code}")

            # Discover regression test: the real v3 failure was a white-screen
            # 500. Feed deterministic Seerr/library shelves through the route
            # and prove both shelves and search results render safely.
            old_seerr = main_app._seerr_shelves
            old_library = main_app._library_shelves
            old_mark = main_app._mark_shelf_library_state
            old_lookup = main_app.discover_lookup
            old_rd_connected = main_app.rd.connected

            async def fake_seerr():
                return ([{
                    "key":"trending-movies", "title":"Trending validation movies", "subtitle":"Seerr fake",
                    "items":[{"title":"Shelf Movie","year":2026,"media_type":"movie","poster":"","in_library":False}],
                }], "")

            async def fake_library():
                return ([{
                    "key":"radarr-kids", "title":"Movies · Kids", "subtitle":"1 shown",
                    "items":[{"title":"Owned Kids Movie","year":2025,"media_type":"movie","poster":"","in_library":True,"route":"kids"}],
                }], [])

            async def fake_mark(_shelves):
                return None

            async def fake_lookup(_q, _media_type):
                return [{"title":"Search Result Movie","year":2024,"genres":["Adventure"],"images":[]}]

            main_app._seerr_shelves = fake_seerr
            main_app._library_shelves = fake_library
            main_app._mark_shelf_library_state = fake_mark
            main_app.discover_lookup = fake_lookup
            main_app.rd.connected = lambda: False
            try:
                r = client.get("/discover?q=test&media_type=movie")
                require(r.status_code == 200, f"Discover deterministic render: {r.status_code}")
                text = r.text
                require("Trending validation movies" in text, "Discover Seerr shelf missing")
                require("Movies · Kids" in text, "Discover library shelf missing")
                require("Search Result Movie" in text, "Discover search result missing")
            finally:
                main_app._seerr_shelves = old_seerr
                main_app._library_shelves = old_library
                main_app._mark_shelf_library_state = old_mark
                main_app.discover_lookup = old_lookup
                main_app.rd.connected = old_rd_connected

            # Music source isolation: provider tabs must render their own data,
            # not silently reuse ListenBrainz highlights.
            old_featured = main_app.provider_featured
            old_provider_search = main_app.provider_search
            old_lidarr = main_app.LidarrClient

            async def fake_featured(source, genre="", count=24):
                label = str(source).upper()
                return ([{"source":label,"kind":"album","id":label,"title":f"{label} ONLY RELEASE","artist":f"{label} ARTIST","genre":genre,"artwork":"","external":""}], f"{label} isolated feed")

            async def fake_provider_search(source, term, kind="artist", count=30):
                label = str(source).upper()
                return ([{"source":label,"kind":kind,"id":"1","title":f"{label} SEARCH","artist":f"{label} ARTIST","genre":"","artwork":"","external":""}], "")

            class FakeLidarr:
                async def artists(self):
                    return []

            main_app.provider_featured = fake_featured
            main_app.provider_search = fake_provider_search
            main_app.LidarrClient = FakeLidarr
            try:
                apple = client.get("/music?source=apple")
                deezer = client.get("/music?source=deezer")
                require(apple.status_code == 200 and deezer.status_code == 200, "Music provider pages")
                require("APPLE ONLY RELEASE" in apple.text and "DEEZER ONLY RELEASE" not in apple.text, "Apple source isolation")
                require("DEEZER ONLY RELEASE" in deezer.text and "APPLE ONLY RELEASE" not in deezer.text, "Deezer source isolation")
            finally:
                main_app.provider_featured = old_featured
                main_app.provider_search = old_provider_search
                main_app.LidarrClient = old_lidarr

            # v7 Spotify personal-library plumbing. Client credentials alone
            # provide catalogue access; a separate per-user OAuth grant is what
            # unlocks saved music/top/recent endpoints. Keep this fully offline.
            from app.db import setting_set as _setting_set
            from app.music import spotify_authorize_url as _spotify_authorize_url, _spotify_track_card as _spotify_track_card
            import app.music as _music
            _setting_set("music.spotify.client_id", "validation-client-id")
            _setting_set("music.spotify.client_secret", "validation-client-secret", True)
            _setting_set("music.spotify.redirect_uri", "https://example.invalid/music/spotify/callback")
            auth_url = _spotify_authorize_url(1, "validation-state", "https://example.invalid/music/spotify/callback")
            require("accounts.spotify.com/authorize" in auth_url, "Spotify authorization URL")
            require("validation-state" in auth_url and "user-library-read" in auth_url and "user-top-read" in auth_url and "user-read-recently-played" in auth_url, "Spotify personal scopes")
            card = _spotify_track_card({
                "id":"track1", "name":"Validation Track",
                "artists":[{"name":"Validation Artist"}],
                "album":{"name":"Validation Album","release_date":"2026-01-01","images":[{"url":"https://example.invalid/art.jpg"}]},
                "external_urls":{"spotify":"https://open.spotify.com/track/track1"},
            }, section="saved")
            require(card.get("source") == "Spotify" and card.get("title") == "Validation Track" and card.get("section") == "saved", "Spotify personal result normalization")

            # A real linked account must populate all personal hub sections.
            # Mock the Spotify Web API only; the aggregator/normalizers remain real.
            _setting_set("music.spotify.user.1.refresh_token", "validation-refresh-token", True)
            _old_spotify_get = _music._spotify_user_get
            async def _fake_spotify_get(_uid, path, params=None):
                artist={"id":"artist1","name":"Validation Artist","genres":["rock"],"images":[{"url":"https://example.invalid/artist.jpg"}],"external_urls":{"spotify":"https://open.spotify.com/artist/artist1"}}
                album={"id":"album1","name":"Validation Album","release_date":"2026-01-01","artists":[{"name":"Validation Artist"}],"images":[{"url":"https://example.invalid/album.jpg"}],"external_urls":{"spotify":"https://open.spotify.com/album/album1"}}
                track={"id":"track1","name":"Validation Track","artists":[{"name":"Validation Artist"}],"album":album,"external_urls":{"spotify":"https://open.spotify.com/track/track1"}}
                if path == "/me": return {"id":"spotify-validator","display_name":"Spotify Validator"}
                if path == "/me/tracks": return {"items":[{"track":track}]}
                if path == "/me/albums": return {"items":[{"album":album}]}
                if path == "/me/playlists": return {"items":[{"id":"playlist1","name":"Validation Playlist","owner":{"display_name":"Spotify Validator"},"images":[{"url":"https://example.invalid/playlist.jpg"}],"external_urls":{"spotify":"https://open.spotify.com/playlist/playlist1"}}]}
                if path == "/me/top/tracks": return {"items":[track]}
                if path == "/me/top/artists": return {"items":[artist]}
                if path == "/me/player/recently-played": return {"items":[{"track":track}]}
                return {}
            _music._spotify_user_get = _fake_spotify_get
            try:
                import asyncio as _spotify_asyncio
                hub = _spotify_asyncio.run(_music.spotify_user_hub(1))
                require(hub.get("linked") is True and (hub.get("profile") or {}).get("display_name") == "Spotify Validator", "Spotify personal profile")
                for section in ("saved_tracks", "saved_albums", "playlists", "top_tracks", "top_artists", "recent"):
                    require(len(hub.get(section) or []) == 1, f"Spotify personal hub section: {section}")
            finally:
                _music._spotify_user_get = _old_spotify_get
            # Return to the normal app-configured/unlinked state for later UI smoke tests.
            _music.spotify_disconnect_user(1)

            # Debrid TV-pack UI with coverage visualizer, cache badge and smart
            # complete/missing-season actions. No live RD calls are made.
            old_rd_connected = main_app.rd.connected
            old_rd_user = main_app.rd.user
            old_rd_torrents = main_app.rd.torrents
            old_release_search = main_app._search_debrid_releases
            old_tv_coverage = main_app._tv_library_coverage

            async def fake_rd_user():
                return {"username":"validator"}

            async def fake_rd_torrents(_limit=500):
                return []

            async def fake_release_search(*_args, **_kwargs):
                pack = classify_release("Example.Show.Complete.Series.S01-S02.1080p.x265")
                return ([{
                    "title":"Example.Show.Complete.Series.S01-S02.1080p.x265",
                    "protocol":"torrent", "indexer":"Validation Indexer", "size":40*1024**3,
                    "seeders":50, "realDebridCached":True, "arrnexus_pack":pack.as_dict(),
                    "arrnexus_coverage":{"text":"Complete coverage","missing":[],"covered":[1,2]},
                    "arrnexus_policy":{"score":100,"decision":"preferred","reasons":["Complete/multi-season pack"]},
                }], {"title":"Example Show","year":2026,"overview":"Validation series","poster":"","genres":["Drama"],"seasons":[{"seasonNumber":1},{"seasonNumber":2}],"raw":{"tvdbId":123}})

            async def fake_coverage(_meta):
                return {"found":True,"title":"Example Show","instance":"nzbdav","route":"default","series_id":1,
                        "seasons":[{"number":1,"have":10,"total":10,"state":"complete"},{"number":2,"have":2,"total":7,"state":"partial"}],
                        "missing_seasons":[2],"complete_seasons":[1],"partial_seasons":[2]}

            main_app.rd.connected = lambda: True
            main_app.rd.user = fake_rd_user
            main_app.rd.torrents = fake_rd_torrents
            main_app._search_debrid_releases = fake_release_search
            main_app._tv_library_coverage = fake_coverage
            try:
                r = client.get("/debrid?release_q=Example%20Show&media_type=tv&pack_mode=full_series")
                require(r.status_code == 200, f"Debrid TV pack page: {r.status_code}")
                require("Full series" in r.text, "Full-series control missing")
                require("Get entire show intelligently" in r.text, "Smart full-show action missing")
                require("Get missing seasons only" in r.text, "Missing-only action missing")
                require("S02" in r.text and "2/7" in r.text, "Sonarr coverage visualizer missing")
                require("RD cached" in r.text, "Real-Debrid cache badge missing")
            finally:
                main_app.rd.connected = old_rd_connected
                main_app.rd.user = old_rd_user
                main_app.rd.torrents = old_rd_torrents
                main_app._search_debrid_releases = old_release_search
                main_app._tv_library_coverage = old_tv_coverage

        # v7 Language Guard is pure-testable without ffprobe/media files.
        from app.language_guard import LanguagePolicy, evaluate_probe_payload
        strict_language = LanguagePolicy(
            enabled=True, require_english_audio=True, require_english_subtitles=True,
            require_default_english_audio=False, unknown_is_failure=True, auto_upgrade_search=True,
        )
        english_probe={"streams":[
            {"codec_type":"audio","tags":{"language":"eng"},"disposition":{"default":1}},
            {"codec_type":"subtitle","tags":{"language":"en"},"disposition":{"default":0}},
        ]}
        foreign_probe={"streams":[
            {"codec_type":"audio","tags":{"language":"ita"},"disposition":{"default":1}},
            {"codec_type":"subtitle","tags":{"language":"ita"},"disposition":{"default":0}},
        ]}
        eng_result=evaluate_probe_payload(english_probe, strict_language)
        foreign_result=evaluate_probe_payload(foreign_probe, strict_language)
        require(eng_result.get("compliant") is True and eng_result.get("english_audio") and eng_result.get("english_subtitles"), "Language Guard English pass")
        require(foreign_result.get("compliant") is False and "English audio" in (foreign_result.get("missing") or []) and "English subtitles" in (foreign_result.get("missing") or []), "Language Guard foreign-media reject")

        # v5 connector/plugin architecture: install a data-only connector and
        # confirm it appears in the Ecosystem page without executing code.
        with TestClient(main_app.app) as client2:
            # Re-use the administrator created in the same temporary DB.
            login = client2.post("/login", data={"username":"validator","password":"validation-password-123"}, follow_redirects=False)
            require(login.status_code == 303, "v5 validator login")
            plugin = {
                "key":"validation-service", "name":"Validation Service", "category":"Community",
                "default_url":"http://127.0.0.1:9", "health_paths":["/health","/"],
                "capabilities":["health","search"], "auth_header":"X-Api-Key"
            }
            import json as _json
            r = client2.post("/ecosystem/plugin", files={"connector_file":("validation.json", _json.dumps(plugin).encode(), "application/json")}, follow_redirects=False)
            require(r.status_code == 303, "connector plugin install")
            page = client2.get("/ecosystem")
            require(page.status_code == 200 and "Validation Service" in page.text, "connector plugin render")

            # Quality Lab must parse and explain a release without any network.
            ql = client2.get("/quality-lab", params={
                "title":"Example.Movie.2026.2160p.WEB-DL.DV.HDR.x265-GROUP",
                "media_type":"movie", "protocol":"torrent", "size_gb":20, "seeders":30, "cached":"true"
            })
            require(ql.status_code == 200, "Quality Lab render")
            require("HEVC / x265" in ql.text, "Quality Lab parser")
            require("/100" in ql.text and "Cached on Real-Debrid" in ql.text, "Quality Lab explanation")

            # Self-Healing degrades gracefully when no DUMB Arr processes are present.
            sh = client2.get("/self-healing")
            require(sh.status_code == 200 and "Self-Healing AutoPilot" in sh.text, "Self-Healing page")

        # InfiniDysk Prometheus parser is deterministic and must ignore noise.
        from app.infinidysk import parse_prometheus_metrics
        metrics = parse_prometheus_metrics("# HELP x test\ninfinidysk_nntp_bytes_total 1024\npython_gc_objects 99\ninfinidysk_seek_latency_seconds 0.25\n")
        require(len(metrics) == 2, "InfiniDysk metric filtering")

        # TV pack parser / smart selector unit checks.
        full = classify_release("Show.Complete.Series.S01-S02.1080p")
        season = classify_release("Show.Season.2.1080p")
        episode = classify_release("Show.S02E03.1080p")
        dotted_range = classify_release("Show.Season.1-2.Complete.1080p")
        require(full.kind == "full_series" and full.seasons == (1,2), "full-series parser")
        require(dotted_range.kind == "full_series" and dotted_range.seasons == (1,2), "dotted season range parser")
        require(season.kind == "season_pack" and season.seasons == (2,), "season-pack parser")
        require(episode.kind == "episode" and episode.episodes == ((2,3),), "episode parser")
        require(pack_matches("full_series", full) and not pack_matches("full_series", season), "pack mode filter")

        rows = [
            {"title":"Show.S01-S02.Complete.1080p", "realDebridCached":True, "seeders":20,
             "arrnexus_pack":full.as_dict(), "arrnexus_policy":{"score":95,"decision":"preferred"}},
            {"title":"Show.S01.1080p", "realDebridCached":True, "seeders":15,
             "arrnexus_pack":classify_release("Show.S01.1080p").as_dict(), "arrnexus_policy":{"score":90,"decision":"preferred"}},
            {"title":"Show.S02.1080p", "realDebridCached":True, "seeders":12,
             "arrnexus_pack":classify_release("Show.S02.1080p").as_dict(), "arrnexus_policy":{"score":88,"decision":"preferred"}},
        ]
        require(choose_best_complete(rows,[1,2]) is rows[0], "best complete pack selector")
        packs, missing = choose_best_season_packs(rows,[1,2])
        require(len(packs)==2 and not missing, "season-pack coverage selector")

        # Policy must use pack-aware size ceilings: 120 GB can be reasonable for
        # a full series but not for one episode.
        policy = ReleasePolicy(max_size_gb=35,max_movie_size_gb=45,max_episode_size_gb=15,max_season_size_gb=100,max_series_size_gb=400,minimum_seeders=2)
        full_score = score_release({"title":"Show.Complete.Series.S01-S04.1080p.x265","protocol":"torrent","size":120*1024**3,"seeders":20},policy,"tv","full_series")
        episode_score = score_release({"title":"Show.S01E01.1080p.x265","protocol":"torrent","size":120*1024**3,"seeders":20},policy,"tv","episode")
        require(full_score["decision"] != "rejected", "full-series size policy")
        require(episode_score["decision"] == "rejected", "episode size policy")

        # v6 acquisition strategy: strict first/fallback behavior and exactly
        # one Arr grab. This test never contacts live indexers.
        from app.acquisition import rank_releases, choose_release, plan_and_grab
        raw_releases = [
            {"title":"Example.Movie.2026.1080p.WEB-DL.x265", "protocol":"usenet", "size":8*1024**3, "seeders":0, "indexer":"NZB test"},
            {"title":"Example.Movie.2026.2160p.WEB-DL.x265", "protocol":"torrent", "size":18*1024**3, "seeders":40, "indexer":"Torrent test"},
        ]
        ranked = rank_releases(raw_releases, "movie", False)
        chosen, reasons = choose_release(ranked, "usenet_first", prefer_cached=False)
        require(chosen and chosen.get("arrnexus_protocol") == "usenet", "Usenet-first strategy")
        chosen2, _ = choose_release([r for r in ranked if r.get("arrnexus_protocol") != "usenet"], "usenet_first", prefer_cached=False)
        require(chosen2 and chosen2.get("arrnexus_protocol") == "torrent", "Usenet fallback to Debrid")

        class FakeAcquireClient:
            def __init__(self): self.grabbed=[]
            async def releases(self, _id): return raw_releases
            async def grab_release(self, row): self.grabbed.append(row)
        fake_client=FakeAcquireClient()
        import asyncio as _asyncio
        acquired=_asyncio.run(plan_and_grab(fake_client,"movie",1,"usenet_first"))
        require(acquired.get("ok") and acquired.get("protocol") == "usenet", "v6 planner result")
        require(len(fake_client.grabbed)==1, "v6 planner must grab exactly one release")

        # Known operational failures receive a useful explanation in Unified Logs.
        from app.log_diagnostics import explain_log
        diag=explain_log("vfs reader: failed to write to cache file: 404 Not Found")
        require(diag and "404" in diag.get("title",""), "v6 VFS 404 diagnostic")
        seek=explain_log("could not seek to byte position 123456")
        require(seek and "byte range" in seek.get("title","").lower(), "v6 seek diagnostic")

        # Service-specific authentication probes. A reachable service with the
        # wrong token must fail — this is the exact v5 false-positive regression.
        import threading as _threading
        from http.server import BaseHTTPRequestHandler as _Handler, ThreadingHTTPServer as _Server
        from urllib.parse import urlparse as _urlparse, parse_qs as _parse_qs
        class _ProbeHandler(_Handler):
            def log_message(self,*_a): pass
            def _send(self,code,body=b'{}',content_type='application/json'):
                self.send_response(code); self.send_header('Content-Type',content_type); self.end_headers(); self.wfile.write(body)
            def do_GET(self):
                parsed=_urlparse(self.path)
                if parsed.path == '/version': return self._send(200,b'{"version":"2.5-test"}')
                if parsed.path == '/healthz': return self._send(200,b'{"status":"healthy","version":"1.2-test"}')
                if parsed.path == '/health': return self._send(200,b'{"status":"healthy","version":"validation"}')
                if parsed.path == '/frontend-health': return self._send(200,b'<!DOCTYPE html><html><body>DUMB frontend</body></html>', 'text/html')
                if parsed.path == '/api/get-overview-stats':
                    return self._send(200,b'{"window":"24h","tiles":{"activeReads":7,"articlesPerMinute":120,"bytesServedPerMinute":64000000},"throughput":[{"bucket":1,"articles":100,"bytesServed":32000000}],"providers":[{"provider":"validation.example","articles":100}],"heatmap":{"cells":[]}}') if self.headers.get('X-Api-Key') == 'correct-infinidysk' else self._send(401,b'{"error":"bad key"}')
                if parsed.path == '/api/torrents':
                    return self._send(200,b'[]') if self.headers.get('Authorization') == 'Bearer correct-decypharr' else self._send(401,b'{"error":"unauthorized"}')
                if parsed.path == '/api':
                    key=(_parse_qs(parsed.query).get('apikey') or [''])[0]
                    return self._send(200,b'{"queue":{"slots":[]}}') if key == 'correct-infinidysk' else self._send(401,b'{"error":"bad key"}')
                return self._send(404)
        srv=_Server(('127.0.0.1',0),_ProbeHandler); port=srv.server_address[1]; th=_threading.Thread(target=srv.serve_forever,daemon=True); th.start()
        try:
            from app.ecosystem import save_connector as _save_connector, probe_connector as _probe_connector
            _save_connector('decypharr',f'http://127.0.0.1:{port}','random-numbers',True)
            bad=_asyncio.run(_probe_connector('decypharr')); require(not bad.get('ok') and bad.get('auth_ok') is False, 'Decypharr random token must fail')
            _save_connector('decypharr',f'http://127.0.0.1:{port}','correct-decypharr',True)
            good=_asyncio.run(_probe_connector('decypharr')); require(good.get('ok') and good.get('auth_ok') is True, 'Decypharr valid token')
            _save_connector('infinidysk',f'http://127.0.0.1:{port}','random-numbers',True)
            bad2=_asyncio.run(_probe_connector('infinidysk')); require(not bad2.get('ok') and bad2.get('auth_ok') is False, 'InfiniDysk random key must fail')
            _save_connector('infinidysk',f'http://127.0.0.1:{port}','correct-infinidysk',True)
            good2=_asyncio.run(_probe_connector('infinidysk')); require(good2.get('ok') and good2.get('auth_ok') is True, 'InfiniDysk valid key')
            from app.infinidysk import InfiniDyskClient as _InfiniDyskClient
            overview=_asyncio.run(_InfiniDyskClient().overview('24h','all'))
            require((overview.get('tiles') or {}).get('activeReads') == 7 and overview.get('throughput'), 'InfiniDysk native Overview API')
            _save_connector('dumb',f'http://127.0.0.1:{port}','',True)
            dumb_good=_asyncio.run(_probe_connector('dumb')); require(dumb_good.get('ok') and dumb_good.get('api_ok') is True, 'DUMB API health verification')
        finally:
            srv.shutdown(); srv.server_close()

        # UI smoke: new navigation, acquisition selector, settings policy and logs.
        with TestClient(main_app.app) as client3:
            login=client3.post('/login',data={'username':'validator','password':'validation-password-123'},follow_redirects=False)
            require(login.status_code==303,'v7 validator login')
            disc=client3.get('/discover?q=test&media_type=movie')
            require(disc.status_code==200 and 'coordinate Usenet + Debrid acquisition' in disc.text, 'Discover acquisition UI')
            sett=client3.get('/settings')
            require(sett.status_code==200 and 'Acquisition strategy' in sett.text and 'Language Guard' in sett.text, 'Acquisition / Language Guard settings UI')
            logs=client3.get('/logs')
            require(logs.status_code==200 and 'Unified Logs' in logs.text, 'Unified Logs UI')
            require('nx-command-trigger' in logs.text and 'nx-nav-section' in logs.text, 'v7 isolated sidebar shell')
            eco=client3.get('/ecosystem')
            require(eco.status_code==200 and 'Trust the connection status' in eco.text and 'Save & verify' in eco.text, 'Connector verification UI')
            dec=client3.get('/decypharr')
            require(dec.status_code==200, 'Decypharr control page')
            idx=client3.get('/indexers')
            require(idx.status_code==200 and 'Indexer control' in idx.text, 'Prowlarr indexer control page')
            spotify=client3.get('/music?source=spotify')
            require(spotify.status_code==200 and 'Connect Spotify' in spotify.text and 'Trending now' in spotify.text, 'Spotify personal/trending UI')
            inf=client3.get('/infinidysk')
            require(inf.status_code==200 and 'LIVE OVERVIEW' in inf.text, 'InfiniDysk live Overview UI')

        js=(root/'app'/'static'/'app.js').read_text(encoding='utf-8')
        css=(root/'app'/'static'/'app.css').read_text(encoding='utf-8')
        inst=(root/'app'/'instances.py').read_text(encoding='utf-8')
        require('X-ArrNexus-Navigation' in js and 'pageCache=new Map()' in js, 'v7 soft navigation/prefetch')
        require('.nx-shell' in css and '.nx-nav-links' in css and 'grid-template-columns:repeat(3,minmax(280px,1fr))' in css, 'v7 responsive shell and connection grids')
        require('_INSTANCE_CACHE_TTL = 4.0' in inst, 'v7 namespace discovery cache')
        music_tpl=(root/'app'/'templates'/'music.html').read_text(encoding='utf-8')
        indexer_tpl=(root/'app'/'templates'/'indexers.html').read_text(encoding='utf-8')
        arr_source=(root/'app'/'arr.py').read_text(encoding='utf-8')
        inf_source=(root/'app'/'infinidysk.py').read_text(encoding='utf-8')
        base_tpl=(root/'app'/'templates'/'base.html').read_text(encoding='utf-8')
        eco_source=(root/'app'/'ecosystem.py').read_text(encoding='utf-8')
        require('Saved tracks' in music_tpl and 'Your Spotify highlights' in music_tpl and 'GLOBAL TREND PULSE · LISTENBRAINZ' in music_tpl, 'v7 Spotify personal hub sections')
        require('releases_for_series' in arr_source and 'releases_for_season' in arr_source, 'v7 Sonarr season-search fix')
        require('/api/get-overview-stats' in inf_source, 'v7 native InfiniDysk Overview client')
        require('Save to Prowlarr' in indexer_tpl, 'v7 Prowlarr indexer management')
        require('nx-version-badge' in base_tpl and 'nx-version-badge' in css, 'v7 version/channel badge')
        require('DUMB /health did not return JSON' in eco_source, 'v7 DUMB frontend false-positive guard')
        dockerfile=(root/'Dockerfile').read_text(encoding='utf-8')
        scanner_source=(root/'app'/'scanner.py').read_text(encoding='utf-8')
        library_source=(root/'app'/'library.py').read_text(encoding='utf-8')
        inbox_tpl=(root/'app'/'templates'/'inbox.html').read_text(encoding='utf-8')
        item_tpl=(root/'app'/'templates'/'item.html').read_text(encoding='utf-8')
        settings_tpl=(root/'app'/'templates'/'settings.html').read_text(encoding='utf-8')
        require('ffmpeg' in dockerfile, 'v7 Docker image includes ffprobe/ffmpeg')
        require('_SCAN_CACHE_TTL = 30.0' in scanner_source, 'v7 DMM scanner cache')
        require('_INVENTORY_TTL = 45.0' in library_source and '_LINK_TTL = 30.0' in library_source, 'v7 library/index cache')
        require('Language <b>{{ counts.language }}</b>' in inbox_tpl and 'language_badge_label' in inbox_tpl, 'v7 Language Guard Inbox UI')
        require('Check language now' in item_tpl and 'Non-destructive replacement path' in item_tpl, 'v7 Language Guard item review UI')
        require('action="/settings/language-guard"' in settings_tpl and 'Require English audio' in settings_tpl and 'Require English subtitles' in settings_tpl, 'v7 Language Guard settings UI')

    print("PASS: ArrNexus v7.0 Spotify personal library, native InfiniDysk telemetry, English Language Guard, Prowlarr indexer control, Sonarr TV search, strict connectors and performance caches")
    return 0


if __name__ == "__main__":
    # Retained validators can leave TestClient/background workers alive after
    # the final assertion. Explicit process exit prevents release-gate hangs
    # without changing any validation assertions.
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(code or 0))
