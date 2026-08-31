# Changelog

## 10.4.3-beta — Recovery Pipeline & Language Inbox Hotfix

- Changed RAR verification from one bulk archive test to **one independent extractor test per video member**, so a damaged season/member cannot prevent other media from being verified.
- Added per-member archive verification progress to background jobs.
- Moved Advanced TV Recovery split outputs from the legacy `/data/split-cache` default into the DUMB-visible recovered-media source root.
- Combined-season files recovered from RAR now place generated episode folders beside the recovered source under `/mnt/debrid/arrnexus-extracted` (or the configured DUMB-visible recovery root).
- Allowed Advanced TV Recovery to analyse ArrNexus recovered-media sources as well as normal DMM provider sources.
- Rebuilt the DMM **Language** view from unresolved source copies before title grouping so current-policy passes disappear immediately after re-check.
- Changed source-level Language Guard aggregation so any unknown/unlabelled/probe-failed member takes precedence over explicit failures elsewhere in the same source; mixed evidence is Manual Review and never destructive-safe.
- Bumped the Language Guard cache namespace again to force affected sources through the corrected source-level evaluator.
- Retained stable archive catalogue signatures, selective media-only extraction, external-ID idempotent Arr imports and all v10.4.2 safety boundaries.

## 10.4.2-beta — Stable Archive Identity Hotfix

- Fixed false **RAR source changed after preview** failures on Decypharr/DUMB virtual mounts.
- Removed transient `/proc/<pid>` paths and virtual `mtime` from archive source identity.
- Added stable logical-path + source-size fingerprints for scan/cache/identity continuity.
- Added content catalogue signatures from archive member path, listed size, packed size, encryption flag and CRC.
- Verification re-lists the archive after testing and refuses to cache results if the media catalogue changed during the job.
- Extraction performs a fresh catalogue comparison against the verified catalogue before recovering any media.
- Added best-effort migration for TMDb archive identities saved under v10.4/v10.4.1 fingerprints.
- Bumped the Language Guard result-cache schema so old false `language_rejected` decisions are re-evaluated under the fixed unknown-language rules.
- Added a separate Manual Review job counter/status so uncertain media is no longer reported as a confirmed language rejection.
- Added TMDb/TVDB external-ID preflight when importing to Radarr/Sonarr; already-owned titles are reused instead of failing on `MovieExistsValidator`, duplicate path, or equivalent series validation.
- Retained media-only, CRC-aware partial RAR recovery from v10.4.1.

## 10.4.1-beta — Selective RAR Recovery Hotfix

- Changed Archived Media Recovery from whole-archive pass/fail to **member-level video verification and partial recovery**.
- Added background **Verify media files** jobs so large/cloud-backed RAR integrity checks no longer block the browser request.
- Archive inspection now preserves useful media catalogues when 7-Zip reports structural errors, while keeping extraction disabled until individual video members are verified.
- Added exact per-file parsing for CRC/data errors; failed or unverified members cannot be selected for recovery.
- Recovery now extracts **only selected verified video files**; XML, SQLite, artwork, torrent padding, nested archives and unrelated support files are never unpacked by the normal workflow.
- Added post-recovery size checks and `ffprobe` video-stream validation before files are committed to the persistent recovered-media root.
- Added fingerprint-cached archive listings and filtered torrent padding from the normal UI/error output.
- Clarified **Recovered media source root** semantics: it stores persistent recovered bytes and must not be set to the final Sonarr/Radarr library.
- Preserved the original provider RAR in all recovery outcomes.

## 10.4.0-beta — Archived Media Recovery, Identity Resolution & Language Safety

