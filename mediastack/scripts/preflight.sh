#!/usr/bin/env bash
set -uo pipefail

ZURG_MOUNT_ROOT="${ZURG_MOUNT_ROOT:-/zurg_mnt}"
STACK_ROOT="${STACK_ROOT:-/opt/arrnexus-mediastack}"
FAIL=0

ok()   { printf '  [OK]   %s\n' "$1"; }
warn() { printf '  [WARN] %s\n' "$1"; }
fail() { printf '  [FAIL] %s\n' "$1"; FAIL=1; }

printf '\nArrNexus MediaStack preflight\n=============================\n'
command -v docker >/dev/null 2>&1 && ok "Docker installed" || fail "Docker is not installed"
docker info >/dev/null 2>&1 && ok "Docker daemon reachable" || fail "Docker daemon is not reachable"
docker compose version >/dev/null 2>&1 && ok "Docker Compose plugin installed" || fail "Docker Compose plugin missing"
[[ -e /dev/fuse ]] && ok "/dev/fuse is available" || fail "/dev/fuse is missing"
[[ -e /dev/dri/renderD128 ]] && ok "Jellyfin render device available" || warn "/dev/dri/renderD128 is not available"
[[ -r /etc/fuse.conf ]] && ok "/etc/fuse.conf readable" || warn "/etc/fuse.conf not readable"

mkdir -p "$ZURG_MOUNT_ROOT/zurg" 2>/dev/null || fail "Cannot create $ZURG_MOUNT_ROOT/zurg"
mkdir -p "$STACK_ROOT" 2>/dev/null || fail "Cannot create $STACK_ROOT"

PROPAGATION="$(findmnt -T "$ZURG_MOUNT_ROOT" -o PROPAGATION -n 2>/dev/null || true)"
if [[ "$PROPAGATION" =~ shared ]]; then
  ok "$ZURG_MOUNT_ROOT mount propagation is shared"
else
  warn "$ZURG_MOUNT_ROOT is not currently shared at the host level; install.sh will bind it to itself and mark it rshared"
fi

if mountpoint -q "$ZURG_MOUNT_ROOT/zurg" && findmnt -T "$ZURG_MOUNT_ROOT/zurg" -t fuse.rclone >/dev/null 2>&1; then
  ok "Existing Zurg FUSE mount detected at $ZURG_MOUNT_ROOT/zurg"
else
  warn "No active fuse.rclone mount detected at $ZURG_MOUNT_ROOT/zurg"
fi

if [[ -S /var/run/docker.sock ]]; then
  ok "Docker socket present"
else
  fail "Docker socket missing"
fi

if (( FAIL )); then
  printf '\nPreflight FAILED. Fix the failed checks before deployment.\n'
  exit 1
fi
printf '\nPreflight passed. Warnings should be reviewed before production deployment.\n'
