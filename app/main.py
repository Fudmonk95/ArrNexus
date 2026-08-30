from __future__ import annotations
import asyncio
import json
import secrets
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
from .music import search_musicbrainz, trending_artists, trending_releases, itunes_search, external_music_links, GENRES

BASE = Path(__file__).resolve().parent
app = FastAPI(title="DMM Arr Router")
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.filters["human_size"] = human_size

radarr = RadarrClient()
sonarr = SonarrClient()
lidarr = LidarrClient()
prowlarr = ProwlarrClient()
RUNNING_TASKS: set[asyncio.Task] = set()


@app.on_event("startup")
async def startup():
    init_db()


def logged_in(request: Request):
    return bool(request.session.get("auth"))


def require_auth(request: Request):
    if not logged_in(request):
        raise HTTPException(401)


@app.exception_handler(401)
async def auth_error(request: Request, exc):
    return RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    good = secrets.compare_digest(username, settings.username) and secrets.compare_digest(password, settings.password)
    if not good:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Incorrect username or password"}, status_code=401)
    request.session["auth"] = True
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
                        lookup = await (radarr.lookup(f"{item.title_guess} {item.year_guess or ''}".strip()) if item.media_type == "movie" else sonarr.lookup(item.title_guess))
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

        jf = {"configured": bool(settings.jellyfin_api_key), "found": False}
        if settings.jellyfin_api_key and state in {"imported", "linked"}:
            async with sem:
                jf = await search_jellyfin(item.title_guess, 3)

        return {
            "item": item,
            "metadata": metadata or {},
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


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    require_auth(request)
    items = scan_source()
    imports = successful_imports_by_source()
    imported = sum(1 for x in items if x.path in imports)
    statuses = {
        "radarr": await arr_status(radarr),
        "sonarr": await arr_status(sonarr),
        "lidarr": await arr_status(lidarr),
        "prowlarr": await arr_status(prowlarr),
    }
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "items": items[:12],
        "source_count": len(items),
        "movie_count": sum(1 for i in items if i.media_type == "movie"),
        "tv_count": sum(1 for i in items if i.media_type == "tv"),
        "imported_count": imported,
        "statuses": statuses,
        "recent": recent_imports(8),
        "activity": recent_activity(12),
        "jobs": recent_jobs(6),
        "source_root": settings.source_root,
        "namespace": namespace_status(),
    })


