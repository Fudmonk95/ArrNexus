# ArrNexus v6.0 — Acquisition Intelligence & Unified Operations

ArrNexus is a self-hosted media-control layer for Radarr/Sonarr/Lidarr stacks using DUMB, InfiniDysk/NzbDAV, Decypharr/Real-Debrid, Prowlarr, Jellyfin and optional Seerr discovery.

v6 focuses on the parts that need to be trustworthy in daily use: **which provider acquires a title, whether service credentials are actually valid, and what the logs are really telling you**. It also introduces a redesigned grouped navigation model inspired by the clarity of InfiniDysk while retaining ArrNexus as its own product.

ArrNexus remains Portainer/Docker Compose first. A normal deployment needs no application secrets in an `.env`: create the administrator in the browser and configure services, credentials, mounts, policies and integrations through the UI.

## What's new in v6

### Acquisition Strategy — Usenet and Debrid are now explicit

Discover no longer relies solely on a broad Radarr/Sonarr search to choose the source. Each request can choose:

- **Automatic** — compare acceptable Usenet and torrent/debrid candidates and grab one best release.
- **Debrid first** — try torrent/debrid candidates first, then fall back to Usenet.
- **Usenet first** — try Usenet first, then fall back to torrent/debrid.
- **Debrid only** — never choose an NZB.
- **Usenet only** — never choose a torrent.
- **Fastest** — favour verified Real-Debrid cache hits when possible.
- **Best quality / score** — compare acceptable candidates using ArrNexus scoring.

ArrNexus requests interactive releases from the target Radarr/Sonarr instance, preserving the indexers and tags that Arr/Prowlarr already allow that instance to see. It then grabs **one** release through the Arr API. Protocol still controls the normal downstream download-client routing:

```text
Usenet result  → InfiniDysk / SAB-compatible client
Torrent result → Decypharr / qBittorrent-compatible client → Real-Debrid
```

If ArrNexus cannot select an acceptable interactive release, an optional final native Arr-search fallback can be enabled under **Settings → Acquisition**. In that fallback, Arr itself resumes responsibility for release selection.

#### Example: Usenet first

```text
Discover request
      ↓
Add / monitor in Radarr or Sonarr
      ↓
Interactive release search
      ↓
Acceptable Usenet result?
  ├─ yes → grab one NZB → InfiniDysk
  └─ no  → evaluate torrent/debrid results
                    ↓
              grab one torrent → Decypharr
```

Acquisition activity records the strategy, candidate counts, selected protocol/indexer and fallback reason.

### Verified service authentication

v5 could incorrectly report a connector as healthy when only its public web endpoint was reachable. v6 deliberately separates:

```text
Service reachable
Authentication valid
API functional
Version
Latency
```

Built-in verification now includes:

- **InfiniDysk:** `/healthz` reachability plus an authenticated SAB queue request using the configured SAB API key.
- **Decypharr:** public `/version` reachability plus Bearer-token-protected `/api/torrents` verification.
- **AltMount:** username/password login followed by an authenticated management API request using the returned JWT session.
- Other connectors continue to use the safest capability their public API exposes.

Incorrect InfiniDysk/Decypharr credentials therefore fail verification rather than receiving a misleading green Connected state.

### Unified Logs

The previous operational-event table has become an interactive log console.

Sources:

- **ArrNexus** — application events stored by ArrNexus.
- **DUMB** — service log tails obtained from DUMB's `/logs` API, including selectable DUMB process names.
- **InfiniDysk** — Warning-and-above events exposed by InfiniDysk's documented SAB `warnings` operation.

The InfiniDysk view is intentionally described as a warnings feed, not a fake full-log mirror. DUMB process logs can be polled live from the browser.

Filters include origin, level, process/source and text search. Known problem rows are clickable and expand into an explanation and suggested action.

Current built-in diagnostics recognise common messages such as:

- VFS/cache `404 Not Found`
- `could not seek to byte position`
- missing/corrupt Usenet article failures
- Arr `not enough free space`
- connector/API authentication failures

This gives ArrNexus a useful troubleshooting layer without replacing the native logs in DUMB or InfiniDysk.

### Native Decypharr status

A new **Decypharr** page uses the authenticated Decypharr REST API to display:

- version
- managed torrent count
- connected Arr count
- repair state
- torrent summary
- broken repair/health entries when exposed by the installed version

### Redesigned navigation

v6 adopts a clearer grouped sidebar inspired by InfiniDysk's information hierarchy:

```text
OVERVIEW
  Dashboard
  Discover
  Music Hub

ACQUISITION
  Acquisition
  Debrid / DMM
  InfiniDysk
  Decypharr
  Download Queue

LIBRARY & AUTOMATION
  DMM Inbox
  Libraries
  Import Jobs
  Routing Rules
  Self-Healing
  Quality Lab

SYSTEM
  Problem Centre
  Maintenance
  Unified Logs
  Ecosystem

SETTINGS
  Connections
  Settings
```

The product remains **ArrNexus** by default, while the existing branding/title setting can be changed from the UI if a deployment wants a custom name.

## Ecosystem control retained from v5

The **Ecosystem** page contains built-in connector definitions for:

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

Connectors are API boundaries: ArrNexus does not bundle or copy those projects. A JSON-only connector SDK under `/data/connectors` allows additional services to advertise safe health/search/queue/etc. capabilities without executing third-party Python in the ArrNexus process.

### Native InfiniDysk operations

The existing InfiniDysk page continues to use documented public interfaces for:

- health
- SAB-compatible queue/history
- safe global queue pause/resume
- filtered Prometheus telemetry where available

