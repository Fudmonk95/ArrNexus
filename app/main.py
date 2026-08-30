from __future__ import annotations
import asyncio
import json
import secrets
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
    update_user_access, requests_today, title_timeline, all_settings, replace_nonsecret_settings, request_rows,
)
from .scanner import scan_source, inspect_item, normalize_title, human_size
from .routing import decide_movie, decide_tv
from .arr import RadarrClient, SonarrClient, LidarrClient, ProwlarrClient, ArrError, poster_url
from .router_service import import_one, route_item, discover_lookup, discover_add, client_for_instance
from .importer import ImportErrorSafe, unlink_created, scan_broken_symlinks, repair_broken_symlink
from .namespace import view_path, is_within_logical, namespace_status, NamespaceError
from .instances import discover_instances
from .library import inventory_roots, build_source_link_index
from .jellyfin import search_jellyfin, jellyfin_status
from .music import (
    search_musicbrainz, trending_artists, trending_releases, itunes_search,
    external_music_links, GENRES, audius_trending, audius_search,
    provider_catalog, enrich_artist_art, enrich_release_art, representative_artwork,
    internet_archive_search, jamendo_search, soundcloud_search, lastfm_search,
    lastfm_top, provider_featured, provider_search,
)
from .connections import get_connection, save_connection
from .paths import movie_roots, tv_roots, lidarr_root, source_root, all_library_roots, dumb_root
from .seerr import SeerrClient, SeerrError, result_rows as seerr_result_rows
from . import realdebrid as rd
from .policy import load_policy, score_release
from .tvpacks import classify_release, pack_matches, coverage_summary, choose_best_complete, choose_best_season_packs
from .notifications import send_notification
from .admin_tools import create_database_backup, list_backups, sanitized_config, diagnostics_zip
from .plugins import load_catalog_plugins, plugin_search_url
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

BASE = Path(__file__).resolve().parent
app = FastAPI(title="ArrNexus")
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.filters["human_size"] = human_size
templates.env.globals["app_setting"] = setting_get

APP_VERSION = "5.0.0"
RUNNING_TASKS: set[asyncio.Task] = set()


