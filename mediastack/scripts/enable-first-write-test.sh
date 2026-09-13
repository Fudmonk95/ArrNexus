#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/arrnexus-mediastack-src}"
STACK_DIR="$REPO_DIR/mediastack"
STATE_DIR="${MEDIASTACK_STATE_DIR:-/opt/arrnexus-mediastack/state}"
CONTROL_ENV="${MEDIASTACK_CONTROL_ENV:-$STATE_DIR/control.env}"
ALLOWLIST="${MEDIASTACK_WRITE_ALLOWLIST:-homarr}"
STABILIZATION_SECONDS="${MEDIASTACK_STABILIZATION_SECONDS:-30}"
HEALTH_TIMEOUT_SECONDS="${MEDIASTACK_HEALTH_TIMEOUT_SECONDS:-120}"

for cmd in docker openssl curl; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Required command is missing: $cmd" >&2; exit 1; }
done
[[ -d "$REPO_DIR/.git" ]] || { echo "ArrNexus checkout not found at $REPO_DIR" >&2; exit 2; }
cd "$REPO_DIR"
[[ "$(git branch --show-current)" == "feature/mediastack-v1-control-plane" ]] || {
  echo "Expected feature/mediastack-v1-control-plane; refusing to enable write mode on another branch." >&2
  exit 3
}

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"

TOKEN=""
if [[ -f "$CONTROL_ENV" ]]; then
  # shellcheck disable=SC1090
  source "$CONTROL_ENV"
  TOKEN="${MEDIASTACK_AGENT_TOKEN:-}"
fi
if [[ -z "$TOKEN" ]]; then
  TOKEN="$(openssl rand -hex 32)"
fi

umask 077
cat > "$CONTROL_ENV" <<EOF
MEDIASTACK_WRITE_ENABLED=true
MEDIASTACK_AGENT_TOKEN=$TOKEN
MEDIASTACK_WRITE_ALLOWLIST=$ALLOWLIST
MEDIASTACK_STABILIZATION_SECONDS=$STABILIZATION_SECONDS
MEDIASTACK_HEALTH_TIMEOUT_SECONDS=$HEALTH_TIMEOUT_SECONDS
EOF
chmod 600 "$CONTROL_ENV"

export MEDIASTACK_WRITE_ENABLED=true
export MEDIASTACK_AGENT_TOKEN="$TOKEN"
export MEDIASTACK_WRITE_ALLOWLIST="$ALLOWLIST"
export MEDIASTACK_STABILIZATION_SECONDS="$STABILIZATION_SECONDS"
export MEDIASTACK_HEALTH_TIMEOUT_SECONDS="$HEALTH_TIMEOUT_SECONDS"

echo "Rebuilding Stack Agent with first-write protection..."
echo "Allowed service(s): $ALLOWLIST"
echo "No-healthcheck stabilization: ${STABILIZATION_SECONDS}s"
echo "Health timeout: ${HEALTH_TIMEOUT_SECONDS}s"
docker compose -f "$STACK_DIR/docker-compose.monitor.yml" up -d --build --force-recreate stack-agent

# The recreated agent may lose the ad-hoc production ArrNexus network added by
# monitor mode, so restore the same shared networks without touching ArrNexus.
mapfile -t ARR_NETWORKS < <(docker inspect arrnexus --format '{{range $name, $cfg := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' | sed '/^$/d' | sort -u)
for network in "${ARR_NETWORKS[@]}"; do
  case "$network" in host|none|bridge) continue ;; esac
  if ! docker inspect arrnexus-stack-agent --format '{{json .NetworkSettings.Networks}}' | grep -Fq '"'"$network"'"'; then
    docker network connect "$network" arrnexus-stack-agent
  fi
done

for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8787/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -fsS http://127.0.0.1:8787/health >/dev/null

echo
echo "Agent capabilities:"
curl -fsS http://127.0.0.1:8787/api/capabilities | python3 -m json.tool

echo
echo "First-write mode is enabled, but only for: $ALLOWLIST"
echo "No media container was restarted, adopted or updated by this script."
echo "Credential stored at $CONTROL_ENV with mode 600."
echo "No-healthcheck containers must remain running for ${STABILIZATION_SECONDS}s before an update is accepted."
echo
echo "Next rebuild the isolated UI test; it will read the control credential automatically:"
echo "  TEST_BIND=192.168.137.10 TEST_PORT=8585 bash mediastack/scripts/start-ui-test.sh"
