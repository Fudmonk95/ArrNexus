# Changelog

## 5.0.0

### Added
- Ecosystem connector platform with built-in definitions for InfiniDysk, DUMB, Decypharr, AltMount, Profilarr, NeutArr, Cleanuparr, Maintainerr, Bazarr, Streamystats, Zilean, Riven and Pulsarr.
- JSON-only community connector SDK stored in `/data/connectors`.
- Ecosystem topology page showing DUMB namespace state and discovered Arr instances.
- Native InfiniDysk page using `/healthz`, SAB-compatible queue/history/control and Prometheus metrics.
- Quality Lab with release-name parsing, policy scoring explanations and Prowlarr comparison.
- Self-Healing scanner for missing media, Radarr cutoff-unmet upgrades and queue warnings.
- Optional bounded Self-Healing AutoPilot scheduler; disabled by default and non-destructive.
- Direct links between Settings, Ecosystem, Quality Lab, Self-Healing and InfiniDysk.

### Changed
- Application version is now 5.0.0.
- Music User-Agent updated to ArrNexus/5.0.
- Dashboard quick actions include the v5 control-plane pages.

### Safety
- Companion projects remain external integrations; ArrNexus does not copy their implementations.
- AutoPilot never deletes library media, Real-Debrid torrents or download-client jobs.
- Community connectors remain data-only and cannot execute code.

## 4.0.0
- Discover regression fix and isolated shelf providers.
- Music source isolation and Deezer provider.
- Smart TV pack modes, season coverage and cache-aware acquisition.
- Problem Centre.
