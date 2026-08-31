from __future__ import annotations
import asyncio
import copy
import json
import secrets
import time
import smtplib
import io
import re
import httpx
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .db import (
    init_db, recent_imports, latest_import_by_source, successful_imports_by_source, latest_success_for_source,
    set_item_state, item_states, recent_activity, add_activity,
    create_job, get_job, recent_jobs, update_job, update_job_item,
    list_rules, save_rule, delete_rule, mark_import_undone, cache_get, cache_set,
    authenticate_user, get_user, update_user, setting_get, setting_set, activity_by_day, request_map, list_users, create_user, delete_user, create_password_reset, consume_password_reset,
    user_count, log_event, list_logs, log_sources, list_mounts, save_mount, delete_mount,
    add_scrape, update_scrape, list_scrapes, ui_pref_get, ui_pref_set,
    update_user_access, requests_today, title_timeline, all_settings, replace_nonsecret_settings, request_rows, update_request_progress,
)
from .scanner import scan_source, scan_media_root, inspect_item, normalize_title, human_size, invalidate_scan_cache
from .routing import decide_movie, decide_tv
from .arr import RadarrClient, SonarrClient, LidarrClient, ProwlarrClient, ArrError, poster_url
from .router_service import import_one, route_item, discover_lookup, discover_add, client_for_instance, LanguageRejectedSafe
from .importer import ImportErrorSafe, unlink_created, scan_broken_symlinks, repair_broken_symlink
from .namespace import view_path, is_within_logical, namespace_status, NamespaceError
from .instances import discover_instances, invalidate_instance_cache
from .library import inventory_roots, build_source_link_index, invalidate_library_cache
from .jellyfin import search_jellyfin, jellyfin_status
from .music import (
    search_musicbrainz, trending_artists, trending_releases, itunes_search,
    external_music_links, GENRES, audius_trending, audius_search,
    provider_catalog, enrich_artist_art, enrich_release_art, representative_artwork,
    internet_archive_search, jamendo_search, soundcloud_search, lastfm_search,
    lastfm_top, provider_featured, provider_search, safe_external_url,
    spotify_app_configured, spotify_user_linked, spotify_authorize_url, spotify_exchange_code,
    spotify_user_hub, spotify_disconnect_user,
)
from .connections import get_connection, save_connection
from .paths import movie_roots, tv_roots, lidarr_root, source_root, all_library_roots, dumb_root
from .seerr import SeerrClient, SeerrError, result_rows as seerr_result_rows
from . import realdebrid as rd
from .policy import load_policy, score_release
from .tvpacks import classify_release, pack_matches, coverage_summary, choose_best_complete, choose_best_season_packs
from .notifications import send_notification
from .admin_tools import create_database_backup, list_backups, sanitized_config, diagnostics_zip
from .plugins import load_catalog_plugins, plugin_search_url, safe_plugin_search_template
from .ecosystem import (
    connector_definitions, connector_config, save_connector as save_ecosystem_connector,
    probe_connector, probe_enabled_connectors, install_connector_plugin,
)
from .infinidysk import InfiniDyskClient, InfiniDyskError
from .qualitylab import evaluate_release, parse_release_name
from .selfhealing import (
    scan_self_healing, settings_state as selfheal_settings_state, save_settings as save_selfheal_settings,
    trigger_search as selfheal_trigger_search, scheduler_loop as selfheal_scheduler_loop,
)
from .acquisition import load_acquisition_settings, save_acquisition_settings, plan_and_grab, STRATEGIES
from .logbridge import external_log_rows
from .log_diagnostics import attach_explanations
from .decypharr import DecypharrClient
from .language_guard import (
    cached_language_result, inspect_source_languages, load_language_policy,
    save_language_policy, result_badge as language_result_badge,
    set_language_override, language_override,
)
from .providers import list_provider_states, save_provider, provider_definition, categories as provider_categories, migrate_legacy_providers
from .readiness import stack_readiness
from .release_export import build_public_release
from .runtime_cache import StaleSnapshot
from .media_servers import definitions as media_server_definitions, builtin_state as media_server_builtin_state, probe_builtin as probe_media_server, list_custom as list_custom_media_servers, save_custom as save_custom_media_server, delete_custom as delete_custom_media_server, probe_custom as probe_custom_media_server
from .consolidation import scan_consolidation, apply_consolidation
from .help_catalog import TOPICS as HELP_TOPICS, categories as help_categories, get_topic as get_help_topic, topic_for_path as help_topic_for_path
from .updater import (
    DEFAULT_REPOSITORY as UPDATE_DEFAULT_REPOSITORY, SELF_UPDATE_CAPABLE,
    check_for_update, start_install as start_self_update, status as update_status,
)
from . import lists as media_lists
from . import aiometadata as aiometadata_integration
from . import provider_cleanup as provider_cleanup_tools
from . import archive_rescue
from . import tv_recovery
from . import archive_media
from . import media_identity

BASE = Path(__file__).resolve().parent
app = FastAPI(title="ArrNexus")
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.filters["human_size"] = human_size
templates.env.globals["app_setting"] = setting_get
templates.env.globals["app_version"] = lambda: APP_VERSION if "APP_VERSION" in globals() else "10.4.4-beta"
templates.env.globals["release_channel"] = lambda: "beta" if "-beta" in APP_VERSION else (setting_get("update.channel", "stable") or "stable")

APP_VERSION = "10.4.4-beta"


@app.middleware("http")
async def request_timing_middleware(request: Request, call_next):
    """Expose real route timing and log only genuinely slow HTML/API requests."""
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed = (time.perf_counter() - started) * 1000
        try:
            log_event("error", "performance", "request_failed", f"{request.method} {request.url.path} failed after {elapsed:.0f}ms")
        except Exception:
            pass
        raise
    elapsed = (time.perf_counter() - started) * 1000
    response.headers["Server-Timing"] = f"arrnexus;dur={elapsed:.1f}"
    response.headers["X-ArrNexus-Elapsed-Ms"] = f"{elapsed:.1f}"
    if elapsed >= 1500 and request.url.path not in {"/download/latest", "/download/latest.sha256"}:
        try:
            log_event("warning", "performance", "slow_request", f"{request.method} {request.url.path} took {elapsed:.0f}ms")
        except Exception:
            pass
    return response

BRAND_ICONS = {
    "radarr": "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/radarr.png",
    "sonarr": "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/sonarr.png",
    "lidarr": "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/lidarr.png",
    "prowlarr": "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/prowlarr.png",
    "jellyfin": "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/jellyfin.png",
    "plex": "https://cdn.simpleicons.org/plex/E5A00D",
    "emby": "https://cdn.simpleicons.org/emby/52B54B",
    "seerr": "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/seerr.png",
    "infinidysk": "https://raw.githubusercontent.com/infinidysk/infinidysk/main/frontend/public/logo-square.png",
    "decypharr": "https://raw.githubusercontent.com/sirrobot01/decypharr/main/docs/public/favicon.png",
    "spotify": "https://cdn.simpleicons.org/spotify/1ED760",
    "soundcloud": "https://cdn.simpleicons.org/soundcloud/FF5500",
    "apple": "https://cdn.simpleicons.org/applemusic/FA243C",
    "audius": "https://cdn.simpleicons.org/audius/7E1BCC",
    "deezer": "https://cdn.simpleicons.org/deezer/A238FF",
    "lastfm": "https://cdn.simpleicons.org/lastdotfm/D51007",
    "bandcamp": "https://cdn.simpleicons.org/bandcamp/408294",
    "amazon": "https://cdn.simpleicons.org/amazonmusic/46C3D0",
    "discogs": "https://cdn.simpleicons.org/discogs/FFFFFF",
    "musicbrainz": "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/musicbrainz.png",
}

def brand_icon(key: str) -> str:
    return BRAND_ICONS.get(str(key or "").lower(), "")

templates.env.globals["brand_icon"] = brand_icon

def request_is_admin(request: Request) -> bool:
    try:
        uid = int(request.session.get("user_id") or 0)
        user = get_user(uid) if uid else None
        return bool(user and user.get("role") == "admin")
    except Exception:
        return False

templates.env.globals["request_is_admin"] = request_is_admin
templates.env.globals["help_topic_for_path"] = help_topic_for_path
RUNNING_TASKS: set[asyncio.Task] = set()

# v9.3 route snapshots.  These pages used to synchronously fan out across the
# filesystem and several remote APIs on every click.  Keep one usable snapshot
# and refresh it behind the current page once it goes stale.
_MAINTENANCE_SNAPSHOT = StaleSnapshot(90.0)
_PROBLEMS_SNAPSHOT = StaleSnapshot(30.0)
_READINESS_SNAPSHOT = StaleSnapshot(30.0)
_INBOX_SNAPSHOT = StaleSnapshot(45.0)
_INFINI_SNAPSHOTS: dict[str, StaleSnapshot] = {}
_INSTANCE_CATALOG_SNAPSHOT = StaleSnapshot(45.0)
_BROKEN_LINK_SNAPSHOT = StaleSnapshot(60.0)
_LIDARR_ARTISTS_SNAPSHOT = StaleSnapshot(45.0)
_MUSIC_ARTIST_SNAPSHOTS: dict[str, StaleSnapshot] = {}



@app.on_event("startup")
async def startup():
    init_db()
    try:
        migrated = migrate_legacy_providers()
        if migrated:
            log_event("info", "providers", "legacy_provider_migration", f"Seeded {len(migrated)} provider field(s) from legacy settings")
    except Exception as exc:
        try: log_event("warning", "providers", "legacy_provider_migration_failed", str(exc))
        except Exception: pass
    # Keep a rolling daily database backup without requiring an external cron job.
    try:
        enabled = setting_get("backup.auto_enabled", "true").lower() in {"1","true","yes","on"}
        if enabled and user_count() > 0:
            today = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y%m%d")
            if not any(today in b.get("name", "") for b in list_backups(20)):
                create_database_backup("auto", int(setting_get("backup.retention", "10") or 10))
    except Exception as exc:
        try: log_event("warning", "backup", "auto_backup_failed", str(exc))
        except Exception: pass
    # Optional self-healing scheduler. It is disabled by default and only
    # performs bounded Arr searches when explicitly enabled in the UI.
    try:
        task = asyncio.create_task(selfheal_scheduler_loop())
        RUNNING_TASKS.add(task)
        task.add_done_callback(RUNNING_TASKS.discard)
    except Exception as exc:
        try: log_event("warning", "selfheal", "scheduler_start_failed", str(exc))
        except Exception: pass

    # v10.2 list automation uses the same bounded in-process scheduler model.
    try:
        list_task = asyncio.create_task(media_lists.scheduler_loop())
        RUNNING_TASKS.add(list_task)
        list_task.add_done_callback(RUNNING_TASKS.discard)
    except Exception as exc:
        try: log_event("warning", "lists", "scheduler_start_failed", str(exc))
        except Exception: pass

    # Pre-warm expensive namespace inventories off the request path.  This is
    # especially important after Maintenance: Dashboard can reuse the same
    # short-lived snapshots instead of walking every virtual file again.
    async def _prewarm_namespace_snapshots():
        try:
            await asyncio.gather(
                asyncio.to_thread(scan_source),
                asyncio.to_thread(build_source_link_index),
                asyncio.to_thread(inventory_roots),
                return_exceptions=True,
            )
        except Exception:
            pass
    try:
        warm = asyncio.create_task(_prewarm_namespace_snapshots())
        RUNNING_TASKS.add(warm)
        warm.add_done_callback(RUNNING_TASKS.discard)
    except Exception:
        pass

    # Build the first dashboard snapshot in the background so opening the
    # Control Centre does not have to fan out to every service on demand.
    try:
        dash = asyncio.create_task(_refresh_dashboard_snapshot())
        RUNNING_TASKS.add(dash)
        dash.add_done_callback(RUNNING_TASKS.discard)
    except Exception:
        pass

    # Stagger the two most frequently opened expensive operational pages.  The
    # work happens after startup and never blocks the web server becoming ready.
    async def _prewarm_v93_ui():
        # Fresh installations have nothing useful to prewarm yet.  Existing
        # configured stacks get a deliberately staggered server-side warm-up
        # instead of the browser crawling every expensive sidebar route.
        if user_count() == 0 or setting_get("setup.complete", "false").lower() not in {"1", "true", "yes", "on"}:
            return
        await asyncio.sleep(1.5)
        try:
            await _INBOX_SNAPSHOT.get(_build_inbox_snapshot)
        except Exception:
            pass
        await asyncio.sleep(0.75)
        try:
            if connector_config("infinidysk").get("enabled"):
                await _infini_snapshot_cache("24h").get(lambda: _build_infinidysk_snapshot("24h"))
        except Exception:
            pass
        # Give normal startup/navigation priority before warming the heavier
        # maintenance/readiness views once in the background.
        await asyncio.sleep(4.0)
        try:
            await _MAINTENANCE_SNAPSHOT.get(_build_maintenance_snapshot)
        except Exception:
            pass
        await asyncio.sleep(0.5)
        try:
            await _READINESS_SNAPSHOT.get(_live_stack_readiness_uncached)
        except Exception:
            pass
    try:
        warm_ui = asyncio.create_task(_prewarm_v93_ui())
        RUNNING_TASKS.add(warm_ui)
        warm_ui.add_done_callback(RUNNING_TASKS.discard)
    except Exception:
        pass


def logged_in(request: Request):
    return bool(request.session.get("user_id") or request.session.get("auth"))


def require_auth(request: Request):
    if not logged_in(request):
        raise HTTPException(401)


def current_user(request: Request) -> dict:
    require_auth(request)
    uid = int(request.session.get("user_id") or 0)
    user = get_user(uid) if uid else None
    if not user:
        raise HTTPException(401)
    return user


def require_admin(request: Request) -> dict:
    user = current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(403, "Administrator access required")
    return user


def require_request_access(request: Request, count_against_limit: bool = False) -> dict:
    user = current_user(request)
    if user.get("role") != "admin" and not int(user.get("can_request", 1) or 0):
        raise HTTPException(403, "This profile is not allowed to request media")
    if count_against_limit and user.get("role") != "admin":
        limit = int(user.get("daily_request_limit") or 0)
        if limit and requests_today(int(user["id"])) >= limit:
            raise HTTPException(429, f"Daily request limit reached ({limit})")
    return user


@app.exception_handler(401)
async def auth_error(request: Request, exc):
    return RedirectResponse("/setup" if user_count() == 0 else "/login", status_code=303)


@app.get("/api/health")
async def health():
    ns = namespace_status()
    return {"ok": True, "app": "ArrNexus", "version": APP_VERSION, "namespace": bool(ns.get("ok"))}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, notice: str = ""):
    if user_count() == 0:
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None, "notice": notice})


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, error: str = ""):
    if user_count() > 0:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("setup.html", {"request": request, "error": error})


@app.post("/setup")
async def setup_create(request: Request, username: str = Form(...), email: str = Form(""), display_name: str = Form(""), password: str = Form(...), confirm: str = Form(...)):
    if user_count() > 0:
        return RedirectResponse("/login", status_code=303)
    if password != confirm:
        return RedirectResponse(f"/setup?error={quote('Passwords do not match')}", status_code=303)
    if len(password) < 10:
        return RedirectResponse(f"/setup?error={quote('Use at least 10 characters for the administrator password')}", status_code=303)
    try:
        uid = create_user(username, email, display_name or username, password, "admin")
        setting_set("app.title", "ArrNexus")
        setting_set("setup.complete", "false")
        setting_set("setup.stage", "administrator")
        log_event("info", "setup", "administrator_created", f"Administrator {username} created")
        request.session.clear(); request.session["auth"] = True; request.session["user_id"] = uid
        request.session["theme"] = "arrnexus"; request.session["display_name"] = display_name or username; request.session["role"] = "admin"
        return RedirectResponse("/onboarding", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/setup?error={quote(str(exc))}", status_code=303)


def _public_origin(request: Request) -> str:
    """Return the configured or reverse-proxy-aware public http(s) origin."""
    configured = (setting_get("app.public_url", "") or "").strip().rstrip("/")
    if configured and re.match(r"^https?://[A-Za-z0-9.\-_:]+$", configured):
        return configured
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip().lower()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc or "").split(",")[0].strip()
    if proto not in {"http", "https"}:
        proto = "https" if request.url.scheme == "https" else "http"
    if not re.match(r"^[A-Za-z0-9.\-_:]+$", host):
        host = request.url.netloc
    return f"{proto}://{host}".rstrip("/")


def _suggested_spotify_redirect_uri(request: Request) -> str:
    return _public_origin(request) + "/music/spotify/callback"


async def _send_reset_email(to_address: str, link: str):
    host = setting_get("smtp.host")
    if not host:
        raise RuntimeError("SMTP is not configured")
    port = int(setting_get("smtp.port", "587") or 587)
    username = setting_get("smtp.username")
    password = setting_get("smtp.password")
    from_address = setting_get("smtp.from_address") or username
    if not from_address:
        raise RuntimeError("SMTP from address is not configured")
    msg = EmailMessage()
    msg["Subject"] = f"{setting_get('app.title','ArrNexus')} password reset"
    msg["From"] = from_address
    msg["To"] = to_address
    msg.set_content(f"A password reset was requested for your ArrNexus account.\n\nOpen this link within 30 minutes:\n{link}\n\nIf you did not request this, ignore this email.")
    def send():
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
        try:
            if port != 465 and setting_get("smtp.starttls", "true").lower() in {"1","true","yes","on"}:
                server.starttls()
            if username:
                server.login(username, password)
            server.send_message(msg)
        finally:
            server.quit()
    await asyncio.to_thread(send)


@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request, sent: str = "", error: str = ""):
    return templates.TemplateResponse("forgot_password.html", {"request": request, "sent": sent, "error": error})


@app.post("/forgot-password")
async def forgot_password(request: Request, email: str = Form(...)):
    token = create_password_reset(email)
    if token:
        try:
            link = _public_origin(request) + f"/reset-password?token={quote(token)}"
            await _send_reset_email(email.strip(), link)
        except Exception as exc:
            return RedirectResponse(f"/forgot-password?error={quote('Email could not be sent: ' + str(exc))}", status_code=303)
    # Deliberately do not reveal whether an address exists.
    return RedirectResponse("/forgot-password?sent=1", status_code=303)


@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = "", error: str = ""):
    return templates.TemplateResponse("reset_password.html", {"request": request, "token": token, "error": error})


@app.post("/reset-password")
async def reset_password(request: Request, token: str = Form(...), password: str = Form(...), confirm: str = Form(...)):
    if password != confirm:
        return RedirectResponse(f"/reset-password?token={quote(token)}&error={quote('Passwords do not match')}", status_code=303)
    if len(password) < 8:
        return RedirectResponse(f"/reset-password?token={quote(token)}&error={quote('Password must be at least 8 characters')}", status_code=303)
    if not consume_password_reset(token, password):
        return RedirectResponse(f"/reset-password?token={quote(token)}&error={quote('Reset link is invalid or expired')}", status_code=303)
    return RedirectResponse(f"/login?notice={quote('Password updated. You can sign in now.')}", status_code=303)


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    user = authenticate_user(username, password)
    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Incorrect username/email or password"}, status_code=401)
    request.session.clear()
    request.session["auth"] = True
    request.session["user_id"] = int(user["id"])
    request.session["theme"] = "arrnexus"
    request.session["display_name"] = user.get("display_name") or user.get("username")
    request.session["role"] = user.get("role") or "user"
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


