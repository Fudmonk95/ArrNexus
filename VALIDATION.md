# ArrNexus v12.0.0 validation

Validation performed against the complete v12 release tree before packaging.

## Automated source/runtime checks

- Python `compileall` passed for all application and test modules.
- AST parsing passed for every Python source file.
- All Jinja templates compiled successfully.
- `docker-compose.yml` and `portainer-stack.yml` parsed successfully as YAML and contain a `services` definition.
- All supplied shell scripts passed `bash -n` syntax validation.
- Runtime source scan found none of the retired low-level integration names that must stay out of the v12 runtime.
- Package safety scan found no `.env`, SQLite or `.db` runtime files in the release tree.
- Version scan confirms the release reports exactly `12.0.0`.

## v12 test suite

`PYTHONPATH=. python -m unittest -v tests.test_v12`

16 tests passed, covering:

1. v12 recovery/orchestration SQLite schema creation;
2. Sonarr multi-episode season grouping into `SeasonSearch`;
3. expired Missing Media cooldown becoming eligible again;
4. NeutArr coexistence deferring automatic Missing Media dispatch;
5. invalid season/episode mapping taking priority over broad Manual Import wording;
6. ID/grab-history Manual Import classification;
7. failed-release, uncertain-sample and stalled-download queue classification;
8. hard failed-release limit entering a paused Needs Attention state;
9. Reset & resume clearing the active retry counter;
10. dry-run bad-release cleanup making no destructive state change;
11. Queue Janitor API/UI state removing raw Arr queue payloads;
12. safe ID-based ManualImport dry-run requiring the appropriate Arr IDs;
13. unsafe ManualImport candidates being refused;
14. Arr queue deletion requesting download-client removal plus blocklisting;
15. manual Missing Media preview/search remaining available while its automatic scheduler is disabled; and
16. FastAPI route rendering, v12 health version and valid non-nested forms for the Missing Media and Queue Janitor pages.

## Behaviour validated

### Missing Media Orchestrator

- Radarr, Sonarr and Lidarr missing-media inventory paths are implemented through the Arr APIs.
- Search dispatch is constrained by batch, delay, active-acquisition, daily and attempt limits.
- Batch size is constrained to 1–5.
- Arr queue and Zurg working/mounted state suppress duplicate searches.
- NeutArr coexistence is enabled by default, so NeutArr owns automatic missing-search cadence unless the setting is explicitly disabled.
- Manual search remains available while coexistence is enabled.
- Items no longer reported missing are moved to `resolved` and their active retry counter is cleared while audit events remain.

### Auto Import + Queue Janitor

- Radarr, Sonarr and Lidarr queues are classified conservatively.
- Invalid season/episode/album mapping is checked before generic ID/manual-import wording and is never force-imported.
- Safe ID/grab-history Manual Import requires readable candidate data and the necessary Arr IDs.
- Uncertain sample/media warnings are probed with `ffprobe` when a readable path is available, including a read-only Zurg correlation fallback.
- Confirmed bad releases can be removed from the client, blocklisted and followed by a controlled Arr search.
- Failed-release count defaults to 3 before automatic recovery stops in Needs Attention.
- Stalled/no-progress items defer to NeutArr/Swaparr by default.
- Both recovery engines are disabled by default and dry-run is enabled by default.

## Container-build limitation

Docker is not installed in the packaging environment, so a full `docker build`/live-container smoke test could not be executed here. The Dockerfile, Compose/Portainer YAML, Python application, templates and test suite were validated structurally and at application level. The supplied `scripts/build-local-image.sh` performs the final image build on the Debian/Portainer host.
