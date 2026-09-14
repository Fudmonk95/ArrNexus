#!/usr/bin/env bash
set -euo pipefail

OUT="${1:-/home/renegademonk/homarr-recovery-report.txt}"
CURRENT="/mnt/appdata/DUMB-metadata/homarr"

mkdir -p "$(dirname "$OUT")"

{
  echo "ArrNexus Homarr recovery scan"
  echo "Generated: $(date -Is)"
  echo
  echo "================ CURRENT HOMARR ================="
  docker ps -a --filter 'name=homarr' --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null || true
  docker inspect homarr --format '{{range .Mounts}}{{printf "%s -> %s [%s]\n" .Source .Destination .Propagation}}{{end}}' 2>/dev/null || true
  echo

  echo "================ CURRENT APPDATA ================="
  if [[ -d "$CURRENT" ]]; then
    du -sh "$CURRENT" 2>/dev/null || true
    find "$CURRENT" -maxdepth 4 -type f -printf '%TY-%Tm-%Td %TH:%TM  %10s  %p\n' 2>/dev/null | sort | head -300 || true
  else
    echo "Current Homarr bind path does not exist: $CURRENT"
  fi
  echo

  echo "================ HOMARR SQLITE DATABASES ================="
  roots=(/mnt/appdata /home/renegademonk /opt /var/lib/docker/volumes)
  for root in "${roots[@]}"; do
    [[ -d "$root" ]] || continue
    find "$root" -xdev -type f \( -iname 'db.sqlite' -o -iname '*homarr*.sqlite' -o -iname '*homarr*.db' -o -iname 'database.sqlite' \) -printf '%TY-%Tm-%Td %TH:%TM  %10s  %p\n' 2>/dev/null || true
  done | sort -r
  echo

  echo "================ HOMARR DIRECTORIES ================="
  for root in "${roots[@]}"; do
    [[ -d "$root" ]] || continue
    find "$root" -xdev -type d -iname '*homarr*' -printf '%TY-%Tm-%Td %TH:%TM  %p\n' 2>/dev/null || true
  done | sort -r | head -300
  echo

  echo "================ LEGACY HOMARR CONFIG CANDIDATES ================="
  for root in /mnt/appdata /home/renegademonk /opt; do
    [[ -d "$root" ]] || continue
    find "$root" -xdev -type f \( -path '*/homarr/*' -o -path '*/Homarr/*' \) \( -iname '*.json' -o -iname '*.yaml' -o -iname '*.yml' -o -iname '*.zip' \) -printf '%TY-%Tm-%Td %TH:%TM  %10s  %p\n' 2>/dev/null || true
  done | sort -r | head -500
  echo

  echo "================ DOCKER VOLUMES ================="
  docker volume ls 2>/dev/null | grep -i homarr || true
  echo

  echo "================ PORTAINER STACK DATA ================="
  PORTAINER_ROOT=/var/lib/docker/volumes/portainer_data/_data
  if [[ -d "$PORTAINER_ROOT" ]]; then
    grep -RIl --exclude='*.db' --exclude='*.bin' -i 'homarr' "$PORTAINER_ROOT" 2>/dev/null | head -100 || true
  else
    echo "Portainer data volume path not found on host."
  fi
  echo

  echo "================ SQLITE QUICK INSPECTION ================="
  python3 - <<'PY'
import os, sqlite3
roots=['/mnt/appdata','/home/renegademonk','/opt','/var/lib/docker/volumes']
seen=set()
for root in roots:
    if not os.path.isdir(root):
        continue
    for base, dirs, files in os.walk(root):
        if base.count(os.sep)-root.count(os.sep) > 7:
            dirs[:] = []
            continue
        for fn in files:
            low=fn.lower()
            if low not in {'db.sqlite','database.sqlite'} and not ('homarr' in low and low.endswith(('.sqlite','.db'))):
                continue
            path=os.path.join(base,fn)
            if path in seen: continue
            seen.add(path)
            try:
                con=sqlite3.connect(f'file:{path}?mode=ro', uri=True)
                tables=[r[0] for r in con.execute("select name from sqlite_master where type='table' order by name")]
                interesting=[t for t in tables if any(x in t.lower() for x in ('board','app','integration','user','widget'))]
                print(f'\n{path}')
                print(f'  size={os.path.getsize(path)} bytes tables={len(tables)}')
                print('  interesting=' + ', '.join(interesting[:40]))
                for table in interesting[:15]:
                    try:
                        count=con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                        print(f'    {table}: {count}')
                    except Exception:
                        pass
                con.close()
            except Exception as exc:
                print(f'\n{path}\n  sqlite inspection failed: {exc}')
PY

  echo
  echo "================ NOTES ================="
  echo "This scan is read-only. Nothing was moved, restored, deleted or changed."
  echo "A useful Homarr v1+ recovery candidate normally contains appdata/db/db.sqlite."
  echo "Older pre-1.0 Homarr installations may instead contain JSON board/config files and need migration rather than a direct database restore."
} > "$OUT"

chmod 600 "$OUT" 2>/dev/null || true
if id renegademonk >/dev/null 2>&1; then chown renegademonk:renegademonk "$OUT" 2>/dev/null || true; fi

echo "Homarr recovery scan saved to: $OUT"
ls -lh "$OUT"