@app.on_event("startup")
async def startup():
    init_db()
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
    return {"ok": True, "app": "ArrNexus", "namespace": bool(ns.get("ok"))}


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
        setting_set("setup.complete", "true")
        log_event("info", "setup", "first_run_complete", f"Administrator {username} created")
        request.session.clear(); request.session["auth"] = True; request.session["user_id"] = uid
        request.session["theme"] = "nexus"; request.session["display_name"] = display_name or username
        return RedirectResponse("/settings?notice=" + quote("Welcome to ArrNexus. Configure your connections and library paths here."), status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/setup?error={quote(str(exc))}", status_code=303)


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
            link = str(request.base_url).rstrip('/') + f"/reset-password?token={quote(token)}"
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
    request.session["theme"] = user.get("theme") or "nexus"
    request.session["display_name"] = user.get("display_name") or user.get("username")
    return RedirectResponse("/", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


async def arr_status(client):
    try:
        s = await client.status()
        return {"ok": True, "version": s.get("version"), "appName": s.get("appName") or client.name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _title_map(entries: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for x in entries or []:
        out.setdefault(normalize_title(x.get("title", "")), []).append(x)
    return out


async def _instance_catalogs():
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
        match = None
        match_inst = None
        candidates = movie_map.get(normalize_title(item.title_guess), []) if item.media_type == "movie" else tv_map.get(normalize_title(item.title_guess), [])
        for candidate, inst in candidates:
            if item.year_guess and candidate.get("year") and int(candidate.get("year")) != int(item.year_guess):
                continue
            match, match_inst = candidate, inst
            break

        metadata = match
        lookup = []
        cache_key = f"lookup:{item.media_type}:{normalize_title(item.title_guess)}:{item.year_guess or 0}"
        if metadata is None:
            lookup = cache_get(cache_key) or []
            if not lookup:
                async with sem:
                    try:
                        lookup = await (RadarrClient().lookup(f"{item.title_guess} {item.year_guess or ''}".strip()) if item.media_type == "movie" else SonarrClient().lookup(item.title_guess))
                    except Exception:
                        lookup = []
                lookup = (lookup or [])[:8]
                if lookup:
                    cache_set(cache_key, lookup)
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
        jf = {"configured": bool(jf_conn.api_key), "found": False}
        if jf_conn.api_key and state in {"imported", "linked"}:
            async with sem:
                jf = await search_jellyfin(item.title_guess, 3)

        display_title = (metadata or {}).get("title") or item.title_guess
        display_year = (metadata or {}).get("year") or item.year_guess
        external_id = (metadata or {}).get("tmdbId") if item.media_type == "movie" else (metadata or {}).get("tvdbId")
        # Title/year is the stable grouping key across mixed-quality RD release
        # names. Using an external ID for only some copies caused duplicate cards
        # when one release matched metadata and another did not.
        canonical_key = f"{item.media_type}:{normalize_title(display_title)}:{display_year or 0}"
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
        group = sorted(
            group,
            key=lambda r: (r["item"].quality or 0, r["item"].size_bytes or 0, state_rank.get(r.get("state"), 0)),
            reverse=True,
        )
        primary = group[0]
        managed = [r for r in group if r.get("state") in {"imported", "linked"} or r.get("linked_paths")]
        ignored = len(group) and all(r.get("state") == "ignored" for r in group)
        primary["duplicate_count"] = len(group)
        primary["duplicate_sources"] = [r["item"].path for r in group]
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
        else:
            primary["state"] = "waiting"
        primary["duplicate"] = len(group) > 1
        primary["upgrade"] = bool(primary["item"].quality and primary["existing_resolution"] and primary["item"].quality > primary["existing_resolution"])
        out.append(primary)
    return sorted(out, key=lambda r: (r.get("display_title") or "").lower())


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    require_auth(request)
    try:
        items = scan_source()
    except Exception as exc:
        items = []
        log_event("warning","namespace","source_scan_unavailable",str(exc))
    imports = successful_imports_by_source(); states = item_states()
    try: links = build_source_link_index()
    except Exception: links = {}
    imported = sum(1 for x in items if x.path in imports or x.path in links)
    ignored = sum(1 for x in items if (states.get(x.path) or {}).get("state") == "ignored")
    waiting = max(0, len(items) - imported - ignored)
    statuses = {"radarr": await arr_status(RadarrClient()), "sonarr": await arr_status(SonarrClient()), "lidarr": await arr_status(LidarrClient()), "prowlarr": await arr_status(ProwlarrClient())}
    try:
        js=await jellyfin_status(); statuses["jellyfin"]={"ok":True,"version":js.get("Version") or js.get("version") or "Connected"}
    except Exception as exc: statuses["jellyfin"]={"ok":False,"error":str(exc)}
    try: statuses["seerr"] = await arr_status(SeerrClient())
    except Exception: statuses["seerr"] = {"ok": False, "error": "not configured"}
    queue_counts = {"radarr":0,"sonarr":0,"lidarr":0}; library_movie_count=library_tv_count=0
    route_inventory=[]
    for inst in discover_instances():
        if inst.service not in {"radarr","sonarr","lidarr"} or not inst.api_key: continue
        try:
            c=client_for_instance(inst)
            if inst.service=="radarr": rows=await c.movies(); library_movie_count += len(rows or [])
            elif inst.service=="sonarr": rows=await c.series(); library_tv_count += len(rows or [])
            else: rows=await c.artists()
            route_inventory.append({"service":inst.service,"instance":inst.instance,"route":inst.destination_key or "default","count":len(rows or [])})
            q=await c.queue(200); rec=q.get("records",[]) if isinstance(q,dict) else (q or [])
            queue_counts[inst.service] = queue_counts.get(inst.service,0) + len(rec)
        except Exception: pass
    dest_counts={}
    for row in recent_imports(5000):
        if row["status"] in {"complete","linked"} and not row["undone"]:
            key=f"{row['arr_name'] or row['media_type']}:{row['destination_key']}"; dest_counts[key]=dest_counts.get(key,0)+1
    rd_user=None
    if rd.connected():
        try: rd_user=await rd.user()
        except Exception: rd_user={"error":"Connected, but account status could not be loaded"}
    activity_days=activity_by_day(7); dest_top=sorted(dest_counts.items(),key=lambda x:x[1],reverse=True)[:10]
    inv=inventory_roots(); scrape_rows=list_scrapes(200,"all"); scraping_count=sum(1 for x in scrape_rows if (x.get("status") or "") in {"searching","queued","running"})
    active_jobs=[dict(x) for x in recent_jobs(20) if x["status"] in {"queued","running"}]
    user=get_user(int(request.session.get("user_id") or 0)) or {}
    return templates.TemplateResponse("dashboard.html",{
        "request":request,"items":items[:10],"source_count":len(items),"movie_count":sum(1 for i in items if i.media_type=="movie"),"tv_count":sum(1 for i in items if i.media_type=="tv"),
        "library_movie_count":library_movie_count,"library_tv_count":library_tv_count,"route_inventory":route_inventory,"libraries":inv,
        "imported_count":imported,"waiting_count":waiting,"ignored_count":ignored,"statuses":statuses,"queue_counts":queue_counts,"scraping_count":scraping_count,"active_jobs":active_jobs,
        "dest_counts":dest_top,"dest_max":max([x[1] for x in dest_top],default=1),"activity_days":activity_days,"activity_max":max([int(x.get("count") or 0) for x in activity_days],default=1),
        "recent":recent_imports(8),"activity":recent_activity(12),"jobs":recent_jobs(6),"source_root":source_root(),"namespace":namespace_status(),"rd_connected":rd.connected(),"rd_user":rd_user,
        "dashboard_layout":user.get("dashboard_layout") or "default",
    })


@app.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request, q: str = "", status: str = "all", media_type: str = "all", view: str = "grid"):
    require_auth(request)
    items = scan_source()
    if media_type in {"movie", "tv"}:
        items = [x for x in items if x.media_type == media_type]
    enriched = dedupe_rows(await enrich_items(items))
    if q:
        nq = normalize_title(q)
        enriched = [x for x in enriched if nq in normalize_title(x.get("display_title") or "") or nq in normalize_title(x["item"].name) or nq in normalize_title(x["item"].title_guess)]
    counts = {
        "all": len(enriched),
        "waiting": sum(1 for x in enriched if x["state"] == "waiting"),
        "imported": sum(1 for x in enriched if x["state"] in {"imported", "linked"}),
        "ignored": sum(1 for x in enriched if x["state"] == "ignored"),
        "duplicate": sum(1 for x in enriched if x.get("duplicate_count", 1) > 1),
        "upgrade": sum(1 for x in enriched if x.get("upgrade")),
    }
    if status != "all":
        if status == "upgrade":
            enriched = [x for x in enriched if x["upgrade"]]
        elif status == "duplicate":
            enriched = [x for x in enriched if x.get("duplicate_count", 1) > 1]
        elif status == "imported":
            enriched = [x for x in enriched if x["state"] in {"imported", "linked"}]
        else:
            enriched = [x for x in enriched if x["state"] == status]
    return templates.TemplateResponse("inbox.html", {
        "request": request,
        "rows": enriched,
        "counts": counts,
        "movie_roots": movie_roots(),
        "tv_roots": tv_roots(),
        "q": q, "status": status, "media_type": media_type, "view": view,
    })


@app.get("/item", response_class=HTMLResponse)
async def item_detail(request: Request, path: str):
    require_auth(request)
    src = Path(path)
    if not is_within_logical(src, source_root()):
        raise HTTPException(400, "Invalid source path")
    try:
        if not view_path(src).exists():
            raise HTTPException(404, "Source not found")
    except NamespaceError as exc:
        raise HTTPException(503, str(exc))
    item = inspect_item(src)
    routed = await route_item(item)
    meta = routed.get("metadata") or routed.get("existing") or ((routed.get("lookup") or [{}])[0] if routed.get("lookup") else {})
    display_title = meta.get("title") or item.title_guess
    display_year = meta.get("year") or item.year_guess
    jf_conn = get_connection("jellyfin")
    jf = await search_jellyfin(display_title, 10) if jf_conn.api_key else {"configured": False, "found": False, "items": []}
    return templates.TemplateResponse("item.html", {
        "request": request,
        "item": item,
        "display_title": display_title, "display_year": display_year,
        "existing": routed["existing"],
        "instance": routed["existing_instance"],
        "lookup": routed["lookup"],
        "decision": routed["decision"],
        "poster": routed["poster"],
        "roots": movie_roots() if item.media_type == "movie" else tv_roots(),
        "upgrade": routed["upgrade"],
        "existing_resolution": routed["existing_resolution"],
        "jellyfin": jf,
        "history": latest_success_for_source(item.path),
    })


@app.post("/item/state")
async def item_state(request: Request, source_path: str = Form(...), state: str = Form(...), note: str = Form("")):
    require_auth(request)
    if state not in {"waiting", "ignored"}:
        raise HTTPException(400, "Invalid state")
    set_item_state(source_path, state, note)
    add_activity("state", Path(source_path).name, f"Marked {state}", source_path)
    return RedirectResponse("/inbox", status_code=303)


async def run_import_job(job_id: int):
    job, job_items = get_job(job_id)
    if not job:
        return
    update_job(job_id, status="running", message="Import in progress")
    completed = failed = 0
    for ji in job_items:
        iid = int(ji["id"])
        source_path = ji["source_path"]
        try:
            update_job_item(iid, status="running", stage="identifying", message="Identifying and routing")
            item = inspect_item(source_path)
            update_job_item(iid, stage="matching", message=f"Matching {item.media_type} in Arr")
            dest = ji.get("destination_key")
            if not dest or dest == "auto":
                routed = await route_item(item)
                dest = routed["decision"].key
                update_job_item(iid, destination_key=dest, stage="linking", message=f"Routing to {dest}")
            result = await import_one(source_path, dest)
            update_job_item(iid, status="complete", stage="complete", message=f"Imported to {result['arr_instance']} / {result['destination_key']}")
            log_event("info","import","item_complete",Path(source_path).name,{"job_id":job_id,"destination":result.get("destination_key"),"arr_instance":result.get("arr_instance")})
            completed += 1
        except Exception as exc:
            update_job_item(iid, status="error", stage="error", message=str(exc))
            log_event("error","import","item_failed",str(exc),{"job_id":job_id,"source":source_path})
            failed += 1
        update_job(job_id, completed=completed, failed=failed, message=f"{completed} complete, {failed} failed")
    update_job(job_id, status="complete" if failed == 0 else "complete_with_errors", completed=completed, failed=failed, message=f"Finished: {completed} complete, {failed} failed")
    log_event("warning" if failed else "info","import","job_finished",f"Job #{job_id}: {completed} complete, {failed} failed",{"job_id":job_id,"completed":completed,"failed":failed})
    try:
        await send_notification(
            f"ArrNexus import job #{job_id}",
            f"{completed} completed, {failed} failed.",
            "warning" if failed else "info",
            "import_job",
        )
    except Exception:
        pass


def _launch(coro):
    task = asyncio.create_task(coro)
    RUNNING_TASKS.add(task)
    task.add_done_callback(RUNNING_TASKS.discard)
    return task


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
        if is_within_logical(p, source_root()):
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
async def single_import(request: Request, source_path: str = Form(...), destination_key: str = Form("auto")):
    require_auth(request)
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
async def logs_page(request: Request, level: str = "all", source: str = "all", q: str = ""):
    require_auth(request)
    rows=list_logs(level,source,q,500)
    return templates.TemplateResponse("logs.html",{"request":request,"rows":rows,"level":level,"source":source,"q":q,"sources":log_sources()})


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


@app.get("/problems", response_class=HTMLResponse)
async def problems_page(request: Request):
    require_auth(request)
    problems=[]; service_rows=[]
    ns=namespace_status()
    if not ns.get("ok"):
        problems.append({"severity":"critical","kind":"Namespace","title":"DUMB namespace unavailable","detail":ns.get("error") or "Mount namespace could not be resolved","href":"/maintenance"})
    # Live service checks are deliberately isolated so one dead service does not break the page.
    for name,client in (("Radarr",RadarrClient()),("Sonarr",SonarrClient()),("Lidarr",LidarrClient()),("Prowlarr",ProwlarrClient())):
        try:
            st=await client.status(); service_rows.append({"name":name,"ok":True,"detail":st.get("version") or "Connected"})
        except Exception as exc:
            service_rows.append({"name":name,"ok":False,"detail":str(exc)})
            problems.append({"severity":"error","kind":"Connection","title":f"{name} unavailable","detail":str(exc),"href":"/arrs"})
    try:
        broken=scan_broken_symlinks(250)
    except Exception as exc:
        broken=[]; problems.append({"severity":"warning","kind":"Maintenance","title":"Broken-link scan failed","detail":str(exc),"href":"/maintenance"})
    if broken:
        problems.append({"severity":"error","kind":"Library","title":f"{len(broken)} broken symlink(s)","detail":"Open Maintenance to inspect and repair links.","href":"/maintenance"})
    failed_jobs=[]
    for j in recent_jobs(50):
        if int(j["failed"] or 0)>0 or j["status"] in {"failed","error"}:
            failed_jobs.append(dict(j))
    if failed_jobs:
        problems.append({"severity":"error","kind":"Import","title":f"{len(failed_jobs)} recent import job(s) with failures","detail":"Open Import Jobs for per-item reasons.","href":"/jobs"})
    error_logs=list_logs("error","all","",30)
    score=100
    score-=min(35,sum(15 for x in service_rows if not x["ok"]))
    score-=min(25,len(broken)*2)
    score-=min(25,sum(int(x.get("failed") or 0) for x in failed_jobs)*3)
    if not ns.get("ok"): score-=30
    score=max(0,score)
    return templates.TemplateResponse("problems.html",{"request":request,"problems":problems,"services":service_rows,"broken":broken,"failed_jobs":failed_jobs,"error_logs":error_logs,"health_score":score,"namespace":ns})


@app.get("/maintenance", response_class=HTMLResponse)
async def maintenance_page(request: Request):
    require_auth(request)
    error = None
    try:
        broken = scan_broken_symlinks(500)
        items = scan_source()
        links = build_source_link_index()
        imports = latest_import_by_source()
        orphans = [x for x in items if x.path not in links and not ((imports.get(x.path) or {}).get("status") in {"complete", "linked"} and not (imports.get(x.path) or {}).get("undone"))]
    except Exception as exc:
        broken, orphans, error = [], [], str(exc)
        log_event("warning","maintenance","scan_unavailable",error)
    return templates.TemplateResponse("maintenance.html", {"request": request, "broken": broken, "orphans": orphans, "error": error})


@app.post("/maintenance/repair")
async def repair_link(request: Request, path: str = Form(...)):
    require_auth(request)
    ok, msg = repair_broken_symlink(path)
    add_activity("repair" if ok else "repair_error", Path(path).name, msg, path)
    return RedirectResponse("/maintenance", status_code=303)


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
    rows = []
    for inst in instances:
        try:
            client = client_for_instance(inst)
            status = await client.status()
            roots = await client.roots()
            tags = await client.tags()
            rows.append({"kind": "arr", "instance": inst, "ok": True, "status": status, "roots": roots, "tags": tags, "url": inst.url, "has_key": bool(inst.api_key)})
        except Exception as exc:
            rows.append({"kind": "arr", "instance": inst, "ok": False, "error": str(exc), "roots": [], "tags": [], "url": inst.url, "has_key": bool(inst.api_key)})
    # Prowlarr is not a DUMB Arr process, but belongs on the same connection page.
    pc = get_connection("prowlarr")
    try:
        ps = await ProwlarrClient().status()
        rows.append({"kind":"prowlarr","service":"prowlarr","instance_name":"main","ok":True,"status":ps,"roots":[],"tags":[],"url":pc.url,"has_key":bool(pc.api_key)})
    except Exception as exc:
        rows.append({"kind":"prowlarr","service":"prowlarr","instance_name":"main","ok":False,"error":str(exc),"roots":[],"tags":[],"url":pc.url,"has_key":bool(pc.api_key)})
    jc = get_connection("jellyfin")
    try:
        js=await jellyfin_status(); rows.append({"kind":"jellyfin","service":"jellyfin","instance_name":"main","ok":True,"status":{"version":js.get("Version") or js.get("version")},"roots":[],"tags":[],"url":jc.url,"has_key":bool(jc.api_key)})
    except Exception as exc:
        rows.append({"kind":"jellyfin","service":"jellyfin","instance_name":"main","ok":False,"status":{},"roots":[],"tags":[],"url":jc.url,"has_key":bool(jc.api_key),"error":str(exc)})
    sc = get_connection("seerr")
    try:
        ss = await SeerrClient().status()
        rows.append({"kind":"seerr","service":"seerr","instance_name":"main","ok":True,"status":ss,"roots":[],"tags":[],"url":sc.url,"has_key":bool(sc.api_key)})
    except Exception as exc:
        rows.append({"kind":"seerr","service":"seerr","instance_name":"main","ok":False,"status":{},"roots":[],"tags":[],"url":sc.url,"has_key":bool(sc.api_key),"error":str(exc)})
    return templates.TemplateResponse("arrs.html", {"request": request, "rows": rows, "notice": notice})


@app.post("/settings/connection")
async def save_connection_route(request: Request, service: str = Form(...), instance: str = Form("main"), url: str = Form(...), api_key: str = Form("")):
    require_admin(request)
    service = service.lower().strip(); instance = instance.strip() or "main"
    if service not in {"radarr","sonarr","lidarr","prowlarr","jellyfin","seerr"}:
        raise HTTPException(400, "Unsupported service")
    save_connection(service, url, api_key, instance)
    # Main DUMB instances are named nzbdav; keep the generic connection in sync
    # so Dashboard/Discover clients use the same credentials.
    if instance == "nzbdav" and service in {"radarr","sonarr","lidarr"}:
        save_connection(service, url, api_key, "main")
    add_activity("settings", service.title(), f"Updated {instance} connection")
    return RedirectResponse(f"/arrs?notice={quote(f'{service.title()} / {instance} saved')}", status_code=303)


THEMES = [
    ("nexus","ArrNexus"),("radarr","Radarr Gold"),("sonarr","Sonarr Blue"),("lidarr","Lidarr Green"),
    ("prowlarr","Prowlarr Purple"),("jellyfin","Jellyfin Violet"),("spotify","Music Green"),
    ("oled","OLED Black"),("nord","Nord"),("dracula","Dracula"),("light","Clean Light"),("cyber","Cyber")
]


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, notice: str = "", error: str = ""):
    require_auth(request)
    uid=int(request.session.get("user_id") or 0)
    user=get_user(uid) if uid else None
    if not user:
        request.session.clear()
        return RedirectResponse("/setup" if user_count()==0 else "/login?notice="+quote("Please sign in again to open your profile"),status_code=303)
    return templates.TemplateResponse("profile.html", {"request":request,"user":user,"themes":THEMES,"notice":notice,"error":error})


@app.post("/profile")
async def profile_save(request: Request, username: str = Form(...), email: str = Form(""), display_name: str = Form(""), theme: str = Form("nexus"), dashboard_layout: str = Form("default"), password: str = Form("")):
    require_auth(request)
    uid = int(request.session.get("user_id") or 0)
    if theme not in {x[0] for x in THEMES}: theme = "nexus"
    try:
        update_user(uid, username, email, display_name or username, theme, dashboard_layout, password)
        request.session["theme"] = theme
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
        "app_title":setting_get("app.title","ArrNexus"),"users":list_users(),"mounts":list_mounts(False),
        "dumb_root":dumb_root(),
        "soundcloud_configured":bool(setting_get("music.soundcloud.client_id") and setting_get("music.soundcloud.client_secret")),
        "jamendo_client_id":setting_get("music.jamendo.client_id",""),
        "lastfm_configured":bool(setting_get("music.lastfm.api_key")),
        "spotify_configured":bool(setting_get("music.spotify.client_id") and setting_get("music.spotify.client_secret")),
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
        "update_repo": setting_get("update.repo",""), "version": APP_VERSION, "catalog_plugins": load_catalog_plugins(),
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


@app.post("/settings/music-providers")
async def settings_music_providers(request: Request, soundcloud_client_id: str = Form(''), soundcloud_client_secret: str = Form(''), jamendo_client_id: str = Form(''), lastfm_api_key: str = Form(''), spotify_client_id: str = Form(''), spotify_client_secret: str = Form('')):
    require_admin(request)
    if soundcloud_client_id.strip(): setting_set('music.soundcloud.client_id',soundcloud_client_id.strip(),True)
    if soundcloud_client_secret.strip() and soundcloud_client_secret != '********': setting_set('music.soundcloud.client_secret',soundcloud_client_secret.strip(),True)
    setting_set('music.jamendo.client_id',jamendo_client_id.strip(),True)
    if lastfm_api_key.strip() and lastfm_api_key != '********': setting_set('music.lastfm.api_key',lastfm_api_key.strip(),True)
    if spotify_client_id.strip(): setting_set('music.spotify.client_id',spotify_client_id.strip(),True)
    if spotify_client_secret.strip() and spotify_client_secret != '********': setting_set('music.spotify.client_secret',spotify_client_secret.strip(),True)
    log_event('info','settings','music_provider_settings','Music provider application credentials updated')
    return RedirectResponse('/settings?notice='+quote('Music provider settings saved'),status_code=303)


@app.post("/settings/general")
async def settings_general(request: Request, app_title: str = Form("ArrNexus"), smtp_host: str = Form(""), smtp_port: str = Form("587"), smtp_username: str = Form(""), smtp_password: str = Form(""), smtp_from: str = Form(""), smtp_starttls: str = Form("false")):
    require_admin(request)
    setting_set("app.title", app_title.strip() or "ArrNexus")
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
    extra = {"version": APP_VERSION, "namespace": ns, "connections": {s: {"url": get_connection(s).url, "api_key_configured": bool(get_connection(s).api_key)} for s in ("radarr","sonarr","lidarr","prowlarr","jellyfin","seerr")}}
    data = diagnostics_zip(extra)
    log_event('info','diagnostics','bundle_created','Sanitized diagnostics bundle generated')
    return Response(data, media_type='application/zip', headers={'Content-Disposition':'attachment; filename="arrnexus-diagnostics.zip"'})


async def _check_update() -> dict:
    repo = setting_get('update.repo','').strip()
    if not repo:
        return {"configured":False,"current":APP_VERSION}
    match = re.search(r'(?:github\.com/)?([^/]+)/([^/]+?)(?:\.git)?$', repo.rstrip('/'))
    if not match:
        return {"configured":True,"current":APP_VERSION,"error":"Use owner/repo or a GitHub repository URL"}
    owner, name = match.group(1), match.group(2)
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers={'Accept':'application/vnd.github+json','User-Agent':'ArrNexus-update-checker'}) as client:
            r = await client.get(f'https://api.github.com/repos/{owner}/{name}/releases/latest')
        r.raise_for_status(); data=r.json(); latest=str(data.get('tag_name') or data.get('name') or '')
        current=APP_VERSION.split('-')[0].lstrip('v'); latest_norm=latest.lstrip('v')
        return {"configured":True,"current":APP_VERSION,"latest":latest,"update_available": bool(latest and latest_norm != current),"url":data.get('html_url') or ''}
    except Exception as exc:
        return {"configured":True,"current":APP_VERSION,"error":str(exc)}


@app.post("/settings/update-repo")
async def settings_update_repo(request: Request, update_repo: str = Form("")):
    require_admin(request)
    setting_set('update.repo',update_repo.strip())
    return RedirectResponse('/settings?notice='+quote('Update source saved'),status_code=303)


@app.get("/api/update-check")
async def api_update_check(request: Request):
    require_admin(request)
    return await _check_update()


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
        if not str(data.get('search_url') or '').startswith(('https://','http://')) or '{query}' not in str(data.get('search_url') or ''):
            raise ValueError('Provider search_url must be http(s) and contain {query}')
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
async def ecosystem_save(request: Request, key: str = Form(...), url: str = Form(""), api_key: str = Form(""), enabled: str = Form("false")):
    require_admin(request)
    try:
        save_ecosystem_connector(key.strip(), url.strip(), api_key.strip(), enabled.lower() in {"1","true","yes","on"})
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


@app.get("/infinidysk", response_class=HTMLResponse)
async def infinidysk_page(request: Request, notice: str = ""):
    require_auth(request)
    cfg = connector_config("infinidysk")
    health = {}; queue = {}; history = {}; metrics = []; error = ""
    if cfg.get("enabled") and cfg.get("url"):
        client = InfiniDyskClient()
        try:
            health = await client.health()
        except Exception as exc:
            error = str(exc)
        try:
            queue = await client.queue()
        except Exception as exc:
            if not error: error = str(exc)
        try:
            history = await client.history()
        except Exception:
            history = {}
        try:
            metrics = await client.metrics()
        except Exception:
            metrics = []
    q = queue.get("queue", {}) if isinstance(queue, dict) else {}
    h = history.get("history", {}) if isinstance(history, dict) else {}
    return templates.TemplateResponse("infinidysk.html", {
        "request": request, "notice": notice, "config": cfg, "health": health, "queue": q,
        "queue_slots": q.get("slots", []) if isinstance(q, dict) else [],
        "history_slots": h.get("slots", []) if isinstance(h, dict) else [],
        "metrics": metrics, "error": error,
    })


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
    rows = []
    sources = []
    for inst in discover_instances():
        try:
            client = client_for_instance(inst)
            data = await client.queue(100)
            records = data.get("records", []) if isinstance(data, dict) else (data or [])
            sources.append({"instance": inst, "ok": True, "count": len(records)})
            for x in records:
                rows.append({"instance": inst, "item": x})
        except Exception as exc:
            sources.append({"instance": inst, "ok": False, "count": 0, "error": str(exc)})
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
        instances=discover_instances()
    except Exception as exc:
        log_event("error","discover","instance_discovery_failed",str(exc))
        return [], [f"DUMB Arr discovery failed: {exc}"]
    for inst in instances:
        if inst.service not in {"radarr","sonarr"} or not inst.api_key:
            continue
        try:
            c=client_for_instance(inst)
            rows=await (c.movies() if inst.service=="radarr" else c.series())
            if not isinstance(rows,list):
                rows=[]
        except Exception as exc:
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
            except Exception as exc:
                log_event("warning","discover","library_card_failed",str(exc),{"title":str(row.get("title") or "")})
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
    # Discover must never white-screen because one optional data source fails.
    try:
        seerr_shelves,seerr_notice=await _seerr_shelves()
    except Exception as exc:
        seerr_shelves=[]; seerr_notice=f"Seerr discovery unavailable: {exc}"; warnings.append(seerr_notice)
    try:
        library_shelves,library_warnings=await _library_shelves(); warnings.extend(library_warnings)
    except Exception as exc:
        library_shelves=[]; warnings.append(f"Library shelves unavailable: {exc}")
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
        })
    except Exception as exc:
        # Last-resort diagnostic instead of an opaque Internal Server Error.
        log_event("error","discover","template_failed",str(exc))
        return HTMLResponse(f"<h1>ArrNexus Discover error</h1><p>{__import__('html').escape(str(exc))}</p><p><a href='/logs?source=discover&level=error'>Open Discover logs</a></p>",status_code=500)