The connector's SAB API key is now genuinely tested in v6.

### Quality Lab

Quality Lab continues to parse and compare:

- resolution / source / codec
- HDR / Dolby Vision
- audio indicators
- TV episode, season-pack and full-series structure
- size, seeders, Real-Debrid cache preference
- ArrNexus release-policy score and explanation

It can compare Prowlarr search results with the same engine. Profilarr/Recyclarr remain external configuration authorities rather than having their source logic copied into ArrNexus.

### Self-Healing

Self-Healing can scan discovered Arr instances for missing media, cutoff-unmet upgrades and queue/import warnings. Its optional AutoPilot is off by default, rate/maintenance-window bounded and deliberately non-destructive.

## Major capabilities retained

- Seerr-style Discover poster rails and specialist-library shelves
- source-isolated Music Hub providers
- DMM/Real-Debrid search and library
- Any / Full Series / Season Packs / Episodes TV pack modes
- smart complete-series and missing-season planning
- Sonarr season coverage visualiser
- Real-Debrid cache checks
- DMM Inbox metadata cleanup, posters, genre badges and duplicate grouping
- specialist Radarr/Sonarr routing
- Waiting / Imported / Duplicates / Upgrades / Ignored views
- bulk imports with route selection
- safe symlink imports and Undo
- persistent import jobs/progress
- unified Arr download queue
- routing rules and learned corrections
- broken-link scanner / orphan detector / repair tools
- read-only namespace file browser
- users, request limits and multiple themes
- SMTP password reset
- title timelines
- notification providers
- diagnostics bundle with secret redaction
- rolling SQLite backups
- config export/import
- update checker
- PWA/mobile support

## Architecture

```text
Discover / DMM / Music Hub
           ↓
       ArrNexus v6
           ↓
 routing + acquisition strategy + policy
           ↓
 Radarr / Sonarr / Lidarr / Prowlarr
       ↙                         ↘
 InfiniDysk                  Decypharr
    Usenet                  Real-Debrid
       ↘                         ↙
           virtual libraries
                  ↓
               Jellyfin
```

## DUMB namespace requirement

On DUMB installs the Debian host itself may not contain `/mnt/debrid`; the mount can exist inside the Arr process mount namespace. ArrNexus therefore follows the main Radarr process namespace through:

```text
/proc/<main-radarr-pid>/root/mnt/debrid
```

while symlinks written into library roots use normal DUMB-visible targets such as:

```text
/mnt/debrid/decypharr/__all__/Movie (2026)/Movie.mkv
```

The supplied stack requires `pid: host` and `SYS_PTRACE`, not `privileged: true`.

## Portainer / Docker Compose

The included Compose file keeps secrets out of the stack definition:

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

Open:

```text
http://YOUR-SERVER:8484
```

A fresh install creates its first administrator in the browser. Connections, connector credentials, mounts, Real-Debrid auth, acquisition policy, themes and users are then managed through ArrNexus.

### Recommended setup order

1. **Connections** — Radarr, Sonarr, Lidarr, Prowlarr, Jellyfin and optional Seerr.
2. **Settings → Mounts** — verify DUMB paths.
3. **Ecosystem** — verify InfiniDysk and Decypharr with their real credentials.
4. **Debrid / DMM** — link Real-Debrid if required.
5. **Settings → Acquisition** — choose the global provider strategy.
6. **Discover** — run one controlled test request and watch **Acquisition**.
7. **Unified Logs / Problem Centre** — confirm health and diagnose failures.
8. **Self-Healing** — scan first; enable AutoPilot only after reviewing results.

## Upgrade from v5.0

Stop v5, extract v6 and copy only the persistent `data` directory:

```bash
cd /opt/dmm-arr-router/arrnexus-v5.0
docker compose down

cd /opt/dmm-arr-router
unzip /home/<user>/arrnexus-v6.0.zip

mkdir -p arrnexus-v6.0/data
cp -a arrnexus-v5.0/data/. arrnexus-v6.0/data/

cd arrnexus-v6.0
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

The v6 validator includes regression and behaviour tests for:

- Python compilation and Jinja templates
- first-run setup/authenticated pages
- Discover regression protection
- Music-provider source isolation
- TV pack parsing and Sonarr coverage
- connector JSON SDK
- InfiniDysk telemetry parsing
- Quality Lab / Self-Healing
- pack-aware release policy
- acquisition ranking
- Usenet-first fallback behaviour
- exactly-one-release grab protection
- local mock-server verification that wrong InfiniDysk and Decypharr credentials fail
- correct InfiniDysk and Decypharr credentials pass
- Unified Logs diagnostic classification
- v6 Discover, Ecosystem, Settings and Decypharr UI smoke tests

See `VALIDATION.md` for the exact packaged-build report.

## Security

- Never commit `/data/router.db`, OAuth credentials, API keys, SMTP credentials or connector secrets.
- UI secrets are masked and diagnostics/config exports redact them.
- Community connectors are JSON-only and cannot execute arbitrary Python.
- DMM imports do not delete underlying Real-Debrid source media.
- Self-Healing does not delete downloads/media.
- Undo removes only symlinks recorded as created by ArrNexus.
- The namespace browser is read-only.
- Put ArrNexus behind appropriate authentication/reverse-proxy controls before exposing it outside a trusted network.

## Third-party integration approach

ArrNexus talks to companion applications through their documented APIs, compatibility APIs and health endpoints. It does not redistribute their code inside ArrNexus. Review each companion project's own licence and documentation when redistributing or enabling those applications.
