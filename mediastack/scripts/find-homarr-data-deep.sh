#!/usr/bin/env bash
set -euo pipefail

OUT="${1:-/home/renegademonk/homarr-deep-scan.txt}"
OLD_DIR="/home/renegademonk/docker/homarr"

mkdir -p "$(dirname "$OUT")"

{
  echo "ArrNexus Homarr deep recovery scan"
  echo "Generated: $(date -Is)"
  echo

  echo "============================================================"
  echo "OLD HOMARR DIRECTORY"
  echo "============================================================"
  if [[ -d "$OLD_DIR" ]]; then
    ls -lahR "$OLD_DIR" 2>/dev/null || true
  else
    echo "Old Homarr directory not found: $OLD_DIR"
  fi
  echo

  echo "============================================================"
  echo "ALL FILES IN OLD HOMARR DIRECTORY"
  echo "============================================================"
  if [[ -d "$OLD_DIR" ]]; then
    find "$OLD_DIR" -type f -printf '%TY-%Tm-%Td %TH:%TM  %10s  %p\n' 2>/dev/null | sort || true
  fi
  echo

  echo "============================================================"
  echo "HISTORICAL HOMARR COMPOSE / STACK DEFINITIONS"
  echo "============================================================"
  roots=(/home/renegademonk /mnt/appdata /opt /var/lib/docker/volumes/portainer_data/_data/compose)
  for root in "${roots[@]}"; do
    [[ -d "$root" ]] || continue
    find "$root" -xdev -type f \
      \( -iname 'docker-compose.yml' -o -iname 'docker-compose.yaml' -o -iname 'compose.yml' -o -iname 'compose.yaml' -o -iname '*.yml' -o -iname '*.yaml' \) \
      -print0 2>/dev/null || true
  done | while IFS= read -r -d '' f; do
    if grep -qi 'homarr' "$f" 2>/dev/null; then
      echo
      echo "---------- $f ----------"
      grep -ni -B10 -A40 'homarr' "$f" 2>/dev/null | head -180 || true
    fi
  done
  echo

  echo "============================================================"
  echo "HOMARR-LIKE DATABASE / CONFIG / BACKUP FILES"
  echo "============================================================"
  for root in /home/renegademonk /mnt/appdata /opt /var/lib/docker/volumes; do
    [[ -d "$root" ]] || continue
    find "$root" -xdev -type f \
      \( -iname '*.sqlite' -o -iname '*.sqlite3' -o -iname '*.db' -o -iname '*.json' -o -iname '*.zip' -o -iname '*.tar' -o -iname '*.tar.gz' -o -iname '*.tgz' -o -iname '*.bak' -o -iname '*.backup' \) \
      -printf '%TY-%Tm-%Td %TH:%TM  %10s  %p\n' 2>/dev/null || true
  done | grep -Ei 'homarr|dashboard|board|config' | sort -r || true
  echo

  echo "============================================================"
  echo "DIRECTORY NAMES THAT MAY CONTAIN OLD HOMARR DATA"
  echo "============================================================"
  for root in /home/renegademonk /mnt/appdata /opt /var/lib/docker/volumes; do
    [[ -d "$root" ]] || continue
    find "$root" -xdev -type d \
      \( -iname '*homarr*' -o -iname '*dashboard*' -o -iname '*board*' \) \
      -printf '%TY-%Tm-%Td %TH:%TM  %p\n' 2>/dev/null || true
  done | sort -r | head -500
  echo

  echo "============================================================"
  echo "PORTAINER / DOCKER HISTORY CLUES"
  echo "============================================================"
  docker ps -a --no-trunc --format '{{.Names}}|{{.Image}}|{{.CreatedAt}}|{{.Status}}' 2>/dev/null | grep -i homarr || true
  echo
  docker volume ls 2>/dev/null | grep -Ei 'homarr|dashboard' || true
  echo
  docker image ls --no-trunc --format '{{.Repository}}:{{.Tag}}|{{.ID}}|{{.CreatedSince}}' 2>/dev/null | grep -i homarr || true
  echo

  echo "============================================================"
  echo "KNOWN HOMARR PATH STRINGS IN TEXT CONFIGS"
  echo "============================================================"
  for root in /home/renegademonk /mnt/appdata /opt /var/lib/docker/volumes/portainer_data/_data/compose; do
    [[ -d "$root" ]] || continue
    grep -RInsI --exclude='*.sqlite' --exclude='*.db' --exclude='*.rdb' \
      -E 'homarr|/appdata|/app/data/configs|/data/configs|SECRET_ENCRYPTION_KEY' "$root" 2>/dev/null | head -1200 || true
  done
  echo

  echo "============================================================"
  echo "SQLITE CANDIDATE INSPECTION"
  echo "============================================================"
  python3 - <<'PY'
import os, sqlite3
roots=['/home/renegademonk','/mnt/appdata','/opt','/var/lib/docker/volumes']
seen=set()
keywords=('board','app','integration','user','widget','layout')
for root in roots:
    if not os.path.isdir(root):
        continue
    for base, dirs, files in os.walk(root):
        # Avoid walking arbitrarily deep through cache/vendor trees.
        if base.count(os.sep)-root.count(os.sep) > 10:
            dirs[:] = []
            continue
        for fn in files:
            low=fn.lower()
            if not low.endswith(('.sqlite','.sqlite3','.db')):
                continue
            path=os.path.join(base,fn)
            if path in seen:
                continue
            seen.add(path)
            try:
                con=sqlite3.connect(f'file:{path}?mode=ro', uri=True)
                tables=[r[0] for r in con.execute("select name from sqlite_master where type='table' order by name")]
                interesting=[t for t in tables if any(k in t.lower() for k in keywords)]
                if not interesting and 'homarr' not in path.lower() and 'dashboard' not in path.lower():
                    con.close(); continue
                print(f'\n{path}')
                print(f'  size={os.path.getsize(path)} bytes tables={len(tables)}')
                print('  interesting=' + ', '.join(interesting[:80]))
                for table in interesting[:30]:
                    try:
                        count=con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                        print(f'    {table}: {count}')
                    except Exception:
                        pass
                con.close()
            except Exception:
                pass
PY
  echo

  echo "============================================================"
  echo "NOTES"
  echo "============================================================"
  echo "Read-only scan. Nothing was moved, restored, deleted or modified."
  echo "Current Homarr v1+ normally uses /appdata/db/db.sqlite."
  echo "Older Homarr releases may use JSON board files under paths mounted to /app/data/configs or /data/configs."
  echo "Historical Compose/Portainer definitions are especially useful because they reveal the old host bind path even if the data itself was moved later."
} > "$OUT"

chmod 600 "$OUT" 2>/dev/null || true
if id renegademonk >/dev/null 2>&1; then
  chown renegademonk:renegademonk "$OUT" 2>/dev/null || true
fi

echo "Homarr deep recovery scan saved to: $OUT"
ls -lh "$OUT"
