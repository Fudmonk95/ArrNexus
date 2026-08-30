# ArrNexus v4.0

ArrNexus is a self-hosted media-control layer for DUMB-style Radarr/Sonarr/Lidarr stacks using InfiniDysk/NzbDAV, Decypharr/Real-Debrid, Prowlarr, Jellyfin and optional Seerr discovery.

It is designed for **Portainer/Docker Compose first** deployment: start the stack, open the browser, create the administrator and configure services in the UI. A normal installation does **not** require an `.env` file.

## What's new in v4

### Discover reliability + Seerr-style shelves

- Fixed the Discover template regression that could return an HTTP 500 when shelf dictionaries exposed an `items` key.
- Discover now isolates Seerr, Arr library and search failures instead of allowing one optional source to white-screen the page.
- Trending/popular Seerr shelves and every discovered specialist Radarr/Sonarr library render as horizontal poster rails.
- Search results retain request state and distinguish `Requested` from `In Real-Debrid`.
- Discover problems are written to the filterable ArrNexus logs and surfaced as source notices.

### Music source isolation

Provider tabs now use their **own provider data** instead of relabelling ListenBrainz highlights:

- ListenBrainz — public sitewide listening trends.
- Apple/iTunes — public catalogue search plus Apple public top-album feeds where available.
- Audius — open trending/search data.
- MusicBrainz — open metadata/search/recent release groups.
- **Deezer — public chart/catalogue discovery without a listener login.**
- Internet Archive — public audio discovery.
- Jamendo — optional developer client ID.
- SoundCloud — optional application credentials; no personal listener account linking.
- Spotify — optional app Client ID/Secret for public catalogue search; no user OAuth.
- Last.fm — optional application key.
- Amazon Music, Beatport, Bandcamp and Discogs — honest external catalogue launchers rather than fake recommendations.

If Lidarr is offline, the selected discovery provider still renders; only the `Add/Search via Lidarr` workflow is unavailable.

### Smart TV packs in Debrid / DMM

TV Prowlarr → Real-Debrid searches now understand:

- `Any`
- `Full series`
- `Season packs`
- `Episodes`

ArrNexus parses common `S01-S03`, `Season 1-3`, `S02`, `S02E03`, complete-series and season-pack naming patterns and displays a pack badge for each release.

Additional controls:

- quality filter (`2160p`, `1080p`, `720p`, any)
- `Cached RD only`
- duplicate hiding
- Real-Debrid cache badges
- pack-aware release scoring
- separate size ceilings for movies, episodes, season packs and full-series packs

For an existing Sonarr series, ArrNexus displays a **season coverage strip** showing complete / partial / missing seasons and provides:

- **Get entire show intelligently** — prefer one acceptable complete-series pack, otherwise assemble the best season-pack combination.
- **Get missing seasons only** — use Sonarr's season statistics and add only the missing/incomplete season packs.

### Problem Centre

A new Problem Centre turns operational state into actionable issues:

- DUMB namespace health
- Radarr/Sonarr/Lidarr/Prowlarr connectivity
- broken symlinks
- recent failed import jobs
- recent error log events
- simple library-health score

This complements the existing Maintenance page and filtered Logs view.

## Existing standout features retained

- First-run administrator setup in the browser.
- UI-managed Radarr, Sonarr, Lidarr, Prowlarr, Jellyfin and Seerr connections/API keys.
- Automatic DUMB Arr process discovery through the host PID namespace.
- DMM/Decypharr inbox scanning through `/proc/<arr-pid>/root/mnt/debrid`.
- Clean metadata titles, posters, genres, duplicate grouping and quality comparisons.
- Waiting / Imported / Duplicates / Upgrades / Ignored inbox states.
- Bulk import with explicit route selection or per-item automatic routing.
- Non-destructive symlink imports; Decypharr/Real-Debrid source media is never moved.
- Background import jobs with persistent progress toasts and per-item failure reasons.
- Scraping page for requests still searching indexers before they reach Download Queue.
- Unified Radarr/Sonarr/Lidarr download queue.
- Real-Debrid device OAuth and account torrent-library view.
- Routing-rule editor plus learned title corrections.
- Broken-link scanner, orphan detector, safe repair and safe Undo.
- Read-only DUMB namespace file browser.
- Filterable application logs.
- Multiple users, email login/password reset, request permissions/limits and themes.
- Per-title operational timeline.
- Explainable release-scoring policy with `Why this score` details.
- Sanitized diagnostics ZIP for GitHub/support issues.
- Optional ntfy, Gotify, Discord webhook and email notifications.
- Daily rolling SQLite backups and manual backups.
- Sanitized configuration export/import.
- Optional GitHub release update checker.
- Safe JSON-only community music-provider SDK.
- Installable PWA/mobile shell.

