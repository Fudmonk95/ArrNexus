# ArrNexus AIO architecture

ArrNexus is moving to a DUMB-style all-in-one runtime: **one Portainer stack and one visible ArrNexus container for the complete media platform**.

The target is deliberately closer to DUMB's user experience than to the current transitional Docker MediaStack: users deploy ArrNexus once, complete a guided wizard, select the projects they want, and ArrNexus installs, configures, supervises and integrates those services internally.

## What Portainer should look like

For the media platform, Portainer should ultimately show:

```text
STACK: arrnexus

CONTAINER:
  arrnexus
```

Portainer itself remains separate as an emergency/recovery plane.

The selected media services no longer need their own host-Docker container rows. They become supervised child services of the ArrNexus AIO container. The MediaStack page remains the service-level UI and continues to expose health, logs, start/stop/restart, updates, configuration, resource telemetry and recovery actions.

## First-run experience

A clean install should require only the ArrNexus Portainer stack. The flow is:

1. Start the `arrnexus` container.
2. Open the ArrNexus web UI.
3. Create the first administrator account.
4. ArrNexus launches the MediaStack setup wizard automatically.
5. Select a deployment mode:
   - **Fresh install** — choose projects and let ArrNexus install/configure them.
   - **Adopt existing server** — detect existing configs/data and bring them under the AIO runtime without starting again.
6. Choose services. Dependencies are selected automatically.
7. Configure storage/provider/media-server choices, PUID/PGID, timezone, ports and hardware acceleration.
8. Review the plan.
9. ArrNexus installs or adopts each service with live progress and health verification.
10. ArrNexus discovers/generates service credentials and wires supported integrations into ArrNexus tools.

After setup, Missing Media, Live Requests, Magic Intake, Jellyfin refresh, Prowlarr/Arr links and other internal tools should use the managed service registry automatically. A user should not need to copy an API key from a service that ArrNexus itself installed.

## Project catalogue

The AIO catalogue is intentionally broader than the current server. It includes the current stack plus the complete project family requested for the DUMB-style experience: debrid/storage tools, the Servarr family, request systems, Jellyfin/Plex/Emby, databases, dashboards, reverse-proxy/access tooling and workflow utilities.

The canonical catalogue lives in `app/aio_catalog.py`. Entries are separated into:

- `ready` — current-server services that can be adopted first.
- `target` — required AIO catalogue entries whose installer recipe must be validated before the wizard can actually deploy them.
- `retired` — compatibility/import-only projects that should not be offered as a normal fresh-install recommendation.

This distinction prevents a checkbox from pretending an untested installer exists.

## Persistent layout

The AIO container must be disposable. Persistent state belongs on bind-mounted host storage.

Target layout:

```text
/data
├── arrnexus/
│   ├── router.db
│   ├── settings/
│   └── backups/
├── services/
│   ├── zurg/
│   ├── sonarr/
│   ├── radarr/
│   ├── lidarr/
│   ├── prowlarr/
│   ├── jellyfin/
│   └── ...
├── postgres/
├── logs/
├── state/
└── installers/
```

The existing server does **not** need to be wiped. During adoption, ArrNexus should first reference the existing service paths. Once a service is proven inside AIO, its data can optionally be consolidated into the managed root later.

## Service supervision

The final AIO runtime replaces the transitional Docker Stack Agent for normal service lifecycle operations.

ArrNexus needs an internal supervisor with:

- one process definition per selected service
- dependency ordering
- PUID/PGID execution
- health probes
- automatic restart policy
- graceful stop timeout
- per-service stdout/stderr logs
- install/update jobs
- process and resource metrics
- zombie reaping / init behaviour
- service-specific preflight checks
- persistent desired-state registry

The ArrNexus web process is the control plane; individual media services remain independently startable and restartable even though they share one outer Docker container.

## Configuration model

The MediaStack **Configuration Files** section must represent services, not every XML/JSON/YAML file found below a config directory.

If 16 services are enabled, the normal view should contain **16 service configuration cards**. Each card opens the service's primary config/settings view and may expose additional files inside that modal when useful. This avoids hundreds of metadata/cache/generated-file entries while preserving access to real service configuration.

Secrets remain masked.

## API and integration discovery

Each managed project can declare an integration adapter. Supported adapters should be able to:

- determine the internal service URL/port
- wait for first-run readiness
- discover or create an API credential when the upstream application supports it
- save the credential in ArrNexus's connection store
- test the connection
- configure dependent applications where safe
- expose capabilities to ArrNexus features

Examples:

- Sonarr/Radarr/Lidarr/Prowlarr -> ArrNexus Missing Media and orchestration.
- Prowlarr -> register selected Arr applications.
- Seerr -> register Sonarr/Radarr.
- Jellyfin -> library refresh and dashboard/media integrations.
- Zurg -> Magic Intake and filesystem reconciliation.

Credentials must never be written to logs or returned unmasked through MediaStack.

## Current-server migration

The current server should be migrated rather than freshly installed.

Migration rules:

1. Inventory every current service before stopping anything.
2. Preserve the existing configuration/database directories.
3. Build and validate the AIO image/runtime separately.
4. Import the current service definitions and connection settings into ArrNexus.
5. Move low-risk services first.
6. Verify each internal AIO service before removing its old Docker container.
7. Move Sonarr/Radarr/Prowlarr/Lidarr next.
8. Move Jellyfin and Zurg late because they have GPU/FUSE/media-path implications.
9. Keep Portainer available throughout for recovery.
10. Delete obsolete Docker containers/stacks only after their AIO replacements have been verified.
11. Once all selected services are internal, remove the transitional Stack Agent and its Docker socket access.

A full stop/delete/redeploy is possible later, but is **not required** to preserve the current configuration. Existing persistent data is the source of truth.

## Resource management

The existing Docker HostConfig CPU/RAM limiter is transitional because child services will no longer each have their own outer Docker container.

The AIO supervisor will keep the same user-facing resource controls, but the backend must become process/cgroup-aware. Resource controls must fail safely when the host/container runtime does not delegate the required cgroup controllers; the UI must never claim a hard limit was applied when it was only advisory.

## Transitional state

The existing `mediastack` Docker-agent implementation remains useful during migration. Its service catalogue, health, updates, logs and setup logic can be reused while the internal AIO supervisor is built.

Production should not be switched to the AIO runtime until the adoption plan can:

- reproduce the current 16 managed services,
- preserve all existing configs/data,
- bring each service healthy,
- re-establish ArrNexus integrations,
- and provide a tested rollback path.
