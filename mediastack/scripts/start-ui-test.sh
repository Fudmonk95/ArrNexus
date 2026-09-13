#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/arrnexus-mediastack-src}"
TEST_NAME="${TEST_NAME:-arrnexus-mediastack-ui-test}"
TEST_IMAGE="${TEST_IMAGE:-arrnexus:mediastack-ui-test}"
TEST_PORT="${TEST_PORT:-8585}"
TEST_BIND="${TEST_BIND:-127.0.0.1}"
TEST_ROOT="${TEST_ROOT:-/opt/arrnexus-mediastack-test}"
LIVE_DATA="${LIVE_DATA:-/mnt/appdata/arrnexus/data}"
AGENT_NAME="${AGENT_NAME:-arrnexus-stack-agent}"

for cmd in docker python3 curl; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Required command is missing: $cmd" >&2
    exit 1
  fi
done

if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "ArrNexus feature checkout not found at $REPO_DIR" >&2
  exit 2
fi

if ! docker inspect arrnexus >/dev/null 2>&1; then
  echo "Production ArrNexus container was not found. Refusing test launch." >&2
  exit 3
fi

if ! docker inspect "$AGENT_NAME" >/dev/null 2>&1; then
  echo "MediaStack agent $AGENT_NAME is not running. Start monitor mode first." >&2
  exit 4
fi

if ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)${TEST_PORT}$"; then
  echo "Port $TEST_PORT is already in use; choose another with TEST_PORT=<port>." >&2
  exit 5
fi

cd "$REPO_DIR"

BRANCH="$(git branch --show-current)"
if [[ "$BRANCH" != "feature/mediastack-v0.1" ]]; then
  echo "Expected feature/mediastack-v0.1, found $BRANCH. Refusing to build the wrong source." >&2
  exit 6
fi

VERSION="$(tr -d '\r\n ' < VERSION)"
if [[ "$VERSION" != "13.4.0" ]]; then
  echo "Expected recovered ArrNexus 13.4.0 baseline, found $VERSION." >&2
  exit 7
fi

mkdir -p "$TEST_ROOT/data"
chmod 700 "$TEST_ROOT" "$TEST_ROOT/data"

LIVE_DB="$LIVE_DATA/router.db"
TEST_DB="$TEST_ROOT/data/router.db"
if [[ ! -f "$LIVE_DB" ]]; then
  echo "Live ArrNexus database not found at $LIVE_DB" >&2
  exit 8
fi

# Use SQLite's online backup API so the production database can stay live.
echo "Creating consistent read snapshot of ArrNexus database..."
python3 - "$LIVE_DB" "$TEST_DB" <<'PY'
import sqlite3, sys
src_path, dst_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
dst = sqlite3.connect(dst_path)
with dst:
    src.backup(dst)
dst.close()
src.close()
PY
chmod 600 "$TEST_DB"

SESSION_SECRET="$(docker inspect arrnexus --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^ARRNEXUS_SESSION_SECRET=//p' | head -n1)"
if [[ -z "$SESSION_SECRET" ]]; then
  echo "Could not recover the current ArrNexus session secret; refusing to launch an ambiguous test login." >&2
  exit 9
fi

# The test image contains the feature branch, including MediaStack UI.
echo "Building $TEST_IMAGE from $BRANCH..."
docker build -t "$TEST_IMAGE" .

if docker inspect "$TEST_NAME" >/dev/null 2>&1; then
  echo "Removing previous test container $TEST_NAME..."
  docker rm -f "$TEST_NAME" >/dev/null
fi

# Do not run ArrNexus lifespan/background automation in the test copy.
# Uvicorn's --lifespan off means no request tracker, queue janitor, Magic Intake,
# list automation, or other production background worker is started.
echo "Starting isolated ArrNexus UI test on ${TEST_BIND}:${TEST_PORT}..."
docker run -d \
  --name "$TEST_NAME" \
  --restart no \
  -p "${TEST_BIND}:${TEST_PORT}:8000" \
  -e DB_PATH=/data/router.db \
  -e ARRNEXUS_SESSION_SECRET="$SESSION_SECRET" \
  -e ARRNEXUS_HTTPS_ONLY=false \
  -e MEDIASTACK_AGENT_URL="http://${AGENT_NAME}:8787" \
  -v "$TEST_ROOT/data:/data" \
  "$TEST_IMAGE" \
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --lifespan off >/dev/null

mapfile -t AGENT_NETWORKS < <(docker inspect "$AGENT_NAME" --format '{{range $name, $cfg := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' | sed '/^$/d' | sort -u)
for network in "${AGENT_NETWORKS[@]}"; do
  case "$network" in
    host|none|bridge) continue ;;
  esac
  docker network connect "$network" "$TEST_NAME" 2>/dev/null || true
done

if [[ ${#AGENT_NETWORKS[@]} -eq 0 ]]; then
  echo "No Stack Agent network found; stopping test container." >&2
  docker rm -f "$TEST_NAME" >/dev/null
  exit 10
fi

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${TEST_PORT}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -fsS "http://127.0.0.1:${TEST_PORT}/api/health" >/dev/null; then
  echo "Test ArrNexus did not become healthy. Recent logs:" >&2
  docker logs --tail 100 "$TEST_NAME" >&2 || true
  exit 11
fi

echo
echo "Testing Stack Agent connectivity from the UI-test container..."
if ! docker exec "$TEST_NAME" python - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://arrnexus-stack-agent:8787/health', timeout=5) as r:
    payload = json.load(r)
print(json.dumps(payload, indent=2))
assert payload.get('ok') is True
PY
then
  echo "Test container cannot reach the Stack Agent. Leaving production untouched." >&2
  docker rm -f "$TEST_NAME" >/dev/null
  exit 12
fi

echo
echo "MediaStack UI test is ready."
echo "Production ArrNexus was not stopped or recreated."
echo "Background ArrNexus workers are disabled in this test container."
if [[ "$TEST_BIND" == "127.0.0.1" ]]; then
  echo "Open through an SSH tunnel or locally: http://127.0.0.1:${TEST_PORT}/mediastack"
else
  echo "Open in a browser using this server's address: http://<server-ip>:${TEST_PORT}/mediastack"
fi
