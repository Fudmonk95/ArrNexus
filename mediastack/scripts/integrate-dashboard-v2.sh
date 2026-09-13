#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MAIN="$ROOT/app/main.py"
BRANCH="feature/mediastack-v1-control-plane"

cd "$ROOT"

python3 - <<'PY'
from pathlib import Path
p = Path("app/main.py")
s = p.read_text(encoding="utf-8")

# 1) Binary Jellyfin artwork proxy needs a raw Response.
s = s.replace(
    "from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse, FileResponse",
    "from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse, FileResponse, Response",
)

# 2) Dashboard v2 JSON/control routes.
marker = '@app.get("/api/dashboard/boards/{board_id}/runtime")'
if marker not in s:
    anchor = '''@app.get("/api/dashboard/boards")\nasync def dashboard_boards_api(request: Request):\n    _require_user(request)\n    return dashboard_boards.board_api()\n'''
    addition = r'''

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
'''
    if anchor not in s:
        raise SystemExit("Could not locate dashboard boards API anchor in app/main.py")
    s = s.replace(anchor, anchor + addition, 1)

# 3) Appearance form handler.
if '@app.post("/dashboard/boards/appearance")' not in s:
    anchor = '''@app.post("/dashboard/boards/save")\nasync def dashboard_board_save(request: Request):\n'''
    addition = r'''
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


'''
    if anchor not in s:
        raise SystemExit("Could not locate dashboard board save anchor in app/main.py")
    s = s.replace(anchor, addition + anchor, 1)

# 4) Prefer the Spotify-safe public base on the settings page. This fixes the
# common case where an old LAN http:// URL masks the Cloudflare HTTPS domain.
old = '''    public_url = setting_get("app.public_url", "") or settings.public_url\n    redirect_uri = music.spotify_redirect_uri(public_url, str(request.url))\n    redirect_ok, redirect_message = music.spotify_redirect_validation(redirect_uri)\n'''
new = '''    stored_public_url = setting_get("app.public_url", "") or settings.public_url\n    redirect_uri = music.spotify_redirect_uri(stored_public_url, str(request.url))\n    redirect_ok, redirect_message = music.spotify_redirect_validation(redirect_uri)\n    public_url = redirect_uri.removesuffix("/music/spotify/callback") if redirect_uri else stored_public_url\n'''
if old in s:
    s = s.replace(old, new, 1)

# 5) Spotify app credential diagnostic action.
if '@app.post("/music/spotify/test")' not in s:
    anchor = '''@app.get("/music/spotify/connect")\nasync def spotify_connect(request: Request):\n'''
    addition = r'''
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


'''
    if anchor not in s:
        raise SystemExit("Could not locate Spotify connect anchor in app/main.py")
    s = s.replace(anchor, addition + anchor, 1)

p.write_text(s, encoding="utf-8")
PY

python3 -m py_compile \
  app/main.py \
  app/dashboard_boards.py \
  app/music.py

# Ensure the new endpoints exist before committing.
grep -q '/api/dashboard/boards/{board_id}/layout' app/main.py
grep -q '/api/dashboard/jellyfin/image/{item_id}' app/main.py
grep -q '/music/spotify/test' app/main.py

if git diff --quiet -- app/main.py; then
  echo "Dashboard v2 main.py integration is already applied."
else
  git add app/main.py
  git commit -m "Wire Dashboard v2 editor and Spotify diagnostics into ArrNexus"
  git push origin "$BRANCH"
fi

echo
echo "Dashboard v2 + Spotify integration complete."
echo "No running Docker media container was changed."
echo "Rebuild only the disposable UI test on 8585 next."
