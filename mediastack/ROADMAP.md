# ArrNexus MediaStack roadmap

ArrNexus MediaStack is a service-per-container control plane for this server. It intentionally does **not** copy DUMB's single-container runtime. The useful ideas are the guided setup, declarative service configuration, service pages, safe updates, recovery, metrics and operator tooling; the implementation stays native to ArrNexus and Docker.

No DUMB source code is copied here. This roadmap is an independent implementation based on documented behaviours and the needs of this server.

## Architecture rules

- ArrNexus remains the UI/orchestrator.
- `arrnexus-stack-agent` is the only component allowed to talk to the Docker socket.
- Every media application stays in its own container.
- Existing persistent config/data paths can be adopted in-place before any optional consolidation.
- `/zurg_mnt` remains the canonical mount namespace.
- Zurg owns `/zurg_mnt` with `rshared`; consumers receive it with `rslave`.
- Portainer remains outside MediaStack as the emergency/recovery control plane.
- No automatic destructive action without a preview, persisted state and rollback path.

## Phase 1 — Observe and adopt

- [x] Docker host/container inventory
- [x] CPU, RAM, network, mounts and restart counters
- [x] Image digest/update detection
- [x] Read-only logs
- [x] Read-only, redacted config browser
- [x] Resource-pressure warnings
- [x] Existing-container watch/adoption mode
- [ ] Persist selected/adopted services in ArrNexus
- [ ] Discovery page showing current Compose projects/stacks
- [ ] Export a recovery manifest/Compose skeleton

## Phase 2 — Guided setup

First-run/re-runnable onboarding inspired by the useful parts of mature stack managers:

1. Choose **Adopt existing server** or **Fresh install**.
2. Select services with dependency-aware recommendations.
3. Run preflight checks for Docker, FUSE, mount propagation, ports, paths and optional GPU access.
4. Choose persistent config paths and `/zurg_mnt` behaviour.
5. Configure service-specific options.
6. Choose update policy per service.
7. Review the exact plan before applying anything.
8. Apply sequentially with live progress and rollback information.

The initial ArrNexus catalogue is intentionally limited to services used on this server:

- Zurg
- Sonarr
- Radarr
- Lidarr + its PostgreSQL dependency
- Prowlarr
- SeerrNG
- Jellyfin
- Bazarr
- Whisparr
- NeutArr
- Maintainerr
- Profilarr + parser
- Homarr (optional; much of its operational role may become unnecessary)

ArrNexus and the Stack Agent are always present. Removed historical workflows such as InfiniDysk, Decypharr, NZB-DAV, Plex, Emby and AIOStreams are not reintroduced.

## Phase 3 — Service lifecycle pages

Each managed service gets a service page with:

- state, health and restart history
- image/tag/digest and update status
- start / stop / restart controls
- dependency graph
- ports, networks and mounts
- CPU/RAM/network charts
- logs
- redacted configuration view
- update policy/channel
- backup/rollback history
- links to the native service UI

Lifecycle controls are capability-gated and require the authenticated Stack Agent write channel.

## Phase 4 — Safe updates

For container images the guarded update lifecycle is:

1. Resolve the requested tag/digest.
2. Pull the candidate **before** stopping the current container.
3. Snapshot Docker inspect/config metadata and record the previous image ID.
4. Stop the current container but retain it as the rollback candidate.
5. Recreate with the same mounts, ports, limits, devices, groups, networks and environment.
6. Require the replacement to become running/healthy and remain stable.
7. Remove the rollback container only after success.
8. On failure, remove the candidate and restart the previous container/image automatically.

Policies:

- `manual` — never changed by ArrNexus automatically.
- `review` — check automatically and show an Update button.
- `automatic` — update only during the configured maintenance window.

Safe defaults for this server:

- Zurg: manual
- Jellyfin: manual/pinned (do not jump to Jellyfin 12 until plugins are approved)
- ArrNexus: manual self-update path
- PostgreSQL: manual
- normal Arr services: review initially, then opt-in automatic after update rollback is proven

## Phase 5 — Adoption instead of a big-bang migration

Running containers cannot literally be moved into a different Compose project without recreation. ArrNexus therefore adopts them progressively:

- discover the existing container and persistent paths
- save it as managed desired state
- keep its existing config path by default
- on the first managed recreate/update, add ArrNexus management labels
- verify health before considering it adopted
- optionally move config into `/opt/arrnexus-mediastack/config/<service>` later as a separate migration

This avoids a single high-risk cutover of the entire server.

## Phase 6 — Recovery, protection and automation

- automatic restart policy with rate limits/backoff
- update/restart event history
- per-service backup policy
- dependency-aware start/stop ordering
- Zurg/Jellyfin media-library protection during mount outages or Zurg updates
- maintenance-mode banner
- notifications/webhooks for persistent failures
- persistent metrics history
- database health checks
- export/import of ArrNexus managed-service state

## Phase 7 — Operator quality-of-life

- embedded/native service UI links and optional same-origin proxying
- searchable service sidebar
- saved service views
- configuration validation/editor for safe fields
- update centre with pending/current/failed/rolled-back states
- live job progress
- multi-instance support where it is genuinely useful
- optional Cloudflare/Traefik helpers without making either mandatory
- redacted diagnostic bundle and optional AI-assisted diagnostics

## Non-goals

- Recreating DUMB as another monolithic application container.
- Blind Watchtower-style updates.
- Deleting media/mount roots as part of a service reset.
- Giving the ArrNexus web process unrestricted Docker-socket access.
- Reintroducing old ArrNexus workflows that were deliberately removed.
