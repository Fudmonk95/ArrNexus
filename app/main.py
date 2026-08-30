from __future__ import annotations
from pathlib import Path
import json
import secrets
from urllib.parse import quote

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .db import init_db, recent_imports, log_import
from .scanner import scan_source, normalize_title, inspect_item
from .routing import decide_movie, decide_tv
from .arr import RadarrClient, SonarrClient, LidarrClient, ArrError
from .importer import import_movie_source, import_tv_source, ImportErrorSafe
from .namespace import view_path, is_within_logical, namespace_status, NamespaceError

BASE = Path(__file__).resolve().parent
app = FastAPI(title="DMM Arr Router")
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

radarr = RadarrClient()
sonarr = SonarrClient()
lidarr = LidarrClient()


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


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    require_auth(request)
    items = scan_source()
    statuses = {
        "radarr": await arr_status(radarr),
        "sonarr": await arr_status(sonarr),
        "lidarr": await arr_status(lidarr),
    }
    movie_count = sum(1 for i in items if i.media_type == "movie")
    tv_count = sum(1 for i in items if i.media_type == "tv")
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "items": items[:12],
        "source_count": len(items),
        "movie_count": movie_count,
        "tv_count": tv_count,
        "statuses": statuses,
        "recent": recent_imports(12),
        "source_root": settings.source_root,
        "namespace": namespace_status(),
    })


async def find_radarr_match(item):
    movies = await radarr.movies()
    n = normalize_title(item.title_guess)
    exact = [m for m in movies if normalize_title(m.get("title", "")) == n and (not item.year_guess or m.get("year") == item.year_guess)]
    if exact:
        return exact[0], None
    lookup = await radarr.lookup(f"{item.title_guess} {item.year_guess or ''}".strip())
    return None, lookup[:8]


async def find_sonarr_match(item):
    series = await sonarr.series()
    n = normalize_title(item.title_guess)
    exact = [s for s in series if normalize_title(s.get("title", "")) == n and (not item.year_guess or s.get("year") == item.year_guess)]
    if exact:
        return exact[0], None
    lookup = await sonarr.lookup(item.title_guess)
    return None, lookup[:8]


