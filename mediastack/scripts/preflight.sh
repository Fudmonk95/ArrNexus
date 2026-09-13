#!/usr/bin/env bash
set -uo pipefail

MEDIA_ROOT="${MEDIA_ROOT:-/mnt/arrnexus}"
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
[[ -r /etc/fuse.conf ]] && ok "/etc/fuse.conf readable" || warn "/etc/fuse.conf not readable"
if grep -Eq '^\s*user_allow_other\s*$' /etc/fuse.conf 2>/dev/null; then
  ok "FUSE allow-other enabled"
else
  warn "user_allow_other is not enabled in /etc/fuse.conf"
fi

mkdir -p "$MEDIA_ROOT/zurg" 2>/dev/null || fail "Cannot create $MEDIA_ROOT/zurg"
mkdir -p "$STACK_ROOT" 2>/dev/null || fail "Cannot create $STACK_ROOT"

if findmnt -T "$MEDIA_ROOT" -o PROPAGATION -n 2>/dev/null | grep -Eq 'shared|rshared'; then
  ok "$MEDIA_ROOT mount propagation is shared"
else
  warn "$MEDIA_ROOT is not currently shared; install.sh can set it rshared"
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
