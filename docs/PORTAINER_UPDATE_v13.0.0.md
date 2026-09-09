# Updating ArrNexus to v13.0.0 in Portainer

v13 keeps the existing database and replaces only the application image.

## Persistent data

Keep:

```text
/mnt/appdata/arrnexus/data:/data
DB_PATH=/data/router.db
```

## Pull and build directly on Debian

After the v13 GitHub release/tag is published:

```bash
rm -rf /opt/arrnexus-v13-src
git clone --depth 1 --branch v13.0.0 https://github.com/Fudmonk95/ArrNexus.git /opt/arrnexus-v13-src
cd /opt/arrnexus-v13-src
chmod +x scripts/pull-build-v13.sh
./scripts/pull-build-v13.sh
```

Or run the standalone `pull-build-arrnexus-v13.sh` supplied with the release workflow.

## Portainer image

```yaml
image: arrnexus:v13.0.0
```

## v13 Magic Intake mount

The main Zurg mount remains read-only. v13 adds a second dedicated writable mount for `__magic__` only:

```yaml
volumes:
  - /mnt/appdata/arrnexus/data:/data
  - /mnt/appdata/zurg-rclone-cache:/host/zurg-rclone-cache:ro
  - /zurg_mnt/zurg/__magic__:/zurg_magic:rw
  - type: bind
    source: /zurg_mnt
    target: /zurg_mnt
    read_only: true
    bind:
      propagation: rslave
```

Environment:

```yaml
MAGIC_ROOT: /zurg_magic
MAGIC_ARR_PREFIX: /zurg_mnt/zurg/__magic__
```

## Standalone service addresses

The supplied stack defaults to the current host at `192.168.137.10`:

```yaml
ZURG_URL: http://192.168.137.10:9999
RADARR_URL: http://192.168.137.10:7878
SONARR_URL: http://192.168.137.10:8989
LIDARR_URL: http://192.168.137.10:8686
PROWLARR_URL: http://192.168.137.10:9696
JELLYFIN_URL: http://192.168.137.10:8096
SEERR_URL: http://192.168.137.10:5055
```

After redeploying:

```bash
docker ps --filter name=arrnexus
docker logs --tail 100 arrnexus
curl http://127.0.0.1:8484/api/health
```

Then open ArrNexus and use the new **Magic Intake** page.
