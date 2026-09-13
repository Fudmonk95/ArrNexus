#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BRANCH="feature/mediastack-v1-control-plane"
cd "$ROOT"

python3 - <<'PY'
from pathlib import Path
p = Path("app/templates/dashboard_board.html")
s = p.read_text(encoding="utf-8")

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

# Compile every template so a Jinja syntax error is caught before the test
# container is rebuilt.
python3 - <<'PY'
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
root = Path("app/templates")
env = Environment(loader=FileSystemLoader(str(root)))
for path in sorted(root.rglob("*.html")):
    rel = path.relative_to(root).as_posix()
    env.get_template(rel)
print("Jinja templates: OK")
PY

python3 -m py_compile app/dashboard_boards.py app/music.py app/main.py

if git diff --quiet -- app/templates/dashboard_board.html; then
  echo "Dashboard v2 template finalizer is already applied."
else
  git add app/templates/dashboard_board.html
  git commit -m "Polish Dashboard v2 live gauges and health tiles"
  git push origin "$BRANCH"
fi

echo "Dashboard v2 template validation complete."
