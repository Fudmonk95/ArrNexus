# ArrNexus v9.1.0-beta — Unified Product & Performance Release

**Your media stack. One control plane.**

ArrNexus is a self-hosted control and intelligence layer that sits above an existing media stack. It integrates with specialist applications instead of unnecessarily replacing them: Radarr, Sonarr, Lidarr, Prowlarr, Jellyfin, Seerr, DUMB, InfiniDysk/NzbDAV, Decypharr, Debrid/Usenet providers, AIOStreams and music discovery/services.

v9.1 is a refinement release built on v9.0. It makes the public ArrNexus visual identity the **only** application theme, expands the public product/download experience and addresses navigation latency with stale-while-revalidate page caching, idle prefetch and a server-side Dashboard snapshot.

`validate.py` runs the retained **v9 → v8 → v7** regression chain before v9.1-specific checks.

## What's new in v9.1

### One ArrNexus visual identity

The black/white public landing style now applies throughout the private control centre:

- near-black application background
- crisp white primary typography
- restrained grey secondary copy
- cyan / violet / magenta ArrNexus accents
- consistent panels, forms, tables, navigation, buttons and account pages
- the ArrNexus logo/icon throughout the app

Legacy theme switching has been removed from the UI. Existing database `theme` fields are retained only for backwards compatibility and are ignored by the application shell.

### Faster navigation

v9.1 extends the v7 performance work rather than replacing it:

- persistent sidebar/header application shell
- soft navigation replaces only the page body
- in-flight request de-duplication
- 45-second client page cache
- stale-while-revalidate reuse for recently visited pages
- pointer-hover / pointer-down prefetch
- low-priority idle prefetch of common navigation destinations
- a short-lived server-side Dashboard snapshot
- Dashboard refresh happens in the background after the snapshot becomes stale
- namespace/source/library filesystem work is moved through worker threads where possible
- normal Dashboard navigation no longer waits on a fresh fan-out to every connected service

Explicit live readiness checks remain on `/readiness` and `/onboarding`, where the user has intentionally asked for fresh verification.

### Expanded public front door

`/` is a safe public ArrNexus product page. It now includes:

- what ArrNexus is and what it does not replace
- architecture/workflow explanation
- major acquisition, routing, Language Guard, self-healing, Quality Lab and telemetry features
- supported Arr, library, Debrid/Usenet, AIOStreams and music integrations
- first-run workflow
- security/non-destructive design notes
- What's New section
- public **Download ZIP** and **SHA-256** controls

No library counts, private service URLs, API keys, mount paths or authenticated service health are exposed on the public page.

### Safe public release download

A public visitor can download the running ArrNexus source build from:

```text
/download/latest
```

Checksum:

```text
/download/latest.sha256
```

Metadata:

```text
/api/public/release
```

The archive is generated from the installed source tree and cached in the operating-system temporary directory. The exporter excludes:

- `/data`
- `.env`
- session secrets
- SQLite/databases
- AIOStreams backup data
- Python bytecode/cache directories
- virtual environments
- Git metadata
- previous ZIP/tar archives

It is intended as a public **source release**, never as a backup of the live server.

## Product architecture retained from v9

### Public/private split

- `/` — public About/product/download page
- `/setup` — first administrator creation
- `/login` — authentication
- `/dashboard` — private control centre
- `/onboarding` — administrator setup guide
- `/providers` — provider registry
- `/readiness` — explicit live stack verification

### Guided onboarding

A fresh instance guides an administrator through:

1. administrator creation
2. environment / DUMB namespace discovery
3. Arr/Jellyfin/Seerr connection verification
4. provider configuration
5. mount/library registry review
6. Stack Readiness

### Provider Registry

Provider identities include:

- Real-Debrid
- TorBox
- Premiumize
- AllDebrid
- Debrid-Link
- EasyDebrid
- Debrider
- Offcloud
- put.io
- PikPak
- Seedr
- Easynews
- InfiniDysk / NzbDAV
- AltMount
- Stremio NNTP (advanced/manual)
- StremThru Newz
- AIOStreams Native
- Torrin

Provider credentials are stored in ArrNexus persistent data and masked in the UI. v9/v9.1 can conservatively seed the registry from clearly named legacy Real-Debrid/NzbDAV settings without deleting the old values.

