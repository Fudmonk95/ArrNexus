# ArrNexus Changelog

## 9.0.0-beta — Product, Onboarding & Provider Architecture

### Brand and public experience
- Added an original ArrNexus wordmark and application icon.
- Added a safe public landing/About experience at `/`.
- Moved the authenticated control centre to `/dashboard`.
- Added new branded first-run setup and login experiences.
- Added PWA branding/cache updates for the v9 assets.
- Shifted the default ArrNexus visual system toward near-black/white with restrained cyan/purple accents while retaining dense operational pages.

### Guided onboarding
- First administrator creation now redirects to `/onboarding` rather than claiming setup is complete.
- Added environment/namespace, application, provider, mount and readiness stages.
- Added bounded live service verification only on explicit onboarding/readiness pages.
- Added `/readiness` and a low-cost Dashboard readiness summary.

### Provider-neutral architecture
- Added `/providers` provider registry.
- Added Real-Debrid, TorBox, Premiumize, AllDebrid, Debrid-Link, EasyDebrid, Debrider, Offcloud, put.io, PikPak, Seedr, Easynews, NzbDAV/InfiniDysk, AltMount, Stremio NNTP, StremThru Newz, AIOStreams Native and Torrin identities.
- Provider credentials are stored privately and masked in the UI.
- Added conservative migration of clearly named legacy Real-Debrid/NzbDAV settings into the provider registry.
- Complex structured AIOStreams provider models are not guessed or flattened unsafely.

### AIOStreams
- Extended Auto-Wire from Real-Debrid/NzbDAV-specific discovery to enabled provider-registry services.
- Generic provider merge only fills missing AIOStreams user credential fields.
- Preserves existing AIOStreams credentials, unrelated userData and automatic service selection.
- Keeps v8 stale-preview, pre-write backup, verified full PUT, rollback backup and search redaction protections.

### Regression protection
- Preserved `validate_v7.py`.
- Added retained `validate_v8.py` regression suite.
- New `validate.py` runs v8/v7 first, then v9-specific tests.
- Version remains `9.0.0-beta` pending live deployment verification.

## 8.0.0-beta — AIOStreams Bridge
- Added administrator-only AIOStreams Bridge.
- Added full User API GET/PUT workflow with digest preview, stale protection, backups, rollback and redacted search diagnostics.
- Added Prowlarr/Real-Debrid/NzbDAV conservative Auto-Wire behavior.

## 7.0 — Corrected operational baseline
- Added personal Spotify OAuth aggregation, Language Guard, native InfiniDysk telemetry, Prowlarr indexer control, season-correct Sonarr search, stricter connector verification and performance caches.
