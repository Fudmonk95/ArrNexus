from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

_TEST_ROOT = tempfile.TemporaryDirectory(prefix="arrnexus-v13-test-")
BASE = Path(_TEST_ROOT.name)
DATA = BASE / "data"
ZURG = BASE / "zurg"
CACHE = BASE / "cache"
for p in (DATA, CACHE, ZURG / "__all__", ZURG / "__magic__", ZURG / "__downloads__", ZURG / "__nzb__", ZURG / "movies", ZURG / "shows", ZURG / "music", ZURG / "__unplayable__"):
    p.mkdir(parents=True, exist_ok=True)
(ZURG / "version.txt").write_text("version: test-nightly\n", encoding="utf-8")

os.environ["DB_PATH"] = str(DATA / "router.db")
os.environ["DB_DIR"] = str(DATA)
os.environ["ZURG_ROOT"] = str(ZURG)
os.environ["ZURG_CACHE_PATH"] = str(CACHE)
os.environ["ARRNEXUS_SESSION_SECRET"] = "v13-1-test-session-secret"
os.environ["MAGIC_ROOT"] = str(ZURG / "__magic__")
os.environ["MAGIC_ARR_PREFIX"] = str(ZURG / "__magic__")

from fastapi.testclient import TestClient

from app import db as dbmod
from app import orchestrator
from app import queue_janitor
from app import magic_intake
from app.main import app