@app.post("/discover/add")
async def discover_add_route(request: Request, media_type: str = Form(...), candidate_json: str = Form(...), destination_key: str = Form("auto"), query: str = Form("")):
    user=require_request_access(request, count_against_limit=True)
    try:
        candidate=json.loads(candidate_json)
        result=await discover_add(candidate,media_type,destination_key,search=True,user_id=int(user["id"]))
        item=result["item"]; ext=item.get("tmdbId") if media_type=="movie" else item.get("tvdbId")
        add_scrape(media_type,item.get("title") or candidate.get("title") or "Untitled",str(ext or ""),"radarr" if media_type=="movie" else "sonarr",result["instance"],item.get("id"),result["destination"],"searching","Arr search command dispatched; waiting for indexer/download activity")
        log_event("info","discover","search_dispatched",item.get("title") or "Untitled",{"media_type":media_type,"instance":result["instance"],"destination":result["destination"]})
        msg=quote(f"Requested {item.get('title')} via {result['instance']}; search is now visible in Scraping")
        return RedirectResponse(f"/discover?media_type={media_type}&q={quote(query)}&notice={msg}",status_code=303)
    except Exception as exc:
        log_event("error","discover","request_failed",str(exc),{"query":query,"media_type":media_type})
        return RedirectResponse(f"/discover?media_type={media_type}&q={quote(query)}&error={quote(str(exc))}",status_code=303)


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


