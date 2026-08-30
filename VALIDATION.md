# ArrNexus v7.0.0 Validation Report

Release target: **ArrNexus 7.0.0**  
Validation date: **2026-08-26**

## Result

```text
PASS: ArrNexus v7.0 Spotify personal library, native InfiniDysk telemetry,
English Language Guard, Prowlarr indexer control, Sonarr TV search,
strict connectors and performance caches
```

## Checks completed

### Application/package integrity

- Python compilation completed for every module under `app/`.
- All Jinja templates compile using the real ArrNexus Jinja environment and custom filters.
- `app/static/app.js` passes Node.js syntax validation.
- Fresh SQLite initialization succeeds.
- First-run administrator setup succeeds.
- Authenticated route smoke tests confirm core pages do not return HTTP 500 in an offline/no-DUMB validation environment.
- A real Uvicorn process was started against a clean temporary database:
  - `GET /api/health` → HTTP 200
  - `GET /setup` → HTTP 200

### Discover/acquisition regressions

- Deterministic Discover shelves render without the historic Jinja white-screen regression.
- Local/specialist library shelves and search results can render together.
- Acquisition planner tests prove:
  - Usenet-first selects an acceptable Usenet result first.
  - it falls back to torrent/debrid when no acceptable Usenet result remains.
  - exactly one release is grabbed by a single acquisition plan.

### Spotify v7 personal integration

Offline mocks validate the real aggregation/normalisation code for:

- OAuth authorization URL generation
- requested personal scopes (`user-library-read`, `user-top-read`, `user-read-recently-played`, private/collaborative playlist read)
- saved tracks
- saved albums
- playlists
- top tracks
- top artists
- recently played
- profile identity
- personal result/card normalisation

Music-provider isolation tests also confirm Apple and Deezer provider pages do not silently reuse one another's feed. The Music Hub template contains the separate **Global Trend Pulse · ListenBrainz** label so global trend data is not misrepresented as Spotify data.

**Live Spotify OAuth was not executed in the validator because that requires the deployer's own Spotify application and an externally registered callback URI.**

### Language Guard

- Dockerfile includes ffmpeg/ffprobe support.
- Pure media-stream policy tests verify:
  - English audio + English subtitle stream → pass.
  - Italian-only audio/subtitles → reject with both missing English audio and missing English subtitle reasons.
- Language Guard Settings UI exists.
- DMM Inbox includes a Language status/filter and language badges.
- Item review includes a manual `Check language now` action and stream-detail panel.
- Import pipeline contains the non-destructive rejection path: a failing DMM source is not linked into the library, is retained in the debrid source, and can trigger a replacement Arr search.

### InfiniDysk

A local authenticated mock verifies:

- incorrect InfiniDysk key is rejected.
- correct key is accepted.
- `/api/get-overview-stats` data is consumed successfully.
- live tile/throughput structures are handled.

Static validation confirms the native Overview endpoint is used by the client.

### Decypharr / DUMB connector verification

A local service mock verifies:

- reachable Decypharr + wrong Bearer token → authentication failure.
- correct Decypharr Bearer token → verified connection.
- DUMB API health JSON → accepted.
- DUMB connector implementation contains a guard against treating an HTML frontend page as a valid API response.

### Sonarr TV search

Static/behavior coverage confirms ArrNexus uses the corrected season-search implementation (`seriesId + seasonNumber`) and keeps the TV pack/full-series planner built on top of those results.

### Prowlarr

- Indexer control page renders.
- Client contains read/update methods for Prowlarr indexers.
- UI exposes supported operational settings and `Save to Prowlarr` action.

### Performance/regression checks

- DUMB process discovery cache retained.
- DMM source scan cache present (30 seconds).
- library inventory cache present (45 seconds).
- source→symlink index cache present (30 seconds).
- startup pre-warming of expensive namespace snapshots is present.
- soft-navigation/prefetch shell remains enabled.
- responsive Connections/Ecosystem grid regression checks remain enabled.

### Version/update UI

- v7.0 application version is embedded in the application.
- Stable/Beta/Development update channel badge is present.
- optional update-available state is cached client-side to avoid repeated GitHub checks on every navigation.

### Secret scan

The source package was scanned for the known credentials that had appeared during earlier development/debugging. Result: **0 known credential matches**.

## Environment limitation

A Docker CLI/daemon was not available in the validation runtime, so an actual `docker build` could not be executed here. The application itself was booted with Uvicorn and the Dockerfile was syntax/release reviewed. The deployment host will perform the container-level build; v7's image will take longer than earlier versions because it installs `ffmpeg` for Language Guard.

## Release packaging procedure

The release ZIP is created without `__pycache__`, `.pyc`, user databases or a populated `/data` directory. After packaging it is extracted into a separate clean directory and `python validate.py` is run again from the extracted copy. The final SHA-256 is recorded in the release response.
