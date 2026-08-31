# ArrNexus v10.4.4-beta — Unified Recovery & TV Intelligence

## Why this release exists

Real-world RAR recovery exposed three remaining architecture gaps: very large archives could outlive Cloudflare's request window, recovered media lived outside the normal Inbox inventory, and TV recovery trusted filenames/owned-Sonarr metadata too heavily. v10.4.4 closes those gaps.

## Large archive inspection

RAR inspection is now a background job. Pressing **Inspect** queues the catalogue read and immediately opens the job page. The completed catalogue is cached and **Open inspection** reads that cache only. A 100+ GB archive can therefore continue inspecting even after the user leaves the page, without holding a Cloudflare HTTP request open.

The original archive remains unchanged. Verification and extraction remain separate explicit stages.

## Recovered media joins DMM Inbox

ArrNexus now inventories both the configured DMM/provider source root and the recovered-media root (default `/mnt/debrid/arrnexus-extracted`). Each top-level recovery folder is one source pack.

Example after recovering three additional Tracy Beaker season archives:

```text
The Story of Tracy Beaker
5 source packs · seasons 1, 2, 3, 4, 5

Season 1 · RAR recovered
Season 2 · RAR recovered
Season 3 · RAR recovered
Season 4 · DMM/provider
Season 5 · DMM/provider
```

Archive TMDb identity is copied onto the recovered folder's own fingerprint so those packs group by the same canonical series identity.

## Runtime-aware TV recovery

Advanced TV Recovery now analyses every TV video. It combines:

1. explicit filename evidence;
2. Sonarr season episode counts when the series is already owned;
3. TMDb season episode counts when Sonarr is not matched;
4. TMDb per-episode runtime samples;
5. actual media duration and chapters from `ffprobe`.

This catches both explicit joins such as `S03E06-7.mp4` and badly named files such as `S03E06.mp4` that actually run for roughly two normal episodes. Runtime-only split boundaries are always shown for review and require explicit confirmation.

Combined-season videos use the same logic: TMDb can provide the season episode count before the show exists in Sonarr, allowing a safe reviewed runtime plan instead of `manual · 0%`.

## Storage and source retention

Generated split episodes stay under the DUMB-visible recovered-media source tree. When a recovered joined/combined file has been successfully split, the original recovered copy is retained under `.arrnexus-originals` and excluded from future Inbox/import scans. The original provider RAR remains untouched.

## Retained v10.4.x safety

- per-member RAR verification;
- selective verified-media-only extraction;
- failed CRC members remain ineligible;
- torrent padding/support files are never normal recovery outputs;
- stable Decypharr catalogue signatures;
- Language Guard uncertainty remains non-destructive;
- existing Radarr/Sonarr items are treated idempotently.

## Container/update note

v10.4.4 adds no new OS package. Installations already running the v10.4+ image with 7-Zip can install this release through the normal browser updater without another Docker image rebuild.
