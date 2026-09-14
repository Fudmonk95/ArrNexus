# ArrNexus AIO architecture

ArrNexus is moving to a DUMB-style all-in-one runtime: one Portainer stack and one ArrNexus container for the complete media platform.

## Target runtime

Portainer should ultimately show one media stack (`arrnexus`) and one media container (`arrnexus`). Portainer itself remains separate as an emergency/recovery plane.

Inside the ArrNexus container a process supervisor will manage ArrNexus plus optional internal services such as Zurg/rclone, Jellyfin, Sonarr, Radarr, Lidarr, PostgreSQL, Prowlarr, SeerrNG, Bazarr, Whisparr, NeutArr, Maintainerr, Profilarr/Parser and ErsatzTV.

ArrNexus will provide install/adopt/configure/start/stop/update/repair controls for those internal services. Persistent application data remains bind-mounted on the host so adopting an existing service does not require rebuilding its library or configuration.

## Migration rules

1. Production stays on port 8484.
2. The 8585 test instance is only removed after its dashboard and Spotify settings are merged into production.
3. Existing service stacks are removed one at a time only after the matching internal ArrNexus service has been verified.
4. Existing config/database directories are reused rather than re-created.
5. Zurg and Jellyfin are high-risk migrations and are moved late in the process.
6. Zurg remains unrestricted during migration; final resource limits will be based on observed real usage.
7. Zurg's FUSE paths must be checked directly; Docker health alone is not sufficient.
8. ArrNexus uses an init/process supervisor so child processes are reaped and service failures remain isolated.

## Transitional state

The existing `mediastack` Docker-agent implementation is transitional. Its useful service catalogue, health, update and setup logic can be reused, but final service lifecycle operations must target internally supervised processes instead of sibling Docker containers.
