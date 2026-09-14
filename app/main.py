from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import httpx
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .admin_tools import database_backup, diagnostics, write_diagnostics
from .arr import LidarrClient
from .config import settings
from .connections import SERVICES, get_connection, save_connection
from .db import (
    all_settings, authenticate_user, create_user, get_user, init_db, list_logs,
    log_event, pipeline_events, pipeline_history, pipeline_recent_events, setting_get, setting_set,
    update_user, user_count,
)
from . import lists as media_lists
from . import media_automation
from . import music
from . import pipeline
from . import orchestrator
from . import queue_janitor
from . import magic_intake
from . import mediastack
from . import stack_setup
from . import dashboard_boards
from . import zurg
from . import services

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
try:
    APP_VERSION = (BASE_DIR.parent / "VERSION").read_text(encoding="utf-8").strip() or "13.1.3"
except OSError:
    APP_VERSION = "13.1.3"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    tasks = [
        asyncio.create_task(zurg.status_loop(), name="zurg-status"),
        asyncio.create_task(zurg.index_loop(), name="zurg-index"),
        asyncio.create_task(zurg.storage_loop(), name="zurg-storage"),
        asyncio.create_task(services.status_loop(), name="service-health"),
        asyncio.create_task(pipeline.tracker_loop(), name="request-tracker"),
        asyncio.create_task(orchestrator.scan_loop(), name="missing-media-scan"),
        asyncio.create_task(orchestrator.dispatcher_loop(), name="missing-media-dispatcher"),
        asyncio.create_task(queue_janitor.scan_loop(), name="queue-janitor"),
        asyncio.create_task(magic_intake.scan_loop(), name="magic-intake"),
        asyncio.create_task(magic_intake.verification_loop(), name="magic-intake-verifier"),
        asyncio.create_task(mediastack.status_loop(), name="mediastack-status"),
        asyncio.create_task(mediastack.metadata_loop(), name="mediastack-metadata"),
        asyncio.create_task(mediastack.update_scheduler_loop(), name="mediastack-update-scheduler"),
        asyncio.create_task(media_lists.scheduler_loop(), name="media-list-scheduler"),
        asyncio.create_task(media_automation.scheduler_loop(), name="media-automation-scheduler"),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="ArrNexus", version=APP_VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _user(request: Request):
    uid = request.session.get("user_id")
    return get_user(int(uid)) if uid else None


def _flash(request: Request, message: str, kind: str = "info"):
    request.session["flash"] = {"message": str(message), "kind": kind}


def _pop_flash(request: Request):
    return request.session.pop("flash", None)


def _render(request: Request, template: str, **context):
    base = {
        "request": request,
        "user": _user(request),
        "flash": _pop_flash(request),
        "version": APP_VERSION,
    }
    base.update(context)
    return TEMPLATES.TemplateResponse(request=request, name=template, context=base)


def _go(path: str):
    return RedirectResponse(path, status_code=303)


def _require_user(request: Request):
    user = _user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    public = request.url.path.startswith("/static/") or request.url.path in {"/login", "/setup", "/api/health"}
    if user_count() == 0 and request.url.path not in {"/setup", "/api/health"} and not request.url.path.startswith("/static/"):
        return _go("/setup")
    if not public and not request.session.get("user_id"):
        return _go("/login")
    return await call_next(request)


# Session middleware is deliberately added after the auth gate so it wraps
# the gate and request.session is available inside it.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=os.getenv("ARRNEXUS_HTTPS_ONLY", "false").lower() in {"1", "true", "yes"},
    max_age=60 * 60 * 24 * 30,
)


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if user_count() > 0:
        return _go("/login")
    return _render(request, "setup.html")


@app.post("/setup")
async def setup_submit(request: Request, username: str = Form(...), display_name: str = Form(""), email: str = Form(""), password: str = Form(...), confirm_password: str = Form(...)):
    if user_count() > 0:
        return _go("/login")
    if len(password) < 8 or password != confirm_password:
        _flash(request, "Passwords must match and be at least 8 characters.", "error")
        return _go("/setup")
    try:
        uid = create_user(username, email, display_name, password)
        request.session["user_id"] = uid
        log_event("info", "auth", "setup", "Initial administrator created")
        if os.getenv("MEDIASTACK_GUIDED_SETUP", "false").lower() in {"1", "true", "yes"}:
            return _go("/stack-setup")
        return _go("/")
    except Exception as exc:
        _flash(request, str(exc), "error")
        return _go("/setup")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _user(request):
        return _go("/")
    return _render(request, "login.html")


@app.post("/login")
async def login_submit(request: Request, identity: str = Form(...), password: str = Form(...)):
    user = authenticate_user(identity, password)
    if not user:
        _flash(request, "Invalid username/email or password.", "error")
        return _go("/login")
    request.session["user_id"] = int(user["id"])
    return _go("/")


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return _go("/login")


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    return _render(request, "profile.html")


@app.post("/profile")
async def profile_save(request: Request, username: str = Form(...), display_name: str = Form(""), email: str = Form(""), password: str = Form(""), confirm_password: str = Form("")):
    user = _require_user(request)
    if password and (len(password) < 8 or password != confirm_password):
        _flash(request, "New passwords must match and be at least 8 characters.", "error")
        return _go("/profile")
    try:
        update_user(int(user["id"]), username, email, display_name, password)
        _flash(request, "Profile updated.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/profile")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if (
        os.getenv("MEDIASTACK_GUIDED_SETUP", "false").lower() in {"1", "true", "yes"}
        and not stack_setup.state().get("completed")
    ):
        return _go("/stack-setup")

    board_id = str(request.query_params.get("board") or dashboard_boards.home_board() or "overview")
    board = dashboard_boards.get_board(board_id) or dashboard_boards.get_board("overview")
    if board and board.get("id") != "overview":
        try:
            await mediastack.refresh_status()
        except Exception:
            pass
        zurg_status = zurg.cached_status()
        live = pipeline.cached_snapshot()
        runtime = await dashboard_boards.runtime(
            board,
            stack=mediastack.cached_snapshot(),
            live=live,
            orchestrator=orchestrator.cached_state(),
            janitor=queue_janitor.cached_state(),
            zurg=zurg_status,
        )
        return _render(request, "dashboard_board.html", **runtime)

    # The built-in Overview remains the original fast cache-only dashboard.
    zurg_status = zurg.cached_status()
    live = pipeline.cached_snapshot()
    return _render(
        request,
        "dashboard.html",
        zurg=zurg_status,
        zurg_error=zurg_status.get("cache_error", ""),
        services=services.cached_status_rows(),
        service_state=services.cache_state(),
        live=live,
        live_error=" · ".join(live.get("errors") or []),
        tracker=pipeline.tracker_state(),
        orchestrator=orchestrator.cached_state(),
        janitor=queue_janitor.cached_state(),
    )


