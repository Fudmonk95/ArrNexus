# ArrNexus v10.6.3-beta Validation

`python3 validate.py` runs the deterministic v10.6.3 current-layer validator. Release certification also runs the retained v10.6.2/v10.6.1/v10.6/v10.5.1/v10.5/v10.4.x validators plus package-hygiene and clean-start checks.

v10.6.3 validation covers:

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
