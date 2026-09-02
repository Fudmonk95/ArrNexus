#!/usr/bin/env python3
from __future__ import annotations

"""ArrNexus v10.6.3-beta generated Real-Debrid archive-link validator."""

import asyncio
import os
from pathlib import Path
import tempfile


def require(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def main() -> int:
    root = Path(__file__).resolve().parent
    td = Path(tempfile.mkdtemp(prefix="arrnexus-v1063-validate-"))
    os.environ["DB_PATH"] = str(td / "router.db")
    os.environ["DB_DIR"] = str(td)
    os.environ["SESSION_SECRET"] = "v1063-validator"
    os.environ["ARRNEXUS_SELF_UPDATE"] = "0"

    from app import db
    db.init_db()
    from app import realdebrid as rd

    # ------------------------------------------------------------------
    # Live Queen's Nose shape observed through RD:
    # * torrent identity is season-4_202405 (extensionless)
    # * RD file rows are the payload members, not season-4_202405.rar
    # * one RD link represents the generated downloadable archive
    # * payload sum is 3,544,186,880 bytes
    # * physical generated RAR is 3,544,189,222 bytes
    # The resolver must use the one generated link and the unrestricted
    # physical size, never the payload sum / Decypharr stat size.
    # ------------------------------------------------------------------
    old_torrents, old_info, old_unrestrict = rd.torrents, rd.torrent_info, rd.unrestrict_link
    async def queen_torrents(limit: int = 250):
        return [{"id": "OWEEUOLVEZDMY", "filename": "season-4_202405"}]
    async def queen_info(tid: str):
        return {
            "id": tid,
            "filename": "season-4_202405",
            "bytes": 3544186880,
            "original_bytes": 3544186880,
            "files": [
                {"id": 1, "path": "/Season 1.mp4", "bytes": 464554440, "selected": 1},
                {"id": 2, "path": "/.___padding_file/0", "bytes": 1024, "selected": 1},
                {"id": 3, "path": "/Season 2.mp4", "bytes": 500000000, "selected": 1},
                {"id": 4, "path": "/Season 3.mp4", "bytes": 500000000, "selected": 1},
                {"id": 5, "path": "/Season 4.mp4", "bytes": 500000000, "selected": 1},
                {"id": 6, "path": "/Season 5.mp4", "bytes": 500000000, "selected": 1},
                {"id": 7, "path": "/season-4_202405_meta.sqlite", "bytes": 4096, "selected": 1},
            ],
            "links": ["https://host.invalid/generated-rar"],
        }
    calls = []
    async def queen_unrestrict(link: str):
        calls.append(link)
        return {
            "id": "direct-queen",
            "filename": "season-4_202405.rar",
            "filesize": 3544189222,
            "download": "https://download.invalid/season-4_202405.rar",
        }
    rd.torrents, rd.torrent_info, rd.unrestrict_link = queen_torrents, queen_info, queen_unrestrict
    try:
        meta = asyncio.run(rd.direct_file_metadata_for_source_file(
            "/mnt/debrid/decypharr/__all__/season-4_202405", "season-4_202405.rar"
        ))
        require(meta["torrent_id"] == "OWEEUOLVEZDMY", "exact multi-file torrent identity was not resolved")
        require(meta.get("generated_archive") is True, "sole RD link was not classified as a generated archive")
        require(meta["file_bytes"] == 3544189222, "physical generated-RAR size was not taken from unrestricted link")
        require(meta["file_bytes"] != 3544186880, "payload sum was incorrectly trusted as generated archive size")
        require(meta.get("payload_bytes") == 3544186880, "payload byte count diagnostic was lost")
        require(meta.get("direct_filename") == "season-4_202405.rar", "generated archive filename validation was lost")
        require("generated RD archive link" in meta.get("matched_by", ""), "generated archive match reason was not recorded")
        require("download" not in meta, "metadata resolver leaked signed direct URL")

        direct = asyncio.run(rd.direct_download_for_source_file(
            "/mnt/debrid/decypharr/__all__/season-4_202405", "season-4_202405.rar"
        ))
        require(direct["file_bytes"] == 3544189222, "direct descriptor lost physical RAR size")
        require(direct["download"].endswith("season-4_202405.rar"), "generated archive direct URL was not returned")
    finally:
        rd.torrents, rd.torrent_info, rd.unrestrict_link = old_torrents, old_info, old_unrestrict

    # ------------------------------------------------------------------
    # Safety: exact torrent + one generated link is still rejected when the
    # unrestricted link names a different archive. This prevents a single-link
    # response from becoming fuzzy title matching.
    # ------------------------------------------------------------------
    old_torrents, old_info, old_unrestrict = rd.torrents, rd.torrent_info, rd.unrestrict_link
    rd.torrents = queen_torrents
    rd.torrent_info = queen_info
    async def wrong_unrestrict(link: str):
        return {"filename": "different-release.rar", "filesize": 3544189222, "download": "https://download.invalid/wrong"}
    rd.unrestrict_link = wrong_unrestrict
    wrong_error = ""
    try:
        try:
            asyncio.run(rd.direct_file_metadata_for_source_file(
                "/mnt/debrid/decypharr/__all__/season-4_202405", "season-4_202405.rar"
            ))
        except Exception as exc:
            wrong_error = str(exc)
    finally:
        rd.torrents, rd.torrent_info, rd.unrestrict_link = old_torrents, old_info, old_unrestrict
    require("does not exactly match requested archive" in wrong_error, "wrong generated archive filename was not rejected")

    # ------------------------------------------------------------------
    # Safety: multiple RD links remain ambiguous for a multi-file payload.
    # ------------------------------------------------------------------
    old_torrents, old_info = rd.torrents, rd.torrent_info
    rd.torrents = queen_torrents
    async def multi_link_info(tid: str):
        info = await queen_info(tid)
        info["links"] = ["https://host.invalid/a", "https://host.invalid/b"]
        return info
    rd.torrent_info = multi_link_info
    multi_error = ""
    try:
        try:
            asyncio.run(rd.direct_file_metadata_for_source_file(
                "/mnt/debrid/decypharr/__all__/season-4_202405", "season-4_202405.rar"
            ))
        except Exception as exc:
            multi_error = str(exc)
    finally:
        rd.torrents, rd.torrent_info = old_torrents, old_info
    require("did not contain a unique selected file" in multi_error, "ambiguous multi-link torrent was not rejected")

    # ------------------------------------------------------------------
    # Static release markers / updater install-safe wiring.
    # ------------------------------------------------------------------
    main_source = (root / "app/main.py").read_text(encoding="utf-8")
    rd_source = (root / "app/realdebrid.py").read_text(encoding="utf-8")
    archive_source = (root / "app/archive_media.py").read_text(encoding="utf-8")
    sw = (root / "app/static/sw.js").read_text(encoding="utf-8")
    require('APP_VERSION = "10.6.3-beta"' in main_source, "v10.6.3 application marker missing")
    require("arrnexus-static-v10.6.3" in sw, "v10.6.3 service-worker marker missing")
    require("sole generated RD archive link" in rd_source, "generated archive resolver implementation missing")
    require("provider_failed and rd_connected and not direct_complete" in archive_source, "provider CRC no-fallback safety rule missing")

    # Compile all Python files.
    import compileall
    require(compileall.compile_dir(str(root / "app"), quiet=1), "Python compile validation failed")

    # Clean FastAPI startup + core routes.
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        health = client.get("/api/health")
        require(health.status_code == 200 and health.json().get("version") in {"10.6.3-beta", "10.7.0-beta", "10.8.0-beta", "10.8.1-beta"}, "v10.6.3 health/version failed")
        require(client.get("/").status_code == 200, "home route failed")
        require(client.get("/setup").status_code == 200, "setup route failed")

    print("PASS: ArrNexus v10.6.3 resolves exact multi-file torrents with one generated RD archive link using the physical unrestricted size, keeps ambiguity safeguards, and retains provider-CRC no-fallback safety")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


