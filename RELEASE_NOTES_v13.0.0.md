# ArrNexus v13.0.0

ArrNexus v13 introduces **Magic Intake**, a first-class workflow for media added directly through DMM/Zurg rather than through the normal Seerr → Arr acquisition path.

## Magic Intake

- Scans only the top level of Zurg `__magic__` for unorganised intake.
- Ignores already-organised `movies/`, `tv/` and `music/` trees.
- Classifies intake as Movie, TV or Music.
- Groups related episodic releases such as `S01E01`, `S01E02`, etc. into a single title card.
- Uses Radarr, Sonarr and Lidarr lookup APIs as the primary metadata source.
- Shows poster artwork, canonical title, year and confidence score.
- Adds **Force Match** for ambiguous or badly named releases.
- Supports destination categories for movies, TV and music.
- Supports multiple movie releases in one group and lets the user select which release to import.
- Performs Zurg-native metadata-only moves inside `__magic__` through a dedicated writable mount.
- Keeps the main Zurg filesystem mounted read-only in ArrNexus.
- Explicitly attempts Arr manual import / targeted rescan after the virtual move.
- Does not mark an intake item complete until the target Arr confirms media files are present.
- Records `Partial` when verification does not complete, rather than silently calling the job successful.
- Keeps unmatched and partial intake groups visible for review.
- Adds Ignore without deleting anything from Real-Debrid.

## New v13 storage boundary

ArrNexus still does not become a general-purpose filesystem manager.

```text
/zurg_mnt                    -> /zurg_mnt    read-only
/zurg_mnt/zurg/__magic__     -> /zurg_magic read-write
```

Only the dedicated Magic Intake mount is writable.

## Standalone service defaults

The supplied Portainer and Docker Compose files now default to the current standalone media stack on `192.168.137.10`:

- Zurg `:9999`
- Radarr `:7878`
- Sonarr `:8989`
- Lidarr `:8686`
- Prowlarr `:9696`
- Jellyfin `:8096`
- Seerr `:5055`

API keys remain stored through ArrNexus settings/database unless supplied through environment variables.

## Retained from v12

- Missing Media Orchestrator
- Queue Janitor
- NeutArr coexistence
- Swaparr stalled-download deferral
- hard retry limits and Needs Attention state
- ID/grab-history based safe auto-import attempts
- live Seerr → Arr → Zurg lifecycle tracking
- Zurg cache/storage visibility
- Spotify public-URL OAuth support
- Portainer persistence using `/data/router.db`

## Upgrade notes

The existing ArrNexus database remains at:

```text
/mnt/appdata/arrnexus/data/router.db
```

No fresh database is required.

The v13 Portainer stack adds one new required bind mount:

```yaml
- /zurg_mnt/zurg/__magic__:/zurg_magic:rw
```

and two environment variables:

```yaml
MAGIC_ROOT: /zurg_magic
MAGIC_ARR_PREFIX: /zurg_mnt/zurg/__magic__
```

The normal Zurg mount remains read-only.
