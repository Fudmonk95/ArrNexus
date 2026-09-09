# Portainer update — ArrNexus v13.1.3

v13.1.3 is an in-place application update from v13.0.0.

## Existing persistent data

Keep the existing database bind exactly as-is:

```text
/mnt/appdata/arrnexus/data:/data
```

ArrNexus continues to use:

```text
DB_PATH=/data/router.db
```

## Existing Zurg mounts

Keep the existing v13 mounts:

```text
/mnt/appdata/zurg-rclone-cache:/host/zurg-rclone-cache:ro
/zurg_mnt -> /zurg_mnt read-only, rslave
/zurg_mnt/zurg/__magic__ -> /zurg_magic read-write, rslave
```

The main Zurg tree remains read-only. Magic Intake only receives write access to `__magic__`.

## Update

Build the new image on Debian:

```bash
./scripts/pull-build-v13.1.3.sh
```

Then change the Portainer stack image to:

```yaml
image: arrnexus:v13.1.3
```

No database reset is required. v13.1.3 migrates the v13 Magic Intake tables in place and preserves existing Force Matches as per-release overrides.

## Verify

```bash
docker ps --filter name=arrnexus
docker logs --tail 100 arrnexus
curl -s http://127.0.0.1:8484/api/health | python3 -m json.tool
```

Expected version:

```json
"version": "13.1.3"
```


## v13.1.3 note

Magic Intake limits active imports to 3 by default. Optionally set `MAGIC_IMPORT_CONCURRENCY` between 1 and 6 in the stack. No new mounts are required.
