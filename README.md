# DMM Arr Router v1.0 — Media Control Hub

A self-hosted control layer for DUMB-style media stacks. It reads Decypharr's Real-Debrid library through the live Arr mount namespace, routes content to specialist Radarr/Sonarr instances, creates safe symlinks, manages Lidarr music discovery/search, and provides maintenance/queue/history tools in one web UI.

## Main features

- Poster-based DMM inbox with search/filter and imported/linked/ignored badges
- Multi-select bulk import with a live progress/job page
- Auto-discovery of DUMB Radarr/Sonarr/Lidarr processes and specialist instances
- Automatic routing to default/Kids/Christmas/Halloween/Easter and Netflix/Disney+/Amazon/Apple/BBC libraries
- Learned routing from manual overrides plus editable rules
- Duplicate detection, source-vs-library resolution comparison and upgrade badge
- Imported items stay visible and receive an **Imported** badge
- Safe undo removes only symlinks created by this Router; it never removes Real-Debrid source media
- Broken symlink scanner and conservative exact-filename repair
- Orphan/unowned DMM source detector
- Unified Radarr/Sonarr/Lidarr queue
- Provider counts/badges for Real-Debrid and Usenet symlinks
- Recent activity timeline
- Optional Jellyfin presence badges (requires Jellyfin API key)
- Dark/light theme, poster-grid/table modes, responsive mobile layout
- Discover page: movie/TV lookup -> route to specialist Arr -> queue Arr search. This uses the user's existing Prowlarr + Decypharr/InfiniDysk setup and does not require a second Real-Debrid login.
- Music Hub using **MusicBrainz** and **ListenBrainz** public endpoints; no Spotify/Apple/Amazon account connection required
- Add artists to Lidarr and queue artist/album Usenet searches
- Optional external search links for Spotify, Apple Music, Amazon Music and Beatport
- Optional iTunes catalog enrichment is available but disabled by default

## Why `pid: host` is required

In DUMB, `/mnt/debrid` is mounted inside the Arr mount namespace, not the normal Debian namespace. This app discovers the live Radarr PID and reads the DUMB filesystem through:

`/proc/<radarr-pid>/root/mnt/debrid`

Symlinks are still written with normal DUMB-visible targets such as `/mnt/debrid/decypharr/__all__/...`, never with `/proc/<pid>/...` paths.

The container uses `SYS_PTRACE` instead of `privileged: true`.

## Upgrade from v0.2

1. Keep your existing `.env` and `data/router.db` safe.
2. Extract v1.0 to a new directory.
3. Copy your existing `.env` into the v1.0 directory.
4. Add these optional variables if wanted:

```env
JELLYFIN_URL=http://192.168.137.10:8096
JELLYFIN_API_KEY=
ENABLE_ITUNES_SEARCH=false
```

5. Copy the old `data/router.db` into the new `data/` directory. v1.0 migrates the v0.2 imports table automatically and preserves history.
6. Rebuild:

```bash
docker compose down
docker compose up -d --build
docker compose logs --tail=100 dmm-arr-router
```

Open `http://<docker-host>:8484`.

## Fresh install

```bash
cp .env.example .env
nano .env
docker compose up -d --build
```

Required API keys:

- Radarr
- Sonarr
- Lidarr
- Prowlarr

Jellyfin is optional.

## Music discovery without user accounts

The default Music Hub deliberately avoids requiring personal Spotify, Apple, Amazon or Beatport credentials:

- **MusicBrainz** — public artist/release metadata
- **Cover Art Archive** — release-group cover images where available
- **ListenBrainz** — public sitewide trending statistics
- **Lidarr** — actual add/monitor/search/download workflow through the user's own NZB indexers

External Spotify/Apple/Amazon/Beatport buttons are just search links. No account tokens are stored by this app.

## Safety behaviour

- Source content under Decypharr `__all__` is never moved or deleted.
- Undo only removes symlinks recorded as created/verified by this Router.
- Broken-link repair only acts when exactly one DMM source file has the same filename.
- Existing Arr ownership wins over a new routing suggestion to avoid duplicate movies/series across specialist instances.
- Bulk jobs continue after a per-item failure and show the error on the progress page.

## Optional Jellyfin key

Create a Jellyfin API key and set `JELLYFIN_API_KEY` if you want exact `Detected in Jellyfin` badges. All import/routing features work without it.
