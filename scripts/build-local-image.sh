#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m compileall -q app
docker build --pull -t arrnexus:v13.1.0 .
docker image inspect arrnexus:v13.1.0 --format '{{.RepoTags}}  {{.Id}}'
echo "The existing Portainer stack can now be updated to image: arrnexus:v13.1.0"
