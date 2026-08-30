# ArrNexus v4.0 validation report

This package was validated after the Discover, music-source and smart-TV-pack merge.

## Passed

- Full Python compilation and AST parsing for `app/`.
- JavaScript syntax check for `app/static/app.js`.
- Jinja compilation of every template using ArrNexus' actual runtime environment and custom `human_size` filter.
- Fresh-database first-run administrator creation.
- Authenticated smoke rendering of Dashboard, Settings, Profile, Logs, Jobs, Routing Rules, Libraries, Connections, Download Queue, Scraping, Maintenance, Problem Centre, Timeline, Discover, Music Hub and Debrid/DMM without live service credentials.
- PWA manifest/service-worker delivery.
- **Discover regression test** using deterministic fake Seerr + specialist-library shelves and an Arr metadata search result. This specifically catches the shelf-dictionary `items` template bug that caused the prior Discover HTTP 500.
- **Music provider isolation regression test** proving Apple and Deezer tabs render different provider-owned content and do not leak/relabel each other's highlights.
- TV release parser tests for full-series packs, season packs, individual episodes and dotted `Season.1-2` naming.
- TV pack-mode filtering and smart complete-pack/season-pack selection.
- Debrid/DMM TV render with full-series controls, Real-Debrid cache badge, Sonarr coverage visualizer, `Get entire show intelligently` and `Get missing seasons only` actions.
- Pack-aware release policy: a large full-series pack can use the full-series ceiling while the same size is correctly rejected for one episode.
- Standalone Uvicorn boot and HTTP checks for `/api/health` and `/setup`.
- Exact development credentials previously used while building ArrNexus were searched for and are not present in this package.

## Runtime behavior intentionally not faked as a guarantee

The validator does not claim that third-party public APIs are always available. Apple/iTunes, MusicBrainz, ListenBrainz, Audius, Deezer, SoundCloud, Spotify, Jamendo, Last.fm, Seerr, Prowlarr and Real-Debrid can change or be unavailable independently. ArrNexus is designed to isolate these failures rather than crash unrelated pages.

## Environment limitation

The validation environment does not provide a Docker CLI/daemon, so an actual Docker image build could not be executed here. The application itself was booted with Uvicorn and the final package should still be built on the target server with:

```bash
docker compose up -d --build
```

## Standout feature set included

- Per-title operational timeline.
- Explainable release policy/scoring engine.
- Smart TV full-series / season-pack / episode detection.
- Sonarr season coverage and missing-only acquisition.
- Real-Debrid cache-aware filtering/badges.
- Problem Centre and library-health score.
- Sanitized diagnostics bundle.
- ntfy, Gotify, Discord webhook and email notifications.
- Daily rolling database backups and manual backups.
- Sanitized configuration export/import.
- GitHub release update checker.
- Safe JSON-only community catalogue provider SDK.
- Installable PWA/mobile shell.
- Per-user request permissions/daily limits.
- UI-managed Arr/Prowlarr/Jellyfin/Seerr connections and logical mounts for Portainer-first deployment.
