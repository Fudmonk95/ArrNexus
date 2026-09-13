#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
STACK_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$STACK_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$STACK_DIR/.env.example" "$ENV_FILE"
  echo "Created $ENV_FILE. Review it before production use."
fi

set -a
source "$ENV_FILE"
set +a

STACK_ROOT="${STACK_ROOT:-/opt/arrnexus-mediastack}"
ZURG_MOUNT_ROOT="${ZURG_MOUNT_ROOT:-/zurg_mnt}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer with sudo/root so it can prepare FUSE mount propagation."
  exit 1
fi

export STACK_ROOT ZURG_MOUNT_ROOT
bash "$SCRIPT_DIR/preflight.sh"

mkdir -p \
  "$STACK_ROOT/config/arrnexus" \
  "$STACK_ROOT/config/zurg" \
  "$STACK_ROOT/config/sonarr" \
  "$STACK_ROOT/config/radarr" \
  "$STACK_ROOT/config/lidarr" \
  "$STACK_ROOT/config/prowlarr" \
  "$STACK_ROOT/config/seerrng" \
  "$STACK_ROOT/config/jellyfin" \
  "$STACK_ROOT/config/whisparr" \
  "$STACK_ROOT/config/bazarr" \
  "$STACK_ROOT/config/neutarr" \
  "$STACK_ROOT/config/maintainerr" \
  "$STACK_ROOT/config/profilarr" \
  "$STACK_ROOT/config/homarr" \
  "$STACK_ROOT/cache/jellyfin" \
  "$STACK_ROOT/backups" \
  "$ZURG_MOUNT_ROOT/zurg"

# Make the exact host tree already used by the working server persistent for
# mount propagation. If it is only a directory, bind it to itself first.
mount --make-rshared "$ZURG_MOUNT_ROOT" 2>/dev/null || {
  mount --bind "$ZURG_MOUNT_ROOT" "$ZURG_MOUNT_ROOT"
  mount --make-rshared "$ZURG_MOUNT_ROOT"
}

ZURG_CONFIG="$STACK_ROOT/config/zurg/config.yml"
if [[ ! -f "$ZURG_CONFIG" ]]; then
  cat > "$ZURG_CONFIG" <<'EOF'
# ArrNexus MediaStack placeholder.
# Replace this entire file with the known-good config from the live Zurg container.
token: REPLACE_ME
EOF
fi

if grep -q 'ARRNEXUS_SESSION_SECRET=change-me-before-production' "$ENV_FILE"; then
  SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  sed -i "s#ARRNEXUS_SESSION_SECRET=change-me-before-production#ARRNEXUS_SESSION_SECRET=$SECRET#" "$ENV_FILE"
fi

if grep -q '^token: REPLACE_ME' "$ZURG_CONFIG"; then
  echo
  echo "STOP: Put your known-good Zurg configuration into:"
  echo "  $ZURG_CONFIG"
  echo "Nothing has been stopped or replaced. The MediaStack has NOT been started."
  exit 2
fi

if ! docker image inspect "${ARRNEXUS_IMAGE:-arrnexus:v13.4.0}" >/dev/null 2>&1; then
  echo
  echo "STOP: ArrNexus image ${ARRNEXUS_IMAGE:-arrnexus:v13.4.0} is not available locally."
  echo "v13.4.0 source/image publishing must be synchronised before this stack is portable."
  exit 3
fi

cd "$STACK_DIR"
docker compose --env-file "$ENV_FILE" config >/dev/null

echo
cat <<'EOF'
MediaStack configuration validated.

This installer does not automatically replace an existing production stack.
Migration/adoption must be run explicitly after configs have been copied and verified.
EOF
