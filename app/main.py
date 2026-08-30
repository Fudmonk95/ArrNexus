from __future__ import annotations
import asyncio
import json
import secrets
import smtplib
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
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
)
from .scanner import scan_source, inspect_item, normalize_title, human_size
from .routing import decide_movie, decide_tv
from .arr import RadarrClient, SonarrClient, LidarrClient, ProwlarrClient, ArrError, poster_url
from .router_service import import_one, route_item, discover_lookup, discover_add, client_for_instance
from .importer import ImportErrorSafe, unlink_created, scan_broken_symlinks, repair_broken_symlink
from .namespace import view_path, is_within_logical, namespace_status, NamespaceError
from .instances import discover_instances
from .library import inventory_roots, build_source_link_index
from .jellyfin import search_jellyfin
from .music import search_musicbrainz, trending_artists, trending_releases, itunes_search, external_music_links, GENRES, audius_trending, audius_search, provider_catalog, enrich_artist_art, enrich_release_art, representative_artwork
from .connections import get_connection, save_connection
from . import realdebrid as rd

BASE = Path(__file__).resolve().parent
app = FastAPI(title="ArrNexus")
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.filters["human_size"] = human_size
templates.env.globals["app_setting"] = setting_get

RUNNING_TASKS: set[asyncio.Task] = set()


@app.on_event("startup")
async def startup():
    init_db()


def logged_in(request: Request):
    return bool(request.session.get("user_id") or request.session.get("auth"))


def require_auth(request: Request):
    if not logged_in(request):
        raise HTTPException(401)


@app.exception_handler(401)
async def auth_error(request: Request, exc):
    return RedirectResponse("/login", status_code=303)


@app.get("/api/health")
async def health():
    ns = namespace_status()
    return {"ok": True, "app": "ArrNexus", "namespace": bool(ns.get("ok"))}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, notice: str = ""):
    return templates.TemplateResponse("login.html", {"request": request, "error": None, "notice": notice})


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
            roots = settings.movie_roots if item.media_type == "movie" else settings.tv_roots
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
        canonical_key = f"{item.media_type}:{external_id}" if external_id else f"{item.media_type}:{normalize_title(display_title)}:{display_year or 0}"
        return {
            "item": item,
            "metadata": metadata or {},
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
    items = scan_source()
    imports = successful_imports_by_source()
    states = item_states()
    links = build_source_link_index()
    imported = sum(1 for x in items if x.path in imports or x.path in links)
    ignored = sum(1 for x in items if (states.get(x.path) or {}).get("state") == "ignored")
    waiting = max(0, len(items) - imported - ignored)
    statuses = {
        "radarr": await arr_status(RadarrClient()),
        "sonarr": await arr_status(SonarrClient()),
        "lidarr": await arr_status(LidarrClient()),
        "prowlarr": await arr_status(ProwlarrClient()),
    }
    queue_counts = {"radarr": 0, "sonarr": 0, "lidarr": 0}
    for name, client in (("radarr", RadarrClient()), ("sonarr", SonarrClient()), ("lidarr", LidarrClient())):
        try:
            q = await client.queue(200)
            rec = q.get("records", []) if isinstance(q, dict) else (q or [])
            queue_counts[name] = len(rec)
        except Exception:
            pass
    dest_counts = {}
    for row in recent_imports(5000):
        if row["status"] in {"complete", "linked"} and not row["undone"]:
            key = f"{row['arr_name'] or row['media_type']}:{row['destination_key']}"
            dest_counts[key] = dest_counts.get(key, 0) + 1
    rd_user = None
    if rd.connected():
        try:
            rd_user = await rd.user()
        except Exception:
            rd_user = {"error": "Connected, but account status could not be loaded"}
    activity_days = activity_by_day(7)
    dest_top = sorted(dest_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "items": items[:10],
        "source_count": len(items),
        "movie_count": sum(1 for i in items if i.media_type == "movie"),
        "tv_count": sum(1 for i in items if i.media_type == "tv"),
        "imported_count": imported,
        "waiting_count": waiting,
        "ignored_count": ignored,
        "statuses": statuses,
        "queue_counts": queue_counts,
        "dest_counts": dest_top,
        "dest_max": max([x[1] for x in dest_top], default=1),
        "activity_days": activity_days,
        "activity_max": max([int(x.get("count") or 0) for x in activity_days], default=1),
        "recent": recent_imports(8),
        "activity": recent_activity(12),
        "jobs": recent_jobs(6),
        "source_root": settings.source_root,
        "namespace": namespace_status(),
        "rd_connected": rd.connected(),
        "rd_user": rd_user,
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
        "movie_roots": settings.movie_roots,
        "tv_roots": settings.tv_roots,
        "q": q, "status": status, "media_type": media_type, "view": view,
    })


