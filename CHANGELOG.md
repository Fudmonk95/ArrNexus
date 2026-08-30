# Changelog

## 6.0.0

### Acquisition Intelligence

- Added explicit Discover acquisition strategies:
  - Automatic — compare Usenet and torrent/debrid candidates and grab one best release.
  - Debrid first → Usenet fallback.
  - Usenet first → Debrid fallback.
  - Debrid only.
  - Usenet only.
  - Fastest — favour verified Real-Debrid cache hits.
  - Best quality / score.
- Discover now adds/monitors a title first, then runs an ArrNexus acquisition job against the target Arr's interactive releases.
- Added protocol-aware release selection while preserving Arr/Prowlarr indexer/tag visibility.
- Added Real-Debrid instant-availability enrichment when an info hash is available.
- Added a configurable final native Arr-search fallback if ArrNexus cannot select an acceptable interactive release.
- Acquisition grabs exactly one release; comparing both sources never intentionally starts duplicate downloads.
- Scraping has been reframed as **Acquisition** and now reports planning/search/grab/fallback progress and selected protocol/indexer.

### Verified Ecosystem Connections

- Replaced generic "URL returned HTTP 200" connector tests with service-specific verification.
- InfiniDysk now verifies both reachability and its SAB API key using an authenticated SAB queue call.
- Decypharr now verifies `/version` separately from a Bearer-token-protected `/api/torrents` request.
- AltMount now supports username/password authentication and validates the resulting JWT-backed session against its management API.
- Connector cards separately report reachability, authentication, API functionality, version and latency.
- Random/incorrect InfiniDysk or Decypharr credentials now fail verification instead of being reported as connected.
- Added service-specific credential labels rather than treating every ecosystem project as a generic API-key service.

### Unified Logs & Diagnostics

- Rebuilt the Logs page into an interactive log console.
- Added ArrNexus, DUMB and InfiniDysk source views.
- Added DUMB log ingestion through DUMB's `/logs` API with process selection and polling.
- Added InfiniDysk warning/error ingestion through its documented SAB `warnings` operation.
- Added level/source/process/text filtering and clickable log rows.
- Added first-pass diagnostics for common media-stack failures including:
  - VFS/cache `404 Not Found` errors.
  - virtual-stream seek failures.
  - missing/corrupt Usenet articles.
  - Arr "not enough free space" warnings.
  - authentication failures.
- Known log patterns now expand into a plain-language explanation and suggested next actions.
- External log polling can be toggled live from the browser.

### Decypharr Control Surface

- Added a native Decypharr page using its authenticated REST API.
- Displays Decypharr version, managed torrent count, connected Arr count, repair state and broken health entries when available.
- Reuses the same verified connector credential path as the Ecosystem page.

### Interface Redesign

- Rebuilt the global navigation around an InfiniDysk-inspired grouped sidebar while keeping ArrNexus's own branding and visual identity.
- New navigation groups: Overview, Acquisition, Library & Automation, System and Settings.
- Added compact top-bar shortcuts for Problems, Logs and Settings.
- Added new responsive/mobile rules for the v6 navigation and log console.
- Existing user-selectable themes remain supported.

### Settings / Operations

- Added **Settings → Acquisition** for global strategy, Real-Debrid cache preference, candidate limit and native Arr fallback.
- Existing Portainer-first, UI-managed configuration remains; no `.env` is required for normal deployment.
- Application version updated to 6.0.0 and service User-Agent strings updated to ArrNexus/6.0 where applicable.

### Retained from v5 and earlier

- Ecosystem connector SDK, InfiniDysk operations, Quality Lab and Self-Healing.
- Full-series/season/episode TV pack planning and Sonarr coverage.
- DMM Inbox routing/imports, specialist Arr support, safe symlinks and import jobs.
- Discover shelves, Music Hub, Real-Debrid library, Problem Centre, profiles/themes, notifications, diagnostics, backups, PWA and read-only library browser.

## 5.0.0

- Added ecosystem connector platform and JSON connector SDK.
- Added native InfiniDysk operations.
- Added Quality Lab.
- Added Self-Healing / optional non-destructive AutoPilot.
- Added DUMB topology awareness.
- Application version updated to 5.0.0.