async def arr_status(client, timeout: float = 4.0):
    try:
        s = await asyncio.wait_for(client.status(), timeout=timeout)
        return {"ok": True, "version": s.get("version"), "appName": s.get("appName") or client.name}
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"Timed out after {timeout:.0f}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _live_stack_readiness_uncached() -> dict:
    """Bounded live readiness probe.

    v9.2 built the synchronous namespace/configuration state first and only then
    started API probes.  On DUMB mount namespaces that serialized several
    seconds of work.  v9.3 runs the base snapshot and remote checks together.
    """
    clients = {
        "radarr": RadarrClient(), "sonarr": SonarrClient(), "lidarr": LidarrClient(),
        "prowlarr": ProwlarrClient(), "seerr": SeerrClient(),
    }

    async def jf_check():
        try:
            data = await asyncio.wait_for(jellyfin_status(), timeout=3.5)
            return {"ok": True, "version": data.get("Version") or data.get("version") or "Connected"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    base_task = asyncio.to_thread(stack_readiness)
    api_task = asyncio.gather(*(arr_status(client, 3.5) for client in clients.values()), jf_check())
    state, results = await asyncio.gather(base_task, api_task)
    live = dict(zip(list(clients) + ["jellyfin"], results))
    for check in state["checks"]:
        if not check["key"].startswith("connection-"):
            continue
        service = check["key"].split("connection-", 1)[1]
        if not check["ok"]:
            continue
        result = live.get(service) or {}
        check["ok"] = bool(result.get("ok"))
        check["detail"] = (f"API verified · {result.get('version') or 'connected'}" if result.get("ok") else f"Configured but API check failed · {result.get('error') or 'unknown error'}")

    # Plex/Emby/custom media servers are optional.  They are included in the
    # readiness detail when configured but never make an Arr-only stack fail.
    media_checks = []
    for definition in media_server_definitions():
        if definition.key == "jellyfin":
            continue
        state_row = media_server_builtin_state(definition.key)
        if state_row.get("url") or state_row.get("has_token"):
            result = await probe_media_server(definition.key, state_row.get("url") or "", get_connection(definition.key).api_key)
            media_checks.append({"key": f"media-{definition.key}", "label": f"{definition.name} media server", "ok": bool(result.get("ok")), "detail": result.get("version") or result.get("error") or result.get("detail") or "Configured", "required": False, "action": "/arrs"})
    for custom in list_custom_media_servers(mask=True):
        result = await probe_custom_media_server(custom)
        media_checks.append({"key": f"media-custom-{custom.get('id')}", "label": custom.get("name") or "External media server", "ok": bool(result.get("ok")), "detail": result.get("detail") or result.get("error") or "Configured", "required": False, "action": "/arrs"})
    state["checks"].extend(media_checks)

    required = [c for c in state["checks"] if c["required"]]
    optional = [c for c in state["checks"] if not c["required"]]
    score = 0
    if required:
        score += round(85 * sum(1 for c in required if c["ok"]) / len(required))
    if optional:
        score += round(15 * sum(1 for c in optional if c["ok"]) / len(optional))
    state["score"] = min(100, score)
    state["ready"] = all(c["ok"] for c in required)
    state["required_ok"] = sum(1 for c in required if c["ok"])
    state["required_total"] = len(required)
    return state


async def live_stack_readiness(force: bool = False) -> dict:
    data, age, refreshing = await _READINESS_SNAPSHOT.get(_live_stack_readiness_uncached, force=force)
    data["snapshot_age"] = age
    data["refreshing"] = refreshing
    return data


def _title_map(entries: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for x in entries or []:
        out.setdefault(normalize_title(x.get("title", "")), []).append(x)
    return out


async def _instance_catalogs_uncached():
    instances = discover_instances()
    movie_entries: list[tuple[dict, object]] = []
    tv_entries: list[tuple[dict, object]] = []

    async def load(inst):
        try:
            client = client_for_instance(inst)
            data = await (client.movies() if inst.service == "radarr" else client.series() if inst.service == "sonarr" else client.artists())
            return inst, data or []
        except Exception:
            return inst, []

    results = await asyncio.gather(*(load(i) for i in instances if i.service in {"radarr", "sonarr"}))
    for inst, rows in results:
        target = movie_entries if inst.service == "radarr" else tv_entries
        target.extend((row, inst) for row in rows)
    return movie_entries, tv_entries


async def _instance_catalogs():
    data, _age, _refreshing = await _INSTANCE_CATALOG_SNAPSHOT.get(_instance_catalogs_uncached)
    return data


async def enrich_items(items):
    imports = latest_import_by_source()
    successful = successful_imports_by_source()
    states = item_states()
    links = build_source_link_index()
    movie_catalog, tv_catalog = await _instance_catalogs()
    movie_map: dict[str, list[tuple[dict, object]]] = {}
    tv_map: dict[str, list[tuple[dict, object]]] = {}
    for entry, inst in movie_catalog:
        movie_map.setdefault(normalize_title(entry.get("title", "")), []).append((entry, inst))
    for entry, inst in tv_catalog:
        tv_map.setdefault(normalize_title(entry.get("title", "")), []).append((entry, inst))

    sem = asyncio.Semaphore(5)

    async def one(item):
        detected_item = item
        item, identity = media_identity.apply_to_item(item)
        match = None
        match_inst = None
        candidates = movie_map.get(normalize_title(item.title_guess), []) if item.media_type == "movie" else tv_map.get(normalize_title(item.title_guess), [])
        if item.media_type == "movie":
            for candidate, inst in candidates:
                if item.year_guess and candidate.get("year") and int(candidate.get("year")) != int(item.year_guess):
                    continue
                match, match_inst = candidate, inst
                break
        else:
            # TV season-pack folder years are frequently upload/release years,
            # so do not reject a single unambiguous Sonarr title just because
            # its folder says 2005/2006.  If Sonarr contains multiple series
            # with the same title (Doctor Who-style remakes), require stronger
            # evidence: an exact TMDb identity or one unique premiere-year match.
            tmdb_id = int((identity or {}).get("tmdb_id") or 0)
            if tmdb_id:
                exact = [(candidate, inst) for candidate, inst in candidates if int(candidate.get("tmdbId") or 0) == tmdb_id]
                if len(exact) == 1:
                    match, match_inst = exact[0]
            if match is None and len(candidates) == 1:
                match, match_inst = candidates[0]
            elif match is None and len(candidates) > 1 and item.year_guess:
                year_matches = [(candidate, inst) for candidate, inst in candidates if candidate.get("year") and int(candidate.get("year")) == int(item.year_guess)]
                if len(year_matches) == 1:
                    match, match_inst = year_matches[0]

        metadata = match
        lookup = []
        cache_key = f"lookup:{item.media_type}:{normalize_title(item.title_guess)}:{item.year_guess or 0}"
        if metadata is None:
            # Inbox is a list view, not a metadata-search endpoint.  Reuse any
            # lookup cached by Item Review/Discover but never launch dozens of
            # Radarr/Sonarr lookups while rendering the inbox.
            lookup = cache_get(cache_key) or []
            metadata = lookup[0] if lookup else {}

        if match and match_inst and match_inst.destination_key:
            decision_key = match_inst.destination_key
            roots = movie_roots() if item.media_type == "movie" else tv_roots()
            decision = {"key": decision_key, "root": roots.get(decision_key, roots["default"]), "reason": f"Already owned by {match_inst.service}/{match_inst.instance}", "confidence": 100}
        else:
            d = decide_movie(item.title_guess, metadata) if item.media_type == "movie" else decide_tv(item.title_guess, metadata)
            decision = {"key": d.key, "root": d.root, "reason": d.reason, "confidence": d.confidence}

        imp = successful.get(item.path) or imports.get(item.path)
        state = (states.get(item.path) or {}).get("state", "waiting")
        if successful.get(item.path):
            state = "imported"
        linked_paths = links.get(item.path, [])
        if linked_paths and state == "waiting":
            state = "linked"
        success_row = successful.get(item.path)
        changed = bool(success_row and success_row.get("source_fingerprint") and success_row.get("source_fingerprint") != item.fingerprint)

        existing_res = 0
        if match:
            f = match.get("movieFile") or {}
            q = (f.get("quality") or {}).get("quality") or {}
            try:
                existing_res = int(q.get("resolution") or 0)
            except Exception:
                existing_res = 0

        jf_conn = get_connection("jellyfin")
        # A per-card Jellyfin query multiplied inbox latency by the number of
        # imported items.  Exact media-server detection belongs on Item Review;
        # list views only advertise that a media server is configured.
        jf = {"configured": bool(jf_conn.api_key), "found": False, "deferred": True}

        display_title = (metadata or {}).get("title") or item.title_guess
        display_year = (metadata or {}).get("year") or item.year_guess
        external_id = (metadata or {}).get("tmdbId") if item.media_type == "movie" else (metadata or {}).get("tvdbId")
        # TV is grouped series-first. Season-pack folder years often represent
        # the release/upload year rather than the show's premiere year (for
        # example different Tracy Beaker seasons). Prefer Sonarr/TVDB identity,
        # then cautiously fall back to canonical title. Movies keep title/year.
        if item.media_type == "tv":
            canonical_key = f"tv:tvdb:{external_id}" if external_id else f"tv:title:{normalize_title(display_title)}"
        else:
            canonical_key = f"movie:{normalize_title(display_title)}:{display_year or 0}"
        language_guard = cached_language_result(item.path, item.fingerprint)
        language_badge_key, language_badge_label = language_result_badge(language_guard)
        if language_guard is None and state in {"language_rejected", "language_issue", "language_review"}:
            language_badge_key, language_badge_label = "recheck_required", "Re-check required"
        return {
            "item": item,
            "metadata": metadata or {},
            "genres": (metadata or {}).get("genres") or [],
            "display_title": display_title,
            "display_year": display_year,
            "canonical_key": canonical_key,
            "poster": poster_url(metadata),
            "existing": match,
            "instance": match_inst,
            "decision": decision,
            "state": state,
            "import": imp,
            "linked_paths": linked_paths,
            "duplicate": bool(match or linked_paths),
            "existing_resolution": existing_res,
            "upgrade": bool(item.quality and existing_res and item.quality > existing_res),
            "changed": changed,
            "jellyfin": jf,
            "language_guard": language_guard,
            "language_badge_key": language_badge_key,
            "language_badge_label": language_badge_label,
            "identity": identity,
            "identity_confidence": int((identity or {}).get("confidence") or decision.get("confidence") or 0),
            "identity_needs_review": bool(not identity and int(decision.get("confidence") or 0) < 75),
            "provenance": "Extracted RAR" if is_within_logical(item.path, archive_media.extraction_root()) else "DMM / provider source",
            "detected_item": detected_item,
        }

    return await asyncio.gather(*(one(x) for x in items))


def dedupe_rows(rows: list[dict]) -> list[dict]:
    """Collapse multiple RD torrents for the same title into one useful card.

    The best available source (resolution, then size) becomes the selectable
    source, while title-level managed/imported state is aggregated from every
    duplicate. This means an already imported 1080p copy plus a new 2160p copy
    appears once and can correctly surface as an upgrade.
    """
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row.get("canonical_key") or row["item"].path, []).append(row)
    out = []
    state_rank = {"imported": 4, "linked": 3, "waiting": 2, "ignored": 1}
    for group in groups.values():
        # Prefer a usable current-policy source for import, but aggregate the
        # language state across every grouped provider copy for display/cleanup.
        language_rank = {"pass": 6, "disabled": 5, "unchecked": 4, "recheck_required": 3, "unknown": 2, "probe_failed": 1, "fail": 0}
        group = sorted(
            group,
            key=lambda r: (language_rank.get(r.get("language_badge_key"), 2), r["item"].quality or 0, r["item"].size_bytes or 0, state_rank.get(r.get("state"), 0)),
            reverse=True,
        )
        primary = group[0]
        managed = [r for r in group if r.get("state") in {"imported", "linked"} or r.get("linked_paths")]
        # Persistent historical states do not count as a current rejection.
        # Only a current-policy fail may present as Language rejected.
        rejected_rows = [r for r in group if r.get("language_badge_key") == "fail"]
        language_counts = {}
        for row in group:
            key = str(row.get("language_badge_key") or "unchecked")
            language_counts[key] = language_counts.get(key, 0) + 1
        primary["language_group_counts"] = language_counts
        primary["rejected_sources"] = [r["item"].path for r in group if r.get("language_badge_key") == "fail" and bool((r.get("language_guard") or {}).get("destructive_safe"))]
        primary["recheck_sources"] = [r["item"].path for r in group if r.get("language_badge_key") == "recheck_required"]
        if language_counts.get("fail"):
            n = language_counts["fail"]
            primary["language_badge_key"] = "fail"
            primary["language_badge_label"] = f"{n} rejected source{'s' if n != 1 else ''}" if len(group) > 1 else "Language rejected"
        elif language_counts.get("recheck_required"):
            n = language_counts["recheck_required"]
            primary["language_badge_key"] = "recheck_required"
            primary["language_badge_label"] = f"{n} re-check required" if len(group) > 1 else "Re-check required"
        elif language_counts.get("unknown") or language_counts.get("probe_failed"):
            primary["language_badge_key"] = "unknown"
            primary["language_badge_label"] = "Manual review"
        ignored = len(group) and all(r.get("state") == "ignored" for r in group)
        primary["duplicate_count"] = len(group)
        primary["duplicate_sources"] = [r["item"].path for r in group]
        if primary["item"].media_type == "tv":
            series_sources = []
            seasons = set()
            for r in group:
                nums = list(r["item"].season_numbers or [])
                seasons.update(nums)
                series_sources.append({
                    "path": r["item"].path,
                    "name": r["item"].name,
                    "seasons": nums,
                    "video_count": r["item"].video_count,
                    "quality": r["item"].quality,
                    "language_key": r.get("language_badge_key") or "unchecked",
                    "language_label": r.get("language_badge_label") or "Language unchecked",
                    "state": r.get("state") or "waiting",
                    "provenance": r.get("provenance") or "DMM / provider source",
                })
            primary["series_sources"] = sorted(series_sources, key=lambda x: (x["seasons"] or [999], x["name"].lower()))
            primary["series_seasons"] = sorted(seasons)
            primary["source_pack_count"] = len(group)
        primary["imported_copy_count"] = len(managed)
        primary["linked_paths"] = sorted({p for r in group for p in (r.get("linked_paths") or [])})
        primary["existing"] = primary.get("existing") or next((r.get("existing") for r in group if r.get("existing")), None)
        primary["instance"] = primary.get("instance") or next((r.get("instance") for r in group if r.get("instance")), None)
        primary["existing_resolution"] = max([int(r.get("existing_resolution") or 0) for r in group], default=0)
        primary["changed"] = any(bool(r.get("changed")) for r in group)
        if ignored:
            primary["state"] = "ignored"
        elif managed:
            primary["state"] = "imported"
        elif rejected_rows:
            primary["state"] = "language_rejected"
        elif language_counts.get("recheck_required") or language_counts.get("unknown") or language_counts.get("probe_failed"):
            primary["state"] = "language_review"
        else:
            primary["state"] = "waiting"
        primary["duplicate"] = len(group) > 1
        primary["upgrade"] = bool(primary["item"].quality and primary["existing_resolution"] and primary["item"].quality > primary["existing_resolution"])
        out.append(primary)
    return sorted(out, key=lambda r: (r.get("display_title") or "").lower())


@app.get("/help", response_class=HTMLResponse)
async def help_centre(request: Request, topic: str = "", q: str = ""):
    query = (q or "").strip().lower()
    selected = get_help_topic(topic)
    topics = list(HELP_TOPICS)
    if query:
        def matches(item):
            haystack = " ".join([
                item.get("title", ""), item.get("category", ""), item.get("summary", ""),
                *item.get("prerequisites", []), *item.get("setup", []), *item.get("usage", []),
                *item.get("success", []), *item.get("troubleshooting", []), *item.get("safety", []),
            ]).lower()
            return query in haystack
        topics = [item for item in topics if matches(item)]
    return templates.TemplateResponse("help.html", {
        "request": request, "topics": topics, "categories": help_categories(),
        "selected": selected, "query": q or "", "configured": user_count() > 0,
        "logged_in": logged_in(request), "version": APP_VERSION,
    })


@app.get("/", response_class=HTMLResponse)
async def public_landing(request: Request):
    return templates.TemplateResponse("landing.html", {
        "request": request,
        "configured": user_count() > 0,
        "logged_in": logged_in(request),
        "version": APP_VERSION,
        "release_filename": f"arrnexus-v{APP_VERSION}.zip",
    })


@app.get("/download/latest")
async def public_release_download():
    release = await asyncio.to_thread(build_public_release, BASE.parent, APP_VERSION)
    return FileResponse(
        release["path"],
        media_type="application/zip",
        filename=release["filename"],
        headers={
            "Cache-Control": "public, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/download/latest.sha256")
async def public_release_sha256():
    release = await asyncio.to_thread(build_public_release, BASE.parent, APP_VERSION)
    body = f"{release['sha256']}  {release['filename']}\n"
    return Response(
        body,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={release['filename']}.sha256", "Cache-Control": "public, max-age=300"},
    )


@app.get("/api/public/release")
async def public_release_metadata():
    release = await asyncio.to_thread(build_public_release, BASE.parent, APP_VERSION)
    return {
        "version": APP_VERSION,
        "filename": release["filename"],
        "sha256": release["sha256"],
        "size": release["size"],
        "files": release["files"],
        "download": "/download/latest",
        "checksum": "/download/latest.sha256",
    }


_DASHBOARD_CACHE_TTL = 25.0
_DASHBOARD_CACHE_AT = 0.0
_DASHBOARD_CACHE: dict | None = None
_DASHBOARD_REFRESH_TASK: asyncio.Task | None = None
_DASHBOARD_LOCK = asyncio.Lock()


async def _build_dashboard_snapshot() -> dict:
    """Build the expensive, user-independent dashboard state once.

    v9.2 keeps this work off ordinary navigation. Filesystem inventories use
    worker threads, service calls run concurrently, and the resulting snapshot
    is reused for a short period by every authenticated profile.
    """
    try:
        items = await asyncio.to_thread(scan_source)
    except Exception as exc:
        items = []
        log_event("warning", "namespace", "source_scan_unavailable", str(exc))

    imports = successful_imports_by_source()
    states = item_states()
    try:
        links = await asyncio.to_thread(build_source_link_index)
    except Exception:
        links = {}
    imported = sum(1 for x in items if x.path in imports or x.path in links)
    ignored = sum(1 for x in items if (states.get(x.path) or {}).get("state") == "ignored")
    waiting = max(0, len(items) - imported - ignored)

    async def _jf_status():
        try:
            js = await asyncio.wait_for(jellyfin_status(), timeout=3.0)
            return {"ok": True, "version": js.get("Version") or js.get("version") or "Connected"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    rad_s, son_s, lid_s, pro_s, jf_s, see_s = await asyncio.gather(
        arr_status(RadarrClient(), 3.0), arr_status(SonarrClient(), 3.0), arr_status(LidarrClient(), 3.0),
        arr_status(ProwlarrClient(), 3.0), _jf_status(), arr_status(SeerrClient(), 3.0),
    )
    statuses = {"radarr": rad_s, "sonarr": son_s, "lidarr": lid_s, "prowlarr": pro_s, "jellyfin": jf_s, "seerr": see_s}

    queue_counts = {"radarr": 0, "sonarr": 0, "lidarr": 0}
    library_movie_count = library_tv_count = 0
    route_inventory = []
    try:
        discovered = await asyncio.to_thread(discover_instances)
    except Exception:
        discovered = []
    dashboard_instances = [i for i in discovered if i.service in {"radarr", "sonarr", "lidarr"} and i.api_key]

    async def _load_dashboard_instance(inst):
        try:
            c = client_for_instance(inst)
            media_coro = c.movies() if inst.service == "radarr" else c.series() if inst.service == "sonarr" else c.artists()
            rows, q = await asyncio.wait_for(asyncio.gather(media_coro, c.queue(200)), timeout=4.0)
            rec = q.get("records", []) if isinstance(q, dict) else (q or [])
            return inst, rows or [], rec
        except Exception:
            return inst, [], []

    loaded = await asyncio.gather(*(_load_dashboard_instance(i) for i in dashboard_instances))
    for inst, rows, rec in loaded:
        if inst.service == "radarr":
            library_movie_count += len(rows)
        elif inst.service == "sonarr":
            library_tv_count += len(rows)
        route_inventory.append({"service": inst.service, "instance": inst.instance, "route": inst.destination_key or "default", "count": len(rows)})
        queue_counts[inst.service] = queue_counts.get(inst.service, 0) + len(rec)

    dest_counts = {}
    for row in recent_imports(5000):
        if row["status"] in {"complete", "linked"} and not row["undone"]:
            key = f"{row['arr_name'] or row['media_type']}:{row['destination_key']}"
            dest_counts[key] = dest_counts.get(key, 0) + 1

    rd_user = None
    if rd.connected():
        try:
            rd_user = await asyncio.wait_for(rd.user(), timeout=3.0)
        except Exception:
            rd_user = {"error": "Connected, but account status could not be loaded"}

    activity_days = activity_by_day(7)
    dest_top = sorted(dest_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    try:
        inv, ns = await asyncio.gather(
            asyncio.to_thread(inventory_roots),
            asyncio.to_thread(namespace_status),
        )
    except Exception:
        inv, ns = [], {"ok": False, "error": "Namespace snapshot unavailable"}
    scrape_rows = list_scrapes(200, "all")
    scraping_count = sum(1 for x in scrape_rows if (x.get("status") or "") in {"searching", "queued", "running"})
    active_jobs = [dict(x) for x in recent_jobs(20) if x["status"] in {"queued", "running"}]

    return {
        "items": items[:10], "source_count": len(items),
        "movie_count": sum(1 for i in items if i.media_type == "movie"),
        "tv_count": sum(1 for i in items if i.media_type == "tv"),
        "library_movie_count": library_movie_count, "library_tv_count": library_tv_count,
        "route_inventory": route_inventory, "libraries": inv,
        "imported_count": imported, "waiting_count": waiting, "ignored_count": ignored,
        "statuses": statuses, "queue_counts": queue_counts, "scraping_count": scraping_count,
        "active_jobs": active_jobs, "dest_counts": dest_top,
        "dest_max": max([x[1] for x in dest_top], default=1),
        "activity_days": activity_days,
        "activity_max": max([int(x.get("count") or 0) for x in activity_days], default=1),
        # SQLite Row objects cannot be deep-copied. Dashboard snapshots are
        # shared/cached, so normalise all DB rows to plain dicts before storing.
        "recent": [dict(x) for x in recent_imports(8)],
        "activity": [dict(x) for x in recent_activity(12)],
        "jobs": [dict(x) for x in recent_jobs(6)],
        "source_root": source_root(), "namespace": ns,
        "rd_connected": rd.connected(), "rd_user": rd_user,
        "snapshot_built_at": time.time(),
    }


async def _refresh_dashboard_snapshot() -> dict:
    global _DASHBOARD_CACHE_AT, _DASHBOARD_CACHE
    data = await _build_dashboard_snapshot()
    _DASHBOARD_CACHE = data
    _DASHBOARD_CACHE_AT = time.monotonic()
    return data


async def dashboard_snapshot(force: bool = False) -> tuple[dict, int]:
    """Return a fast stale-while-revalidate dashboard snapshot."""
    global _DASHBOARD_REFRESH_TASK
    now = time.monotonic()
    if _DASHBOARD_CACHE is not None and not force:
        age = max(0, int(time.time() - float(_DASHBOARD_CACHE.get("snapshot_built_at") or time.time())))
        if now - _DASHBOARD_CACHE_AT >= _DASHBOARD_CACHE_TTL:
            if _DASHBOARD_REFRESH_TASK is None or _DASHBOARD_REFRESH_TASK.done():
                _DASHBOARD_REFRESH_TASK = asyncio.create_task(_refresh_dashboard_snapshot())
                RUNNING_TASKS.add(_DASHBOARD_REFRESH_TASK)
                _DASHBOARD_REFRESH_TASK.add_done_callback(RUNNING_TASKS.discard)
        return copy.deepcopy(_DASHBOARD_CACHE), age

    async with _DASHBOARD_LOCK:
        if _DASHBOARD_CACHE is None or force:
            data = await _refresh_dashboard_snapshot()
        else:
            data = _DASHBOARD_CACHE
    age = max(0, int(time.time() - float(data.get("snapshot_built_at") or time.time())))
    return copy.deepcopy(data), age


def _empty_dashboard_snapshot(error: str = "") -> dict:
    """Safe render payload used when one dashboard dependency is unavailable."""
    return {
        "items": [], "source_count": 0, "movie_count": 0, "tv_count": 0,
        "library_movie_count": 0, "library_tv_count": 0, "route_inventory": [],
        "libraries": [], "imported_count": 0, "waiting_count": 0, "ignored_count": 0,
        "statuses": {}, "queue_counts": {"radarr": 0, "sonarr": 0, "lidarr": 0},
        "scraping_count": 0, "active_jobs": [], "dest_counts": [], "dest_max": 1,
        "activity_days": [], "activity_max": 1, "recent": [], "activity": [], "jobs": [],
        "source_root": source_root(), "namespace": {"ok": False, "error": error or "Snapshot unavailable"},
        "rd_connected": False, "rd_user": None, "snapshot_built_at": time.time(),
        "dashboard_error": error or "",
    }


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = current_user(request)
    try:
        data, age = await dashboard_snapshot()
    except Exception as exc:
        # The Control Centre must remain usable even if an integration or cache
        # snapshot fails. Record the real exception for admins and render a
        # degraded dashboard instead of FastAPI's opaque Internal Server Error.
        try:
            log_event("error", "dashboard", "snapshot_failed", str(exc))
        except Exception:
            pass
        data, age = _empty_dashboard_snapshot(str(exc)), 0
    try:
        readiness = stack_readiness() if user.get("role") == "admin" else None
    except Exception as exc:
        readiness = None
        data["dashboard_error"] = data.get("dashboard_error") or f"Readiness unavailable: {exc}"
    data.update({
        "request": request,
        "dashboard_layout": user.get("dashboard_layout") or "default",
        "readiness": readiness,
        "snapshot_age": age,
    })
    return templates.TemplateResponse("dashboard.html", data)


@app.post("/api/dashboard/refresh")
async def refresh_dashboard(request: Request):
    require_auth(request)
    _data, age = await dashboard_snapshot(force=True)
    return {"ok": True, "age": age}


async def _build_inbox_snapshot() -> dict:
    # v10.4.4 treats ArrNexus-recovered media as a first-class DMM Inbox source.
    # Each top-level recovered folder is one source pack, so separate Tracy
    # Beaker Season 1/2/3 recoveries join existing Season 4/5 provider packs on
    # the same series card instead of living in a disconnected recovery view.
    provider_items, recovered_items = await asyncio.gather(
        asyncio.to_thread(scan_source),
        asyncio.to_thread(scan_media_root, archive_media.extraction_root()),
    )
    by_path = {x.path: x for x in [*provider_items, *recovered_items]}
    items = list(by_path.values())
    raw_rows = await enrich_items(items)
    # RD removal can take a moment to disappear from the mounted source tree.
    # Once ArrNexus has an exact provider deletion success, never put that item
    # back into Waiting while the mount catches up.
    raw_rows = [r for r in raw_rows if r.get("state") != "language_rejected_removed"]
    rows = dedupe_rows([dict(r) for r in raw_rows])
    return {"rows": rows, "raw_rows": raw_rows, "built_at": time.time()}


def _language_attention_row(row: dict) -> bool:
    """Return True only for a source copy that still needs language action.

    A current-policy pass must leave the Language view immediately even when a
    duplicate title has other unresolved provider copies. Grouping for the
    Language tab therefore happens *after* resolved copies are removed.
    """
    return str(row.get("language_badge_key") or "unchecked") in {"fail", "probe_failed", "unknown", "recheck_required"}


@app.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request, q: str = "", status: str = "all", media_type: str = "all", view: str = "grid", refresh: int = 0):
    require_auth(request)
    snapshot, snapshot_age, refreshing = await _INBOX_SNAPSHOT.get(_build_inbox_snapshot, force=bool(refresh))
    enriched = list(snapshot.get("rows") or [])
    raw_filtered = list(snapshot.get("raw_rows") or [])
    if media_type in {"movie", "tv"}:
        enriched = [x for x in enriched if x["item"].media_type == media_type]
        raw_filtered = [x for x in raw_filtered if x["item"].media_type == media_type]
    if q:
        nq = normalize_title(q)
        def _matches_q(x):
            return nq in normalize_title(x.get("display_title") or "") or nq in normalize_title(x["item"].name) or nq in normalize_title(x["item"].title_guess)
        enriched = [x for x in enriched if _matches_q(x)]
        raw_filtered = [x for x in raw_filtered if _matches_q(x)]

    # Build the Language tab from unresolved *source copies* first, then group.
    # This prevents a source that has just passed from lingering in Language
    # merely because it shares a title with another RD copy.
    language_enriched = dedupe_rows([dict(x) for x in raw_filtered if _language_attention_row(x)])
    counts = {
        "all": len(enriched),
        "waiting": sum(1 for x in enriched if x["state"] == "waiting"),
        "imported": sum(1 for x in enriched if x["state"] in {"imported", "linked"}),
        "ignored": sum(1 for x in enriched if x["state"] == "ignored"),
        "duplicate": sum(1 for x in enriched if x.get("duplicate_count", 1) > 1),
        "upgrade": sum(1 for x in enriched if x.get("upgrade")),
        "language": len(language_enriched),
    }
    if status != "all":
        if status == "upgrade":
            enriched = [x for x in enriched if x["upgrade"]]
        elif status == "duplicate":
            enriched = [x for x in enriched if x.get("duplicate_count", 1) > 1]
        elif status == "imported":
            enriched = [x for x in enriched if x["state"] in {"imported", "linked"}]
        elif status == "language":
            enriched = language_enriched
        else:
            enriched = [x for x in enriched if x["state"] == status]
    try:
        archive_rows = await asyncio.to_thread(archive_media.scan_archives, bool(refresh), 500)
        archive_count = sum(1 for row in archive_rows if not row.get("ignored"))
    except Exception:
        archive_count = 0
    return templates.TemplateResponse("inbox.html", {
        "request": request,
        "rows": enriched,
        "archive_count": archive_count,
        "counts": counts,
        "movie_roots": movie_roots(),
        "tv_roots": tv_roots(),
        "q": q, "status": status, "media_type": media_type, "view": view,
        "snapshot_age": snapshot_age, "snapshot_refreshing": refreshing,
    })


def _managed_media_source(value: str | Path) -> bool:
    """Allow normal DMM sources plus ArrNexus-managed recovery output."""
    return is_within_logical(value, source_root()) or is_within_logical(value, archive_media.extraction_root())


@app.get("/item", response_class=HTMLResponse)
async def item_detail(request: Request, path: str, identity_q: str = "", identity_type: str = ""):
    require_auth(request)
    src = Path(path)
    if not _managed_media_source(src):
        raise HTTPException(400, "Invalid source path")
    try:
        if not view_path(src).exists():
            raise HTTPException(404, "Source not found")
        detected_item = await asyncio.to_thread(inspect_item, src)
        item, identity = media_identity.apply_to_item(detected_item)
        routed = await route_item(item)
        meta = routed.get("metadata") or routed.get("existing") or ((routed.get("lookup") or [{}])[0] if routed.get("lookup") else {})
        display_title = (identity or {}).get("title") or meta.get("title") or item.title_guess
        display_year = (identity or {}).get("year") or meta.get("year") or item.year_guess
        identity_results = []
        identity_error = ""
        if identity_q.strip():
            try:
                identity_results = await media_identity.search_tmdb(identity_q.strip(), identity_type or item.media_type, expected_year=item.year_guess)
            except Exception as exc:
                identity_error = str(exc)
        naming_preview = media_identity.naming_preview(item.path, item.fingerprint, identity)
        jf_conn = get_connection("jellyfin")
        try:
            jf = await search_jellyfin(display_title, 10) if jf_conn.api_key else {"configured": False, "found": False, "items": []}
        except Exception as exc:
            jf = {"configured": True, "found": False, "items": [], "error": str(exc)}
        state = (item_states().get(item.path) or {}).get("state", "")
        language = cached_language_result(item.path, item.fingerprint)
        recheck_required = language is None and state in {"language_rejected", "language_issue", "language_review"}
        return templates.TemplateResponse("item.html", {
            "request": request, "item": item, "display_title": display_title, "display_year": display_year,
            "existing": routed["existing"], "instance": routed["existing_instance"], "lookup": routed["lookup"],
            "decision": routed["decision"], "poster": routed["poster"],
            "roots": movie_roots() if item.media_type == "movie" else tv_roots(), "upgrade": routed["upgrade"],
            "existing_resolution": routed["existing_resolution"], "jellyfin": jf, "history": latest_success_for_source(item.path),
            "language_guard": language, "language_policy": load_language_policy(), "language_recheck_required": recheck_required,
            "language_override": language_override(item.path, item.fingerprint),
            "item_state": state,
            "identity": identity, "identity_results": identity_results, "identity_error": identity_error,
            "identity_q": identity_q, "tmdb_configured": media_identity.tmdb_configured(),
            "naming_preview": naming_preview, "detected_item": detected_item,
            "provenance": "Extracted RAR" if is_within_logical(item.path, archive_media.extraction_root()) else "DMM / provider source",
            "can_provider_delete": is_within_logical(item.path, source_root()),
        })
    except HTTPException:
        raise
    except Exception as exc:
        log_event("error", "item", "review_failed", str(exc), {"source": path})
        return templates.TemplateResponse("item_error.html", {
            "request": request, "source_path": path, "source_name": src.name,
            "error": str(exc), "version": APP_VERSION,
        }, status_code=503)


@app.post("/item/language-check")
async def item_language_check(request: Request, source_path: str = Form(...)):
    require_auth(request)
    if not _managed_media_source(source_path):
        raise HTTPException(400, "Invalid source path")
    item = inspect_item(source_path)
    result = await asyncio.to_thread(inspect_source_languages, source_path, item.fingerprint, True)
    status = str(result.get("status") or "unknown")
    if status == "fail":
        set_item_state(source_path, "language_rejected", result.get("summary") or "Language rejected")
    elif status in {"unknown", "probe_failed", "error"}:
        set_item_state(source_path, "language_review", result.get("summary") or "Manual language review required")
    elif status == "pass":
        current = (item_states().get(source_path) or {}).get("state", "")
        if current in {"language_rejected", "language_issue", "language_review"}:
            set_item_state(source_path, "waiting", "Current Language Guard policy passed")
    level = "info" if status == "pass" else "warning"
    log_event(level, "language_guard", "source_checked", result.get("summary") or "Language check complete", {"source": source_path, "status": status})
    _INBOX_SNAPSHOT.clear()
    invalidate_scan_cache()
    return RedirectResponse("/item?path=" + quote(source_path), status_code=303)


@app.post("/item/language-override")
async def item_language_override(request: Request, source_path: str = Form(...), action: str = Form("english")):
    user = require_admin(request)
    if not _managed_media_source(source_path):
        raise HTTPException(400, "Invalid source path")
    item = inspect_item(source_path)
    if action == "english":
        set_language_override(source_path, item.fingerprint, english=True, actor=str(user.get("username") or "administrator"))
        set_item_state(source_path, "waiting", "English audio confirmed manually for this exact source fingerprint")
        add_activity("language_override", item.title_guess, "Administrator confirmed English audio", source_path)
    elif action == "clear":
        set_language_override(source_path, item.fingerprint, english=False, actor=str(user.get("username") or "administrator"))
        set_item_state(source_path, "language_review", "Manual English override cleared; re-check required")
    else:
        raise HTTPException(400, "Invalid language override action")
    _INBOX_SNAPSHOT.clear(); invalidate_scan_cache()
    return RedirectResponse("/item?path=" + quote(source_path), status_code=303)


@app.post("/item/identity")
async def item_identity_save(request: Request):
    require_admin(request)
    form = await request.form()
    source_path = str(form.get("source_path") or "").strip()
    if not _managed_media_source(source_path):
        raise HTTPException(400, "Invalid source path")
    item = inspect_item(source_path)
    action = str(form.get("action") or "save")
    if action == "clear":
        media_identity.clear_identity(source_path, item.fingerprint)
        add_activity("media_identity", item.title_guess, "Cleared source identity override", source_path)
    else:
        payload = {
            "media_type": str(form.get("media_type") or item.media_type),
            "title": str(form.get("title") or "").strip(),
            "year": int(form.get("year") or 0) or None,
            "tmdb_id": int(form.get("tmdb_id") or 0) or None,
            "poster": str(form.get("poster") or ""),
            "overview": str(form.get("overview") or ""),
            "confidence": int(form.get("confidence") or 100),
            "source": "tmdb",
        }
        saved = media_identity.save_identity(source_path, item.fingerprint, payload)
        add_activity("media_identity", saved["title"], f"Resolved source identity via TMDb ({saved.get('confidence', 100)}%)", source_path)
    _INBOX_SNAPSHOT.clear(); invalidate_scan_cache()
    return RedirectResponse("/item?path=" + quote(source_path), status_code=303)


@app.post("/item/state")
async def item_state(request: Request, source_path: str = Form(...), state: str = Form(...), note: str = Form("")):
    require_auth(request)
    if not _managed_media_source(source_path):
        raise HTTPException(400, "Invalid source path")
    if state not in {"waiting", "ignored"}:
        raise HTTPException(400, "Invalid state")
    set_item_state(source_path, state, note)
    add_activity("state", Path(source_path).name, f"Marked {state}", source_path)
    return RedirectResponse("/inbox", status_code=303)


def _parse_destination_spec(value: str, detected_media_type: str = "") -> tuple[str, str]:
    """Return (media_type_override, destination_key) for explicit routes.

    v10.3 namespaces manual routes (tv:bbc, movie:kids) so a user's
    explicit media-type choice cannot be silently reinterpreted by scanner
    heuristics. Legacy unprefixed values remain supported.
    """
    raw = str(value or "auto").strip().lower()
    if raw == "auto":
        return "", "auto"
    if ":" in raw:
        kind, key = raw.split(":", 1)
        if kind in {"movie", "tv"} and key:
            return kind, key
    return "", raw


async def run_import_job(job_id: int):
    job, job_items = get_job(job_id)
    if not job:
        return
    update_job(job_id, status="running", message="Import in progress")
    completed = failed = rejected = reviewed = 0
    for ji in job_items:
        iid = int(ji["id"])
        source_path = ji["source_path"]
        try:
            update_job_item(iid, status="running", stage="identifying", message="Identifying and routing")
            item = inspect_item(source_path)
            update_job_item(iid, stage="matching", message=f"Matching {item.media_type} in Arr")
            raw_dest = str(ji.get("destination_key") or "auto")
            override, dest = _parse_destination_spec(raw_dest, item.media_type)
            if not dest or dest == "auto":
                routed = await route_item(item)
                dest = routed["decision"].key
                update_job_item(iid, destination_key=dest, stage="linking", message=f"Auto routing to {item.media_type}:{dest}")
            else:
                selected_type = override or item.media_type
                update_job_item(iid, destination_key=f"{selected_type}:{dest}", stage="linking", message=f"Manual route {selected_type}:{dest}")
            result = await import_one(source_path, dest, media_type_override=override or None)
            update_job_item(iid, status="complete", stage="complete", message=f"Imported to {result['arr_instance']} / {result['destination_key']}")
            log_event("info","import","item_complete",Path(source_path).name,{"job_id":job_id,"destination":result.get("destination_key"),"arr_instance":result.get("arr_instance")})
            completed += 1
        except LanguageRejectedSafe as exc:
            cleanup = getattr(exc, "cleanup", {}) or {}
            manual_review = bool(getattr(exc, "manual_review", False))
            if manual_review:
                cleanup_text = "Manual review required · source retained"
                stage = "language_review"
                event = "item_review"
            else:
                cleanup_text = "Rejected Debrid source removed" if cleanup.get("deleted") else "Rejected source retained"
                stage = "language_rejected"
                event = "item_rejected"
            # Manual Review is a protected/uncertain outcome, not a confirmed
            # language rejection. Keep the item blocked, but report it separately.
            update_job_item(iid, status="review" if manual_review else "rejected", stage=stage, message=f"{str(exc)} · {cleanup_text}")
            log_event("warning","language_guard",event,str(exc),{"job_id":job_id,"source":source_path,"provider_deleted":bool(cleanup.get("deleted")),"manual_review":manual_review})
            if manual_review:
                reviewed += 1
            else:
                rejected += 1
        except Exception as exc:
            update_job_item(iid, status="error", stage="error", message=str(exc))
            log_event("error","import","item_failed",str(exc),{"job_id":job_id,"source":source_path})
            failed += 1
        _INBOX_SNAPSHOT.clear()
        invalidate_scan_cache()
        invalidate_library_cache()
        update_job(job_id, completed=completed, failed=failed, rejected=rejected, reviewed=reviewed, message=f"{completed} complete, {reviewed} manual review, {rejected} language rejected, {failed} failed")
    final_status = "complete_with_errors" if failed else ("complete_with_rejections" if rejected else ("complete_with_reviews" if reviewed else "complete"))
    update_job(job_id, status=final_status, completed=completed, failed=failed, rejected=rejected, reviewed=reviewed, message=f"Finished: {completed} complete, {reviewed} manual review, {rejected} language rejected, {failed} failed")
    log_event("warning" if (failed or rejected or reviewed) else "info","import","job_finished",f"Job #{job_id}: {completed} complete, {reviewed} manual review, {rejected} language rejected, {failed} failed",{"job_id":job_id,"completed":completed,"reviewed":reviewed,"rejected":rejected,"failed":failed})
    try:
        await send_notification(
            f"ArrNexus import job #{job_id}",
            f"{completed} completed, {reviewed} manual review, {rejected} language rejected, {failed} failed.",
            "warning" if (failed or rejected or reviewed) else "info",
            "import_job",
        )
    except Exception:
        pass


def _launch(coro):
    task = asyncio.create_task(coro)
    RUNNING_TASKS.add(task)
    task.add_done_callback(RUNNING_TASKS.discard)
    return task


async def run_language_scan_job(job_id: int, force: bool = False):
    job, job_items = get_job(job_id)
    if not job:
        return
    update_job(job_id, status="running", message="Language Guard scan in progress")
    completed = failed = rejected = reviewed = 0
    for ji in job_items:
        iid = int(ji["id"])
        source_path = str(ji.get("source_path") or "")
        try:
            update_job_item(iid, status="running", stage="language_probe", message="Checking media streams with ffprobe")
            item = await asyncio.to_thread(inspect_item, source_path)
            result = await asyncio.to_thread(inspect_source_languages, source_path, item.fingerprint, force)
            status = str(result.get("status") or "unknown")
            if status == "fail":
                rejected += 1
                set_item_state(source_path, "language_rejected", result.get("summary") or "Language rejected")
                update_job_item(iid, status="rejected", stage="language_rejected", message=result.get("summary") or "Language rejected")
            elif status == "unknown":
                reviewed += 1
                set_item_state(source_path, "language_review", result.get("summary") or "Manual language review required")
                update_job_item(iid, status="review", stage="language_review", message=result.get("summary") or "Manual review required")
            else:
                completed += 1
                # A successful current-policy check clears stale language-only state.
                current = (item_states().get(source_path) or {}).get("state")
                if current in {"language_rejected", "language_issue", "language_review"}:
                    set_item_state(source_path, "waiting", "Current Language Guard policy passed")
                update_job_item(iid, status="complete", stage="language_pass", message=result.get("summary") or "English verified")
            log_event("info" if status == "pass" else "warning", "language_guard", "bulk_source_checked", result.get("summary") or "Language check complete", {"source": source_path, "status": status})
        except Exception as exc:
            failed += 1
            update_job_item(iid, status="error", stage="error", message=str(exc))
            log_event("error", "language_guard", "bulk_source_failed", str(exc), {"source": source_path})
        _INBOX_SNAPSHOT.clear()
        update_job(job_id, completed=completed, failed=failed, rejected=rejected, reviewed=reviewed, message=f"{completed} verified, {reviewed} manual review, {rejected} rejected, {failed} failed")
    final = "complete_with_errors" if failed else ("complete_with_rejections" if rejected else ("complete_with_reviews" if reviewed else "complete"))
    update_job(job_id, status=final, completed=completed, failed=failed, rejected=rejected, reviewed=reviewed, message=f"Finished: {completed} verified, {reviewed} manual review, {rejected} language rejected, {failed} failed")


async def run_language_cleanup_job(job_id: int):
    job, job_items = get_job(job_id)
    if not job:
        return
    update_job(job_id, status="running", message="Checking rejected-source deletion safety")
    completed = failed = rejected = 0
    for ji in job_items:
        iid = int(ji["id"])
        source_path = str(ji.get("source_path") or "")
        try:
            update_job_item(iid, status="running", stage="dependency_check", message="Revalidating language decision and library dependencies")
            item = await asyncio.to_thread(inspect_item, source_path)
            result = cached_language_result(source_path, item.fingerprint)
            if not result or str(result.get("status") or "") != "fail":
                raise RuntimeError("Deletion refused: this source does not have a current-policy Language rejected result. Re-check it first.")
            if not bool(result.get("destructive_safe")):
                raise RuntimeError("Deletion refused: the language result is not destructive-safe")
            links = await asyncio.to_thread(build_source_link_index, 200000, True)
            dependants = list(links.get(source_path) or [])
            if dependants:
                raise RuntimeError(f"Deletion refused: {len(dependants)} surviving managed library link(s) still depend on this source")
            update_job_item(iid, stage="provider_match", message="Resolving one exact Real-Debrid torrent identity")
            cleanup = await rd.delete_source_torrent_exact(source_path, item.size_bytes)
            if not cleanup.get("deleted"):
                raise RuntimeError(cleanup.get("reason") or "Exact Real-Debrid deletion did not complete")
            set_item_state(source_path, "language_rejected_removed", f"Exact Real-Debrid source removed: {cleanup.get('torrent_id')}")
            completed += 1
            update_job_item(iid, status="complete", stage="provider_deleted", message=f"Deleted exact Real-Debrid torrent {cleanup.get('torrent_id')}")
            add_activity("language_cleanup", item.title_guess, "Rejected exact Real-Debrid source removed", source_path)
        except Exception as exc:
            failed += 1
            update_job_item(iid, status="error", stage="protected", message=str(exc))
            log_event("warning", "language_guard", "manual_cleanup_refused", str(exc), {"source": source_path})
        invalidate_scan_cache(); invalidate_library_cache(); _INBOX_SNAPSHOT.clear()
        update_job(job_id, completed=completed, failed=failed, rejected=rejected, message=f"{completed} deleted, {failed} protected/failed")
    final = "complete_with_errors" if failed else "complete"
    update_job(job_id, status=final, completed=completed, failed=failed, rejected=0, message=f"Finished: {completed} exact rejected source(s) deleted, {failed} protected/failed")


@app.post("/inbox/language-scan")
async def inbox_language_scan(request: Request):
    require_auth(request)
    form = await request.form()
    scope = str(form.get("scope") or "selected").strip().lower()
    force = str(form.get("force") or "").lower() in {"1", "true", "yes", "on"}
    requested = [str(x) for x in form.getlist("source_path")]
    states = item_states()
    candidates = []
    if scope in {"unchecked", "all"}:
        for item in await asyncio.to_thread(scan_source):
            current = cached_language_result(item.path, item.fingerprint)
            state = (states.get(item.path) or {}).get("state", "")
            recheck_required = not current and state in {"language_rejected", "language_issue", "language_review"}
            if scope == "all" or current is None or recheck_required:
                candidates.append(item.path)
    else:
        candidates = requested
    valid = []
    seen = set()
    for path in candidates:
        if path in seen or not is_within_logical(path, source_root()):
            continue
        seen.add(path)
        valid.append({"source_path": path, "display_name": Path(path).name, "destination_key": "language"})
    if not valid:
        return RedirectResponse("/inbox?notice=" + quote("No sources need a language check"), status_code=303)
    jid = create_job("language_scan", valid)
    _launch(run_language_scan_job(jid, force=force))
    return RedirectResponse(f"/jobs/{jid}", status_code=303)


@app.post("/inbox/language-delete")
async def inbox_language_delete(request: Request):
    require_auth(request)
    form = await request.form()
    single = str(form.get("single_source_path") or "").strip()
    paths = [single] if single else [str(x) for x in form.getlist("source_path")]
    valid = []
    seen = set()
    for path in paths:
        if path in seen or not is_within_logical(path, source_root()):
            continue
        seen.add(path)
        valid.append({"source_path": path, "display_name": Path(path).name, "destination_key": "rd-exact-delete"})
    if not valid:
        raise HTTPException(400, "No valid rejected sources selected")
    jid = create_job("language_cleanup", valid)
    _launch(run_language_cleanup_job(jid))
    return RedirectResponse(f"/jobs/{jid}", status_code=303)


@app.post("/bulk-import")
async def bulk_import(request: Request):
    require_auth(request)
    form = await request.form()
    paths = [str(x) for x in form.getlist("source_path")]
    destination = str(form.get("destination_key") or "auto")
    if not paths:
        return RedirectResponse("/inbox", status_code=303)
    valid = []
    for p in paths:
        if _managed_media_source(p):
            valid.append({"source_path": p, "display_name": Path(p).name, "destination_key": destination})
    if not valid:
        raise HTTPException(400, "No valid source paths")
    jid = create_job("bulk_import", valid)
    _launch(run_import_job(jid))
    log_event("info","import","bulk_job_started",f"Import job #{jid} started with {len(valid)} item(s)",{"job_id":jid,"destination":destination})
    if request.headers.get("x-requested-with") == "ArrNexus" or "application/json" in request.headers.get("accept",""):
        return JSONResponse({"ok":True,"job_id":jid,"total":len(valid),"url":f"/jobs/{jid}"})
    return RedirectResponse(f"/jobs/{jid}", status_code=303)


@app.post("/import")
async def single_import(
    request: Request, source_path: str = Form(...), destination_key: str = Form("auto"),
    title_override: str = Form(""), identity_media_type: str = Form(""), year_override: str = Form(""), tmdb_id: str = Form(""),
):
    require_auth(request)
    if not _managed_media_source(source_path):
        raise HTTPException(400, "Invalid source path")
    if title_override.strip():
        item = inspect_item(source_path)
        try:
            year = int(year_override or 0) or None
        except Exception:
            year = None
        try:
            tid = int(tmdb_id or 0) or None
        except Exception:
            tid = None
        media_type = str(identity_media_type or item.media_type).lower()
        if media_type not in {"movie", "tv"}:
            media_type = item.media_type
        media_identity.save_identity(source_path, item.fingerprint, {
            "media_type": media_type, "title": title_override.strip(), "year": year, "tmdb_id": tid,
            "source": "tmdb" if tid else "administrator", "confidence": 100,
        })
    jid = create_job("import", [{"source_path": source_path, "display_name": Path(source_path).name, "destination_key": destination_key}])
    _launch(run_import_job(jid))
    log_event("info","import","job_started",f"Import job #{jid} started",{"job_id":jid,"source":source_path,"destination":destination_key})
    if request.headers.get("x-requested-with") == "ArrNexus" or "application/json" in request.headers.get("accept",""):
        return JSONResponse({"ok":True,"job_id":jid,"total":1,"url":f"/jobs/{jid}"})
    return RedirectResponse(f"/jobs/{jid}", status_code=303)


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request):
    require_auth(request)
    return templates.TemplateResponse("jobs.html", {"request": request, "jobs": recent_jobs(50)})


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, job_id: int):
    require_auth(request)
    job, items = get_job(job_id)
    if not job:
        raise HTTPException(404)
    return templates.TemplateResponse("job.html", {"request": request, "job": job, "items": items})


@app.get("/api/jobs/{job_id}")
async def job_api(request: Request, job_id: int):
    require_auth(request)
    job, items = get_job(job_id)
    if not job:
        raise HTTPException(404)
    return {"job": job, "items": items}


@app.get("/api/jobs-active")
async def active_jobs_api(request: Request):
    require_auth(request)
    rows=[dict(x) for x in recent_jobs(20) if x["status"] in {"queued","running"}]
    return {"jobs":rows}



@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, level: str = "all", source: str = "all", q: str = "", origin: str = "arrnexus", process: str = "DUMB"):
    require_auth(request)
    external_error = ""
    if origin in {"dumb", "infinidysk"}:
        rows, external_error = await external_log_rows(origin, process, 1200)
        if level != "all": rows = [r for r in rows if r.get("level") == level]
        if q:
            nq=q.lower(); rows=[r for r in rows if nq in str(r.get("message") or "").lower() or nq in str(r.get("event") or "").lower()]
    else:
        rows=list_logs(level,source,q,800)
    rows=attach_explanations([dict(r) for r in rows])
    counts={k:sum(1 for r in rows if r.get("level")==k) for k in ("debug","info","warning","error","critical")}
    processes=["DUMB","NzbWebDAV","Rclone w/ NzbDAV"]
    try:
        processes += [f"{i.service.capitalize()} {i.instance}" for i in discover_instances()]
    except Exception: pass
    processes=list(dict.fromkeys(processes))
    return templates.TemplateResponse("logs.html",{
        "request":request,"rows":rows,"level":level,"source":source,"q":q,"sources":log_sources(),
        "origin":origin,"process":process,"processes":processes,"external_error":external_error,"counts":counts,
    })

