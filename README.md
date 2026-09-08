# ArrNexus v12.0.0

ArrNexus is the Zurg-first control and orchestration layer for my personal media server.

v12 turns the v11 control plane into an active recovery system without bringing back the old archive/extraction/filesystem stack. ArrNexus now supervises missing media and broken Arr queue items while leaving the actual acquisition/storage layer to the Arr applications, Real-Debrid/Zurg and the existing DUMB stack.

## Architecture

```text
                         Seerr
                           |
                           v
                  Sonarr / Radarr / Lidarr
                     |       |       |
                     +-------+-------+
                             |
               +-------------+-------------+
               |                           |
               v                           v
      Missing Media Orchestrator     Queue Janitor
      controlled search policy       import / failure repair
               |                           |
               +-------------+-------------+
                             |
                          ArrNexus
                observe / decide / trigger
                             |
                             v
                    /zurg_mnt/zurg
                             |
                            Zurg
                             |
                             v
                          Jellyfin
```

**Responsibility split**

- **ArrNexus** decides what should be searched, when to retry, when to stop, and how to handle queue warnings.
- **Sonarr / Radarr / Lidarr** perform searches, grabs, blocklisting and imports through their normal APIs.
- **NeutArr** can continue to own periodic missing-media search cadence.
- **Swaparr** can continue to handle stalled downloads when enabled through NeutArr.
- **Zurg** remains responsible for acquisition visibility, the mounted source library and working views.
- **Jellyfin** remains the supported media server for this personal build.

ArrNexus does **not** rename, move, split, extract, repair archives or stage source media.

## New in v12

### Missing Media Orchestrator

The Missing Media page inventories monitored missing content from:

- Radarr movies;
- Sonarr episodes and seasons; and
- Lidarr albums.

It creates a persistent recovery queue and tracks each item through states such as:

```text
Detected
  -> Searching
  -> Arr queue
  -> Zurg working
  -> Mounted
  -> Resolved
```

The scheduler is deliberately controlled:

- batch size: 1–5;
- delay between searches;
- maximum simultaneous active acquisitions;
- daily search limit;
- maximum searches without acquisition;
- cooldown between repeated no-result searches; and
- hard failed-release limit shared with queue recovery state.

For Sonarr, multiple missing episodes in the same season are grouped into a **SeasonSearch** rather than firing one request per episode. A single missing episode uses **EpisodeSearch**.

Before dispatching a search ArrNexus checks the Arr queues and the Zurg correlation index. If the media is already queued, in `__downloads__`, being processed in `__magic__` / `__nzb__`, or already mounted, ArrNexus observes it instead of creating duplicate work.

### NeutArr coexistence

v12 treats NeutArr as useful again rather than as something ArrNexus must replace.

With **NeutArr coexistence mode** enabled (the default):

- NeutArr owns the automatic missing-media search cadence;
- ArrNexus still inventories missing media;
- ArrNexus tracks Arr/Zurg activity and recovery state;
- manual **Search now** actions remain available; and
- ArrNexus does not run a second automatic missing-media scheduler on top of NeutArr.

Turn coexistence mode off only when you want ArrNexus itself to own automatic missing-media search scheduling.

### Auto Import + Queue Janitor

The Queue Janitor continuously evaluates Sonarr, Radarr and Lidarr queues and applies a conservative decision tree.

```text
                    ARR QUEUE ITEM
                          |
              +-----------+-----------+
              |                       |
              v                       v
       GOOD MEDIA MAY EXIST      RELEASE IS BROKEN
       ID/manual-import issue     failed / corrupt / bad
              |                       |
              v                       v
       SAFE EXPLICIT IMPORT       REMOVE FROM CLIENT
       using Arr IDs/history      BLOCKLIST RELEASE
              |                       |
          +---+---+                   v
          |       |              CONTROLLED RETRY
       success  unsafe                |
          |       |                   v
          v       v              HARD RETRY LIMIT
        clean   attention          /        \
                                  no        yes
                                  |          |
                                retry    stop + Needs Attention
```

#### Queue rules

| Queue condition | Automatic policy |
| --- | --- |
| Download failed | remove from client + blocklist + search another release |
| Zurg / provider / Real-Debrid timeout or failure | remove + blocklist + search another |
| No files found eligible for import | remove + blocklist + search another |
| Broken archive / missing articles / extraction failure reported by the Arr | remove + blocklist + search another |
| ffprobe genuinely cannot read visible media | remove + blocklist + search another |
| Confirmed sample | remove + blocklist + search another |
| Invalid season / episode / album mapping | do **not** blindly import; cleanup/retry only |
| Unable to determine whether file is a sample | inspect with ffprobe first; unreadable/too-short media can then fail/retry |
| Matched by ID / Manual Import required | **attempt explicit safe auto-import first** |
| Successful import | let the Arr complete normal queue cleanup |
| Stalled/no-progress download | defer to NeutArr/Swaparr by default |

