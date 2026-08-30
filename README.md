# ArrNexus v7.0.0 — Personal Music, Language Guard & Live Media Operations

ArrNexus is a self-hosted control and intelligence layer for Arr-based media stacks. It is designed to sit above Radarr, Sonarr, Lidarr, Prowlarr, Jellyfin/Seerr, DUMB, InfiniDysk and Decypharr without replacing the specialist applications that already do those jobs well.

v7 turns the unfinished v6.2 work into a release: personal Spotify data, native InfiniDysk telemetry, strict English-language validation for DMM imports, Prowlarr indexer management, corrected Sonarr TV release search, stricter service verification and short-lived namespace caches to reduce repeated filesystem work.

## v7 highlights

### Spotify personal account integration

Spotify application credentials still provide catalogue search, but v7 adds a separate per-user OAuth flow. Once a user chooses **Music Hub → Spotify → Connect Spotify**, ArrNexus can show:

- saved tracks
- saved albums
- playlists
- top tracks
- top artists
- recently played tracks
- Spotify catalogue search

Global trends are shown separately as **ListenBrainz global trend data** and are never presented as Spotify-owned trending data.

Spotify user data is stored per ArrNexus profile. Refresh tokens are stored as secret settings. A Spotify redirect URI must exactly match the URI configured in the Spotify Developer Dashboard; use HTTPS for normal remote/LAN deployments.

### English Language Guard

DMM/Real-Debrid filenames are not trusted as proof of media language. v7 installs `ffprobe` and can inspect the actual audio and subtitle streams before a source is linked into a Radarr/Sonarr library.

Default policy:

- English audio required
- English subtitles required
- unknown language metadata fails closed
- source media is never deleted by Language Guard
- a failed source can trigger a replacement search in its owning Arr

The DMM Inbox exposes a Language filter/badge and the item review page can run a fresh stream inspection. The policy is configurable under Settings.

### Native InfiniDysk live telemetry

ArrNexus now uses InfiniDysk's authenticated `/api/get-overview-stats` API rather than depending only on raw Prometheus scraping. The integration supports the native 1h/24h/7d/30d/all windows and can surface live tiles, throughput series, providers, sessions, heatmap, latency/errors and indexer data when returned by InfiniDysk.

The InfiniDysk page also keeps queue/history controls and filters long history titles safely inside their panel.

### Prowlarr indexer control

The Indexers page reads Prowlarr indexers and can write supported operational settings back through the Prowlarr API:

- enabled state
- priority
- RSS
- automatic search
- interactive search

Indexer cards also expose tag/category context. Routing-critical tags are highlighted because DUMB-managed settings may be restored by DUMB after manual edits.

### Correct Sonarr TV interactive search

Sonarr does not perform a series-wide interactive search from `seriesId` alone. v7 searches the show's real seasons with `seriesId + seasonNumber`, merges/deduplicates the returned releases and feeds those results into ArrNexus's TV/full-series acquisition planner.

### Verified ecosystem connections

A green connector means more than “the web page answered”. InfiniDysk credentials are checked against an authenticated SAB-compatible call, Decypharr Bearer tokens are checked against a protected API, and DUMB rejects an HTML frontend response when an API response is expected.

### Performance work

The DMM source scanner, library inventory and source→symlink index now have bounded short-lived caches and are pre-warmed after startup. Arr/Dashboard work remains concurrent and network calls use short operational timeouts. This removes much of the repeated full-tree work that previously made normal page navigation increasingly expensive.

## Acquisition strategies

Discover supports:

- Automatic — compare Usenet + Debrid and grab one best candidate
- Debrid first → Usenet fallback
- Usenet first → Debrid fallback
- Debrid only
- Usenet only
- Fastest / prefer cached RD
- Best quality / score

The Arr remains responsible for final hand-off: NZBs go to the configured Usenet client (normally InfiniDysk) and torrents go to the configured torrent/debrid client (normally Decypharr).

## Other retained features

- DMM/Real-Debrid Inbox with metadata, routing, duplicate grouping and bulk import
- full-series / season-pack / episode classification
- Sonarr season coverage visualisation
- release-scoring Quality Lab
- Scraping/Acquisition timeline
- Download Queue aggregation
- self-healing search controls
- broken symlink detection/repair
- routing rules and specialist libraries
- Jellyfin integration and library browser
- Unified Logs + known-error explanations
- DUMB/InfiniDysk/Decypharr ecosystem connectors
- profiles, themes and request permissions
- notifications
- backups, diagnostics and sanitized config export/import
- PWA/mobile shell
- version/update channel badge (Stable / Beta / Development)

## Portainer / Docker deployment

A normal ArrNexus deployment does not require an `.env` file. Persistent application state lives in `/data` and is configured through the browser.

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

v7 installs `ffmpeg` in the container because Language Guard uses `ffprobe`. The first Docker build will therefore take longer than older releases.

## Upgrade from an earlier ArrNexus release

Stop the current container, extract v7, then copy the **persistent data directory only** into the new folder.

Example when upgrading from v6.1:

```bash
cd /opt/dmm-arr-router/arrnexus-v6.1
docker compose down

cd /opt/dmm-arr-router
unzip /home/renegademonk/arrnexus-v7.0.zip
mkdir -p arrnexus-v7.0/data
cp -a arrnexus-v6.1/data/. arrnexus-v7.0/data/

cd arrnexus-v7.0
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=150 arrnexus
```

Do not copy an old `.env` unless you intentionally still rely on legacy migration variables.

## Validation

Run:

```bash
python validate.py
```

The offline validator covers compilation/templates, authenticated page smoke tests, Discover regressions, music-provider isolation, Spotify OAuth/personal hub aggregation, TV-pack behavior, acquisition fallback, strict connector authentication, native InfiniDysk Overview parsing, English Language Guard policy evaluation, performance-cache presence, Prowlarr indexer UI, version badge and retained v6 behavior.

See `VALIDATION.md` for the release validation record.

## Security model

- secrets are stored in the ArrNexus persistent database, not rendered back in plaintext
- diagnostics sanitize secrets
- Language Guard never deletes DMM/Real-Debrid source media
- Undo removes ArrNexus-created links, not the underlying debrid source
- JSON community connectors/providers are data-only and do not execute third-party Python
- destructive actions should remain explicit and bounded