- Fixed Language Guard false rejections for old/archive media whose audio tags are `und`, blank, mixed or unknown; uncertain metadata is Manual review and is never destructive-safe.
- Added fingerprint-bound administrator **Mark this source as English** overrides for verified old encodes; overrides expire automatically when source media changes.
- Made Real-Debrid rejected-source cleanup opt-in by default for fresh installs while retaining exact-match/dependency/current-policy safety.
- Reworked DMM TV grouping to be series-first: multiple season/source packs for the same Sonarr series are shown under one show card with season/source detail.
- Added **Archived Media Recovery**: scan DMM `__all__` for first-volume RAR/multipart sets, inspect contents before extraction, classify media/password/nested/non-media archives, ignore unwanted archives and extract only explicit administrator selections.
- Added archive safety gates for path traversal, archive-created symlinks, nested-only archives, password protection, source fingerprint changes, extraction-size ceilings and free-space margin.
- Added a DUMB-visible recovered-media root (default `/mnt/debrid/arrnexus-extracted`) so Sonarr/Jellyfin can resolve symlink targets from extracted RAR media.
- Added product-wide **TMDb media identity** settings/search and fingerprint-bound source identity overrides for ambiguous names such as `season-4_202405.rar`.
- Added source identity confidence and pre-import canonical filename previews.
- Added **Review naming & import** on Item Review so an administrator can correct title/type/year before the actual Arr match/import job.
- TMDb/source identity now feeds the real routing/import pipeline, not only display metadata.
- Added 7zip/unrar support to the Docker image for RAR recovery. A container rebuild is required before RAR inspection/extraction can work on installations whose existing image lacks an extractor.
- Updated Help Centre, User Guide, Documentation Audit and service-worker cache for v10.4.
- Added v10.4 validation while retaining v7 → v10.3 regression coverage.

## 10.3.0-beta — Archive Rescue & Advanced Media Recovery

- Added **Archive Rescue**: scan monitored Sonarr gaps, search Prowlarr's Internet Archive indexer, inspect real `.torrent` manifests and hand selected files to Real-Debrid.
- Added selective Real-Debrid file selection for archive torrents and fail-closed cleanup if selected manifest paths cannot be matched safely.
- Added **Advanced TV Recovery** with archive-style episode/season parsing, combined-season detection, chapter-aware splitting, lower-confidence runtime estimates requiring explicit confirmation, configurable staging and ffprobe output verification.
- Added explicit typed manual routes (`movie:*` / `tv:*`) so a selected TV destination cannot be reinterpreted as a movie route; restored TV Default and TV Kids in DMM bulk routing.
- Added DMM Inbox bulk Language Guard actions: Check selected, Check all unchecked and Force re-check all, plus persistent Re-check required state for stale policy results.
- Added direct and bulk safe deletion of confirmed rejected Real-Debrid sources with current-policy, fingerprint, dependency and exact-provider revalidation at apply time.
- Hardened Item Review so inspection failures render an ArrNexus diagnostic page instead of a bare HTTP 500.
- Reworked Trakt account linking around **Device OAuth** as the normal flow; moved application Client ID/Secret into Advanced setup and added pending/slow-down/denial/expiry handling.
- Rebuilt Dark/Light appearance around product-wide neutral surface/text tokens so Light uses dark typography and legacy navy/blue component surfaces no longer leak through.
- Updated Help Centre, generated User Guide, Documentation Audit and service-worker cache to v10.3.
- Added v10.3 regression coverage while retaining the complete v7 → v10.2 chain.

## 10.2.0-beta — Lists, Language Guard v2 & Product Appearance

- Added Lists & Watchlists automation with Trakt OAuth/watchlist/lists, IMDb, TMDb, Plex Watchlist, Simkl, RSS/Atom and Custom JSON adapters.
- Added per-list movie/TV/mixed routing to specialist Radarr/Sonarr destinations using existing ArrNexus acquisition strategies.
- Added list preview, manual sync, scheduled sync and run history; existing titles are never automatically moved between specialist libraries.
- Added atomic Trakt access/refresh token replacement for refresh safety.
- Added Language Guard v2 defaults: English audio required, English subtitles optional. Probe failures and unknown language metadata become Manual review and are never destructive.
- Added dependency-protected Provider Duplicate Cleanup with stale-preview digest checking and exact Real-Debrid deletion only after all surviving managed-link dependencies are gone.
- Added managed AIOMetadata health, masked user-config visibility, explicit manifest verification and AIOStreams relationship visibility.
- Removed historical multi-theme CSS and replaced it with exactly Dark and Light product appearances plus a permanent top-right toggle.
- Converted Providers and Libraries to collapsed summary-first layouts.
- Updated the service-worker cache to `arrnexus-static-v10.2`, Help Centre, User Guide, documentation audit and release documentation.
- Added the v10.2 validation layer while retaining the v7 → v10.1 regression chain.

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
