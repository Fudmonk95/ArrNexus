# ArrNexus v13.0.0 validation

Validation performed against the complete v13 release tree before packaging.

## Automated source/runtime checks

- Python `compileall` passed for the complete application tree.
- `app.main` imports successfully and reports exactly `13.0.0`.
- All Jinja templates compile successfully, including the new Magic Intake UI.
- `docker-compose.yml` and `portainer-stack.yml` are included with the v13 image tag.
- The main Zurg bind remains read-only.
- A dedicated writable `/zurg_magic` bind is provided only for Zurg `__magic__` moves.
- Standalone service defaults point to `192.168.137.10`.
- Package safety keeps the persistent database outside the release tree.

## Automated test suite

Run with:

```bash
PYTHONPATH=. python -m unittest -v tests.test_v13
```

**19 tests passed.**

Coverage includes:

1. v12 recovery/orchestration SQLite schema remains compatible;
2. Sonarr season grouping for multiple missing episodes;
3. expired Missing Media cooldown eligibility;
4. NeutArr coexistence deferring automatic dispatch;
5. invalid episode mapping taking priority over unsafe manual import;
6. ID/grab-history Manual Import classification;
7. failed-release, uncertain-sample and stalled-download classification;
8. hard retry-limit pause behaviour;
9. Reset & Resume recovery counter behaviour;
10. Queue Janitor dry-run safety;
11. Queue Janitor API state strips raw Arr payloads;
12. safe ID-based manual import dry-run;
13. unsafe manual import rejection;
14. queue removal requests client removal plus blocklisting;
15. manual Missing Media actions remain available while automatic scheduling is disabled;
16. Magic Intake groups `SxxExx` releases into one TV title;
17. Magic Intake ignores already-organised `movies/`, `tv/` and `music/` directories;
18. Magic Intake Force Match persists the selected metadata identity; and
19. FastAPI routes render successfully and `/api/health` reports `13.0.0`.

## Magic Intake behaviour validated

- Top-level release-name normalisation removes common quality/source suffixes.
- Episode markers are preserved while grouping related releases under one title.
- Already-organised Magic Intake directories are excluded from discovery.
- Force Match persists title, year, external ID, artwork and 100% confidence.
- The normal Zurg root is not used for writes by Magic Intake.
- Magic Intake write operations are constrained to the configured dedicated magic root.
- Destination paths are restricted to the configured movie/TV/music destination map.
- Movie groups with multiple candidate releases require explicit release selection.
- Arr items are resolved/added with automatic searching disabled before the virtual move.
- The import path attempts explicit Arr ManualImport first, then a targeted Arr rescan/refresh.
- Completion requires Arr confirmation; otherwise the group is retained as `partial`.
- Ignore changes only ArrNexus state and does not delete Real-Debrid content.

## Container-build limitation

Docker is not installed in the packaging environment, so a live `docker build` and Zurg-FUSE move test cannot be executed here. The Debian host performs the final image build with `scripts/pull-build-v13.sh` after the GitHub tag is published.
