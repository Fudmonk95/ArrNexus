#!/usr/bin/env bash
set -euo pipefail

CONTAINERS=(
  zurg arrnexus sonarr radarr lidarr lidarr-postgres prowlarr seerrng jellyfin
  whisparr bazarr neutarr maintainerr profilarr profilarr-parser homarr
)

mask_env() {
  awk -F= '
    BEGIN { IGNORECASE=1 }
    {
      key=$1
      value=substr($0, length(key)+2)
      if (key ~ /(password|passwd|token|secret|api.?key|credential)/) value="********"
      print key "=" value
    }
  '
}

echo "ArrNexus MediaStack migration inventory"
echo "Generated: $(date -Is)"
echo "Host: $(hostname)"
echo

echo "================ Docker ================="
docker version --format 'Server {{.Server.Version}}' 2>/dev/null || true
docker info --format 'CPUs={{.NCPU}} Memory={{.MemTotal}} Driver={{.Driver}}' 2>/dev/null || true
printf '\n'

for name in "${CONTAINERS[@]}"; do
  if ! docker inspect "$name" >/dev/null 2>&1; then
    echo "================ $name ================"
    echo "NOT PRESENT"
    echo
    continue
  fi

  echo "================ $name ================"
  docker inspect "$name" --format 'Image: {{.Config.Image}}
State: {{.State.Status}}
Health: {{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}
User: {{.Config.User}}
RestartCount: {{.RestartCount}}
Entrypoint: {{json .Config.Entrypoint}}
Command: {{json .Config.Cmd}}
CapAdd: {{json .HostConfig.CapAdd}}
Devices: {{json .HostConfig.Devices}}
Groups: {{json .HostConfig.GroupAdd}}
SecurityOpt: {{json .HostConfig.SecurityOpt}}
Networks: {{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'

  echo "-- Mounts --"
  docker inspect "$name" --format '{{range .Mounts}}{{printf "%s -> %s type=%s rw=%t propagation=%s\n" .Source .Destination .Type .RW .Propagation}}{{end}}'

  echo "-- Environment (credentials masked) --"
  docker inspect "$name" --format '{{range .Config.Env}}{{println .}}{{end}}' | mask_env
  echo
 done

echo "================ Host mount propagation ================"
findmnt -T /zurg_mnt -o TARGET,SOURCE,FSTYPE,OPTIONS,PROPAGATION 2>/dev/null || true
findmnt -T /zurg_mnt/zurg -o TARGET,SOURCE,FSTYPE,OPTIONS,PROPAGATION 2>/dev/null || true

echo
echo "================ Key config locations ================"
for path in \
  /opt/zurg \
  /mnt/appdata/arrnexus/data \
  /home/renegademonk/docker/sonarr/config \
  /home/renegademonk/docker/radarr/config \
  /home/renegademonk/docker/lidarr/config \
  /home/renegademonk/docker/prowlarr/config \
  /home/renegademonk/docker/seerrng/config \
  /home/renegademonk/docker/jellyfin \
  /home/renegademonk/docker/whisparr/config \
  /home/renegademonk/docker/bazarr/config \
  /home/renegademonk/docker/neutarr/config \
  /home/renegademonk/docker/maintainerr/data \
  /home/renegademonk/docker/profilarr/config; do
  if [[ -e "$path" ]]; then
    du -sh "$path" 2>/dev/null | sed 's/^/PRESENT /'
  else
    echo "MISSING $path"
  fi
done

echo
echo "READ-ONLY INVENTORY COMPLETE"
echo "No container or file changes were made."
