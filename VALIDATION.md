# ArrNexus v8.0.0-beta Validation Report

Release target: **ArrNexus 8.0.0-beta**  
Validation date: **2026-08-27**

## Result

**OFFLINE RELEASE VALIDATION: PASS**

The v8 release is built directly from the user's uploaded ArrNexus v7.0.0 source package. The original v7 validator is preserved byte-for-byte as `validate_v7.py`, and `validate.py` executes that entire suite before running v8-specific AIOStreams tests.

The final release ZIP was also extracted into a separate clean directory and validated again from the extracted package before its SHA-256 was recorded.

## Preserved v7 regression suite

```text
PASS: ArrNexus v7.0 Spotify personal library, native InfiniDysk telemetry,
English Language Guard, Prowlarr indexer control, Sonarr TV search,
strict connectors and performance caches
```

`validate_v7.py` covers the v7 Spotify personal-library flow using deterministic mocks, English Language Guard, native InfiniDysk Overview parsing, Prowlarr indexer management, corrected Sonarr season search, strict ecosystem connectors, performance caches, Jinja templates, fresh database/setup and core authenticated routes.

The SHA-256 of `validate_v7.py` matches the original v7 `validate.py`, confirming the prior regression suite was preserved unchanged.

## v8 AIOStreams checks

```text
PASS: ArrNexus v8.0.0-beta retains all v7 regressions and safely adds
AIOStreams full-config preview/apply/backup/rollback/search integration
```

The v8 validator runs a deterministic local HTTP implementation of the AIOStreams API and verifies:

- public instance status and authenticated User API access are treated separately;
- full `GET /api/v1/user` configuration retrieval;
- returned `encryptedPassword` reuse without rendering it;
- full-replacement `PUT /api/v1/user` behavior rather than a partial-update assumption;
- Auto-Wire does not mutate the source object and preserves unrelated AIOStreams configuration;
- Prowlarr URL and API key reuse;
- new Prowlarr presets leave service selection automatic and use an empty `sources` selection so both torrent and Usenet remain eligible;
- existing explicit Prowlarr service/source restrictions are preserved and only safely extended where required;
- Real-Debrid credentials are masked from preview/display output;
- existing NzbDAV credentials win and only clearly identified missing fields are filled;
- stale previews are rejected before backup or PUT;
- successful Apply creates a private pre-write backup before the remote PUT;
- rollback first creates a safety backup of the current remote configuration;
- search diagnostics redact playback URLs, request/proxy headers, Authorization, Cookie and other secret-bearing fields;
- AIOStreams UI/API routes require an administrator account;
- all Jinja templates compile with the actual ArrNexus template environment and filters.

## Package/release checks completed

- Python compilation: **PASS**.
- `app/static/app.js` Node.js syntax check: **PASS**.
- Fresh SQLite initialization and first-run administrator setup: **PASS** through the validators.
- Authenticated core/AIOStreams page smoke tests using deterministic/mock services: **PASS**.
- Real Uvicorn clean-copy smoke:
  - `GET /api/health` → **HTTP 200**.
  - `GET /setup` → **HTTP 200**.
- Release-tree secret scan: **PASS**.
- No live `.env`, SQLite database, session secret, OAuth token store, AIOStreams backup JSON or persistent user data is included.
- `__pycache__`, `.pyc` and transient runtime/cache files are excluded from the release ZIP.
- Exact finished ZIP clean-re-extraction `validate.py`: **PASS**.
- Exact finished ZIP clean-re-extraction JavaScript syntax check: **PASS**.

## Docker validation

Docker is **not installed in the packaging environment used for this release**, so `docker compose config` and `docker compose build` were **not run here**. They are required live-deployment checks on the Debian server and must not be treated as already passed.

The provided `docker-compose.yml` remains the v7 Portainer-first layout with `pid: host`, `SYS_PTRACE`, `host.docker.internal`, port `8484:8000`, and `./data:/data`. The Dockerfile retains ffmpeg/ffprobe for Language Guard.

## Live integration still required

Offline validation deliberately does **not** use the deployer's real AIOStreams credential, Real-Debrid token, NzbDAV credential, Prowlarr API key or Spotify OAuth token.

After deployment with a **copy** of v7 persistent data, verify in this order:

1. Existing v7 pages and routing still operate normally.
2. AIOStreams public status and authenticated User API both verify.
3. GET the current AIOStreams configuration and inspect the masked Auto-Wire preview.
4. Confirm unrelated AIOStreams settings survive the proposed merge.
5. Confirm Prowlarr, Real-Debrid and NzbDAV changes are conservative and expected.
6. Apply only after confirming the preview and verify that a private pre-write backup exists.
7. Confirm AIOStreams remains functional after the full-config PUT.
8. Test rollback and confirm it first creates a safety backup.
9. Run ID search diagnostics and confirm playback URLs/headers/secrets are not rendered.
10. Complete a live Spotify OAuth callback test with the real Spotify application before considering that integration fully proven.

Do not promote `8.0.0-beta` to stable until these live checks pass.
