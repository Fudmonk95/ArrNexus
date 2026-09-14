#!/usr/bin/env bash
set -euo pipefail

REPO="Fudmonk95/ArrNexus"
BRANCH="feature/mediastack-v0.1"

for cmd in git gh python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Required command is missing: $cmd" >&2
    exit 1
  fi
done

if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub CLI is not authenticated." >&2
  exit 2
fi

gh auth setup-git >/dev/null
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

gh repo clone "$REPO" "$WORK/repo" -- --quiet
cd "$WORK/repo"
git fetch origin "$BRANCH" --quiet
git checkout -B "$BRANCH" "origin/$BRANCH" --quiet

if [[ "$(tr -d '\r\n ' < VERSION)" != "13.4.0" ]]; then
  echo "Refusing to integrate against unexpected VERSION: $(cat VERSION)" >&2
  exit 3
fi

python3 - <<'PY'
from pathlib import Path

p = Path('app/main.py')
s = p.read_text(encoding='utf-8')

if 'from . import mediastack\n' not in s:
    needle = 'from . import magic_intake\nfrom . import zurg\n'
    if needle not in s:
        raise SystemExit('Could not find the expected v13.4 import block in app/main.py')
    s = s.replace(needle, 'from . import magic_intake\nfrom . import mediastack\nfrom . import zurg\n', 1)

status_task = '        asyncio.create_task(mediastack.status_loop(), name="mediastack-status"),\n'
metadata_task = '        asyncio.create_task(mediastack.metadata_loop(), name="mediastack-metadata"),\n'
if status_task not in s:
    needle = '        asyncio.create_task(magic_intake.verification_loop(), name="magic-intake-verifier"),\n'
    if needle not in s:
        raise SystemExit('Could not find the expected v13.4 lifespan task block in app/main.py')
    s = s.replace(needle, needle + status_task + metadata_task, 1)

routes = '''@app.get("/mediastack", response_class=HTMLResponse)\nasync def mediastack_page(request: Request):\n    return _render(request, "mediastack.html", stack=mediastack.cached_snapshot())\n\n\n@app.get("/api/mediastack")\nasync def mediastack_api(request: Request):\n    _require_user(request)\n    return mediastack.cached_snapshot()\n\n\n@app.post("/api/mediastack/refresh")\nasync def mediastack_refresh_api(request: Request):\n    _require_user(request)\n    return await mediastack.refresh_status()\n\n\n@app.post("/api/mediastack/check-updates")\nasync def mediastack_update_check_api(request: Request):\n    _require_user(request)\n    await mediastack.refresh_updates()\n    return mediastack.cached_snapshot()\n\n\n@app.post("/api/mediastack/refresh-configs")\nasync def mediastack_config_refresh_api(request: Request):\n    _require_user(request)\n    await mediastack.refresh_configs()\n    return mediastack.cached_snapshot().get("configs") or {}\n\n\n@app.get("/api/mediastack/logs/{name}")\nasync def mediastack_logs_api(request: Request, name: str, tail: int = 250):\n    _require_user(request)\n    try:\n        return await mediastack.logs(name, tail)\n    except Exception as exc:\n        raise HTTPException(502, str(exc))\n\n\n@app.get("/api/mediastack/config")\nasync def mediastack_config_api(request: Request, root: str, path: str):\n    _require_user(request)\n    try:\n        return await mediastack.config_file(root, path)\n    except Exception as exc:\n        raise HTTPException(502, str(exc))\n\n\n'''

if '@app.get("/mediastack", response_class=HTMLResponse)' not in s:
    needle = '@app.get("/logs", response_class=HTMLResponse)\n'
    if needle not in s:
        raise SystemExit('Could not find /logs route insertion point in app/main.py')
    s = s.replace(needle, routes + needle, 1)

p.write_text(s, encoding='utf-8')
PY

python3 -m py_compile app/main.py app/mediastack.py

if git diff --quiet; then
  echo "MediaStack UI integration is already present on $BRANCH."
  exit 0
fi

git config user.name "RenegadeMonk"
git config user.email "17593249+Fudmonk95@users.noreply.github.com"

echo
echo "Changes being committed:"
git diff --stat

git add app/main.py
git commit -m "Wire MediaStack dashboard into ArrNexus v13.4"
git push origin "$BRANCH"

echo
echo "MediaStack UI integration pushed to $BRANCH."
echo "No running containers were changed."
