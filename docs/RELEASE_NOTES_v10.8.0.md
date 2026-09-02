# ArrNexus v10.8.0-beta

## Recovery no longer stops for language metadata

The automatic recovery/import path no longer runs Language Guard ffprobe scans. Language metadata cannot pause or reject an import, create a manual-review loop, remove provider media or start an Arr replacement search. Existing language-only workflow states are neutralised during upgrade. Manual language scans remain available under Advanced diagnostics and save advisory exact-fingerprint observations only.

The one-click v10.7 archive pipeline remains intact: provider verification, exact direct Real-Debrid fallback, authoritative local verification, verified extraction, immediate inventory refresh, safe batch TV splitting, generated episode verification, persistent split state, fresh naming/import planning and final Sonarr/Radarr import.

## Live job terminal everywhere

Every background job now exposes current activity, progress, live item states and a compact scrollable terminal. Detailed output stays with the job; Unified Logs receives start, completion and error summaries. Tracked schedulers and workers cancel cleanly during Uvicorn shutdown.

## Media Automation hub

- Store one normalized collection definition independently of any media server.
- Preview changes before sync; v10.8 is additive and removes no existing collection members.
- Target Plex through a detected Kometa executable/config.
- Target Jellyfin and Emby through native collection APIs.
- Detect Jellyfin SmartLists while retaining the native fallback.
- Keep failures isolated per target.
- Schedule enabled definitions, cancel jobs, and inspect all output in the live terminal.
- Import safe collection names and exact provider IDs from Kometa YAML without executing templates or builders.
- Use manual provider IDs, IMDb, TMDb, Trakt, Plex Watchlist, MDBList, RSS and JSON sources where their required credentials are configured.
- Missing-title acquisition remains explicitly disabled by default.

## Additional reliability work

- Fix Windows low-level RAR staging descriptors to use binary mode, preventing byte `0x1A` from being treated as legacy end-of-file.
- Apply the saved light/dark appearance before external CSS loads to prevent a white loading flash.
- Preserve provider archives, safe symlinks, `/mnt/debrid/arrnexus-extracted`, `.arrnexus-originals` exclusions, cancellation, review controls and job dismissal.

## Validation

Certified with the v10.8 validator, the v10.7 one-click recovery validator, every retained v7-v10.6.3 layer, Python compilation, authenticated Media Automation route/template/API smoke tests and a clean standalone Uvicorn startup/shutdown smoke test.
