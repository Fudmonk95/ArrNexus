# ArrNexus v9.2.0-beta Validation Report

Release target: **ArrNexus 9.2.0-beta**

This report records deterministic/offline checks run against the release source. It does not claim live verification against a user's Debian/DUMB/Arr/provider environment.

## Regression chain

`validate.py` executes the v9.1 regression layer, which executes v9 → v8 → v7 coverage before the v9.2-specific tests.

Expected result:

```text
PASS: ArrNexus v7.0 Spotify personal library, native InfiniDysk telemetry, English Language Guard, Prowlarr indexer control, Sonarr TV search, strict connectors and performance caches
PASS: ArrNexus v8 regression suite retained: v7 regressions + AIOStreams full-config preview/apply/backup/rollback/search integration
PASS: ArrNexus v9.0.0-beta retains v7/v8 regressions and adds branded public onboarding, provider-neutral acquisition, readiness scoring and safe multi-provider AIOStreams wiring
PASS: ArrNexus v9.1 regression layer retained inside v9.2
PASS: ArrNexus v9.2.0-beta retains v7/v8/v9/v9.1 regressions and fixes production-data Dashboard caching while adding README-driven public documentation and measurable performance diagnostics
```

## v9.2 deterministic checks

- public landing page renders unauthenticated
- public page includes README-derived architecture, requirements, install, virtualenv validation, networking, feature guide, acquisition and troubleshooting sections
- public download remains source-only and excludes persistent/private artifacts
- request timing headers are emitted
- administrator setup still works
- a non-empty database is seeded with import/activity/job rows before Dashboard is opened
- Dashboard renders HTTP 200 with real SQLite history rows
- Dashboard deliberately forced into a snapshot failure still returns HTTP 200 in degraded mode
- cached Dashboard history rows are normalised to plain dictionaries
- aggressive idle route prefetch is absent
- intent-prefetch and stale-while-revalidate navigation remain
- v9.2 README includes the host virtualenv validation procedure

## Docker status

If Docker is unavailable in the packaging environment, `docker compose config` and image build are recorded as **not run**, not as passed. They remain target-host checks.

## Live checks required before stable promotion

- `docker compose config` on the target host
- image build/start
- v9.1 persistent-data copy
- Dashboard against the real non-empty database
- navigation timing against the real Arr/DUMB namespace
- check Unified Logs for `performance / slow_request` entries to identify remaining slow routes
- existing Arr, DMM, Jellyfin, InfiniDysk, music and provider workflows
- real AIOStreams preview/apply/rollback where configured
- public source download from the deployed container

Do not promote `9.2.0-beta` to stable until live checks pass.
