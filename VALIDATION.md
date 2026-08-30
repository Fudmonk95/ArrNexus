# ArrNexus v9.1.0-beta Validation Report

Release target: **ArrNexus 9.1.0-beta**

This report records deterministic/offline checks run against the release source. It does not claim live verification against a user's Debian/DUMB/Arr/provider environment.

## Regression chain

`validate.py` executes `validate_v9.py`, which executes `validate_v8.py`, which executes the preserved `validate_v7.py`.

Expected result:

```text
PASS: ArrNexus v7.0 Spotify personal library, native InfiniDysk telemetry, English Language Guard, Prowlarr indexer control, Sonarr TV search, strict connectors and performance caches
PASS: ArrNexus v8 regression suite retained: v7 regressions + AIOStreams full-config preview/apply/backup/rollback/search integration
PASS: ArrNexus v9.0.0-beta retains v7/v8 regressions and adds branded public onboarding, provider-neutral acquisition, readiness scoring and safe multi-provider AIOStreams wiring
PASS: ArrNexus v9.1.0-beta retains v7/v8/v9 functionality and adds one product-wide UI, faster stale-while-revalidate navigation, dashboard snapshots and safe public release downloads
```

## v9.1 deterministic checks

- public landing page renders unauthenticated
- landing page contains expanded product/feature/workflow/download content
- public page does not expose private Dashboard counts
- current-build public source ZIP endpoint returns a valid archive
- public source archive includes application source + validator
- public source archive excludes `/data`, `.env`, session secrets, databases, AIOStreams backups, bytecode and cache directories
- SHA-256 endpoint is present
- first administrator still enters guided onboarding
- Provider Registry still masks secrets
- provider-neutral AIOStreams merge still preserves existing remote provider credentials
- profile no longer exposes theme switching
- Stack Readiness authorization remains intact
- non-admin users remain denied administrator-only provider/readiness/onboarding pages
- base application shell no longer selects a per-profile theme
- black v9.1 design-system markers are present
- client soft-navigation includes stale-while-revalidate, in-flight de-duplication and idle prefetch
- server Dashboard includes short-lived stale-while-revalidate snapshot caching
- source/link/library filesystem calls are moved through worker threads on the Dashboard path

## Retained v9/v8/v7 coverage

The retained suites continue to cover:

- public/private product split and guided onboarding
- Provider Registry
- Stack Readiness
- AIOStreams stale-preview refusal / backup / apply / rollback / redaction
- Prowlarr URL/API-key reuse
- provider credential preservation
- Spotify personal OAuth aggregation logic
- Language Guard
- native InfiniDysk telemetry
- season-correct Sonarr search
- Prowlarr management
- strict connectors
- namespace/source/link/library caches

## Docker status

Docker availability is checked during release packaging. If Docker is unavailable in the packaging environment, `docker compose config` and image build are explicitly recorded as **not run**, not as passed.

## Live checks required before stable promotion

- `docker compose config` on the target host
- image build/start
- v9.0 persistent-data copy
- navigation responsiveness against the real Arr/DUMB namespace
- existing Arr, DMM, Jellyfin, InfiniDysk and music workflows
- Provider Registry migration/state
- real AIOStreams preview/apply/rollback
- real Spotify OAuth where configured
- public source download endpoint from the deployed container

Do not promote `9.1.0-beta` to stable until live checks pass.

## Packaging-environment results for this build

Before the final ZIP was created:

- Python compilation: **PASS**
- real Jinja compilation through `validate.py`: **PASS**
- JavaScript syntax (`node --check`, Node v22.16.0): **PASS**
- v7 regression suite: **PASS**
- v8 regression suite: **PASS**
- v9 regression suite: **PASS**
- v9.1 regression suite: **PASS**
- clean-copy real Uvicorn start: **PASS**
- `GET /api/health`: **HTTP 200**
- `GET /setup`: **HTTP 200**
- `GET /`: **HTTP 200**
- public source release endpoint: **HTTP 200** and source/runtime exclusion inspection **PASS**
- Docker executable in packaging environment: **NOT AVAILABLE**

The final release process then stages a bytecode-free source tree, performs a secret/private-artifact scan, creates the versioned ZIP, extracts that exact ZIP into a separate directory and reruns the full validator and JavaScript check there.
