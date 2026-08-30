# ArrNexus v9.0.0-beta — The Product Release

**Your media stack. One control plane.**

ArrNexus is a self-hosted control and intelligence layer that sits above an existing media stack. It integrates with specialist applications instead of unnecessarily replacing them: Radarr, Sonarr, Lidarr, Prowlarr, Jellyfin, Seerr, DUMB, InfiniDysk/NzbDAV, Decypharr, Debrid providers, AIOStreams and music discovery/services.

v9 is a major product/onboarding release built directly on the validated v8 source package. The v7 and v8 regression suites remain part of `validate.py`.

## What's new in v9

### A real public front door

`/` is now a safe public ArrNexus landing/about page. It explains what ArrNexus is, what it integrates with and how a first deployment works without exposing private library counts, IP addresses, service health, mount paths or credentials.

- Brand-new instance: **Get started** opens `/setup`.
- Configured but logged out: **Sign in** opens `/login`.
- Logged in: **Dashboard** opens `/dashboard`.

The authenticated control centre is now explicitly `/dashboard`.

### New ArrNexus identity

v9 includes a new original ArrNexus wordmark and compact application icon in `app/static/` and uses them throughout the public landing page, first-run setup, login, sidebar, favicon and PWA manifest.

The visual system moves toward a near-black/white operational UI with restrained cyan/purple accent lighting. The existing information-dense operational pages remain intact.

### Guided first-run onboarding

Creating the first administrator no longer pretends setup is finished. It redirects to `/onboarding`, which guides an administrator through:

1. Administrator creation.
2. Environment and DUMB/Arr namespace detection.
3. Live bounded API verification for configured Radarr/Sonarr/Lidarr/Prowlarr/Jellyfin/Seerr services.
4. Provider selection.
5. Mount/library registry review.
6. Final Stack Readiness review.

Normal page navigation does **not** run the live readiness probes. They are restricted to explicit setup/readiness pages so v9 does not regress the performance/caching work from v7.

### Provider Registry

v9 stops treating Real-Debrid as the architecture. It introduces a provider-neutral registry at `/providers`.

Supported provider identities include:

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
- Stremio NNTP (manual/structured AIOStreams configuration)
- StremThru Newz
- AIOStreams Native
- Torrin

Credential field names for AIOStreams-compatible providers are aligned with the current AIOStreams service schema. Complex structured providers such as direct Stremio NNTP are identified but are deliberately **not** auto-written from a guessed flat credential model.

Provider secrets are stored in ArrNexus persistent SQLite settings and masked in the UI. v9 can conservatively seed the new registry from clearly named legacy Real-Debrid/NzbDAV settings during startup; existing settings are not deleted.

### Multi-provider AIOStreams Auto-Wire

The v8 safety workflow remains:

`GET current config → digest → masked preview → re-check digest → private backup → merged full PUT → verify`

v9 extends the merge to enabled provider-registry services. Provider credentials only fill **missing** AIOStreams user fields. Existing AIOStreams user credentials and unrelated configuration are preserved. AIOStreams operator-level default/forced credentials are not modified by ArrNexus.

Prowlarr wiring continues to preserve automatic source selection (`sources: []` / omitted service allow-list) so both torrent and Usenet sources can be used when AIOStreams supports them.

### Stack Readiness

`/readiness` gives administrators a dependency-oriented view of whether the stack is ready for automation:

- Core Arr/Prowlarr configuration
- optional Lidarr/Jellyfin/Seerr integrations
- DUMB/Arr mount namespace availability
- library mount registry
- configured acquisition providers

The Dashboard shows a cheap readiness summary. Live API checks run only on the explicit onboarding/readiness pages.

## Retained v8 functionality

v9 retains the v8 administrator-only AIOStreams Bridge:

- separate public status and authenticated User API verification
- `GET /api/v1/user` full configuration handling
- full-replacement `PUT /api/v1/user`
- `encryptedPassword` reuse
- stale-preview refusal
- masked preview
- private pre-write backups
- rollback with pre-rollback safety backup
- Prowlarr URL/API-key reuse
- conservative NzbDAV credential reuse
- Real-Debrid compatibility wiring
- Newznab/Torznab/SAB endpoint helpers
- ID-based AIOStreams search diagnostics
- aggressive playback URL/header/secret redaction

## Retained v7 functionality

The preserved v7 suite continues to cover the corrected v7 baseline, including:

- per-user Spotify OAuth/personal library aggregation logic
- native InfiniDysk Overview telemetry parsing
- English audio **and** English subtitle Language Guard
- non-destructive Language Guard rejection path
- Prowlarr indexer management
- correct Sonarr season-by-season interactive search
- strict DUMB/InfiniDysk/Decypharr connector verification
- DMM/source/link/library caches and startup prewarming
- the existing ArrNexus acquisition, routing, quality, self-healing and maintenance workflows

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

The `pid: host` + `SYS_PTRACE` architecture is intentional. ArrNexus follows the live Radarr/DUMB namespace when `/mnt/debrid` is not mounted on the Debian host itself.

## Fresh install

```bash
mkdir -p /opt/dmm-arr-router
cd /opt/dmm-arr-router
unzip /path/to/arrnexus-v9.0.zip
cd arrnexus-v9.0

docker compose config
docker compose up -d --build
docker compose ps
```

Open:

```text
http://YOUR-SERVER:8484/
```

The public ArrNexus landing page will offer **Start setup**. Create the first administrator and complete the guided setup.

## Upgrade from v8

Do **not** overwrite or delete v8. Keep it as rollback protection.

```bash
cd /opt/dmm-arr-router/arrnexus-v8.0
docker compose down

cd /opt/dmm-arr-router
unzip /path/to/arrnexus-v9.0.zip
mkdir -p arrnexus-v9.0/data
cp -a arrnexus-v8.0/data/. arrnexus-v9.0/data/

cd arrnexus-v9.0
python3 validate.py

docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=200 arrnexus
curl -fsS http://127.0.0.1:8484/api/health && echo
```

Then open:

```text
http://YOUR-SERVER:8484/
```

On an upgraded installation the first page is intentionally the new public About page. Click **Dashboard** to enter the authenticated control centre.

## Rollback to v8

```bash
cd /opt/dmm-arr-router/arrnexus-v9.0
docker compose down

cd /opt/dmm-arr-router/arrnexus-v8.0
docker compose up -d
```

v9 should be tested against a **copy** of v8 persistent data before v8 is removed.

## Validation

Run:

```bash
python3 validate.py
```

`validate.py` runs:

1. `validate_v8.py`
2. which runs the preserved `validate_v7.py`
3. then v9-specific product/provider/onboarding tests

The release process additionally compiles Python and Jinja templates, checks JavaScript syntax, performs a clean Uvicorn smoke test, scans the package for credential artifacts, creates the ZIP, extracts that exact ZIP into a new directory and runs validation again.

## Beta status

`9.0.0-beta` is intentional. Offline/deterministic release validation cannot prove your real DUMB namespace, Arr credentials, provider accounts, Spotify OAuth or AIOStreams instance. Keep v8 intact until the live server test is complete.

## Security notes

- Credentials are never bundled in the release ZIP.
- Provider and connector secrets remain in persistent `/data`.
- AIOStreams raw backup JSON may contain credentials and is stored privately beside persistent data; it is never packaged.
- Public pages do not expose library/service/mount details.
- Destructive operations remain preview/confirmation oriented and should target ArrNexus-created links rather than raw Debrid content.
