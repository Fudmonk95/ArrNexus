# ArrNexus v10.6.1-beta Validation

`python3 validate.py` runs the deterministic v10.6.1 current-layer validator. Release certification also runs the retained historical compatibility chain.

v10.6.1 validation covers:

- Python compilation and Jinja template compilation;
- exact direct-source resolution when the Decypharr source-pack directory is an extensionless form of the RD archive filename;
- hard-stop behaviour when provider CRC exists but the authoritative RD original cannot be resolved;
- proof that provider bytes are not copied in that unresolved-direct state;
- clean SQLite startup/migration through the retained validators;
- exact Real-Debrid source-file metadata resolution without exposing signed URLs during preview;
- provider-mount vs direct-original byte-size mismatch handling;
- direct Real-Debrid staging being preferred after provider CRC failure even when a sequential mount read could succeed;
- the direct original becoming authoritative for all archive-member verification;
- retained v10.5.1 EIO retry/direct-download fallback and safe staged extraction;
- Sonarr missing-media scanning and active-download detection;
- Radarr monitored-missing scanning and active-download detection;
- Archive Rescue UI coverage for both Sonarr and Radarr;
- dedicated Sonarr Rescue and Radarr Rescue routes;
- version-badge update modal, server-side newer-than comparison, no-cache update APIs and post-update browser cleanup;
- authenticated route smoke checks for `/sonarr-rescue`, `/radarr-rescue`, `/archive-rescue` and `/api/health`.

Historical validators are retained from v7 through v10.5.1 so recovery, Language Guard, job control, recovered-media indexing, TV source selection and older acquisition behaviour remain covered.

No validator contacts production Arr instances, Real-Debrid, Prowlarr or production databases. External integrations are mocked or exercised only through isolated temporary data.
