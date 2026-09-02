#!/usr/bin/env python3
from __future__ import annotations

"""ArrNexus v10.8.1-beta recovery-reliability acceptance validator."""

import asyncio
import os
import tempfile
import threading
import time
from pathlib import Path


def require(value, message: str) -> None:
    if not value:
        raise AssertionError(message)


def main() -> int:
    root = Path(__file__).resolve().parent
    td = Path(tempfile.mkdtemp(prefix="arrnexus-v1081-validate-"))
    os.environ["DB_PATH"] = str(td / "router.db")
    os.environ["DB_DIR"] = str(td)
    os.environ["SESSION_SECRET"] = "v1081-validator"
    os.environ["ARRNEXUS_SELF_UPDATE"] = "0"

    from app import db
    db.init_db()

    # A running recovery job has no live owner after process restart.  It must
    # become terminal/retryable while retaining the last saved stage.
    jid = db.create_job("recover_import", [{"source_path": "/source/archive.rar", "display_name": "Archive", "destination_key": "auto"}])
    job, items = db.get_job(jid)
    db.update_job(jid, status="running", current_stage="importing", resume_stage="importing", current_operation="Importing")
    db.update_job_item(items[0]["id"], status="running", stage="importing", message="Import in progress")

    cancelling = db.create_job("archive_verify", [{"source_path": "/source/other.rar", "display_name": "Other"}])
    cjob, citems = db.get_job(cancelling)
    db.update_job(cancelling, status="cancelling", cancel_requested=1, current_stage="provider_verification")
    db.update_job_item(citems[0]["id"], status="running", stage="provider_verification")

    reconciled = db.reconcile_interrupted_jobs()
    job, items = db.get_job(jid)
    cjob, citems = db.get_job(cancelling)
    require(reconciled["failed"] >= 1 and reconciled["cancelled"] >= 1, "startup reconciliation did not classify stale jobs")
    require(job["status"] == "failed" and job["resume_stage"] == "importing", "interrupted recovery job did not become retryable at saved stage")
    require(items[0]["status"] == "error", "interrupted recovery item was left running")
    require(cjob["status"] == "cancelled" and citems[0]["status"] == "cancelled", "stale cancelling job did not terminalise")

    # The shared import path must translate the literal UI sentinel `auto` into
    # the concrete routing decision before root validation.
    from app import router_service as rs
    from app.scanner import ScanItem
    from app.routing import RouteDecision

    item = ScanItem(
        name="Example.Movie.2020.1080p.mkv", path="/source/example", media_type="movie",
        title_guess="Example Movie", year_guess=2020, video_count=1, season_numbers=[],
        size_bytes=1234, quality=1080, fingerprint="fingerprint",
    )

    class FakeClient:
        async def rescan(self, _ident):
            return None

    async def fake_route(_item):
        return {
            "decision": RouteDecision("kids", "/movies/kids", "validator route", 99),
            "existing": {"id": 7, "title": "Example Movie", "year": 2020, "path": "/movies/kids/Example Movie (2020)"},
            "existing_instance": None,
            "lookup": [],
        }

    rs.inspect_item = lambda _path: item
    rs.media_identity.apply_to_item = lambda value: (value, None)
    rs.route_item = fake_route
    rs.movie_roots = lambda: {"default": "/movies", "kids": "/movies/kids"}
    rs.client_for_destination = lambda _service, _key: (FakeClient(), None)
    rs.import_movie_source = lambda *_a, **_k: ["/movies/kids/Example Movie (2020)/Example Movie (2020).mkv"]
    rs.invalidate_library_cache = lambda: None
    rs.clear_language_block_states = lambda *_a, **_k: 0
    rs.log_import = lambda **_k: 1
    rs.add_activity = lambda *_a, **_k: None

    class Policy:
        enabled = False
    rs.load_language_policy = lambda: Policy()

    imported = asyncio.run(rs.import_one("/source/example", "auto"))
    require(imported["destination_key"] == "kids", "auto destination was not resolved to concrete route")
    require(imported["language_checks"] == "off", "advisory language result reporting regressed")

    # On POSIX, a child with a background descendant holding stdout open is a
    # useful regression test for process-group cancellation. Killing only the
    # shell would leave communicate() blocked until sleep exits.
    from app.process_control import run_cancellable, CancelledOperation
    if os.name == "posix":
        state = {"cancel": False}
        timer = threading.Timer(0.20, lambda: state.__setitem__("cancel", True))
        timer.start()
        started = time.monotonic()
        try:
            run_cancellable(
                ["sh", "-c", "sleep 20 & wait"], cancel_check=lambda: state["cancel"],
                terminate_timeout=0.35, poll_seconds=0.05,
            )
            raise AssertionError("cancellable process unexpectedly completed")
        except CancelledOperation:
            pass
        finally:
            timer.cancel()
        require(time.monotonic() - started < 4.0, "process-tree cancellation did not terminate descendants promptly")

    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    router_source = (root / "app" / "router_service.py").read_text(encoding="utf-8")
    db_source = (root / "app" / "db.py").read_text(encoding="utf-8")
    process_source = (root / "app" / "process_control.py").read_text(encoding="utf-8")
    job_ui = (root / "app" / "templates" / "job.html").read_text(encoding="utf-8")

    require('APP_VERSION = "10.8.1-beta"' in main_source, "v10.8.1 version marker missing")
    for token in (
        'requested_destination == "auto"', "resolved_destination", "requested_destination_key",
        "retryable = {\"failed\", \"error\", \"complete_with_errors\"}",
        "_finalize_job_cancel_after_grace", "reconcile_interrupted_jobs",
    ):
        require(token in main_source or token in router_source or token in db_source, f"v10.8.1 contract missing: {token}")
    for token in ("start_new_session", "os.killpg", "SIGTERM", "SIGKILL"):
        require(token in process_source, f"process-tree cancellation contract missing: {token}")
    require("complete_with_errors" in job_ui and "Retry Stage" in job_ui, "retry UI does not match retryable backend states")
    require("policy = load_language_policy()" in router_source, "post-import language reporting policy is still undefined")

    # Verify the retry endpoint accepts an older `error` representation and
    # resets stale cancellation state without launching real recovery I/O.
    from app import main as main_module
    from fastapi.testclient import TestClient
    with TestClient(main_module.app) as client:
        setup = client.post(
            "/setup",
            data={"username": "v1081validator", "email": "v1081@example.invalid", "display_name": "V10.8.1 Validator", "password": "validation-password-123", "confirm": "validation-password-123"},
            follow_redirects=False,
        )
        require(setup.status_code == 303, "administrator setup failed")
        retry_id = db.create_job("recover_import", [{"source_path": "/source/retry.rar", "display_name": "Retry", "destination_key": "auto"}])
        retry_job, retry_items = db.get_job(retry_id)
        db.update_job(retry_id, status="error", failed=1, cancel_requested=1, current_stage="importing", resume_stage="importing", finished_at="done")
        db.update_job_item(retry_items[0]["id"], status="error", stage="importing", message="old error representation")

        original_launch = main_module._launch
        def discard_launch(coro):
            try:
                coro.close()
            except Exception:
                pass
            return None
        main_module._launch = discard_launch
        try:
            response = client.post(f"/jobs/{retry_id}/retry", follow_redirects=False)
        finally:
            main_module._launch = original_launch
        require(response.status_code == 303, f"retry endpoint rejected compatible error state: {response.status_code}")
        retry_job, retry_items = db.get_job(retry_id)
        require(retry_job["status"] == "queued" and int(retry_job["cancel_requested"] or 0) == 0, "retry did not reset terminal/cancellation state")
        require(retry_items[0]["status"] == "queued" and retry_items[0]["stage"] == "importing", "retry did not preserve saved stage")

        health = client.get("/api/health")
        require(health.status_code == 200 and health.json().get("version") == "10.8.1-beta", "v10.8.1 health/version failed")

    print("ArrNexus v10.8.1-beta validation passed")
    print("auto route, retry compatibility, startup reconciliation and process-tree cancellation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
