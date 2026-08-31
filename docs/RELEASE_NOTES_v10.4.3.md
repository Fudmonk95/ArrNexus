# ArrNexus v10.4.3-beta — Recovery Pipeline & Language Inbox Hotfix

v10.4.3 addresses the remaining field issues discovered while recovering a partially damaged *The Queen's Nose* RAR and re-checking legacy Language Guard results.

## Independent media verification

RAR verification now executes one test per recognised video member. This is deliberately slower than a single bulk command but is much more useful for damaged/cloud-backed archives: one CRC failure no longer prevents the remaining seasons or episodes from being independently proven recoverable.

Verification remains backgrounded and now reports member-level progress. Only independently verified media can be selected for extraction.

## Unified recovered-media storage

Advanced TV Recovery no longer uses `/data/split-cache` as its default output. Split episode files are written to the DUMB-visible recovered-media source tree, defaulting to:

`/mnt/debrid/arrnexus-extracted`

When a combined-season video was itself recovered from a RAR, generated `Season XX` episode folders are placed beside that source. This keeps the real media bytes in one recovery namespace while Sonarr/Radarr/Jellyfin libraries resolve symlinks back to it.

## Language view cleanup

The DMM Language tab now removes current-policy passes before duplicate-title grouping. A source copy that passes a re-check leaves the Language view immediately; another unresolved duplicate may remain as its own grouped issue.

## Bernard's Watch / mixed-language uncertainty

Source-level evaluation now gives uncertainty precedence over confirmed failure. If any file in a multi-file source has undefined/unlabelled language metadata or a probe failure, the whole source is Manual Review even if another member carries an explicit non-English tag. A source becomes confirmed `language_rejected` only when the inspected set is complete and contains no unknown/probe-failed members.

The Language Guard cache namespace is bumped to `v1043`, forcing affected historical decisions through this corrected evaluator.

## Deployment

No new OS dependency is introduced. Installations that already rebuilt the v10.4 image for 7-Zip can install v10.4.3 through the normal browser updater without another container rebuild.