### AIOStreams Bridge

The retained safe workflow is:

```text
GET current config
→ digest
→ masked preview
→ re-check digest
→ private backup
→ merged full PUT
→ verify result
```

Multi-provider Auto-Wire only fills missing user-level provider credentials. Existing AIOStreams user credentials, unrelated configuration and operator-level default/forced credentials are preserved.

### Stack Readiness

`/readiness` checks the important dependencies intentionally and concurrently:

- Radarr / Sonarr / Prowlarr core configuration
- optional Lidarr / Jellyfin / Seerr
- DUMB/Arr mount namespace visibility
- mount registry
- acquisition provider configuration

The Dashboard uses a cheap summary and cached service snapshot; it does not repeat the full readiness probe on every click.

## Retained v8 functionality

- administrator-only AIOStreams Bridge
- separate public status and authenticated User API verification
- full `GET /api/v1/user` handling
- full-replacement `PUT /api/v1/user`
- encryptedPassword reuse
- stale-preview refusal
- masked previews
- backup before write
- rollback with pre-rollback backup
- Prowlarr URL/API-key reuse
- conservative NzbDAV credential handling
- ID-based AIOStreams search diagnostics
- playback URL/header/Authorization/Cookie redaction

## Retained v7 functionality

- per-user Spotify OAuth/personal-library aggregation
- native InfiniDysk Overview telemetry
- English audio **and** English subtitle Language Guard
- non-destructive Language Guard rejection
- Prowlarr indexer management
- correct Sonarr season-by-season interactive search
- strict DUMB/InfiniDysk/Decypharr verification
- source/link/library caches and startup prewarming
- acquisition, routing, Quality Lab, self-healing and maintenance workflows

## Deployment model

Normal deployment remains Portainer/Docker Compose friendly and does not require an `.env` file for application settings.

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

`pid: host` + `SYS_PTRACE` is intentional. ArrNexus follows the live Arr/DUMB namespace when useful `/mnt/debrid` mounts exist inside that namespace rather than on the Debian host itself.

## Fresh install

```bash
mkdir -p /opt/arrnexus
cd /opt/arrnexus
unzip /path/to/arrnexus-v9.1.zip
cd arrnexus-v9.1

docker compose config
docker compose up -d --build
docker compose ps
```

Open:

```text
http://YOUR-SERVER:8484/
```

Create the first administrator and complete the guided setup.

## Upgrade from v9.0

Keep v9.0 intact as rollback protection.

```bash
cd /opt/arrnexus/arrnexus-v9.0
docker compose down

cd /opt/arrnexus
unzip /path/to/arrnexus-v9.1.zip
mkdir -p arrnexus-v9.1/data
cp -a arrnexus-v9.0/data/. arrnexus-v9.1/data/

cd arrnexus-v9.1
python3 validate.py

docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=200 arrnexus
curl -fsS http://127.0.0.1:8484/api/health && echo
```

If host Python dependencies are not installed, run the validator from a virtual environment or validate inside an ArrNexus-compatible Python environment. Docker itself installs the application requirements while building the image.

## Rollback to v9.0

```bash
cd /opt/arrnexus/arrnexus-v9.1
docker compose down

cd /opt/arrnexus/arrnexus-v9.0
docker compose up -d
```

## Validation

`validate.py` executes:

1. `validate_v9.py`
2. `validate_v8.py`
3. preserved `validate_v7.py`
4. v9.1-specific UI/performance/public-release tests

Release engineering additionally performs Python compilation, real Jinja compilation, JavaScript syntax checks, a real Uvicorn smoke test from a clean copy, staged-package secret scanning, ZIP packaging and a second validation run after extracting the exact final ZIP into another clean directory.

## Beta status

`9.1.0-beta` remains intentional until the unified UI and performance changes have been exercised against a real multi-service deployment.

## Security notes

- Credentials are never bundled in the release ZIP.
- Provider/connector secrets remain in persistent `/data`.
- AIOStreams raw backup JSON may contain credentials and is never included in the public release exporter.
- Public pages do not expose private stack state.
- Destructive operations remain preview/confirmation oriented and should target ArrNexus-created links rather than raw Debrid content.