@app.get("/item", response_class=HTMLResponse)
async def item_detail(request: Request, path: str):
    require_auth(request)
    src = Path(path)
    if not is_within_logical(src, settings.source_root):
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
        "roots": settings.movie_roots if item.media_type == "movie" else settings.tv_roots,
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
            completed += 1
        except Exception as exc:
            update_job_item(iid, status="error", stage="error", message=str(exc))
            failed += 1
        update_job(job_id, completed=completed, failed=failed, message=f"{completed} complete, {failed} failed")
    update_job(job_id, status="complete" if failed == 0 else "complete_with_errors", completed=completed, failed=failed, message=f"Finished: {completed} complete, {failed} failed")


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
        if is_within_logical(p, settings.source_root):
            valid.append({"source_path": p, "display_name": Path(p).name, "destination_key": destination})
    if not valid:
        raise HTTPException(400, "No valid source paths")
    jid = create_job("bulk_import", valid)
    _launch(run_import_job(jid))
    return RedirectResponse(f"/jobs/{jid}", status_code=303)


@app.post("/import")
async def single_import(request: Request, source_path: str = Form(...), destination_key: str = Form("auto")):
    require_auth(request)
    jid = create_job("import", [{"source_path": source_path, "display_name": Path(source_path).name, "destination_key": destination_key}])
    _launch(run_import_job(jid))
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


@app.get("/maintenance", response_class=HTMLResponse)
async def maintenance_page(request: Request):
    require_auth(request)
    broken = scan_broken_symlinks(500)
    items = scan_source()
    links = build_source_link_index()
    imports = latest_import_by_source()
    orphans = [x for x in items if x.path not in links and not ((imports.get(x.path) or {}).get("status") in {"complete", "linked"} and not (imports.get(x.path) or {}).get("undone"))]
    return templates.TemplateResponse("maintenance.html", {"request": request, "broken": broken, "orphans": orphans})


@app.post("/maintenance/repair")
async def repair_link(request: Request, path: str = Form(...)):
    require_auth(request)
    ok, msg = repair_broken_symlink(path)
    add_activity("repair" if ok else "repair_error", Path(path).name, msg, path)
    return RedirectResponse("/maintenance", status_code=303)


@app.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request):
    require_auth(request)
    return templates.TemplateResponse("rules.html", {"request": request, "rules": list_rules(), "movie_roots": settings.movie_roots, "tv_roots": settings.tv_roots})


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
    rows.append({"kind":"jellyfin","service":"jellyfin","instance_name":"main","ok":bool(jc.api_key),"status":{},"roots":[],"tags":[],"url":jc.url,"has_key":bool(jc.api_key),"error":"API key not configured" if not jc.api_key else ""})
    return templates.TemplateResponse("arrs.html", {"request": request, "rows": rows, "notice": notice})


@app.post("/settings/connection")
async def save_connection_route(request: Request, service: str = Form(...), instance: str = Form("main"), url: str = Form(...), api_key: str = Form("")):
    require_auth(request)
    service = service.lower().strip(); instance = instance.strip() or "main"
    if service not in {"radarr","sonarr","lidarr","prowlarr","jellyfin"}:
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
    user = get_user(int(request.session.get("user_id") or 0))
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
    require_auth(request)
    return templates.TemplateResponse("settings.html", {
        "request":request, "notice":notice, "rd_connected":rd.connected(),
        "smtp": {k: setting_get(f"smtp.{k}") for k in ("host","port","username","from_address","starttls")},
        "app_title": setting_get("app.title", "ArrNexus"),
        "users": list_users(),
    })


@app.post("/settings/general")
async def settings_general(request: Request, app_title: str = Form("ArrNexus"), smtp_host: str = Form(""), smtp_port: str = Form("587"), smtp_username: str = Form(""), smtp_password: str = Form(""), smtp_from: str = Form(""), smtp_starttls: str = Form("false")):
    require_auth(request)
    setting_set("app.title", app_title.strip() or "ArrNexus")
    setting_set("smtp.host", smtp_host.strip()); setting_set("smtp.port", smtp_port.strip())
    setting_set("smtp.username", smtp_username.strip())
    if smtp_password and smtp_password != "********": setting_set("smtp.password", smtp_password, True)
    setting_set("smtp.from_address", smtp_from.strip())
    setting_set("smtp.starttls", "true" if smtp_starttls.lower() in {"1","true","yes","on"} else "false")
    return RedirectResponse(f"/settings?notice={quote('Settings saved')}", status_code=303)


@app.post("/settings/users/add")
async def settings_user_add(request: Request, username: str = Form(...), email: str = Form(""), display_name: str = Form(""), password: str = Form(...), role: str = Form("user")):
    require_auth(request)
    try:
        create_user(username, email, display_name, password, role)
        return RedirectResponse(f"/settings?notice={quote('User account created')}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/settings?notice={quote('Could not create user: ' + str(exc))}", status_code=303)


@app.post("/settings/users/delete/{user_id}")
async def settings_user_delete(request: Request, user_id: int):
    require_auth(request)
    try:
        delete_user(user_id, int(request.session.get("user_id") or 0))
        return RedirectResponse(f"/settings?notice={quote('User removed')}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/settings?notice={quote(str(exc))}", status_code=303)


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


