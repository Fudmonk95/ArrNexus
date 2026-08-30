# ArrNexus v6.0 Validation Report

Validation date: 2026-08-26

## Result

**PASS**

The v6 source tree passed compilation, template, JavaScript, startup, HTTP smoke, acquisition-policy, connector-authentication and regression tests before packaging.

Primary validator output:

```text
PASS: v6 core, acquisition fallback, verified connectors, Unified Logs diagnostics, v5 regressions and clean startup
```

## Checks performed

### Static / application integrity

- Python `compileall` over the application, validator and migration helper.
- Python AST/import coverage through the project validator.
- Jinja templates compiled through the real ArrNexus template environment.
- JavaScript syntax checked with `node --check app/static/app.js`.
- Existing v4/v5 migration and UI regressions retained in the validator.

### Fresh application startup

The application was started with Uvicorn against a fresh temporary SQLite database.

Observed:

```text
GET /api/health  -> HTTP 200
GET /setup       -> HTTP 200
GET /login       -> HTTP 303 on an unconfigured fresh database (expected redirect to setup)
```

Health payload included `ok: true`.

### v6 Acquisition Intelligence

The validator checks:

- strategy definitions load correctly;
- release candidates are separated by protocol;
- Usenet-first falls back to torrent/debrid when no acceptable Usenet candidate exists;
- the planner selects/grabs exactly **one** release;
- scoring/policy integration remains compatible with pack-aware limits;
- Discover and Settings render the v6 acquisition controls.

### Verified ecosystem credentials

A local mock HTTP service is used to exercise real HTTP authentication paths.

The validator asserts:

- an incorrect Decypharr Bearer token fails verification;
- the correct Decypharr Bearer token succeeds;
- an incorrect InfiniDysk SAB API key fails verification;
- the correct InfiniDysk SAB API key succeeds;
- the Ecosystem verification UI renders reachability/auth/API state separately.

This specifically protects against the v5 bug where random credentials could still appear Connected because only a public/reachable endpoint had been tested.

### Unified Logs

The validator covers:

- v6 Unified Logs page rendering;
- source/origin filtering;
- known-error classification for VFS/cache 404 failures;
- known-error classification for stream seek failures;
- diagnostic/action content attached to recognised failures.

DUMB integration uses its documented log API. InfiniDysk integration uses the documented SAB `warnings` feed; it is intentionally Warning-and-above rather than pretending to expose InfiniDysk's entire native log store.

### Decypharr page

- native Decypharr page renders;
- authenticated connector path is shared with the verification layer;
- version/torrent/repair/Arr summary handling degrades gracefully when optional endpoint data is unavailable.

### Existing regression coverage retained

- Discover previous template/HTTP-500 regression.
- Music Hub provider-source isolation.
- TV pack parsing and full-series/season/episode classification.
- Sonarr coverage visualisation.
- Real-Debrid cache badges.
- JSON-only connector SDK installation/rendering.
- InfiniDysk Prometheus metric filtering.
- Quality Lab parsing/scoring.
- Self-Healing graceful behaviour.
- PWA resources.

### Credential scan

The source/package tree was scanned for the known API keys, application password and session secret previously used during development. Result:

```text
KNOWN_SECRET_HITS=0
```

No known development credentials are embedded in the v6 source package.

## Environment limitation

A Docker CLI/daemon is not available in the build-validation environment, so an actual `docker build` could not be performed here.

The exact FastAPI application was started successfully with Uvicorn. The final Docker/container-level validation must therefore happen on the deployment host with:

```bash
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=150 arrnexus
```

## Deployment acceptance checks

After upgrading, verify these in order:

1. **Ecosystem → InfiniDysk**: an intentionally wrong API key should fail, then the real key should pass.
2. **Ecosystem → Decypharr**: an intentionally wrong token should fail, then the real token should pass.
3. **Discover**: request one test title with `Usenet first` and observe the Acquisition page.
4. Repeat with `Debrid first` or `Automatic` on a different test title.
5. **Unified Logs → DUMB**: choose a DUMB process and confirm log rows populate/poll.
6. **Unified Logs → InfiniDysk**: confirm Warning/Error events render when InfiniDysk has warnings available.
7. Click a known VFS 404/seek warning and confirm the diagnostic drawer expands.
8. **Decypharr**: confirm version/torrent/repair summary loads with the verified token.

## Safety notes

- Acquisition compares providers but intentionally grabs only one selected release.
- `Debrid only` / `Usenet only` remain strict. The optional native Arr fallback is used only by mixed/automatic strategies and hands final release choice back to Arr.
- Self-Healing remains non-destructive.
- Unified Logs do not mutate DUMB or InfiniDysk state.
- Diagnostics and exports continue to redact application secrets.
