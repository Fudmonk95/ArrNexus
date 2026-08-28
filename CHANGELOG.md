# Changelog

## 10.1.0-beta — Language Cleanup & Library Consolidation

- Language Guard rejections are now tracked separately from genuine import errors.
- Added distinct `Language check failed` and `Language rejected` DMM states.
- DMM Inbox snapshots are invalidated immediately after language checks/import outcomes.
- Added optional exact-match Real-Debrid cleanup for rejected Language Guard sources; fuzzy/ambiguous matches fail closed and are retained.
- Added a `rejected` job counter and `complete_with_rejections` terminal state.
- Added Maintenance → Library Consolidation: scans every managed movie/TV symlink, groups duplicate movie parts/episodes, ranks candidates by language/resolution/source/HDR/codec/audio/size, and previews KEEP/REMOVE decisions.
- Consolidation uses a stale-preview digest and refuses to apply if the library changed after preview.
- Optional orphan-provider cleanup is off by default and only considers sources made unreferenced by the exact consolidation operation.
- Added v10.1 regression validation and retained v10 → v7 validation chain.

## 10.0.0-beta — Native Updates & Product UI

- Added the v10 native self-update architecture. ArrNexus can check the official GitHub Releases feed, notify administrators when a newer release is available, verify the release SHA-256, create a transaction-safe SQLite backup, safely extract and validate the new runtime, then restart itself through the in-container bootstrap supervisor.
- Added persistent runtime staging under `/data/runtime` with current/previous release tracking and automatic startup rollback when a newly selected runtime fails health checks.
- The self-updater deliberately does **not** require the Docker socket, Portainer, Watchtower or another updater service. Releases that change the base image/bootstrap can still require a normal Docker rebuild.
- Reworked **Connections** into compact collapsed service accordions. Optional/disabled services remain visible without filling the page with unused configuration forms.
- Reworked **Ecosystem** with the same collapsed/disabled service treatment for a cleaner deployment-specific view.
- Added a product-wide near-black visual layer with white/grey surfaces and restrained purple/cyan ArrNexus accents, replacing the older blue-heavy card treatment.
- Rebuilt the public landing page to match the redesigned GitHub presentation and new ArrNexus hero, architecture, core-features and quick-start artwork.
- Updated the Help Centre and generated User Guide with native-update, release ZIP and rollback guidance.
- Retained and re-ran the complete v7 → v8 → v9 → v9.1 → v9.2 → v9.3 → v9.4 regression chain before the v10 validation layer.

## 9.4.0-beta — Documentation & Guided Operations

- Added a public in-app **Help Centre** at `/help`.
- Added a context-sensitive `?` Help button and Help & Guides sidebar entry throughout the authenticated application.
- Added 43 structured guides covering prerequisites, setup, normal use, success criteria, troubleshooting and safety/privacy.
- Added detailed Spotify application + per-user OAuth instructions, including the exact callback shown for the current ArrNexus address, exact redirect matching and current Development Mode allowlist/Premium notes.
- Added `docs/USER_GUIDE.md`, generated from the same catalogue as the web Help Centre.
- Added `docs/DOCUMENTATION_AUDIT.md` mapping application routes/actions to Help coverage.
- Added release validation that fails when primary pages lose contextual Help or core documentation is missing.
- Retained all v9.3 performance, Music, Plex/Emby/custom media-server and earlier regression layers.

# ArrNexus Changelog

## 9.3.0-beta — Music, Media Servers & Targeted Performance

### Performance

- Added targeted stale-while-revalidate snapshots for the routes shown as slow by live `performance / slow_request` logs:
  - DMM Inbox
  - Maintenance
  - Problem Centre
  - Stack Readiness
  - InfiniDysk
  - Music Artist
- Added a shared broken-link snapshot so Maintenance and Problem Centre do not independently repeat the same expensive symlink walk.
- Maintenance now runs broken-link, DMM source, source→link and import-state work concurrently on worker threads.
- Problem Centre now runs namespace resolution, broken-link state and Arr service probes concurrently.
- InfiniDysk now requests health, queue, history and native Overview telemetry concurrently with bounded timeouts and per-window snapshots.
- Music Artist now runs Lidarr library, Lidarr lookup, MusicBrainz and artwork work concurrently; stable Lidarr artist inventory is reused for 45 seconds.
- Specialist Arr existing-match checks remain concurrent across discovered Arr instances.
- Existing configured installs receive a staggered server-side warm-up for commonly expensive snapshots after startup rather than browser-side automatic crawling.
- Retained `Server-Timing`, `X-ArrNexus-Elapsed-Ms` and `performance / slow_request` diagnostics from v9.2.

