# ArrNexus v10.6.3-beta - Generated Archive Link Recovery Hotfix

## Why this hotfix exists

Live Queen's Nose recovery exposed the last incorrect assumption in the v10.6 direct-source resolver. ArrNexus correctly stopped trusting the Decypharr-mounted RAR, but still assumed the requested `.rar` must exist as one of Real-Debrid's torrent file rows.

The real torrent metadata showed otherwise. The exact torrent `season-4_202405` contains individual payload files such as `Season 1.mp4`, `Season 2.mp4`, metadata and padding files, while Real-Debrid exposes exactly **one** generated download link. The mounted Decypharr `.rar` reports `3,544,186,880` bytes - the payload sum - while the normally downloaded RAR is `3,544,189,222` bytes and passes 7-Zip completely, including `Season 1.mp4`.

## Correct generated-archive handling

For this exact topology ArrNexus now:

1. resolves the backing torrent using exact source/archive identity only;
2. recognises that many payload rows plus exactly one RD link can represent a generated archive;
3. does **not** search those payload rows for a `.rar` that is not present;
4. unrestricts the sole RD link;
5. verifies the returned filename is an exact archive/stem equivalent;
6. takes the unrestricted `filesize` as the authoritative physical archive size;
7. downloads that generated archive directly to `/mnt/debrid/arrnexus-extracted/.arrnexus-staging`;
8. re-verifies every media member against that local file; and
9. extracts only from the verified local archive.

The mounted provider representation remains untouched and is never allowed to confirm archive corruption after a provider CRC anomaly.

## Safety remains strict

The new generated-link path is accepted only when the torrent identity is exact and exactly one RD link exists. A returned filename that does not exactly match the requested archive is rejected. Multiple links, multiple equally matching torrents, or other ambiguity remain hard failures.

ArrNexus also checks the generated archive filename and filesize again immediately before download, preventing a changed RD link from silently becoming the approved recovery source.

## Retained v10.6 functionality

- Sonarr and Radarr Archive Rescue;
- dedicated Sonarr Rescue and Radarr Rescue pages;
- review-first Debrid handoff;
- updater/version-badge modal fixes;
- v10.6.1 authoritative-source/no-provider-fallback rule;
- v10.6.2 safe single-file/stem resolver variants;
- v10.5.1 resilient extraction and provider-I/O handling;
- Language Checks bypass/manual-review controls;
- season-aware partial TV imports and recovered-media source selection.

## Recovery root

The logical ArrNexus recovery root remains:

```text
/mnt/debrid/arrnexus-extracted
```

No production provider archive or existing recovered episode is deleted by this verification flow.
