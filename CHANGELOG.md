# ArrNexus Changelog

## 9.2.0-beta — Reliability, Documentation & Performance

### Dashboard production-data fix
- Fixed an HTTP 500 on upgraded/live databases caused by `copy.deepcopy()` receiving cached `sqlite3.Row` objects from recent imports, jobs and activity.
- Dashboard snapshot rows are normalised to plain dictionaries before caching.
- Added a migrated/non-empty database regression that reproduces the original failure.
- Added degraded Dashboard rendering plus diagnostic logging so a future integration failure does not become an opaque Internal Server Error.

### Public documentation
- Rebuilt `/` from the detailed public README structure rather than a short marketing summary.
- Added problem statement, project origin, reference architecture, requirements matrix, installation, upgrade, Python virtualenv validation, Docker networking, integrations, DMM/virtual-media explanation, acquisition strategies, feature guide, security and troubleshooting.
- Kept the public current-build ZIP and SHA-256 controls.

### Performance
- Removed v9.1's automatic idle crawl/prefetch of expensive sidebar destinations.
- Retained persistent-shell soft navigation, in-flight request de-duplication and stale-while-revalidate caching.
- Increased recently visited page freshness and changed hover prefetch to sustained user intent.
- Added `Server-Timing` and `X-ArrNexus-Elapsed-Ms` response headers.
- Added `performance / slow_request` log events for requests taking 1.5 seconds or longer.

### Regression protection
- Added `validate_v91.py` to retain v9.1 regression coverage.
- v9.2 `validate.py` runs v9.1 → v9 → v8 → v7 before v9.2-specific tests.
- Added non-empty Dashboard history, degraded Dashboard, public documentation and route timing tests.

## 9.1.0-beta — Unified UI & Performance

### Product-wide visual identity
- Removed theme switching from the public/private UI.
- Existing theme database fields remain only for backwards compatibility.
- Applied the black ArrNexus visual system across the sidebar, top bar, panels, forms, tables, cards, settings, profile and operational pages.
- Restyled password recovery to match the v9 branded login/setup experience.
- Kept the ArrNexus logo/icon as the single product identity.

### Public product page
- Expanded `/` using the project README feature set.
- Added architecture, feature, integration, workflow, safety and release sections.
- Added public current-build source download and SHA-256 endpoints.
- Public source exporter excludes persistent data, secrets, databases, backups, virtualenvs and runtime caches.

### Performance
- Extended client soft-navigation cache to 45 seconds.
- Added client stale-while-revalidate reuse for recently visited pages.
- Added in-flight navigation request de-duplication.
- Added pointer-down/hover prefetch and low-priority idle prefetch of common routes.
- Added short-lived server-side Dashboard snapshot caching.
- Dashboard now serves stale state immediately while refreshing expired snapshots in the background.
- Moved source/link/library namespace work off the async event loop with worker threads where practical.
- Shortened Dashboard service fan-out timeouts and removed mandatory fresh service fan-out from every Dashboard navigation.

### Regression protection
- Preserved `validate_v7.py`.
- Preserved `validate_v8.py` and extended its version acceptance for v9.1.
- Preserved v9.0 validation as `validate_v9.py`.
- v9.1 `validate.py` runs v9 → v8 → v7 before new tests.
- Added public release-export hygiene checks, single-theme checks and v9.1 performance architecture checks.

## 9.0.0-beta — Product, Onboarding & Provider Architecture
- Added original ArrNexus branding and public landing page.
- Added guided onboarding and Stack Readiness.
- Added provider-neutral registry and multi-provider AIOStreams Auto-Wire.
- Preserved v8/v7 acquisition and safety functionality.

## 8.0.0-beta — AIOStreams Bridge
- Added administrator-only AIOStreams Bridge.
- Added full User API GET/PUT workflow with digest preview, stale protection, backups, rollback and redacted search diagnostics.
- Added Prowlarr/Real-Debrid/NzbDAV conservative Auto-Wire behaviour.

## 7.0 — Corrected operational baseline
- Added personal Spotify OAuth aggregation, Language Guard, native InfiniDysk telemetry, Prowlarr indexer control, season-correct Sonarr search, stricter connector verification and performance caches.
