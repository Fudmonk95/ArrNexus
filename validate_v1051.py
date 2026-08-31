#!/usr/bin/env python3
from __future__ import annotations

"""ArrNexus v10.5.1-beta cloud-RAR staging hotfix validator."""

import errno
import os
from pathlib import Path
import tempfile


def require(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def main() -> int:
    root = Path(__file__).resolve().parent
    td = Path(tempfile.mkdtemp(prefix="arrnexus-v1051-validate-"))
    os.environ["DB_PATH"] = str(td / "router.db")
    os.environ["DB_DIR"] = str(td)
    os.environ["SESSION_SECRET"] = "v1051-validator"
    os.environ["ARRNEXUS_SELF_UPDATE"] = "0"

    from app import db
    db.init_db()
    from app import archive_media

    # A provider EIO must be retried at the same byte offset and eventually
    # produce a byte-identical local copy when the source recovers.
    source = td / "flaky.rar"
    payload = (b"ArrNexus-v10.5.1-" * 65536) + os.urandom(131072)
    source.write_bytes(payload)
    dest = td / "flaky.partial"
    real_pread = archive_media.os.pread
    calls = {"n": 0, "offsets": []}
    def flaky_pread(fd: int, count: int, offset: int):
        calls["n"] += 1
        calls["offsets"].append(offset)
        if calls["n"] in {1, 2}:
            raise OSError(errno.EIO, "simulated Decypharr EIO")
        return real_pread(fd, count, offset)
    archive_media.os.pread = flaky_pread
    try:
        copied = archive_media._copy_provider_file_resilient(
            source, dest, expected_size=len(payload), copied_before=0,
            total_size=len(payload), progress=None, cancel_check=lambda: False,
        )
    finally:
        archive_media.os.pread = real_pread
    require(copied == len(payload), "resilient stage copy byte count mismatch")
    require(dest.read_bytes() == payload, "resilient stage copy was not byte-identical")
    require(calls["offsets"][:3] == [0, 0, 0], "failed provider range was not retried at the same offset")

    # Persistent EIO must never be skipped/padded or misclassified as local CRC.
    bad = td / "always-bad.partial"
    archive_media.os.pread = lambda fd, count, offset: (_ for _ in ()).throw(OSError(errno.EIO, "persistent EIO"))
    try:
        try:
            archive_media._copy_provider_file_resilient(
                source, bad, expected_size=len(payload), copied_before=0,
                total_size=len(payload), progress=None, cancel_check=lambda: False,
            )
            raise AssertionError("persistent provider EIO was silently ignored")
        except archive_media.ProviderStageReadError as exc:
            require(exc.offset == 0, "persistent EIO reported the wrong failed offset")
            require("after 8 retries" in str(exc), "persistent EIO did not report retry exhaustion")
    finally:
        archive_media.os.pread = real_pread

    # Exact Real-Debrid file mapping must select one exact archive and never
    # guess when the torrent contains several files.
    import asyncio
    from app import realdebrid as rd
    old_torrents, old_info, old_unrestrict = rd.torrents, rd.torrent_info, rd.unrestrict_link
    async def fake_torrents(limit: int = 250):
        return [{"id": "rd-test", "filename": "Tracy Pack"}]
    async def fake_info(tid: str):
        return {
            "filename": "Tracy Pack",
            "files": [
                {"id": 1, "path": "/Tracy Pack/readme.txt", "bytes": 2, "selected": 1},
                {"id": 2, "path": "/Tracy Pack/Season 1.rar", "bytes": 12345, "selected": 1},
            ],
            "links": ["https://host.invalid/readme", "https://host.invalid/rar"],
        }
    async def fake_unrestrict(link: str):
        require(link.endswith("/rar"), "RD resolver selected the wrong torrent link")
        return {"download": "https://download.invalid/exact-rar", "filesize": 12345, "id": "download-id"}
    rd.torrents, rd.torrent_info, rd.unrestrict_link = fake_torrents, fake_info, fake_unrestrict
    try:
        resolved = asyncio.run(rd.direct_download_for_source_file("/mnt/debrid/decypharr/__all__/Tracy Pack", "Season 1.rar"))
    finally:
        rd.torrents, rd.torrent_info, rd.unrestrict_link = old_torrents, old_info, old_unrestrict
    require(resolved["download"] == "https://download.invalid/exact-rar", "exact RD HTTPS fallback did not resolve")
    require(resolved["file_bytes"] == 12345, "exact RD HTTPS fallback size metadata missing")

    # Queen's Nose regression: valid recovered S01 episode files must supersede
    # a combined S01 source instead of making the group depend on the bad file.
    from app import tv_source_selection
    from app.scanner import ScanItem
    old_inspect = tv_source_selection.inspect_item
    old_video = tv_source_selection.video_files
    old_recovered = tv_source_selection._is_recovered
    good = "/mnt/debrid/arrnexus-extracted/The Queens Nose/Season 01"
    bad_combined = "/mnt/debrid/arrnexus-extracted/The Queen's Nose [899cac977632]"
    def fake_item(path: str):
        return ScanItem(Path(path).name, path, "tv", "The Queen's Nose", 1995, 6, [1], 1000, 576, "fp" + str(len(path)))
    def fake_videos(path: str):
        if path == good:
            return [Path(good) / f"The Queens Nose - S01E{i:02d}.mp4" for i in range(1, 7)]
        return [Path(bad_combined) / "Season 1.mp4"]
    tv_source_selection.inspect_item = fake_item
    tv_source_selection.video_files = fake_videos
    tv_source_selection._is_recovered = lambda _p: True
    try:
        described = tv_source_selection.describe_group_sources([bad_combined, good])
    finally:
        tv_source_selection.inspect_item = old_inspect
        tv_source_selection.video_files = old_video
        tv_source_selection._is_recovered = old_recovered
    require(described["preferred_by_season"].get(1) == good, "six recovered S01 episodes were not preferred")
    bad_row = next(x for x in described["sources"] if x["path"] == bad_combined)
    require(1 in bad_row["superseded_seasons"], "combined S01 source was not marked superseded")

    # Language Checks OFF must neutralise stale workflow blocks without deleting
    # cached Language Guard evidence. This covers the Bernard's Watch regression
    # where a completed/unknown re-check kept the Inbox demanding another scan.
    from app import language_guard
    language_source = "/mnt/debrid/decypharr/__all__/Bernards Watch"
    db.set_item_state(language_source, "language_review", "old unknown result")
    policy = language_guard.load_language_policy()
    language_cache_key = language_guard._cache_key(language_source, "bernards-fp", policy)
    db.cache_set(language_cache_key, {"status": "unknown", "summary": "metadata language und"})
    language_guard.set_language_checks_enabled(False)
    cleared = db.clear_language_block_states()
    require(cleared >= 1, "Language Checks OFF did not clear stale language-only workflow state")
    require(db.item_states()[language_source]["state"] == "waiting", "language review state still blocks Inbox while checks are OFF")
    require(db.cache_get(language_cache_key) is not None, "Language Checks OFF incorrectly deleted retained Language Guard cache")

    # The new job review queue must be a real authenticated route, not just a
    # template fragment. Use a synthetic review job; no production media is read.
    review_job = db.create_job("import", [{"source_path": language_source, "display_name": "Bernard's Watch", "destination_key": "tv"}])
    _, review_rows = db.get_job(review_job)
    db.update_job_item(int(review_rows[0]["id"]), status="review", stage="language_review", message="Manual review required")
    db.update_job(review_job, status="complete_with_reviews", reviewed=1, message="Finished: 1 review")
    from fastapi.testclient import TestClient
    from app import main as main_app
    with TestClient(main_app.app) as client:
        setup = client.post("/setup", data={"username": "v1051validator", "email": "v1051@example.invalid", "display_name": "V10.5.1 Validator", "password": "validation-password-123", "confirm": "validation-password-123"}, follow_redirects=False)
        require(setup.status_code == 303, "v10.5.1 review-route administrator setup failed")
        review_page = client.get(f"/jobs/{review_job}/review")
        require(review_page.status_code == 200 and "Confirm English" in review_page.text, "Import Job manual review route failed")

    main_source = (root / "app/main.py").read_text(encoding="utf-8")
    archive_source = (root / "app/archive_media.py").read_text(encoding="utf-8")
    archive_tpl = (root / "app/templates/archive_media.html").read_text(encoding="utf-8")
    require(any(v in main_source for v in ('APP_VERSION = \"10.5.1-beta\"', 'APP_VERSION = \"10.6.0-beta\"', 'APP_VERSION = \"10.6.1-beta\"')), "v10.5.1+ application marker missing")
    require("ProviderStageReadError" in archive_source and "os.pread" in archive_source, "resilient provider staging implementation missing")
    require("provider_io_failure" in archive_source and "provider source could not be read reliably" in archive_tpl, "provider-I/O classification UI missing")
    require(any(x in (root / "app/static/sw.js").read_text() for x in ("arrnexus-static-v10.5.1", "arrnexus-static-v10.6.0", "arrnexus-static-v10.6.1")), "v10.5.1+ service worker marker missing")
    jobs_tpl = (root / "app/templates/jobs.html").read_text(encoding="utf-8")
    job_tpl = (root / "app/templates/job.html").read_text(encoding="utf-8")
    review_tpl = (root / "app/templates/job_review.html").read_text(encoding="utf-8")
    app_js = (root / "app/static/app.js").read_text(encoding="utf-8")
    require("Review all" in job_tpl and "/review" in jobs_tpl, "Import Jobs manual-review controls missing")
    require("Confirm English" in review_tpl and "approve-language" in review_tpl, "Language manual-review action missing")
    require("data-toast-dismiss" in app_js and "dismissed-job-toasts" in app_js, "non-cancelling job-toast dismiss control missing")
    require("clear_language_block_states" in main_source, "master language toggle does not neutralise stale review blocks")
    require((root / "docs/RELEASE_NOTES_v10.5.1.md").exists(), "v10.5.1 release notes missing")
    print("PASS: ArrNexus v10.5.1 hardens provider extraction/staging, keeps valid Queen's Nose S01 preferred, makes Language Checks OFF clear stale blocks, adds job review controls, and makes job notifications dismissible without cancellation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
