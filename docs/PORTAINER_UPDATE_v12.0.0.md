# Updating ArrNexus to v12.0.0 in Portainer

This update reuses the existing ArrNexus database and data directory. Do not deploy a second clean ArrNexus instance.

## 1. Back up the current database

ArrNexus can create a database backup from **Settings -> Maintenance**. A host-level backup is also fine.

Current persistent directory:

```text
/mnt/appdata/arrnexus/data
```

Current database:

```text
/mnt/appdata/arrnexus/data/router.db
```

## 2. Build the image

Extract `ArrNexus-v12.0.0.zip` on the Debian server and run:

```bash
cd ArrNexus-v12.0.0
chmod +x scripts/*.sh
./scripts/build-local-image.sh
```

Expected image:

```text
arrnexus:v12.0.0
```

The image now contains ffmpeg/ffprobe for Queue Janitor media inspection.

## 3. Update the Portainer stack

Use the supplied `portainer-stack.yml`, or update the existing image line to:

```yaml
image: arrnexus:v12.0.0
```

Keep the existing data mount:

```yaml
- /mnt/appdata/arrnexus/data:/data
```

Keep Zurg read-only with propagation:

```yaml
- type: bind
  source: /zurg_mnt
  target: /zurg_mnt
  read_only: true
  bind:
    propagation: rslave
```

Keep cache visibility read-only:

```yaml
- /mnt/appdata/zurg-rclone-cache:/host/zurg-rclone-cache:ro
```

Keep:

```yaml
DB_PATH: /data/router.db
```

Deploy/update the stack. v12 creates the new recovery tables in the existing SQLite database automatically.

## 4. Verify

```bash
./scripts/verify-running.sh
```

Check:

```text
http://SERVER-IP:8484
```

The sidebar should include:

- Missing Media
- Queue Janitor

## 5. Start in dry-run

Both engines default to safe mode.

Recommended first pass:

1. Open **Missing Media** and click **Scan now**.
2. Keep **NeutArr owns automatic missing searches** enabled if NeutArr will continue doing missing-media cycles.
3. Open **Queue Janitor** and click **Scan only**.
4. Run a dry-run cycle and inspect classifications/actions.
5. Only then disable dry-run and enable the automatic Janitor if the live queue matches the rules you expect.

Stalled downloads are deferred to NeutArr/Swaparr by default.