@app.get("/api/logs/external")
async def api_external_logs(request: Request, origin: str = "dumb", process: str = "DUMB", level: str = "all", q: str = ""):
    require_auth(request)
    rows,error=await external_log_rows(origin,process,1200)
    if level != "all": rows=[r for r in rows if r.get("level")==level]
    if q:
        nq=q.lower(); rows=[r for r in rows if nq in str(r.get("message") or "").lower()]
    return {"rows":attach_explanations(rows),"error":error}


@app.post("/undo/{import_id}")
async def undo_import(request: Request, import_id: int):
    require_auth(request)
    rows = [dict(x) for x in recent_imports(10000) if int(x["id"]) == import_id]
    if not rows:
        raise HTTPException(404)
    row = rows[0]
    try:
        paths = json.loads(row.get("created_paths") or "[]")
    except Exception:
        paths = []
    removed, errors = unlink_created(paths)
    invalidate_library_cache()
    note = f"Removed {removed} router-created symlink(s)" + (f"; errors: {'; '.join(errors)}" if errors else "")
    mark_import_undone(import_id, note)
    add_activity("undo", row.get("source_name") or "Import", note, row.get("source_path") or "")
    return RedirectResponse("/inbox?status=imported", status_code=303)


@app.get("/libraries", response_class=HTMLResponse)
async def libraries_page(request: Request):
    require_auth(request)
    return templates.TemplateResponse("libraries.html", {"request": request, "libraries": inventory_roots()})


def _allowed_browser_path(path: str) -> bool:
    return any(is_within_logical(path,m.get("logical_path") or "") for m in list_mounts(True) if m.get("logical_path"))


@app.get("/browser", response_class=HTMLResponse)
async def browser_page(request: Request, path: str = ""):
    require_auth(request)
    path=path or next((m.get("logical_path") for m in list_mounts(True) if m.get("kind") in {"movie","tv","music"}), source_root())
    if not _allowed_browser_path(path): raise HTTPException(400,"Path is outside the registered ArrNexus libraries")
    try:
        actual=view_path(path)
        if not actual.exists() or not actual.is_dir(): raise HTTPException(404,"Directory not found")
        entries=[]
        for x in sorted(actual.iterdir(),key=lambda p:(not p.is_dir(),p.name.lower()))[:1000]:
            logical=str(Path(path)/x.name)
            try:
                stat=x.stat() if not x.is_symlink() else x.lstat()
                size=stat.st_size
            except OSError: size=0
            entries.append({"name":x.name,"path":logical,"is_dir":x.is_dir(),"is_symlink":x.is_symlink(),"size":size})
        parent=str(Path(path).parent) if str(Path(path).parent)!=path else ""
        if parent and not _allowed_browser_path(parent): parent=""
        return templates.TemplateResponse("browser.html",{"request":request,"path":path,"entries":entries,"parent":parent,"mounts":list_mounts(True)})
    except NamespaceError as exc:
        raise HTTPException(503,str(exc))


@app.get("/browser/file")
async def browser_file(request: Request, path: str):
    require_auth(request)
    if not _allowed_browser_path(path): raise HTTPException(400,"Path is outside the registered ArrNexus libraries")
    actual=view_path(path)
    if not actual.exists() or not actual.is_file(): raise HTTPException(404)
    return FileResponse(actual,filename=Path(path).name)


async def _build_problems_snapshot() -> dict:
    async def service_probe(name, client):
        result = await arr_status(client, 3.5)
        return {"name": name, "ok": bool(result.get("ok")), "detail": result.get("version") or result.get("error") or "Connected"}

    ns_task = asyncio.to_thread(namespace_status)
    broken_task = _BROKEN_LINK_SNAPSHOT.get(lambda: asyncio.to_thread(scan_broken_symlinks, 500))
    services_task = asyncio.gather(
        service_probe("Radarr", RadarrClient()), service_probe("Sonarr", SonarrClient()),
        service_probe("Lidarr", LidarrClient()), service_probe("Prowlarr", ProwlarrClient()),
    )
    ns_result, broken_result, service_rows = await asyncio.gather(ns_task, broken_task, services_task, return_exceptions=True)
    ns = ns_result if isinstance(ns_result, dict) else {"ok": False, "error": str(ns_result)}
    if isinstance(broken_result, Exception):
        broken = []
    else:
        broken_value = broken_result[0] if isinstance(broken_result, tuple) else broken_result
        broken = list(broken_value or [])[:250]
    problems = []
    if not ns.get("ok"):
        problems.append({"severity":"critical","kind":"Namespace","title":"DUMB namespace unavailable","detail":ns.get("error") or "Mount namespace could not be resolved","href":"/maintenance"})
    if isinstance(service_rows, Exception):
        service_error = str(service_rows)
        service_rows = []
        problems.append({"severity":"warning","kind":"Connection","title":"Service health check failed","detail":service_error,"href":"/arrs"})
    for row in service_rows:
        if not row.get("ok"):
            problems.append({"severity":"error","kind":"Connection","title":f"{row['name']} unavailable","detail":row.get("detail") or "Connection failed","href":"/arrs"})
    if isinstance(broken_result, Exception):
        problems.append({"severity":"warning","kind":"Maintenance","title":"Broken-link scan failed","detail":str(broken_result),"href":"/maintenance"})
    elif broken:
        problems.append({"severity":"error","kind":"Library","title":f"{len(broken)} broken symlink(s)","detail":"Open Maintenance to inspect and repair links.","href":"/maintenance"})
    failed_jobs = []
    for j in recent_jobs(50):
        if int(j["failed"] or 0) > 0 or j["status"] in {"failed", "error"}:
            failed_jobs.append(dict(j))
    if failed_jobs:
        problems.append({"severity":"error","kind":"Import","title":f"{len(failed_jobs)} recent import job(s) with failures","detail":"Open Import Jobs for per-item reasons.","href":"/jobs"})
    error_logs = list_logs("error", "all", "", 30)
    score = 100
    score -= min(35, sum(15 for x in service_rows if not x["ok"]))
    score -= min(25, len(broken) * 2)
    score -= min(25, sum(int(x.get("failed") or 0) for x in failed_jobs) * 3)
    if not ns.get("ok"):
        score -= 30
    return {"problems": problems, "services": service_rows, "broken": broken, "failed_jobs": failed_jobs, "error_logs": error_logs, "health_score": max(0, score), "namespace": ns, "built_at": time.time()}


@app.get("/problems", response_class=HTMLResponse)
async def problems_page(request: Request, refresh: int = 0):
    require_auth(request)
    data, age, refreshing = await _PROBLEMS_SNAPSHOT.get(_build_problems_snapshot, force=bool(refresh))
    return templates.TemplateResponse("problems.html", {"request": request, **data, "snapshot_age": age, "snapshot_refreshing": refreshing})


async def _build_maintenance_snapshot() -> dict:
    try:
        broken_task = _BROKEN_LINK_SNAPSHOT.get(lambda: asyncio.to_thread(scan_broken_symlinks, 500))
        items_task = asyncio.to_thread(scan_source)
        links_task = asyncio.to_thread(build_source_link_index)
        imports_task = asyncio.to_thread(latest_import_by_source)
        broken_result, items, links, imports = await asyncio.gather(broken_task, items_task, links_task, imports_task)
        broken = broken_result[0] if isinstance(broken_result, tuple) else broken_result
        broken = list(broken or [])
        orphans = [x for x in items if x.path not in links and not ((imports.get(x.path) or {}).get("status") in {"complete", "linked"} and not (imports.get(x.path) or {}).get("undone"))]
        return {"broken": broken, "orphans": orphans, "error": None, "built_at": time.time()}
    except Exception as exc:
        log_event("warning", "maintenance", "scan_unavailable", str(exc))
        return {"broken": [], "orphans": [], "error": str(exc), "built_at": time.time()}


