# ArrNexus v10.3.0-beta — Archive Rescue & Advanced Media Recovery

v10.3 combines the DMM/Language/Trakt fixes discovered during v10.2 testing with a new recovery path for difficult and archival TV media.

## Highlights

### Archive Rescue
- Scans monitored missing Sonarr media across discovered instances.
- Searches Internet Archive through Prowlarr.
- Requires a reviewable `.torrent` manifest for selective acquisition.
- Sends selected torrent files to Real-Debrid from inside ArrNexus.

### Advanced TV Recovery
- Better season/episode detection for archive-style names.
- Typed manual routes prevent TV destinations being validated as movie destinations.
- Combined-season detection and safe FFmpeg splitting with chapter-first confidence.
- Runtime-estimated boundaries require explicit confirmation.
- Outputs are ffprobe-verified and the original provider source is retained.

### DMM Language Guard workflow
- Bulk language checking and forced re-checks from Inbox.
- Re-check required state for stale policy results.
- Direct/bulk exact Real-Debrid cleanup only after destructive-safety and dependency checks.
- Item Review errors degrade inside ArrNexus instead of returning a bare 500.

### Trakt Device OAuth
- Device OAuth is the normal account-linking flow.
- App-level Client ID/Secret moved into Advanced setup.
- Pending, slow-down, denied, expired and connected states are handled explicitly.

### Product appearance rebuild
- Exactly Dark and Light remain.
- Light mode now has dark typography across the application shell and operational components.
- Legacy navy/blue surfaces are neutralised; purple/cyan remain restrained accents.

## Validation
The release gate retains v7 → v10.2 regression coverage, adds v10.3-specific parser/routing/language/Trakt/Archive Rescue/TV Recovery/theme tests, and then re-runs the validator plus a real Uvicorn smoke test against the exact extracted release ZIP.
