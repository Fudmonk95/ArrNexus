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
MEDIA_ROOT="${MEDIA_ROOT:-/mnt/arrnexus}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer with sudo/root so it can prepare FUSE mount propagation."
  exit 1
fi

export STACK_ROOT MEDIA_ROOT
"$SCRIPT_DIR/preflight.sh"

mkdir -p \
  "$STACK_ROOT/config/arrnexus" \
  "$STACK_ROOT/config/zurg/data" \
  "$STACK_ROOT/config/rclone" \
  "$STACK_ROOT/config/sonarr" \
  "$STACK_ROOT/config/radarr" \
  "$STACK_ROOT/config/lidarr" \
  "$STACK_ROOT/config/prowlarr" \
  "$STACK_ROOT/config/seerr" \
  "$STACK_ROOT/config/jellyfin" \
  "$STACK_ROOT/cache/jellyfin" \
  "$STACK_ROOT/backups" \
  "$MEDIA_ROOT/zurg"

if ! grep -Eq '^\s*user_allow_other\s*$' /etc/fuse.conf 2>/dev/null; then
  printf '\nuser_allow_other\n' >> /etc/fuse.conf
fi

mount --make-rshared "$MEDIA_ROOT" 2>/dev/null || {
  mount --bind "$MEDIA_ROOT" "$MEDIA_ROOT"
  mount --make-rshared "$MEDIA_ROOT"
}

RCLONE_CONF="$STACK_ROOT/config/rclone/rclone.conf"
if [[ ! -f "$RCLONE_CONF" ]]; then
  cat > "$RCLONE_CONF" <<'EOF'
[zurg]
type = webdav
url = http://zurg:9999/dav
vendor = other
pacer_min_sleep = 0
EOF
fi

ZURG_CONFIG="$STACK_ROOT/config/zurg/config.yml"
if [[ ! -f "$ZURG_CONFIG" ]]; then
  cat > "$ZURG_CONFIG" <<'EOF'
# ArrNexus MediaStack placeholder. Replace this with your known-good Zurg config.
token: REPLACE_ME
EOF
fi

if grep -q 'ARRNEXUS_SESSION_SECRET=change-me-before-production' "$ENV_FILE"; then
  SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  sed -i "s#ARRNEXUS_SESSION_SECRET=change-me-before-production#ARRNEXUS_SESSION_SECRET=$SECRET#" "$ENV_FILE"
fi

if grep -q '^token: REPLACE_ME' "$ZURG_CONFIG"; then
  echo
  echo "STOP: Put your working Zurg configuration into:"
  echo "  $ZURG_CONFIG"
  echo "The stack has NOT been started yet."
  exit 2
fi

cd "$STACK_DIR"
docker compose --env-file "$ENV_FILE" config >/dev/null
docker compose --env-file "$ENV_FILE" pull --ignore-buildable
docker compose --env-file "$ENV_FILE" build stack-agent arrnexus
docker compose --env-file "$ENV_FILE" up -d

echo
echo "ArrNexus MediaStack started."
echo "ArrNexus:    http://SERVER:${ARRNEXUS_PORT:-8484}"
echo "Stack Agent: http://SERVER:${STACK_AGENT_PORT:-8787}/health"
