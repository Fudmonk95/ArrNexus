# ArrNexus v3 validation report

This package was validated after the v3 feature merge.

## Passed

- Python compilation for the full `app/` package.
- Jinja compilation for every template with ArrNexus' real template environment, including the `human_size` filter.
- Fresh-database first-run setup and administrator creation.
- Upgrade migration from an ArrNexus v2-style database, including the Profile page that previously returned HTTP 500.
- Profile save/theme persistence.
- Settings rendering with release policy, notifications, backups, request limits, provider plugins and update configuration.
- Core authenticated pages return without server errors in an environment where DUMB mounts are unavailable; Maintenance now degrades gracefully instead of crashing.
- SQLite backup creation.
- Sanitized configuration export/import.
- Sanitized diagnostics ZIP generation.
- JSON-only provider plugin installation.
- PWA manifest and service worker delivery.
- Release policy scoring/rejection logic.
- Standalone Uvicorn boot and HTTP checks for `/api/health`, `/setup` and the PWA manifest.
- Exact ArrNexus credentials previously used during development were checked and are not present in this package.

## Environment limitation

The validation environment does not provide a Docker daemon, so an actual `docker build` could not be executed here. The Dockerfile and Compose file are intentionally minimal and the application itself was booted directly with Uvicorn. On the target server, `docker compose up -d --build` remains the final container-runtime validation.

## Outstanding feature set included

The validated build contains the features proposed to make ArrNexus stand out:

- Per-title operational timeline.
- Explainable release policy/scoring engine.
- Sanitized one-click diagnostics bundle.
- ntfy, Gotify, Discord webhook and email notifications.
- Daily rolling database backups and manual backups.
- Sanitized configuration export/import.
- GitHub release update checker with a user-configured repository.
- Safe JSON-only community catalog provider SDK.
- Installable PWA/mobile shell.
- User request permissions and daily request limits.
- Release-score explanations in the Prowlarr/Real-Debrid UI.
- Existing routing, import progress, logs, maintenance, discovery, music, Real-Debrid and specialist Arr features remain in place.
