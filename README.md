# DMM Arr Router v0.2

Web UI for reviewing media already present in Decypharr/Real-Debrid and linking it into the existing DUMB Radarr/Sonarr library trees.

## Important v0.2 change

On this DUMB host, `/mnt/debrid` is not mounted in Debian's normal mount namespace. It is visible inside Radarr/Sonarr/Lidarr and can be read from Docker through `/proc/<radarr-pid>/root/mnt/debrid` when the container uses the host PID namespace.

v0.2 therefore:

- runs with `pid: host`;
- adds only `SYS_PTRACE` rather than `privileged: true`;
- finds the current main Radarr PID automatically;
- reads/writes DUMB files through `/proc/<pid>/root/...`;
- creates symlinks whose stored targets remain normal `/mnt/debrid/...` paths so Radarr, Sonarr and Jellyfin can resolve them inside DUMB.

## Deploy

```bash
unzip dmm-arr-router-v0.2.zip
cd dmm-arr-router-v0.2
cp .env.example .env
nano .env
```

Fill in:

- `APP_PASSWORD`
- `SESSION_SECRET`
- Radarr API key
- Sonarr API key
- Lidarr API key
- Prowlarr API key

Then:

```bash
docker compose config
docker compose up -d --build
docker compose logs -f --tail=100
```

Open:

`http://<docker-host>:8484`

## Safety

The router does **not** move or delete anything from Decypharr `__all__`. It creates symlinks under the chosen Arr library path and asks the appropriate Arr to rescan.

Start with one known movie and verify the resulting symlink before doing anything in bulk.

## Destinations

Movies:

- Default
- Kids
- Christmas
- Halloween
- Easter

TV:

- Default
- Kids
- Netflix
- Disney+
- Amazon/Prime
- Apple TV+
- BBC

Music search goes through Lidarr and its existing NZB/indexer/download-client configuration. Decypharr is not used for music.
