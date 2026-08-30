# ArrNexus v9.2.0-beta — Reliability, Documentation & Performance Release

**Your media stack. One control plane.**

ArrNexus is a self-hosted control and intelligence layer that sits above an existing media stack. It integrates with specialist applications instead of unnecessarily replacing them: Radarr, Sonarr, Lidarr, Prowlarr, Jellyfin, Seerr, DUMB, InfiniDysk/NzbDAV, Decypharr, Debrid/Usenet providers, AIOStreams and music discovery/services.

v9.2 builds on v9.1 with a production-data Dashboard fix, a much more detailed README-driven public homepage, measurable route-performance diagnostics and less aggressive navigation prefetching. The black/white ArrNexus visual identity remains the single product-wide design.

`validate.py` runs the retained **v9.1 → v9 → v8 → v7** regression chain before v9.2-specific checks.

## What's new in v9.2


### Dashboard reliability fix

v9.1 introduced a shared Dashboard snapshot. Real upgraded databases contain `sqlite3.Row` objects in recent imports/jobs/activity, and those objects cannot be deep-copied. A clean empty validation database did not expose that. v9.2 normalises every cached database row to plain dictionaries before caching and adds a regression test with real non-empty history.

The Dashboard also has a degraded-mode fallback: if one integration/snapshot fails, administrators see a diagnostic banner and the error is written to Unified Logs instead of receiving an opaque `Internal Server Error`.

### Measurable performance

- persistent private application shell remains
- client page cache freshness increased for recently visited pages
- stale-while-revalidate retained
- sustained hover/pointer intent prefetch retained
- aggressive idle crawling of every sidebar route removed
- Dashboard server snapshot retained
- every HTTP response includes `Server-Timing` and `X-ArrNexus-Elapsed-Ms`
- requests slower than 1.5 seconds are recorded as `performance / slow_request` log events

This makes future optimisation evidence-driven: the slow route can be identified directly rather than guessed at.

### README-driven public documentation

The public `/` page now includes the project explanation, architecture, requirements matrix, installation and upgrade procedure, Python virtualenv validator steps, Docker networking guidance, integration overview, DMM/virtual-media concepts, acquisition strategies, feature guide, security model, troubleshooting and latest-build download.

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
- no automatic idle crawl of expensive navigation destinations; prefetch happens only on real user intent
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

The recommended Docker deployment does not require installing Python dependencies on the host. However, if you want to run the packaged release validator before building the image, use a local virtual environment. This avoids modifying Debian's system Python.

```bash
sudo apt update
sudo apt install -y unzip python3-venv

mkdir -p /opt/arrnexus
cd /opt/arrnexus
unzip /path/to/arrnexus-v9.2.zip
cd arrnexus-v9.2

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python validate.py
deactivate

docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=200 arrnexus
curl -fsS http://127.0.0.1:8484/api/health && echo
```

Open:

```text
http://YOUR-SERVER:8484/
```

Create the first administrator and complete the guided setup.

## Upgrade from v9.1

Keep v9.1 intact as rollback protection. Build v9.2 beside it and copy only persistent application data.

```bash
cd /opt/arrnexus/arrnexus-v9.1
docker compose down

cd /opt/arrnexus
unzip /path/to/arrnexus-v9.2.zip
mkdir -p arrnexus-v9.2/data
cp -a arrnexus-v9.1/data/. arrnexus-v9.2/data/

cd arrnexus-v9.2
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python validate.py
deactivate

docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=200 arrnexus
curl -fsS http://127.0.0.1:8484/api/health && echo
```

If any command fails, stop at that command and diagnose before making further changes.

## Rollback to v9.1

```bash
cd /opt/arrnexus/arrnexus-v9.2
docker compose down

cd /opt/arrnexus/arrnexus-v9.1
docker compose up -d
```

## Validation

`validate.py` executes:

1. `validate_v9.py`
2. `validate_v8.py`
3. preserved `validate_v7.py`
4. v9.1 regression suite
5. v9.2-specific Dashboard/documentation/performance/public-release tests

Release engineering additionally performs Python compilation, real Jinja compilation, JavaScript syntax checks, a real Uvicorn smoke test from a clean copy, staged-package secret scanning, ZIP packaging and a second validation run after extracting the exact final ZIP into another clean directory.

## Beta status

`9.2.0-beta` remains intentional until the unified UI and performance changes have been exercised against a real multi-service deployment.

## Security notes

- Credentials are never bundled in the release ZIP.
- Provider/connector secrets remain in persistent `/data`.
- AIOStreams raw backup JSON may contain credentials and is never included in the public release exporter.
- Public pages do not expose private stack state.
- Destructive operations remain preview/confirmation oriented and should target ArrNexus-created links rather than raw Debrid content.
