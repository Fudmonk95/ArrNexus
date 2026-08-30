# ArrNexus v6.1.0 Validation Report

Validation date: 2026-08-26

## Result

**PASS**

Primary validator output:

```text
PASS: v6.1 UX shell, fast navigation/cache, acquisition fallback, verified connectors, Unified Logs diagnostics and v6 regressions
```

## v6.1-specific checks

- Python application compilation passes.
- All Jinja templates compile in the real ArrNexus template environment.
- `node --check app/static/app.js` passes.
- The authenticated UI renders the isolated `nx-*` navigation shell.
- The new sidebar no longer depends on the legacy `.sidebar nav` responsive grid rules that caused navigation items to interleave/wrap.
- Soft navigation, page cache and hover prefetch are present and use same-origin HTML only.
- The responsive Connections/Ecosystem grid is capped at 3 / 2 / 1 columns according to viewport size.
- DUMB/Arr `/proc` instance discovery has a short TTL cache.
- Radarr/Sonarr/Lidarr/Prowlarr use a persistent HTTP keep-alive client and short read cache.
- Dashboard fan-out calls run concurrently.
- Connections status/roots/tags queries run concurrently.
- Discover Seerr + local library shelves run concurrently; specialist Arr shelves are queried concurrently.
- Download Queue fan-out runs concurrently.

## Retained v6 regression checks

- Acquisition strategies and Usenet/Debrid fallback planning.
- Exactly one selected release is grabbed by the acquisition planner.
- Wrong InfiniDysk SAB key is rejected; correct key succeeds.
- Wrong Decypharr Bearer token is rejected; correct token succeeds.
- Unified Logs page and VFS/seek error diagnostics.
- Discover rendering.
- Music provider isolation regression tests.
- TV pack/full-series parsing and pack-aware policy checks.
- Quality Lab and Self-Healing page smoke tests.
- Fresh application startup and authenticated page smoke tests.

## Container limitation

The package validator does not run a Docker daemon. The application itself is tested through FastAPI/TestClient and Python/JavaScript/template validation; the target Debian/Portainer host performs the final Docker build.
