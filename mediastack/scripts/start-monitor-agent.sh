#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
STACK_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$STACK_DIR"

for cmd in docker curl; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Required command is missing: $cmd" >&2
    exit 1
  fi
done

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not available to this shell." >&2
  exit 2
fi

if ! docker inspect arrnexus >/dev/null 2>&1; then
  echo "Existing ArrNexus container was not found; refusing live adoption mode." >&2
  exit 3
fi

echo "Building read-only MediaStack Stack Agent..."
docker compose -f docker-compose.monitor.yml build stack-agent

echo "Starting Stack Agent only..."
docker compose -f docker-compose.monitor.yml up -d stack-agent

mapfile -t ARR_NETWORKS < <(docker inspect arrnexus --format '{{range $name, $cfg := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' | sed '/^$/d' | sort -u)

if [[ ${#ARR_NETWORKS[@]} -eq 0 ]]; then
  echo "WARNING: ArrNexus has no attachable Docker network; host health check will still run." >&2
else
  echo "Attaching Stack Agent to ArrNexus network(s): ${ARR_NETWORKS[*]}"
  for network in "${ARR_NETWORKS[@]}"; do
    case "$network" in
      host|none) continue ;;
    esac
    if docker inspect arrnexus-stack-agent --format '{{json .NetworkSettings.Networks}}' | grep -Fq '"'"$network"'"'; then
      continue
    fi
    docker network connect "$network" arrnexus-stack-agent
  done
fi

echo
echo "Host health check:"
curl -fsS http://127.0.0.1:8787/health
echo

echo
echo "Testing agent DNS/reachability from the existing ArrNexus container..."
if docker exec arrnexus python - <<'PY'
import json
import urllib.request
with urllib.request.urlopen('http://arrnexus-stack-agent:8787/health', timeout=5) as r:
    print(json.dumps(json.load(r), indent=2))
PY
then
  echo "ArrNexus -> Stack Agent connectivity: OK"
else
  echo "WARNING: Host agent works but the current ArrNexus container cannot reach it by name yet." >&2
  echo "Do not migrate anything. Send this output back so the Docker network can be corrected." >&2
  exit 4
fi

echo
echo "Monitor-only Stack Agent is ready. No media application container was recreated or stopped."