## DUMB namespace requirement

The Debian host may not contain `/mnt/debrid`. DUMB creates that mount tree inside the Arr process mount namespace.

ArrNexus therefore reads through:

```text
/proc/<main-radarr-pid>/root/mnt/debrid
```

while symlinks written to the media libraries retain normal DUMB-visible targets such as:

```text
/mnt/debrid/decypharr/__all__/Movie (2026)/Movie.mkv
```

The stack requires `pid: host` and `SYS_PTRACE` so it can inspect the live Arr namespace. It does **not** require `privileged: true`.

## Portainer / Docker Compose

The included Compose file intentionally contains no application secrets:

```yaml
services:
  arrnexus:
    build: .
    container_name: arrnexus
    restart: unless-stopped
    pid: host
    cap_add:
      - SYS_PTRACE
    extra_hosts:
      - "host.docker.internal:host-gateway"
    ports:
      - "8484:8000"
    volumes:
      - ./data:/data
```

Build/start it and open:

```text
http://YOUR-SERVER:8484
```

On a fresh database the setup wizard creates the first administrator. Service URLs/API keys, mounts, provider settings, themes, users and operational policy are then maintained through the UI and persisted in `/data/router.db`.

### Recommended setup order

1. **Connections** — Radarr, Sonarr, Lidarr, Prowlarr, Jellyfin and optional Seerr.
2. **Settings → Mounts & logical directories** — confirm DUMB roots and specialist destinations.
3. **Debrid / DMM** — connect the same Real-Debrid account used by DMM/Decypharr if desired.
4. **Profile** — select theme/dashboard layout.
5. **Problem Centre** — confirm namespace/service health.
6. **DMM Inbox** — test one import before bulk routing.

## Core workflows

### Normal Arr request

```text
Discover
  → specialist Radarr/Sonarr
  → Prowlarr
  → InfiniDysk/Usenet OR Decypharr/Real-Debrid
  → Arr library
  → Jellyfin
```

### Direct Debrid / DMM acquisition

```text
Debrid / DMM
  → Prowlarr torrent search
  → TV-pack / quality / cache / policy filtering
  → Real-Debrid
  → Decypharr
  → __all__
  → DMM Inbox
  → specialist Arr symlink library
```

### Music

```text
Public/account-free discovery catalogue
  → identify artist/album
  → Lidarr
  → Prowlarr
  → NZB/Usenet
  → lidarr-nzbdav
```

ArrNexus does not download audio from streaming services.

## Upgrading from v3.0 validated

Stop v3, extract v4 and copy only the persistent `data` directory:

```bash
cd /opt/dmm-arr-router/arrnexus-v3.0-validated
docker compose down

cd /opt/dmm-arr-router
unzip /home/<user>/arrnexus-v4.0.zip

mkdir -p arrnexus-v4.0/data
cp -a arrnexus-v3.0-validated/data/. arrnexus-v4.0/data/

cd arrnexus-v4.0
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=150 arrnexus
```

Do **not** copy an old `.env` as a requirement. Existing SQLite configuration migrates forward automatically.

## Validation

Run:

```bash
python validate.py
```

The v4 validator covers Python/Jinja startup plus specific regression tests for:

- Discover shelf/search rendering (the previous HTTP 500 case)
- music-provider source isolation
- TV full-series/season/episode parsing
- full-series and missing-season UI
- Sonarr season-coverage visualizer
- Real-Debrid cache badges
- pack-aware size/scoring rules
- core authenticated pages and PWA resources

See `VALIDATION.md` for the full report and environment limits.

## Security

- Never commit `/data/router.db`, OAuth credentials, API keys or SMTP secrets.
- Stored connection secrets are masked in normal UI views.
- Diagnostics/config exports omit or mask secrets.
- Real-Debrid source media is never deleted during import.
- Undo removes only symlinks recorded as created by ArrNexus.
- The file browser is read-only.
- Put ArrNexus behind an appropriate reverse proxy/authentication layer before exposing it outside a trusted network.

## Community provider example

The provider SDK is JSON-only; community providers cannot execute Python code:

```json
{
  "key": "example-catalog",
  "name": "Example Catalog",
  "description": "Community catalog search",
  "search_url": "https://example.com/search?q={query}"
}
```
