#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/arrnexus-mediastack-src}"
EXPECTED_BRANCH="feature/mediastack-v1-control-plane"

for cmd in git python3; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Required command is missing: $cmd" >&2; exit 1; }
done
[[ -d "$REPO_DIR/.git" ]] || { echo "ArrNexus checkout not found at $REPO_DIR" >&2; exit 2; }
cd "$REPO_DIR"
[[ "$(git branch --show-current)" == "$EXPECTED_BRANCH" ]] || { echo "Expected $EXPECTED_BRANCH" >&2; exit 3; }

python3 - <<'PY'
from pathlib import Path

path = Path('app/main.py')
text = path.read_text()

if 'from . import dashboard_boards' not in text:
    text = text.replace('from . import stack_setup\n', 'from . import stack_setup\nfrom . import dashboard_boards\n', 1)

old = '''@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if (
        os.getenv("MEDIASTACK_GUIDED_SETUP", "false").lower() in {"1", "true", "yes"}
        and not stack_setup.state().get("completed")
    ):
        return _go("/stack-setup")
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
'''

new = '''@app.get("/", response_class=HTMLResponse)
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
'''

if old in text:
    text = text.replace(old, new, 1)
elif 'board_id = str(request.query_params.get("board")' not in text:
    raise SystemExit('Could not locate dashboard route; refusing unsafe patch')

anchor = '@app.get("/pipeline", response_class=HTMLResponse)\n'
if '@app.get("/dashboard/boards", response_class=HTMLResponse)' not in text:
    routes = '''@app.get("/dashboard/boards", response_class=HTMLResponse)
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


'''
    if anchor not in text:
        raise SystemExit('Could not locate pipeline anchor; refusing unsafe route insertion')
    text = text.replace(anchor, routes + anchor, 1)

path.write_text(text)

music = Path('app/music.py')
mtext = music.read_text()
old_base = '    base = str(public_url or "").strip().rstrip("/")\n    if not base and request_url:\n'
new_base = '''    base = str(public_url or "").strip().rstrip("/")
    if not base:
        domain = setting_get("dashboard.public_domain", "").strip().lower()
        if domain:
            domain = domain.removeprefix("https://").removeprefix("http://").strip("/")
            base = f"https://arrnexus.{domain}"
    if not base and request_url:
'''
if old_base in mtext:
    mtext = mtext.replace(old_base, new_base, 1)
elif 'dashboard.public_domain' not in mtext:
    raise SystemExit('Could not locate Spotify redirect base logic')
mtext = mtext.replace('host in {"127.0.0.1", "localhost", "::1"}', 'host in {"127.0.0.1", "::1"}')
music.write_text(mtext)
PY

python3 -m py_compile app/main.py app/dashboard_boards.py app/music.py

git add app/main.py app/music.py app/dashboard_boards.py app/templates/dashboard_board.html app/templates/dashboard_boards.html app/templates/base.html app/static/dashboard_boards.css app/static/dashboard_boards.js mediastack/scripts/find-homarr-data.sh mediastack/scripts/integrate-dashboard-boards-v1.sh

if git diff --cached --quiet; then
  echo "Dashboard boards integration is already applied."
  exit 0
fi

git commit -m "Add Homarr-style native dashboard boards and Spotify public URL fallback"
git push origin "$EXPECTED_BRANCH"

echo
echo "ArrNexus native dashboard boards integrated and pushed."
echo "No running media container was changed."
echo "Next rebuild the disposable UI test on port 8585."
