# ArrNexus v10.3.0-beta Validation Report

Release target: **ArrNexus 10.3.0-beta**  
Validation date: **2026-08-30**

## Release gate

v10.3 retains every historical regression layer from v7 through v10.2 and adds a v10.3-specific layer for Archive Rescue, Advanced TV Recovery, typed DMM routing, Language Guard cleanup, Trakt Device OAuth and the product-wide Dark/Light appearance contract.

Historical validators are executed as separate release-gate commands because several legacy FastAPI/TestClient suites can leave background workers alive when chained from one long-lived Python parent after their final PASS assertion. The assertions themselves are unchanged.

`python3 validate.py` is intentionally the **install-safe current-release validator** used by the native updater. It runs the complete v10.3-specific compile/template/HTTP/feature checks deterministically. The packaging gate additionally runs every retained historical validator (`validate_v7.py` through `validate_v102.py`) separately before the ZIP is accepted.

## Retained regression layers

PASS layers:

- v7
- v8
- v9
- v9.1
- v9.2
- v9.3
- v9.4
- v10
- v10.1
- v10.2
- v10.3

## v10.3-specific checks

- Archive-style TV names such as `Season 6 episode 1`, `Series 7 Episode 06` and `Season 1 S01 Complete` are parsed as TV/season media.
- Manual DMM destination values are typed (`movie:*` / `tv:*`), so `TV · BBC`, `TV · Default` and `TV · Kids` cannot be validated as movie destinations.
- Combined-season sources are identified and routed to Advanced TV Recovery rather than blindly imported as a movie.
- Advanced TV Recovery prefers exact chapter boundaries, supports lower-confidence runtime estimates only with explicit confirmation, writes partial outputs safely, validates outputs with ffprobe and retains the original provider source.
- DMM Inbox exposes Check selected languages, Check all unchecked, Force re-check all and safe direct/bulk rejected-source cleanup.
- Stale Language Guard results become Re-check required and are never used as destructive evidence.
- Rejected Real-Debrid deletion rechecks current policy/fingerprint state, surviving managed-library dependencies and exact provider identity at apply time.
- Item Review inspection failures render a controlled ArrNexus diagnostic view rather than a bare HTTP 500.
- Trakt Device OAuth start/poll state is implemented; application Client ID/Secret are isolated under Advanced setup and rotating token replacement remains atomic.
- Archive Rescue scans monitored Sonarr gaps, searches the configured Prowlarr Internet Archive source, parses the actual `.torrent` manifest, supports selective file choice and hands selected files to Real-Debrid using fail-closed matching.
- Dark and Light are the only product appearances. The application shell, dashboard, panels, controls, tables and typography use shared neutral appearance tokens; legacy navy/blue surfaces are neutralised and purple/cyan remain accents.
- JavaScript syntax, Python compilation and all Jinja templates pass.
- Help Centre, generated User Guide, Documentation Audit, README, CHANGELOG and v10.3 release notes cover the new workflows.
- Updater ordering recognises `10.3.0-beta` as newer than `10.2.0-beta`.

## Source-tree process smoke

A real Uvicorn process started against a fresh temporary SQLite database and returned:

- `GET /api/health` → **200**, version `10.3.0-beta`
- `GET /setup` → **200**
- `GET /` → **200**

## Package hygiene

The release package excludes:

- `.env`
- SQLite databases
- `/data` runtime state
- runtime staging/backups
- virtual environments
- `__pycache__`
- `.pyc`
- live API keys/tokens/credentials

A high-confidence secret-pattern scan is run before packaging and again against the extracted release.

## Docker

Docker is not available in the packaging environment, so `docker compose config` and `docker compose build` cannot be claimed here. Node.js is available and `node --check app/static/app.js` passes.

## Exact ZIP certification

The finished `arrnexus-v10.3.0-beta.zip` is extracted into a separate clean directory. The v10.3 validator, every retained regression layer, package hygiene checks and a real Uvicorn health/setup/landing smoke are rerun against that extracted copy before the external SHA-256 file is created.
