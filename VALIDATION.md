# ArrNexus v5.0 Validation Report

Validation date: 2026-08-26

## Result

**PASS** for the application-level and package-level checks available in the build environment.

## Automated validator

Command:

```bash
python validate.py
```

Result:

```text
PASS: v5 Python/templates/startup, Discover/music regressions, TV packs, connector SDK, InfiniDysk metrics, Quality Lab and Self-Healing
```

The validator covers:

- Python compilation for the `app` package.
- Jinja compilation using ArrNexus's real template environment and custom filters.
- Fresh database creation and first-run administrator setup.
- Authenticated smoke tests for Dashboard, Settings, Profile, Logs, Jobs, Rules, Libraries, Connections, Queue, Scraping, Maintenance, Problem Centre, Timeline, Discover, Music Hub, Debrid/DMM, Ecosystem, InfiniDysk, Quality Lab and Self-Healing.
- Discover shelf/search regression coverage for the previous HTTP 500 failure.
- Music-provider isolation so selected provider tabs do not silently reuse another provider's data.
- TV full-series/season/episode parsing and pack filters.
- Sonarr season-coverage rendering and smart full-series controls.
- Real-Debrid cache badge rendering.
- Pack-aware size/scoring rules.
- v5 JSON-only ecosystem connector installation and rendering.
- InfiniDysk Prometheus metric filtering.
- Quality Lab release parsing and score explanations.
- Self-Healing no-Arr graceful behaviour.
- PWA manifest/service-worker routes.

## Additional checks

### Python AST / compile

```text
PASS
```

Every application Python file parses successfully.

### JavaScript syntax

`node --check app/static/app.js`:

```text
PASS
```

### Real Uvicorn boot

The application was started with Uvicorn against a fresh temporary database.

Checked over HTTP:

```text
GET /api/health  -> 200
GET /setup       -> 200
```

Health response during the isolated validation host correctly reported that no DUMB namespace was available.

### Credential scan

The package was scanned for the API keys, password and session secret that had appeared during development/testing discussions.

```text
PASS — none of those known credentials are present in the package
```

### Clean-package revalidation

The final ZIP is extracted into a separate clean directory and `python validate.py` is executed against the extracted copy before release. This catches missing-file/packaging errors that source-tree validation alone would miss.

## Safety validation notes

- The Self-Healing scheduler is disabled by default.
- Built-in Self-Healing only triggers bounded Arr search commands; it does not delete library files, symlinks, Real-Debrid torrents or download-client jobs.
- Community ecosystem connectors are JSON-only and cannot execute Python code.
- InfiniDysk queue controls exposed in v5 are limited to safe global pause/resume operations.
- External projects are integrated through APIs/health endpoints rather than bundled source code.

## Environment limitation

The build environment does not provide a Docker daemon/CLI, so an actual Docker image build could not be performed here. The included Compose file is unchanged in its core runtime requirements from v4 and the application itself was booted successfully with Uvicorn.

The deployment host should still run:

```bash
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=150 arrnexus
```

before considering the container-level deployment validated.
