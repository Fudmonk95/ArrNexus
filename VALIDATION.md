# ArrNexus v10.4.4-beta Validation

v10.4.4 is the unified recovery/TV-intelligence field release on top of the certified v10.4.3 baseline.

## Current-layer regressions

The v10.4.4 validator checks:

- multi-episode filename parsing preserves `S03E06-7`, `S03E06-E07`, `S03E06E07` and `3x06-07`;
- canonical naming preserves multi-episode ranges;
- a nominal single E06 file with a 2× typical runtime is classified as a joined E06-E07 source;
- explicit joined files keep the correct episode offset in generated boundaries;
- combined-season files use metadata episode counts to produce confirmation-required runtime plans;
- large RAR inspection is routed through a background job and the review page reads only cached inspection data;
- recovered media is scanned into Inbox alongside provider sources with source-pack provenance;
- TMDb season runtime/count helpers and the pre-Sonarr TV recovery gate are present;
- service-worker and application version markers are v10.4.4.

## Retained validation

Historical/current layers are retained through:

`v7 → v8 → v9 → v9.1 → v9.2 → v9.3 → v9.4 → v10 → v10.1 → v10.2 → v10.3 → v10.4 → v10.4.1 → v10.4.2 → v10.4.3 → v10.4.4`

`python3 validate.py` runs the deterministic v10.4.4 current layer. Full release certification runs historical validators separately, then repeats current validation and real Uvicorn smoke against the exact extracted ZIP.

Package hygiene rejects runtime `.env`, SQLite/database files, bytecode/cache directories and high-confidence embedded credential material. Docker build is not claimed when Docker is unavailable in the packaging environment.
