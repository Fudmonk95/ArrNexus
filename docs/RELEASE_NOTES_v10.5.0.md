# ArrNexus v10.5.0-beta — Recovery Control & Reliable Imports

## Why this release exists

Real-world recovery of **The Queen's Nose** and **The Story of Tracy Beaker** proved that v10.4.4 could recover useful media from damaged/cloud-backed RARs, but the workflow still had four operational weaknesses: language metadata could unnecessarily block known-English legacy media, long jobs could not be stopped, recovered symlinks were not counted as imported, and one bad/combined season could derail a grouped series import. v10.5.0 addresses those control and reliability gaps without weakening the existing recovery safety model.

## Language Checks master control

A persistent **Language Checks ON / OFF** master switch is available from DMM Inbox and Settings.

When OFF, import does not launch ffprobe language checks, does not require a re-check or English override, ignores stale unknown/failed/rejected Language Guard state for import decisions, and never performs language-driven provider cleanup. Existing policy, scan results and exact-source overrides are retained unchanged so turning checks back ON restores the normal Language Guard workflow.

## Cancellable Import Jobs

Running import/recovery jobs now expose **Cancel job**. Workers periodically check the cancellation flag; long 7-Zip, ffprobe and ffmpeg subprocesses are terminated cleanly and killed only if they do not exit. Cancellation becomes the `cancelled` job state rather than a failure. Original provider archives, valid source media, completed recovered episodes and unrelated library links are retained. Operation-local `.partial` output is cleaned where safely identifiable.

Finished job history can be removed individually or cleared in bulk without touching any media.

## Season-aware grouped TV import

Grouped TV imports now build a season/episode plan across every source pack. Individual recovered episodes are preferred over overlapping provider packs; safe individual episodes can import even while another season still needs TV Recovery. Inferior overlapping packs are annotated **Superseded — covered by preferred source** and remain reviewable. ArrNexus does not delete the underlying provider source simply because it is superseded.

A mixed show can therefore report results such as:

- Season 3 imported
- Season 6 imported
- Season 7 imported
- Season 2 needs recovery
- Season 4 needs recovery
- Season 5 needs recovery
- Season 1 unavailable/CRC damaged

## Recovered media now counts as imported

The library source-link index now recognises both the configured DMM/provider root and the recovered-media root. Symlinks whose targets live under `/mnt/debrid/arrnexus-extracted` update the DMM Inbox exactly like normal provider targets, fixing successful recovered imports that previously stayed at **Waiting**.

The logical recovery path remains exactly:

```text
/mnt/debrid/arrnexus-extracted
```

No ArrNexus path is changed to the Proxmox host or CT bind-mount path.

## CRC retry from local staging

A provider-side CRC failure is still treated as a failure; v10.5 never blindly ignores CRC. Instead, after a failed verification the UI can explicitly **Retry from local staging**. ArrNexus shows the archive volume-set size and recovery-storage free space before confirmation, then copies the archive to `.arrnexus-staging` under the recovered-media disk as a cancellable background job and re-runs independent member verification locally.

If a previously failed member passes locally, it is classified as a provider/virtual read-path issue and extraction may use the verified staged copy. If it still fails locally, it remains ineligible as genuine CRC/archive damage. The provider archive is retained in both cases.

## TV Recovery job control

ffmpeg splitting now runs through the same persistent job system, so long combined-season recovery can be cancelled safely. Completed verified episode outputs remain; the current incomplete `.partial` output is removed. `.arrnexus-originals` remains excluded from normal scanning/import.

## Safety retained from v10.4.x

- background/cached huge-RAR inspection;
- independent per-member verification;
- verified-media-only extraction;
- TMDb inherited archive identity;
- runtime-aware TV Recovery;
- ffprobe verification of generated episodes;
- `.arrnexus-originals` exclusion;
- provider archive retention;
- stable Radarr/Sonarr external-ID duplicate handling;
- DUMB namespace-safe library symlinks;
- exact-source language overrides;
- non-destructive provider cleanup rules.

## Upgrade note

No production database reset is required. SQLite migration adds cancellation state and per-job result metadata in place. Existing language settings and caches are retained. Installations already using the v10.4+ container image with 7-Zip/ffmpeg/ffprobe do not require a new OS package solely for v10.5.0.
