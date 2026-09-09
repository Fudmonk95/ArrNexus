# ArrNexus v13.1.1

ArrNexus v13.1.1 is a focused **Magic Intake performance and correctness hotfix** for v13.1.0.

It addresses the problems found on the live server after deploying Magic Intake 2.0: duplicate cards surviving from old/running rows, very large inbox pages, Cloudflare origin timeouts during scans/polling, stale partial rows whose source had already moved, and movie folders being incorrectly classified as music when they also contained audio files.

## Magic Intake fixes

### Canonical inbox is rebuilt from the real top-level `__magic__`

The active inbox is now rebuilt from what actually exists at the top level of the writable Magic mount.

- stale `partial`, `queued`, `importing` and `verifying` display rows are no longer preserved forever;
- old v13/v13.1 duplicate cards are removed on the next clean scan;
- imported history remains preserved;
- a restart marks in-memory import jobs as interrupted once, then the next scan reconstructs the inbox from the real Zurg state.

This fixes cases such as two separate **Stranger Things** cards continuing to appear even though both resolve to the same Sonarr identity.

### One active import per canonical title

ArrNexus now locks imports by canonical identity as well as by row key.

If two stale cards still temporarily resolve to the same Sonarr/Radarr/Lidarr identity, only one import can run. A second click is refused with an `already running for this same canonical title` result.

### Scans no longer block the FastAPI event loop

Top-level Zurg/FUSE discovery and media classification now run in a worker thread instead of synchronously on the web event loop.

The **Scan Now** action also starts a background scan and returns immediately. It no longer waits for thousands of metadata matches to finish inside the browser request.

This specifically targets reverse-proxy/Cloudflare `Proxy Read Timeout` failures seen when a scan or very large Magic Intake refresh exceeded the proxy read window.

### Large inboxes are paginated

Magic Intake no longer renders or polls the entire catalogue at once.

- default page size: **72 cards**;
- server-side Movie / TV / Music filtering;
- server-side genre filtering;
- server-side Kids / Christmas / Halloween theme filtering;
- server-side state filtering;
- server-side title/source-release search;
- **Load more** fetches the next page without a full page reload.

The UI now reports both the number of matching groups and how many cards are currently loaded.

### Lightweight progress polling

The old two-second poll returned the full Magic Intake state, including every group and every source-path list.

v13.1.1 polls only:

- summary counters;
- filter metadata;
- active import progress;
- a bounded set of terminal updates.

This dramatically reduces JSON size, browser DOM churn and proxy load on large `__magic__` libraries.

### Movie/music classification corrected

A directory containing a real video file is now treated as video media even if it also contains commentary, soundtrack or other audio files.

Music classification is only selected when audio is present **and no video files are detected**.

This fixes movie titles such as **Troublemakers** or **Serendipity** being displayed as Lidarr/music intake items simply because their folder also contained audio.

## Import behaviour

Background imports from v13.1.0 remain in place:

```text
Queued
  -> Resolving Arr target
  -> Moving releases inside __magic__
  -> Manual Import candidates
  -> Targeted Arr rescan
  -> Verifying
  -> Imported / Partial
```

Full-series UI grouping still never merges the underlying media files. Individual episode releases remain separate on Zurg.

## Upgrade / compatibility

- Version: `13.1.1`
- Docker image: `arrnexus:v13.1.1`
- Existing database remains `/mnt/appdata/arrnexus/data/router.db`
- No database reset is required
- Existing ArrNexus users, service connections, Force Matches, Queue Janitor state and Missing Media state are retained
- Existing v13 Magic writable mount is unchanged
- Main Zurg tree remains read-only

No new Portainer mount is required. After publishing/building the image, the existing stack only needs its image changed to:

```yaml
image: arrnexus:v13.1.1
```

## Validation

The packaged release was validated with **33 automated tests**, Python compilation, Jinja template compilation, JavaScript syntax validation, Compose YAML parsing and shell-script syntax checks.
