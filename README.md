# ArrNexus v3.0

ArrNexus is a self-hosted media-control layer for DUMB-style Radarr/Sonarr/Lidarr stacks using InfiniDysk/NzbDAV, Decypharr/Real-Debrid, Prowlarr and Jellyfin.

It is designed to be deployed as a single Docker/Portainer stack and then configured entirely from the browser. A normal installation does **not** require an `.env` file.

## Main features

- First-run web setup creates the administrator account.
- UI-managed Radarr, Sonarr, Lidarr, Prowlarr, Jellyfin and Seerr connections.
- Automatic discovery of DUMB Arr processes through the host PID namespace.
- DMM/Decypharr inbox scanning through `/proc/<arr-pid>/root/mnt/debrid`.
- Clean metadata titles, posters, genre badges, duplicate grouping and quality comparison.
- Waiting / Imported / Duplicates / Upgrades / Ignored inbox states.
- Bulk import with one route for all selected items or per-item automatic routing.
- Non-destructive symlink imports: Decypharr/Real-Debrid source media is never moved.
- Background import jobs with progress, per-item failure reasons and a persistent job toast.
- Scraping page showing Arr searches before a result reaches the normal download queue.
- Unified Radarr/Sonarr/Lidarr download queue.
- Seerr-backed trending/popular Discover shelves plus separate shelves for every discovered specialist Arr library.
- Direct Prowlarr -> Real-Debrid torrent search with Movies / TV category filtering.
- Real-Debrid device OAuth and account library view.
- Music Hub backed by public/open discovery sources, with Add/Search actions routed into Lidarr/Usenet.
- MusicBrainz, ListenBrainz, Apple/iTunes, Audius and Internet Archive support by default.
- Optional SoundCloud application credentials, Jamendo client ID and Last.fm application API key.
- Spotify, Amazon Music, Beatport, Bandcamp, Last.fm and Discogs catalog launchers without personal account linking.
- Routing-rule editor and learned title corrections.
- Broken-symlink scanner, orphan detector, safe repair and safe Undo.
- Read-only browser for registered DUMB library paths.
- Filterable application logs.
- Multiple users, email login/password reset, per-user dashboard layouts and 12 themes.

## DUMB namespace requirement

The host itself may not contain `/mnt/debrid`. DUMB creates the mount tree inside the Arr process mount namespace.

ArrNexus therefore uses:

```text
/proc/<main-radarr-pid>/root/mnt/debrid
```

while all symlinks it creates still contain the normal DUMB-visible target path such as:

```text
/mnt/debrid/decypharr/__all__/Movie (2026)/Movie.mkv
```

The Compose stack needs `pid: host` and `SYS_PTRACE` so it can inspect the live Arr namespace. It does not need `privileged: true`.

## Portainer / Docker Compose installation

Use the included `docker-compose.yml`:

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

Then build/start the stack and open:

```text
http://YOUR-SERVER:8484
```

On the first visit ArrNexus opens the setup wizard. Create the administrator there; no username/password environment variables are needed.

### Browser setup order

1. **Connections**: save Radarr, Sonarr, Lidarr, Prowlarr, Jellyfin and (optionally) Seerr URLs/API keys.
2. **Settings -> Mounts & logical directories**: confirm the DUMB paths and add/remove custom route roots.
3. **Debrid / DMM**: optionally connect Real-Debrid using device authentication.
4. **Profile & Appearance**: choose a theme and dashboard layout.
5. **DMM Inbox**: review routing suggestions before the first bulk import.

## Seerr discovery

Seerr is optional but strongly recommended when it is already present in the media stack. ArrNexus uses it as the discovery metadata backend for trending/popular movie and TV shelves. This avoids requiring every ArrNexus user to create a separate TMDB credential.

The existing Arr libraries are always shown independently from Seerr, so specialist shelves such as Kids, Christmas, Halloween, Netflix, Disney+, Amazon, Apple TV+ and BBC can still be displayed from the discovered Arr instances.

## DMM / Real-Debrid workflow

There are two related but distinct workflows:

```text
Discover -> Radarr/Sonarr -> Prowlarr -> selected download client
```

and:

