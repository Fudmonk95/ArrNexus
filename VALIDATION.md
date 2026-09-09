# ArrNexus v13.1.0 validation

Validation performed against the v13.1.0 release tree before packaging.

## Automated tests

`python -m unittest -v tests/test_v13.py`

**31 tests passed.** Coverage includes:

- existing Missing Media Orchestrator behaviour;
- Queue Janitor classifications, dry-run safety and retry limits;
- safe ID/manual-import handling;
- NeutArr coexistence;
- canonical TV grouping by Sonarr/TVDB identity;
- canonical music grouping by Lidarr artist identity;
- multi-episode `S01E07-E09` parsing;
- legacy `1x02` episode parsing;
- nested audio folder classification;
- Magic Intake genre/theme filter metadata;
- per-release Force Match override persistence;
- v13.0.0 -> v13.1.0 SQLite schema migration;
- preservation of an existing v13 match if the first upgrade metadata lookup temporarily fails;
- exact expected-episode Sonarr verification rather than whole-library counts;
- Lidarr album lookup endpoint use;
- non-blocking background import queueing; and
- FastAPI page rendering + `/api/health` version reporting.

## Static/runtime validation

Passed:

- Python `compileall` for `app/` and `tests/`;
- all Jinja templates compile;
- `node --check app/static/app.js`;
- `docker-compose.yml` YAML parse;
- `portainer-stack.yml` YAML parse;
- both Compose files point to `arrnexus:v13.1.0`;
- Bash syntax checks for release/build helper scripts;
- `VERSION` is exactly `13.1.0`;
- runtime scan found no stale `arrnexus:v13.0.0` image reference or `APP_VERSION = "13.0.0"` fallback.

## Upgrade behaviour specifically checked

v13.1.0 adds Magic Intake columns to an existing v13 database in place:

```text
canonical_key
genres_json
progress
progress_detail
job_id
```

Existing confidence-100 Force Matches are migrated to per-source overrides before canonical regrouping.

The first v13.1 scan re-resolves old v13 cards so genres/canonical IDs can be refreshed, but falls back to the existing match identity if the Arr metadata lookup is temporarily unavailable.

## Packaging limitation

Docker is not installed in this packaging environment, so the actual `docker build` and live Zurg-FUSE move cannot be executed here. The final image build is performed on the Debian host using:

```bash
./scripts/pull-build-v13.1.sh
```

The release package preserves the same v13 writable Magic mount and read-only main Zurg mount.
