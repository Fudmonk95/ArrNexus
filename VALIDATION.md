# ArrNexus v10.4.3-beta Validation

v10.4.3 is a focused field hotfix on top of the certified v10.4.2 baseline.

## Current-layer regressions

The v10.4.3 validator checks:

- independent one-command-per-media RAR verification: a synthetic CRC-broken Season 1 does not stop Seasons 2 and 3 from verifying;
- mixed explicit-fail + unknown Language Guard evidence resolves to Manual Review and is never destructive-safe;
- the Language Guard cache namespace is `v1043`;
- Language Inbox attention filtering excludes current-policy passes and retains unknown/fail/re-check results;
- legacy `/data/split-cache` automatically resolves to the DUMB-visible recovered-media root;
- TV Recovery source/output code accepts ArrNexus recovered-media paths and writes through `view_path`;
- service-worker and application version markers are v10.4.3.

## Retained validation

Historical/current layers are retained through:

`v7 → v8 → v9 → v9.1 → v9.2 → v9.3 → v9.4 → v10 → v10.1 → v10.2 → v10.3 → v10.4 → v10.4.1 → v10.4.2 → v10.4.3`

`python3 validate.py` runs the deterministic v10.4.3 current layer. Full release certification runs historical validators separately, then repeats current validation and real Uvicorn smoke against the exact extracted ZIP.

Package hygiene rejects runtime `.env`, SQLite/database files, bytecode/cache directories and high-confidence embedded credential material. Docker build is not claimed when Docker is unavailable in the packaging environment.
