# ArrNexus v13.1.0

ArrNexus v13.1.0 is a focused **Magic Intake 2.0** release. It fixes the biggest usability problem in v13.0.0: multiple releases that all resolve to the same TV series, movie or music artist no longer appear as separate import cards.

## Magic Intake 2.0

### Canonical grouping

Magic Intake now performs provisional release parsing first, resolves metadata through the appropriate Arr, and then groups the UI/import job by the canonical Arr identity.

- **TV:** one card per Sonarr series / TVDB identity.
- **Movies:** one card per Radarr movie / TMDB identity, with multiple candidate releases kept underneath.
- **Music:** Lidarr album lookup resolves the owning artist, then releases/albums are grouped under one Lidarr artist identity.

The grouping is only an orchestration/UI grouping. **ArrNexus does not merge or flatten the underlying media files.** A 20-episode season still remains 20 distinct episode releases/files.

### Better episode parsing

TV intake parsing now understands:

- `S01E01`
- `S01E07-E09`
- old-style `1x02`
- season ranges such as `S01-S09`

Series cards show release count, detected seasons and exact episode markers.

### Filters

Magic Intake now has instant client-side filters for:

- All / Movies / TV / Music
- Genre
- Theme (`Kids`, `Christmas`, `Halloween` when metadata/title supports it)
- Intake state
- Text search across title and source releases

This makes it much easier to isolate family/animation content for Kids libraries, horror/Halloween content, and Christmas-labelled content before choosing destinations.

### Force Match without losing your place

Force Match now opens in an in-page modal and queries the matching Arr API asynchronously. Selecting a result updates the intake item without navigating to the top of the page.

Existing v13.0.0 100% Force Matches are migrated into per-release overrides so they survive canonical regrouping.

### Background imports

Import actions no longer hold the browser request open while ArrNexus waits for the move, ManualImport, rescan and verification sequence.

The import is queued immediately and the card reports live progress through:

```text
Queued
  -> Resolving Arr target
  -> Moving releases inside __magic__
  -> Manual Import candidates
  -> Targeted Arr rescan
  -> Verifying
  -> Imported / Partial
```

### Safer TV verification

v13.0.0 could compare the total number of existing Sonarr episode files against the number of incoming episode markers. On an established series that could produce a misleading success.

v13.1.0 verifies the **specific expected season/episode identities** where markers are available. Existing unrelated episodes no longer count toward the new import.

### Music improvements

- Added Lidarr album lookup support.
- Album releases can resolve to their owning artist.
- Multiple releases from the same artist are shown under one artist import card.
- Nested album/disc folders containing audio are recognised as music more reliably.

## Upgrade / compatibility

- Version: `13.1.0`
- Docker image: `arrnexus:v13.1.0`
- Existing database remains `/mnt/appdata/arrnexus/data/router.db`.
- The v13.0.0 Magic Intake schema is migrated in place.
- Existing service connections, users, Janitor state, Missing Media state and Magic Intake Force Matches are retained.
- Main Zurg mount remains read-only.
- Only `/zurg_mnt/zurg/__magic__` is mounted writable as `/zurg_magic`.

No new Portainer mounts are required if the v13.0.0 stack was already deployed correctly; only the image tag changes to `arrnexus:v13.1.0`.

## Retained

v13.1.0 keeps the existing Zurg-first architecture, including:

- Missing Media Orchestrator
- Queue Janitor / safe ManualImport recovery
- hard retry limits and Needs Attention
- NeutArr coexistence and Swaparr stall ownership
- Seerr → Arr → Zurg lifecycle tracking
- Zurg/cache visibility
- Spotify public HTTPS callback support
- Jellyfin integration
- List Import
- Media Automation / Kometa support

The removed legacy archive/extraction, Infinity/Infinidysk, Decypharr and NZBDAV architecture remains removed.
