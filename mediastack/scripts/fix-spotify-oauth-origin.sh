#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BRANCH="feature/mediastack-v1-control-plane"
cd "$ROOT"

python3 - <<'PY'
from pathlib import Path
p = Path("app/main.py")
s = p.read_text(encoding="utf-8")

s = s.replace(
    "from urllib.parse import quote_plus",
    "from urllib.parse import quote_plus, urlparse",
)

old = '''    if not redirect_ok:\n        _flash(request, f"Spotify cannot be linked yet. {redirect_message} Redirect URI: {redirect_uri or 'not configured'}", "error")\n        return _go("/music/settings")\n    state = secrets.token_urlsafe(24)\n'''
new = '''    if not redirect_ok:\n        _flash(request, f"Spotify cannot be linked yet. {redirect_message} Redirect URI: {redirect_uri or 'not configured'}", "error")\n        return _go("/music/settings")\n\n    # OAuth state is deliberately tied to the browser session that starts the\n    # flow. Starting from a LAN/test hostname and returning through the public\n    # Cloudflare hostname creates a different browser cookie/session and makes\n    # the state check fail. Refuse that unsafe/ambiguous flow up front and tell\n    # the user which hostname to open instead.\n    redirect_host = (urlparse(redirect_uri).hostname or "").lower()\n    forwarded_host = str(request.headers.get("x-forwarded-host") or "").split(",", 1)[0].strip()\n    current_netloc = forwarded_host or request.url.netloc\n    try:\n        current_host = (urlparse("//" + current_netloc).hostname or "").lower()\n    except Exception:\n        current_host = (request.url.hostname or "").lower()\n    if redirect_host and current_host and redirect_host != current_host:\n        public_base = redirect_uri.removesuffix("/music/spotify/callback")\n        _flash(\n            request,\n            f"Open ArrNexus at {public_base} and press Link Spotify there. "\n            f"This browser session is on {current_netloc}, but Spotify returns to {redirect_host}; "\n            "using two different ArrNexus origins would fail the OAuth state check.",\n            "error",\n        )\n        return _go("/music/settings")\n\n    state = secrets.token_urlsafe(24)\n'''
if old not in s:
    if "using two different ArrNexus origins would fail the OAuth state check" not in s:
        raise SystemExit("Could not locate Spotify connect guard anchor in app/main.py")
else:
    s = s.replace(old, new, 1)

old_cb = '''    if error or not code or state != expected_state:\n        _flash(request, error or "Spotify authorization state did not match.", "error")\n        return _go("/music")\n'''
new_cb = '''    if error or not code or state != expected_state:\n        if error:\n            message = error\n        elif state != expected_state:\n            message = (\n                "Spotify authorization state did not match. Start Link Spotify from the same HTTPS "\n                "ArrNexus hostname used by the callback; do not begin OAuth from a LAN IP/test origin "\n                "and return through a different ArrNexus instance."\n            )\n        else:\n            message = "Spotify authorization did not return an authorization code."\n        _flash(request, message, "error")\n        return _go("/music")\n'''
if old_cb in s:
    s = s.replace(old_cb, new_cb, 1)

p.write_text(s, encoding="utf-8")
PY

python3 -m py_compile app/main.py app/music.py

grep -q 'using two different ArrNexus origins would fail the OAuth state check' app/main.py
grep -q 'Start Link Spotify from the same HTTPS' app/main.py

if git diff --quiet -- app/main.py; then
  echo "Spotify OAuth origin guard already applied."
else
  git add app/main.py
  git commit -m "Guard Spotify OAuth against cross-origin state mismatch"
  git push origin "$BRANCH"
fi

echo
echo "Spotify OAuth origin guard applied."
echo "No running media container was changed."
