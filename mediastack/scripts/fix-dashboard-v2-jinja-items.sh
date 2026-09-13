#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BRANCH="feature/mediastack-v1-control-plane"
cd "$ROOT"

python3 - <<'PY'
from pathlib import Path
p = Path("app/templates/dashboard_board.html")
s = p.read_text(encoding="utf-8")
old = "{% for item in board.items %}"
new = "{% for item in board['items'] %}"
if old not in s and new not in s:
    raise SystemExit("Could not find Dashboard v2 board items loop")
s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
PY

# Validate the template before committing.
python3 - <<'PY'
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
root = Path("app/templates")
env = Environment(loader=FileSystemLoader(str(root)))
env.get_template("dashboard_board.html")
print("dashboard_board.html: Jinja syntax OK")
PY

# Guard against this exact regression coming back.
if grep -Fq '{% for item in board.items %}' app/templates/dashboard_board.html; then
  echo "ERROR: unsafe board.items access still present" >&2
  exit 1
fi

grep -Fq "{% for item in board['items'] %}" app/templates/dashboard_board.html

if git diff --quiet -- app/templates/dashboard_board.html; then
  echo "Dashboard v2 Jinja items fix already applied."
else
  git add app/templates/dashboard_board.html
  git commit -m "Fix Dashboard v2 Jinja board items lookup"
  git push origin "$BRANCH"
fi

echo
echo "Dashboard v2 Jinja items collision fixed."
echo "No running media container was changed."
