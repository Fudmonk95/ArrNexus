#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/arrnexus-mediastack-src}"
EXPECTED_BRANCH="feature/mediastack-v1-control-plane"

cd "$REPO_DIR"

if [[ "$(git branch --show-current)" != "$EXPECTED_BRANCH" ]]; then
  echo "Expected $EXPECTED_BRANCH; refusing to patch another branch." >&2
  exit 2
fi

python3 - <<'PY'
from pathlib import Path

agent = Path('mediastack/agent/main.py')
app = Path('app/mediastack.py')

agent_text = agent.read_text(encoding='utf-8')
old_agent = '@app.post("/api/actions/{name}/{action}")\nasync def lifecycle_action'
new_agent = '@app.post("/api/lifecycle/{name}/{action}")\nasync def lifecycle_action'
if old_agent not in agent_text:
    if new_agent not in agent_text:
        raise SystemExit('Could not locate Stack Agent lifecycle route')
else:
    agent_text = agent_text.replace(old_agent, new_agent, 1)
    agent.write_text(agent_text, encoding='utf-8')

app_text = app.read_text(encoding='utf-8')
old_call = 'result = await _post(f"/api/actions/{name}/{action}", timeout=75.0)'
new_call = 'result = await _post(f"/api/lifecycle/{name}/{action}", timeout=75.0)'
if old_call not in app_text:
    if new_call not in app_text:
        raise SystemExit('Could not locate ArrNexus lifecycle proxy call')
else:
    app_text = app_text.replace(old_call, new_call, 1)
    app.write_text(app_text, encoding='utf-8')
PY

python3 -m py_compile mediastack/agent/main.py app/mediastack.py

echo "Patched route layout:"
grep -nE '@app.post\("/api/(lifecycle|actions)/\{name\}/' mediastack/agent/main.py

git add mediastack/agent/main.py app/mediastack.py
if git diff --cached --quiet; then
  echo "Route fix already applied."
  exit 0
fi

git commit -m "Fix MediaStack update route collision"
git push origin "$EXPECTED_BRANCH"

echo
echo "MediaStack update route collision fixed and pushed."
echo "No running Docker media container was changed by this script."
