#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/arrnexus-mediastack-src}"
TEST_NAME="${TEST_NAME:-arrnexus-mediastack-ui-test}"
TEST_IMAGE="${TEST_IMAGE:-arrnexus:mediastack-v1-ui-test}"
TEST_PORT="${TEST_PORT:-8585}"
TEST_BIND="${TEST_BIND:-127.0.0.1}"
TEST_ROOT="${TEST_ROOT:-/opt/arrnexus-mediastack-test}"
LIVE_DATA="${LIVE_DATA:-/mnt/appdata/arrnexus/data}"
AGENT_NAME="${AGENT_NAME:-arrnexus-stack-agent}"
EXPECTED_BRANCH="feature/mediastack-v1-control-plane"

for cmd in docker python3 curl; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Required command is missing: $cmd" >&2; exit 1; }
done

[[ -d "$REPO_DIR/.git" ]] || { echo "ArrNexus feature checkout not found at $REPO_DIR" >&2; exit 2; }
docker inspect arrnexus >/dev/null 2>&1 || { echo "Production ArrNexus container was not found. Refusing test launch." >&2; exit 3; }
docker inspect "$AGENT_NAME" >/dev/null 2>&1 || { echo "MediaStack agent $AGENT_NAME is not running. Start monitor mode first." >&2; exit 4; }

if docker inspect "$TEST_NAME" >/dev/null 2>&1; then
  echo "Removing previous disposable test container $TEST_NAME..."
  docker rm -f "$TEST_NAME" >/dev/null
fi

if ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)${TEST_PORT}$"; then
  echo "Port $TEST_PORT is already in use; choose another with TEST_PORT=<port>." >&2
  exit 5
fi

cd "$REPO_DIR"
BRANCH="$(git branch --show-current)"
if [[ "$BRANCH" != "$EXPECTED_BRANCH" ]]; then
  echo "Expected $EXPECTED_BRANCH, found $BRANCH. Refusing to build the wrong source." >&2
  exit 6
fi

mkdir -p "$TEST_ROOT/data"
chmod 700 "$TEST_ROOT" "$TEST_ROOT/data"
LIVE_DB="$LIVE_DATA/router.db"
TEST_DB="$TEST_ROOT/data/router.db"
[[ -f "$LIVE_DB" ]] || { echo "Live ArrNexus database not found at $LIVE_DB" >&2; exit 8; }

echo "Creating consistent read snapshot of ArrNexus database..."
rm -f "$TEST_DB"
python3 - "$LIVE_DB" "$TEST_DB" <<'PY'
import sqlite3, sys
src_path, dst_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
dst = sqlite3.connect(dst_path)
with dst:
    src.backup(dst)
dst.close(); src.close()
PY
chmod 600 "$TEST_DB"

echo "Building $TEST_IMAGE from $BRANCH..."
docker build -t "$TEST_IMAGE" .

echo "Starting isolated ArrNexus control-plane UI test on ${TEST_BIND}:${TEST_PORT}..."
docker run -d \
  --name "$TEST_NAME" \
  --restart no \
  -p "${TEST_BIND}:${TEST_PORT}:8000" \
  -e TZ=Europe/London \
  -e DB_PATH=/data/router.db \
  -e ARRNEXUS_HTTPS_ONLY=false \
  -e MEDIASTACK_GUIDED_SETUP=false \
  -e MEDIASTACK_AGENT_URL="http://${AGENT_NAME}:8787" \
  -v "$TEST_ROOT/data:/data" \
  "$TEST_IMAGE" \
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --lifespan off >/dev/null

mapfile -t AGENT_NETWORKS < <(docker inspect "$AGENT_NAME" --format '{{range $name, $cfg := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' | sed '/^$/d' | sort -u)
ATTACHED=0
for network in "${AGENT_NETWORKS[@]}"; do
  case "$network" in host|none|bridge) continue ;; esac
  if docker network connect "$network" "$TEST_NAME" 2>/dev/null; then
    ATTACHED=1
  elif docker inspect "$TEST_NAME" --format '{{json .NetworkSettings.Networks}}' | grep -Fq '"'"$network"'"'; then
    ATTACHED=1
  fi
done
if [[ "$ATTACHED" -ne 1 ]]; then
  echo "No shared user-defined Stack Agent network could be attached; stopping test container." >&2
  docker rm -f "$TEST_NAME" >/dev/null
  exit 10
fi

for _ in $(seq 1 30); do
  if docker exec "$TEST_NAME" python - <<'PY' >/dev/null 2>&1
import urllib.request
urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()
PY
  then break; fi
  sleep 1
done

if ! docker exec "$TEST_NAME" python - <<'PY' >/dev/null 2>&1
import urllib.request
urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()
PY
then
  echo "Test ArrNexus did not become healthy. Recent logs:" >&2
  docker logs --tail 120 "$TEST_NAME" >&2 || true
  exit 11
fi

echo
echo "Testing Stack Agent health/capabilities from the UI-test container..."
docker exec "$TEST_NAME" python - <<'PY'
import json, urllib.request
for path in ('/health', '/api/capabilities'):
    with urllib.request.urlopen('http://arrnexus-stack-agent:8787' + path, timeout=5) as r:
        payload = json.load(r)
    print(path)
    print(json.dumps(payload, indent=2))
    assert payload.get('ok') is True
PY

echo
echo "MediaStack v1 UI test is ready."
echo "Production ArrNexus was not stopped or recreated."
echo "Background ArrNexus workers and automatic updates are disabled in this test container (--lifespan off)."
echo "The Stack Agent should still report write_enabled=false for this first control-plane UI validation."
if [[ "$TEST_BIND" == "127.0.0.1" ]]; then
  echo "Open through an SSH tunnel or locally: http://127.0.0.1:${TEST_PORT}/mediastack"
else
  echo "Open MediaStack: http://${TEST_BIND}:${TEST_PORT}/mediastack"
  echo "Open guided setup: http://${TEST_BIND}:${TEST_PORT}/stack-setup"
fi
