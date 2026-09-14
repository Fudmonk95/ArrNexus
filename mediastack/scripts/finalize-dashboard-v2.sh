#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BRANCH="feature/mediastack-v1-control-plane"
cd "$ROOT"

python3 - <<'PY'
from pathlib import Path
p = Path("app/templates/dashboard_board.html")
s = p.read_text(encoding="utf-8")

# Jinja resolves dict attributes before mapping keys for names such as `items`.
# `board.items` therefore becomes dict.items (a method) rather than the board's
# widget list. Always use an explicit mapping lookup for this key.
s = s.replace(
    '{% for item in board.items %}',
    "{% for item in board['items'] %}",
)

s = s.replace(
    '<div class="resource-gauge" style="--pct:{{ [metrics.cpu,100]|min }}">',
    '<div class="resource-gauge" style="--angle:{{ ([metrics.cpu,100]|min) * 3.6 }}deg">',
)
s = s.replace(
    '<div class="resource-gauge memory" style="--pct:{{ metrics.memory }}">',
    '<div class="resource-gauge memory" style="--angle:{{ ([metrics.memory,100]|min) * 3.6 }}deg">',
)
# Mark service tiles so the 10-second runtime poll can change their health state
# without reloading the whole dashboard.
s = s.replace(
    'class="app-tile status-{{ service.board_status }}" href=',
    'class="app-tile status-{{ service.board_status }}" data-service="{{ service.name }}" href=',
)
s = s.replace(
    'class="app-tile status-{{ service.board_status }} disabled">',
    'class="app-tile status-{{ service.board_status }} disabled" data-service="{{ service.name }}">',
)
s = s.replace(
    '<span>{{ service.status_text or service.state }}</span></div>',
    '<span data-service-state>{{ service.status_text or service.state }}</span></div>',
)

p.write_text(s, encoding="utf-8")
PY

# Compile templates when the host has Jinja installed. The ArrNexus Docker build
# always installs Jinja from requirements.txt, so lack of a host Python package
# must not block this source-only finalizer.
python3 - <<'PY'
from pathlib import Path
try:
    from jinja2 import Environment, FileSystemLoader
except Exception:
    print("Jinja host package not installed; template compile will be validated by the Docker build/runtime.")
else:
    root = Path("app/templates")
    env = Environment(loader=FileSystemLoader(str(root)))
    for path in sorted(root.rglob("*.html")):
        rel = path.relative_to(root).as_posix()
        env.get_template(rel)
    print("Jinja templates: OK")
PY

python3 -m py_compile app/dashboard_boards.py app/music.py app/main.py

# Guard against the exact Jinja dict.items regression that caused Dashboard v2
# to return HTTP 500.
if grep -Fq '{% for item in board.items %}' app/templates/dashboard_board.html; then
  echo "ERROR: unsafe board.items Jinja access remains" >&2
  exit 1
fi

grep -Fq "{% for item in board['items'] %}" app/templates/dashboard_board.html

if git diff --quiet -- app/templates/dashboard_board.html; then
  echo "Dashboard v2 template finalizer is already applied."
else
  git add app/templates/dashboard_board.html
  git commit -m "Polish Dashboard v2 live gauges and health tiles"
  git push origin "$BRANCH"
fi

echo "Dashboard v2 template validation complete."