@app.get("/dashboard/boards", response_class=HTMLResponse)
async def dashboard_boards_page(request: Request):
    state = dashboard_boards.board_api()
    return _render(
        request,
        "dashboard_boards.html",
        boards=state["boards"],
        home_board=state["home_board"],
        widgets=state["widgets"],
        public_domain=state["public_domain"],
        service_links=state["service_links"],
        spotify_public_url=state["spotify_public_url"],
    )


@app.get("/api/dashboard/boards")
async def dashboard_boards_api(request: Request):
    _require_user(request)
    return dashboard_boards.board_api()


@app.get("/api/dashboard/boards/{board_id}/runtime")
async def dashboard_board_runtime_api(request: Request, board_id: str):
    _require_user(request)
    board = dashboard_boards.get_board(board_id)
    if not board or board.get("id") == "overview":
        raise HTTPException(404, "Dashboard board not found")
    try:
        await mediastack.refresh_status()
    except Exception:
        pass
    state = await dashboard_boards.runtime(
        board,
        stack=mediastack.cached_snapshot(),
        live=pipeline.cached_snapshot(),
        orchestrator=orchestrator.cached_state(),
        janitor=queue_janitor.cached_state(),
        zurg=zurg.cached_status(),
    )
    return {
        "ok": True,
        "metrics": state.get("metrics") or {},
        "services": state.get("services") or [],
        "updates": state.get("updates") or [],
    }


@app.post("/api/dashboard/boards/{board_id}/layout")
async def dashboard_board_layout_api(request: Request, board_id: str):
    _require_user(request)
    payload = await request.json()
    try:
        board = dashboard_boards.save_layout(board_id, payload if isinstance(payload, dict) else {})
        return {"ok": True, "board": board}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/dashboard/boards/{board_id}/items")