def reset_db() -> None:
    path = Path(os.environ["DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(str(path) + suffix).unlink()
        except FileNotFoundError:
            pass
    dbmod.init_db()


class V131CoreTests(unittest.TestCase):
    def setUp(self):
        reset_db()
        orchestrator._CACHE.update({
            "updated_monotonic": 0.0, "updated_at": "",
            "summary": {"missing": 0, "queued": 0, "searching": 0, "working": 0, "cooldown": 0, "attention": 0},
            "rows": [], "error": "", "refreshing": False,
        })
        queue_janitor._CACHE.update({
            "updated_monotonic": 0.0, "updated_at": "", "rows": [],
            "summary": {"queue": 0, "healthy": 0, "warning": 0, "actionable": 0, "attention": 0},
            "error": "", "refreshing": False,
        })
        magic_intake._CACHE.update({
            "last_scan_at": "", "last_error": "", "running": False, "groups": [],
            "summary": {"total": 0, "matched": 0, "review": 0, "unmatched": 0, "imported": 0, "partial": 0},
        })
        magic_root = Path(os.environ["MAGIC_ROOT"])
        import shutil
        for child in list(magic_root.iterdir()):
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    def test_schema_contains_recovery_tables(self):
        conn = sqlite3.connect(os.environ["DB_PATH"])
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        for table in ("recovery_counters", "orchestrator_items", "orchestrator_events", "janitor_actions"):
            self.assertIn(table, tables)

    def test_sonarr_groups_multiple_missing_episodes_into_season(self):
        payload = {"records": [
            {"id": 101, "seriesId": 9, "seasonNumber": 2, "episodeNumber": 1, "title": "One", "monitored": True, "hasFile": False, "series": {"id": 9, "title": "Test Show"}},
            {"id": 102, "seriesId": 9, "seasonNumber": 2, "episodeNumber": 2, "title": "Two", "monitored": True, "hasFile": False, "series": {"id": 9, "title": "Test Show"}},
            {"id": 201, "seriesId": 9, "seasonNumber": 3, "episodeNumber": 4, "title": "Solo", "monitored": True, "hasFile": False, "series": {"id": 9, "title": "Test Show"}},
        ]}
        rows = orchestrator._sonarr_candidates(payload)
        by_key = {x["item_key"]: x for x in rows}
        self.assertIn("sonarr:season:9:2", by_key)
        self.assertEqual(by_key["sonarr:season:9:2"]["media_type"], "season")
        self.assertIn("sonarr:episode:201", by_key)

    def test_expired_orchestrator_cooldown_becomes_eligible(self):
        now = datetime.now(timezone.utc)
        item = {
            "item_key": "radarr:movie:1", "service": "Radarr", "media_type": "movie",
            "title": "Example", "arr_id": 1, "attempts": 1,
            "last_search_at": (now - timedelta(hours=8)).isoformat(),
            "next_retry_at": (now - timedelta(minutes=1)).isoformat(),
        }
        with patch.object(orchestrator.zurg, "title_matches", return_value={"movies": [], "shows": [], "downloads": [], "magic": [], "nzb": [], "working": []}):
            out = orchestrator._state_from_environment(item, {"radarr": set(), "sonarr": set(), "lidarr": set()}, orchestrator.settings_state())
        self.assertEqual(out["state"], "detected")
        self.assertIsNone(out["next_retry_at"])

    def test_neutarr_coexistence_defers_automatic_dispatch(self):
        cfg = dict(orchestrator.settings_state())
        cfg.update({"enabled": True, "neutarr_coexist": True})
        with patch.object(orchestrator, "settings_state", return_value=cfg):
            result = asyncio.run(orchestrator.dispatch_once())
        self.assertEqual(result["action"], "deferred_to_neutarr")

    def test_queue_classifier_prioritizes_invalid_mapping_over_manual_import(self):
        cfg = dict(queue_janitor.settings_state())
        cfg["warning_grace_minutes"] = 0
        row = {
            "id": 1, "seriesId": 4, "episodeId": 22, "title": "Release",
            "trackedDownloadStatus": "warning",
            "statusMessages": [{"messages": ["Invalid episode; Manual Import required"]}],
        }
        item = queue_janitor.classify("Sonarr", row, cfg)
        self.assertEqual(item["classification"], "invalid_mapping")
        self.assertEqual(item["recommended_action"], "blocklist_retry")

    def test_queue_classifier_id_match_auto_import(self):
        cfg = dict(queue_janitor.settings_state()); cfg["warning_grace_minutes"] = 0
        row = {"id": 2, "movieId": 44, "title": "Halloween.5", "statusMessages": [{"messages": ["Found matching movie via grab history, but release was matched to movie by ID. Manual Import required."]}]}
        item = queue_janitor.classify("Radarr", row, cfg)
        self.assertEqual(item["classification"], "id_manual_import")
        self.assertEqual(item["recommended_action"], "auto_import")

    def test_queue_classifier_bad_uncertain_and_stalled(self):
        cfg = dict(queue_janitor.settings_state()); cfg["warning_grace_minutes"] = 0
        bad = queue_janitor.classify("Radarr", {"id": 3, "movieId": 1, "title": "Bad", "errorMessage": "Missing articles"}, cfg)
        uncertain = queue_janitor.classify("Sonarr", {"id": 4, "episodeId": 1, "seriesId": 1, "title": "Maybe", "statusMessages": [{"messages": ["Unable to determine if file is a sample"]}]}, cfg)
        stalled = queue_janitor.classify("Lidarr", {"id": 5, "albumId": 1, "title": "Stuck", "statusMessages": [{"messages": ["Download stalled - no progress"]}]}, cfg)
        self.assertEqual((bad["classification"], bad["recommended_action"]), ("bad_release", "blocklist_retry"))
        self.assertEqual((uncertain["classification"], uncertain["recommended_action"]), ("sample_uncertain", "probe"))
        self.assertEqual((stalled["classification"], stalled["recommended_action"]), ("stalled_deferred", "defer_swaparr"))

    def test_hard_failure_limit_pauses(self):
        key = "radarr:movie:77"
        for n in range(3):
            rec = dbmod.recovery_register_failure(key, "Radarr", f"Release {n}", pause=(n == 2), reason="limit" if n == 2 else "")
        self.assertEqual(rec["failures"], 3)
        self.assertEqual(rec["paused"], 1)

    def test_resume_clears_retry_counter(self):
        key = "radarr:movie:88"
        dbmod.recovery_register_failure(key, "Radarr", "Bad", pause=True, reason="limit")
        queue_janitor.resume_media(key)
        self.assertEqual(dbmod.recovery_get(key)["failures"], 0)
        self.assertEqual(dbmod.recovery_get(key)["paused"], 0)

    def test_dry_run_cleanup_does_not_change_failure_counter(self):
        key = "radarr:movie:99"
        cfg = dict(queue_janitor.settings_state())
        cfg.update({"dry_run": True, "max_failures": 3, "search_after_cleanup": True})
        item = {"media_key": key, "service": "Radarr", "release_title": "Bad.Release", "queue_id": "1", "raw": {"movieId": 99}}
        outcome, detail = asyncio.run(queue_janitor.cleanup_bad_release(item, cfg))
        self.assertEqual(outcome, "dry_run")
        self.assertIn("1/3", detail)
        self.assertEqual(dbmod.recovery_get(key)["failures"], 0)

    def test_cached_janitor_api_state_strips_raw_payload(self):
        queue_janitor._CACHE["rows"] = [{"service": "Radarr", "queue_id": "1", "raw": {"secretish": "internal"}, "severity": "healthy"}]
        state = queue_janitor.cached_state()
        self.assertNotIn("raw", state["rows"][0])
        self.assertIsNotNone(queue_janitor.get_cached_item("Radarr", "1").get("raw"))


    def test_manual_import_dry_run_uses_safe_id_candidate_without_submission(self):
        class FakeClient:
            def __init__(self): self.submitted = False
            async def manual_import_candidates(self, **kwargs):
                return [{
                    "path": "/downloads/Halloween.5.mkv",
                    "movie": {"id": 44},
                    "quality": {"quality": {"id": 7, "name": "WEBDL-1080p"}},
                    "languages": [],
                    "rejections": ["Found matching movie via grab history; Manual Import required"],
                }]
            async def manual_import(self, files, import_mode="auto"):
                self.submitted = True
                return {"id": 123}
        fake = FakeClient()
        item = {
            "service": "Radarr", "raw": {"movieId": 44}, "output_path": "/downloads",
            "download_id": "abc", "release_title": "Halloween.5",
        }
        with patch.object(queue_janitor, "_client_for", return_value=fake):
            outcome, detail = asyncio.run(queue_janitor.attempt_auto_import(item, True))
        self.assertEqual(outcome, "dry_run")
        self.assertIn("ManualImport", detail)
        self.assertFalse(fake.submitted)

    def test_manual_import_refuses_unsafe_invalid_candidate(self):
        class FakeClient:
            async def manual_import_candidates(self, **kwargs):
                return [{
                    "path": "/downloads/bad.mkv", "series": {"id": 9},
                    "episodes": [{"id": 101}],
                    "rejections": ["Invalid episode; Manual Import required"],
                }]
        item = {"service": "Sonarr", "raw": {"seriesId": 9}, "output_path": "/downloads", "download_id": "x"}
        with patch.object(queue_janitor, "_client_for", return_value=FakeClient()):
            outcome, detail = asyncio.run(queue_janitor.attempt_auto_import(item, True))
        self.assertEqual(outcome, "attention")
        self.assertIn("none were safe", detail)

    def test_remove_queue_item_requests_blocklist_and_client_removal(self):
        from app.arr import RadarrClient
        client = RadarrClient()
        client.request = AsyncMock(return_value=None)
        asyncio.run(client.remove_queue_item(55, remove_from_client=True, blocklist=True, skip_redownload=True))
        method, path = client.request.call_args.args[:2]
        params = client.request.call_args.kwargs["params"]
        self.assertEqual((method, path), ("DELETE", "/api/v3/queue/55"))
        self.assertEqual(params["removeFromClient"], "true")
        self.assertEqual(params["blocklist"], "true")

    def test_manual_orchestrator_dry_run_can_preview_while_scheduler_disabled(self):
        cfg = dict(orchestrator.settings_state())
        cfg.update({"enabled": False, "dry_run": True, "neutarr_coexist": True, "daily_limit": 50, "max_active": 5, "priority": "least_attempts"})
        item = {
            "item_key": "radarr:movie:12", "service": "Radarr", "media_type": "movie", "title": "Test",
            "arr_id": 12, "sub_id": 12, "state": "detected", "attempts": 0, "created_at": "2026-01-01T00:00:00+00:00",
        }
        orchestrator._CACHE["rows"] = [item]
        async def no_refresh(): return orchestrator.cached_state()
        with patch.object(orchestrator, "settings_state", return_value=cfg), patch.object(orchestrator, "refresh", side_effect=no_refresh), patch.object(orchestrator, "_daily_search_count", return_value=0):
            result = asyncio.run(orchestrator.dispatch_once("radarr:movie:12", manual=True))
        self.assertEqual(result["action"], "dry_run")
        self.assertIn("DRY RUN", result["detail"])


    def test_magic_intake_groups_episode_releases(self):
        magic_root = Path(os.environ["MAGIC_ROOT"])
        for child in list(magic_root.iterdir()):
            if child.is_file(): child.unlink()
        (magic_root / "64.Zoo.Lane.S01E01.1080p.mkv").write_bytes(b"")
        (magic_root / "64.Zoo.Lane.S01E02.1080p.mkv").write_bytes(b"")
        async def no_match(media_type, title, year): return {}
        with patch.object(magic_intake, "_best_match", side_effect=no_match):
            state = asyncio.run(magic_intake.scan())
        groups = [g for g in state["groups"] if g["normalized_title"] == "64 Zoo Lane"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["media_type"], "tv")
        self.assertEqual(groups[0]["release_count"], 2)
        self.assertEqual(groups[0]["episodes"], ["S01E01", "S01E02"])

    def test_magic_intake_ignores_organised_directories(self):
        magic_root = Path(os.environ["MAGIC_ROOT"])
        for name in ("movies", "tv", "music"):
            (magic_root / name).mkdir(exist_ok=True)
            (magic_root / name / "should-not-scan.mkv").write_bytes(b"")
        async def no_match(media_type, title, year): return {}
        with patch.object(magic_intake, "_best_match", side_effect=no_match):
            state = asyncio.run(magic_intake.scan())
        self.assertFalse(any("should-not-scan" in p for g in state["groups"] for p in g["source_paths"]))

    def test_magic_intake_force_match_persists_selected_identity(self):
        magic_intake.ensure_schema()
        magic_intake._upsert_group({"group_key":"movie:test:2000","media_type":"movie","normalized_title":"Test","year":2000,"source_paths":["Test.2000.mkv"],"episodes":[],"state":"unmatched"})
        magic_intake.force_match("movie:test:2000", {"id": None, "external_id":"12345", "title":"Test Movie", "year":2000, "poster_url":"https://example/poster.jpg"})
        row = magic_intake.get_group("movie:test:2000")
        self.assertEqual(row["match_title"], "Test Movie")
        self.assertEqual(row["match_external_id"], "12345")
        self.assertEqual(row["confidence"], 100)

    def test_web_routes_render_and_version_is_stable_v13_1(self):
        dbmod.create_user("admin", "admin@example.test", "Admin", "abcdefgh")
        client = TestClient(app)
        login = client.post("/login", data={"identity": "admin", "password": "abcdefgh"}, follow_redirects=False)
        self.assertEqual(login.status_code, 303)
        rendered = {}
        for path in ("/", "/missing-media", "/queue-janitor", "/magic-intake", "/pipeline", "/zurg", "/settings", "/music/settings"):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
            rendered[path] = response.text
        from bs4 import BeautifulSoup
        for path in ("/missing-media", "/queue-janitor", "/magic-intake"):
            soup = BeautifulSoup(rendered[path], "html.parser")
            for form in soup.find_all("form"):
                self.assertIsNone(form.find_parent("form"), f"nested form in {path}")
        health = client.get("/api/health").json()
        self.assertEqual(health["version"], "13.1.0")

    def test_magic_intake_canonical_groups_same_sonarr_series(self):
        magic_root = Path(os.environ["MAGIC_ROOT"])
        (magic_root / "SpongeBob.S01E01.1080p.mkv").write_bytes(b"")
        (magic_root / "SpongeBob.SquarePants.S01E02.1080p.mkv").write_bytes(b"")

        async def same_series(media_type, title, year):
            self.assertEqual(media_type, "tv")
            return {
                "id": None, "external_id": "75886", "title": "SpongeBob SquarePants",
                "year": 1999, "poster_url": "https://example/sponge.jpg",
                "genres": ["Animation", "Family"], "confidence": 95,
            }

        with patch.object(magic_intake, "_best_match", side_effect=same_series):
            state = asyncio.run(magic_intake.scan())
        rows = [g for g in state["groups"] if g["match_external_id"] == "75886"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["release_count"], 2)
        self.assertEqual(rows[0]["episode_count"], 2)
        self.assertEqual(rows[0]["episodes"], ["S01E01", "S01E02"])
        self.assertEqual(rows[0]["seasons"], [1])
        self.assertIn("Animation", rows[0]["genres"])
        self.assertIn("kids", rows[0]["themes"])

    def test_magic_intake_expands_multi_episode_release_marker(self):
        markers = magic_intake._episode_markers("64.Zoo.Lane.S01E07-E09.1080p.WEB-DL")
        self.assertEqual(markers, ["S01E07", "S01E08", "S01E09"])

    def test_magic_intake_canonical_groups_music_by_lidarr_artist(self):
        magic_root = Path(os.environ["MAGIC_ROOT"])
        a = magic_root / "2011 - Panic Of Girls"
        b = magic_root / "1999 - No Exit"
        a.mkdir(); b.mkdir()
        (a / "01.flac").write_bytes(b"")
        (b / "01.flac").write_bytes(b"")

        async def same_artist(media_type, title, year):
            self.assertEqual(media_type, "music")
            return {
                "id": None, "external_id": "blondie-mbid", "title": "Blondie",
                "year": "", "poster_url": "https://example/blondie.jpg",
                "genres": ["Rock"], "confidence": 95,
            }

        with patch.object(magic_intake, "_best_match", side_effect=same_artist):
            state = asyncio.run(magic_intake.scan())
        rows = [g for g in state["groups"] if g["match_external_id"] == "blondie-mbid"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["display_title"], "Blondie")
        self.assertEqual(rows[0]["release_count"], 2)
        self.assertEqual(set(rows[0]["source_paths"]), {"2011 - Panic Of Girls", "1999 - No Exit"})

    def test_magic_filter_options_include_metadata_genres_and_themes(self):
        rows = [{"genres": ["Animation", "Family"], "themes": ["kids"], "media_type": "tv"}]
        filters = magic_intake.filter_options(rows)
        self.assertEqual(filters["genres"], ["Animation", "Family"])
        self.assertEqual(filters["themes"], ["kids"])

    def test_magic_force_match_creates_per_source_overrides(self):
        magic_intake.ensure_schema()
        magic_intake._upsert_group({
            "group_key": "raw:tv:odd-show:0", "canonical_key": "raw:tv:odd-show:0", "media_type": "tv",
            "normalized_title": "Odd Show", "year": None, "source_paths": ["Odd.Show.S01E01.mkv", "Odd.Show.S01E02.mkv"],
            "episodes": ["S01E01", "S01E02"], "genres": [], "state": "unmatched",
        })
        magic_intake.force_match("raw:tv:odd-show:0", {
            "id": None, "external_id": "tvdb-123", "title": "The Odd Show", "year": 2001,
            "poster_url": "https://example/odd.jpg", "genres": ["Family"],
        })
        with dbmod.db() as conn:
            count = conn.execute("SELECT COUNT(*) FROM magic_intake_overrides WHERE external_id='tvdb-123'").fetchone()[0]
        self.assertEqual(count, 2)

    def test_magic_tv_verification_checks_expected_episode_identity_not_whole_library(self):
        class FakeSonarr:
            async def episodes(self, series_id):
                return [
                    {"seasonNumber": 1, "episodeNumber": 1, "hasFile": True},
                    {"seasonNumber": 1, "episodeNumber": 2, "hasFile": False},
                    {"seasonNumber": 9, "episodeNumber": 99, "hasFile": True},
                ]
        group = {"episodes": ["S01E01", "S01E02"]}
        with patch.object(magic_intake.asyncio, "sleep", new=AsyncMock()):
            ok, detail = asyncio.run(magic_intake._verify(FakeSonarr(), "tv", 7, group, {"count": 99}))
        self.assertFalse(ok)
        self.assertIn("1/2 expected", detail)

    def test_lidarr_album_lookup_uses_album_lookup_endpoint(self):
        from app.arr import LidarrClient
        client = LidarrClient()
        client.request = AsyncMock(return_value=[])
        asyncio.run(client.album_lookup("Panic Of Girls"))
        method, path = client.request.call_args.args[:2]
        self.assertEqual((method, path), ("GET", "/api/v1/album/lookup"))

    def test_magic_import_enqueue_returns_without_waiting_for_import(self):
        magic_intake.ensure_schema()
        magic_intake._upsert_group({
            "group_key": "movie:external:55", "canonical_key": "movie:external:55", "media_type": "movie",
            "normalized_title": "Example", "year": 2020, "source_paths": ["Example.2020.mkv"], "episodes": [], "genres": [],
            "match_service": "radarr", "match_title": "Example", "match_external_id": "55", "confidence": 95, "state": "matched",
        })
        async def fake_import(*args, **kwargs):
            await asyncio.sleep(0)
            return {"ok": True}

        async def run_case():
            with patch.object(magic_intake, "import_group", side_effect=fake_import):
                result = magic_intake.enqueue_import("movie:external:55", "main", "")
                self.assertTrue(result["queued"])
                self.assertTrue(result["job_id"])
                await asyncio.sleep(0)
                await asyncio.sleep(0)
        asyncio.run(run_case())


    def test_magic_schema_migrates_v13_columns_in_place(self):
        path = Path(os.environ["DB_PATH"])
        conn = sqlite3.connect(path)
        try:
            conn.execute("DROP TABLE IF EXISTS magic_intake_groups")
            conn.execute("DROP TABLE IF EXISTS magic_intake_overrides")
            conn.execute("""CREATE TABLE magic_intake_groups (
                group_key TEXT PRIMARY KEY, media_type TEXT NOT NULL DEFAULT 'unknown', normalized_title TEXT NOT NULL DEFAULT '',
                year INTEGER, source_paths_json TEXT NOT NULL DEFAULT '[]', release_count INTEGER NOT NULL DEFAULT 0,
                episodes_json TEXT NOT NULL DEFAULT '[]', match_service TEXT NOT NULL DEFAULT '', match_id INTEGER,
                match_title TEXT NOT NULL DEFAULT '', match_year INTEGER, match_external_id TEXT NOT NULL DEFAULT '',
                poster_url TEXT NOT NULL DEFAULT '', confidence INTEGER NOT NULL DEFAULT 0, destination_key TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT 'discovered', ignored INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("""CREATE TABLE magic_intake_overrides (
                raw_key TEXT PRIMARY KEY, media_type TEXT NOT NULL, service TEXT NOT NULL, arr_id INTEGER,
                title TEXT NOT NULL DEFAULT '', year INTEGER, external_id TEXT NOT NULL DEFAULT '', poster_url TEXT NOT NULL DEFAULT '',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.commit()
        finally:
            conn.close()
        magic_intake.ensure_schema()
        conn = sqlite3.connect(path)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(magic_intake_groups)")}
            override_cols = {r[1] for r in conn.execute("PRAGMA table_info(magic_intake_overrides)")}
        finally:
            conn.close()
        for col in ("canonical_key", "genres_json", "progress", "progress_detail", "job_id"):
            self.assertIn(col, cols)
        self.assertIn("genres_json", override_cols)

    def test_nested_audio_folder_is_classified_as_music(self):
        magic_root = Path(os.environ["MAGIC_ROOT"])
        album = magic_root / "Some Album"
        disc = album / "Disc 1"
        disc.mkdir(parents=True)
        (disc / "01 - Track.flac").write_bytes(b"")
        self.assertEqual(magic_intake._media_type(album, album.name, []), "music")

    def test_old_style_1x02_episode_marker_is_detected(self):
        self.assertEqual(magic_intake._episode_markers("Show.Name.1x02.720p"), ["S01E02"])


    def test_v13_cached_match_survives_temporary_lookup_failure_during_upgrade(self):
        magic_root = Path(os.environ["MAGIC_ROOT"])
        source = "Legacy.Show.S01E01.1080p.mkv"
        (magic_root / source).write_bytes(b"")
        magic_intake.ensure_schema()
        # Simulate a v13.0 row: good external identity but no v13.1 canonical_key yet.
        with dbmod.db() as conn:
            conn.execute("""INSERT OR REPLACE INTO magic_intake_groups(
                group_key,canonical_key,media_type,normalized_title,source_paths_json,release_count,episodes_json,genres_json,
                match_service,match_title,match_external_id,confidence,state,ignored
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            ("tv:legacy-show:0", "", "tv", "Legacy Show", json.dumps([source]), 1, json.dumps(["S01E01"]), "[]",
             "sonarr", "Legacy Show", "tvdb-legacy", 95, "matched"))
        async def lookup_fails(media_type, title, year): return {}
        with patch.object(magic_intake, "_best_match", side_effect=lookup_fails):
            state = asyncio.run(magic_intake.scan())
        rows = [g for g in state["groups"] if g.get("match_external_id") == "tvdb-legacy"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["match_title"], "Legacy Show")



if __name__ == "__main__":
    unittest.main(verbosity=2)
