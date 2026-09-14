#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/arrnexus-mediastack-src}"
STATE_DIR="${MEDIASTACK_STATE_DIR:-/opt/arrnexus-mediastack/state}"
CONTROL_ENV="${MEDIASTACK_CONTROL_ENV:-$STATE_DIR/control.env}"

# Services proven suitable for normal guarded/manual MediaStack updates.
# High-risk services intentionally remain excluded:
#   zurg, jellyfin, lidarr-postgres, arrnexus
DEFAULT_ALLOWLIST="homarr,bazarr,maintainerr,neutarr,profilarr,profilarr-parser,prowlarr,seerrng,radarr,sonarr,lidarr,whisparr"
ALLOWLIST="${MEDIASTACK_WRITE_ALLOWLIST:-$DEFAULT_ALLOWLIST}"
STABILIZATION_SECONDS="${MEDIASTACK_STABILIZATION_SECONDS:-30}"
HEALTH_TIMEOUT_SECONDS="${MEDIASTACK_HEALTH_TIMEOUT_SECONDS:-120}"

[[ -d "$REPO_DIR/.git" ]] || { echo "ArrNexus checkout not found at $REPO_DIR" >&2; exit 2; }
cd "$REPO_DIR"
[[ "$(git branch --show-current)" == "feature/mediastack-v1-control-plane" ]] || {
  echo "Expected feature/mediastack-v1-control-plane; refusing to change write policy on another branch." >&2
  exit 3
}
[[ -f "$CONTROL_ENV" ]] || {
  echo "Missing $CONTROL_ENV. Run enable-first-write-test.sh once first so the agent token already exists." >&2
  exit 4
}

# Preserve the existing secret token and only widen the explicit service allowlist.
# shellcheck disable=SC1090
source "$CONTROL_ENV"
TOKEN="${MEDIASTACK_AGENT_TOKEN:-}"
[[ -n "$TOKEN" ]] || { echo "MEDIASTACK_AGENT_TOKEN is missing from $CONTROL_ENV" >&2; exit 5; }

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

STACK_DIR="$REPO_DIR/mediastack"
echo "Rebuilding Stack Agent with standard guarded-update allowlist..."
echo "Allowed: $ALLOWLIST"
echo "Still blocked: zurg,jellyfin,lidarr-postgres,arrnexus"
docker compose -f "$STACK_DIR/docker-compose.monitor.yml" up -d --build --force-recreate stack-agent

mapfile -t ARR_NETWORKS < <(docker inspect arrnexus --format '{{range $name, $cfg := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' | sed '/^$/d' | sort -u)
for network in "${ARR_NETWORKS[@]}"; do
  case "$network" in host|none|bridge) continue ;; esac
  if ! docker inspect arrnexus-stack-agent --format '{{json .NetworkSettings.Networks}}' | grep -Fq '"'"$network"'"'; then
    docker network connect "$network" arrnexus-stack-agent
  fi
done

for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8787/health >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS http://127.0.0.1:8787/health >/dev/null

echo
echo "Agent capabilities:"
curl -fsS http://127.0.0.1:8787/api/capabilities | python3 -m json.tool

echo
echo "Normal services can now use guarded lifecycle/update actions."
echo "Zurg, Jellyfin, Lidarr PostgreSQL and ArrNexus remain blocked at the agent layer."
echo "No media container was restarted or updated by this script."
