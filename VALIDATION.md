# ArrNexus v10.2.0-beta Validation Report

Release target: **ArrNexus 10.2.0-beta**  
Validation date: **2026-08-29**

## Release gate

The release is accepted only when the complete retained regression chain and the v10.2-specific layer pass against the source tree, then the exact distributable ZIP is extracted into a separate clean directory and the same validator is run again.

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

The retained v9.1 visual assertion was made compatibility-safe: it still requires the v9.1 unified-black marker, but accepts the deliberate v10.2 base black `#030304` instead of demanding obsolete `#050506`.

## v10.2-specific checks

- Fresh SQLite migration creates `media_lists` and `media_list_runs`.
- Lists & Watchlists adapters are present for Trakt Watchlist/lists, IMDb, TMDb, Plex Watchlist, Simkl, RSS/Atom and Custom JSON.
- List preview distinguishes existing/requested/new/unmatched state before add.
- List sync reuses normal ArrNexus discovery/routing/acquisition and preserves the list monitor setting.
- Trakt access/refresh token replacement is committed atomically.
- Language Guard defaults to English audio required and English subtitles optional.
- Confirmed non-English audio can be a policy rejection; unknown/probe-failed language state is Manual review and is never destructive.
- Provider Duplicate Cleanup uses stale-preview digest protection and surviving-link dependency checks before exact Real-Debrid deletion.
- AIOMetadata masks secret-bearing fields and exposes bounded health/configuration/AIOStreams relationship visibility.
- Providers and Libraries use collapsed summary-first `<details>` layouts.
- Historical multi-theme CSS selectors are removed; Dark and Light are the only product appearances.
- Top-right appearance toggle persists browser preference.
- Service-worker cache marker is `arrnexus-static-v10.2`.
- Python compilation, JavaScript syntax and all Jinja templates pass.
- Help Centre catalogue, generated User Guide and generated route documentation include v10.2 workflows.

## Process smoke test

A real Uvicorn process started from the release source and returned:

- `GET /api/health` → **200**, version `10.2.0-beta`
- `GET /setup` → **200**
- `GET /` → **200**

The new authenticated routes also render successfully in TestClient on a fresh database:

- `/lists`
- `/aiometadata`
- `/maintenance/provider-cleanup`
- `/providers`
- `/libraries`

## Docker

Docker is not available in the packaging environment, so `docker compose config` / image build cannot be executed here. The release retains the validated v10.1 Dockerfile/Compose baseline and does not introduce a new container-level dependency.

## Exact ZIP certification

The release workflow extracts the finished `arrnexus-v10.2.0-beta.zip` into a clean directory and reruns `python3 validate.py` plus the real Uvicorn smoke test against that extracted copy. The external `.sha256` file is created only after that exact ZIP passes.