```text
Debrid / DMM -> Prowlarr torrent search -> Real-Debrid -> Decypharr -> __all__ -> DMM Inbox -> Arr symlink library
```

A title being present in Radarr/Sonarr does not by itself mean it exists in the Real-Debrid library. ArrNexus displays these states separately.

## Music Hub

ArrNexus does not download audio from Spotify, Apple Music, Amazon Music, SoundCloud or other streaming services. Discovery metadata is resolved independently and the selected artist/album is sent to Lidarr. Lidarr then searches its configured Prowlarr/Usenet indexers and uses the existing Usenet download path.

No personal Spotify, Apple or Amazon login is required.

Optional provider settings are entered under **Settings -> Optional music catalog credentials**:

- SoundCloud: application client ID + client secret.
- Jamendo: developer client ID.
- Last.fm: application API key; public chart/search calls do not require a listener session.

The core Music Hub remains usable without either.

## Upgrading from ArrNexus v2.0

Stop v2, extract v3 and copy the old persistent `data` directory. Do **not** copy the old `.env` as a requirement for v3.

```bash
cd /opt/dmm-arr-router/arrnexus-v2.0
docker compose down

cd /opt/dmm-arr-router
unzip /home/<user>/arrnexus-v3.0.zip

mkdir -p arrnexus-v3.0/data
cp -a arrnexus-v2.0/data/. arrnexus-v3.0/data/

# Optional one-time migration if v2 still kept API keys only in .env:
python3 arrnexus-v3.0/migrate_legacy_env.py \
  arrnexus-v2.0/.env \
  arrnexus-v3.0/data/router.db

cd arrnexus-v3.0
docker compose up -d --build
```

Open `http://SERVER:8484` and check **Connections**. The optional migration command copies old connection values into SQLite; after verification the old `.env` is no longer required by ArrNexus v3.

## Security

- Never commit `data/router.db`, OAuth credentials or API keys to Git.
- ArrNexus masks stored API keys in the UI.
- Real-Debrid source files are never deleted by an import.
- Undo only removes symlinks recorded as created by ArrNexus.
- The built-in file browser is read-only.
- Put ArrNexus behind your normal reverse proxy/authentication policy before exposing it outside your trusted network.

## GitHub publishing notes

Recommended repository contents:

```text
app/
Dockerfile
docker-compose.yml
requirements.txt
README.md
.env.example     # informational/legacy only
.gitignore
```

Do not commit the runtime `data/` directory.

## Validated standout features

This validated v3 build also includes the higher-level features intended to make ArrNexus useful as a public GitHub project rather than only a private dashboard:

- **Per-title timeline**: follow a title across request, scraping/search, import and repair activity from one chronological screen.
- **Explainable release policy**: Prowlarr/Real-Debrid results receive a visible score with reasons such as resolution, codec, release size, seeders and rejected CAM/TS terms. The policy is edited in Settings.
- **Diagnostics bundle**: one-click ZIP containing sanitized settings, logs, jobs, imports, version and namespace/connection state. Secrets are masked or omitted.
- **Notifications**: optional ntfy, Gotify, Discord webhook and email delivery. Notification failures are isolated from import jobs.
- **Automatic backups**: rolling daily SQLite backups plus manual backup downloads.
- **Configuration portability**: sanitized JSON export/import for non-secret settings and logical mount mappings.
- **Update checker**: optional GitHub release checker against a repository configured by the administrator.
- **Provider SDK**: safe JSON-only music catalog provider files under `/data/providers`; community providers cannot execute Python code.
- **PWA/mobile support**: installable manifest/service worker and responsive mobile navigation.
- **User request controls**: administrators can allow/deny requests and set per-user daily request limits.
- **Release score explanations**: every scored direct-debrid result can show exactly why it ranked where it did.

Run the bundled offline validator after installing dependencies:

```bash
python validate.py
```

See `VALIDATION.md` for the validation matrix and environment limitations.

### JSON catalog provider example

A provider file is data-only:

```json
{
  "key": "example-catalog",
  "name": "Example Catalog",
  "description": "Community catalog search",
  "search_url": "https://example.com/search?q={query}"
}
```

Upload it from **Settings → Music discovery providers**. The required `{query}` placeholder is URL-encoded by ArrNexus.
