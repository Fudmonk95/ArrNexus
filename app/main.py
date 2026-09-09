from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse, FileResponse
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
from . import zurg
from . import services

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
try:
    APP_VERSION = (BASE_DIR.parent / "VERSION").read_text(encoding="utf-8").strip() or "13.0.0"
except OSError:
    APP_VERSION = "13.0.0"


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
    # Dashboard rendering is intentionally cache-only. Slow providers, Zurg
    # indexing and request correlation run in background tasks and can never
    # block the web UI.
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
async def magic_intake_page(request: Request, q: str = "", media_type: str = "movie", group_key: str = ""):
    state = magic_intake.cached_state()
    results = []
    if q and group_key:
        results = await magic_intake.lookup(media_type, q)
    return _render(request, "magic_intake.html", state=state, groups=state["groups"], cfg=state["settings"], search_results=results, search_q=q, search_type=media_type, search_group=group_key)


@app.post("/magic-intake/settings")
async def magic_intake_settings(request: Request, enabled: bool = Form(False), interval_seconds: int = Form(60), auto_match_threshold: int = Form(95), magic_root: str = Form(""), arr_prefix: str = Form("")):
    magic_intake.save_settings(locals())
    _flash(request, "Magic Intake settings saved.", "success")
    return _go("/magic-intake")


@app.post("/magic-intake/scan")
async def magic_intake_scan(request: Request):
    try:
        state = await magic_intake.scan()
        _flash(request, f"Magic Intake scan complete: {state['summary']['total']} group(s).", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/magic-intake")


@app.post("/magic-intake/force-match")
async def magic_intake_force_match(request: Request, group_key: str = Form(...), media_type: str = Form(...), candidate_json: str = Form(...)):
    try:
        candidate = json.loads(candidate_json)
        magic_intake.force_match(group_key, candidate)
        _flash(request, f"Force matched to {candidate.get('title') or 'selected item'}.", "success")
    except Exception as exc:
        _flash(request, str(exc), "error")
    return _go("/magic-intake")


@app.post("/magic-intake/import")
async def magic_intake_import(request: Request, group_key: str = Form(...), destination_key: str = Form(...), selected_source: str = Form("")):
    try:
        result = await magic_intake.import_group(group_key, destination_key, selected_source)
        _flash(request, result.get("detail") or "Magic Intake import complete.", "success" if result.get("ok") else "info")
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
    return magic_intake.cached_state()

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
    public_url = setting_get("app.public_url", "") or settings.public_url
    redirect_uri = music.spotify_redirect_uri(public_url, str(request.url))
    redirect_ok, redirect_message = music.spotify_redirect_validation(redirect_uri)
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


@app.get("/music/spotify/connect")
async def spotify_connect(request: Request):
    user = _require_user(request)
    public_url = setting_get("app.public_url", "") or settings.public_url
    redirect_uri = music.spotify_redirect_uri(public_url, str(request.url))
    redirect_ok, redirect_message = music.spotify_redirect_validation(redirect_uri)
    if not redirect_ok:
        _flash(request, f"Spotify cannot be linked yet. {redirect_message} Redirect URI: {redirect_uri or 'not configured'}", "error")
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
        _flash(request, error or "Spotify authorization state did not match.", "error")
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
    }