@app.get("/maintenance", response_class=HTMLResponse)
async def maintenance_page(request: Request, refresh: int = 0):
    require_auth(request)
    data, age, refreshing = await _MAINTENANCE_SNAPSHOT.get(_build_maintenance_snapshot, force=bool(refresh))
    return templates.TemplateResponse("maintenance.html", {"request": request, **data, "snapshot_age": age, "snapshot_refreshing": refreshing})


@app.post("/maintenance/repair")
async def repair_link(request: Request, path: str = Form(...)):
    require_auth(request)
    ok, msg = repair_broken_symlink(path)
    add_activity("repair" if ok else "repair_error", Path(path).name, msg, path)
    return RedirectResponse("/maintenance", status_code=303)


@app.get("/maintenance/consolidation", response_class=HTMLResponse)
async def consolidation_page(request: Request, notice: str = ""):
    require_admin(request)
    try:
        preview = await asyncio.to_thread(scan_consolidation)
        error = ""
    except Exception as exc:
        preview = {"symlinks_scanned": 0, "duplicate_groups": 0, "recommended_removals": 0, "groups": [], "digest": ""}
        error = str(exc)
        log_event("error", "consolidation", "scan_failed", error)
    return templates.TemplateResponse("consolidation.html", {"request": request, "preview": preview, "error": error, "notice": notice})


@app.post("/maintenance/consolidation/apply")
async def consolidation_apply(request: Request, digest: str = Form(...), remove_provider_sources: str = Form("false")):
    require_admin(request)
    remove_provider = str(remove_provider_sources).lower() in {"1", "true", "yes", "on"}
    try:
        result = await asyncio.to_thread(apply_consolidation, digest, remove_provider)
        _INBOX_SNAPSHOT.clear(); _MAINTENANCE_SNAPSHOT.clear(); _BROKEN_LINK_SNAPSHOT.clear()
        log_event("warning" if result.get("errors") else "info", "consolidation", "applied", f"Removed {result.get('removed_count',0)} redundant symlink(s)", {"provider_cleanup": len(result.get("provider_cleanup") or []), "errors": len(result.get("errors") or [])})
        deleted = sum(1 for x in (result.get("provider_cleanup") or []) if x.get("deleted"))
        notice = f"Removed {result.get('removed_count',0)} redundant symlink(s); {len(result.get('orphaned_sources') or [])} source(s) became unreferenced; {deleted} Real-Debrid source(s) removed"
    except Exception as exc:
        log_event("error", "consolidation", "apply_failed", str(exc))
        notice = "Consolidation not applied: " + str(exc)
    return RedirectResponse("/maintenance/consolidation?notice=" + quote(notice), status_code=303)


@app.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request):
    require_auth(request)
    return templates.TemplateResponse("rules.html", {"request": request, "rules": list_rules(), "movie_roots": movie_roots(), "tv_roots": tv_roots()})


@app.post("/rules/add")
async def rule_add(request: Request, media_type: str = Form(...), field: str = Form(...), pattern: str = Form(...), destination_key: str = Form(...), weight: int = Form(90)):
    require_auth(request)
    if media_type not in {"movie", "tv"} or field not in {"title", "normalized_title", "genre", "network", "studio"}:
        raise HTTPException(400)
    save_rule(media_type, field, pattern, destination_key, weight)
    add_activity("rule", pattern, f"{media_type}/{field} → {destination_key}")
    return RedirectResponse("/rules", status_code=303)


@app.post("/rules/delete/{rule_id}")
async def rule_delete(request: Request, rule_id: int):
    require_auth(request)
    delete_rule(rule_id)
    return RedirectResponse("/rules", status_code=303)


@app.get("/arrs", response_class=HTMLResponse)
async def arrs_page(request: Request, notice: str = ""):
    require_auth(request)
    instances = discover_instances()

    async def _load_arr(inst):
        try:
            client = client_for_instance(inst)
            status, roots, tags = await asyncio.gather(client.status(), client.roots(), client.tags())
            return {"kind": "arr", "instance": inst, "ok": True, "status": status, "roots": roots or [], "tags": tags or [], "url": inst.url, "has_key": bool(inst.api_key)}
        except Exception as exc:
            return {"kind": "arr", "instance": inst, "ok": False, "error": str(exc), "roots": [], "tags": [], "url": inst.url, "has_key": bool(inst.api_key)}

    async def _load_prowlarr():
        pc = get_connection("prowlarr")
        try:
            ps = await ProwlarrClient().status()
            return {"kind":"prowlarr","service":"prowlarr","instance_name":"main","ok":True,"status":ps,"roots":[],"tags":[],"url":pc.url,"has_key":bool(pc.api_key)}
        except Exception as exc:
            return {"kind":"prowlarr","service":"prowlarr","instance_name":"main","ok":False,"error":str(exc),"roots":[],"tags":[],"url":pc.url,"has_key":bool(pc.api_key)}

    async def _load_jellyfin():
        jc = get_connection("jellyfin")
        try:
            js = await jellyfin_status()
            return {"kind":"jellyfin","service":"jellyfin","instance_name":"main","ok":True,"status":{"version":js.get("Version") or js.get("version")},"roots":[],"tags":[],"url":jc.url,"has_key":bool(jc.api_key)}
        except Exception as exc:
            return {"kind":"jellyfin","service":"jellyfin","instance_name":"main","ok":False,"status":{},"roots":[],"tags":[],"url":jc.url,"has_key":bool(jc.api_key),"error":str(exc)}

    async def _load_media_server(kind: str):
        state = media_server_builtin_state(kind)
        conn = get_connection(kind)
        result = await probe_media_server(kind, conn.url, conn.api_key)
        return {
            "kind": "media", "service": kind, "instance_name": "main",
            "ok": bool(result.get("ok") and conn.api_key), "status": {"version": result.get("version") or result.get("detail") or ""},
            "roots": [], "tags": [], "url": conn.url, "has_key": bool(conn.api_key),
            "error": result.get("error") or "", "media_name": state.get("name"),
            "media_description": state.get("description"), "token_label": state.get("token_label"),
        }

    async def _load_custom_media(row):
        result = await probe_custom_media_server(row)
        return {**row, "kind": "media_custom", "ok": bool(result.get("ok")), "error": result.get("error") or "", "detail": result.get("detail") or "", "status_code": result.get("status_code"), "content_type": result.get("content_type") or ""}

    async def _load_seerr():
        sc = get_connection("seerr")
        try:
            ss = await SeerrClient().status()
            return {"kind":"seerr","service":"seerr","instance_name":"main","ok":True,"status":ss,"roots":[],"tags":[],"url":sc.url,"has_key":bool(sc.api_key)}
        except Exception as exc:
            return {"kind":"seerr","service":"seerr","instance_name":"main","ok":False,"status":{},"roots":[],"tags":[],"url":sc.url,"has_key":bool(sc.api_key),"error":str(exc)}

    custom_media = list_custom_media_servers(mask=True)
    rows = list(await asyncio.gather(
        *(_load_arr(i) for i in instances),
        _load_prowlarr(), _load_media_server("jellyfin"), _load_media_server("plex"), _load_media_server("emby"), _load_seerr(),
    ))
    custom_rows = list(await asyncio.gather(*(_load_custom_media(row) for row in custom_media))) if custom_media else []
    return templates.TemplateResponse("arrs.html", {"request": request, "rows": rows, "custom_media": custom_rows, "notice": notice})


@app.post("/settings/connection")
async def save_connection_route(request: Request, service: str = Form(...), instance: str = Form("main"), url: str = Form(...), api_key: str = Form("")):
    require_admin(request)
    service = service.lower().strip(); instance = instance.strip() or "main"
    if service not in {"radarr","sonarr","lidarr","prowlarr","jellyfin","plex","emby","seerr"}:
        raise HTTPException(400, "Unsupported service")
    save_connection(service, url, api_key, instance)
    invalidate_instance_cache()
    # Main DUMB instances are named nzbdav; keep the generic connection in sync
    # so Dashboard/Discover clients use the same credentials.
    if instance == "nzbdav" and service in {"radarr","sonarr","lidarr"}:
        save_connection(service, url, api_key, "main")
    add_activity("settings", service.title(), f"Updated {instance} connection")
    return RedirectResponse(f"/arrs?notice={quote(f'{service.title()} / {instance} saved')}", status_code=303)



@app.post("/media-servers/custom")
async def save_custom_media_server_route(request: Request, name: str = Form(...), url: str = Form(...), health_path: str = Form("/"), auth_mode: str = Form("none"), auth_name: str = Form("Authorization"), secret_value: str = Form(""), media_id: str = Form("")):
    require_admin(request)
    try:
        save_custom_media_server(name, url, health_path, auth_mode, auth_name, secret_value, media_id)
        log_event("info", "media_server", "custom_saved", f"External media server {name} saved")
        return RedirectResponse("/arrs?notice=" + quote(f"{name} media server saved"), status_code=303)
    except Exception as exc:
        return RedirectResponse("/arrs?notice=" + quote(f"Could not save media server: {exc}"), status_code=303)


@app.post("/media-servers/custom/{media_id}/delete")
async def delete_custom_media_server_route(request: Request, media_id: str):
    require_admin(request)
    delete_custom_media_server(media_id)
    log_event("warning", "media_server", "custom_removed", f"External media server {media_id} removed")
    return RedirectResponse("/arrs?notice=" + quote("External media server removed"), status_code=303)


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, notice: str = "", error: str = ""):
    require_auth(request)
    uid=int(request.session.get("user_id") or 0)
    user=get_user(uid) if uid else None
    if not user:
        request.session.clear()
        return RedirectResponse("/setup" if user_count()==0 else "/login?notice="+quote("Please sign in again to open your profile"),status_code=303)
    return templates.TemplateResponse("profile.html", {"request":request,"user":user,"notice":notice,"error":error})


@app.post("/profile")
async def profile_save(request: Request, username: str = Form(...), email: str = Form(""), display_name: str = Form(""), dashboard_layout: str = Form("default"), password: str = Form("")):
    require_auth(request)
    uid = int(request.session.get("user_id") or 0)
    try:
        update_user(uid, username, email, display_name or username, "arrnexus", dashboard_layout, password)
        request.session["theme"] = "arrnexus"
        request.session["display_name"] = display_name or username
        return RedirectResponse(f"/profile?notice={quote('Profile saved')}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/profile?error={quote(str(exc))}", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, notice: str = ""):
    require_admin(request)
    return templates.TemplateResponse("settings.html", {
        "request":request,"notice":notice,"rd_connected":rd.connected(),
        "smtp":{k:setting_get(f"smtp.{k}") for k in ("host","port","username","from_address","starttls")},
        "app_title":setting_get("app.title","ArrNexus"),"public_url":setting_get("app.public_url", ""),"users":list_users(),"mounts":list_mounts(False),
        "dumb_root":dumb_root(),
        "soundcloud_configured":bool(setting_get("music.soundcloud.client_id") and setting_get("music.soundcloud.client_secret")),
        "jamendo_client_id":setting_get("music.jamendo.client_id",""),
        "lastfm_configured":bool(setting_get("music.lastfm.api_key")),
        "spotify_configured":bool(setting_get("music.spotify.client_id") and setting_get("music.spotify.client_secret")),
        "spotify_redirect_uri":setting_get("music.spotify.redirect_uri", "") or _suggested_spotify_redirect_uri(request),
        "update_channel":setting_get("update.channel", "beta") or "beta",
        "seerr":get_connection("seerr"),
        "policy": load_policy(),
        "notifications": {
            "enabled": setting_get("notify.enabled","false"), "failures_only": setting_get("notify.failures_only","false"),
            "ntfy_server": setting_get("notify.ntfy.server",""), "ntfy_topic": setting_get("notify.ntfy.topic",""),
            "ntfy_token_set": bool(setting_get("notify.ntfy.token","")), "gotify_url": setting_get("notify.gotify.url",""),
            "gotify_token_set": bool(setting_get("notify.gotify.token","")), "discord_set": bool(setting_get("notify.discord.webhook","")),
            "email_to": setting_get("notify.email.to",""),
        },
        "backups": list_backups(10), "backup_auto": setting_get("backup.auto_enabled","true"), "backup_retention": setting_get("backup.retention","10"),
        "update_repo": setting_get("update.repo", UPDATE_DEFAULT_REPOSITORY) or UPDATE_DEFAULT_REPOSITORY, "version": APP_VERSION, "self_update_capable": SELF_UPDATE_CAPABLE, "update_state": update_status(), "catalog_plugins": load_catalog_plugins(),
        "acquisition": load_acquisition_settings(), "acquisition_strategies": STRATEGIES,
        "language_policy": load_language_policy(),
    })


@app.post("/settings/path-root")
async def settings_path_root(request: Request, dumb_root_value: str = Form(...)):
    require_admin(request)
    value=dumb_root_value.strip().rstrip('/') or '/mnt/debrid'
    if not value.startswith('/'):
        return RedirectResponse('/settings?notice='+quote('DUMB root must be an absolute path'),status_code=303)
    setting_set('paths.dumb_root',value)
    try:
        from .namespace import refresh_anchor_pid
        refresh_anchor_pid()
    except Exception:
        pass
    log_event('info','settings','dumb_root_updated',value)
    return RedirectResponse('/settings?notice='+quote('DUMB mount root saved'),status_code=303)


@app.post("/settings/mount/add")
async def settings_mount_add(request: Request, name: str = Form(...), logical_path: str = Form(...), kind: str = Form('library'), service: str = Form(''), destination_key: str = Form('')):
    require_admin(request)
    try:
        save_mount(name,logical_path,kind,service,destination_key)
        log_event('info','settings','mount_added',f'{name}: {logical_path}',{'kind':kind,'service':service,'destination':destination_key})
        return RedirectResponse('/settings?notice='+quote('Library path added'),status_code=303)
    except Exception as exc:
        return RedirectResponse('/settings?notice='+quote('Could not add path: '+str(exc)),status_code=303)


@app.post("/settings/mount/delete/{mount_id}")
async def settings_mount_delete(request: Request, mount_id: int):
    require_admin(request)
    delete_mount(mount_id); log_event('warning','settings','mount_removed',f'Mount #{mount_id} removed')
    return RedirectResponse('/settings?notice='+quote('Library path removed'),status_code=303)


@app.get("/music/settings", response_class=HTMLResponse)
async def music_settings_page(request: Request, notice: str = ""):
    require_admin(request)
    return templates.TemplateResponse("music_settings.html", {
        "request": request, "notice": notice,
        "soundcloud_configured": bool(setting_get("music.soundcloud.client_id") and setting_get("music.soundcloud.client_secret")),
        "jamendo_configured": bool(setting_get("music.jamendo.client_id")),
        "jamendo_client_id": setting_get("music.jamendo.client_id", ""),
        "lastfm_configured": bool(setting_get("music.lastfm.api_key")),
        "spotify_configured": bool(setting_get("music.spotify.client_id") and setting_get("music.spotify.client_secret")),
        "spotify_redirect_uri": setting_get("music.spotify.redirect_uri", "") or _suggested_spotify_redirect_uri(request),
        "spotify_redirect_suggested": _suggested_spotify_redirect_uri(request),
    })


@app.post("/music/settings")
async def music_settings_save(request: Request, soundcloud_client_id: str = Form(''), soundcloud_client_secret: str = Form(''), jamendo_client_id: str = Form(''), lastfm_api_key: str = Form(''), spotify_client_id: str = Form(''), spotify_client_secret: str = Form(''), spotify_redirect_uri: str = Form('')):
    require_admin(request)
    if soundcloud_client_id.strip(): setting_set('music.soundcloud.client_id',soundcloud_client_id.strip(),True)
    if soundcloud_client_secret.strip() and soundcloud_client_secret != '********': setting_set('music.soundcloud.client_secret',soundcloud_client_secret.strip(),True)
    setting_set('music.jamendo.client_id',jamendo_client_id.strip(),True)
    if lastfm_api_key.strip() and lastfm_api_key != '********': setting_set('music.lastfm.api_key',lastfm_api_key.strip(),True)
    if spotify_client_id.strip(): setting_set('music.spotify.client_id',spotify_client_id.strip(),True)
    if spotify_client_secret.strip() and spotify_client_secret != '********': setting_set('music.spotify.client_secret',spotify_client_secret.strip(),True)
    setting_set('music.spotify.redirect_uri', spotify_redirect_uri.strip())
    log_event('info','settings','music_provider_settings','Music provider application credentials updated from Music Hub')
    return RedirectResponse('/music/settings?notice='+quote('Music provider settings saved'),status_code=303)


@app.post("/settings/music-providers")
async def settings_music_providers(request: Request, soundcloud_client_id: str = Form(''), soundcloud_client_secret: str = Form(''), jamendo_client_id: str = Form(''), lastfm_api_key: str = Form(''), spotify_client_id: str = Form(''), spotify_client_secret: str = Form(''), spotify_redirect_uri: str = Form('')):
    require_admin(request)
    if soundcloud_client_id.strip(): setting_set('music.soundcloud.client_id',soundcloud_client_id.strip(),True)
    if soundcloud_client_secret.strip() and soundcloud_client_secret != '********': setting_set('music.soundcloud.client_secret',soundcloud_client_secret.strip(),True)
    setting_set('music.jamendo.client_id',jamendo_client_id.strip(),True)
    if lastfm_api_key.strip() and lastfm_api_key != '********': setting_set('music.lastfm.api_key',lastfm_api_key.strip(),True)
    if spotify_client_id.strip(): setting_set('music.spotify.client_id',spotify_client_id.strip(),True)
    if spotify_client_secret.strip() and spotify_client_secret != '********': setting_set('music.spotify.client_secret',spotify_client_secret.strip(),True)
    setting_set('music.spotify.redirect_uri', spotify_redirect_uri.strip())
    log_event('info','settings','music_provider_settings','Music provider application credentials updated')
    return RedirectResponse('/settings?notice='+quote('Music provider settings saved'),status_code=303)


@app.post("/settings/general")
async def settings_general(request: Request, app_title: str = Form("ArrNexus"), public_url: str = Form(""), smtp_host: str = Form(""), smtp_port: str = Form("587"), smtp_username: str = Form(""), smtp_password: str = Form(""), smtp_from: str = Form(""), smtp_starttls: str = Form("false")):
    require_admin(request)
    setting_set("app.title", app_title.strip() or "ArrNexus")
    public_value = public_url.strip().rstrip("/")
    if public_value and not re.match(r"^https?://[A-Za-z0-9.\-_:]+$", public_value):
        return RedirectResponse(f"/settings?notice={quote('Public URL must be an http(s) origin without a path')}", status_code=303)
    setting_set("app.public_url", public_value)
    setting_set("smtp.host", smtp_host.strip()); setting_set("smtp.port", smtp_port.strip())
    setting_set("smtp.username", smtp_username.strip())
    if smtp_password and smtp_password != "********": setting_set("smtp.password", smtp_password, True)
    setting_set("smtp.from_address", smtp_from.strip())
    setting_set("smtp.starttls", "true" if smtp_starttls.lower() in {"1","true","yes","on"} else "false")
    return RedirectResponse(f"/settings?notice={quote('Settings saved')}", status_code=303)


@app.post("/settings/users/add")
async def settings_user_add(request: Request, username: str = Form(...), email: str = Form(""), display_name: str = Form(""), password: str = Form(...), role: str = Form("user")):
    require_admin(request)
    try:
        create_user(username, email, display_name, password, role)
        return RedirectResponse(f"/settings?notice={quote('User account created')}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/settings?notice={quote('Could not create user: ' + str(exc))}", status_code=303)


@app.post("/settings/users/delete/{user_id}")
async def settings_user_delete(request: Request, user_id: int):
    require_admin(request)
    try:
        delete_user(user_id, int(request.session.get("user_id") or 0))
        return RedirectResponse(f"/settings?notice={quote('User removed')}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/settings?notice={quote(str(exc))}", status_code=303)



@app.post("/settings/language-guard")
async def settings_language_guard(
    request: Request, enabled: str = Form("false"), require_english_audio: str = Form("false"),
    require_english_subtitles: str = Form("false"), require_default_english_audio: str = Form("false"),
    unknown_is_failure: str = Form("false"), auto_upgrade_search: str = Form("false"),
    remove_rejected_debrid: str = Form("false"), max_files: int = Form(300), probe_timeout_seconds: int = Form(20),
):
    require_admin(request)
    truth = lambda value: str(value).lower() in {"1", "true", "yes", "on"}
    save_language_policy(
        enabled=truth(enabled), require_english_audio=truth(require_english_audio),
        require_english_subtitles=truth(require_english_subtitles),
        require_default_english_audio=truth(require_default_english_audio),
        unknown_is_failure=truth(unknown_is_failure), auto_upgrade_search=truth(auto_upgrade_search),
        remove_rejected_debrid=truth(remove_rejected_debrid), max_files=max_files, probe_timeout_seconds=probe_timeout_seconds,
    )
    log_event("info", "language_guard", "settings_updated", "English-language media policy updated")
    return RedirectResponse("/settings?notice=" + quote("Language Guard settings saved"), status_code=303)


@app.post("/settings/policy")
async def settings_policy(request: Request, preferred_resolution: int = Form(1080), minimum_resolution: int = Form(720), max_size_gb: float = Form(35), max_movie_size_gb: float = Form(45), max_episode_size_gb: float = Form(15), max_season_size_gb: float = Form(100), max_series_size_gb: float = Form(400), minimum_seeders: int = Form(2), prefer_hevc: str = Form("false"), prefer_cached_debrid: str = Form("false"), reject_terms: str = Form("cam,telesync,telecine,hdcam,ts")):
    require_admin(request)
    setting_set("policy.preferred_resolution", str(max(480, preferred_resolution)))
    setting_set("policy.minimum_resolution", str(max(0, minimum_resolution)))
    setting_set("policy.max_size_gb", str(max(0, max_size_gb)))
    setting_set("policy.max_movie_size_gb", str(max(0, max_movie_size_gb)))
    setting_set("policy.max_episode_size_gb", str(max(0, max_episode_size_gb)))
    setting_set("policy.max_season_size_gb", str(max(0, max_season_size_gb)))
    setting_set("policy.max_series_size_gb", str(max(0, max_series_size_gb)))
    setting_set("policy.minimum_seeders", str(max(0, minimum_seeders)))
    setting_set("policy.prefer_hevc", "true" if prefer_hevc.lower() in {"1","true","yes","on"} else "false")
    setting_set("policy.prefer_cached_debrid", "true" if prefer_cached_debrid.lower() in {"1","true","yes","on"} else "false")
    setting_set("policy.reject_terms", reject_terms.strip())
    log_event("info","policy","updated","Release scoring policy updated",{"movie_gb":max_movie_size_gb,"episode_gb":max_episode_size_gb,"season_gb":max_season_size_gb,"series_gb":max_series_size_gb})
    return RedirectResponse('/settings?notice='+quote('Release policy saved'),status_code=303)


@app.post("/settings/acquisition")
async def settings_acquisition(request: Request, default_strategy: str = Form("automatic"), native_search_fallback: str = Form("false"), prefer_cached_debrid: str = Form("false"), max_candidates: int = Form(100)):
    require_admin(request)
    save_acquisition_settings(
        default_strategy,
        native_search_fallback.lower() in {"1","true","yes","on"},
        prefer_cached_debrid.lower() in {"1","true","yes","on"},
        max_candidates,
    )
    log_event("info","acquisition","settings_updated",f"Default strategy: {default_strategy}")
    return RedirectResponse('/settings?notice='+quote('Acquisition strategy saved'),status_code=303)


@app.post("/settings/notifications")
async def settings_notifications(request: Request, enabled: str = Form("false"), failures_only: str = Form("false"), ntfy_server: str = Form(""), ntfy_topic: str = Form(""), ntfy_token: str = Form(""), gotify_url: str = Form(""), gotify_token: str = Form(""), discord_webhook: str = Form(""), email_to: str = Form("")):
    require_admin(request)
    setting_set("notify.enabled", "true" if enabled.lower() in {"1","true","yes","on"} else "false")
    setting_set("notify.failures_only", "true" if failures_only.lower() in {"1","true","yes","on"} else "false")
    setting_set("notify.ntfy.server", ntfy_server.strip())
    setting_set("notify.ntfy.topic", ntfy_topic.strip())
    if ntfy_token.strip() and ntfy_token != "********": setting_set("notify.ntfy.token", ntfy_token.strip(), True)
    setting_set("notify.gotify.url", gotify_url.strip())
    if gotify_token.strip() and gotify_token != "********": setting_set("notify.gotify.token", gotify_token.strip(), True)
    if discord_webhook.strip() and discord_webhook != "********": setting_set("notify.discord.webhook", discord_webhook.strip(), True)
    setting_set("notify.email.to", email_to.strip())
    log_event("info","notifications","settings_updated","Notification settings updated")
    return RedirectResponse('/settings?notice='+quote('Notification settings saved'),status_code=303)


@app.post("/settings/notifications/test")
async def settings_notifications_test(request: Request):
    require_admin(request)
    results = await send_notification("ArrNexus test", "Notification delivery is working.", "info", "test")
    if not results:
        notice = "Notifications are disabled or no providers are configured"
    else:
        notice = "; ".join(f"{r['provider']}: {'OK' if r.get('ok') else 'FAILED'}" for r in results)
    return RedirectResponse('/settings?notice='+quote(notice),status_code=303)


@app.post("/settings/users/access/{user_id}")
async def settings_user_access(request: Request, user_id: int, role: str = Form("user"), can_request: str = Form("false"), daily_request_limit: int = Form(0)):
    require_admin(request)
    update_user_access(user_id, role, can_request.lower() in {"1","true","yes","on"}, max(0, daily_request_limit))
    log_event("info","auth","user_access_updated",f"Access updated for user #{user_id}",{"role":role,"daily_limit":daily_request_limit})
    return RedirectResponse('/settings?notice='+quote('User permissions saved'),status_code=303)


@app.post("/settings/backup")
async def settings_backup(request: Request, auto_enabled: str = Form("true"), retention: int = Form(10)):
    require_admin(request)
    setting_set("backup.auto_enabled", "true" if auto_enabled.lower() in {"1","true","yes","on"} else "false")
    setting_set("backup.retention", str(max(1, min(100, int(retention or 10)))))
    path = create_database_backup("manual", max(1, int(retention or 10)))
    log_event("info","backup","backup_created",path.name)
    return RedirectResponse('/settings?notice='+quote(f'Backup created: {path.name}'),status_code=303)