@app.get("/discover", response_class=HTMLResponse)
async def discover_page(request: Request, q: str = "", media_type: str = "movie", notice: str = "", error: str = ""):
    require_auth(request)
    results = []
    page_error = error or None
    rd_names = []
    if q.strip():
        try:
            results = await discover_lookup(q.strip(), media_type)
            if rd.connected():
                try:
                    torrents = await rd.torrents(250)
                    rd_names = [normalize_title(x.get("filename") or x.get("original_filename") or "") for x in torrents]
                except Exception:
                    rd_names = []
            for c in results:
                nt = normalize_title(c.get("title") or "")
                c["arrnexus_in_rd"] = bool(nt and any(nt in rn or rn in nt for rn in rd_names if rn))
        except Exception as exc:
            page_error = str(exc)
    return templates.TemplateResponse("discover.html", {
        "request": request, "q": q, "media_type": media_type, "results": results,
        "error": page_error, "notice": notice, "movie_roots": settings.movie_roots,
        "tv_roots": settings.tv_roots, "rd_connected": rd.connected(),
    })


@app.post("/discover/add")
async def discover_add_route(request: Request, media_type: str = Form(...), candidate_json: str = Form(...), destination_key: str = Form("auto"), query: str = Form("")):
    require_auth(request)
    try:
        candidate = json.loads(candidate_json)
        result = await discover_add(candidate, media_type, destination_key, search=True)
        msg = quote(f"Requested {result['item'].get('title')} via {result['instance']}; search queued")
        return RedirectResponse(f"/discover?media_type={media_type}&q={quote(query)}&notice={msg}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/discover?media_type={media_type}&q={quote(query)}&error={quote(str(exc))}", status_code=303)


@app.get("/debrid", response_class=HTMLResponse)
async def debrid_page(request: Request, q: str = "", release_q: str = "", protocol: str = "torrent", error: str = "", notice: str = ""):
    require_auth(request)
    user = None
    torrents = []
    releases = []
    page_error = error or None
    if rd.connected():
        try:
            user, torrents = await asyncio.gather(rd.user(), rd.torrents(500))
        except Exception as exc:
            page_error = str(exc)
    if q.strip():
        nq = q.lower()
        torrents = [x for x in torrents if nq in (x.get("filename") or "").lower()]
    if release_q.strip():
        try:
            releases = await ProwlarrClient().search(release_q.strip(), limit=100)
            if protocol in {"torrent", "usenet"}:
                releases = [x for x in (releases or []) if str(x.get("protocol") or "").lower() == protocol]
            releases = (releases or [])[:100]
        except Exception as exc:
            page_error = str(exc)
    return templates.TemplateResponse("debrid.html", {
        "request": request, "connected": rd.connected(), "user": user, "torrents": torrents,
        "error": page_error, "notice": notice, "q": q, "release_q": release_q, "protocol": protocol, "releases": releases,
    })


@app.post("/debrid/add-release")
async def debrid_add_release(request: Request, release_json: str = Form(...), return_query: str = Form("")):
    require_auth(request)
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
    artist = await lc.add_artist(candidate, settings.lidarr_root, search=False)
    return artist, True


@app.get("/music", response_class=HTMLResponse)
async def music_page(request: Request, q: str = "", kind: str = "artist", source: str = "unified", genre: str = ""):
    require_auth(request)
    providers = provider_catalog()
    results = []
    trends = []
    releases = []
    audius = []
    error = None
    external_url = ""
    links = external_music_links(q or genre or "")
    try:
        if q.strip():
            if source == "apple":
                results = await itunes_search(q, "album" if kind == "album" else "musicArtist", 30)
            elif source == "audius":
                results = await audius_search(q, 30)
            elif source == "musicbrainz":
                results = await search_musicbrainz(q, kind, 30)
            elif source == "listenbrainz":
                # ListenBrainz excels at public trends, while MusicBrainz resolves searched entities.
                results = await search_musicbrainz(q, kind, 30)
            elif source in {"spotify", "amazon", "beatport", "bandcamp", "lastfm", "discogs"}:
                external_url = links.get(source, "")
            else:
                mb, apple, au = await asyncio.gather(
                    search_musicbrainz(q, kind, 18),
                    itunes_search(q, "album" if kind == "album" else "musicArtist", 18),
                    audius_search(q, 18),
                )
                seen = set()
                for row in mb + apple + au:
                    k = (normalize_title(row.get("artist") or ""), normalize_title(row.get("title") or ""))
                    if k not in seen:
                        seen.add(k); results.append(row)
        else:
            trends, releases, audius = await asyncio.gather(
                trending_artists(20, "this_week"),
                trending_releases(20, "this_week"),
                audius_trending(20, genre),
            )
            trends, releases = await asyncio.gather(enrich_artist_art(trends, 8), enrich_release_art(releases, 8))
        lidarr_artists = await LidarrClient().artists()
    except Exception as exc:
        lidarr_artists = []
        error = str(exc)
    return templates.TemplateResponse("music.html", {
        "request": request, "q": q, "kind": kind, "source": source, "genre": genre,
        "results": results, "trends": trends, "releases": releases, "audius": audius,
        "lidarr_artists": lidarr_artists, "genres": GENRES, "error": error,
        "providers": providers, "external_url": external_url,
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