async def dashboard_board_add_item_api(request: Request, board_id: str):
    _require_user(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(400, "JSON object required")
    try:
        item = dashboard_boards.add_item(
            board_id,
            str(payload.get("kind") or ""),
            payload.get("options") if isinstance(payload.get("options"), dict) else {},
        )
        return {"ok": True, "item": item}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/dashboard/boards/{board_id}/items/{item_id}/remove")
async def dashboard_board_remove_item_api(request: Request, board_id: str, item_id: str):
    _require_user(request)
    try:
        dashboard_boards.remove_item(board_id, item_id)
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/dashboard/jellyfin/image/{item_id}")
async def dashboard_jellyfin_image(request: Request, item_id: str):
    _require_user(request)
    conn = get_connection("jellyfin")
    if not conn.url or not conn.api_key:
        raise HTTPException(404, "Jellyfin is not configured")
    if not item_id or len(item_id) > 128:
        raise HTTPException(400, "Invalid Jellyfin item id")
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(
                conn.url.rstrip("/") + f"/Items/{item_id}/Images/Primary",
                params={"maxWidth": 420, "quality": 86},
                headers={"Authorization": f'MediaBrowser Token="{conn.api_key}"'},
            )
        if response.status_code >= 400 or not response.content:
            raise HTTPException(404, "Jellyfin artwork unavailable")
        return Response(
            content=response.content,
            media_type=response.headers.get("content-type", "image/jpeg"),
            headers={"Cache-Control": "private, max-age=21600"},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Jellyfin artwork proxy failed: {exc}")


@app.post("/dashboard/boards/settings")
async def dashboard_boards_settings(request: Request):
    _require_user(request)
    form = await request.form()
    domain = dashboard_boards.set_public_domain(str(form.get("public_domain") or ""))
    for key in dashboard_boards.SERVICE_KEYS:
        value = str(form.get(f"url_{key}") or "").strip()
        dashboard_boards.set_service_url(key, value)
    if domain:
        _flash(request, f"Public service links saved for {domain}. ArrNexus HTTPS public URL is ready for Spotify.", "success")
    else:
        _flash(request, "Dashboard public links saved.", "success")
    return _go("/dashboard/boards")



@app.post("/dashboard/boards/appearance")
async def dashboard_board_appearance(request: Request):
    _require_user(request)
    form = await request.form()
    board_id = str(form.get("board_id") or "")
    try:
        dashboard_boards.save_appearance(board_id, {
            "accent": str(form.get("accent") or "#8b5cf6"),
            "columns": str(form.get("columns") or "12"),
            "row_height": str(form.get("row_height") or "74"),
            "panel_opacity": str(form.get("panel_opacity") or "0.82"),
            "blur": str(form.get("blur") or "18"),
            "radius": str(form.get("radius") or "18"),
            "background": str(form.get("background") or ""),
        })
        _flash(request, "Dashboard appearance saved.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/dashboard/boards")


@app.post("/dashboard/boards/save")
async def dashboard_board_save(request: Request):
    _require_user(request)
    form = await request.form()
    try:
        board = dashboard_boards.save_board(
            str(form.get("board_id") or ""),
            str(form.get("name") or ""),
            [str(x) for x in form.getlist("widgets")],
            str(form.get("description") or ""),
        )
        _flash(request, f"Board {board['name']} saved.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/dashboard/boards")


@app.post("/dashboard/boards/delete")
async def dashboard_board_delete(request: Request, board_id: str = Form(...)):
    _require_user(request)
    try:
        dashboard_boards.delete_board(board_id)
        _flash(request, "Dashboard board deleted.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/dashboard/boards")


@app.post("/dashboard/boards/home")
async def dashboard_board_home(request: Request, board_id: str = Form(...)):
    _require_user(request)
    try:
        dashboard_boards.set_home_board(board_id)
        _flash(request, "Home dashboard updated.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/dashboard/boards")


@app.get("/pipeline", response_class=HTMLResponse)
async def pipeline_page(request: Request):
    live = pipeline.cached_snapshot()
    return _render(
        request, "pipeline.html", live=live, history=pipeline_history(120),
        events=pipeline_recent_events(160), tracker=pipeline.tracker_state(),
        error=" · ".join(live.get("errors") or []),
    )


@app.get("/api/pipeline/live")
async def pipeline_api(request: Request):
    _require_user(request)
    # Return the already-built snapshot immediately; the background tracker
    # refreshes every few seconds.
    return pipeline.cached_snapshot()


@app.get("/api/pipeline/{item_key:path}/events")
async def pipeline_events_api(request: Request, item_key: str):
    _require_user(request)
    return {"ok": True, "events": pipeline_events(item_key)}


@app.get("/zurg", response_class=HTMLResponse)
async def zurg_page(request: Request):
    status = zurg.cached_status()
    recent = await asyncio.to_thread(zurg.recent_entries, 80)
    return _render(request, "zurg.html", zurg=status, recent=recent, index=zurg.index_state())


@app.get("/api/zurg/status")
async def zurg_api(request: Request):
    _require_user(request)
    return zurg.cached_status()


@app.get("/arr-services", response_class=HTMLResponse)
async def arr_services_page(request: Request):
    rows = await services.status_rows(timeout=7.0)
    return _render(request, "arr_services.html", services=rows, service_names=SERVICES)


@app.post("/arr-services/{service}")
async def arr_service_save(request: Request, service: str, url: str = Form(...), api_key: str = Form("")):
    if service not in SERVICES:
        raise HTTPException(404)
    old = get_connection(service)
    save_connection(service, url, api_key or ("********" if old.api_key else ""))
    _flash(request, f"{service.title()} connection saved.", "success")
    return _go("/arr-services")


@app.get("/api/services")
async def services_api(request: Request):
    _require_user(request)
    return {"ok": True, "services": services.cached_status_rows(), "state": services.cache_state()}



@app.get("/missing-media", response_class=HTMLResponse)
async def missing_media_page(request: Request):
    state = orchestrator.cached_state()
    return _render(request, "missing_media.html", state=state, cfg=state["settings"])


@app.post("/missing-media/settings")
async def missing_media_settings(
    request: Request,
    enabled: bool = Form(False), dry_run: bool = Form(False), radarr: bool = Form(False), sonarr: bool = Form(False), lidarr: bool = Form(False),
    batch_size: int = Form(3), delay_seconds: int = Form(30), max_active: int = Form(5), daily_limit: int = Form(50),
    max_search_attempts: int = Form(5), max_failures: int = Form(3), cooldown_hours: float = Form(6), search_observe_minutes: int = Form(10),
    priority: str = Form("least_attempts"), neutarr_coexist: bool = Form(False),
):
    orchestrator.save_settings(locals())
    _flash(request, "Missing Media Orchestrator settings saved.", "success")
    return _go("/missing-media")


@app.post("/missing-media/scan")
async def missing_media_scan(request: Request):
    try:
        await orchestrator.refresh()
        _flash(request, "Missing-media scan refreshed.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/missing-media")


@app.post("/missing-media/dispatch")
async def missing_media_dispatch(request: Request):
    try:
        result = await orchestrator.dispatch_once(manual=True)
        _flash(request, result.get("detail") or result.get("action") or "Dispatch complete.", "success" if result.get("ok") else "error")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/missing-media")


@app.post("/missing-media/item/search")
async def missing_media_item_search(request: Request, item_key: str = Form(...)):
    try:
        result = await orchestrator.dispatch_once(item_key, manual=True)
        _flash(request, result.get("detail") or "Search action complete.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/missing-media")


@app.post("/missing-media/item/pause")
async def missing_media_item_pause(request: Request, item_key: str = Form(...)):
    orchestrator.pause_item(item_key)
    _flash(request, "Item paused and moved to Needs Attention.", "success")
    return _go("/missing-media")


@app.post("/missing-media/item/resume")
async def missing_media_item_resume(request: Request, item_key: str = Form(...)):
    orchestrator.resume_item(item_key)
    _flash(request, "Item resumed.", "success")
    return _go("/missing-media")


@app.get("/api/missing-media")
async def missing_media_api(request: Request):
    _require_user(request)
    return orchestrator.cached_state()


@app.get("/queue-janitor", response_class=HTMLResponse)
async def queue_janitor_page(request: Request):
    state = queue_janitor.cached_state()
    return _render(request, "queue_janitor.html", state=state, cfg=state["settings"])


@app.post("/queue-janitor/settings")
async def queue_janitor_settings(
    request: Request,
    enabled: bool = Form(False), dry_run: bool = Form(False), radarr: bool = Form(False), sonarr: bool = Form(False), lidarr: bool = Form(False),
    auto_import: bool = Form(False), cleanup_bad: bool = Form(False), search_after_cleanup: bool = Form(False), swaparr_defer_stalled: bool = Form(False),
    interval_seconds: int = Form(30), warning_grace_minutes: int = Form(5), max_failures: int = Form(3), cooldown_hours: float = Form(6),
    min_movie_seconds: int = Form(300), min_episode_seconds: int = Form(60), min_music_seconds: int = Form(20),
):
    queue_janitor.save_settings(locals())
    _flash(request, "Queue Janitor settings saved.", "success")
    return _go("/queue-janitor")


@app.post("/queue-janitor/scan")
async def queue_janitor_scan(request: Request):
    try:
        await queue_janitor.refresh(run_actions=False)
        _flash(request, "Queue scan refreshed without making changes.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/queue-janitor")


@app.post("/queue-janitor/run")
async def queue_janitor_run(request: Request):
    try:
        await queue_janitor.refresh(run_actions=True, allow_disabled=True)
        cfg = queue_janitor.settings_state()
        _flash(request, "Dry-run evaluation complete." if cfg["dry_run"] else "Queue Janitor cycle complete.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/queue-janitor")


@app.post("/queue-janitor/action")
async def queue_janitor_action(request: Request, service: str = Form(...), queue_id: str = Form(...)):
    try:
        await queue_janitor.refresh(run_actions=False)
        item = queue_janitor.get_cached_item(service, queue_id)
        if not item:
            raise ValueError("Queue item is no longer present")
        result = await queue_janitor.execute(item, force=True)
        _flash(request, result.get("detail") or result.get("outcome") or "Action complete.", "success" if result.get("outcome") not in {"error", "attention"} else "info")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/queue-janitor")


@app.post("/queue-janitor/attention/resume")
async def queue_janitor_resume(request: Request, media_key: str = Form(...)):
    queue_janitor.resume_media(media_key)
    _flash(request, "Recovery counter resumed. The next scan can retry it.", "success")
    return _go("/queue-janitor")


@app.get("/api/queue-janitor")
async def queue_janitor_api(request: Request):
    _require_user(request)
    return queue_janitor.cached_state()



@app.get("/magic-intake", response_class=HTMLResponse)
async def magic_intake_page(request: Request):
    state = magic_intake.cached_state()
    page = magic_intake.query_groups(limit=magic_intake.DEFAULT_PAGE_SIZE)
    return _render(
        request, "magic_intake.html", state=state, groups=page["rows"],
        recent_imported=[g for g in (state.get("groups") or []) if g.get("state") == "imported"][:20],
        total_visible=page["total"], has_more=page["has_more"], page_limit=page["limit"],
        cfg=state["settings"], filters=state.get("filters") or {"genres": [], "themes": []},
    )


@app.post("/magic-intake/settings")
async def magic_intake_settings(
    request: Request, enabled: bool = Form(False), interval_seconds: int = Form(60), auto_match_threshold: int = Form(95),
    verify_window_minutes: int = Form(10), verify_interval_seconds: int = Form(30),
    trusted_dmm_bypass: bool = Form(False),
    magic_root: str = Form(""), arr_prefix: str = Form(""),
):
    magic_intake.save_settings(locals())
    _flash(request, "Magic Intake settings saved.", "success")
    return _go("/magic-intake")


@app.post("/magic-intake/scan")
async def magic_intake_scan(request: Request):
    try:
        result = magic_intake.request_scan()
        _flash(request, result.get("detail") or "Magic Intake scan queued.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/magic-intake")


@app.post("/magic-intake/force-match")
async def magic_intake_force_match(request: Request, group_key: str = Form(...), candidate_json: str = Form(...)):
    try:
        candidate = json.loads(candidate_json)
        magic_intake.force_match(group_key, candidate)
        asyncio.create_task(magic_intake.scan(), name="magic-regroup-after-force-match-form")
        _flash(request, f"Force matched to {candidate.get('title') or 'selected item'}.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/magic-intake")


@app.post("/magic-intake/import")
async def magic_intake_import(request: Request, group_key: str = Form(...), destination_key: str = Form(...), selected_source: str = Form("")):
    try:
        result = magic_intake.enqueue_import(group_key, destination_key, selected_source)
        _flash(request, result.get("detail") or "Magic Intake import queued.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/magic-intake")


@app.post("/magic-intake/ignore")
async def magic_intake_ignore(request: Request, group_key: str = Form(...)):
    magic_intake.ignore(group_key)
    _flash(request, "Magic Intake group ignored.", "success")
    return _go("/magic-intake")


@app.get("/api/magic-intake")
async def magic_intake_api(request: Request):
    _require_user(request)
    return magic_intake.lightweight_state()


@app.get("/api/magic-intake/groups")
async def magic_intake_groups_api(
    request: Request, media_type: str = "all", genre: str = "all", theme: str = "all",
    source: str = "all", state: str = "all", q: str = "", offset: int = 0, limit: int = 72,
):
    _require_user(request)
    return magic_intake.query_groups(
        media_type=media_type, genre=genre, theme=theme, source=source, state=state, q=q, offset=offset, limit=limit,
    )


@app.post("/api/magic-intake/scan")
async def magic_intake_scan_api(request: Request):
    _require_user(request)
    return magic_intake.request_scan()


@app.get("/api/magic-intake/lookup")
async def magic_intake_lookup_api(request: Request, media_type: str, q: str):
    _require_user(request)
    if media_type not in {"movie", "tv", "music"}:
        raise HTTPException(400, "Unknown media type")
    return {"ok": True, "results": await magic_intake.lookup(media_type, q)}


@app.post("/api/magic-intake/force-match")
async def magic_intake_force_match_api(request: Request):
    _require_user(request)

    payload = await request.json()
    group_key = str(payload.get("group_key") or "")
    candidate = payload.get("candidate") or {}

    if not group_key or not isinstance(candidate, dict):
        raise HTTPException(
            400,
            "group_key and candidate are required",
        )

    try:
        group = magic_intake.force_match(
            group_key,
            candidate,
        )

        inspection = await magic_intake.inspect_group(
            group_key,
            manual=True,
        )

        return {
            "ok": True,
            "group": group,
            "inspection": inspection,
        }

    except Exception as exc:
        raise HTTPException(
            400,
            str(exc) or repr(exc),
        )

@app.post("/api/magic-intake/type")
async def magic_intake_type_api(request: Request):
    _require_user(request)
    payload = await request.json()
    group_key = str(payload.get("group_key") or "")
    media_type = str(payload.get("media_type") or "")
    if not group_key or media_type not in {"movie", "tv", "music"}:
        raise HTTPException(400, "group_key and valid media_type are required")
    try:
        result = magic_intake.set_media_type(group_key, media_type)
        scan_result = magic_intake.request_scan()
        result["scan"] = scan_result
        return result
    except Exception as exc:
        raise HTTPException(400, str(exc) or repr(exc))


@app.post("/api/magic-intake/import")
async def magic_intake_import_api(request: Request):
    _require_user(request)
    payload = await request.json()
    try:
        result = magic_intake.enqueue_import(
            str(payload.get("group_key") or ""),
            str(payload.get("destination_key") or ""),
            str(payload.get("selected_source") or ""),
        )
        return result
    except Exception as exc:
        raise HTTPException(400, str(exc))




@app.post("/api/magic-intake/recheck")
async def magic_intake_recheck_api(request: Request):
    _require_user(request)
    payload = await request.json()
    group_key = str(payload.get("group_key") or "")
    if not group_key:
        raise HTTPException(400, "group_key is required")
    try:
        return magic_intake.request_recheck(group_key)
    except Exception as exc:
        raise HTTPException(400, str(exc))



@app.post("/api/magic-intake/inspect")
async def magic_intake_inspect_api(request: Request):
    _require_user(request)

    payload = await request.json()
    group_key = str(payload.get("group_key") or "")

    if not group_key:
        raise HTTPException(
            400,
            "group_key is required",
        )

    try:
        return await magic_intake.inspect_group(
            group_key,
            manual=True,
        )
    except Exception as exc:
        raise HTTPException(
            400,
            str(exc) or repr(exc),
        )


@app.post("/api/magic-intake/bulk-recheck")
async def magic_intake_bulk_recheck_api(request: Request):
    _require_user(request)

    payload = await request.json()
    group_keys = payload.get("group_keys") or []

    if not isinstance(group_keys, list):
        raise HTTPException(
            400,
            "group_keys must be a list",
        )

    try:
        return magic_intake.request_bulk_inspect(
            group_keys,
        )
    except Exception as exc:
        raise HTTPException(
            400,
            str(exc) or repr(exc),
        )


@app.post("/api/magic-intake/bulk-clear")
async def magic_intake_bulk_clear_api(request: Request):
    _require_user(request)

    payload = await request.json()
    group_keys = payload.get("group_keys") or []

    if not isinstance(group_keys, list):
        raise HTTPException(
            400,
            "group_keys must be a list",
        )

    try:
        return magic_intake.clear_groups(
            group_keys,
        )
    except Exception as exc:
        raise HTTPException(
            400,
            str(exc) or repr(exc),
        )


@app.get("/lists", response_class=HTMLResponse)
async def lists_page(request: Request):
    return _render(
        request, "lists.html",
        definitions=media_lists.list_definitions(),
        runs=media_lists.list_runs(limit=30),
        providers=media_lists.provider_state(),
        source_types=media_lists.SOURCE_TYPES,
        media_types={"mixed": "Mixed", "movie": "Movies", "tv": "TV"},
    )


@app.post("/lists/save")
async def lists_save(
    request: Request,
    list_id: int | None = Form(None), name: str = Form(...), source_type: str = Form(...), source_ref: str = Form(""),
    media_type: str = Form("mixed"), movie_destination: str = Form("auto"), tv_destination: str = Form("auto"),
    monitor: bool = Form(False), search_automatically: bool = Form(False), enabled: bool = Form(False), sync_interval_hours: int = Form(12),
):
    try:
        media_lists.save_definition(
            list_id=list_id, name=name, source_type=source_type, source_ref=source_ref, media_type=media_type,
            movie_destination=movie_destination, tv_destination=tv_destination, acquisition_strategy="arr_native",
            monitor=monitor, search_automatically=search_automatically, enabled=enabled, sync_interval_hours=sync_interval_hours,
        )
        _flash(request, "List saved.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/lists")


@app.post("/lists/{list_id}/delete")
async def lists_delete(request: Request, list_id: int):
    media_lists.delete_definition(list_id)
    _flash(request, "List removed.", "success")
    return _go("/lists")


@app.post("/lists/{list_id}/preview")
async def lists_preview(request: Request, list_id: int):
    defn = media_lists.get_definition(list_id)
    if not defn:
        raise HTTPException(404)
    try:
        result = await media_lists.preview_definition(defn)
        request.session["list_result"] = {"name": defn["name"], "mode": "Preview", "result": result}
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/lists")


@app.post("/lists/{list_id}/sync")
async def lists_sync(request: Request, list_id: int):
    defn = media_lists.get_definition(list_id)
    if not defn:
        raise HTTPException(404)
    try:
        result = await media_lists.sync_definition(defn, preview=False, user_id=int(_require_user(request)["id"]))
        request.session["list_result"] = {"name": defn["name"], "mode": "Sync", "result": result}
        _flash(request, f"{defn['name']} synced.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/lists")


@app.get("/api/lists/last-result")
async def list_last_result(request: Request):
    _require_user(request)
    return request.session.pop("list_result", {"result": None})


@app.post("/lists/providers")
async def list_provider_save(request: Request, trakt_client_id: str = Form(""), trakt_client_secret: str = Form(""), tmdb_api_key: str = Form(""), simkl_client_id: str = Form(""), simkl_access_token: str = Form("")):
    media_lists.save_trakt_app(trakt_client_id, trakt_client_secret)
    media_lists.save_tmdb(tmdb_api_key)
    media_lists.save_simkl(simkl_client_id, simkl_access_token)
    _flash(request, "List provider settings saved.", "success")
    return _go("/lists")


@app.post("/lists/trakt/connect")
async def trakt_connect(request: Request):
    try:
        result = await media_lists.trakt_device_begin()
        _flash(request, f"Open {result.get('verification_url') or 'https://trakt.tv/activate'} and enter code {result.get('user_code')}", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/lists")


@app.post("/lists/trakt/poll")
async def trakt_poll(request: Request):
    try:
        result = await media_lists.trakt_device_poll()
        _flash(request, "Trakt connected." if result.get("connected") else str(result.get("message") or "Waiting for Trakt authorization."), "success" if result.get("connected") else "info")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/lists")


@app.post("/lists/trakt/disconnect")
async def trakt_disconnect(request: Request):
    media_lists.trakt_disconnect()
    _flash(request, "Trakt disconnected.", "success")
    return _go("/lists")


@app.get("/music", response_class=HTMLResponse)
async def music_page(request: Request, q: str = ""):
    user = _require_user(request)
    uid = int(user["id"])
    try:
        spotify_hub = await asyncio.wait_for(music.spotify_user_hub(uid), timeout=10) if music.spotify_user_linked(uid) else {"linked": False}
    except Exception as exc:
        spotify_hub = {"linked": True, "profile": {}, "errors": [str(exc)]}
    try:
        spotify_results = await asyncio.wait_for(music.spotify_search(q, "album", 15), timeout=10) if q else []
    except Exception:
        spotify_results = []
    lidarr_results = []
    if q:
        try:
            lidarr_results = (await asyncio.wait_for(LidarrClient().artist_lookup(q), timeout=10))[:15]
        except Exception:
            lidarr_results = []
    return _render(
        request, "music.html", q=q, spotify=spotify_hub, spotify_results=spotify_results,
        lidarr_results=lidarr_results, beatport_url=music.beatport_search_url(q) if q else "https://www.beatport.com/",
        spotify_app_configured=music.spotify_app_configured(), spotify_linked=music.spotify_user_linked(uid),
    )


@app.get("/music/settings", response_class=HTMLResponse)
async def music_settings_page(request: Request):
    stored_public_url = setting_get("app.public_url", "") or settings.public_url
    redirect_uri = music.spotify_redirect_uri(stored_public_url, str(request.url))
    redirect_ok, redirect_message = music.spotify_redirect_validation(redirect_uri)
    public_url = redirect_uri.removesuffix("/music/spotify/callback") if redirect_uri else stored_public_url
    return _render(
        request, "music_settings.html", client_id=setting_get("music.spotify.client_id"),
        market=setting_get("music.spotify.market", "GB"), configured=music.spotify_app_configured(),
        public_url=public_url, redirect_uri=redirect_uri, redirect_ok=redirect_ok, redirect_message=redirect_message,
    )


@app.post("/music/settings")
async def music_settings_save(request: Request, client_id: str = Form(""), client_secret: str = Form(""), market: str = Form("GB"), public_url: str = Form("")):
    setting_set("music.spotify.client_id", client_id.strip())
    if client_secret:
        setting_set("music.spotify.client_secret", client_secret.strip(), True)
    setting_set("music.spotify.market", (market or "GB").strip().upper()[:2])
    setting_set("app.public_url", public_url.strip().rstrip("/"))
    redirect_uri = music.spotify_redirect_uri(public_url.strip(), str(request.url))
    ok, message = music.spotify_redirect_validation(redirect_uri)
    _flash(request, "Spotify settings saved. Redirect URI is ready." if ok else f"Spotify settings saved. {message}", "success" if ok else "info")
    return _go("/music/settings")



@app.post("/music/spotify/test")
async def spotify_test(request: Request):
    _require_user(request)
    result = await music.spotify_app_diagnostics()
    stored_public_url = setting_get("app.public_url", "") or settings.public_url
    redirect_uri = music.spotify_redirect_uri(stored_public_url, str(request.url))
    redirect_ok, redirect_message = music.spotify_redirect_validation(redirect_uri)
    if result.get("ok") and redirect_ok:
        _flash(request, f"Spotify credentials are valid. OAuth callback ready: {redirect_uri}", "success")
    elif not result.get("ok"):
        _flash(request, f"Spotify credentials test failed: {result.get('message') or result.get('status')}", "error")
    else:
        _flash(request, f"Spotify credentials are valid, but OAuth needs attention: {redirect_message}", "error")
    return _go("/music/settings")


@app.get("/music/spotify/connect")
async def spotify_connect(request: Request):
    user = _require_user(request)
    public_url = setting_get("app.public_url", "") or settings.public_url
    redirect_uri = music.spotify_redirect_uri(public_url, str(request.url))
    redirect_ok, redirect_message = music.spotify_redirect_validation(redirect_uri)
    if not redirect_ok:
        _flash(request, f"Spotify cannot be linked yet. {redirect_message} Redirect URI: {redirect_uri or 'not configured'}", "error")
        return _go("/music/settings")

    # OAuth state is deliberately tied to the browser session that starts the
    # flow. Starting from a LAN/test hostname and returning through the public
    # Cloudflare hostname creates a different browser cookie/session and makes
    # the state check fail. Refuse that unsafe/ambiguous flow up front and tell
    # the user which hostname to open instead.
    redirect_host = (urlparse(redirect_uri).hostname or "").lower()
    forwarded_host = str(request.headers.get("x-forwarded-host") or "").split(",", 1)[0].strip()
    current_netloc = forwarded_host or request.url.netloc
    try:
        current_host = (urlparse("//" + current_netloc).hostname or "").lower()
    except Exception:
        current_host = (request.url.hostname or "").lower()
    if redirect_host and current_host and redirect_host != current_host:
        public_base = redirect_uri.removesuffix("/music/spotify/callback")
        _flash(
            request,
            f"Open ArrNexus at {public_base} and press Link Spotify there. "
            f"This browser session is on {current_netloc}, but Spotify returns to {redirect_host}; "
            "using two different ArrNexus origins would fail the OAuth state check.",
            "error",
        )
        return _go("/music/settings")

    state = secrets.token_urlsafe(24)
    request.session["spotify_state"] = state
    request.session["spotify_redirect_uri"] = redirect_uri
    try:
        return RedirectResponse(music.spotify_authorize_url(int(user["id"]), state, redirect_uri), status_code=302)
    except Exception as exc:
        _flash(request, str(exc), "error")
        return _go("/music/settings")


@app.get("/music/spotify/callback", name="spotify_callback")
async def spotify_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    user = _require_user(request)
    expected_state = request.session.pop("spotify_state", None)
    redirect_uri = request.session.pop("spotify_redirect_uri", "")
    if error or not code or state != expected_state:
        if error:
            message = error
        elif state != expected_state:
            message = (
                "Spotify authorization state did not match. Start Link Spotify from the same HTTPS "
                "ArrNexus hostname used by the callback; do not begin OAuth from a LAN IP/test origin "
                "and return through a different ArrNexus instance."
            )
        else:
            message = "Spotify authorization did not return an authorization code."
        _flash(request, message, "error")
        return _go("/music")
    try:
        if not redirect_uri:
            public_url = setting_get("app.public_url", "") or settings.public_url
            redirect_uri = music.spotify_redirect_uri(public_url, str(request.url))
        await music.spotify_exchange_code(int(user["id"]), code, redirect_uri)
        _flash(request, "Spotify linked.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/music")


@app.post("/music/spotify/disconnect")
async def spotify_disconnect(request: Request):
    music.spotify_disconnect_user(int(_require_user(request)["id"]))
    _flash(request, "Spotify disconnected.", "success")
    return _go("/music")


@app.post("/music/lidarr/add")
async def music_lidarr_add(request: Request, foreign_artist_id: str = Form(...), search_term: str = Form("")):
    client = LidarrClient()
    try:
        rows = await client.artist_lookup(foreign_artist_id or search_term)
        candidate = next((x for x in rows if str(x.get("foreignArtistId") or "") == foreign_artist_id), rows[0] if rows else None)
        if not candidate:
            raise ValueError("Artist was not found in Lidarr lookup")
        roots = await client.roots()
        if not roots:
            raise ValueError("Lidarr has no root folder configured")
        root = max(roots, key=lambda x: int(x.get("freeSpace") or 0)).get("path")
        await client.add_artist(candidate, root, search=True)
        _flash(request, f"Added {candidate.get('artistName') or candidate.get('name') or 'artist'} to Lidarr.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/music?q=" + quote_plus(search_term or foreign_artist_id))


@app.get("/automation", response_class=HTMLResponse)
async def automation_page(request: Request):
    libraries = []
    try:
        from .jellyfin import JellyfinClient
        libraries = await asyncio.wait_for(JellyfinClient().libraries(), timeout=10)
    except Exception:
        pass
    return _render(
        request, "automation.html", definitions=media_automation.list_definitions(),
        source_types=media_automation.SOURCE_TYPES, presets=media_automation.PRESETS, libraries=libraries,
    )


@app.post("/automation/save")
async def automation_save(
    request: Request, automation_id: int | None = Form(None), name: str = Form(...), media_type: str = Form("mixed"), source_type: str = Form("manual"),
    source_ref: str = Form(""), schedule_hours: int = Form(24), enabled: bool = Form(False), library_name: str = Form(""), collection_name: str = Form(""),
    summary: str = Form(""), manual_items: str = Form(""),
):
    try:
        media_automation.save_definition(
            automation_id=automation_id, name=name, media_type=media_type, source_type=source_type, source_ref=source_ref,
            schedule_hours=schedule_hours, enabled=enabled, library_name=library_name, collection_name=collection_name,
            summary=summary, manual_items=manual_items,
        )
        _flash(request, "Media automation saved.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/automation")


@app.post("/automation/{automation_id}/delete")
async def automation_delete(request: Request, automation_id: int):
    media_automation.delete_definition(automation_id)
    _flash(request, "Media automation removed.", "success")
    return _go("/automation")


@app.post("/automation/{automation_id}/preview")
async def automation_preview(request: Request, automation_id: int):
    defn = media_automation.get_definition(automation_id)
    if not defn:
        raise HTTPException(404)
    try:
        request.session["automation_result"] = {"name": defn["name"], "mode": "Preview", "result": await media_automation.preview(defn)}
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/automation")


@app.post("/automation/{automation_id}/sync")
async def automation_sync(request: Request, automation_id: int):
    defn = media_automation.get_definition(automation_id)
    if not defn:
        raise HTTPException(404)
    try:
        request.session["automation_result"] = {"name": defn["name"], "mode": "Sync", "result": await media_automation.sync(defn)}
        _flash(request, f"{defn['name']} synced to Jellyfin.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/automation")


@app.get("/api/automation/last-result")
async def automation_last_result(request: Request):
    _require_user(request)
    return request.session.pop("automation_result", {"result": None})


@app.get("/automation/{automation_id}/kometa.yml")
async def automation_export(request: Request, automation_id: int):
    _require_user(request)
    try:
        return PlainTextResponse(await media_automation.export_kometa(automation_id), media_type="text/yaml", headers={"Content-Disposition": f'attachment; filename="arrnexus-collection-{automation_id}.yml"'})
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/automation/kometa/import")
async def automation_import(request: Request, library_name: str = Form(""), yaml_text: str = Form(""), yaml_file: UploadFile | None = File(None)):
    try:
        text = yaml_text
        if yaml_file and yaml_file.filename:
            text = (await yaml_file.read()).decode("utf-8", errors="replace")
        created = media_automation.import_kometa_yaml(text, library_name)
        _flash(request, f"Imported {len(created)} Kometa collection definition(s).", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/automation")


@app.get("/mediastack", response_class=HTMLResponse)
async def mediastack_page(request: Request):
    try:
        await mediastack.refresh_status()
        await mediastack.refresh_jobs()
    except Exception:
        pass
    return _render(request, "mediastack.html", stack=mediastack.cached_snapshot())


@app.get("/api/mediastack")
async def mediastack_api(request: Request):
    _require_user(request)
    return mediastack.cached_snapshot()


@app.post("/api/mediastack/refresh")
async def mediastack_refresh_api(request: Request):
    _require_user(request)
    await mediastack.refresh_status()
    await mediastack.refresh_jobs()
    return mediastack.cached_snapshot()


@app.post("/api/mediastack/check-updates")
async def mediastack_update_check_api(request: Request):
    _require_user(request)
    await mediastack.refresh_updates()
    return mediastack.cached_snapshot()


@app.post("/api/mediastack/refresh-configs")
async def mediastack_config_refresh_api(request: Request):
    _require_user(request)
    await mediastack.refresh_configs()
    return mediastack.cached_snapshot().get("configs") or {}


@app.get("/api/mediastack/logs/{name}")
async def mediastack_logs_api(request: Request, name: str, tail: int = 250):
    _require_user(request)
    try:
        return await mediastack.logs(name, tail)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.get("/api/mediastack/config")
async def mediastack_config_api(request: Request, root: str, path: str):
    _require_user(request)
    try:
        return await mediastack.config_file(root, path)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.get("/api/mediastack/discovery")
async def mediastack_discovery_api(request: Request):
    _require_user(request)
    try:
        return await mediastack.discovery()
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.get("/api/mediastack/update-plan/{name}")
async def mediastack_update_plan_api(request: Request, name: str):
    _require_user(request)
    try:
        return await mediastack.update_plan(name)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.post("/api/mediastack/actions/{name}/{action}")
async def mediastack_action_api(request: Request, name: str, action: str):
    _require_user(request)
    if action == "update":
        try:
            return await mediastack.start_update(name)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:1000] if exc.response is not None else str(exc)
            raise HTTPException(exc.response.status_code if exc.response is not None else 502, detail)
        except Exception as exc:
            raise HTTPException(502, str(exc))
    if action not in {"start", "stop", "restart"}:
        raise HTTPException(400, "Unsupported MediaStack action")
    try:
        return await mediastack.lifecycle(name, action)
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:1000] if exc.response is not None else str(exc)
        raise HTTPException(exc.response.status_code if exc.response is not None else 502, detail)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.post("/api/mediastack/actions/{name}/update")
async def mediastack_start_update_api(request: Request, name: str):
    _require_user(request)
    try:
        return await mediastack.start_update(name)
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:1000] if exc.response is not None else str(exc)
        raise HTTPException(exc.response.status_code if exc.response is not None else 502, detail)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.get("/api/mediastack/jobs/{job_id}")
async def mediastack_job_api(request: Request, job_id: str):
    _require_user(request)
    try:
        return await mediastack.job(job_id)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.get("/stack-setup", response_class=HTMLResponse)
async def stack_setup_page(request: Request):
    try:
        await mediastack.refresh_status()
    except Exception:
        pass
    stack = mediastack.cached_snapshot()
    return _render(request, "stack_setup.html", setup=stack_setup.page_context(stack), stack=stack)


@app.post("/api/stack-setup/preview")
async def stack_setup_preview_api(request: Request):
    _require_user(request)
    payload = await request.json()
    current = stack_setup.normalise(payload if isinstance(payload, dict) else {})
    return {
        "ok": True,
        "state": current,
        "plan": stack_setup.adoption_plan(mediastack.cached_snapshot(), current),
    }


@app.get("/api/stack-setup/recovery-compose")
async def stack_setup_recovery_compose_api(request: Request):
    _require_user(request)
    text = stack_setup.recovery_compose(mediastack.cached_snapshot(), stack_setup.state())
    return PlainTextResponse(
        text,
        media_type="text/yaml",
        headers={"Content-Disposition": 'attachment; filename="arrnexus-mediastack-recovery.yml"'},
    )


@app.post("/stack-setup")
async def stack_setup_save(request: Request):
    _require_user(request)
    form = await request.form()
    selected = [str(value) for value in form.getlist("services")]
    payload = {
        "mode": str(form.get("mode") or "adopt"),
        "selected_services": selected,
        "stack_root": str(form.get("stack_root") or "/opt/arrnexus-mediastack"),
        "zurg_mount_root": str(form.get("zurg_mount_root") or "/zurg_mnt"),
        "config_strategy": str(form.get("config_strategy") or "keep-existing"),
        "tz": str(form.get("tz") or "Europe/London"),
        "puid": str(form.get("puid") or "1000"),
        "pgid": str(form.get("pgid") or "1000"),
        "jellyfin_gpu": str(form.get("jellyfin_gpu") or "auto"),
        "update_mode": str(form.get("update_mode") or "review"),
        "update_time": str(form.get("update_time") or "04:00"),
        "rollback": bool(form.get("rollback")),
        "stabilization_seconds": str(form.get("stabilization_seconds") or "30"),
    }
    action = str(form.get("action") or "save")
    complete = action == "complete"
    saved = stack_setup.save(payload, complete=complete)
    policies = {
        key: str(form.get(f"policy_{key}") or "")
        for key in saved.get("selected_services") or []
    }
    stack_setup.save_service_policies(policies)

    stack = mediastack.cached_snapshot()
    plan = stack_setup.adoption_plan(stack, saved)
    if complete and saved.get("mode") == "adopt":
        names = [str(row.get("container") or "") for row in plan.get("services") or [] if row.get("present") and row.get("container")]
        if stack.get("write_enabled") and names:
            try:
                await mediastack.adopt(names)
                _flash(request, f"MediaStack setup saved. Adopted {len(names)} existing container(s).", "success")
            except Exception as exc:
                _flash(request, f"Setup saved, but adoption needs attention: {exc}", "error")
        else:
            _flash(request, "MediaStack setup saved. The Docker control channel is still locked, so no containers were changed.", "success")
    elif complete and saved.get("mode") == "fresh":
        _flash(request, "Fresh-install desired state saved. No production containers were replaced; the install executor can apply this plan when enabled.", "success")
    else:
        _flash(request, "MediaStack setup plan saved.", "success")
    return _go("/stack-setup")


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, level: str = "all", source: str = "all", q: str = ""):
    rows = list_logs(level, source, q, 500)
    sources = sorted({str(x.get("source") or "") for x in list_logs(limit=1000) if x.get("source")})
    return _render(request, "logs.html", logs=rows, level=level, source=source, q=q, sources=sources)


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return _render(
        request, "settings.html", zurg_root=setting_get("zurg.root", settings.zurg_root),
        zurg_url=setting_get("zurg.url", settings.zurg_url),
        zurg_cache_path=setting_get("zurg.cache_path", settings.zurg_cache_path),
        public_url=setting_get("app.public_url", "") or settings.public_url,
        settings_rows=all_settings(mask_secrets=True),
    )