### Music

- Added a dedicated administrator-only **Music API Settings** page.
- Added Music API Settings to the persistent Settings navigation.
- Music Hub now exposes direct Music API Settings and Public Home controls.
- Spotify, SoundCloud, Jamendo and Last.fm application credentials can be managed from the dedicated page.
- Spotify OAuth guidance now points to Music API Settings.
- Added caching for provider-specific featured/search results.
- Added safe external music URL validation; known documentation/example domains are refused rather than opened as if they were live catalogues.
- Reduced optional external metadata timeouts so catalogue/artwork services cannot serially block the artist page.

### Media servers

- Retained Jellyfin as the deepest current media-server integration.
- Added first-class **Plex Media Server** connection support using `X-Plex-Token` verification.
- Added first-class **Emby Server** connection support using the protected system-info API and `X-Emby-Token`.
- Added a safe **External / custom media server** connector with:
  - name
  - base URL
  - health/status path
  - no-auth, bearer, custom-header or query-parameter authentication
  - privately stored secret/token
- Custom media-server connectors perform bounded HTTP probes and do not execute arbitrary third-party code.
- Added Plex/Emby media-server entries to diagnostics connection summaries and Stack Readiness checks.

### Navigation / UI

- Added an always-visible Public Home button to the authenticated top bar.
- Added **Public Home / About** to the sidebar footer.
- Updated the authenticated header wording from Jellyfin-specific to generic **Media Servers**.
- Bumped CSS/JavaScript/service-worker assets to v9.3 so browsers do not keep stale v9.2 assets.
- Added snapshot-age/refresh indicators to expensive operational pages.
- Extended the product-wide black ArrNexus visual lock to new v9.3 connection/music/snapshot components.

### Documentation / deployment

- Rewrote the public README for v9.3.
- Expanded the public landing page installation guide.
- Documented host validator setup using `python3-venv` and a local `.venv` rather than Debian system Python.
- Added explicit installation paths for:
  - release ZIP + Docker Compose source build
  - Git clone + Docker Compose source build
  - Portainer Git stack
  - future official GitHub Container Registry image
  - Portainer Web Editor / Stack file using the future official image
- Added Plex, Emby and external media-server architecture/documentation.
- Marked GHCR examples as templates until an official image is actually published.

### Validation

- Preserved v9.2, v9.1, v9, v8 and v7 regression suites.
- Added deterministic v9.3 checks for:
  - Music API Settings UI/save
  - placeholder music URL rejection
  - Plex XML API probe
  - Emby JSON API probe
  - custom media-server secret masking
  - Plex/Emby connection persistence
  - stale snapshot reuse
  - Maintenance concurrency
  - InfiniDysk concurrency
  - Music Artist concurrency
  - Public Home navigation
  - v9.3 static/service-worker versioning
  - Git/Compose/Portainer/GHCR installation documentation

## 9.2.0-beta — Reliability, Documentation & Performance

- Fixed Dashboard HTTP 500 on real upgraded databases by converting `sqlite3.Row` history objects to plain dictionaries before snapshot caching.
- Added degraded Dashboard fallback and performance route timing/logging.
- Expanded public documentation and host virtualenv validation guidance.
- Removed aggressive automatic browser prefetch of expensive sidebar routes.

## 9.1.0-beta — Unified Product UI & Navigation Performance

- Applied the public ArrNexus visual system across authenticated pages.
- Removed user-selectable theme switching from the product UI.
- Added persistent-shell navigation, page cache, request de-duplication and stale-while-revalidate client navigation.
- Added safe public source-release export.

## 9.0.0-beta — Product / Onboarding / Providers

- Added original ArrNexus branding and public landing page.
- Added guided first-run onboarding and Stack Readiness.
- Added provider-neutral Provider Registry and multi-provider AIOStreams wiring.

## 8.0.0-beta — AIOStreams Bridge

- Added safe AIOStreams full-config preview/apply/backup/rollback/search integration while retaining v7 functionality.

## 7.0 — Spotify, Language Guard, Telemetry & Performance

- Added Spotify personal OAuth, Language Guard, native InfiniDysk telemetry, Prowlarr indexer control, corrected Sonarr season search, strict connector verification and performance caches.
