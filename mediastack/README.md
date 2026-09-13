# ArrNexus MediaStack v0.1

MediaStack is the portable infrastructure layer for ArrNexus. It gives one deployment and one configuration root while keeping every application in its own container and mount namespace.

## Design rules

- One stack, **not** one giant container.
- Zurg itself owns the working rclone/FUSE mount. There is no second rclone container.
- Preserve the proven host mount namespace at `/zurg_mnt`.
- Zurg receives `/zurg_mnt` with `rshared` propagation; media consumers receive the same tree with `rslave` propagation.
- Persistent application state is migrated below one `STACK_ROOT` (default `/opt/arrnexus-mediastack`).
- The management agent is read-only in v0.1/v0.2: inventory, resource usage, mounts, logs, configuration viewing and image update checks only.
- No unattended container updates.
- Portainer stays outside MediaStack so there is always an independent recovery/control plane.

## Proven live-server layout

The current Debian 13 LXC uses Zurg image `ghcr.io/debridmediamanager/zurg:latest`. Zurg has `CAP_SYS_ADMIN`, `/dev/fuse`, `apparmor:unconfined`, `/opt/zurg:/config`, the rclone cache at `/mnt/appdata/zurg-rclone-cache`, and `/zurg_mnt` as `rshared`.

The working Zurg configuration has internal rclone enabled and mounts to `/zurg_mnt/zurg`. It also contains the currently used Real-Debrid/NZB provider configuration, Magic support and download-client category configuration. That live configuration is migration input only and must **never** be committed to Git.

Jellyfin is currently pinned to `ghcr.io/jellyfin/jellyfin:10.11.11`, runs as `1001:1001`, has supplementary groups `44` and `104`, and receives `/dev/dri` for hardware acceleration. MediaStack preserves those proven device/identity requirements while moving its persistent data toward the consolidated stack root.

## Core services

- Zurg
- Sonarr
- Radarr
- Lidarr
- Prowlarr
- SeerrNG
- Jellyfin
- ArrNexus
- ArrNexus Stack Agent

Optional `extras` profile services currently represented in the stack are Whisparr, Bazarr, NeutArr, Maintainerr, Profilarr, Profilarr Parser and Homarr. Portainer is intentionally excluded.

The live server also has a dedicated PostgreSQL 16 container used alongside Lidarr. Its exact database/user/secret wiring must be captured before the final migration stage; it is not being guessed in v0.1.

## Monitor/adoption mode — safe on the current server

`docker-compose.monitor.yml` runs **only** the Stack Agent. It does not recreate, stop or modify any current media application.

It watches the existing container names and mounts the known standalone configuration directories read-only, allowing MediaStack monitoring to be proved before migration.

```bash
cd mediastack
docker compose -f docker-compose.monitor.yml up -d --build
```

The monitor agent is bound to `127.0.0.1:8787` deliberately. It can expose logs and masked configuration data and should not be published directly to the LAN or Internet. The finished ArrNexus UI will proxy the agent over the internal Docker network.

Stop monitor mode with:

```bash
docker compose -f docker-compose.monitor.yml down
```

This removes only the Stack Agent container created by monitor mode.

## Full portable stack

The full stack is defined in `docker-compose.yml` and uses a consolidated configuration root.

```text
/opt/arrnexus-mediastack/
├── config/
│   ├── arrnexus/
│   ├── bazarr/
│   ├── homarr/
│   ├── jellyfin/
│   ├── lidarr/
│   ├── maintainerr/
│   ├── neutarr/
│   ├── profilarr/
│   ├── prowlarr/
│   ├── radarr/
│   ├── seerrng/
│   ├── sonarr/
│   ├── whisparr/
│   └── zurg/
├── cache/
│   └── jellyfin/
└── backups/

/zurg_mnt/
├── local/
└── zurg/               # FUSE mount created inside Zurg
    ├── __all__/
    ├── __downloads__/
    ├── __magic__/
    ├── __nzb__/
    ├── __realdebrid__/
    ├── __unplayable__/
    ├── movies/
    ├── music/
    └── shows/
```

The existing temporary `The Queens Nose` recovery bind and old `DUMB-metadata` paths are intentionally not part of the target architecture. They remain untouched until migration validation confirms they can be removed.

## Preflight/install safety

```bash
cd mediastack
cp .env.example .env
nano .env
sudo bash scripts/preflight.sh
sudo bash scripts/install.sh
```

The installer prepares/validates the filesystem and Compose configuration but **does not automatically replace the current production containers**. Migration/adoption will be a separate explicit operation after configuration backups and path checks succeed.

## Stack Agent API

- `GET /health` — Docker socket/agent health and watched container/config roots.
- `GET /api/system` — Docker host CPU count, total memory, Docker version/driver and container totals.
- `GET /api/containers` — managed service state, CPU, RAM, network I/O, image data, health, ports and mounts.
- `GET /api/updates` — compares local image digests with the configured remote tag where the registry permits it.
- `GET /api/configs` — lists supported files across consolidated or adoption-mode config roots.
- `GET /api/config?root=...&path=...` — read-only config viewer with common credentials masked.
- `GET /api/logs/{container}?tail=200` — read-only recent container logs for managed/watched containers.

The agent has `/var/run/docker.sock` mounted read-only because Docker's local API is the source of container stats. The API intentionally contains no restart, create, exec, pull, stop or delete endpoints yet.

## Recovered ArrNexus v13.4.0 source

The running `arrnexus:v13.4.0` container was exported and verified. Compared with the repository's v13.1.3 `main` baseline, the recovered build includes newer Magic Intake/backend/frontend work and the version bump. MediaStack integration must use that recovered v13.4.0 code as its application baseline so merging the stack cannot silently downgrade the live server.

## Next milestones

1. Restore/publish the recovered v13.4.0 application source to GitHub.
2. Proxy Stack Agent data through a new ArrNexus **MediaStack** page.
3. Capture Lidarr PostgreSQL settings and build its migration path.
4. Add backup and restore manifests.
5. Add guarded `backup -> pull/recreate -> health-check -> rollback` update operations.
6. Migrate existing standalone application configs only after monitor mode and backups have been validated.
