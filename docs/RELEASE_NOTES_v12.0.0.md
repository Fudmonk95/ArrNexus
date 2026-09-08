# ArrNexus v12.0.0 release notes

v12 adds two recovery engines to the Zurg-first control plane while keeping all low-level acquisition/storage responsibilities with the Arr/Zurg stack.

## Missing Media Orchestrator

- Radarr monitored missing movies.
- Sonarr monitored missing episodes, with multi-episode seasons grouped into SeasonSearch.
- Lidarr monitored missing albums.
- Controlled 1–5 item batches, inter-search delay, active-acquisition ceiling and daily limit.
- Persistent search attempts, cooldown and Needs Attention state.
- Duplicate-work suppression when an item is already in an Arr queue or Zurg working/mounted view.
- NeutArr coexistence mode: NeutArr owns automatic missing search cadence while ArrNexus supervises and provides manual controlled search actions.

## Auto Import + Queue Janitor

- Radarr, Sonarr and Lidarr queue inspection.
- Safe explicit ManualImport attempt for ID/grab-history matching warnings before deletion.
- Invalid season/episode/album mapping is never blindly imported.
- ffprobe inspection for uncertain-sample warnings when media is visible through the queue path or Zurg.
- Remove-from-client + exact release blocklist for confirmed failed/unusable releases.
- Search another release after cleanup.
- Persistent failed-release counter with default hard limit of 3.
- Needs Attention stop state instead of endless retry loops.
- NeutArr/Swaparr ownership of stalled/no-progress downloads by default.
- Dry-run by default and persistent action history.

## Existing v11 improvements retained

- non-blocking dashboard/provider caches;
- Seerr -> Arr -> Zurg -> library lifecycle tracking;
- Zurg background index and cache/storage visibility;
- protected Zurg HTTP endpoint health handling; and
- Spotify public-URL OAuth callback handling.

## Stable release naming

The release version, package, image and tag are all `12.0.0`.
