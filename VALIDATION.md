# ArrNexus v10.1.0-beta Validation Report

Release target: **ArrNexus 10.1.0-beta**

## Regression chain

The v10.1 release tree was validated against the retained regression layers individually:

- PASS — v7.0
- PASS — v8
- PASS — v9.0.0-beta
- PASS — v9.1
- PASS — v9.2
- PASS — v9.3.0-beta
- PASS — v9.4.0-beta
- PASS — v10.0.0-beta compatibility layer
- PASS — v10.1.0-beta feature layer

The top-level `validate.py` still invokes the retained v10 validator, which in turn retains the earlier layers. The release-engineering environment runs the layers separately as well so long historical chains do not hide which layer failed.

## v10.1 checks

Validated:

- Python compilation across `app/`, bootstrap and validators.
- JavaScript syntax when Node is available.
- Real Jinja template compilation through the ArrNexus environment.
- `/api/health` reporting `10.1.0-beta`.
- First-admin setup smoke.
- Language Guard `remove_rejected_debrid` setting persistence.
- Separate `Language check failed` and `Language rejected` badge states.
- Database migration for controlled job `rejected` counts.
- `complete_with_rejections` workflow markers and immediate DMM Inbox invalidation.
- Real-Debrid cleanup requires exact source-folder/torrent-name identity and rejects fuzzy/partial matching.
- Exact provider deletion uses the matched provider torrent ID rather than filesystem deletion.
- Consolidation ranking gives Language Guard eligibility priority over raw 4K/file-size quality.
- Preview-first Library Consolidation route and template.
- Stale-preview digest protection before link removal.
- Optional provider cleanup remains separate from link cleanup and is off by default in the UI.
- Provider cleanup only considers sources made unreferenced by the exact consolidation apply operation.
- v10 updater semantic ordering recognizes `10.1.0-beta` as newer than `10.0.0-beta`.
- Documentation audit covers 124 application routes/actions including both consolidation routes.

## Real Uvicorn smoke

A real Uvicorn process was started against a temporary SQLite database with the v10 self-update environment enabled.

Verified:

- `GET /api/health` → 200 with `10.1.0-beta`
- `GET /setup` → 200
- `GET /` → 200

## Docker

Docker is not available inside the release-packaging environment, so no claim is made that `docker compose build` was executed there. The included Compose/Dockerfile remain covered by the retained v10 source validation. Run `docker compose config` and `docker compose build` on the deployment host before a manual container upgrade.

## Release safety rule

The final distributable ZIP must be created only after transient/runtime files are removed. That exact ZIP must then be extracted into a separate directory and the regression/feature validation repeated against the extracted copy before its SHA-256 is published.