@app.get("/music", response_class=HTMLResponse)
async def music_page(request: Request, q: str = "", kind: str = "artist", source: str = "unified", genre: str = ""):
    require_auth(request)
    providers=provider_catalog(); results=[]; source_featured=[]; error=None; external_url=""; source_note=""; lidarr_error=None
    available_keys={p.get("key") for p in providers}
    if source not in available_keys:
        source="unified"
    term=q.strip() or genre.strip()

    # Provider discovery and Lidarr health are deliberately isolated. A broken
    # Lidarr connection must not make Apple/Audius/MusicBrainz/etc disappear.
    try:
        source_featured,source_note=await provider_featured(source,genre,24)
        if term:
            results, external_url = await provider_search(source,term,kind,30)
        elif source in {"amazon","beatport","bandcamp","discogs"}:
            external_url=external_music_links(genre or "").get(source,"") if genre else ""
    except Exception as exc:
        error=str(exc)
        log_event("warning","music","provider_failed",str(exc),{"source":source,"query":q,"genre":genre})

    if source=="unified":
        try:
            trends,releases,audius=await asyncio.gather(trending_artists(18,"this_week"),trending_releases(18,"this_week"),audius_trending(18,genre))
            trends,releases=await asyncio.gather(enrich_artist_art(trends,10),enrich_release_art(releases,10))
        except Exception as exc:
            trends=[]; releases=[]; audius=[]
            log_event("warning","music","unified_trends_failed",str(exc))
    else:
        trends=releases=audius=[]

    try:
        lidarr_artists=await LidarrClient().artists()
    except Exception as exc:
        lidarr_artists=[]; lidarr_error=str(exc)
        log_event("warning","music","lidarr_library_failed",str(exc))

    selected_provider=next((p for p in providers if p.get("key")==source),{"name":"For You","description":""})
    return templates.TemplateResponse("music.html",{
        "request":request,"q":q,"kind":kind,"source":source,"genre":genre,"results":results,"source_featured":source_featured,
        "source_note":source_note,"selected_provider":selected_provider,"trends":trends,"releases":releases,"audius":audius,
        "lidarr_artists":lidarr_artists,"lidarr_error":lidarr_error,"genres":GENRES,"error":error,"providers":providers,"external_url":external_url,
        "soundcloud_configured":bool(setting_get("music.soundcloud.client_id") and setting_get("music.soundcloud.client_secret")),
        "jamendo_configured":bool(setting_get("music.jamendo.client_id")),"lastfm_configured":bool(setting_get("music.lastfm.api_key")),
        "spotify_configured":bool(setting_get("music.spotify.client_id") and setting_get("music.spotify.client_secret")),
    })


@app.get("/music/artist", response_class=HTMLResponse)
async def music_artist_page(request: Request, name: str):
    require_auth(request)
    lc = LidarrClient()
    lookup = await lc.artist_lookup(name)
    exact = lookup[0] if lookup else None
    mb = await search_musicbrainz(name, "artist", 6)
    links = external_music_links(name)
    existing = None
    for a in await lc.artists():
        if normalize_title(a.get("artistName") or a.get("name") or "") == normalize_title(name):
            existing = a
            break
    albums = await lc.albums(int(existing["id"])) if existing else []
    artwork = await representative_artwork(name)
    return templates.TemplateResponse("music_artist.html", {"request": request, "name": name, "lookup": exact, "mb": mb, "existing": existing, "albums": albums, "links": links, "artwork": artwork})


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
