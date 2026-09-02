#!/usr/bin/env python3
from __future__ import annotations

"""ArrNexus v10.6.2-beta Real-Debrid single-file resolver validator."""

import asyncio
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace


def require(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def main() -> int:
    root = Path(__file__).resolve().parent
    td = Path(tempfile.mkdtemp(prefix="arrnexus-v1062-validate-"))
    os.environ["DB_PATH"] = str(td / "router.db")
    os.environ["DB_DIR"] = str(td)
    os.environ["SESSION_SECRET"] = "v1062-validator"
    os.environ["ARRNEXUS_SELF_UPDATE"] = "0"

    from app import db
    db.init_db()

    # ------------------------------------------------------------------
    # Real-Debrid metadata resolver: authoritative size can be obtained
    # without unrestricting a link or exposing a signed direct URL.
    # ------------------------------------------------------------------
    from app import realdebrid as rd
    old_torrents, old_info, old_unrestrict = rd.torrents, rd.torrent_info, rd.unrestrict_link
    async def fake_torrents(limit: int = 250):
        return [{"id": "rd-v106", "filename": "Known Good Pack"}]
    async def fake_info(tid: str):
        return {
            "filename": "Known Good Pack",
            "files": [{"id": 7, "path": "/Known Good Pack/archive.rar", "bytes": 120, "selected": 1}],
            "links": ["https://host.invalid/restricted"],
        }
    async def fake_unrestrict(link: str):
        return {"download": "https://download.invalid/original", "filesize": 120, "id": "direct-v106"}
    rd.torrents, rd.torrent_info, rd.unrestrict_link = fake_torrents, fake_info, fake_unrestrict
    try:
        meta = asyncio.run(rd.direct_file_metadata_for_source_file("/mnt/debrid/decypharr/__all__/Known Good Pack", "archive.rar"))
        require(meta["file_bytes"] == 120, "RD metadata resolver lost authoritative file size")
        require("download" not in meta, "RD metadata preview unexpectedly exposed a signed URL")
        direct = asyncio.run(rd.direct_download_for_source_file("/mnt/debrid/decypharr/__all__/Known Good Pack", "archive.rar"))
        require(direct["download"] == "https://download.invalid/original", "RD direct resolver did not unrestrict exact file")
    finally:
        rd.torrents, rd.torrent_info, rd.unrestrict_link = old_torrents, old_info, old_unrestrict

    # ------------------------------------------------------------------
    # v10.6.1 field regression: Decypharr can expose an extensionless source
    # folder while RD reports a single-file torrent/archive with .rar.  This
    # must resolve as an exact equivalent, never as fuzzy title matching.
    # ------------------------------------------------------------------
    old_torrents, old_info = rd.torrents, rd.torrent_info
    async def stem_torrents(limit: int = 250):
        return [{"id": "queen-rd", "filename": "season-4_202405.rar"}]
    async def stem_info(tid: str):
        return {
            "filename": "season-4_202405.rar",
            "files": [{"id": 1, "path": "/season-4_202405.rar", "bytes": 3544189222, "selected": 1}],
            "links": ["https://host.invalid/queen-rar"],
        }
    rd.torrents, rd.torrent_info = stem_torrents, stem_info
    try:
        stem_meta = asyncio.run(rd.direct_file_metadata_for_source_file(
            "/mnt/debrid/decypharr/__all__/season-4_202405", "season-4_202405.rar"
        ))
        require(stem_meta["torrent_id"] == "queen-rd", "extension-equivalent RD torrent was not resolved")
        require(stem_meta["file_bytes"] == 3544189222, "authoritative Queen's Nose byte size was not retained")
    finally:
        rd.torrents, rd.torrent_info = old_torrents, old_info

    # ------------------------------------------------------------------
    # v10.6.2 live-field regression: RD can strip `.rar` from the selected
    # file path even though the exact torrent/archive identity retains it.
    # ------------------------------------------------------------------
    old_torrents, old_info = rd.torrents, rd.torrent_info
    async def stripped_torrents(limit: int = 250):
        return [{"id": "queen-stripped", "filename": "season-4_202405.rar"}]
    async def stripped_info(tid: str):
        return {
            "filename": "season-4_202405.rar",
            "files": [{"id": 1, "path": "/season-4_202405", "bytes": 3544189222, "selected": 1}],
            "links": ["https://host.invalid/queen-stripped"],
        }
    rd.torrents, rd.torrent_info = stripped_torrents, stripped_info
    try:
        stripped = asyncio.run(rd.direct_file_metadata_for_source_file(
            "/mnt/debrid/decypharr/__all__/season-4_202405", "season-4_202405.rar"
        ))
        require(stripped["torrent_id"] == "queen-stripped", "stripped selected-file .rar equivalent was not resolved")
        require(stripped["file_bytes"] == 3544189222, "stripped selected-file authoritative byte size was lost")
    finally:
        rd.torrents, rd.torrent_info = old_torrents, old_info

    # ------------------------------------------------------------------
    # Exact single-file torrent identity is enough when RD rewrites the sole
    # internal file path. There is one torrent, one file, one link: no guess.
    # ------------------------------------------------------------------
    old_torrents, old_info = rd.torrents, rd.torrent_info
    async def rewritten_torrents(limit: int = 250):
        return [{"id": "queen-rewritten", "filename": "season-4_202405.rar"}]
    async def rewritten_info(tid: str):
        return {
            "filename": "season-4_202405.rar",
            "files": [{"id": 9, "path": "/rd-object-0009", "bytes": 3544189222, "selected": 1}],
            "links": ["https://host.invalid/queen-rewritten"],
        }
    rd.torrents, rd.torrent_info = rewritten_torrents, rewritten_info
    try:
        rewritten = asyncio.run(rd.direct_file_metadata_for_source_file(
            "/mnt/debrid/decypharr/realdebrid/season-4_202405", "season-4_202405.rar"
        ))
        require(rewritten["torrent_id"] == "queen-rewritten", "exact single-file torrent with rewritten path was not resolved")
        require("single-file torrent identity" in rewritten["matched_by"], "single-file identity match was not recorded")
    finally:
        rd.torrents, rd.torrent_info = old_torrents, old_info

    # ------------------------------------------------------------------
    # Selection flags may be absent/normalised. One exact file + one link is
    # still unambiguous and must resolve without trusting provider bytes.
    # ------------------------------------------------------------------
    old_torrents, old_info = rd.torrents, rd.torrent_info
    async def noflag_torrents(limit: int = 250):
        return [{"id": "queen-noflag", "filename": "season-4_202405.rar"}]
    async def noflag_info(tid: str):
        return {
            "filename": "season-4_202405.rar",
            "files": [{"id": 3, "path": "/season-4_202405.rar", "bytes": 3544189222}],
            "links": ["https://host.invalid/queen-noflag"],
        }
    rd.torrents, rd.torrent_info = noflag_torrents, noflag_info
    try:
        noflag = asyncio.run(rd.direct_file_metadata_for_source_file(
            "/mnt/debrid/decypharr/__all__/season-4_202405", "season-4_202405.rar"
        ))
        require(noflag["torrent_id"] == "queen-noflag", "single-file RD response without selected flag was not resolved")
    finally:
        rd.torrents, rd.torrent_info = old_torrents, old_info

    # ------------------------------------------------------------------
    # RD may expose one direct link but omit file rows for an exact single-file
    # torrent. Use torrent bytes; still reject anything with multiple links.
    # ------------------------------------------------------------------
    old_torrents, old_info = rd.torrents, rd.torrent_info
    async def omitted_torrents(limit: int = 250):
        return [{"id": "queen-omitted", "filename": "season-4_202405.rar"}]
    async def omitted_info(tid: str):
        return {
            "filename": "season-4_202405.rar",
            "bytes": 3544189222,
            "files": [],
            "links": ["https://host.invalid/queen-omitted"],
        }
    rd.torrents, rd.torrent_info = omitted_torrents, omitted_info
    try:
        omitted = asyncio.run(rd.direct_file_metadata_for_source_file(
            "/mnt/debrid/decypharr/__all__/season-4_202405", "season-4_202405.rar"
        ))
        require(omitted["file_bytes"] == 3544189222, "single-link exact torrent did not retain torrent byte metadata")
    finally:
        rd.torrents, rd.torrent_info = old_torrents, old_info

    # ------------------------------------------------------------------
    # Multi-file ambiguity remains forbidden even when the torrent name is an
    # exact archive identity. Never guess among multiple selected links.
    # ------------------------------------------------------------------
    old_torrents, old_info = rd.torrents, rd.torrent_info
    async def ambiguous_torrents(limit: int = 250):
        return [{"id": "queen-amb", "filename": "season-4_202405.rar"}]
    async def ambiguous_info(tid: str):
        return {
            "filename": "season-4_202405.rar",
            "files": [
                {"id": 1, "path": "/part-a.bin", "bytes": 10, "selected": 1},
                {"id": 2, "path": "/part-b.bin", "bytes": 20, "selected": 1},
            ],
            "links": ["https://host.invalid/a", "https://host.invalid/b"],
        }
    rd.torrents, rd.torrent_info = ambiguous_torrents, ambiguous_info
    ambiguity = ""
    try:
        try:
            asyncio.run(rd.direct_file_metadata_for_source_file(
                "/mnt/debrid/decypharr/__all__/season-4_202405", "season-4_202405.rar"
            ))
        except Exception as exc:
            ambiguity = str(exc)
    finally:
        rd.torrents, rd.torrent_info = old_torrents, old_info
    require("did not contain a unique selected file" in ambiguity, "ambiguous multi-file RD candidate was not rejected")
    require("part-a.bin" in ambiguity or "selected" in ambiguity, "RD resolver failure did not expose candidate file diagnostics")

    # ------------------------------------------------------------------
    # Real-world regression model: provider mount advertises a different
    # byte length and has a failed CRC result, while exact RD original is
    # larger and verifies. Direct original must be preferred even when a
    # sequential provider copy *could* succeed.
    # ------------------------------------------------------------------
    from app import archive_media as am
    sandbox = td / "archive-stage"
    sandbox.mkdir()
    provider = sandbox / "archive.rar"
    provider.write_bytes(b"P" * 100)
    recovery = sandbox / "recovery"
    recovery.mkdir()
    logical = "/mnt/debrid/decypharr/__all__/Known Good Pack/archive.rar"
    plan = {
        "fingerprint": "fp-v106-direct",
        "catalogue_signature": "catalog-v106",
        "media": [{"path": "Season 1.mp4", "size": 12}],
        "logical_path": logical,
    }
    old_attrs = {}
    patch_names = [
        "inspect_archive", "view_path", "logical_from_view", "_archive_volume_actual_paths",
        "source_root", "extraction_root", "storage_state", "cache_get", "cache_set",
        "_rd_direct_metadata_descriptor", "_rd_direct_download_descriptor", "_copy_http_resumable",
        "_copy_provider_file_resilient", "_extractor", "_verify_media_members_independently", "log_event",
    ]
    for name in patch_names:
        old_attrs[name] = getattr(am, name)
    cache = {
        am._verification_key(plan["fingerprint"]): {
            "fingerprint": plan["fingerprint"],
            "catalogue_signature": plan["catalogue_signature"],
            "members": [{"path": "Season 1.mp4", "size": 12, "status": "failed", "error": "CRC Failed"}],
            "failed_count": 1,
        }
    }
    provider_copy_calls = {"count": 0}
    def fake_view(value):
        value = str(value)
        if value == logical:
            return provider
        prefix = "/mnt/debrid/arrnexus-extracted"
        if value.startswith(prefix):
            return recovery / value[len(prefix):].lstrip("/")
        return Path(value)
    def fake_http(url, destination, **kwargs):
        destination.write_bytes(b"D" * 120)
        return 120
    def forbidden_provider_copy(*args, **kwargs):
        provider_copy_calls["count"] += 1
        raise AssertionError("provider-mounted bytes were copied even though exact direct original was available")
    def fake_verify(kind, exe, archive, media, **kwargs):
        require(Path(archive).stat().st_size == 120, "local verifier did not receive complete direct original")
        return {"members": [{"path": "Season 1.mp4", "size": 12, "status": "verified", "error": ""}], "issues": [], "exit_code": 0}
    am.inspect_archive = lambda *a, **k: plan
    am.view_path = fake_view
    am.logical_from_view = lambda _p: Path(logical)
    am._archive_volume_actual_paths = lambda _p: [provider]
    am.source_root = lambda: "/mnt/debrid/decypharr/__all__"
    am.extraction_root = lambda: "/mnt/debrid/arrnexus-extracted"
    am.storage_state = lambda n=0: {"enough": True, "free": 10_000, "total": 20_000, "required": int(n)}
    am.cache_get = lambda key: cache.get(key)
    am.cache_set = lambda key, value: cache.__setitem__(key, value)
    am._rd_direct_metadata_descriptor = lambda _p: {"file_bytes": 120, "torrent_id": "rd-v106"}
    am._rd_direct_download_descriptor = lambda _p: {"file_bytes": 120, "download": "https://download.invalid/original"}
    am._copy_http_resumable = fake_http
    am._copy_provider_file_resilient = forbidden_provider_copy
    am._extractor = lambda: ("7z", "7z")
    am._verify_media_members_independently = fake_verify
    am.log_event = lambda *a, **k: None
    try:
        staged = am.stage_and_reverify(logical, expected_fingerprint=plan["fingerprint"])
    finally:
        for name, value in old_attrs.items():
            setattr(am, name, value)
    require(provider_copy_calls["count"] == 0, "direct-original path was not preferred after provider CRC failure")
    require(staged["classification"] == "provider_mount_untrusted_direct_verified", "direct original did not classify mount as untrusted")
    require(staged["mounted_size"] == 100 and staged["direct_size"] == 120 and staged["size_difference"] == 20, "provider/direct size mismatch was not retained")
    require(staged["verification"]["members"][0]["verification_source"] == "direct_realdebrid", "direct verification source was not authoritative")

    # ------------------------------------------------------------------
    # If provider verification has failed and RD is connected, failure to
    # resolve the direct original must STOP.  Provider bytes must never be
    # copied and then mislabelled as confirmed/genuine damage.
    # ------------------------------------------------------------------
    old_attrs = {}
    patch_names = [
        "inspect_archive", "view_path", "logical_from_view", "_archive_volume_actual_paths",
        "source_root", "storage_state", "cache_get", "cache_set",
        "_rd_direct_metadata_descriptor", "_copy_provider_file_resilient",
    ]
    for name in patch_names:
        old_attrs[name] = getattr(am, name)
    old_rd_connected = rd.connected
    unresolved_cache = {
        am._verification_key(plan["fingerprint"]): {
            "members": [{"path": "Season 1.mp4", "status": "failed", "error": "CRC Failed"}],
            "failed_count": 1,
        }
    }
    provider_copy_calls = {"count": 0}
    def should_not_copy(*args, **kwargs):
        provider_copy_calls["count"] += 1
        raise AssertionError("provider mount was copied after direct-original resolution failure")
    am.inspect_archive = lambda *a, **k: plan
    am.view_path = fake_view
    am.logical_from_view = lambda _p: Path(logical)
    am._archive_volume_actual_paths = lambda _p: [provider]
    am.source_root = lambda: "/mnt/debrid/decypharr/__all__"
    am.storage_state = lambda n=0: {"enough": True, "free": 10_000, "total": 20_000, "required": int(n)}
    am.cache_get = lambda key: unresolved_cache.get(key)
    am.cache_set = lambda key, value: unresolved_cache.__setitem__(key, value)
    am._rd_direct_metadata_descriptor = lambda _p: (_ for _ in ()).throw(RuntimeError("No exact RD torrent matched source/archive"))
    am._copy_provider_file_resilient = should_not_copy
    rd.connected = lambda: True
    unresolved_error = ""
    try:
        try:
            am.stage_and_reverify(logical, expected_fingerprint=plan["fingerprint"])
        except RuntimeError as exc:
            unresolved_error = str(exc)
    finally:
        rd.connected = old_rd_connected
        for name, value in old_attrs.items():
            setattr(am, name, value)
    require("not authoritative" in unresolved_error and "could not be resolved" in unresolved_error, "unresolved direct source did not stop authoritatively")
    require(provider_copy_calls["count"] == 0, "provider copy fallback still ran after direct resolution failed")
    unresolved_state = unresolved_cache.get(am._local_stage_key(plan["fingerprint"])) or {}
    require(unresolved_state.get("classification") == "direct_source_unresolved", "unresolved direct state was not persisted")

    # ------------------------------------------------------------------
    # Radarr/Sonarr missing scans: both services must exist and active queue
    # state must be surfaced rather than blindly creating duplicate rescue.
    # ------------------------------------------------------------------
    from app import rescue
    old_discover, old_client = rescue.discover_instances, rescue.client_for_instance
    sonarr_inst = SimpleNamespace(service="sonarr", api_key="key", instance="main", destination_key="tv")
    radarr_inst = SimpleNamespace(service="radarr", api_key="key", instance="main", destination_key="movies")
    class FakeSonarr:
        async def series(self):
            return [{"id": 10, "title": "Missing Show", "monitored": True, "year": 2001, "statistics": {"episodeCount": 6, "episodeFileCount": 4}, "seasons": [{"seasonNumber": 1, "monitored": True, "statistics": {"episodeCount": 6, "episodeFileCount": 4}}]}]
        async def queue(self, _limit):
            return {"records": [{"seriesId": 10}]}
    class FakeRadarr:
        async def movies(self):
            return [{"id": 20, "title": "Missing Movie", "monitored": True, "hasFile": False, "year": 1999, "tmdbId": 123, "status": "released"}]
        async def queue(self, _limit):
            return {"records": []}
    rescue.discover_instances = lambda: [sonarr_inst, radarr_inst]
    rescue.client_for_instance = lambda inst: FakeSonarr() if inst.service == "sonarr" else FakeRadarr()
    try:
        srows = asyncio.run(rescue.scan_missing_sonarr())
        rrows = asyncio.run(rescue.scan_missing_radarr())
    finally:
        rescue.discover_instances, rescue.client_for_instance = old_discover, old_client
    require(len(srows) == 1 and srows[0]["missing"] == 2 and srows[0]["actively_downloading"], "Sonarr Rescue missing/queue scan failed")
    require(len(rrows) == 1 and rrows[0]["title"] == "Missing Movie" and not rrows[0]["actively_downloading"], "Radarr Rescue missing scan failed")

    # ------------------------------------------------------------------
    # Templates and updater behaviour markers.
    # ------------------------------------------------------------------
    main_source = (root / "app/main.py").read_text(encoding="utf-8")
    archive_source = (root / "app/archive_media.py").read_text(encoding="utf-8")
    archive_tpl = (root / "app/templates/archive_media.html").read_text(encoding="utf-8")
    base_tpl = (root / "app/templates/base.html").read_text(encoding="utf-8")
    archive_rescue_tpl = (root / "app/templates/archive_rescue.html").read_text(encoding="utf-8")
    arr_rescue_tpl = (root / "app/templates/arr_rescue.html").read_text(encoding="utf-8")
    app_js = (root / "app/static/app.js").read_text(encoding="utf-8")
    updater_source = (root / "app/updater.py").read_text(encoding="utf-8")
    require(any(v in main_source for v in ('APP_VERSION = \"10.6.2-beta\"', 'APP_VERSION = \"10.6.3-beta\"')), "v10.6 application marker missing")
    require("provider_mount_untrusted_direct_verified" in archive_source and "Verify original directly" in archive_tpl, "direct-original archive recovery UI/logic missing")
    require("direct_file_metadata_for_source_file" in archive_source or "_rd_direct_metadata_descriptor" in archive_source, "RD authoritative-size comparison missing")
    require("Missing Radarr media" in archive_rescue_tpl and "Missing Sonarr media" in archive_rescue_tpl, "Archive Rescue does not cover both Arr services")
    require("Sonarr Rescue" in base_tpl and "Radarr Rescue" in base_tpl and "DEBRID CANDIDATES" in arr_rescue_tpl, "dedicated Arr Rescue UI missing")
    require("data-update-open" in base_tpl and "compareVersions" in app_js and "completedUpdate" in app_js, "version badge/update modal fix missing")
    require("Cache-Control" in main_source and "no-store" in main_source, "update API no-cache protection missing")
    require("version_key(latest) > version_key(current_version)" in updater_source, "server updater can still offer the running version")
    require(any(x in (root / "app/static/sw.js").read_text(encoding="utf-8") for x in ("arrnexus-static-v10.6.2", "arrnexus-static-v10.6.3")), "v10.6 service-worker cache marker missing")

    # Compile every Jinja template so new rescue/integrity markup cannot ship
    # with a syntax error.
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(root / "app/templates")))
    env.filters["human_size"] = lambda v: str(v)
    env.filters["urlencode"] = lambda v: str(v)
    for template in sorted((root / "app/templates").glob("*.html")):
        env.get_template(template.name)

    # Authenticated route smoke test. Patch Archive Rescue's indexer discovery
    # so certification never contacts a production Prowlarr instance.
    from fastapi.testclient import TestClient
    from app import archive_rescue as ar
    old_indexers = ar.internet_archive_indexers
    async def empty_indexers():
        return []
    ar.internet_archive_indexers = empty_indexers
    from app import main as main_app
    try:
        with TestClient(main_app.app) as client:
            setup = client.post("/setup", data={"username": "v106validator", "email": "v106@example.invalid", "display_name": "V10.6 Validator", "password": "validation-password-123", "confirm": "validation-password-123"}, follow_redirects=False)
            require(setup.status_code == 303, "v10.6 administrator setup failed")
            require(client.get("/sonarr-rescue").status_code == 200, "Sonarr Rescue route failed")
            require(client.get("/radarr-rescue").status_code == 200, "Radarr Rescue route failed")
            require(client.get("/archive-rescue").status_code == 200, "Archive Rescue route failed")
            health = client.get("/api/health")
            require(health.status_code == 200 and health.json().get("version") in {"10.6.2-beta", "10.6.3-beta", "10.7.0-beta", "10.8.0-beta", "10.8.1-beta"}, "v10.6 health/version failed")
    finally:
        ar.internet_archive_indexers = old_indexers

    print("PASS: ArrNexus v10.6.2 resolves safe Real-Debrid single-file archive variants, forbids provider fallback after CRC, and retains v10.6 rescue/updater behaviour")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


