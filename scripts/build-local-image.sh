#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
echo "Building ArrNexus v12.0.0..."
docker build --pull -t arrnexus:v12.0.0 .
echo
echo "Built:"
docker image inspect arrnexus:v12.0.0 --format '{{.RepoTags}}  {{.Id}}'
echo
echo "The existing Portainer stack can now be updated to image: arrnexus:v12.0.0"
