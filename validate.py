#!/usr/bin/env python3
"""Offline ArrNexus v6.1.1 package validator.

Run inside the project folder after dependencies are installed:
    python validate.py

The test suite does not contact live Arr, Seerr, music, Prowlarr or Real-Debrid
services. Network-facing views are smoke-tested with deterministic in-process
fakes so template/route regressions are caught without user credentials.
"""
from __future__ import annotations

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

    with tempfile.TemporaryDirectory(prefix="arrnexus-v6-validate-") as tmp:
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
                "/debrid", "/ecosystem", "/infinidysk", "/quality-lab", "/self-healing", "/static/manifest.webmanifest", "/static/sw.js",
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
            def _send(self,code,body=b'{}'):
                self.send_response(code); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
            def do_GET(self):
                parsed=_urlparse(self.path)
                if parsed.path == '/version': return self._send(200,b'{"version":"2.5-test"}')
                if parsed.path == '/healthz': return self._send(200,b'{"status":"healthy","version":"1.2-test"}')
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
        finally:
            srv.shutdown(); srv.server_close()

        # UI smoke: new navigation, acquisition selector, settings policy and logs.
        with TestClient(main_app.app) as client3:
            login=client3.post('/login',data={'username':'validator','password':'validation-password-123'},follow_redirects=False)
            require(login.status_code==303,'v6 validator login')
            disc=client3.get('/discover?q=test&media_type=movie')
            require(disc.status_code==200 and 'coordinate Usenet + Debrid acquisition' in disc.text, 'Discover acquisition UI')
            sett=client3.get('/settings')
            require(sett.status_code==200 and 'Acquisition strategy' in sett.text, 'Acquisition settings UI')
            logs=client3.get('/logs')
            require(logs.status_code==200 and 'Unified Logs' in logs.text, 'Unified Logs UI')
            require('nx-command-trigger' in logs.text and 'nx-nav-section' in logs.text, 'v6.1 isolated sidebar shell')
            eco=client3.get('/ecosystem')
            require(eco.status_code==200 and 'Trust the connection status' in eco.text and 'Save & verify' in eco.text, 'Connector verification UI')
            dec=client3.get('/decypharr')
            require(dec.status_code==200, 'Decypharr control page')

        js=(root/'app'/'static'/'app.js').read_text(encoding='utf-8')
        css=(root/'app'/'static'/'app.css').read_text(encoding='utf-8')
        inst=(root/'app'/'instances.py').read_text(encoding='utf-8')
        require('X-ArrNexus-Navigation' in js and 'pageCache=new Map()' in js, 'v6.1 soft navigation/prefetch')
        require('.nx-shell' in css and '.nx-nav-links' in css and 'grid-template-columns:repeat(3,minmax(280px,1fr))' in css, 'v6.1 responsive shell and connection grids')
        require('_INSTANCE_CACHE_TTL = 4.0' in inst, 'v6.1 namespace discovery cache')

    print("PASS: v6.1 UX shell, fast navigation/cache, acquisition fallback, verified connectors, Unified Logs diagnostics and v6 regressions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
