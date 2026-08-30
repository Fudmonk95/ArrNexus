# ArrNexus v8.0.0-beta — AIOStreams Bridge & Live Media Operations

ArrNexus is a self-hosted control and intelligence layer for Arr-based media stacks. It is designed to sit above Radarr, Sonarr, Lidarr, Prowlarr, Jellyfin/Seerr, DUMB, InfiniDysk and Decypharr without replacing the specialist applications that already do those jobs well.

v8.0.0-beta builds directly on the validated v7 source baseline. It retains v7 personal Spotify data, native InfiniDysk telemetry, strict English-language validation, Prowlarr management, corrected Sonarr search, strict service verification and performance caches, then adds an administrator-only AIOStreams Bridge designed around AIOStreams' full-replacement User API.

## v8.0 beta — AIOStreams Bridge

AIOStreams integration is intentionally conservative because `PUT /api/v1/user` replaces the complete stored user configuration. ArrNexus therefore uses this write sequence:

**GET current config → calculate digest → masked preview → re-check digest → private backup → merged full PUT → verify**

If the remote AIOStreams configuration changes after a preview, ArrNexus refuses Apply and requires a fresh preview. Unrelated AIOStreams settings are copied forward unchanged rather than replaced with an ArrNexus-only object.

The administrator-only **System → AIOStreams** page can:

- store the AIOStreams base URL, UUID/alias and password through ArrNexus' persistent secret settings;
- authenticate using either the configured password or the `encryptedPassword` returned by AIOStreams;
- show public instance reachability separately from authenticated User API access;
- create a masked Auto-Wire preview before any write;
- reuse the existing ArrNexus Prowlarr URL and API key in a Prowlarr preset;
- keep a new Prowlarr preset's `sources` empty so both torrent and Usenet indexers remain available;
- preserve an existing explicit Prowlarr service/source allow-list rather than replacing it;
- enable/reuse Real-Debrid only when ArrNexus can identify an existing key safely;
- enable NzbDAV conservatively, preserving existing AIOStreams credentials and filling only clearly identified missing fields;
- warn instead of inventing NzbDAV credentials when ArrNexus cannot confirm them;
- show safe Newznab, Torznab and SAB-compatible endpoint helpers;
- perform ID-based AIOStreams search diagnostics while redacting playback URLs, Authorization/Cookie/header data and secret-bearing fields;
- create private local AIOStreams configuration backups before Auto-Wire writes and before rollback.

Raw AIOStreams backups can contain credentials. They live under the persistent `/data` area with restrictive permissions and are never included in release ZIPs or rendered in the UI.

**Beta note:** the bridge has deterministic offline/mock API validation, but this version remains `8.0.0-beta` until its preview/apply/rollback flow has been exercised against the real local AIOStreams instance and a copy of the existing v7 data.

## v7 baseline retained

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

v8 retains `ffmpeg` in the container because Language Guard uses `ffprobe`. The first Docker build will therefore take longer than older releases.

## Upgrade from ArrNexus v7

Do not modify or delete the v7 directory. Extract v8 beside it, then copy **only the persistent `data/` directory** into v8 before starting the beta. This gives you an immediate rollback path to v7.

```bash
cd /opt/dmm-arr-router/arrnexus-v7.0
docker compose down

cd /opt/dmm-arr-router
unzip /home/renegademonk/arrnexus-v8.0.zip
mkdir -p arrnexus-v8.0/data
cp -a arrnexus-v7.0/data/. arrnexus-v8.0/data/

cd arrnexus-v8.0
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=150 arrnexus
```

After startup, first verify the existing v7 features, then configure **System → AIOStreams**. Use **Preview** before Apply and confirm the preview preserves unrelated AIOStreams settings. Test rollback before considering the beta proven.

Do not copy an old `.env` unless you intentionally still rely on legacy/bootstrap variables. Normal deployment does not require one.

## Validation

Run:

```bash
python validate.py
```

`validate.py` first runs the complete preserved v7 suite (`validate_v7.py`), then validates the v8 AIOStreams bridge with a deterministic local mock API. It checks full-config preservation, stale-preview protection, Prowlarr URL/key reuse, Real-Debrid secret masking, conservative NzbDAV credential reuse, pre-write backups, rollback safety backups, search redaction, admin-only routes, real templates and the version/channel UI.

See `VALIDATION.md` for the release validation record.

## Security model

- secrets are stored in the ArrNexus persistent database, not rendered back in plaintext
- diagnostics sanitize secrets
- Language Guard never deletes DMM/Real-Debrid source media
- Undo removes ArrNexus-created links, not the underlying debrid source
- JSON community connectors/providers are data-only and do not execute third-party Python
- AIOStreams full-config writes require an explicit preview and stale-digest check
- AIOStreams raw backup JSON stays in private persistent storage and is never packaged
- AIOStreams search diagnostics remove playback URLs and credential-bearing headers
- destructive actions should remain explicit and bounded
