# ArrNexus v10.6.0-beta - Rescue Expansion & Direct Archive Integrity

## Why this release exists

v10.6 addresses two real operational gaps. First, monitored Sonarr/Radarr media can sit wanted for long periods when the normal Arr/Prowlarr path finds nothing useful. Second, live RAR recovery testing proved that a Decypharr-mounted archive can be readable and report healthy at the filesystem/service level while still exposing bytes that are not identical to the original Real-Debrid file.

During the Queen's Nose test, a direct Windows download of the RAR passed both full-archive and Season 1 verification in 7-Zip and extracted/played correctly. The same logical RAR read through the Decypharr mount exposed a different byte length and SHA-256 and produced a CRC failure. v10.6 changes ArrNexus so mounted-provider CRC errors are no longer treated as definitive corruption.

## Direct original archive verification

When provider-side verification reports CRC/EIO and the source maps exactly to a Real-Debrid torrent file, ArrNexus now:

1. reads the authoritative file size from Real-Debrid metadata;
2. compares it with the provider-mounted file length;
3. marks a mismatching mount as untrusted;
4. downloads the exact original over authenticated Real-Debrid HTTPS to the recovery staging area;
5. verifies every media member from that complete local original;
6. extracts locally verified members only from that staged original.

The mounted provider archive is never modified or deleted. Signed RD URLs are not stored in logs/cache. Exact source/torrent/file matching is required; ambiguous mappings fail closed.

A direct-original failure is now the boundary for calling archive damage confirmed. If the direct original passes, the previous provider-side CRC is classified as a provider-mount/read-path integrity issue.

The logical recovery namespace remains:

```text
/mnt/debrid/arrnexus-extracted
```

## Archive Rescue now includes Radarr

Archive Rescue can scan both:

- monitored Sonarr series with missing episodes; and
- monitored Radarr movies with no imported file.

Both can search the configured Prowlarr Internet Archive indexer. Torrent manifests remain reviewable before any handoff to Real-Debrid.

## Sonarr Rescue and Radarr Rescue

Two dedicated acquisition-rescue pages now provide an explicit fallback for wanted media that normal automation has not filled.

ArrNexus searches configured torrent sources through Prowlarr, ranks candidates, annotates Real-Debrid instant availability and shows identity/policy information before an administrator chooses a handoff. Real-Debrid is used as cache/handoff infrastructure; ArrNexus does not pretend RD provides a general media search API.

Items already active in an Arr download queue are clearly marked so rescue does not encourage duplicate acquisition. No rescue candidate is automatically grabbed.

## Native updater UX fixes

The top version badge now opens the update dialog directly. v10.6 also fixes stale version/update state so:

- an installed version cannot be offered as its own update;
- update APIs are returned with no-cache headers;
- browser-side cached metadata is tied to the currently running version;
- successful restart/version confirmation clears stale update state; and
- the update modal closes and reloads ArrNexus after a verified successful installation.

## Retained safety

All v10.5.1 recovery controls remain, including resilient provider EIO retries as a fallback, cancellable archive jobs, provider retention, Language Checks bypass behaviour, Import Job review controls, recovered-media source selection and safe `.arrnexus-originals` exclusion.

## Container note

v10.6 adds no new required OS package beyond the existing v10.4+ archive tooling. Existing v10.5.1 installations using the self-update bootstrap can install the application release through the normal ArrNexus updater.
