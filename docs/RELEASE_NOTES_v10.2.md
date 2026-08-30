# ArrNexus v10.2.0-beta — Lists, Language Guard v2 & Product Appearance

v10.2 makes external media lists a first-class automation input, strengthens ArrNexus's fail-closed cleanup rules and finishes the black/white product interface.

## Highlights

### Lists & Watchlists

ArrNexus can now follow:

- Trakt Watchlist and personal/public Trakt lists using OAuth 2.0
- IMDb public lists
- TMDb lists
- Plex Watchlist using the existing saved Plex token
- Simkl account watch state
- RSS / Atom feeds
- Custom JSON feeds

A list can be Movies, TV or Mixed. Movies and shows can be routed to separate specialist Radarr/Sonarr destinations and use the existing ArrNexus acquisition strategies: Automatic, Debrid first, Usenet first, Debrid only, Usenet only, Fastest or Quality.

Before automation is enabled, Preview shows how many titles already exist, are already requested, would be added or cannot currently be matched. Existing titles are not moved between specialist libraries simply because another list contains them.

Trakt access/refresh token replacement is saved atomically so a successful refresh cannot persist only half of the new credential pair.

### Language Guard v2

The new defaults are:

- English audio: required
- English subtitles: optional
- confirmed non-English media: policy rejection
- ffprobe failure / unknown language metadata: Manual review

A Manual-review result blocks a policy-gated import but is not evidence that the provider source is bad. ArrNexus will not trigger destructive provider cleanup from an uncertain/probe-failed result. Item Review retains the explicit forced recheck action and previous results remain persisted.

### Provider Duplicate Cleanup

Maintenance now has a separate dependency-protected provider cleanup page derived from Library Consolidation KEEP/REMOVE decisions.

Available actions are:

- delete redundant symlinks only
- delete currently unreferenced exact Real-Debrid sources only
- delete redundant links, then delete only provider sources that become fully unreferenced

Every apply rechecks the preview digest. Real-Debrid deletion is refused while any surviving managed library link still depends on the source, and the existing exact torrent resolver remains mandatory. No fuzzy provider deletion has been introduced.

### AIOMetadata

AIOMetadata is now a managed ArrNexus integration with:

- `/health` verification
- masked per-user configuration visibility
- explicit manifest URL verification
- AIOStreams relationship visibility

ArrNexus does not manufacture AIOMetadata compressed-config URLs and does not take ownership of the remote AIOMetadata configuration.

### Dark / Light product appearance

The old Nexus/Radarr/Sonarr/Lidarr/Prowlarr/Jellyfin/Spotify/OLED/Nord/Dracula/Cyber theme CSS has been physically removed.

v10.2 has exactly two appearances:

- Dark — near-black/charcoal, default
- Light — white/near-white with near-black text

The sun/moon control is permanently available in the top-right toolbar and persists in the browser. Providers and Libraries now use collapsed summary-first layouts.

## Upgrade

This release is intended for the native v10 updater:

`GitHub v10.2 Release → Update available → Install update → verify SHA-256 → database backup → validator → stage runtime → restart → health check`

Release assets:

- `arrnexus-v10.2.0-beta.zip`
- `arrnexus-v10.2.0-beta.zip.sha256`

The final SHA-256 is written only after the exact distributable ZIP has passed extracted-ZIP validation.

## Validation target

The release gate retains v7, v8, v9, v9.1, v9.2, v9.3, v9.4, v10 and v10.1 regression coverage, then adds v10.2-specific tests for list automation, Language Guard uncertainty safety, Provider Duplicate Cleanup, AIOMetadata, collapsed layouts, two-appearance CSS, JavaScript syntax, templates, fresh database migration and HTTP routes.
