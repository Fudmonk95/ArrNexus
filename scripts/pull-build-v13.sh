#!/usr/bin/env bash
set -euo pipefail

REPO="https://github.com/Fudmonk95/ArrNexus.git"
TAG="v13.0.0"
IMAGE="arrnexus:v13.0.0"
DEST="/opt/arrnexus-v13-src"

echo "======================================================================"
echo " ARRNEXUS v13.0.0 - PULL + BUILD"
echo "======================================================================"

command -v git >/dev/null || { echo "ERROR: git is not installed"; exit 1; }
command -v docker >/dev/null || { echo "ERROR: docker is not installed"; exit 1; }

rm -rf "$DEST"
git clone --depth 1 --branch "$TAG" "$REPO" "$DEST"
cd "$DEST"

VERSION="$(tr -d '\r\n ' < VERSION)"
if [ "$VERSION" != "13.0.0" ]; then
  echo "ERROR: Expected VERSION 13.0.0, got: $VERSION"
  exit 1
fi

python3 -m compileall -q app

docker build --pull -t "$IMAGE" .

echo
echo "Built image:"
docker image inspect "$IMAGE" --format '  {{.RepoTags}} -> {{.Id}}'

echo
echo "Portainer stack file is ready at:"
echo "  $DEST/portainer-stack.yml"
echo
echo "Existing ArrNexus data is NOT changed:"
echo "  /mnt/appdata/arrnexus/data/router.db"
echo
echo "Next: update/redeploy the ArrNexus Portainer stack using image: $IMAGE"
