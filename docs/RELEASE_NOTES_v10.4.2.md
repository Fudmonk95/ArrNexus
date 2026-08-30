# ArrNexus v10.4.2-beta — Stable Archive Identity Hotfix

v10.4.2 fixes the field failure where **Verify media files** could immediately end with `RAR source changed after preview; inspect it again` even though the Real-Debrid/Decypharr archive had not actually changed.

## Root cause

v10.4.1 included the resolved `/proc/<Radarr PID>/root/...` path and integer `mtime` in the RAR fingerprint. Decypharr/DUMB can legitimately refresh virtual metadata or expose the same mount through a different process PID. That made the safety fingerprint change even while the provider archive and its media catalogue were identical.

## Fix

- Stable archive source identity is now based on the logical DMM path plus provider-visible file size.
- Transient `/proc` PID values and virtual modification time are never used as content identity.
- Archive inspection produces a **catalogue signature** from non-padding member paths, listed/packed sizes, encryption state and CRC metadata.
- Verification re-lists the archive after the media test; if the catalogue changed during verification, the result is discarded.
- Extraction re-lists again and requires an exact match with the verified catalogue before recovering selected media.
- A same-size archive replacement with different members/CRCs is therefore still rejected.
- TMDb archive identity saved on v10.4/v10.4.1 is migrated to the stable fingerprint when it can be resolved safely.

## Retained from v10.4.1

- Media-only RAR inspection and extraction.
- Torrent padding/support files excluded.
- Background per-member verification.
- CRC/data-error members marked failed.
- Good members remain recoverable from partially damaged archives.
- Selected recovered files must match listed size and pass `ffprobe`.
- Original provider RAR is retained.

## Container requirements

No new OS dependency is introduced. If the v10.4 image was already rebuilt and `7z` is available, v10.4.2 is an application-only browser update.
## Additional field fixes included

### Language Guard stale decisions

The language evaluator was corrected in v10.4, but the cache namespace was not changed with the algorithm. A source could therefore reuse an older cached `fail` result even when the current evaluator would classify the same undefined/unknown audio metadata as Manual Review. v10.4.2 bumps the cache schema so those decisions are re-probed under the current rules.

Import jobs also track **Manual Review** separately from **Language rejected**. Unknown/probe-incomplete sources remain blocked when strict unknown handling is enabled, but they are not described or counted as confirmed non-English media.

### Existing Radarr/Sonarr titles

An import can already be present in the target Arr even when title/year matching misses it. v10.4.2 performs a stable external-ID check (`tmdbId` for Radarr, `tvdbId` for Sonarr) before POSTing a new item. Existing items are reused, so expected duplicate validators such as `MovieExistsValidator` and an already-configured movie path are idempotent ownership states instead of job failures.

