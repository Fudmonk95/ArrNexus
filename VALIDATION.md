# ArrNexus v9.0.0-beta Validation Report

Release target: **ArrNexus 9.0.0-beta**

This report records checks actually performed on the packaged v9 source. It does not claim live verification against the user's Debian/DUMB/Arr/provider environment.

## Regression suites

`validate.py` executes `validate_v8.py`, which executes the preserved `validate_v7.py`.

Observed development-tree result:

```text
PASS: ArrNexus v7.0 Spotify personal library, native InfiniDysk telemetry, English Language Guard, Prowlarr indexer control, Sonarr TV search, strict connectors and performance caches
PASS: ArrNexus v8 regression suite retained: v7 regressions + AIOStreams full-config preview/apply/backup/rollback/search integration
PASS: ArrNexus v9.0.0-beta retains v7/v8 regressions and adds branded public onboarding, provider-neutral acquisition, readiness scoring and safe multi-provider AIOStreams wiring
```

## v9-specific deterministic checks

- Public `/` landing page renders without authentication.
- Private `/dashboard` redirects an unconfigured instance to `/setup`.
- First administrator creation redirects to `/onboarding` and does not mark setup complete prematurely.
- New landing, setup, login, onboarding, provider and readiness templates compile using the real Jinja environment.
- ArrNexus v9 wordmark/icon files exist and are integrated into public/private UI and PWA assets.
- Provider Registry renders representative Debrid and Usenet providers.
- Provider secret values are masked and are not rendered back into the provider page.
- TorBox provider credentials are available internally to the AIOStreams bridge after saving.
- Provider-neutral AIOStreams merge enables a configured provider and preserves unrelated userData.
- Existing AIOStreams provider credentials win over ArrNexus values rather than being blindly overwritten.
- Provider-aware masked preview does not expose provider keys.
- Stack Readiness page renders for administrators.
- Setup completion is only recorded after the onboarding finish action.
- Non-admin users are denied `/providers`, `/readiness` and `/onboarding`.
- Login enters `/dashboard` rather than exposing private Dashboard data at the public root URL.

## v8 AIOStreams regression checks retained

The retained v8 suite continues to exercise a deterministic local HTTP AIOStreams implementation and verifies:

- public status vs authenticated User API verification
- `encryptedPassword` reuse
- full User API replacement semantics
- full-config preservation
- stale-preview refusal with zero PUTs
- backup before Auto-Wire write
- backup before rollback
- Prowlarr URL/API-key reuse
- automatic torrent + Usenet Prowlarr source behavior
- conservative NzbDAV credential handling
- Real-Debrid masking
- playback URL/header/Authorization/Cookie redaction
- administrator-only AIOStreams routes

## Release-engineering checks

Completed before packaging:

- Python module compilation: **PASS**
- actual Jinja template compilation via the application environment: **PASS** (through `validate.py`)
- JavaScript syntax validation with Node v22.16.0: **PASS**
- fresh SQLite/admin setup via regression validators: **PASS**
- real Uvicorn process from a separate clean source copy: **PASS**
  - `GET /api/health` -> HTTP 200
  - `GET /setup` -> HTTP 200
  - `GET /` -> HTTP 200
- public landing response contained ArrNexus branding and no authenticated library data: **PASS**

The final package pass additionally removes bytecode/runtime artifacts, scans the staged package for secrets and private deployment literals, creates the ZIP, re-extracts that exact ZIP into another clean directory, and reruns `validate.py` plus the JavaScript syntax check there. The exact ZIP SHA-256 is recorded alongside the release artifact.

## Docker status

Docker is **not installed in the packaging environment**. Therefore `docker compose config` and `docker compose build` were **not run here and are not claimed as passed**. They must be run on the Debian deployment host before the beta is accepted.

## Live checks still required before stable promotion

- `docker compose config` on the Debian host
- Docker image build/start
- v8 persistent-data copy migration
- existing Arr/DUMB/Jellyfin/InfiniDysk workflows
- real mount namespace and mount registry
- provider migration/credentials
- live AIOStreams provider Auto-Wire preview/apply/rollback
- real Spotify OAuth callback
- real playback/client behavior where applicable

Do not promote `9.0.0-beta` to stable until the live deployment checks pass.
