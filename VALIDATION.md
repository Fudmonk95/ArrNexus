# ArrNexus v9.3.0-beta Validation Report

Release target: **ArrNexus 9.3.0-beta**

This file records tests performed on the source release before packaging. A second validation pass is required after extracting the exact finished ZIP into a clean directory.

## Regression chain

`validate.py` executes:

```text
v9.3
 -> validate_v92.py
    -> validate_v91.py
       -> validate_v9.py
          -> validate_v8.py
             -> validate_v7.py
```

Source-tree result before final packaging:

```text
PASS: ArrNexus v7.0 Spotify personal library, native InfiniDysk telemetry, English Language Guard, Prowlarr indexer control, Sonarr TV search, strict connectors and performance caches
PASS: ArrNexus v8 regression suite retained: v7 regressions + AIOStreams full-config preview/apply/backup/rollback/search integration
PASS: ArrNexus v9.0.0-beta retains v7/v8 regressions and adds branded public onboarding, provider-neutral acquisition, readiness scoring and safe multi-provider AIOStreams wiring
PASS: ArrNexus v9.1 regression layer retained
PASS: ArrNexus v9.2 regression layer retained: production-data Dashboard caching, public documentation and measurable performance diagnostics
PASS: ArrNexus v9.3.0-beta retains v7/v8/v9/v9.1/v9.2 regressions and adds targeted slow-route snapshots, repaired Music API configuration/artist loading, Plex/Emby/custom media servers, persistent Public Home navigation and complete source/Git/Portainer/GHCR deployment documentation
```

## v9.3 deterministic checks

- Python application compilation
- Jinja compilation using the real ArrNexus template environment
- fresh SQLite initialization / first administrator setup
- public landing page
- Music API Settings route and credential persistence
- Spotify/SoundCloud/Jamendo/Last.fm configuration UI markers
- Plex and Emby connection persistence
- local deterministic Plex XML probe/parser
- local deterministic Emby system-info JSON probe/parser
- custom external media-server persistence and secret masking
- example/documentation-domain music URL rejection
- stale snapshot reuse
- Maintenance independent work runs concurrently
- InfiniDysk health/queue/history/overview work runs concurrently
- Music Artist independent Lidarr/metadata/artwork work runs concurrently
- v9.3 Public Home / Music API Settings persistent navigation
- full source/Git/Portainer/GHCR installation documentation
- v9.3 CSS/service-worker asset version markers

## Performance architecture verified by source/tests

- DMM Inbox short snapshot and deferred per-item Jellyfin lookup
- shared broken-link snapshot
- Maintenance worker-thread fan-out
- Problem Centre concurrent namespace/link/service probes
- Stack Readiness concurrent bounded application checks
- InfiniDysk per-window stale-while-revalidate snapshots
- cached Lidarr artist inventory
- Music Artist concurrent metadata pipeline
- persistent-shell client navigation without automatic sidebar crawling
- route timing headers and `slow_request` logging retained

## Live third-party limitations

The offline validator does **not** claim that these external services were live-authenticated from the packaging environment:

- Spotify OAuth against the user's real Spotify developer application
- Plex/Emby against the user's personal servers
- AIOStreams against the user's live instance
- InfiniDysk/Decypharr/DUMB against the user's live stack
- every supported provider

Those checks belong to live beta deployment.

## Docker

Docker availability must be checked separately in the packaging environment. If Docker is unavailable, the release must **not** claim `docker compose build` was executed. The deployment host should run:

```bash
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=200 arrnexus
```

## Final package gate

Before this release is handed off, the exact finished ZIP must still pass:

1. source/package secret scan
2. removal of `__pycache__`, `.pyc`, `.env`, virtualenvs and runtime state
3. Python compile
4. Jinja compile
5. JavaScript syntax check
6. clean real-Uvicorn `/api/health` and `/setup` HTTP 200 smoke
7. authenticated important-page smoke
8. ZIP creation
9. extraction of the exact ZIP into another clean directory
10. `validate.py` from that extracted directory
11. JavaScript syntax check from the extracted package
12. SHA-256 calculation of the exact ZIP

Do not promote `9.3.0-beta` to stable until live stack tests are complete.
