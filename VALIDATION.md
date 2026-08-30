# ArrNexus v10.4.1-beta Validation

v10.4.1 is a focused hotfix on top of the certified v10.4 baseline. The release gate retains the historical regression chain and adds explicit tests for selective, media-only partial RAR recovery.

## Current-layer regression cases

The v10.4.1 validator reproduces the field pattern found in a real Queen's Nose archive:

- 17 recognised video members are enumerated;
- 7-Zip returns archive exit code `2`;
- the archive reports `Unexpected end of archive`;
- one member (`Season 1.mp4`) reports `CRC Failed`;
- the other 16 members are independently reached by the 7-Zip test pass.

Expected result: **16 verified, 1 failed, 0 unverified**. The CRC-failed member is not selectable for recovery.

A second test proves that when a structurally damaged archive exits before reaching a member, that member remains **unverified** rather than being assumed safe.

## Media-only recovery contract

The current layer also checks that:

- torrent padding detection handles the observed `.___padding_file` / `.____padding_file` forms;
- normal `CRC = ...` listing metadata is not misclassified as an error;
- useful structural warnings are retained for display;
- Archive Recovery exposes a background media-verification route;
- recovery accepts selected verified media paths only;
- the UI exposes per-member verified/failed states and a selective recovery action;
- the recovered-media root is described as a persistent source, not the final library;
- service-worker cache and application version are v10.4.1.

A synthetic extraction harness was also run during certification with a mocked archive-level exit code `2`: only the two selected verified MP4 members were committed, while a failed member was refused.

## Retained validation

Historical/current layers are retained through:

`v7 → v8 → v9 → v9.1 → v9.2 → v9.3 → v9.4 → v10 → v10.1 → v10.2 → v10.3 → v10.4 → v10.4.1`

Historical TestClient validators are run in isolated processes because some older layers can print their final PASS marker and then keep interpreter/background threads alive during shutdown. Process isolation does not skip assertions; it only reaps a validator after its final PASS marker has been emitted.

## Native updater entry point

`python3 validate.py` runs the deterministic v10.4.1 current layer only. Full retained certification is performed separately with `validate_v1041.py` before packaging.

## Runtime smoke

The release is also launched with a fresh SQLite database under real Uvicorn and checked for:

- `/api/health` → HTTP 200 and version `10.4.1-beta`;
- `/setup` → HTTP 200;
- `/` → HTTP 200.

## Package hygiene

Before packaging and again against the exact extracted release ZIP:

- no `.env` runtime file;
- no SQLite/database files;
- no Python bytecode / `__pycache__`;
- no virtualenv/runtime directories;
- no high-confidence embedded credential material.

Docker image build is not claimed in the packaging environment when Docker is unavailable. v10.4.1 introduces no new OS package beyond the 7zip-compatible extractor already added by v10.4.
