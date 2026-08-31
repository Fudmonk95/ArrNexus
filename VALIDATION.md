# ArrNexus v10.6.2-beta Validation

`python3 validate.py` runs the deterministic v10.6.2 current-layer validator. Release certification also runs retained v10.6.1/v10.6/v10.5.1 recovery validators and package hygiene checks.

v10.6.2 validation covers:

- exact Real-Debrid archive torrent + exact selected file;
- Decypharr extensionless archive folder -> `.rar` RD torrent identity;
- selected-file basename `.rar` stripping;
- missing/normalised Real-Debrid selection flags;
- exact single-file torrent with a rewritten internal RD file path;
- exact single-file torrent where RD omits file rows but exposes one link and byte count;
- ambiguous multi-file selections remain rejected;
- direct-source resolution failure never falls back to provider-mounted archive bytes after CRC anomalies;
- authoritative direct-original size/verification flow from v10.6.1;
- Sonarr/Radarr Rescue and updater behavior retained;
- template compilation and temporary HTTP route/version checks.
