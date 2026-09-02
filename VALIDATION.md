# ArrNexus v10.8.1-beta Validation

`python3 validate.py` runs the deterministic v10.8.1 current-layer validator. Release certification also runs the retained v7 through v10.6.3 validators plus package-hygiene and clean-start checks.

v10.8.1 validation adds recovery destination resolution, retry-state compatibility, interrupted-job reconciliation and process-tree cancellation checks. The retained v10.8 validation covers advisory-only language handling, stale language-state migration, the generic live job terminal, no-flash theme bootstrap, normalized media automation definitions, safe Kometa YAML import, non-destructive previews, Kometa/Plex and native Jellyfin/Emby adapter contracts, schedules, cancellation, route/template compilation and clean FastAPI routes. The retained v10.7 source contracts continue to cover persistent archive recovery stages, split state, batch reindexing, retry and cancellation.

Retained v10.6.3 validation covers:

- the exact live Queen's Nose Real-Debrid topology: extensionless torrent identity, many selected payload files, one generated RD archive link;
- authoritative physical generated-RAR size `3,544,189,222` bytes rather than the `3,544,186,880` selected-payload/mounted size;
- generated archive filename validation after `/unrestrict/link`;
- signed direct URL is never exposed by metadata-only resolution;
- generated archive identity/size is checked again immediately before download;
- wrong generated archive filename is rejected;
- multiple RD links remain ambiguous and rejected;
- provider CRC anomalies still cannot fall back to Decypharr-mounted bytes when Real-Debrid is connected;
- retained v10.6.2 single-file resolver variants;
- retained v10.6.1 authoritative-source safety;
- retained v10.5.1 extraction/EIO, Language Guard review and dismissible job controls;
- Python compilation and clean FastAPI `/api/health`, `/`, `/setup` route checks.
