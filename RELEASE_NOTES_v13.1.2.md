# ArrNexus v13.1.2

ArrNexus v13.1.2 is a focused **Magic Intake verification and recovery** release. It keeps the canonical grouping, server-side filtering, background imports and large-library performance work from v13.1.0/v13.1.1, but replaces the vague catch-all `partial` result with explicit post-move states and delayed Arr verification.

## Why this release exists

In v13.1, a release could be moved successfully inside Zurg but still show `partial` simply because Sonarr, Radarr or Lidarr had not confirmed the file before the short verification timeout. That made successful-but-slow imports look broken. It also made episode-numbering mismatches such as `64 Zoo Lane` hard to distinguish from a genuine failed import.

v13.1.2 separates those outcomes.

## Explicit Magic Intake states

Magic Intake can now report:

- **Moved - awaiting Arr** — the Zurg virtual move completed and ArrNexus is waiting for the relevant Arr to register it.
- **Partially verified** — some, but not all, expected Sonarr episode identities are confirmed.
- **Numbering mismatch** — Sonarr has files in the expected season(s), but the exact incoming `SxxExx` markers do not line up. This is intended for alternate/DVD/absolute/air-order style numbering problems rather than pretending the files are missing.
- **Verification timeout** — the media was moved but Arr did not confirm it within the configured background verification window.
- **Source missing** — the original top-level `__magic__` release is gone and the expected destination cannot be safely reconciled.
- **Import failed** — an actual API/filesystem/import error occurred before a safe completion state was reached.
- **Imported** — Arr positively confirms the expected media.

Legacy v13.1 `partial` records are reclassified during the in-place schema upgrade when their saved detail is specific enough to identify a partial verification, verification timeout or missing source.

## Delayed background verification

A short initial verification still runs immediately after the virtual move, ManualImport attempt and targeted Arr rescan. If Arr has not finished processing yet, the browser request is not held open and the item is not permanently labelled failed.

By default ArrNexus now:

- keeps verification active for **10 minutes**;
- rechecks pending imports every **30 seconds**;
- automatically promotes a late Radarr/Sonarr/Lidarr confirmation to **Imported**;
- turns a still-unconfirmed `Moved - awaiting Arr` item into **Verification timeout** only after the configured window;
- gives timed-out jobs a cheap automatic retry every five minutes, so a slow Arr can still recover without manual babysitting.

Both the verification window and recheck interval are configurable in Magic Intake scanner settings.

## Better Sonarr diagnosis

For TV imports ArrNexus still verifies the exact expected season/episode identities rather than counting unrelated existing episodes.

If 30 of 33 expected episodes are present, the state is now:

```text
Partially verified
Sonarr confirms 30/33 expected episode file(s)
```

If none of the exact 93 markers match, but Sonarr already has files in the same expected seasons, the state becomes:

```text
Numbering mismatch
Sonarr has files in the expected seasons, but 0/93 exact SxxExx markers match
```

This is deliberately different from `0/93 missing`.

## Safe retry / stale-source handling

Once a Magic Intake job has passed the filesystem-move stage, ArrNexus will no longer blindly attempt the same move again.

- Post-move states disable the Import button.
- A new **Recheck now** action asks the relevant Arr to verify the existing destination instead.
- If a previous attempt already moved the source and the expected destination exists, a retry switches to Arr reconciliation, ManualImport/rescan and verification rather than throwing another `No such file or directory` move error.
- Actual pre-move failures remain retryable.

This specifically addresses stale cards where the error showed the release had already moved from top-level `__magic__` into `movies/...` or `tv/...`.

## UI changes

Magic Intake now shows separate counters for:

- Importing
- Awaiting Arr
- Needs attention
- Imported

The State filter includes the new verification states, and post-move warning states use clearer card borders without treating every incomplete verification as a red fatal error.

## Retained v13.1.1 fixes

v13.1.2 includes the previous hotfix work:

- canonical one-card-per-series/movie/artist grouping;
- 72-card pagination and server-side filters;
- lightweight polling instead of returning the complete Magic catalogue repeatedly;
- FUSE discovery off the FastAPI event loop;
- background Scan Now;
- one active import per canonical identity;
- stale duplicate cleanup from pre-v13.1.1 active rows;
- Force Match modal without scroll-to-top;
- movie folders containing video + soundtrack/commentary audio remain Movies rather than being misclassified as Music.

## Upgrade / compatibility

- Version: `13.1.2`
- Docker image: `arrnexus:v13.1.2`
- Existing database remains `/mnt/appdata/arrnexus/data/router.db`.
- No database reset is required.
- Existing users, service connections, Force Matches, Missing Media state and Queue Janitor state are retained.
- Main Zurg mount remains read-only.
- Only `/zurg_mnt/zurg/__magic__` is mounted writable as `/zurg_magic`.
- No new Portainer mounts are required if v13 is already deployed correctly.

The only Portainer image change is:

```yaml
image: arrnexus:v13.1.2
```
