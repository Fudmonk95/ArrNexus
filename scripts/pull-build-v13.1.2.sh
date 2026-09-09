#!/usr/bin/env bash
set -euo pipefail

REPO="https://github.com/Fudmonk95/ArrNexus.git"
TAG="v13.1.2"
IMAGE="arrnexus:v13.1.2"
DEST="/opt/arrnexus-v13.1-src"

printf '%s\n' "======================================================================" " ARRNEXUS v13.1.2 - PULL + BUILD" "======================================================================"

command -v git >/dev/null || { echo "ERROR: git is not installed"; exit 1; }
command -v docker >/dev/null || { echo "ERROR: docker is not installed"; exit 1; }

# Always leave the source directory before replacing it. This avoids the
# v13.0 helper bug where running the script from inside DEST deleted the
# shell's current working directory before git clone.
cd /root
rm -rf "$DEST"
git clone --depth 1 --branch "$TAG" "$REPO" "$DEST"
cd "$DEST"

VERSION="$(tr -d '\r\n ' < VERSION)"
if [ "$VERSION" != "13.1.2" ]; then
  echo "ERROR: Expected VERSION 13.1.2, got: $VERSION"
  exit 1
fi

echo "PASS: Pulled ArrNexus $VERSION"
python3 -m compileall -q app

echo "Building Docker image: $IMAGE"
docker build --pull -t "$IMAGE" .

echo
echo "Built image:"
docker image inspect "$IMAGE" --format '  {{.RepoTags}} -> {{.Id}}'

echo
echo "Portainer stack file:"
echo "  $DEST/portainer-stack.yml"
echo
echo "Existing ArrNexus data is unchanged:"
echo "  /mnt/appdata/arrnexus/data/router.db"
echo
echo "Next: update/redeploy the Portainer stack using image: $IMAGE"
