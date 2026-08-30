# ArrNexus v5.0 — Media Intelligence & Ecosystem Control

ArrNexus is a self-hosted media-control layer for DUMB-style Radarr/Sonarr/Lidarr stacks using InfiniDysk/NzbDAV, Decypharr/Real-Debrid, Prowlarr, Jellyfin and optional Seerr discovery.

v5 expands ArrNexus from a media router into a **control plane** for the wider self-hosted media ecosystem. It is still Portainer/Docker Compose first: deploy the container, open the browser, create the administrator and configure everything in the UI. A normal installation does **not** require an `.env` file.

## What's new in v5

### Ecosystem connector platform

A new **Ecosystem** page provides built-in connector definitions for:

- InfiniDysk
- DUMB
- Decypharr
- AltMount
- Profilarr
- NeutArr
- Cleanuparr
- Maintainerr
- Bazarr
- Streamystats
- Zilean
- Riven
- Pulsarr

Connectors are API boundaries; ArrNexus does not bundle or copy these projects. Each connector advertises capabilities such as `health`, `queue`, `quality`, `search`, `analytics`, `lifecycle` or `subtitles`.

Enabled connectors are health-probed from the UI. Secrets persist in `/data/router.db` and are never displayed back in plaintext.

#### Safe community connector SDK

Custom service connectors can be installed as JSON under `/data/connectors`. They are data-only and cannot execute Python code.

Example:

```json
{
  "key": "my-service",
  "name": "My Service",
  "category": "Community",
  "default_url": "http://host.docker.internal:9000",
  "health_paths": ["/api/health", "/"],
  "auth_header": "X-Api-Key",
  "capabilities": ["health", "search"]
}
```

See `examples/connectors/example-service.json`.

### Native InfiniDysk operations

A new **InfiniDysk** page uses stable public interfaces instead of scraping its UI:

- `/healthz` health state
- SAB-compatible queue
- SAB-compatible recent history
- queue pause/resume
- `/metrics` Prometheus telemetry
- filtered operational metrics for NNTP, throughput, bytes, latency, seeking, streams, errors and queue state

The InfiniDysk connector key is treated as the SAB/API key for queue calls. Metrics degrade gracefully if the deployment does not expose them.

### Quality Lab

A new **Quality Lab** makes release selection explainable.

It can:

- parse resolution, codec, source, HDR/Dolby Vision, audio, edition and release group
- identify TV episode / season pack / full-series pack structure
- apply the current ArrNexus release policy
- show a 0–100 score and every reason that affected the score
- search Prowlarr and compare returned releases with the same scoring engine
- show Real-Debrid cache preference, seeders and pack-aware size ceilings
- recognise Profilarr as an optional external quality/configuration authority through the Ecosystem connector layer

ArrNexus intentionally does not copy Profilarr/Recyclarr logic. The connector boundary leaves room for deeper API integration while keeping ArrNexus responsible for orchestration, comparison and troubleshooting.

### Self-Healing

A new **Self-Healing** page scans every discovered DUMB Arr instance for:

- monitored missing movies
- missing TV episodes based on Sonarr statistics
- missing Lidarr tracks based on artist statistics
- Radarr cutoff-unmet upgrades where the Arr API exposes the flag
- queue warnings/import/stalled signals

Manual actions can trigger bounded searches for the first N missing/upgradable items.

An optional AutoPilot scheduler is included and is **off by default**. When enabled it:

- respects a configurable maintenance window
- has a minimum 15-minute interval
- has a configurable maximum number of actions per cycle
- can search missing items
- can optionally search upgrades
- never deletes torrents, Real-Debrid media or library files

Destructive queue-cleaning logic is deliberately left to specialist tools such as Cleanuparr rather than duplicated inside ArrNexus.

### DUMB topology awareness

The Ecosystem page visualises the DUMB namespace and currently discovered Radarr/Sonarr/Lidarr processes. This works even if no DUMB HTTP connector is configured because ArrNexus already sees the host PID namespace.

This makes ArrNexus useful both as:

- a DUMB-aware control layer on a single all-in-one host; and
- a standalone control layer for separately deployed Arr services.

## Existing v4 features retained

