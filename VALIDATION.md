# ArrNexus v10.4.0-beta Validation

ArrNexus v10.4 is release-gated from the certified v10.3 baseline.

`validate.py` is the install-safe current-release validator used by the native updater. `validate_v104.py` is the release-certification entry point and retains the historical v7 → v10.3 validation layers before the v10.4-specific layer.

## v10.4-specific coverage

The v10.4 validator checks:

- Python compilation and Jinja template loading;
- JavaScript syntax when Node is available;
- fresh SQLite initialization and first-administrator setup;
- `/api/health`, DMM Inbox and Archived Media Recovery HTTP routes;
- undefined/blank/mixed Language Guard metadata becoming Manual review rather than a false non-English rejection;
- explicit foreign-language metadata remaining rejectable;
- the `Block import when language metadata is unknown` policy without making uncertainty destructive-safe;
- fingerprint-bound administrator English-override plumbing;
- first-volume RAR/multipart-RAR discovery and listing parsing;
- RAR path-traversal refusal and archive safety markers;
- ambiguous archive names such as `season-4_202405.rar` requiring identity resolution;
- TMDb media identity, confidence scoring and canonical TV/movie naming;
- source identity feeding the actual Radarr/Sonarr import path;
- series-first DMM TV grouping markers;
- pre-import naming/review workflow;
- recovered-media protection from provider cleanup;
- DUMB-visible recovery-root and archive size/free-space safety;
- RAR extractor/rebuild diagnostics;
- v10.4 service-worker cache, version ordering, README and generated documentation coverage.

## Release certification — 2026-08-30

Source-tree checks:

- retained v7 validator: **PASS**
- retained v8 validator: **PASS**
- retained v9 validator: **PASS**
- retained v9.1 validator: **PASS**
- retained v9.2 validator: **PASS**
- retained v9.3 validator: **PASS**
- retained v9.4 validator: **PASS**
- retained v10 validator: **PASS**
- retained v10.1 validator: **PASS**
- retained v10.2 validator: **PASS**
- retained v10.3 validator: **PASS**
- v10.4 validator: **PASS**
- real Uvicorn `/api/health`: **200**, version `10.4.0-beta`
- real Uvicorn `/setup`: **200**
- real Uvicorn `/`: **200**
- runtime/private-file scan: **PASS**
- high-confidence credential scan: **PASS**

Candidate-package checks were also run from a clean extraction of the ZIP and passed the retained validation layers, the install-safe `python3 validate.py` entry point and the real Uvicorn smoke.

The final release process rebuilds after this report is written, extracts that exact final ZIP into a second clean directory, reruns validation and the Uvicorn smoke, performs the private/runtime-file scan again, then calculates SHA-256 from that exact unchanged artifact.

## Historical validator shutdown note

Some older FastAPI/TestClient validators can print their final `PASS` after every assertion has completed but leave interpreter/background threads alive during Python shutdown. The v10.4 release runner uses unbuffered output and, only after the final `PASS:` marker has been emitted, gives the historical process a grace period and reaps its process group. No historical assertion is removed or skipped.

## Docker

Docker is not installed in the packaging environment, so `docker compose config` and an image build were **not** claimed. The v10.4 Dockerfile does install `ffmpeg` plus a 7zip-compatible RAR extractor (`p7zip-full` with a `7zip` fallback). Existing v10.3 containers must be rebuilt/redeployed before RAR inspection/extraction can work if the old image does not already contain a supported extractor.