The ID/manual-import rule is intentionally evaluated conservatively. If the candidate also contains an invalid season, invalid episode, sample, unknown-media, language/quality or similar unsafe rejection, ArrNexus will not force-import it.

### Hard retry limit

The default failed-release limit is **3**.

Example:

```text
47 Meters Down

Attempt 1: missing articles
Attempt 2: provider timeout
Attempt 3: unreadable media

STATUS: Needs Attention
Automatic searching stopped
```

Failure counters and Janitor actions are persisted in SQLite. **Reset & resume** clears the active retry counter but retains the Janitor audit history.

### Dry-run first

Both recovery engines ship safely:

```text
Missing Media automatic scheduler: disabled
Queue Janitor automatic actions:     disabled
Dry-run:                              enabled
NeutArr coexistence:                  enabled
Swaparr stall deferral:               enabled
```

Start by scanning and running dry-run cycles. Enable live actions only after the classifications match the queue behaviour on the live server.

### Live Requests / Zurg tracking

v12 keeps the background lifecycle tracker introduced in v11:

```text
Requested
  -> Approved
  -> Searching
  -> Grabbed
  -> Zurg working
  -> Zurg scraping
  -> Mounted
  -> Imported
  -> Available
```

Provider/Zurg work runs in background caches so the web UI does not wait for a large Zurg scan.

### Zurg health and cache visibility

ArrNexus reports:

- mounted filesystem health;
- Zurg version/nightly string;
- movie, show and music counts;
- `__downloads__`, `__magic__`, `__nzb__` and unplayable counts;
- protected HTTP endpoint reachability;
- correlation-index state;
- Zurg cache size; and
- free space on the cache filesystem.

The cache mount is read-only inside ArrNexus. ArrNexus does not automatically purge Zurg's cache.

### Spotify OAuth

Spotify uses an explicit public ArrNexus URL rather than the local LAN address.

Example:

```text
ARRNEXUS_PUBLIC_URL=https://arrnexus.example.com
```

Callback:

```text
https://arrnexus.example.com/music/spotify/callback
```

Copy the exact callback displayed on **Music Hub -> Spotify settings** into the Spotify developer application.

## Existing integrations

- Zurg
- Sonarr
- Radarr
- Lidarr
- Prowlarr
- Seerr
- Jellyfin
- NeutArr coexistence / Swaparr stall ownership
- Spotify
- Beatport search hand-off
- Trakt
- TMDb
- IMDb
- Simkl
- RSS / custom JSON lists
- Kometa YAML import/export

## Personal-build notice

From v11 onward, ArrNexus releases are built around my own server and workflow rather than trying to be a universal media-stack product.

The repository remains open source under the repository's existing licensing terms. Anyone is welcome to fork it, adapt it or continue it for their own stack.

The only media server targeted by this personal build is **Jellyfin**.

## Removed architecture stays removed

v12 does not reintroduce the old low-level media-processing layers. Zurg now handles the source-library/acquisition responsibilities that made those layers unnecessary.

ArrNexus remains an orchestration/control application: **observe, decide, trigger, correlate, retry, stop and report**.

## Installation / Portainer

### Host layout used by this build

```text
/mnt/appdata/arrnexus/data       -> /data
/zurg_mnt                        -> /zurg_mnt (read-only, rslave)
/mnt/appdata/zurg-rclone-cache   -> /host/zurg-rclone-cache (read-only)
```

Database:

```text
/data/router.db
```

The supplied `portainer-stack.yml` is aligned with the current server.

### Build the v12 image on the server

```bash
cd ArrNexus-v12.0.0
./scripts/build-local-image.sh
```

This creates:

```text
arrnexus:v12.0.0
```

Then update the existing Portainer ArrNexus stack to use that image.

**Do not create a new ArrNexus data directory.** Keep:

```text
/mnt/appdata/arrnexus/data:/data
```

v12 extends the existing SQLite database in place with recovery/orchestration tables.

See `docs/PORTAINER_UPDATE_v12.0.0.md` for the exact update sequence.

## Verifying the running container

```bash
./scripts/verify-running.sh
```

Or manually:

```bash
curl -fsS http://127.0.0.1:8484/api/health
docker logs --tail 100 arrnexus
```

## Zurg resources

Official project resources:

- https://github.com/debridmediamanager/zurg-public
- https://github.com/debridmediamanager/zurg-public/wiki
- https://github.com/debridmediamanager/zurg-public/wiki/Release-cycle

## Release status

**ArrNexus v12.0.0** is the first stable-named release of the Zurg-first recovery/orchestration architecture.