@app.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request, q: str = "", status: str = "all", media_type: str = "all", view: str = "grid"):
    require_auth(request)
    items = scan_source()
    if q:
        nq = q.lower()
        items = [x for x in items if nq in x.name.lower() or nq in x.title_guess.lower()]
    if media_type in {"movie", "tv"}:
        items = [x for x in items if x.media_type == media_type]
    enriched = await enrich_items(items)
    if status != "all":
        if status == "upgrade":
            enriched = [x for x in enriched if x["upgrade"]]
        elif status == "duplicate":
            enriched = [x for x in enriched if x["duplicate"]]
        else:
            enriched = [x for x in enriched if x["state"] == status]
    return templates.TemplateResponse("inbox.html", {
        "request": request,
        "rows": enriched,
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
    jf = await search_jellyfin(item.title_guess, 10) if settings.jellyfin_api_key else {"configured": False, "found": False, "items": []}
    return templates.TemplateResponse("item.html", {
        "request": request,
        "item": item,
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
async def arrs_page(request: Request):
    require_auth(request)
    instances = discover_instances()
    rows = []
    for inst in instances:
        try:
            client = client_for_instance(inst)
            status = await client.status()
            roots = await client.roots()
            tags = await client.tags()
            rows.append({"instance": inst, "ok": True, "status": status, "roots": roots, "tags": tags})
        except Exception as exc:
            rows.append({"instance": inst, "ok": False, "error": str(exc), "roots": [], "tags": []})
    return templates.TemplateResponse("arrs.html", {"request": request, "rows": rows})


@app.get("/queue", response_class=HTMLResponse)
async def queue_page(request: Request):
    require_auth(request)
    rows = []
    for inst in discover_instances():
        try:
            client = client_for_instance(inst)
            data = await client.queue(100)
            records = data.get("records", []) if isinstance(data, dict) else (data or [])
            for x in records:
                rows.append({"instance": inst, "item": x})
        except Exception:
            pass
    return templates.TemplateResponse("queue.html", {"request": request, "rows": rows})


@app.get("/discover", response_class=HTMLResponse)
async def discover_page(request: Request, q: str = "", media_type: str = "movie"):
    require_auth(request)
    results = []
    error = None
    if q.strip():
        try:
            results = await discover_lookup(q.strip(), media_type)
        except Exception as exc:
            error = str(exc)
    return templates.TemplateResponse("discover.html", {"request": request, "q": q, "media_type": media_type, "results": results, "error": error, "movie_roots": settings.movie_roots, "tv_roots": settings.tv_roots})


@app.post("/discover/add")
async def discover_add_route(request: Request, media_type: str = Form(...), candidate_json: str = Form(...), destination_key: str = Form("auto")):
    require_auth(request)
    try:
        candidate = json.loads(candidate_json)
        result = await discover_add(candidate, media_type, destination_key, search=True)
        msg = quote(f"Added {result['item'].get('title')} to {result['instance']} and queued search")
        return RedirectResponse(f"/discover?media_type={media_type}&notice={msg}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/discover?media_type={media_type}&error={quote(str(exc))}", status_code=303)


async def ensure_lidarr_artist(artist_name: str):
    artists = await lidarr.artists()
    target = normalize_title(artist_name)
    existing = next((a for a in artists if normalize_title(a.get("artistName") or a.get("name") or "") == target), None)
    if existing:
        return existing, False
    lookup = await lidarr.artist_lookup(artist_name)
    if not lookup:
        raise ArrError(f"Lidarr could not find artist: {artist_name}")
    # Prefer exact normalized name.
    candidate = next((x for x in lookup if normalize_title(x.get("artistName") or x.get("name") or "") == target), lookup[0])
    artist = await lidarr.add_artist(candidate, settings.lidarr_root, search=False)
    return artist, True


@app.get("/music", response_class=HTMLResponse)
async def music_page(request: Request, q: str = "", kind: str = "artist"):
    require_auth(request)
    results = []
    itunes = []
    error = None
    try:
        if q.strip():
            results = await search_musicbrainz(q, kind, 24)
            itunes = await itunes_search(q, "album" if kind == "album" else "musicArtist", 12)
        trends = await trending_artists(24, "this_week")
        releases = await trending_releases(18, "this_week")
        lidarr_artists = await lidarr.artists()
    except Exception as exc:
        trends, releases, lidarr_artists = [], [], []
        error = str(exc)
    return templates.TemplateResponse("music.html", {
        "request": request, "q": q, "kind": kind, "results": results, "trends": trends, "releases": releases,
        "itunes": itunes, "lidarr_artists": lidarr_artists, "genres": GENRES, "error": error,
        "itunes_enabled": settings.enable_itunes_search,
    })


@app.get("/music/artist", response_class=HTMLResponse)
async def music_artist_page(request: Request, name: str):
    require_auth(request)
    lookup = await lidarr.artist_lookup(name)
    exact = lookup[0] if lookup else None
    mb = await search_musicbrainz(name, "artist", 6)
    links = external_music_links(name)
    existing = None
    for a in await lidarr.artists():
        if normalize_title(a.get("artistName") or a.get("name") or "") == normalize_title(name):
            existing = a
            break
    albums = await lidarr.albums(int(existing["id"])) if existing else []
    return templates.TemplateResponse("music_artist.html", {"request": request, "name": name, "lookup": exact, "mb": mb, "existing": existing, "albums": albums, "links": links})


@app.post("/music/add-artist")
async def music_add_artist(request: Request, artist_name: str = Form(...), search_now: bool = Form(False)):
    require_auth(request)
    try:
        artist, created = await ensure_lidarr_artist(artist_name)
        if search_now:
            await lidarr.search_artist(int(artist["id"]))
        add_activity("music", artist_name, f"{'Added to' if created else 'Already in'} Lidarr" + ("; search queued" if search_now else ""))
        return RedirectResponse(f"/music/artist?name={quote(artist_name)}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/music?q={quote(artist_name)}&error={quote(str(exc))}", status_code=303)


@app.post("/music/search-album")
async def music_search_album(request: Request, artist_name: str = Form(...), album_title: str = Form(...)):
    require_auth(request)
    try:
        artist, _ = await ensure_lidarr_artist(artist_name)
        albums = await lidarr.albums(int(artist["id"]))
        album = next((x for x in albums if normalize_title(x.get("title", "")) == normalize_title(album_title)), None)
        if not album:
            await lidarr.command({"name": "RefreshArtist", "artistId": int(artist["id"])})
            raise ArrError("Artist is in Lidarr but this album is not in its metadata yet. RefreshArtist was queued; retry in a moment.")
        if not album.get("monitored"):
            album = await lidarr.monitor_album(int(album["id"]), True)
        await lidarr.search_album(int(album["id"]))
        add_activity("music", f"{artist_name} — {album_title}", "Album monitored and Lidarr Usenet search queued")
        return RedirectResponse(f"/music/artist?name={quote(artist_name)}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/music/artist?name={quote(artist_name)}&error={quote(str(exc))}", status_code=303)
