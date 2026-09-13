# ArrNexus MediaStack v0.1

MediaStack is the portable infrastructure layer for ArrNexus. It gives one deployment and one configuration root while keeping every application in its own container and mount namespace.

## Design rules

- One stack, **not** one giant container.
- Zurg/rclone owns the FUSE mount; consumers receive it with `rslave` propagation.
- Persistent application state lives below one `STACK_ROOT` (default `/opt/arrnexus-mediastack`).
- Media is exposed below one `MEDIA_ROOT` (default `/mnt/arrnexus`).
- The management agent is read-only in v0.1: inventory, resource usage, mounts, configuration viewing and image update checks only.
- No automatic container updates in v0.1.

## Services

`zurg`, `zurg-rclone`, `sonarr`, `radarr`, `lidarr`, `prowlarr`, `seerr`, `jellyfin`, `arrnexus`, and `arrnexus-stack-agent`.

## Safe first deployment

Do **not** point this at your current production config folders for the first test. This branch is intended to be deployed alongside the working stack with alternate ports or on a test host.

```bash
cd mediastack
cp .env.example .env
nano .env
sudo bash scripts/preflight.sh
sudo bash scripts/install.sh
```

The installer intentionally stops before starting containers if the generated Zurg config still contains `REPLACE_ME`. Copy your known-good Zurg config into `${STACK_ROOT}/config/zurg/config.yml`, then rerun `install.sh`.

## Stack Agent API

- `GET /health` — Docker socket/agent health.
- `GET /api/containers` — stack service state, CPU, RAM, network I/O, image data, health and mounts.
- `GET /api/updates` — compares locally installed image digests with the configured remote tag where the registry permits it.
- `GET /api/configs` — lists supported config files below the stack config root.
- `GET /api/config?path=...` — reads a config file and masks common API key/token/password/secret assignments.

The agent has `/var/run/docker.sock` mounted because Docker's local API is the source of container stats. The v0.1 code exposes GET-only operations and contains no restart, create, exec, pull or delete endpoints. A later release should place write operations behind an explicit allow-list and confirmation/backup flow.

## Filesystem layout

```text
/opt/arrnexus-mediastack/
├── config/
│   ├── arrnexus/
│   ├── jellyfin/
│   ├── lidarr/
│   ├── prowlarr/
│   ├── radarr/
│   ├── rclone/
│   ├── seerr/
│   ├── sonarr/
│   └── zurg/
├── cache/
│   └── jellyfin/
└── backups/

/mnt/arrnexus/
└── zurg/
    ├── __all__/
    ├── __downloads__/
    ├── __magic__/
    ├── movies/
    └── shows/
```

## Why this avoids the DUMB mount problem

`zurg-rclone` receives `/mnt/arrnexus/zurg` as an `rshared` bind. Sonarr, Radarr, Lidarr, Jellyfin and ArrNexus receive the same host path with `rslave` propagation. They therefore see the FUSE mount created by rclone without sharing one application container or using a second hidden filesystem view.

## Next milestone

v0.2 will wire the Stack Agent into the ArrNexus web UI and add guarded operations: backup, restart, pull/recreate, post-update health verification, and rollback to the previous image digest.
