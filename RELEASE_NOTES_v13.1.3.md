# ArrNexus v13.1.3

ArrNexus v13.1.3 is a focused **Magic Intake reliability and classification** hotfix.

It addresses the failures seen when many Magic Intake cards were started together in v13.1.2. Those failures occurred before the Zurg move stage: the stored `destination_arr_path` remained empty, which means the affected releases were never moved. v13.1.3 protects Sonarr, Radarr and Lidarr with controlled import concurrency and records stage-specific exception details instead of allowing timeout-style exceptions to appear as a blank `Import failed` card.

## Magic Intake import queue

- Maximum active imports is now **3 by default**.
- Additional clicks remain queued instead of all hitting the Arr APIs at once.
- `MAGIC_IMPORT_CONCURRENCY` can be set from 1 to 6.
- Each queued card reports that it is waiting for an import slot.
- Existing canonical-title duplicate protection remains in place.

This avoids an import storm where dozens of jobs simultaneously call Sonarr/Radarr/Lidarr for catalogue lookups, quality profiles, root folders, Manual Import and rescans.

## Failed import diagnostics

v13.1.2 stored `str(exception)` as the failure reason. Timeout and pool exceptions can stringify to an empty string, producing cards that showed only `Import failed` with no explanation.

v13.1.3 now records:

- the import stage;
- the exception class;
- a non-empty exception representation;
- the same information in the Magic Intake event log.

Examples now look like:

```text
Resolving target in Arr: ReadTimeout: ReadTimeout('')
```

or:

```text
Resolving target in Arr: ArrError: Sonarr: Magic destination root '...' is not configured as an Arr root folder. Configured root folder(s): ...
```

A later canonical scan also no longer blanks an existing failed/source-missing error message.

## Safe recovery of blank v13.1.2 failures

On upgrade, a failed Magic Intake row is automatically returned to a retryable state only when all of these are true:

- state is `failed`;
- stored error is blank;
- no Arr destination path was ever recorded.

That combination proves the job failed before ArrNexus reached the Zurg move stage. No media move is repeated automatically; the card simply becomes safe to retry under the controlled v13.1.3 queue.

## Arr root validation

Before ArrNexus adds a brand-new movie, series or artist, the chosen Magic destination root is checked against the root folders configured in the corresponding Arr application.

If it is not registered, the import stops before any Zurg move and shows the exact destination plus the Arr roots that are actually configured.

Existing items already present in Radarr/Sonarr/Lidarr do not require this add-time root validation.

## Persistent Movie / TV Series / Music override

Every active Magic Intake card now has a **Media type** selector:

- Movie
- TV Series
- Music

Changing the type while the source is still at top-level `__magic__`:

1. stores a source-level override;
2. clears the stale match from the wrong Arr service;
3. triggers a new scan;
4. rematches against Radarr, Sonarr or Lidarr as appropriate;
5. keeps that type on future scans.

This fixes cases where a movie filename contains an episode-looking token and is incorrectly classified as a Sonarr series.

The type selector locks once the release has passed the move stage, preventing an unsafe post-move reclassification.

## Retained from v13.1.2

- explicit `Moved - awaiting Arr`, `Partially verified`, `Numbering mismatch`, `Verification timeout`, `Source missing`, `Import failed` and `Imported` states;
- delayed Arr verification and automatic late promotion to Imported;
- `Recheck now` for post-move verification;
- canonical one-card-per-series/artist grouping;
- server-side filtering and pagination;
- lightweight Magic Intake polling;
- background FUSE scanning;
- exact Sonarr episode verification;
- Zurg-first read-only architecture outside the dedicated writable `__magic__` mount;
- Missing Media Orchestrator and Queue Janitor.

## Upgrade

No database wipe and no new mount are required.

Build:

```bash
./scripts/pull-build-v13.1.3.sh
```

Portainer image:

```yaml
image: arrnexus:v13.1.3
```

Recommended optional stack setting:

```yaml
MAGIC_IMPORT_CONCURRENCY: "3"
```

Existing `/mnt/appdata/arrnexus/data/router.db` is migrated in place.