- Discover reliability fixes and Seerr-style poster shelves
- per-specialist-library discovery rails
- source-isolated Music Hub providers
- Debrid/DMM TV pack modes: Any / Full Series / Season Packs / Episodes
- smart complete-series and missing-season acquisition planning
- Real-Debrid cache badges
- Sonarr season coverage visualiser
- Problem Centre
- first-run browser setup
- UI-managed Radarr/Sonarr/Lidarr/Prowlarr/Jellyfin/Seerr credentials
- automatic DUMB Arr process discovery
- DMM/Decypharr inbox scanning through the Arr mount namespace
- clean metadata titles, posters, genres, duplicate grouping and quality comparison
- Waiting / Imported / Duplicates / Upgrades / Ignored inbox states
- bulk import with route selection
- non-destructive symlink imports
- persistent import jobs and progress toasts
- Scraping/search status page
- unified Arr download queue
- Real-Debrid device OAuth and torrent library
- routing rules and learned corrections
- broken-link scanner, orphan detector, repair and safe Undo
- read-only namespace file browser
- filterable logs
- users, themes, email reset and request limits
- title timeline
- explainable release policy
- diagnostics ZIP
- ntfy/Gotify/Discord/email notifications
- rolling SQLite backups
- config export/import
- update checker
- PWA/mobile shell

## Architecture

### Core workflow

```text
Discover / DMM Inbox / Music Hub
            ↓
         ArrNexus
            ↓
   routing + policy + jobs
            ↓
Radarr / Sonarr / Lidarr / Prowlarr
            ↓
InfiniDysk / Decypharr / Real-Debrid
            ↓
        Jellyfin
```

### Ecosystem workflow

```text
                    ┌─ InfiniDysk
                    ├─ DUMB
                    ├─ Profilarr
                    ├─ NeutArr
                    ├─ Cleanuparr
ArrNexus connector ─┼─ Maintainerr
     platform        ├─ Bazarr
                    ├─ Streamystats
                    ├─ Zilean
                    ├─ Riven
                    └─ community JSON connectors
```

The objective is orchestration, not source-code aggregation.

## DUMB namespace requirement

The Debian host may not contain `/mnt/debrid`. DUMB can create that mount tree inside the Arr process mount namespace.

ArrNexus therefore reads through:

```text
/proc/<main-radarr-pid>/root/mnt/debrid
```

while symlinks written to media libraries retain normal DUMB-visible targets such as:

```text
/mnt/debrid/decypharr/__all__/Movie (2026)/Movie.mkv
```

The stack requires `pid: host` and `SYS_PTRACE`. It does **not** require `privileged: true`.

## Portainer / Docker Compose

The included Compose file contains no application secrets:

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

Build/start and open:

```text
http://YOUR-SERVER:8484
```

On a fresh database the setup wizard creates the first administrator. Service URLs/API keys, mounts, connectors, provider settings, themes, users and policy are then managed through the UI.

### Recommended setup order

1. **Connections** — Radarr, Sonarr, Lidarr, Prowlarr, Jellyfin and optional Seerr.
2. **Settings** — confirm logical DUMB paths/mounts.
3. **Ecosystem** — enable InfiniDysk and any optional companion services.
4. **Debrid / DMM** — connect Real-Debrid if desired.
5. **Quality Lab** — validate release policy against real Prowlarr results.
6. **Self-Healing** — scan first; only enable AutoPilot after reviewing the results.
7. **Problem Centre** — confirm overall service health.

## Upgrading from v4.0

Stop v4, extract v5 and copy only the persistent `data` directory:

```bash
cd /opt/dmm-arr-router/arrnexus-v4.0
docker compose down

cd /opt/dmm-arr-router
unzip /home/<user>/arrnexus-v5.0.zip

mkdir -p arrnexus-v5.0/data
cp -a arrnexus-v4.0/data/. arrnexus-v5.0/data/

cd arrnexus-v5.0
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=150 arrnexus
```

Do not copy an old `.env` as a requirement. Existing SQLite configuration migrates forward automatically.

## Validation

Run:

```bash
python validate.py
```

The v5 validator covers:

- Python compilation and Jinja templates
- application startup/authenticated pages
- Discover regression protection
- Music provider source isolation
- TV pack parsing and coverage UI
- pack-aware release scoring
- connector JSON SDK installation/rendering
- InfiniDysk Prometheus metric filtering
- Quality Lab release parsing/explanation
- Self-Healing graceful no-Arr behaviour
- PWA resources

See `VALIDATION.md` for the full report and environment limits.

## Security

- Never commit `/data/router.db`, OAuth credentials, API keys or SMTP secrets.
- Connector/API secrets are stored as secret application settings and masked in normal UI views.
- Diagnostics/config exports omit or mask secrets.
- Real-Debrid source media is never deleted during import.
- Self-Healing does not delete media/downloads.
- Undo removes only symlinks recorded as created by ArrNexus.
- The file browser is read-only.
- Community connectors are JSON-only and cannot execute Python code.
- Put ArrNexus behind an appropriate reverse proxy/authentication layer before exposing it outside a trusted network.

## Third-party integration / licensing approach

ArrNexus integrates with external applications through HTTP APIs, service health endpoints and user-configured connectors. It does not redistribute those projects inside the ArrNexus image. Review each project's own licence and documentation before enabling or redistributing companion services.