@app.get("/settings/backup/{name}")
async def settings_backup_download(request: Request, name: str):
    require_admin(request)
    safe = Path(name).name
    path = Path(settings.db_path).resolve().parent / 'backups' / safe
    if not path.exists() or not safe.startswith('arrnexus-') or path.suffix != '.db':
        raise HTTPException(404)
    return FileResponse(path, filename=safe, media_type='application/x-sqlite3')


@app.get("/settings/export-config")
async def settings_export_config(request: Request):
    require_admin(request)
    payload = json.dumps(sanitized_config(), indent=2, default=str)
    return Response(payload, media_type='application/json', headers={'Content-Disposition':'attachment; filename="arrnexus-config-sanitized.json"'})


@app.post("/settings/import-config")
async def settings_import_config(request: Request, config_file: UploadFile = File(...)):
    require_admin(request)
    create_database_backup("before-config-import", int(setting_get("backup.retention","10") or 10))
    try:
        raw = await config_file.read()
        data = json.loads(raw.decode('utf-8'))
        if data.get('format') != 'arrnexus-config-v1':
            raise ValueError('Unsupported ArrNexus config format')
        replace_nonsecret_settings(data.get('settings') or {})
        for m in data.get('mounts') or []:
            name=str(m.get('name') or '').strip(); logical=str(m.get('logical_path') or '').strip()
            if name and logical.startswith('/'):
                try: save_mount(name, logical, str(m.get('kind') or 'library'), str(m.get('service') or ''), str(m.get('destination_key') or ''))
                except Exception: pass
        log_event('info','settings','config_imported',config_file.filename or 'config.json')
        return RedirectResponse('/settings?notice='+quote('Sanitized configuration imported'),status_code=303)
    except Exception as exc:
        return RedirectResponse('/settings?notice='+quote('Config import failed: '+str(exc)),status_code=303)


@app.get("/diagnostics/download")
async def diagnostics_download(request: Request):
    require_admin(request)
    ns = namespace_status()
    extra = {"version": APP_VERSION, "namespace": ns, "connections": {s: {"url": get_connection(s).url, "api_key_configured": bool(get_connection(s).api_key)} for s in ("radarr","sonarr","lidarr","prowlarr","jellyfin","plex","emby","seerr")}}
    data = diagnostics_zip(extra)
    log_event('info','diagnostics','bundle_created','Sanitized diagnostics bundle generated')
    return Response(data, media_type='application/zip', headers={'Content-Disposition':'attachment; filename="arrnexus-diagnostics.zip"'})


async def _check_update() -> dict:
    repo = setting_get("update.repo", UPDATE_DEFAULT_REPOSITORY) or UPDATE_DEFAULT_REPOSITORY
    channel = (setting_get("update.channel", "beta") or "beta").lower()
    if channel not in {"stable", "beta", "development"}:
        channel = "beta"
    try:
        return await check_for_update(APP_VERSION, repo, channel)
    except Exception as exc:
        return {
            "configured": True, "repository": repo, "current": APP_VERSION,
            "channel": channel, "self_update_capable": SELF_UPDATE_CAPABLE,
            "error": str(exc),
        }


@app.post("/settings/update-repo")
async def settings_update_repo(request: Request, update_repo: str = Form(""), update_channel: str = Form("beta")):
    require_admin(request)
    channel = update_channel if update_channel in {"stable", "beta", "development"} else "beta"
    repo = update_repo.strip() or UPDATE_DEFAULT_REPOSITORY
    setting_set("update.repo", repo)
    setting_set("update.channel", channel)
    return RedirectResponse("/settings?notice=" + quote("Update source and channel saved"), status_code=303)


@app.get("/api/update-check")
async def api_update_check(request: Request):
    require_admin(request)
    return await _check_update()


@app.get("/api/update-status")
async def api_update_status(request: Request):
    require_admin(request)
    state = update_status()
    return {
        "current": APP_VERSION,
        "self_update_capable": SELF_UPDATE_CAPABLE,
        **state,
    }


@app.post("/api/update-install")
async def api_update_install(request: Request):
    require_admin(request)
    if not SELF_UPDATE_CAPABLE:
        raise HTTPException(409, "This container predates the ArrNexus v10 self-update bootstrap. Perform one normal Docker upgrade to v10 first.")
    state = update_status()
    if state.get("state") in {"downloading", "verifying", "backup", "dependencies", "validating", "staging", "restarting"}:
        raise HTTPException(409, "An ArrNexus update is already running")
    metadata = await _check_update()
    if metadata.get("error"):
        raise HTTPException(502, str(metadata["error"]))
    if not metadata.get("update_available"):
        return {"ok": True, "started": False, "message": "ArrNexus is already up to date", **metadata}
    if not metadata.get("installable"):
        raise HTTPException(409, metadata.get("reason") or "The GitHub Release is missing a verified ZIP/checksum pair")
    start_self_update(metadata, APP_VERSION)
    log_event("info", "updater", "install_started", f"Installing ArrNexus {metadata.get('latest')}", {"repository": metadata.get("repository")})
    return {"ok": True, "started": True, "target": metadata.get("latest"), "message": "Update started. ArrNexus will restart automatically."}


@app.get("/timeline", response_class=HTMLResponse)
async def timeline_page(request: Request, title: str = "", source_path: str = ""):
    require_auth(request)
    events = title_timeline(title, source_path, 160)
    return templates.TemplateResponse('timeline.html', {"request":request,"title":title or Path(source_path).name,"source_path":source_path,"events":events})