@app.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request):
    require_auth(request)
    items = scan_source()
    return templates.TemplateResponse("inbox.html", {
        "request": request,
        "items": items,
        "movie_roots": settings.movie_roots,
        "tv_roots": settings.tv_roots,
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
    existing = None
    lookup = []
    error = None
    try:
        if item.media_type == "movie":
            existing, lookup = await find_radarr_match(item)
            metadata = existing or (lookup[0] if lookup else {})
            decision = decide_movie(item.title_guess, metadata)
        else:
            existing, lookup = await find_sonarr_match(item)
            metadata = existing or (lookup[0] if lookup else {})
            decision = decide_tv(item.title_guess, metadata)
    except Exception as e:
        error = str(e)
        decision = decide_movie(item.title_guess) if item.media_type == "movie" else decide_tv(item.title_guess)

    return templates.TemplateResponse("item.html", {
        "request": request,
        "item": item,
        "existing": existing,
        "lookup": lookup,
        "decision": decision,
        "roots": settings.movie_roots if item.media_type == "movie" else settings.tv_roots,
        "error": error,
    })


@app.post("/import")
async def do_import(
    request: Request,
    source_path: str = Form(...),
    media_type: str = Form(...),
    destination_key: str = Form(...),
    candidate_index: int = Form(-1),
):
    require_auth(request)
    src = Path(source_path)
    if not is_within_logical(src, settings.source_root):
        raise HTTPException(400, "Invalid source path")
    try:
        if not view_path(src).exists():
            raise HTTPException(404, "Source not found")
    except NamespaceError as exc:
        raise HTTPException(503, str(exc))

    item = inspect_item(src)
    try:
        if media_type == "movie":
            roots = settings.movie_roots
            if destination_key not in roots:
                raise ImportErrorSafe("Invalid movie destination")
            dest_root = roots[destination_key]
            existing, lookup = await find_radarr_match(item)
            if existing:
                movie = existing
            else:
                if not lookup:
                    raise ImportErrorSafe("No Radarr match found. Use Radarr to add/match this title first.")
                idx = candidate_index if 0 <= candidate_index < len(lookup) else 0
                movie = await radarr.add_movie(lookup[idx], dest_root)
            dest_dir = movie.get("path") or f"{dest_root}/{movie['title']} ({movie.get('year', '')})"
            created = import_movie_source(source_path, dest_dir)
            await radarr.rescan(int(movie["id"]))
            log_import(
                source_path=source_path,
                source_name=item.name,
                media_type="movie",
                destination_key=destination_key,
                destination_path=dest_dir,
                arr_name="radarr",
                arr_id=movie.get("id"),
                status="linked",
                note=f"Created {len(created)} symlink(s)",
            )
        elif media_type == "tv":
            roots = settings.tv_roots
            if destination_key not in roots:
                raise ImportErrorSafe("Invalid TV destination")
            dest_root = roots[destination_key]
            existing, lookup = await find_sonarr_match(item)
            if existing:
                series = existing
            else:
                if not lookup:
                    raise ImportErrorSafe("No Sonarr match found. Use Sonarr to add/match this title first.")
                idx = candidate_index if 0 <= candidate_index < len(lookup) else 0
                series = await sonarr.add_series(lookup[idx], dest_root)
            series_path = series.get("path") or f"{dest_root}/{series['title']}"
            created = import_tv_source(source_path, series_path)
            await sonarr.rescan(int(series["id"]))
            log_import(
                source_path=source_path,
                source_name=item.name,
                media_type="tv",
                destination_key=destination_key,
                destination_path=series_path,
                arr_name="sonarr",
                arr_id=series.get("id"),
                status="linked",
                note=f"Created {len(created)} symlink(s)",
            )
        else:
            raise ImportErrorSafe("Unknown media type")
    except (ArrError, ImportErrorSafe, OSError) as e:
        log_import(
            source_path=source_path,
            source_name=item.name,
            media_type=media_type,
            destination_key=destination_key,
            destination_path="",
            arr_name="radarr" if media_type == "movie" else "sonarr",
            arr_id=None,
            status="error",
            note=str(e),
        )
        return RedirectResponse(f"/item?path={quote(source_path)}&error={quote(str(e))}", status_code=303)

    return RedirectResponse("/inbox", status_code=303)


@app.get("/arrs", response_class=HTMLResponse)
async def arrs_page(request: Request):
    require_auth(request)
    data = {}
    for key, client in [("Radarr", radarr), ("Sonarr", sonarr), ("Lidarr", lidarr)]:
        try:
            data[key] = {
                "status": await client.status(),
                "roots": await client.roots(),
                "tags": await client.tags(),
                "error": None,
            }
        except Exception as e:
            data[key] = {"status": None, "roots": [], "tags": [], "error": str(e)}
    return templates.TemplateResponse("arrs.html", {"request": request, "data": data})


@app.get("/libraries", response_class=HTMLResponse)
async def libraries_page(request: Request):
    require_auth(request)
    roots = {**{f"movie:{k}": v for k, v in settings.movie_roots.items()}, **{f"tv:{k}": v for k, v in settings.tv_roots.items()}, "music:default": settings.lidarr_root}
    info = []
    for key, path in roots.items():
        try:
            p = view_path(path)
            exists = p.exists()
            children = [c for c in p.iterdir() if c.is_dir()] if exists else []
            info.append({"key": key, "path": path, "exists": exists, "count": len(children), "sample": [c.name for c in children[:8]]})
        except Exception as e:
            info.append({"key": key, "path": path, "exists": p.exists(), "count": 0, "sample": [], "error": str(e)})
    return templates.TemplateResponse("libraries.html", {"request": request, "roots": info})


@app.get("/music", response_class=HTMLResponse)
async def music_page(request: Request, q: str = "", artist_id: int | None = None, album_id: int | None = None):
    require_auth(request)
    result = {"lookup": [], "artists": [], "albums": [], "releases": [], "error": None}
    try:
        result["artists"] = await lidarr.artists()
        if q:
            result["lookup"] = await lidarr.artist_lookup(q)
        if artist_id:
            result["albums"] = await lidarr.albums(artist_id)
        if album_id:
            result["releases"] = await lidarr.releases_for_album(album_id)
    except Exception as e:
        result["error"] = str(e)
    return templates.TemplateResponse("music.html", {
        "request": request,
        "q": q,
        "artist_id": artist_id,
        "album_id": album_id,
        **result,
    })


@app.post("/music/grab")
async def music_grab(request: Request, release_json: str = Form(...)):
    require_auth(request)
    try:
        release = json.loads(release_json)
        await lidarr.grab_release(release)
    except Exception as e:
        return RedirectResponse(f"/music?error={quote(str(e))}", status_code=303)
    return RedirectResponse("/music", status_code=303)
