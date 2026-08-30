# ArrNexus Changelog

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
