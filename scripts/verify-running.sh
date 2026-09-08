#!/usr/bin/env bash
set -euo pipefail
echo "=== Container ==="
docker ps --filter name=arrnexus
echo
echo "=== Health API ==="
curl --max-time 8 -fsS http://127.0.0.1:8484/api/health
echo
echo "=== Data mount ==="
docker inspect arrnexus --format '{{range .Mounts}}{{println .Source "->" .Destination "rw=" .RW "propagation=" .Propagation}}{{end}}'
echo
echo "=== Version ==="
docker exec arrnexus sh -c 'cat /app/VERSION 2>/dev/null || python -c "from app.main import APP_VERSION; print(APP_VERSION)"'
echo
echo "=== Recent logs ==="
docker logs --tail 80 arrnexus
