# ArrNexus v11.0.0-beta release notes

## Direction

v11 is the Zurg-first rebuild. Zurg is the source-library layer and ArrNexus sits above it as the control plane.

This is also the point where ArrNexus becomes explicitly a personal-server project. Future releases are driven by this server's requirements. The source can still be forked and modified by anyone who wants a different direction.

## Removed from the application

- low-level archive extraction and staging
- media splitting and filesystem transformation workflows
- rescue-specific Sonarr/Radarr workflows
- redundant manual acquisition inbox workflows
- legacy storage bridge integrations
- redundant mount-management integrations
- old stream/metadata aggregation pages
- music providers that are not used on this server
- generic media-server compatibility paths

The removal is intentional: those responsibilities either moved upstream to Zurg or are no longer part of this server.

## Kept and rebuilt

- Sonarr, Radarr and Lidarr connections
- native list import into Sonarr/Radarr
- Prowlarr status
- Seerr integration
- Jellyfin integration
- Spotify Music Hub
- Beatport search
- Kometa collection-definition workflow
- local authentication, logs and diagnostics

## New

### Live Requests

A two-second live view correlates:

1. Seerr request state
2. Sonarr/Radarr presence, queue and history
3. Zurg working/library visibility
4. Seerr's final media availability

This produces a simple request → grab → Zurg work → mount/import → finished timeline.

### Zurg dashboard

ArrNexus now checks the source mount, key Zurg folders, endpoint status, library counts, recent activity and the unplayable view.

### Smaller Music Hub

Spotify remains the account/catalogue source. Beatport uses its current web-search flow. Lidarr remains the acquisition destination.

### Jellyfin-first media automation

Collection automation now operates directly against Jellyfin and can import/export supported Kometa YAML collection IDs.