@app.post("/settings/zurg")
async def settings_zurg_save(request: Request, zurg_root: str = Form(...), zurg_url: str = Form(""), zurg_cache_path: str = Form("")):
    setting_set("zurg.root", zurg_root.strip())
    setting_set("zurg.url", zurg_url.strip())
    setting_set("zurg.cache_path", zurg_cache_path.strip())
    _flash(request, "Zurg settings saved.", "success")
    return _go("/settings")


@app.post("/settings/app")
async def settings_app_save(request: Request, public_url: str = Form("")):
    setting_set("app.public_url", public_url.strip().rstrip("/"))
    _flash(request, "ArrNexus public URL saved.", "success")
    return _go("/settings")


@app.post("/settings/backup")
async def settings_backup(request: Request):
    try:
        path = database_backup()
        _flash(request, f"Database backup created: {path}", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/settings")


@app.post("/settings/diagnostics")
async def settings_diagnostics(request: Request):
    try:
        path = write_diagnostics()
        _flash(request, f"Diagnostics written: {path}", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/settings")


@app.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    return _render(request, "about.html")


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "app": "ArrNexus",
        "version": APP_VERSION,
        "zurg": zurg.cached_status(),
        "tracker": pipeline.tracker_state(),
        "services": services.cache_state(),
        "orchestrator": {"summary": orchestrator.cached_state().get("summary"), "ready": orchestrator.cached_state().get("ready")},
        "janitor": {"summary": queue_janitor.cached_state().get("summary"), "ready": queue_janitor.cached_state().get("ready")},
        "magic_intake": {"summary": magic_intake.cached_state().get("summary"), "running": magic_intake.cached_state().get("running", False)},
    }
