# ArrNexus v2.0

ArrNexus is a self-hosted control layer for DUMB-style Radarr/Sonarr/Lidarr stacks that combine Usenet and debrid media. It provides one UI for discovery, Real-Debrid/DMM-style library management, routing, symlink imports, queue visibility, music discovery and application settings.

## What v2.0 adds

- **ArrNexus branding** and a redesigned dashboard with activity/routing charts.
- **Discover request state**: search results stay on screen after `Add + search now` and show `Requested` when the title already exists in an Arr or the ArrNexus request database.
- **Real-Debrid device OAuth**: connect an RD account in the UI without pasting an API token.
- **Debrid / DMM page**: view the same Real-Debrid torrent library that DMM reads.
- **Prowlarr release search → Real-Debrid**: search configured torrent indexers and submit a chosen torrent/magnet directly to Real-Debrid. Decypharr can then expose it under `__all__` for routing.
- **DMM Inbox cleanup**: metadata-first display names, removal of common `www/indexer` release prefixes, canonical symlink filenames and duplicate RD torrents grouped into one title.
- **Inbox states**: Waiting, Imported, Duplicates, Upgrades and Ignored tabs.
- **Bulk routing**: select many entries and choose Auto, Default, Kids, Christmas, Halloween, Easter, Netflix, Disney+, Amazon, Apple TV+ or BBC as applicable.
- **Connection manager**: Radarr/Sonarr/Lidarr discovered instances, Prowlarr and Jellyfin can be edited from the UI. Saved UI values override `.env` values.
- **Unified queue diagnostics**: each Arr instance shows its connection state so an empty queue is no longer silently caused by a bad API key.
- **Music Hub**: provider tabs for native public sources (MusicBrainz, ListenBrainz, Apple/iTunes Search, Audius) plus account-free launchers for Spotify, Amazon Music, Beatport, Bandcamp, Last.fm and Discogs.
- **Lidarr actions**: artist results can be added to Lidarr and searched; existing Lidarr albums can be monitored and searched through the configured Usenet workflow.
- **Profiles**: username or email login, display name, password changes, per-profile dashboard layout and theme.
- **Email password reset**: configure SMTP in Settings and users can request 30-minute, one-time reset links from the login screen.
- **Multiple users**: administrators can create additional username/email accounts in Settings.
- **Themes**: ArrNexus, Radarr Gold, Sonarr Blue, Lidarr Green, Prowlarr Purple, Jellyfin Violet, Music Green, OLED, Nord, Dracula, Clean Light and Cyber.
- Existing features remain: safe symlink-only imports, progress jobs, duplicate/quality detection, routing rules and learned corrections, broken-link scan/repair, orphan detection, undo, provider badges, Jellyfin status and activity history.

## DMM and Real-Debrid: important distinction

ArrNexus does **not** depend on a private or undocumented debridmediamanager.com API. Instead it connects directly to the user's Real-Debrid account using Real-Debrid's official open-source device OAuth workflow.

That means DMM and ArrNexus can see the same Real-Debrid torrent library when they are connected to the same account. A movie requested through Radarr will not appear in the RD/DMM library merely because it was added to Radarr; it appears after a torrent is actually grabbed and submitted to Real-Debrid/Decypharr. ArrNexus therefore shows separate **Requested** and **In Real-Debrid** states.

## DUMB mount namespace

On DUMB installations the host may not have `/mnt/debrid` in its normal mount namespace. ArrNexus follows the live Radarr namespace through `/proc/<pid>/root/mnt/debrid`.

The Compose file therefore requires:

```yaml
pid: host
cap_add:
  - SYS_PTRACE
```

This is intentionally narrower than running the web application with `privileged: true`.

## First installation

```bash
cp .env.example .env
nano .env

docker compose config
docker compose up -d --build
docker compose ps
```

Default UI: `http://<docker-host>:8484`

Set a strong `APP_PASSWORD` and `SESSION_SECRET` before first start. Main Arr/Prowlarr/Jellyfin URLs and API keys can be placed in `.env` initially and then changed through **Connections** in the UI.

## Upgrading from DMM Arr Router v1.0

Stop v1.0, extract ArrNexus, then copy the existing `.env` and data directory into the new folder before starting it. The SQLite database is migrated automatically and retains import/activity history.

Example when the old directory is `/opt/dmm-arr-router/dmm-arr-router-v1.0`:

```bash
cd /opt/dmm-arr-router/dmm-arr-router-v1.0
docker compose down

cd /opt/dmm-arr-router
unzip /home/<user>/arrnexus-v2.0.zip

cp dmm-arr-router-v1.0/.env arrnexus-v2.0/.env
mkdir -p arrnexus-v2.0/data
cp -a dmm-arr-router-v1.0/data/. arrnexus-v2.0/data/

cd arrnexus-v2.0
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 arrnexus
```

The UI remains on port `8484` unless `APP_PORT` is changed.

## Real-Debrid connection

Open **Debrid / DMM → Connect Real-Debrid**. ArrNexus displays a device code, opens the official Real-Debrid device authorization page and polls until authorization completes. OAuth client credentials, access tokens and refresh tokens stay in the local SQLite settings database and are never rendered back into the UI.

For a distributable/public installation, protect the `data/` directory and never commit `.env`, `router.db`, API keys or OAuth credentials to Git.

## Music discovery sources

### Native, no personal streaming account

- **MusicBrainz** — artist/release metadata.
- **ListenBrainz** — public sitewide trending statistics.
- **Apple/iTunes Search** — public catalog/artwork search; no Apple account connection.
- **Audius** — public read-only track search and trending discovery.

### Catalog launchers

Spotify, Amazon Music, Beatport, Bandcamp, Last.fm and Discogs are intentionally treated as optional external catalog/search tabs where a stable unrestricted no-account API is not appropriate. ArrNexus never asks users to supply their personal credentials for these services.

Regardless of discovery source, the action path is:

`Discovery → Lidarr → Prowlarr/indexers → Usenet download client → Lidarr library`

ArrNexus does not download or copy audio from Spotify/Apple/Amazon/Beatport.

## Safe import model

ArrNexus never moves or deletes a Decypharr Real-Debrid source when importing it. It creates recorded symlinks into the selected Arr library and asks the Arr to rescan. Undo removes only symlinks recorded as created by ArrNexus.

Movie links use canonical names such as:

```text
Movie Title (2024)/Movie Title (2024).mkv
```

TV links use canonical episode names where an `SxxExx` identifier is available:

```text
Series Title/Season 01/Series Title - S01E03.mkv
```

## Publishing to GitHub

Before publishing:

- commit `.env.example`, never `.env`;
- exclude `data/`, `router.db`, logs and credentials;
- use a project-specific `MUSIC_USER_AGENT` with a project/contact URL;
- document that ArrNexus expects the user to have legitimate access to all configured services and media.