@app.post("/settings/provider-plugin")
async def settings_provider_plugin(request: Request, provider_file: UploadFile = File(...)):
    require_admin(request)
    try:
        raw=await provider_file.read(); data=json.loads(raw.decode('utf-8'))
        if not safe_plugin_search_template(str(data.get('search_url') or '')):
            raise ValueError('Provider search_url must be a real http(s) catalogue URL containing {query}; example/documentation domains are refused')
        key=str(data.get('key') or Path(provider_file.filename or 'provider').stem).lower().replace(' ','-')
        dest=Path(settings.db_path).resolve().parent/'providers'; dest.mkdir(parents=True,exist_ok=True)
        (dest/f'{key}.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
        log_event('info','plugins','catalog_provider_added',key)
        return RedirectResponse('/settings?notice='+quote(f'Catalog provider {key} installed'),status_code=303)
    except Exception as exc:
        return RedirectResponse('/settings?notice='+quote('Provider install failed: '+str(exc)),status_code=303)


@app.get("/ecosystem", response_class=HTMLResponse)
async def ecosystem_page(request: Request, notice: str = ""):
    require_auth(request)
    definitions = connector_definitions()
    configs = {d.key: connector_config(d.key) for d in definitions}
    probes = {x.get("key"): x for x in await probe_enabled_connectors()}
    ns = namespace_status()
    instances = discover_instances()
    return templates.TemplateResponse("ecosystem.html", {
        "request": request, "notice": notice, "definitions": definitions, "configs": configs,
        "probes": probes, "namespace": ns, "arr_instances": instances,
        "configured_count": sum(1 for c in configs.values() if c.get("enabled")),
        "online_count": sum(1 for x in probes.values() if x.get("ok")),
    })


@app.post("/ecosystem/save")
async def ecosystem_save(request: Request, key: str = Form(...), url: str = Form(""), api_key: str = Form(""), username: str = Form(""), password: str = Form(""), enabled: str = Form("false")):
    require_admin(request)
    try:
        save_ecosystem_connector(key.strip(), url.strip(), api_key.strip(), enabled.lower() in {"1","true","yes","on"}, username.strip(), password)
        result = await probe_connector(key.strip())
        detail = "connected" if result.get("ok") else result.get("error") or result.get("state") or "saved"
        log_event("info" if result.get("ok") else "warning", "ecosystem", "connector_saved", f"{key}: {detail}")
        return RedirectResponse("/ecosystem?notice=" + quote(f"{key} saved · {detail}"), status_code=303)
    except Exception as exc:
        return RedirectResponse("/ecosystem?notice=" + quote(f"Could not save connector: {exc}"), status_code=303)


@app.post("/ecosystem/plugin")
async def ecosystem_plugin(request: Request, connector_file: UploadFile = File(...)):
    require_admin(request)
    try:
        raw = await connector_file.read()
        data = json.loads(raw.decode("utf-8"))
        path = install_connector_plugin(data, connector_file.filename or "connector.json")
        log_event("info", "ecosystem", "connector_plugin_installed", path.name)
        return RedirectResponse("/ecosystem?notice=" + quote(f"Connector plugin installed: {path.name}"), status_code=303)
    except Exception as exc:
        return RedirectResponse("/ecosystem?notice=" + quote(f"Connector plugin failed: {exc}"), status_code=303)


@app.get("/api/ecosystem")
async def api_ecosystem(request: Request):
    require_auth(request)
    rows = []
    for definition in connector_definitions():
        cfg = connector_config(definition.key)
        rows.append({
            "key": definition.key, "name": definition.name, "category": definition.category,
            "enabled": bool(cfg.get("enabled")), "configured": bool(cfg.get("url")),
            "capabilities": list(definition.capabilities),
        })
    return {"connectors": rows, "namespace": namespace_status()}


def _infini_history_matches(row: dict, history_filter: str) -> bool:
    if history_filter == "all":
        return True
    cat = str(row.get("category") or row.get("cat") or "").lower()
    if history_filter == "movies":
        return "movie" in cat or "radarr" in cat
    if history_filter == "tv":
        return "tv" in cat or "sonarr" in cat
    return True


def _infini_graph_points(overview: dict) -> str:
    rows = list((overview or {}).get("throughput") or [])
    if not rows:
        return ""
    values = [max(0, int(x.get("bytesFetched") or x.get("bytesServed") or 0)) for x in rows]
    peak = max(values) if values else 0
    if peak <= 0:
        return ""
    count = len(values)
    points = []
    for idx, value in enumerate(values):
        x = 0 if count <= 1 else (idx / (count - 1)) * 100
        y = 96 - (value / peak) * 88
        points.append(f"{x:.2f},{y:.2f}")
    return " ".join(points)


async def _build_infinidysk_snapshot(window: str) -> dict:
    cfg = connector_config("infinidysk")
    health = {}; queue = {}; history = {}; overview = {}; metrics = []; errors = []
    if cfg.get("enabled") and cfg.get("url"):
        client = InfiniDyskClient()

        async def bounded(call, label):
            try:
                return await asyncio.wait_for(call, timeout=4.5)
            except Exception as exc:
                return exc

        health_v, queue_v, history_v, overview_v = await asyncio.gather(
            bounded(client.health(), "health"), bounded(client.queue(), "queue"),
            bounded(client.history(), "history"), bounded(client.overview(window, "all"), "overview"),
        )
        if isinstance(health_v, Exception): errors.append(str(health_v))
        else: health = health_v or {}
        if isinstance(queue_v, Exception): errors.append(str(queue_v))
        else: queue = queue_v or {}
        if isinstance(history_v, Exception): errors.append(str(history_v))
        else: history = history_v or {}
        if isinstance(overview_v, Exception):
            errors.append(str(overview_v))
        else:
            overview = overview_v or {}
    return {"config": cfg, "health": health, "queue_raw": queue, "history_raw": history, "overview": overview, "metrics": metrics, "errors": errors, "built_at": time.time()}


def _infini_snapshot_cache(window: str) -> StaleSnapshot:
    cache = _INFINI_SNAPSHOTS.get(window)
    if cache is None:
        cache = StaleSnapshot(30.0)
        _INFINI_SNAPSHOTS[window] = cache
    return cache


@app.get("/infinidysk", response_class=HTMLResponse)
async def infinidysk_page(request: Request, notice: str = "", window: str = "24h", history_filter: str = "all", refresh: int = 0):
    require_auth(request)
    if window not in {"1h", "24h", "7d", "30d", "all"}:
        window = "24h"
    if history_filter not in {"all", "movies", "tv"}:
        history_filter = "all"
    snapshot, age, refreshing = await _infini_snapshot_cache(window).get(lambda: _build_infinidysk_snapshot(window), force=bool(refresh))
    queue = snapshot.get("queue_raw") or {}
    history = snapshot.get("history_raw") or {}
    q = queue.get("queue", {}) if isinstance(queue, dict) else {}
    h = history.get("history", {}) if isinstance(history, dict) else {}
    history_slots = h.get("slots", []) if isinstance(h, dict) else []
    history_slots = [x for x in history_slots if _infini_history_matches(x, history_filter)]
    errors = snapshot.get("errors") or []
    overview = snapshot.get("overview") or {}
    return templates.TemplateResponse("infinidysk.html", {
        "request": request, "notice": notice, "config": snapshot.get("config") or {}, "health": snapshot.get("health") or {}, "queue": q,
        "queue_slots": q.get("slots", []) if isinstance(q, dict) else [], "history_slots": history_slots,
        "overview": overview, "graph_points": _infini_graph_points(overview), "metrics": snapshot.get("metrics") or [],
        "error": " · ".join(dict.fromkeys(x for x in errors if x)), "window": window, "history_filter": history_filter,
        "snapshot_age": age, "snapshot_refreshing": refreshing,
    })


@app.get("/api/infinidysk/live")
async def infinidysk_live(request: Request, window: str = "24h", force: int = 0):
    require_auth(request)
    if window not in {"1h", "24h", "7d", "30d", "all"}:
        window = "24h"
    try:
        snapshot, age, refreshing = await _infini_snapshot_cache(window).get(lambda: _build_infinidysk_snapshot(window), force=bool(force))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
    queue_data = (snapshot.get("queue_raw") or {}).get("queue", {}) if isinstance(snapshot.get("queue_raw"), dict) else {}
    return {"ok": True, "overview": snapshot.get("overview") or {}, "queue": queue_data, "age": age, "refreshing": refreshing}


@app.post("/infinidysk/action")
async def infinidysk_action(request: Request, action: str = Form(...)):
    require_admin(request)
    try:
        client = InfiniDyskClient()
        if action == "pause":
            await client.pause()
        elif action == "resume":
            await client.resume()
        else:
            raise ValueError("Unsupported InfiniDysk action")
        log_event("info", "infinidysk", f"queue_{action}", f"InfiniDysk queue {action} requested")
        return RedirectResponse("/infinidysk?notice=" + quote(f"Queue {action} requested"), status_code=303)
    except Exception as exc:
        return RedirectResponse("/infinidysk?notice=" + quote(f"InfiniDysk action failed: {exc}"), status_code=303)


@app.get("/decypharr", response_class=HTMLResponse)
async def decypharr_page(request: Request):
    require_auth(request)
    cfg=connector_config("decypharr"); version={}; torrents=[]; repair={}; arrs=[]; broken=[]; error=""
    if cfg.get("enabled") and cfg.get("url"):
        c=DecypharrClient()
        try: version=await c.version()
        except Exception as exc: error=str(exc)
        try:
            raw=await c.torrents(); torrents=raw if isinstance(raw,list) else raw.get("torrents") or raw.get("data") or [] if isinstance(raw,dict) else []
        except Exception as exc:
            if not error: error=str(exc)
        try:
            raw=await c.repair_status(); repair=raw if isinstance(raw,dict) else {}
        except Exception: repair={}
        try:
            raw=await c.arrs(); arrs=raw if isinstance(raw,list) else raw.get("arrs") or raw.get("data") or [] if isinstance(raw,dict) else []
        except Exception: arrs=[]
        try:
            raw=await c.repair_health(); health=raw if isinstance(raw,list) else raw.get("entries") or raw.get("data") or [] if isinstance(raw,dict) else []
            broken=[x for x in health if str(x.get("status") or "").lower() in {"broken","failed","error","unhealthy"}]
        except Exception: broken=[]
    else:
        error="Enable and verify the Decypharr connector in Ecosystem first"
    return templates.TemplateResponse("decypharr.html",{"request":request,"config":cfg,"version":version,"torrents":torrents,"torrent_count":len(torrents),"repair":repair,"arrs":arrs,"arr_count":len(arrs),"broken":broken,"error":error})


@app.get("/indexers", response_class=HTMLResponse)
async def indexers_page(request: Request, notice: str = ""):
    require_auth(request)
    rows = []; tags = []; error = ""
    try:
        client = ProwlarrClient()
        rows_v, tags_v = await asyncio.gather(client.indexers(), client.tags(), return_exceptions=True)
        if isinstance(rows_v, Exception):
            raise rows_v
        rows = rows_v or []
        tags = [] if isinstance(tags_v, Exception) else (tags_v or [])
    except Exception as exc:
        error = str(exc)
    tag_names = {int(t.get("id") or 0): str(t.get("label") or t.get("name") or t.get("id") or "") for t in tags if t.get("id") is not None}
    view = []
    for row in rows:
        item = dict(row)
        item["tag_names"] = [tag_names.get(int(t), str(t)) for t in (row.get("tags") or [])]
        caps = row.get("capabilities") or {}
        categories = []
        for cat in caps.get("categories") or []:
            if isinstance(cat, dict):
                categories.append(str(cat.get("name") or cat.get("id") or ""))
        item["category_names"] = [x for x in categories if x][:8]
        item["routing_critical"] = any(str(x).lower() in {"nzbdav", "decypharr", "infinidysk"} for x in item["tag_names"])
        view.append(item)
    view.sort(key=lambda x: (str(x.get("protocol") or ""), int(x.get("priority") or 50), str(x.get("name") or "").lower()))
    return templates.TemplateResponse("indexers.html", {"request":request,"indexers":view,"tags":tags,"error":error,"notice":notice})


@app.post("/indexers/{indexer_id}")
async def indexer_update(request: Request, indexer_id: int, enable: str = Form("false"), priority: int = Form(25), enable_rss: str = Form("false"), enable_automatic: str = Form("false"), enable_interactive: str = Form("false")):
    require_admin(request)
    changes = {
        "enable": enable.lower() in {"1","true","yes","on"},
        "priority": max(1, min(50, int(priority))),
        "enableRss": enable_rss.lower() in {"1","true","yes","on"},
        "enableAutomaticSearch": enable_automatic.lower() in {"1","true","yes","on"},
        "enableInteractiveSearch": enable_interactive.lower() in {"1","true","yes","on"},
    }
    try:
        updated = await ProwlarrClient().update_indexer(indexer_id, changes)
        name = str((updated or {}).get("name") or f"Indexer {indexer_id}") if isinstance(updated, dict) else f"Indexer {indexer_id}"
        log_event("info", "prowlarr", "indexer_updated", f"{name} updated from ArrNexus", changes)
        return RedirectResponse("/indexers?notice=" + quote(f"{name} saved to Prowlarr"), status_code=303)
    except Exception as exc:
        log_event("warning", "prowlarr", "indexer_update_failed", str(exc), {"indexer_id": indexer_id})
        return RedirectResponse("/indexers?notice=" + quote(f"Indexer update failed: {exc}"), status_code=303)


@app.get("/quality-lab", response_class=HTMLResponse)
async def quality_lab_page(request: Request, title: str = "", media_type: str = "movie", protocol: str = "torrent", size_gb: float = 0, seeders: int = 0, cached: str = "false", pack_type: str = "", q: str = ""):
    require_auth(request)
    analysis = None
    if title.strip():
        analysis = evaluate_release(title.strip(), protocol=protocol, size_gb=size_gb, seeders=seeders, cached=cached.lower() in {"1","true","yes","on"}, media_type=media_type, pack_type=pack_type)
    releases = []
    search_error = ""
    if q.strip():
        try:
            raw = await ProwlarrClient().search(q.strip(), limit=50)
            for row in raw or []:
                parsed = parse_release_name(str(row.get("title") or ""))
                inferred_pack = (parsed.get("pack") or {}).get("kind") or ""
                score = score_release(row, load_policy(), media_type=media_type, pack_type=inferred_pack)
                releases.append({**row, "arrnexus_parsed": parsed, "arrnexus_policy": score})
            releases.sort(key=lambda r: int((r.get("arrnexus_policy") or {}).get("score") or 0), reverse=True)
        except Exception as exc:
            search_error = str(exc)
    profilarr_cfg = connector_config("profilarr")
    return templates.TemplateResponse("qualitylab.html", {
        "request": request, "analysis": analysis, "title": title, "media_type": media_type,
        "protocol": protocol, "size_gb": size_gb, "seeders": seeders, "cached": cached,
        "pack_type": pack_type, "q": q, "releases": releases, "search_error": search_error,
        "policy": load_policy(), "profilarr": profilarr_cfg,
    })


@app.get("/self-healing", response_class=HTMLResponse)
async def self_healing_page(request: Request, notice: str = ""):
    require_auth(request)
    rows = await scan_self_healing()
    totals = {
        "missing": sum(int((r.get("counts") or {}).get("missing") or 0) for r in rows),
        "upgrades": sum(int((r.get("counts") or {}).get("upgrades") or 0) for r in rows),
        "queue_issues": sum(int((r.get("counts") or {}).get("queue_issues") or 0) for r in rows),
    }
    return templates.TemplateResponse("selfhealing.html", {
        "request": request, "notice": notice, "rows": rows, "totals": totals, "settings": selfheal_settings_state(),
        "neutarr": connector_config("neutarr"), "cleanuparr": connector_config("cleanuparr"),
    })


@app.post("/self-healing/settings")
async def self_healing_settings(request: Request, enabled: str = Form("false"), search_missing: str = Form("false"), search_upgrades: str = Form("false"), interval_minutes: int = Form(60), max_actions: int = Form(3), window_start: str = Form("02:00"), window_end: str = Form("06:00")):
    require_admin(request)
    save_selfheal_settings(
        enabled=enabled.lower() in {"1","true","yes","on"},
        search_missing=search_missing.lower() in {"1","true","yes","on"},
        search_upgrades=search_upgrades.lower() in {"1","true","yes","on"},
        interval_minutes=interval_minutes, max_actions=max_actions, window_start=window_start, window_end=window_end,
    )
    log_event("info", "selfheal", "settings_updated", "Self-healing settings updated")
    return RedirectResponse("/self-healing?notice=" + quote("Self-healing settings saved"), status_code=303)


@app.post("/self-healing/search")
async def self_healing_search(request: Request, service: str = Form(...), instance: str = Form(...), kind: str = Form("missing"), limit: int = Form(10)):
    require_admin(request)
    try:
        result = await selfheal_trigger_search(service, instance, kind, limit)
        text = f"Triggered {result.get('completed',0)} {kind} search(es)"
        if result.get("errors"):
            text += f" · {len(result['errors'])} error(s)"
        return RedirectResponse("/self-healing?notice=" + quote(text), status_code=303)
    except Exception as exc:
        return RedirectResponse("/self-healing?notice=" + quote(f"Search failed: {exc}"), status_code=303)


@app.get("/queue", response_class=HTMLResponse)
async def queue_page(request: Request):
    require_auth(request)
    instances=discover_instances()
    async def _load(inst):
        try:
            client=client_for_instance(inst); data=await client.queue(100)
            records=data.get("records",[]) if isinstance(data,dict) else (data or [])
            return inst, records, None
        except Exception as exc:
            return inst, [], exc
    loaded=await asyncio.gather(*(_load(i) for i in instances))
    rows=[]; sources=[]
    for inst, records, exc in loaded:
        sources.append({"instance":inst,"ok":exc is None,"count":len(records),**({"error":str(exc)} if exc else {})})
        rows.extend({"instance":inst,"item":x} for x in records)
    return templates.TemplateResponse("queue.html", {"request": request, "rows": rows, "sources": sources})


def _arr_media_card(row: dict, media_type: str, route: str = "default", instance: str = "main") -> dict:
    return {
        "title": row.get("title") or row.get("artistName") or "Untitled",
        "year": row.get("year"), "media_type": media_type, "route": route or "default", "instance": instance,
        "poster": poster_url(row), "rating": 0, "overview": row.get("overview") or "",
        "in_library": True, "has_file": bool(row.get("hasFile") or (row.get("statistics") or {}).get("episodeFileCount")),
    }


async def _library_shelves() -> tuple[list[dict], list[str]]:
    shelves=[]; warnings=[]
    try:
        instances=[i for i in discover_instances() if i.service in {"radarr","sonarr"} and i.api_key]
    except Exception as exc:
        log_event("error","discover","instance_discovery_failed",str(exc))
        return [], [f"DUMB Arr discovery failed: {exc}"]

    async def _load(inst):
        try:
            c=client_for_instance(inst)
            rows=await (c.movies() if inst.service=="radarr" else c.series())
            return inst, rows if isinstance(rows,list) else [], None
        except Exception as exc:
            return inst, [], exc

    loaded=await asyncio.gather(*(_load(i) for i in instances))
    for inst, rows, exc in loaded:
        if exc is not None:
            warnings.append(f"{inst.service.title()}/{inst.instance}: {exc}")
            log_event("warning","discover","library_shelf_failed",str(exc),{"service":inst.service,"instance":inst.instance})
            continue
        media_type="movie" if inst.service=="radarr" else "tv"
        rows=sorted(rows, key=lambda x: (str(x.get("added") or ""), str(x.get("title") or "")), reverse=True)[:30]
        route=inst.destination_key or "default"
        pretty_route={"default":"","disney":"Disney+","apple":"Apple TV+"}.get(route,route.replace("_"," ").title())
        label=("Movies" if media_type=="movie" else "TV") + (f" · {pretty_route}" if pretty_route else "")
        cards=[]
        for row in rows:
            try:
                cards.append(_arr_media_card(row,media_type,route,inst.instance))
            except Exception as card_exc:
                log_event("warning","discover","library_card_failed",str(card_exc),{"title":str(row.get("title") or "")})
        shelves.append({"key":f"{inst.service}-{route}".replace("/","-"),"title":label,"subtitle":f"{len(cards)} shown from {inst.service}/{inst.instance}","items":cards})
    return shelves,warnings


async def _seerr_shelves() -> tuple[list[dict], str]:
    try:
        c=SeerrClient()
        if not c.configured:
            return [], "Configure Seerr in Connections to enable trending/popular discovery. Your own Arr library shelves still work without Seerr."
        tm,tp,mm,tt=await asyncio.gather(
            c.trending("movie","week",1), c.trending("tv","week",1),
            c.discover_movies(1,"popularity.desc"), c.discover_tv(1,"popularity.desc"),
            return_exceptions=True,
        )
        specs=[
            ("trending-movies","Trending movies this week","movie",tm),
            ("popular-movies","Popular movies","movie",mm),
            ("trending-tv","Trending TV this week","tv",tp),
            ("popular-tv","Popular TV shows","tv",tt),
        ]
        shelves=[]; failures=[]
        for key,title,kind,payload in specs:
            if isinstance(payload,Exception):
                failures.append(f"{title}: {payload}"); continue
            try:
                rows=seerr_result_rows(payload if isinstance(payload,dict) else {},kind)[:24]
                shelves.append({"key":key,"title":title,"subtitle":"Seerr discovery","items":rows})
            except Exception as exc:
                failures.append(f"{title}: {exc}")
        return shelves, ("; ".join(failures) if failures else "")
    except Exception as exc:
        log_event("warning","discover","seerr_shelves_failed",str(exc))
        return [], f"Seerr discovery unavailable: {exc}"


async def _mark_shelf_library_state(shelves: list[dict]):
    owned_movies={}; owned_tv={}
    try:
        instances=discover_instances()
    except Exception as exc:
        log_event("warning","discover","library_state_instances_failed",str(exc)); return
    for inst in instances:
        if inst.service not in {"radarr","sonarr"} or not inst.api_key: continue
        try:
            c=client_for_instance(inst); rows=await (c.movies() if inst.service=="radarr" else c.series())
        except Exception: continue
        target=owned_movies if inst.service=="radarr" else owned_tv
        for x in rows or []:
            title=normalize_title(x.get("title") or "")
            if title: target[title]=(inst.destination_key or "default", inst.instance)
    for shelf in shelves or []:
        for x in shelf.get("items") or []:
            if x.get("in_library"): continue
            hit=(owned_movies if x.get("media_type")=="movie" else owned_tv).get(normalize_title(x.get("title") or ""))
            if hit:
                x["in_library"]=True; x["route"],x["instance"]=hit


@app.get("/discover", response_class=HTMLResponse)
async def discover_page(request: Request, q: str = "", media_type: str = "movie", notice: str = "", error: str = ""):
    require_auth(request)
    results=[]; page_error=error or None; rd_names=[]; warnings=[]
    # Seerr and local Arr shelves are independent, so load them together.
    # One failure is isolated instead of delaying or blanking the whole page.
    seerr_result, library_result = await asyncio.gather(_seerr_shelves(), _library_shelves(), return_exceptions=True)
    if isinstance(seerr_result, Exception):
        seerr_shelves=[]; seerr_notice=f"Seerr discovery unavailable: {seerr_result}"; warnings.append(seerr_notice)
    else:
        seerr_shelves,seerr_notice=seerr_result
        if seerr_notice: warnings.append(seerr_notice)
    if isinstance(library_result, Exception):
        library_shelves=[]; warnings.append(f"Library shelves unavailable: {library_result}")
    else:
        library_shelves,library_warnings=library_result; warnings.extend(library_warnings)
    try:
        await _mark_shelf_library_state(seerr_shelves)
    except Exception as exc:
        warnings.append(f"Library-state badges unavailable: {exc}")
    if q.strip():
        try:
            results=await discover_lookup(q.strip(), media_type)
            if rd.connected():
                try:
                    torrents=await rd.torrents(250); rd_names=[normalize_title(x.get("filename") or x.get("original_filename") or "") for x in torrents]
                except Exception: rd_names=[]
            for candidate in results:
                nt=normalize_title(candidate.get("title") or "")
                candidate["arrnexus_in_rd"]=bool(nt and any(nt in rn or rn in nt for rn in rd_names if rn))
                candidate["arrnexus_poster"]=poster_url(candidate)
                candidate["arrnexus_genres"]=candidate.get("genres") or []
        except Exception as exc:
            page_error=str(exc); log_event("error","discover","search_failed",str(exc),{"query":q,"media_type":media_type})
    try:
        return templates.TemplateResponse("discover.html", {
            "request":request,"q":q,"media_type":media_type,"results":results,
            "error":page_error,"notice":notice,"movie_roots":movie_roots(),"tv_roots":tv_roots(),"rd_connected":rd.connected(),
            "catalog_shelves":seerr_shelves,"library_shelves":library_shelves,"seerr_notice":seerr_notice,"discover_warnings":warnings,
            "acquisition":load_acquisition_settings(),"acquisition_strategies":STRATEGIES,
        })
    except Exception as exc:
        # Last-resort diagnostic instead of an opaque Internal Server Error.
        log_event("error","discover","template_failed",str(exc))
        return HTMLResponse(f"<h1>ArrNexus Discover error</h1><p>{__import__('html').escape(str(exc))}</p><p><a href='/logs?source=discover&level=error'>Open Discover logs</a></p>",status_code=500)


@app.post("/discover/add")
async def discover_add_route(request: Request, media_type: str = Form(...), candidate_json: str = Form(...), destination_key: str = Form("auto"), query: str = Form(""), acquisition_strategy: str = Form("automatic")):
    user=require_request_access(request, count_against_limit=True)
    try:
        candidate=json.loads(candidate_json)
        # v6 deliberately adds/monitors the title first but does not dispatch a
        # broad Arr search. The background acquisition planner performs an
        # interactive release search and grabs exactly one release according to
        # the requested Usenet/Debrid strategy.
        result=await discover_add(candidate,media_type,destination_key,search=False,user_id=int(user["id"]))
        item=result["item"]; ext=item.get("tmdbId") if media_type=="movie" else item.get("tvdbId")
        strategy=acquisition_strategy if acquisition_strategy in STRATEGIES else load_acquisition_settings().default_strategy
        scrape_id=add_scrape(media_type,item.get("title") or candidate.get("title") or "Untitled",str(ext or ""),"radarr" if media_type=="movie" else "sonarr",result["instance"],item.get("id"),result["destination"],"planning",f"Acquisition strategy: {strategy.replace('_',' ')} · comparing Usenet and Debrid releases")
        update_request_progress(media_type,str(ext or ""),"planning",f"Acquisition strategy: {strategy}","")
        _launch(_run_discover_acquisition(scrape_id,media_type,str(ext or ""),result["instance"],int(item.get("id") or 0),strategy,item.get("title") or candidate.get("title") or "Untitled"))
        log_event("info","acquisition","planner_dispatched",item.get("title") or "Untitled",{"media_type":media_type,"instance":result["instance"],"destination":result["destination"],"strategy":strategy})
        msg=quote(f"Requested {item.get('title')} via {result['instance']}; {strategy.replace('_',' ')} acquisition is running")
        return RedirectResponse(f"/discover?media_type={media_type}&q={quote(query)}&notice={msg}",status_code=303)
    except Exception as exc:
        log_event("error","discover","request_failed",str(exc),{"query":query,"media_type":media_type})
        return RedirectResponse(f"/discover?media_type={media_type}&q={quote(query)}&error={quote(str(exc))}",status_code=303)


async def _run_discover_acquisition(scrape_id: int, media_type: str, external_id: str, instance_name: str, arr_id: int, strategy: str, title: str):
    service="radarr" if media_type=="movie" else "sonarr"
    try:
        inst=next((i for i in discover_instances() if i.service==service and i.instance==instance_name and i.api_key),None)
        client=client_for_instance(inst) if inst else (RadarrClient() if media_type=="movie" else SonarrClient())
        update_scrape(scrape_id,"searching",f"{strategy.replace('_',' ').title()} · querying interactive Arr releases from Usenet + torrent indexers")
        result=await plan_and_grab(client,media_type,arr_id,strategy)
        counts=result.get("counts") or {}
        if result.get("ok"):
            proto=result.get("protocol") or "unknown"
            label="Real-Debrid / torrent" if proto=="torrent" else "Usenet / InfiniDysk" if proto=="usenet" else proto
            detail=(f"Selected {label} · {result.get('indexer') or 'indexer'} · score {result.get('score',0)} · "
                    f"{counts.get('usenet',0)} Usenet / {counts.get('torrent',0)} torrent candidates" +
                    (" · RD cached" if result.get("cached") else ""))
            update_scrape(scrape_id,"grabbed",detail)
            update_request_progress(media_type,external_id,"grabbed",detail,proto)
            log_event("info","acquisition","release_grabbed",title,{"strategy":strategy,"protocol":proto,"indexer":result.get("indexer"),"release":result.get("title"),"score":result.get("score"),"counts":counts})
            return
        cfg=load_acquisition_settings()
        reason="; ".join(result.get("reasons") or [])
        if cfg.native_search_fallback and strategy in {"automatic","fastest","quality","debrid_first","usenet_first"}:
            await client.search(arr_id)
            detail=f"ArrNexus planner found no acceptable release ({reason}). Native Arr search dispatched as final fallback."
            update_scrape(scrape_id,"searching",detail); update_request_progress(media_type,external_id,"searching",detail,"")
            log_event("warning","acquisition","native_fallback",title,{"strategy":strategy,"reason":reason,"counts":counts})
        else:
            detail=f"No acceptable release: {reason}"
            update_scrape(scrape_id,"failed",detail); update_request_progress(media_type,external_id,"failed",detail,"")
            log_event("error","acquisition","no_release",title,{"strategy":strategy,"reason":reason,"counts":counts})
    except Exception as exc:
        detail=f"Acquisition planner failed: {exc}"
        update_scrape(scrape_id,"failed",detail); update_request_progress(media_type,external_id,"failed",detail,"")
        log_event("error","acquisition","planner_failed",detail,{"title":title,"strategy":strategy})


@app.get("/scraping", response_class=HTMLResponse)
async def scraping_page(request: Request, status: str = "all"):
    require_auth(request)
    rows=list_scrapes(150,status)
    # Use live queues to annotate which searches have progressed into a grab.
    queues=[]
    for inst in discover_instances():
        if inst.service not in {"radarr","sonarr"} or not inst.api_key: continue
        try:
            c=client_for_instance(inst); q=await c.queue(200); records=q.get("records",[]) if isinstance(q,dict) else (q or [])
            queues.extend(records)
        except Exception: pass
    for row in rows:
        target=normalize_title(row.get("title") or "")
        live=next((x for x in queues if target and target in normalize_title(x.get("title") or x.get("movie",{}).get("title") or x.get("series",{}).get("title") or "")),None)
        if live:
            row["live"]=live; row["display_status"]="grabbed / downloading"
            if row.get("status") == "searching": update_scrape(int(row["id"]), "downloading", "Release grabbed; now visible in Download Queue")
        else:
            row["live"]=None; row["display_status"]=row.get("status") or "searching"
            # If the Arr item now has media, finish the scraping stage automatically.
            if row.get("arr_id") and row.get("status") in {"searching","downloading"}:
                inst=next((i for i in discover_instances() if i.service==row.get("arr_service") and i.instance==row.get("arr_instance")),None)
                if inst and inst.api_key:
                    try:
                        c=client_for_instance(inst)
                        entity=await (c.movie(int(row["arr_id"])) if row.get("media_type")=="movie" else c.series_by_id(int(row["arr_id"])))
                        ready=bool(entity.get("hasFile")) if row.get("media_type")=="movie" else bool((entity.get("statistics") or {}).get("episodeFileCount"))
                        if ready:
                            row["display_status"]="complete"; update_scrape(int(row["id"]),"complete","Media file imported by Arr")
                    except Exception: pass
    return templates.TemplateResponse("scraping.html",{"request":request,"rows":rows,"status":status})


@app.get("/api/scraping")
async def scraping_api(request: Request):
    require_auth(request); return {"items":list_scrapes(50,"all")}



def _release_hash(row: dict) -> str:
    h=str(row.get("infoHash") or row.get("infohash") or row.get("hash") or "").strip()
    if h:
        return h.lower()
    magnet=str(row.get("magnetUrl") or row.get("magnet") or "")
    m=re.search(r"(?i)btih:([a-z0-9]{32,40})",magnet)
    return (m.group(1).lower() if m else "")


async def _annotate_rd_cache(rows: list[dict], limit: int = 35):
    if not rd.connected():
        return
    sem=asyncio.Semaphore(6)
    async def one(row):
        h=_release_hash(row)
        if not h:
            row["realDebridCached"]=False; return
        key=f"rdcache:{h}"
        cached=cache_get(key)
        if isinstance(cached,dict) and "cached" in cached:
            row["realDebridCached"]=bool(cached["cached"]); return
        async with sem:
            try:
                payload=await rd.instant_availability(h)
                node=(payload or {}).get(h) or (payload or {}).get(h.lower()) or {}
                available=False
                if isinstance(node,dict):
                    for provider_rows in node.values():
                        if provider_rows:
                            available=True; break
                row["realDebridCached"]=available
                cache_set(key,{"cached":available})
            except Exception:
                row["realDebridCached"]=False
    await asyncio.gather(*(one(r) for r in rows[:limit]))


def _expected_tv_seasons(meta: dict | None) -> list[int]:
    seasons=[]
    for x in ((meta or {}).get("seasons") or []):
        try:
            n=int(x.get("seasonNumber"))
            if n>0: seasons.append(n)
        except Exception: pass
    return sorted(set(seasons))


async def _tv_library_coverage(meta: dict | None) -> dict | None:
    """Find an existing Sonarr copy and summarize season coverage.

    This powers the 'get missing only' decision so ArrNexus can avoid grabbing a
    whole series when only one or two seasons are actually absent.
    """
    if not meta:
        return None
    raw=meta.get("raw") or {}
    target_tvdb=str(raw.get("tvdbId") or raw.get("tvdbid") or "")
    target_title=normalize_title(meta.get("title") or raw.get("title") or "")
    try:
        instances=[i for i in discover_instances() if i.service=="sonarr" and i.api_key]
    except Exception:
        return None
    for inst in instances:
        try:
            rows=await client_for_instance(inst).series()
        except Exception:
            continue
        hit=None
        for row in rows or []:
            row_tvdb=str(row.get("tvdbId") or row.get("tvdbid") or "")
            if target_tvdb and row_tvdb and target_tvdb==row_tvdb:
                hit=row; break
            if target_title and normalize_title(row.get("title") or "")==target_title:
                hit=row; break
        if not hit:
            continue
        seasons=[]; missing=[]; complete=[]; partial=[]
        for season in hit.get("seasons") or []:
            try:
                n=int(season.get("seasonNumber") or 0)
            except Exception:
                continue
            if n<=0:
                continue
            st=season.get("statistics") or {}
            total=int(st.get("episodeCount") or st.get("totalEpisodeCount") or 0)
            have=int(st.get("episodeFileCount") or 0)
            if total>0 and have>=total:
                state="complete"; complete.append(n)
            elif have>0:
                state="partial"; partial.append(n); missing.append(n)
            else:
                state="missing"; missing.append(n)
            seasons.append({"number":n,"have":have,"total":total,"state":state})
        seasons.sort(key=lambda x:x["number"])
        return {
            "found":True,"title":hit.get("title") or meta.get("title"),"series_id":hit.get("id"),
            "instance":inst.instance,"route":inst.destination_key or "default","seasons":seasons,
            "missing_seasons":sorted(set(missing)),"complete_seasons":sorted(set(complete)),"partial_seasons":sorted(set(partial)),
        }
    return {"found":False,"seasons":[],"missing_seasons":[],"complete_seasons":[],"partial_seasons":[]}


async def _search_debrid_releases(release_q: str, media_type: str, protocol: str, pack_mode: str = "any", quality: str = "any", cached_only: bool = False, hide_duplicates: bool = True) -> tuple[list[dict],dict|None]:
    release_meta=None
    categories=[2000] if media_type=="movie" else [5000] if media_type=="tv" else None
    try:
        if media_type=="tv": meta_rows=await SonarrClient().lookup(release_q.strip())
        elif media_type=="movie": meta_rows=await RadarrClient().lookup(release_q.strip())
        else:
            meta_rows=await RadarrClient().lookup(release_q.strip())
            if not meta_rows: meta_rows=await SonarrClient().lookup(release_q.strip())
        if meta_rows:
            m=meta_rows[0]
            release_meta={"title":m.get("title") or release_q,"year":m.get("year"),"overview":m.get("overview") or "","poster":poster_url(m),"genres":m.get("genres") or [],"seasons":m.get("seasons") or [],"raw":m}
    except Exception:
        release_meta=None
    rows=await ProwlarrClient().search(release_q.strip(),categories=categories,limit=100)
    if protocol in {"torrent","usenet"}:
        rows=[x for x in (rows or []) if str(x.get("protocol") or "").lower()==protocol]
    work=[]
    for raw in rows or []:
        row=dict(raw)
        pack=classify_release(row.get("title") or "") if media_type=="tv" else None
        if pack:
            row["arrnexus_pack"]=pack.as_dict()
            row["arrnexus_coverage"]=coverage_summary(pack,_expected_tv_seasons(release_meta))
            if not pack_matches(pack_mode,pack):
                continue
        if quality!="any" and f"{quality}p" not in str(row.get("title") or "").lower() and not (quality=="2160" and "4k" in str(row.get("title") or "").lower()):
            continue
        work.append(row)
    if protocol!="usenet":
        await _annotate_rd_cache(work)
    policy=load_policy(); scored=[]; seen=set()
    for row in work:
        pack_type=(row.get("arrnexus_pack") or {}).get("kind") or ""
        row["arrnexus_policy"]=score_release(row,policy,media_type=media_type,pack_type=pack_type)
        if cached_only and not row.get("realDebridCached"):
            continue
        if hide_duplicates:
            dedupe=(normalize_title(row.get("title") or ""),int(row.get("size") or 0)//(100*1024*1024))
            if dedupe in seen: continue
            seen.add(dedupe)
        scored.append(row)
    scored.sort(key=lambda x:(bool(x.get("realDebridCached")),int((x.get("arrnexus_policy") or {}).get("score") or 0),int(x.get("seeders") or 0)),reverse=True)
    return scored,release_meta


async def _add_release_to_rd(release: dict) -> str:
    if str(release.get("protocol") or "").lower()!="torrent":
        raise ArrError("Only torrent releases can be added directly to Real-Debrid")
    magnet=release.get("magnetUrl") or release.get("magnet") or ""; info_hash=_release_hash(release)
    if not magnet and info_hash: magnet=f"magnet:?xt=urn:btih:{info_hash}"
    if magnet:
        added=await rd.add_magnet(magnet)
    else:
        dl=await ProwlarrClient().download_release(release.get("downloadUrl") or release.get("downloadURL") or "")
        added=await (rd.add_magnet(dl["magnet"]) if dl.get("magnet") else rd.add_torrent_file(dl.get("content") or b""))
    tid=str((added or {}).get("id") or "")
    if tid:
        try: await rd.select_all(tid)
        except Exception: pass
    return tid


@app.get("/debrid", response_class=HTMLResponse)
async def debrid_page(request: Request, q: str = "", release_q: str = "", protocol: str = "torrent", media_type: str = "all", pack_mode: str = "any", quality: str = "any", cached_only: str = "false", hide_duplicates: str = "true", error: str = "", notice: str = ""):
    require_auth(request)
    user=None; torrents=[]; releases=[]; release_meta=None; page_error=error or None
    cached_flag=str(cached_only).lower() in {"1","true","yes","on"}
    dedupe_flag=str(hide_duplicates).lower() not in {"0","false","no","off"}
    if rd.connected():
        try:
            user,torrents=await asyncio.gather(rd.user(),rd.torrents(500))
        except Exception as exc:
            page_error=str(exc)
    if q.strip():
        nq=q.lower(); torrents=[x for x in torrents if nq in (x.get("filename") or "").lower()]
    if release_q.strip():
        try:
            releases,release_meta=await _search_debrid_releases(release_q.strip(),media_type,protocol,pack_mode,quality,cached_flag,dedupe_flag)
        except Exception as exc:
            page_error=str(exc); log_event("error","debrid","search_failed",str(exc),{"query":release_q,"media_type":media_type,"pack_mode":pack_mode})
    expected_seasons=_expected_tv_seasons(release_meta) if media_type=="tv" else []
    tv_coverage=None
    if media_type=="tv" and release_meta:
        try:
            tv_coverage=await _tv_library_coverage(release_meta)
        except Exception as exc:
            log_event("warning","debrid","tv_coverage_failed",str(exc),{"query":release_q})
    full_count=sum(1 for r in releases if (r.get("arrnexus_pack") or {}).get("kind")=="full_series")
    season_count=sum(1 for r in releases if (r.get("arrnexus_pack") or {}).get("kind")=="season_pack")
    episode_count=sum(1 for r in releases if (r.get("arrnexus_pack") or {}).get("kind") in {"episode","episode_bundle"})
    return templates.TemplateResponse("debrid.html",{
        "request":request,"connected":rd.connected(),"user":user,"torrents":torrents,"error":page_error,"notice":notice,"q":q,
        "release_q":release_q,"protocol":protocol,"media_type":media_type,"pack_mode":pack_mode,"quality":quality,"cached_only":cached_flag,
        "hide_duplicates":dedupe_flag,"releases":releases,"release_meta":release_meta,"policy":load_policy(),"expected_seasons":expected_seasons,
        "tv_coverage":tv_coverage,"pack_counts":{"full_series":full_count,"season_pack":season_count,"episode":episode_count},
    })


@app.post("/debrid/add-smart-show")
async def debrid_add_smart_show(request: Request, release_q: str = Form(...), quality: str = Form("any"), cached_only: str = Form("false")):
    require_request_access(request,count_against_limit=False)
    if not rd.connected():
        return RedirectResponse("/debrid?error="+quote("Connect Real-Debrid first"),status_code=303)
    try:
        rows,meta=await _search_debrid_releases(release_q,"tv","torrent","any",quality,str(cached_only).lower() in {"1","true","yes","on"},True)
        expected=_expected_tv_seasons(meta)
        best=choose_best_complete(rows,expected)
        added=[]
        if best:
            await _add_release_to_rd(best); added=[best]
            detail=f"Added best complete-series pack: {best.get('title')}"
        else:
            packs,missing=choose_best_season_packs(rows,expected)
            if missing and expected:
                raise ArrError("No complete-series pack found and season packs are missing: "+", ".join(f"S{x:02d}" for x in missing))
            if not packs:
                raise ArrError("No complete-series or season-pack combination matched the current policy")
            for row in packs:
                await _add_release_to_rd(row); added.append(row)
            detail=f"Added {len(added)} season pack(s) covering the show"
        add_activity("debrid",meta.get("title") if meta else release_q,detail)
        log_event("info","debrid","smart_show_added",detail,{"query":release_q,"count":len(added)})
        return RedirectResponse(f"/debrid?release_q={quote(release_q)}&media_type=tv&protocol=torrent&pack_mode=any&notice={quote(detail)}",status_code=303)
    except Exception as exc:
        log_event("error","debrid","smart_show_failed",str(exc),{"query":release_q})
        return RedirectResponse(f"/debrid?release_q={quote(release_q)}&media_type=tv&protocol=torrent&error={quote(str(exc))}",status_code=303)


@app.post("/debrid/add-missing-show")
async def debrid_add_missing_show(request: Request, release_q: str = Form(...), quality: str = Form("any"), cached_only: str = Form("false")):
    require_request_access(request,count_against_limit=False)
    if not rd.connected():
        return RedirectResponse("/debrid?error="+quote("Connect Real-Debrid first"),status_code=303)
    try:
        rows,meta=await _search_debrid_releases(release_q,"tv","torrent","any",quality,str(cached_only).lower() in {"1","true","yes","on"},True)
        coverage=await _tv_library_coverage(meta)
        if not coverage or not coverage.get("found"):
            raise ArrError("This show is not currently matched in Sonarr, so ArrNexus cannot safely determine which seasons are missing")
        missing=list(coverage.get("missing_seasons") or [])
        if not missing:
            detail="Sonarr already reports every released season as complete"
            return RedirectResponse(f"/debrid?release_q={quote(release_q)}&media_type=tv&protocol=torrent&notice={quote(detail)}",status_code=303)
        packs,still_missing=choose_best_season_packs(rows,missing)
        if still_missing:
            raise ArrError("Could not find acceptable season packs for: "+", ".join(f"S{x:02d}" for x in still_missing))
        if not packs:
            raise ArrError("No acceptable season packs were found for the missing seasons")
        for row in packs:
            await _add_release_to_rd(row)
        detail="Added season pack(s) for missing coverage: "+", ".join(f"S{x:02d}" for x in missing)
        add_activity("debrid",meta.get("title") if meta else release_q,detail)
        log_event("info","debrid","missing_seasons_added",detail,{"query":release_q,"seasons":missing,"count":len(packs)})
        return RedirectResponse(f"/debrid?release_q={quote(release_q)}&media_type=tv&protocol=torrent&notice={quote(detail)}",status_code=303)
    except Exception as exc:
        log_event("error","debrid","missing_seasons_failed",str(exc),{"query":release_q})
        return RedirectResponse(f"/debrid?release_q={quote(release_q)}&media_type=tv&protocol=torrent&error={quote(str(exc))}",status_code=303)


@app.post("/debrid/add-release")
async def debrid_add_release(request: Request, release_json: str = Form(...), return_query: str = Form("")):
    require_request_access(request, count_against_limit=False)
    if not rd.connected():
        return RedirectResponse("/debrid?error=" + quote("Connect Real-Debrid first"), status_code=303)
    try:
        release = json.loads(release_json)
        if str(release.get("protocol") or "").lower() != "torrent":
            raise ArrError("Only torrent releases can be added directly to Real-Debrid; use Lidarr/Radarr/Sonarr for Usenet results")
        magnet = release.get("magnetUrl") or release.get("magnet") or ""
        info_hash = release.get("infoHash") or release.get("infohash") or release.get("hash") or ""
        if not magnet and info_hash:
            magnet = f"magnet:?xt=urn:btih:{info_hash}"
        if magnet:
            added = await rd.add_magnet(magnet)
        else:
            dl = await ProwlarrClient().download_release(release.get("downloadUrl") or release.get("downloadURL") or "")
            if dl.get("magnet"):
                added = await rd.add_magnet(dl["magnet"])
            else:
                added = await rd.add_torrent_file(dl.get("content") or b"")
        tid = str((added or {}).get("id") or "")
        if tid:
            try:
                await rd.select_all(tid)
            except Exception:
                pass
        title = release.get("title") or "Torrent release"
        add_activity("debrid", title, "Added to Real-Debrid from Prowlarr search")
        return RedirectResponse(f"/debrid?release_q={quote(return_query)}&protocol=torrent&notice={quote('Added to Real-Debrid. Decypharr will expose it in the DMM Inbox when ready.')}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/debrid?release_q={quote(return_query)}&protocol=torrent&error={quote(str(exc))}", status_code=303)


@app.post("/debrid/connect")
async def debrid_connect(request: Request):
    require_auth(request)
    try:
        data = await rd.begin_oauth()
        return RedirectResponse("/debrid/auth", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/debrid?error={quote(str(exc))}", status_code=303)


@app.get("/debrid/auth", response_class=HTMLResponse)
async def debrid_auth_page(request: Request):
    require_auth(request)
    return templates.TemplateResponse("debrid_auth.html", {
        "request": request,
        "user_code": setting_get("rd.pending_user_code"),
        "verification_url": setting_get("rd.pending_verification_url", "https://real-debrid.com/device"),
    })


@app.get("/api/debrid/poll")
async def debrid_poll(request: Request):
    require_auth(request)
    try:
        return await rd.poll_oauth()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@app.post("/debrid/disconnect")
async def debrid_disconnect(request: Request):
    require_auth(request)
    rd.disconnect()
    return RedirectResponse("/debrid", status_code=303)


async def ensure_lidarr_artist(artist_name: str):
    lc = LidarrClient()
    artists = await lc.artists()
    target = normalize_title(artist_name)
    existing = next((a for a in artists if normalize_title(a.get("artistName") or a.get("name") or "") == target), None)
    if existing:
        return existing, False
    lookup = await lc.artist_lookup(artist_name)
    if not lookup:
        raise ArrError(f"Lidarr could not find artist: {artist_name}")
    candidate = next((x for x in lookup if normalize_title(x.get("artistName") or x.get("name") or "") == target), lookup[0])
    artist = await lc.add_artist(candidate, lidarr_root(), search=False)
    return artist, True


def _spotify_redirect_uri(request: Request) -> str:
    configured = setting_get("music.spotify.redirect_uri", "").strip()
    return configured or _suggested_spotify_redirect_uri(request)


def _spotify_redirect_is_acceptable(uri: str) -> bool:
    value = (uri or "").strip().lower()
    return value.startswith("https://") or value.startswith("http://127.0.0.1") or value.startswith("http://[::1]")


async def _lidarr_artists_cached(force: bool = False):
    value, age, refreshing = await _LIDARR_ARTISTS_SNAPSHOT.get(lambda: LidarrClient().artists(), force=force)
    return list(value or []), age, refreshing


@app.get("/music", response_class=HTMLResponse)
async def music_page(request: Request, q: str = "", kind: str = "artist", source: str = "unified", genre: str = "", notice: str = "", error: str = ""):
    user = current_user(request)
    providers = provider_catalog()
    available_keys = {p.get("key") for p in providers}
    if source not in available_keys:
        source = "unified"
    term = q.strip() or genre.strip()
    results: list[dict] = []
    source_featured: list[dict] = []
    source_note = ""
    external_url = ""
    provider_error = error
    lidarr_error = ""
    spotify_hub = {"linked": False, "profile": {}, "saved_tracks": [], "saved_albums": [], "playlists": [], "top_tracks": [], "top_artists": [], "recent": [], "errors": []}
    spotify_trending: list[dict] = []
    spotify_trending_artists: list[dict] = []

    async def _feature():
        return await provider_featured(source, genre, 24)

    async def _search():
        if term:
            return await provider_search(source, term, kind, 30)
        if source in {"amazon", "beatport", "bandcamp", "discogs"} and genre:
            return [], external_music_links(genre).get(source, "")
        return [], ""

    async def _lidarr():
        try:
            artists, _, _ = await _lidarr_artists_cached()
            return artists, ""
        except Exception as exc:
            return [], str(exc)

    tasks = [_feature(), _search(), _lidarr()]
    task_names = ["featured", "search", "lidarr"]
    if source == "spotify" and spotify_user_linked(int(user["id"])):
        tasks.extend([
            spotify_user_hub(int(user["id"])),
            trending_releases(18, "this_week"),
            trending_artists(18, "this_week"),
        ])
        task_names.extend(["spotify_hub", "spotify_trending", "spotify_trending_artists"])
    elif source == "spotify":
        tasks.extend([trending_releases(18, "this_week"), trending_artists(18, "this_week")])
        task_names.extend(["spotify_trending", "spotify_trending_artists"])
    elif source == "unified":
        tasks.extend([
            trending_artists(18, "this_week"),
            trending_releases(18, "this_week"),
            audius_trending(18, genre),
        ])
        task_names.extend(["trends", "releases", "audius"])

    values = await asyncio.gather(*tasks, return_exceptions=True)
    loaded = dict(zip(task_names, values))

    featured_value = loaded.get("featured")
    if isinstance(featured_value, Exception):
        provider_error = provider_error or str(featured_value)
        log_event("warning", "music", "provider_failed", str(featured_value), {"source": source, "query": q, "genre": genre})
    else:
        source_featured, source_note = featured_value or ([], "")

    search_value = loaded.get("search")
    if isinstance(search_value, Exception):
        provider_error = provider_error or str(search_value)
    else:
        results, external_url = search_value or ([], "")
        external_url = safe_external_url(external_url)

    lidarr_value = loaded.get("lidarr")
    if isinstance(lidarr_value, Exception):
        lidarr_artists, lidarr_error = [], str(lidarr_value)
    else:
        lidarr_artists, lidarr_error = lidarr_value or ([], "")

    trends = releases = audius = []
    if source == "unified":
        trends = [] if isinstance(loaded.get("trends"), Exception) else (loaded.get("trends") or [])
        releases = [] if isinstance(loaded.get("releases"), Exception) else (loaded.get("releases") or [])
        audius = [] if isinstance(loaded.get("audius"), Exception) else (loaded.get("audius") or [])
        try:
            trends, releases = await asyncio.gather(enrich_artist_art(trends, 10), enrich_release_art(releases, 10))
        except Exception as exc:
            log_event("warning", "music", "unified_artwork_failed", str(exc))
    elif source == "spotify":
        hub_value = loaded.get("spotify_hub")
        if hub_value is not None and not isinstance(hub_value, Exception):
            spotify_hub = hub_value
        elif isinstance(hub_value, Exception):
            spotify_hub["linked"] = True
            spotify_hub["errors"] = [str(hub_value)]
        raw_trending = [] if isinstance(loaded.get("spotify_trending"), Exception) else (loaded.get("spotify_trending") or [])
        spotify_trending_artists = [] if isinstance(loaded.get("spotify_trending_artists"), Exception) else (loaded.get("spotify_trending_artists") or [])
        try:
            spotify_trending = await enrich_release_art(raw_trending, min(10, len(raw_trending)))
            spotify_trending_artists = await enrich_artist_art(spotify_trending_artists, min(8, len(spotify_trending_artists)))
        except Exception:
            spotify_trending = raw_trending

    selected_provider = next((p for p in providers if p.get("key") == source), {"name": "For You", "description": ""})
    redirect_uri = _spotify_redirect_uri(request)
    return templates.TemplateResponse("music.html", {
        "request": request, "q": q, "kind": kind, "source": source, "genre": genre, "results": results,
        "source_featured": source_featured, "source_note": source_note, "selected_provider": selected_provider,
        "trends": trends, "releases": releases, "audius": audius, "lidarr_artists": lidarr_artists,
        "lidarr_error": lidarr_error, "genres": GENRES, "error": provider_error, "notice": notice,
        "providers": providers, "external_url": external_url,
        "soundcloud_configured": bool(setting_get("music.soundcloud.client_id") and setting_get("music.soundcloud.client_secret")),
        "jamendo_configured": bool(setting_get("music.jamendo.client_id")), "lastfm_configured": bool(setting_get("music.lastfm.api_key")),
        "spotify_configured": spotify_app_configured(), "spotify_linked": spotify_user_linked(int(user["id"])),
        "spotify_hub": spotify_hub, "spotify_trending": spotify_trending, "spotify_trending_artists": spotify_trending_artists,
        "spotify_redirect_uri": redirect_uri, "spotify_redirect_ready": _spotify_redirect_is_acceptable(redirect_uri),
    })


@app.get("/music/spotify/connect")
async def spotify_connect(request: Request):
    user = current_user(request)
    if not spotify_app_configured():
        return RedirectResponse("/music?source=spotify&error=" + quote("Configure the Spotify Client ID and Client Secret in Music API settings first."), status_code=303)
    redirect_uri = _spotify_redirect_uri(request)
    if not _spotify_redirect_is_acceptable(redirect_uri):
        message = "Spotify user linking needs an HTTPS redirect URI (or 127.0.0.1 for local development). Add the exact callback URL in Music API settings and in your Spotify app."
        return RedirectResponse("/music?source=spotify&error=" + quote(message), status_code=303)
    state = secrets.token_urlsafe(32)
    request.session["spotify_oauth_state"] = state
    request.session["spotify_oauth_user"] = int(user["id"])
    return RedirectResponse(spotify_authorize_url(int(user["id"]), state, redirect_uri), status_code=303)


@app.get("/music/spotify/callback", name="spotify_callback")
async def spotify_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    user = current_user(request)
    expected = str(request.session.pop("spotify_oauth_state", "") or "")
    expected_user = int(request.session.pop("spotify_oauth_user", 0) or 0)
    if error:
        return RedirectResponse("/music?source=spotify&error=" + quote("Spotify authorization was not completed: " + error), status_code=303)
    if not code or not state or not expected or not secrets.compare_digest(state, expected) or expected_user != int(user["id"]):
        log_event("warning", "spotify", "oauth_state_mismatch", "Spotify OAuth callback state did not validate")
        return RedirectResponse("/music?source=spotify&error=" + quote("Spotify authorization could not be validated. Please try Connect Spotify again."), status_code=303)
    try:
        await spotify_exchange_code(int(user["id"]), code, _spotify_redirect_uri(request))
        hub = await spotify_user_hub(int(user["id"]))
        who = (hub.get("profile") or {}).get("display_name") or (hub.get("profile") or {}).get("id") or "Spotify"
        log_event("info", "spotify", "account_linked", f"Spotify account linked for {who}", {"user_id": int(user["id"])})
        return RedirectResponse("/music?source=spotify&notice=" + quote(f"Spotify linked as {who}"), status_code=303)
    except Exception as exc:
        log_event("warning", "spotify", "oauth_failed", str(exc), {"user_id": int(user["id"])})
        return RedirectResponse("/music?source=spotify&error=" + quote(str(exc)), status_code=303)


@app.post("/music/spotify/disconnect")
async def spotify_disconnect(request: Request):
    user = current_user(request)
    spotify_disconnect_user(int(user["id"]))
    log_event("info", "spotify", "account_disconnected", "Spotify account disconnected", {"user_id": int(user["id"])})
    return RedirectResponse("/music?source=spotify&notice=" + quote("Spotify account disconnected"), status_code=303)


def _music_artist_snapshot(name: str) -> StaleSnapshot:
    key = normalize_title(name) or name.strip().lower()
    snap = _MUSIC_ARTIST_SNAPSHOTS.get(key)
    if snap is None:
        snap = StaleSnapshot(60.0)
        _MUSIC_ARTIST_SNAPSHOTS[key] = snap
    return snap


async def _build_music_artist_state(name: str) -> dict:
    lc = LidarrClient()

    async def bounded(coro, timeout: float, default):
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except Exception:
            return default

    # These calls are independent.  v9.2 performed them mostly one after the
    # other, so a slow MusicBrainz/Apple response stacked on top of Lidarr.
    artists_task = asyncio.create_task(_lidarr_artists_cached())
    lookup_task = asyncio.create_task(bounded(lc.artist_lookup(name), 3.5, []))
    mb_task = asyncio.create_task(bounded(search_musicbrainz(name, "artist", 6), 3.0, []))
    artwork_task = asyncio.create_task(bounded(representative_artwork(name), 2.75, ""))

    artists_v, _, _ = await artists_task
    existing = next((a for a in (artists_v or []) if normalize_title(a.get("artistName") or a.get("name") or "") == normalize_title(name)), None)
    albums_task = asyncio.create_task(bounded(lc.albums(int(existing["id"])), 3.5, [])) if existing else None
    lookup_v, mb_v, artwork_v = await asyncio.gather(lookup_task, mb_task, artwork_task)
    albums = await albums_task if albums_task is not None else []
    links = {k: safe_external_url(v) for k, v in external_music_links(name).items()}
    links = {k: v for k, v in links.items() if v}
    return {
        "name": name, "lookup": (lookup_v or [None])[0] if lookup_v else None,
        "mb": mb_v or [], "existing": existing, "albums": albums or [],
        "links": links, "artwork": artwork_v or "", "built_at": time.time(),
    }


@app.get("/music/artist", response_class=HTMLResponse)
async def music_artist_page(request: Request, name: str, refresh: int = 0):
    require_auth(request)
    data, age, refreshing = await _music_artist_snapshot(name).get(lambda: _build_music_artist_state(name), force=bool(refresh))
    return templates.TemplateResponse("music_artist.html", {"request": request, **data, "snapshot_age": age, "snapshot_refreshing": refreshing})


@app.post("/music/add-result")
async def music_add_result(request: Request, artist_name: str = Form(...), title: str = Form(""), kind: str = Form("artist"), source: str = Form("")):
    require_auth(request)
    try:
        artist, created = await ensure_lidarr_artist(artist_name)
        lc = LidarrClient()
        detail = ""
        if kind == "album" and title:
            albums = await lc.albums(int(artist["id"]))
            album = next((x for x in albums if normalize_title(x.get("title", "")) == normalize_title(title)), None)
            if album:
                if not album.get("monitored"):
                    await lc.monitor_album(int(album["id"]), True)
                await lc.search_album(int(album["id"]))
                detail = f"Album monitored and search queued via Lidarr ({source or 'discovery'})"
            else:
                await lc.command({"name": "RefreshArtist", "artistId": int(artist["id"])})
                await lc.search_artist(int(artist["id"]))
                detail = "Artist added/refreshed; album was not resolved yet so an artist search was queued"
        else:
            await lc.search_artist(int(artist["id"]))
            detail = f"{'Added artist and' if created else 'Artist already managed;'} search queued via Lidarr"
        add_activity("music", f"{artist_name}{' — ' + title if title and kind == 'album' else ''}", detail)
        return RedirectResponse(f"/music/artist?name={quote(artist_name)}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/music?q={quote(artist_name)}&error={quote(str(exc))}", status_code=303)


@app.post("/music/add-artist")
async def music_add_artist(request: Request, artist_name: str = Form(...), search_now: bool = Form(False)):
    require_auth(request)
    try:
        artist, created = await ensure_lidarr_artist(artist_name)
        if search_now:
            await LidarrClient().search_artist(int(artist["id"]))
        add_activity("music", artist_name, f"{'Added to' if created else 'Already in'} Lidarr" + ("; search queued" if search_now else ""))
        return RedirectResponse(f"/music/artist?name={quote(artist_name)}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/music?q={quote(artist_name)}&error={quote(str(exc))}", status_code=303)


@app.post("/music/search-album")
async def music_search_album(request: Request, artist_name: str = Form(...), album_title: str = Form(...)):
    require_auth(request)
    try:
        artist, _ = await ensure_lidarr_artist(artist_name)
        lc = LidarrClient()
        albums = await lc.albums(int(artist["id"]))
        album = next((x for x in albums if normalize_title(x.get("title", "")) == normalize_title(album_title)), None)
        if not album:
            await lc.command({"name": "RefreshArtist", "artistId": int(artist["id"])})
            raise ArrError("Artist is in Lidarr but this album is not in its metadata yet. RefreshArtist was queued; retry in a moment.")
        if not album.get("monitored"):
            album = await lc.monitor_album(int(album["id"]), True)
        await lc.search_album(int(album["id"]))
        add_activity("music", f"{artist_name} — {album_title}", "Album monitored and Lidarr Usenet search queued")
        return RedirectResponse(f"/music/artist?name={quote(artist_name)}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/music/artist?name={quote(artist_name)}&error={quote(str(exc))}", status_code=303)

# ===== ARRNEXUS V8 AIOSTREAMS ROUTES =========================================
from . import aiostreams as _aio


def _aio_admin(request: Request):
    return require_admin(request)


def _aio_form_bool(value) -> bool:
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def _aio_redirect(message: str, error: bool = False):
    key = "error" if error else "notice"
    return RedirectResponse(f"/aiostreams?{key}={quote(str(message))}", status_code=303)


async def _aio_page_state(user_data: dict | None = None, user_error: str = "") -> dict:
    conn = _aio.connection_settings(mask=True)
    status_state = await _aio.status() if conn.get("url") else {"ok": False, "detail": "AIOStreams URL not configured", "data": {}}
    raw_user = user_data
    authenticated = False
    error = user_error
    if raw_user is None and conn.get("configured"):
        try:
            raw_user = await _aio.get_user(raw=True)
            authenticated = True
        except Exception as exc:
            error = str(exc)
    elif raw_user is not None:
        authenticated = True
    cfg = (raw_user or {}).get("userData") if isinstance(raw_user, dict) else {}
    if not isinstance(cfg, dict):
        cfg = {}
    integrations = _aio.discover_arrnexus_integrations()
    return {
        "connection": conn,
        "status": status_state,
        "user_ok": authenticated,
        "user_error": error,
        "service_count": len(cfg.get("services") or []) if isinstance(cfg.get("services"), list) else 0,
        "preset_count": len(cfg.get("presets") or []) if isinstance(cfg.get("presets"), list) else 0,
        "integrations": _aio.integration_summary(integrations),
        "endpoints": _aio.endpoint_helpers(),
        "backups": _aio.list_backups(30),
    }


# ---------------------------------------------------------------------------
# ARRNEXUS V9 PRODUCT / PROVIDER / ONBOARDING ROUTES
# ---------------------------------------------------------------------------

@app.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request, "configured": user_count() > 0, "logged_in": logged_in(request), "version": APP_VERSION, "release_filename": f"arrnexus-v{APP_VERSION}.zip"})


@app.get("/onboarding", response_class=HTMLResponse)
async def onboarding_page(request: Request, notice: str = ""):
    require_admin(request)
    return templates.TemplateResponse("onboarding.html", {
        "request": request,
        "notice": notice,
        "readiness": await live_stack_readiness(),
        "providers": list_provider_states(mask=True),
    })


@app.post("/onboarding/finish")
async def onboarding_finish(request: Request):
    require_admin(request)
    state = await live_stack_readiness()
    setting_set("setup.complete", "true")
    setting_set("setup.stage", "complete")
    log_event("info", "setup", "onboarding_complete", f"Readiness score {state['score']}%")
    return RedirectResponse("/dashboard?notice=" + quote(f"Setup saved · readiness {state['score']}%"), status_code=303)


@app.get("/providers", response_class=HTMLResponse)
async def providers_page(request: Request, notice: str = ""):
    require_admin(request)
    states = list_provider_states(mask=True)
    grouped = {}
    for state in states:
        grouped.setdefault(state["category"], []).append(state)
    return templates.TemplateResponse("providers.html", {
        "request": request, "providers": states, "grouped": grouped,
        "categories": provider_categories(), "notice": notice,
    })


@app.post("/providers/{provider_id}")
async def provider_save_route(request: Request, provider_id: str):
    require_admin(request)
    definition = provider_definition(provider_id)
    if not definition:
        raise HTTPException(404, "Unknown provider")
    form = await request.form()
    values = {field: str(form.get(field) or "") for field, _label, _secret in definition.credential_fields}
    save_provider(provider_id, str(form.get("enabled") or "").lower() in {"1", "true", "on", "yes"}, values)
    log_event("info", "providers", "provider_updated", f"{definition.name} provider settings updated")
    return RedirectResponse("/providers?notice=" + quote(f"{definition.name} saved"), status_code=303)


@app.get("/readiness", response_class=HTMLResponse)
async def readiness_page(request: Request, refresh: int = 0):
    require_admin(request)
    return templates.TemplateResponse("readiness.html", {"request": request, "readiness": await live_stack_readiness(force=bool(refresh))})


@app.get("/aiostreams", response_class=HTMLResponse)
async def aiostreams_page(request: Request, notice: str = "", error: str = ""):
    _aio_admin(request)
    state = await _aio_page_state()
    return templates.TemplateResponse("aiostreams.html", {
        "request": request,
        "state": state,
        "notice": notice,
        "error": error,
        "preview": None,
        "search_json": "",
        "search_query": {"type": "movie", "id": ""},
    })


@app.post("/aiostreams/save")
async def aiostreams_save(request: Request):
    _aio_admin(request)
    form = await request.form()
    try:
        _aio.save_connection(
            str(form.get("url") or ""),
            str(form.get("user") or ""),
            str(form.get("credential") or ""),
        )
        _aio.save_manual_realdebrid_key(str(form.get("realdebrid_api_key") or ""))
        log_event("info", "aiostreams", "connection_saved", "AIOStreams connection settings updated")
        return _aio_redirect("AIOStreams settings saved privately")
    except Exception as exc:
        log_event("warning", "aiostreams", "connection_save_failed", str(exc))
        return _aio_redirect(str(exc), True)


@app.post("/aiostreams/verify")
async def aiostreams_verify(request: Request):
    _aio_admin(request)
    try:
        result = await _aio.verify()
        log_event("info", "aiostreams", "verified", "AIOStreams User API verified", {
            "services": result.get("services"), "presets": result.get("presets")
        })
        return _aio_redirect(f"AIOStreams verified: {result.get('services', 0)} services, {result.get('presets', 0)} presets")
    except Exception as exc:
        log_event("warning", "aiostreams", "verify_failed", str(exc))
        return _aio_redirect(str(exc), True)


@app.post("/aiostreams/preview", response_class=HTMLResponse)
async def aiostreams_preview(request: Request):
    _aio_admin(request)
    form = await request.form()
    wire_prowlarr = _aio_form_bool(form.get("wire_prowlarr"))
    wire_realdebrid = _aio_form_bool(form.get("wire_realdebrid"))
    wire_nzbdav = _aio_form_bool(form.get("wire_nzbdav"))
    try:
        current = await _aio.get_user(raw=True)
        existing = current["userData"]
        integrations = _aio.discover_arrnexus_integrations()
        plan = _aio.merge_autowire(
            existing,
            integrations,
            wire_prowlarr=wire_prowlarr,
            wire_realdebrid=wire_realdebrid,
            wire_nzbdav=wire_nzbdav,
        )
        preview = {
            "digest": _aio.config_digest(existing),
            "changes": plan["changes"],
            "warnings": plan["warnings"],
            "safe_config": _aio.safe_json(plan["config"]),
            "wire_prowlarr": wire_prowlarr,
            "wire_realdebrid": wire_realdebrid,
            "wire_nzbdav": wire_nzbdav,
        }
        state = await _aio_page_state(current)
        return templates.TemplateResponse("aiostreams.html", {
            "request": request,
            "state": state,
            "notice": "Preview created. No AIOStreams configuration has been changed.",
            "error": "",
            "preview": preview,
            "search_json": "",
            "search_query": {"type": "movie", "id": ""},
        })
    except Exception as exc:
        log_event("warning", "aiostreams", "preview_failed", str(exc))
        return _aio_redirect(str(exc), True)


@app.post("/aiostreams/apply")
async def aiostreams_apply(request: Request):
    _aio_admin(request)
    form = await request.form()
    try:
        result = await _aio.apply_autowire(
            str(form.get("expected_digest") or ""),
            wire_prowlarr=_aio_form_bool(form.get("wire_prowlarr")),
            wire_realdebrid=_aio_form_bool(form.get("wire_realdebrid")),
            wire_nzbdav=_aio_form_bool(form.get("wire_nzbdav")),
        )
        if result.get("no_change"):
            return _aio_redirect("AIOStreams already matches the selected Auto-Wire plan; no PUT was required")
        backup_name = ((result.get("backup") or {}).get("name") or "created")
        log_event("warning", "aiostreams", "autowire_applied", "AIOStreams full configuration updated", {
            "backup": backup_name,
            "change_count": len(result.get("changes") or []),
        })
        return _aio_redirect(f"AIOStreams Auto-Wire applied and verified. Backup: {backup_name}")
    except Exception as exc:
        log_event("error", "aiostreams", "autowire_failed", str(exc))
        return _aio_redirect(str(exc), True)


@app.post("/aiostreams/rollback")
async def aiostreams_rollback(request: Request):
    _aio_admin(request)
    form = await request.form()
    try:
        result = await _aio.rollback(str(form.get("backup_name") or ""))
        log_event("warning", "aiostreams", "rollback", "AIOStreams configuration rolled back", {
            "backup": result.get("restored"),
            "safety_backup": (result.get("safety_backup") or {}).get("name"),
        })
        return _aio_redirect(f"AIOStreams restored from {result.get('restored')}; the pre-rollback state was also backed up")
    except Exception as exc:
        log_event("error", "aiostreams", "rollback_failed", str(exc))
        return _aio_redirect(str(exc), True)


@app.get("/aiostreams/search", response_class=HTMLResponse)
async def aiostreams_search_page(request: Request):
    _aio_admin(request)
    media_type = str(request.query_params.get("type") or "movie")
    external_id = str(request.query_params.get("id") or "")
    state = await _aio_page_state()
    search_json = ""
    error = ""
    if external_id:
        try:
            result = await _aio.search(media_type, external_id, True)
            search_json = json.dumps(_aio.safe_search_payload(result), indent=2, ensure_ascii=False, sort_keys=True)
        except Exception as exc:
            error = str(exc)
    return templates.TemplateResponse("aiostreams.html", {
        "request": request,
        "state": state,
        "notice": "",
        "error": error,
        "preview": None,
        "search_json": search_json,
        "search_query": {"type": media_type, "id": external_id},
    })


@app.get("/api/aiostreams/status")
async def aiostreams_status_api(request: Request):
    _aio_admin(request)
    state = await _aio_page_state()
    return JSONResponse({
        "ok": bool(state["status"].get("ok") and state["user_ok"]),
        "reachable": bool(state["status"].get("ok")),
        "authenticated": bool(state["user_ok"]),
        "configured": bool(state["connection"].get("configured")),
        "serviceCount": state["service_count"],
        "presetCount": state["preset_count"],
        "lastSync": state["connection"].get("last_sync"),
        "error": state.get("user_error") or "",
    })


@app.get("/api/aiostreams/search")
async def aiostreams_search_api(request: Request):
    _aio_admin(request)
    media_type = str(request.query_params.get("type") or "movie")
    external_id = str(request.query_params.get("id") or "")
    try:
        data = await _aio.search(media_type, external_id, True)
        return JSONResponse({"ok": True, "data": _aio.safe_search_payload(data)})
    except _aio.AIOStreamsError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
# ===== END ARRNEXUS V8 AIOSTREAMS ROUTES ======================================


# ===== ARRNEXUS V10.3 ADVANCED TV RECOVERY =================================

@app.get("/tv-recovery/analyse", response_class=HTMLResponse)
async def tv_recovery_analyse(request: Request, path: str):
    require_admin(request)
    try:
        plan = await tv_recovery.analyse_source(path)
        return templates.TemplateResponse("tv_recovery.html", {"request": request, "plan": plan, "result": None, "error": ""})
    except Exception as exc:
        return templates.TemplateResponse("tv_recovery.html", {"request": request, "plan": None, "result": None, "error": str(exc)}, status_code=400)


@app.post("/tv-recovery/staging")
async def tv_recovery_staging(request: Request):
    require_admin(request)
    form = await request.form()
    path = str(form.get("source_path") or "")
    try:
        tv_recovery.save_staging_root(str(form.get("staging_root") or ""))
        return RedirectResponse("/tv-recovery/analyse?path=" + quote(path), status_code=303)
    except Exception as exc:
        return RedirectResponse("/tv-recovery/analyse?path=" + quote(path) + "&error=" + quote(str(exc)), status_code=303)


@app.post("/tv-recovery/split", response_class=HTMLResponse)
async def tv_recovery_split(request: Request):
    require_admin(request)
    form = await request.form()
    digest = str(form.get("digest") or "")
    file_path = str(form.get("file_path") or "")
    allow_estimated = _v102_truth(form.get("allow_estimated")) if '_v102_truth' in globals() else str(form.get("allow_estimated") or "").lower() in {"1","true","yes","on"}
    try:
        result = await asyncio.to_thread(tv_recovery.split_plan, digest, file_path, allow_estimated)
        invalidate_scan_cache()
        _INBOX_SNAPSHOT.clear()
        plan = cache_get(f"tv_recovery:plan:{digest}")
        return templates.TemplateResponse("tv_recovery.html", {"request": request, "plan": plan, "result": result, "error": ""})
    except Exception as exc:
        plan = cache_get(f"tv_recovery:plan:{digest}")
        return templates.TemplateResponse("tv_recovery.html", {"request": request, "plan": plan, "result": None, "error": str(exc)}, status_code=400)



# ===== ARRNEXUS V10.4 ARCHIVED MEDIA RECOVERY ===============================

async def run_archive_inspect_job(job_id: int):
    job, items = get_job(job_id)
    if not job:
        return
    update_job(job_id, status="running", message="RAR catalogue inspection in progress")
    completed = failed = 0
    for ji in items:
        iid = int(ji["id"])
        source_path = str(ji.get("source_path") or "")
        try:
            update_job_item(iid, status="running", stage="inspect", message="Reading archive catalogue in the background; this page can be left safely")
            result = await asyncio.to_thread(archive_media.inspect_archive, source_path, force=True)
            completed += 1
            update_job_item(
                iid, status="complete", stage="complete",
                message=f"Catalogue cached: {result.get('media_count', 0)} video member(s) · {result.get('health', 'unknown')} archive",
            )
            log_event("info", "archive_recovery", "inspect_job_complete", f"Inspected {Path(source_path).name}", {"job_id": job_id, "media": result.get("media_count", 0)})
        except Exception as exc:
            failed += 1
            update_job_item(iid, status="error", stage="error", message=str(exc))
            log_event("error", "archive_recovery", "inspect_job_failed", str(exc), {"source": source_path, "job_id": job_id})
        update_job(job_id, completed=completed, failed=failed, message=f"{completed} inspected, {failed} failed")
    update_job(job_id, status="complete_with_errors" if failed else "complete", completed=completed, failed=failed, message=f"Finished: {completed} archive(s) inspected, {failed} failed")


async def run_archive_verify_job(job_id: int):
    job, items = get_job(job_id)
    if not job:
        return
    update_job(job_id, status="running", message="RAR media verification in progress")
    completed = failed = 0
    for ji in items:
        iid = int(ji["id"])
        source_path = str(ji.get("source_path") or "")
        try:
            update_job_item(iid, status="running", stage="verify", message="Testing video members only; support files and torrent padding are ignored")
            row = next((x for x in archive_media.scan_archives(True, 2000) if x.get("logical_path") == source_path), None)
            if not row:
                raise RuntimeError("RAR source is no longer present in the DMM source tree")
            def _progress(done: int, total: int, member: str, member_status: str):
                update_job_item(iid, status="running", stage="archive_verify", message=f"Verified {done}/{total}: {Path(member).name} · {member_status}")
            result = await asyncio.to_thread(archive_media.verify_archive_media, source_path, expected_fingerprint=str(row.get("fingerprint") or ""), progress=_progress)
            completed += 1
            update_job_item(
                iid, status="complete", stage="complete",
                message=f"Verified {result.get('verified_count', 0)} media file(s); {result.get('failed_count', 0)} failed; {result.get('untested_count', 0)} unverified",
            )
            log_event("info", "archive_recovery", "verify_job_complete", f"Verified {Path(source_path).name}", {"job_id": job_id, "verified": result.get("verified_count", 0), "failed": result.get("failed_count", 0)})
        except Exception as exc:
            failed += 1
            update_job_item(iid, status="error", stage="error", message=str(exc))
            log_event("error", "archive_recovery", "verify_job_failed", str(exc), {"source": source_path, "job_id": job_id})
        update_job(job_id, completed=completed, failed=failed, message=f"{completed} verified, {failed} failed")
    update_job(job_id, status="complete_with_errors" if failed else "complete", completed=completed, failed=failed, message=f"Finished: {completed} archive(s) verified, {failed} failed")


async def run_archive_extract_job(job_id: int):
    job, items = get_job(job_id)
    if not job:
        return
    selection = cache_get(f"archive_recovery:job_selection:{job_id}") or {}
    selected_media = [str(x) for x in (selection.get("media_paths") or []) if str(x)] if isinstance(selection, dict) else []
    update_job(job_id, status="running", message="Verified media recovery in progress")
    completed = failed = 0
    for ji in items:
        iid = int(ji["id"])
        source_path = str(ji.get("source_path") or "")
        try:
            update_job_item(iid, status="running", stage="inspect", message="Revalidating stable archive identity, media catalogue, verification state and free space")
            row = next((x for x in archive_media.scan_archives(True, 2000) if x.get("logical_path") == source_path), None)
            if not row:
                raise RuntimeError("RAR source is no longer present in the DMM source tree")
            update_job_item(iid, stage="extract", message="Recovering selected verified video members only")
            result = await asyncio.to_thread(
                archive_media.extract_archive, source_path,
                expected_fingerprint=str(row.get("fingerprint") or ""), selected_media=selected_media,
            )
            completed += 1
            invalidate_scan_cache()
            _INBOX_SNAPSHOT.clear()
            target = str(result.get("target") or "")
            skipped = len(result.get("failed_after_extract") or [])
            update_job_item(iid, status="complete", stage="complete", message=f"Recovered {result.get('recovered', 0)} verified media file(s); {skipped} skipped → {target}")
            log_event("info", "archive_recovery", "job_complete", f"Recovered {Path(source_path).name}", {"target": target, "job_id": job_id, "recovered": result.get("recovered", 0), "skipped": skipped})
        except Exception as exc:
            failed += 1
            update_job_item(iid, status="error", stage="error", message=str(exc))
            log_event("error", "archive_recovery", "job_failed", str(exc), {"source": source_path, "job_id": job_id})
        update_job(job_id, completed=completed, failed=failed, message=f"{completed} recovered, {failed} failed")
    update_job(job_id, status="complete_with_errors" if failed else "complete", completed=completed, failed=failed, message=f"Finished: {completed} recovered, {failed} failed")


@app.get("/maintenance/archives", response_class=HTMLResponse)
async def archived_media_page(request: Request, refresh: int = 0, inspect_path: str = "", identity_q: str = "", identity_type: str = "tv", notice: str = "", error: str = ""):
    require_admin(request)
    rows = []
    inspection = None
    identity_results = []
    page_error = error
    storage = None
    try:
        rows = await asyncio.to_thread(archive_media.scan_archives, bool(refresh), 1000)
        if inspect_path:
            if not is_within_logical(inspect_path, source_root()):
                raise RuntimeError("Archive path is outside the DMM source root")
            inspection = await asyncio.to_thread(archive_media.cached_inspection, inspect_path)
            if inspection is None:
                page_error = "This archive has not completed a background inspection yet. Start Inspect from the archive list and return when that job finishes."
            else:
                storage = await asyncio.to_thread(archive_media.storage_state, int((inspection or {}).get("unpacked_size") or 0))
                if identity_q.strip():
                    identity_results = await media_identity.search_tmdb(identity_q.strip(), identity_type or "tv", limit=12)
    except Exception as exc:
        page_error = str(exc)
    return templates.TemplateResponse("archive_media.html", {
        "request": request, "rows": rows, "inspection": inspection,
        "identity_results": identity_results, "identity_q": identity_q, "identity_type": identity_type,
        "notice": notice, "error": page_error, "storage": storage,
        "recovery_root": archive_media.extraction_root(),
        "max_extract_gb": int(archive_media.max_extract_bytes() / 1024**3),
        "tmdb_configured": media_identity.tmdb_configured(),
        "extractor_state": archive_media.extractor_state(),
    })


@app.post("/maintenance/archives/settings")
async def archived_media_settings(request: Request):
    require_admin(request)
    form = await request.form()
    try:
        archive_media.save_settings(root=str(form.get("recovery_root") or ""), max_gb=int(form.get("max_extract_gb") or 100))
        key = str(form.get("tmdb_api_key") or "").strip()
        if key:
            media_identity.save_tmdb_api_key(key)
        return RedirectResponse("/maintenance/archives?notice=" + quote("Archived Media Recovery settings saved"), status_code=303)
    except Exception as exc:
        return RedirectResponse("/maintenance/archives?error=" + quote(str(exc)), status_code=303)


@app.post("/maintenance/archives/ignore")
async def archived_media_ignore(request: Request):
    require_admin(request)
    form = await request.form()
    source_path = str(form.get("source_path") or "")
    fingerprint = str(form.get("fingerprint") or "")
    ignored = str(form.get("ignored") or "1").lower() in {"1", "true", "yes", "on"}
    if not is_within_logical(source_path, source_root()):
        raise HTTPException(400, "Invalid archive source")
    archive_media.set_ignored(source_path, fingerprint, ignored)
    return RedirectResponse("/maintenance/archives?refresh=1", status_code=303)


@app.post("/maintenance/archives/identity")
async def archived_media_identity(request: Request):
    require_admin(request)
    form = await request.form()
    source_path = str(form.get("source_path") or "").strip()
    if not is_within_logical(source_path, source_root()):
        raise HTTPException(400, "Invalid archive source")
    row = next((x for x in archive_media.scan_archives(True, 2000) if x.get("logical_path") == source_path), None)
    if not row:
        raise HTTPException(404, "Archive source was not found")
    identity = media_identity.save_identity(source_path, str(row.get("fingerprint") or ""), {
        "media_type": str(form.get("media_type") or "tv"),
        "title": str(form.get("title") or "").strip(),
        "year": int(form.get("year") or 0) or None,
        "tmdb_id": int(form.get("tmdb_id") or 0) or None,
        "poster": str(form.get("poster") or ""),
        "overview": str(form.get("overview") or ""),
        "confidence": int(form.get("confidence") or 100),
        "source": "tmdb",
    })
    add_activity("media_identity", identity["title"], "Resolved archived source identity via TMDb", source_path)
    _INBOX_SNAPSHOT.clear()
    return RedirectResponse("/maintenance/archives?inspect_path=" + quote(source_path) + "&notice=" + quote(f"Identity set to {identity['title']}"), status_code=303)


@app.post("/maintenance/archives/inspect")
async def archived_media_inspect(request: Request):
    require_admin(request)
    form = await request.form()
    source_path = str(form.get("source_path") or "").strip()
    if not is_within_logical(source_path, source_root()):
        raise HTTPException(400, "Invalid archive source")
    row = next((x for x in archive_media.scan_archives(True, 2000) if x.get("logical_path") == source_path), None)
    if not row:
        raise HTTPException(404, "Archive source was not found")
    jid = create_job("archive_inspect", [{"source_path": source_path, "display_name": Path(source_path).name, "destination_key": "background catalogue"}])
    _launch(run_archive_inspect_job(jid))
    return RedirectResponse(f"/jobs/{jid}", status_code=303)


@app.post("/maintenance/archives/verify")
async def archived_media_verify(request: Request):
    require_admin(request)
    form = await request.form()
    source_path = str(form.get("source_path") or "").strip()
    if not is_within_logical(source_path, source_root()):
        raise HTTPException(400, "Invalid archive source")
    row = next((x for x in archive_media.scan_archives(True, 2000) if x.get("logical_path") == source_path), None)
    if not row:
        raise HTTPException(404, "Archive source was not found")
    jid = create_job("archive_verify", [{"source_path": source_path, "display_name": Path(source_path).name, "destination_key": "media-only verification"}])
    _launch(run_archive_verify_job(jid))
    return RedirectResponse(f"/jobs/{jid}", status_code=303)


@app.post("/maintenance/archives/extract")
async def archived_media_extract(request: Request):
    require_admin(request)
    form = await request.form()
    source_path = str(form.get("source_path") or "").strip()
    selected_media = [str(x) for x in form.getlist("media_path") if str(x)]
    if not is_within_logical(source_path, source_root()):
        raise HTTPException(400, "Invalid archive source")
    row = next((x for x in archive_media.scan_archives(True, 2000) if x.get("logical_path") == source_path), None)
    if not row:
        raise HTTPException(404, "Archive source was not found")
    verification = row.get("verification") if isinstance(row, dict) else None
    verified = {str(x.get("path") or "") for x in ((verification or {}).get("members") or []) if x.get("status") == "verified"}
    if not selected_media:
        raise HTTPException(400, "Select at least one verified media file to recover")
    if any(x not in verified for x in selected_media):
        raise HTTPException(400, "Recovery selection contains a failed or unverified media file")
    jid = create_job("archive_extract", [{"source_path": source_path, "display_name": Path(source_path).name, "destination_key": "verified media only"}])
    cache_set(f"archive_recovery:job_selection:{jid}", {"media_paths": selected_media, "fingerprint": str(row.get("fingerprint") or "")})
    _launch(run_archive_extract_job(jid))
    return RedirectResponse(f"/jobs/{jid}", status_code=303)


# ===== ARRNEXUS V10.3 ARCHIVE RESCUE ========================================

@app.get("/archive-rescue", response_class=HTMLResponse)
async def archive_rescue_page(request: Request, scan: int = 0, search_archive: int = 0, q: str = "", notice: str = "", error: str = ""):
    require_admin(request)
    missing = []
    results = []
    indexers = []
    try:
        indexers = await archive_rescue.internet_archive_indexers()
        if search_archive:
            missing = await archive_rescue.search_missing_archive(30)
        elif scan:
            missing = await archive_rescue.scan_missing_sonarr()
        if q.strip():
            results = await archive_rescue.search_archive(q.strip(), 80)
    except Exception as exc:
        error = error or str(exc)
    return templates.TemplateResponse("archive_rescue.html", {
        "request": request, "missing": missing, "results": results, "query": q,
        "indexers": indexers, "notice": notice, "error": error, "rd_connected": rd.connected(),
    })


@app.get("/archive-rescue/release/{token}", response_class=HTMLResponse)
async def archive_rescue_release(request: Request, token: str):
    require_admin(request)
    try:
        manifest = await archive_rescue.release_manifest(token)
        return templates.TemplateResponse("archive_rescue_release.html", {"request": request, "manifest": manifest, "rd_connected": rd.connected(), "error": "", "notice": ""})
    except Exception as exc:
        return templates.TemplateResponse("archive_rescue_release.html", {"request": request, "manifest": None, "rd_connected": rd.connected(), "error": str(exc), "notice": ""}, status_code=400)


@app.post("/archive-rescue/send-rd")
async def archive_rescue_send_rd(request: Request):
    require_admin(request)
    form = await request.form()
    token = str(form.get("token") or "").strip()
    selected = [str(x) for x in form.getlist("file_path")]
    if not token:
        raise HTTPException(400, "Archive Rescue token is missing")
    try:
        result = await archive_rescue.send_release_to_realdebrid(token, selected)
        msg = f"Sent to Real-Debrid as torrent {result.get('torrent_id')}. Decypharr/DMM will expose it when the provider source becomes available."
        return RedirectResponse("/archive-rescue?notice=" + quote(msg), status_code=303)
    except Exception as exc:
        return RedirectResponse("/archive-rescue?error=" + quote(str(exc)), status_code=303)


# ===== ARRNEXUS V10.2 LISTS / AIOMETADATA / PROVIDER CLEANUP ================

def _v102_admin(request: Request) -> dict:
    require_admin(request)
    return current_user(request)


def _v102_truth(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _v102_origin(request: Request) -> str:
    configured = (setting_get("app.public_url", "") or "").strip().rstrip("/")
    if configured:
        return configured
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    forwarded_host = str(request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).split(",")[0].strip()
    return f"{forwarded_proto}://{forwarded_host}".rstrip("/")


async def _v102_lists_context(request: Request, notice: str = "", error: str = "", preview_list_id: int = 0, preview: dict | None = None):
    state = media_lists.provider_state()
    trakt_lists = []
    if state.get("trakt_connected"):
        try:
            trakt_lists = await media_lists.trakt_personal_lists()
        except Exception as exc:
            if not error:
                error = f"Trakt personal lists could not be loaded: {exc}"
    return {
        "request": request, "notice": notice, "error": error,
        "lists": media_lists.list_definitions(), "runs": media_lists.list_runs(limit=30),
        "source_types": media_lists.SOURCE_TYPES, "strategies": STRATEGIES,
        "movie_destinations": sorted(movie_roots()), "tv_destinations": sorted(tv_roots()),
        "provider_state": state, "trakt_lists": trakt_lists,
        "preview_list_id": int(preview_list_id or 0), "preview": preview,
    }


@app.get("/lists", response_class=HTMLResponse)
async def lists_page(request: Request, notice: str = "", error: str = ""):
    _v102_admin(request)
    return templates.TemplateResponse("lists.html", await _v102_lists_context(request, notice, error))


@app.post("/lists/providers")
async def lists_provider_save(request: Request):
    _v102_admin(request)
    form = await request.form()
    try:
        media_lists.save_trakt_app(str(form.get("trakt_client_id") or ""), str(form.get("trakt_client_secret") or ""))
        media_lists.save_tmdb(str(form.get("tmdb_api_key") or ""))
        media_lists.save_simkl(str(form.get("simkl_client_id") or ""), str(form.get("simkl_access_token") or ""))
        log_event("info", "lists", "provider_settings_saved", "List provider settings updated")
        return RedirectResponse("/lists?notice=" + quote("List provider settings saved"), status_code=303)
    except Exception as exc:
        return RedirectResponse("/lists?error=" + quote(str(exc)), status_code=303)


@app.post("/lists/trakt/device/start")
async def lists_trakt_device_start(request: Request):
    _v102_admin(request)
    try:
        data = await media_lists.trakt_device_begin()
        log_event("info", "lists", "trakt_device_started", "Trakt device authorization started")
        return RedirectResponse("/lists?notice=" + quote(f"Trakt code {data.get('user_code')} created. Open Trakt and authorize ArrNexus, then click Check authorization."), status_code=303)
    except Exception as exc:
        return RedirectResponse("/lists?error=" + quote(str(exc)), status_code=303)


@app.post("/lists/trakt/device/poll")
async def lists_trakt_device_poll(request: Request):
    _v102_admin(request)
    try:
        result = await media_lists.trakt_device_poll()
        status = result.get("status")
        if status == "connected":
            log_event("info", "lists", "trakt_connected", "Trakt account connected through Device OAuth")
            return RedirectResponse("/lists?notice=" + quote("Trakt connected successfully"), status_code=303)
        return RedirectResponse("/lists?notice=" + quote(str(result.get("message") or "Trakt authorization is still pending")), status_code=303)
    except Exception as exc:
        text = str(exc)
        if "limit" in text.lower() or "community" in text.lower():
            text += " Your Trakt account may have reached its Connected Apps limit; review Trakt Settings → Connected Apps."
        return RedirectResponse("/lists?error=" + quote(text), status_code=303)


@app.get("/lists/trakt/connect")
async def lists_trakt_connect(request: Request):
    _v102_admin(request)
    state = secrets.token_urlsafe(24)
    request.session["trakt_oauth_state"] = state
    redirect_uri = _v102_origin(request) + "/lists/trakt/callback"
    setting_set("lists.trakt.redirect_uri", redirect_uri)
    try:
        url = media_lists.trakt_authorize_url(redirect_uri, state)
        return RedirectResponse(url, status_code=303)
    except Exception as exc:
        return RedirectResponse("/lists?error=" + quote(str(exc)), status_code=303)


@app.get("/lists/trakt/callback")
async def lists_trakt_callback(request: Request, code: str = "", state: str = ""):
    _v102_admin(request)
    expected = str(request.session.pop("trakt_oauth_state", "") or "")
    if not expected or not secrets.compare_digest(expected, str(state or "")):
        return RedirectResponse("/lists?error=" + quote("Trakt OAuth state did not match; connection was refused"), status_code=303)
    if not code:
        return RedirectResponse("/lists?error=" + quote("Trakt did not return an authorization code"), status_code=303)
    redirect_uri = setting_get("lists.trakt.redirect_uri") or (_v102_origin(request) + "/lists/trakt/callback")
    try:
        await media_lists.trakt_exchange_code(code, redirect_uri)
        log_event("info", "lists", "trakt_connected", "Trakt account connected")
        return RedirectResponse("/lists?notice=" + quote("Trakt connected"), status_code=303)
    except Exception as exc:
        return RedirectResponse("/lists?error=" + quote(str(exc)), status_code=303)


@app.post("/lists/trakt/disconnect")
async def lists_trakt_disconnect(request: Request):
    _v102_admin(request)
    media_lists.trakt_disconnect()
    log_event("warning", "lists", "trakt_disconnected", "Trakt account disconnected")
    return RedirectResponse("/lists?notice=" + quote("Trakt disconnected"), status_code=303)


@app.post("/lists/save")
async def lists_save(request: Request):
    _v102_admin(request)
    form = await request.form()
    try:
        raw_id = str(form.get("list_id") or "").strip()
        list_id = int(raw_id) if raw_id.isdigit() else None
        saved = media_lists.save_definition(
            list_id=list_id, name=str(form.get("name") or ""), source_type=str(form.get("source_type") or ""),
            source_ref=str(form.get("source_ref") or ""), media_type=str(form.get("media_type") or "mixed"),
            movie_destination=str(form.get("movie_destination") or "auto"), tv_destination=str(form.get("tv_destination") or "auto"),
            acquisition_strategy=str(form.get("acquisition_strategy") or "automatic"), monitor=_v102_truth(form.get("monitor")),
            search_automatically=_v102_truth(form.get("search_automatically")), enabled=_v102_truth(form.get("enabled")),
            sync_interval_hours=int(form.get("sync_interval_hours") or 12),
        )
        log_event("info", "lists", "definition_saved", f"List #{saved} saved", {"list_id": saved})
        return RedirectResponse("/lists?notice=" + quote("List automation saved"), status_code=303)
    except Exception as exc:
        return RedirectResponse("/lists?error=" + quote(str(exc)), status_code=303)


@app.post("/lists/{list_id}/delete")
async def lists_delete(request: Request, list_id: int):
    _v102_admin(request)
    media_lists.delete_definition(list_id)
    log_event("warning", "lists", "definition_deleted", f"List #{list_id} deleted")
    return RedirectResponse("/lists?notice=" + quote("List automation deleted"), status_code=303)


@app.get("/lists/{list_id}/preview", response_class=HTMLResponse)
async def lists_preview(request: Request, list_id: int):
    _v102_admin(request)
    defn = media_lists.get_definition(list_id)
    if not defn:
        raise HTTPException(404, "List not found")
    try:
        preview = await media_lists.sync_definition(defn, preview=True, user_id=int(current_user(request).get("id") or 0))
        return templates.TemplateResponse("lists.html", await _v102_lists_context(request, preview_list_id=list_id, preview=preview))
    except Exception as exc:
        return templates.TemplateResponse("lists.html", await _v102_lists_context(request, error=str(exc), preview_list_id=list_id))


@app.post("/lists/{list_id}/sync")
async def lists_sync(request: Request, list_id: int):
    user = _v102_admin(request)
    defn = media_lists.get_definition(list_id)
    if not defn:
        raise HTTPException(404, "List not found")
    try:
        result = await media_lists.sync_definition(defn, preview=False, user_id=int(user.get("id") or 0))
        msg = f"List sync complete: {result.get('added', 0)} added, {result.get('unmatched', 0)} unmatched"
        return RedirectResponse("/lists?notice=" + quote(msg), status_code=303)
    except Exception as exc:
        log_event("error", "lists", "manual_sync_failed", str(exc), {"list_id": list_id})
        return RedirectResponse("/lists?error=" + quote(str(exc)), status_code=303)


@app.get("/api/lists")
async def lists_api(request: Request):
    _v102_admin(request)
    return JSONResponse({"lists": media_lists.list_definitions(), "providers": media_lists.provider_state()})


@app.get("/aiometadata", response_class=HTMLResponse)
async def aiometadata_page(request: Request, notice: str = "", error: str = ""):
    _v102_admin(request)
    raw = aiometadata_integration.connection(mask=False)
    state = await aiometadata_integration.page_state()
    return templates.TemplateResponse("aiometadata.html", {"request": request, "state": state, "raw": raw, "notice": notice, "error": error})


@app.post("/aiometadata/save")
async def aiometadata_save(request: Request):
    _v102_admin(request)
    form = await request.form()
    try:
        aiometadata_integration.save_connection(str(form.get("url") or ""), str(form.get("user_uuid") or ""), str(form.get("password") or ""), str(form.get("manifest_url") or ""))
        state = await aiometadata_integration.health()
        if not state.get("ok"):
            raise RuntimeError(state.get("reason") or "AIOMetadata health check failed")
        log_event("info", "aiometadata", "connection_saved", "AIOMetadata connection verified")
        return RedirectResponse("/aiometadata?notice=" + quote("AIOMetadata saved and health check passed"), status_code=303)
    except Exception as exc:
        log_event("warning", "aiometadata", "connection_failed", str(exc))
        return RedirectResponse("/aiometadata?error=" + quote(str(exc)), status_code=303)


@app.get("/api/aiometadata/status")
async def aiometadata_status_api(request: Request):
    _v102_admin(request)
    return JSONResponse(await aiometadata_integration.page_state())


@app.get("/maintenance/provider-cleanup", response_class=HTMLResponse)
async def provider_cleanup_page(request: Request, notice: str = "", error: str = ""):
    _v102_admin(request)
    try:
        preview = await asyncio.to_thread(provider_cleanup_tools.scan_provider_cleanup)
    except Exception as exc:
        preview = {"rows": [], "digest": "", "duplicate_groups": 0, "recommended_links": 0, "provider_candidates": 0}
        error = error or f"Provider dependency scan unavailable: {exc}"
    return templates.TemplateResponse("provider_cleanup.html", {"request": request, "preview": preview, "notice": notice, "error": error})


@app.post("/maintenance/provider-cleanup/apply")
async def provider_cleanup_apply(request: Request, digest: str = Form(...), action: str = Form("both")):
    _v102_admin(request)
    try:
        result = await provider_cleanup_tools.apply_provider_cleanup(digest, action)
        deleted = sum(1 for x in result.get("provider_cleanup") or [] if x.get("deleted"))
        msg = f"Provider cleanup complete: {len(result.get('removed_links') or [])} link(s) removed, {deleted} exact RD source(s) deleted"
        log_event("warning", "provider_cleanup", "apply", msg, {"action": action})
        return RedirectResponse("/maintenance/provider-cleanup?notice=" + quote(msg), status_code=303)
    except Exception as exc:
        log_event("error", "provider_cleanup", "apply_failed", str(exc), {"action": action})
        return RedirectResponse("/maintenance/provider-cleanup?error=" + quote(str(exc)), status_code=303)
# ===== END ARRNEXUS V10.2 FEATURES ===========================================
