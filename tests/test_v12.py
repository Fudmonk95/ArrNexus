from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

_TEST_ROOT = tempfile.TemporaryDirectory(prefix="arrnexus-v12-test-")
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
os.environ["ARRNEXUS_SESSION_SECRET"] = "v12-test-session-secret"

from fastapi.testclient import TestClient

from app import db as dbmod
from app import orchestrator
from app import queue_janitor
from app.main import app


def reset_db() -> None:
    path = Path(os.environ["DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(str(path) + suffix).unlink()
        except FileNotFoundError:
            pass
    dbmod.init_db()


class V12CoreTests(unittest.TestCase):
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

    def test_schema_contains_v12_tables(self):
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

    def test_web_routes_render_and_version_is_stable_v12(self):
        dbmod.create_user("admin", "admin@example.test", "Admin", "abcdefgh")
        client = TestClient(app)
        login = client.post("/login", data={"identity": "admin", "password": "abcdefgh"}, follow_redirects=False)
        self.assertEqual(login.status_code, 303)
        rendered = {}
        for path in ("/", "/missing-media", "/queue-janitor", "/pipeline", "/zurg", "/settings", "/music/settings"):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
            rendered[path] = response.text
        from bs4 import BeautifulSoup
        for path in ("/missing-media", "/queue-janitor"):
            soup = BeautifulSoup(rendered[path], "html.parser")
            for form in soup.find_all("form"):
                self.assertIsNone(form.find_parent("form"), f"nested form in {path}")
        health = client.get("/api/health").json()
        self.assertEqual(health["version"], "12.0.0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
