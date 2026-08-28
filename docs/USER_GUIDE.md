# ArrNexus User Guide

This guide is generated from the same documentation catalogue used by the in-app `/help` Help Centre.

Every guide is structured around prerequisites, setup, normal use, success criteria, troubleshooting and safety/privacy. When third-party provider rules can change, verify the provider's current official documentation as well.

# Start here

## Getting started & first deployment

Deploy ArrNexus, create the first administrator, connect only the services you actually use, map any virtual-media paths and finish with Stack Readiness.

### Before you start

- Docker on a Linux host is the recommended runtime.
- For host-side release validation on Debian/Ubuntu install unzip and python3-venv.
- The ArrNexus container must be able to reach the APIs you intend to connect.

### Setup

1. Download a release ZIP or clone the Git repository.
2. For a ZIP release, create a local .venv, install requirements.txt and run python validate.py before building Docker.
3. Run docker compose config, then docker compose up -d --build.
4. Open http://<ARRNEXUS_HOST>:8484. A fresh install redirects Dashboard to first-run setup.
5. Create the administrator account, then follow Setup Guide to connect applications, providers, mounts and readiness checks.

### How to use it

- Use the public home page before sign-in for architecture, installation and download guidance.
- Use Dashboard for the quick operational snapshot and Stack Readiness when you deliberately want live checks.

### What working looks like

- /api/health returns HTTP 200.
- The container is healthy and the first administrator can sign in.
- Configured integrations report healthy from inside ArrNexus, not merely from your desktop browser.

### If it does not work

- If python validate.py reports ModuleNotFoundError, activate a .venv and install requirements.txt; do not pollute Debian system Python.
- If another container is configured as localhost, replace localhost with a Docker DNS name, routable host name or reverse-proxy address.
- Check docker compose logs --tail=300 arrnexus when the application itself fails to start.

### Safety / privacy

- Keep ./data private and back it up before major upgrades.
- Do not publish .env, databases, API keys, Debrid tokens or raw diagnostics.

### Related guides

`onboarding`, `connections`, `readiness`, `release-management`

## Setup Guide / onboarding

The guided setup checks the environment, connected applications, provider registry and library/mount assumptions before marking the installation complete.

### Before you start

- An administrator account must exist.

### Setup

1. Open Setup Guide from the sidebar or /onboarding.
2. Work through environment, applications, providers, mounts and readiness in order.
3. Use Connections and Providers for any missing details; return to Setup Guide afterwards.

### How to use it

- The wizard is safe to revisit after upgrades; it is not only for first boot.
- Treat warnings as configuration work to review, not permission for ArrNexus to rewrite third-party services automatically.

### What working looks like

- Required checks for the services you actually use are green and setup.complete is recorded.

### If it does not work

- A missing DUMB mount is often a namespace visibility issue; check DUMB namespace help before changing host paths.
- Optional integrations can remain unconfigured without blocking the application.

### Safety / privacy

- Detection is advisory. Confirm paths and destinations before changing routing or library mappings.

# Accounts & security

## Sign-in, profiles & access

ArrNexus has its own local users. Authentication is independent of Radarr, Sonarr, DUMB, Spotify and other connected systems.

### Before you start

- The first administrator is created during first-run setup.

### Setup

1. Administrators can add users under Settings → User accounts & request limits.
2. Assign admin/user role, request permission and optional daily request limit.
3. Each user can change display name/email/password from Profile.

### How to use it

- Sign in with username or saved email address.
- Administrator-only pages include Providers, AIOStreams, Stack Readiness configuration and Music API Settings.

### What working looks like

- User can authenticate and sees only actions allowed by their role.

### If it does not work

- If a normal user cannot request media, check can_request and daily limit in Settings.
- For lost passwords use the password-recovery guide; SMTP must be configured for emailed reset links.

### Safety / privacy

- Do not reuse service API keys as ArrNexus passwords.
- Keep administrator accounts limited to people who need configuration access.

### Related guides

`password-recovery`, `settings`

# Operations

## Dashboard / Control Centre

The Dashboard is a cached operational summary of library counts, DMM backlog, downloads, recent activity, service state and Stack Readiness.

### Before you start

- Sign in to ArrNexus.

### Setup

1. No separate setup is required; cards populate from whichever integrations are configured.

### How to use it

- Use Dashboard for a fast snapshot rather than forcing a live scan on every visit.
- Use the refresh control when you explicitly need a new snapshot.
- Open Stack Readiness for bounded live service verification.

### What working looks like

- Dashboard renders quickly and shows a snapshot age rather than blocking on every external API.

### If it does not work

- If a dependency fails, Dashboard should remain available in degraded mode and point you to diagnostics.
- Search Unified Logs for source=performance or slow_request if navigation remains slow.

### Safety / privacy

- A cached value may be a few seconds old; use live readiness before making a critical operational decision.

# Discovery & acquisition

## Discover & request media

Discover combines metadata search with your Arr/media-server context and can hand a selected movie, show or music result into the owning specialist application.

### Before you start

- Configure the relevant Radarr/Sonarr/Lidarr connection for the type of media you want to add.

### Setup

1. Open Connections and verify the owning Arr application.
2. Configure acquisition/provider settings if you want ArrNexus to compare more than one route.

### How to use it

- Search by title, inspect existing-library state, choose the intended Arr/library destination and submit the request.
- Use Acquisition/Quality Lab when you need to understand candidate scoring rather than immediately adding a result.

### What working looks like

- The owning Arr accepts the item and ArrNexus records the request/activity trail.

### If it does not work

- If a result adds to the wrong instance, review Routing Rules and destination mappings.
- If search is empty, test the upstream Arr/metadata connections and review Unified Logs.

### Safety / privacy

- ArrNexus should hand ownership to the correct Arr rather than directly replacing its library database.

## Acquisition strategies & scraping

Acquisition compares available Usenet/Debrid candidates and applies the configured strategy while leaving the owning Arr/download client responsible for the final hand-off.

### Before you start

- At least one usable Arr application and one acquisition path/provider for meaningful comparisons.

### Setup

1. Configure strategy under Settings → Acquisition.
2. Verify Prowlarr/indexers and any provider/backend used by that strategy.

### How to use it

- Automatic compares acceptable routes; Debrid-first and Usenet-first add explicit fallback order; focused modes can restrict to one side or prefer cached/quality score.

### What working looks like

- Scraping/Acquisition shows the candidate trail, selected route and final hand-off rather than a silent decision.

### If it does not work

- No candidates usually means the relevant search backend or indexer is unavailable, not that ArrNexus should invent one.
- Use Quality Lab for release-name/scoring explanations.

### Safety / privacy

- Use dry inspection pages before changing acquisition policy for a large live library.

### Related guides

`quality-lab`, `providers`, `indexers`

# Debrid & virtual media

## Debrid / DMM & device linking

The Debrid/DMM area connects ArrNexus to supported Debrid workflows and the DMM-originated source media that originally motivated the project.

### Before you start

- A supported Debrid account or a DMM/Decypharr workflow if you intend to use these features.

### Setup

1. Open Debrid / DMM and choose Connect when device authorization is required.
2. Follow the displayed device-code/provider flow until ArrNexus reports the account connected.
3. Configure Provider Registry separately when you want provider-neutral AIOStreams/acquisition capabilities.

### How to use it

- Use DMM/Debrid source information as input to Inbox review, matching, routing and managed-library linking.

### What working looks like

- ArrNexus reports the Debrid connection and DMM sources can be inspected without deleting or rewriting them.

### If it does not work

- If device authorization expires, restart Connect rather than reusing an old device code.
- If DMM items are missing, verify DUMB/source-root visibility and the namespace guide.

### Safety / privacy

- Disconnecting ArrNexus credentials must not delete source media from the provider.
- Undo is designed around ArrNexus-created links, not source deletion.

### Related guides

`dmm-inbox`, `providers`, `dumb-namespace`

## DMM Inbox & bulk imports

DMM Inbox reviews existing Debrid/DMM content, matches it to the correct movie/show, groups duplicates, evaluates policy/language and creates managed library links through explicit jobs.

### Before you start

- DMM/Debrid source visibility plus the owning Radarr/Sonarr where matching/import is expected.

### Setup

1. Confirm the DUMB/source root and logical library paths under Settings.
2. Verify Radarr/Sonarr and optional media server connections.

### How to use it

- Open an item for detailed identity, route and Language Guard review.
- Select multiple reviewed items for a bulk import job when the destination is clear.

### What working looks like

- Imports create/record the expected managed-library link and activity trail while the source remains intact.

### If it does not work

- If Inbox is slow, inspect performance/slow_request logs and source visibility rather than repeatedly rescanning.
- If an item cannot be matched, use Item Review and the owning Arr search.

### Safety / privacy

- Do not bulk import ambiguous matches. The source and managed-library link are intentionally treated as different objects.

### Related guides

`item-review`, `language-guard`, `jobs`, `libraries`

## DMM Item Review & Language Guard

Item Review is the decision page for one DMM/Debrid source: inferred identity, owning Arr item, destination route, language inspection and import action.

### Before you start

- Open an item from DMM Inbox.

### Setup

1. Configure Language Guard policy under Settings if you want stream-language enforcement.

### How to use it

- Review metadata and destination before importing.
- Run/re-run the language check when stream metadata is not yet known.
- Use replacement search when a source is rejected but the Arr item still needs media.

### What working looks like

- The item shows an explainable state and any import/skip/reject action is recorded.

### If it does not work

- Unknown language metadata can fail closed depending on policy; verify ffprobe can inspect the actual media path.
- A failed match should be corrected before creating a link.

### Safety / privacy

- When rejected-source cleanup is enabled, ArrNexus deletes only an exactly identified Real-Debrid torrent after the import has been blocked. Ambiguous provider matches are preserved.

### Related guides

`language-guard`, `dmm-inbox`

# Library & automation

## Import Jobs & background work

Jobs track multi-step or asynchronous ArrNexus work so long operations do not disappear behind one HTTP request.

### Before you start

- An action that creates an import/background job.

### Setup

1. No special setup is required.

### How to use it

- Open Import Jobs to see queued/running/completed state and open an individual job for per-item progress.

### What working looks like

- Jobs reach a terminal state with clear per-item results.

### If it does not work

- If a job appears stuck, inspect its detail page and Unified Logs before re-running the source action.

### Safety / privacy

- Avoid launching duplicate bulk jobs until you know whether the first one completed.

## Libraries, logical mounts & filesystem browser

ArrNexus distinguishes source content, virtual/cache layers and managed Arr/media-server libraries. Logical mounts describe those relationships without assuming the host filesystem is the same as the DUMB namespace.

### Before you start

- Know the paths visible to the relevant containers or DUMB mount namespace.

### Setup

1. Configure DUMB logical root and library/source mounts in Settings.
2. Use the read-only browser to confirm what ArrNexus can actually see before saving a path.

### How to use it

- Libraries shows configured/inventoried roots.
- Browser is for inspection and downloading a visible file, not arbitrary filesystem mutation.

### What working looks like

- Movie/TV/music/source paths resolve to the intended namespace and correspond to the owning Arr roots.

### If it does not work

- If /mnt/debrid exists inside DUMB but not on the host, read DUMB namespace help; do not replace it with an unrelated host path.
- If a path is invisible, inspect Docker mounts/namespace rather than repeatedly changing ArrNexus mappings.

### Safety / privacy

- Browser access is deliberately bounded to configured logical areas. Do not publish screenshots containing private path/user information.

### Related guides

`dumb-namespace`, `routing`

## DUMB mount namespace & /proc/<PID>/root

In DUMB deployments useful virtual mounts can exist inside another process mount namespace. ArrNexus can view those paths through /proc/<PID>/root while keeping logical paths such as /mnt/debrid in the UI.

### Before you start

- Docker Compose retains pid: host and SYS_PTRACE where this architecture is required.

### Setup

1. Keep the logical DUMB root configured to the path used inside the DUMB/Arr namespace.
2. Use Stack Readiness or Setup Guide to confirm an anchor process/PID and namespace visibility.

### How to use it

- Continue entering logical paths such as /mnt/debrid/... rather than hard-coding /proc/<PID>/root into every configuration field.

### What working looks like

- ArrNexus can inspect source/library paths that are not directly mounted on the Docker host.

### If it does not work

- If namespace discovery breaks after a container restart, refresh readiness/connection state so ArrNexus can find the new PID.
- Do not remove pid: host/SYS_PTRACE just because normal host ls cannot see the mount.

### Safety / privacy

- Namespace access is powerful. Keep ArrNexus private and only enable the capability on a host you control.

## Routing Rules

Routing Rules decide which configured destination/library should own different media instead of forcing every request into one universal root.

### Before you start

- Define the relevant logical library mounts/destinations first.

### Setup

1. Open Routing Rules and add only the criteria/destination mappings you need.
2. Confirm specialist Radarr/Sonarr/Lidarr instances or roots exist before referencing them.

### How to use it

- Review rules when a title is consistently being sent to the wrong library.
- Delete a rule only after checking what falls back to the default route.

### What working looks like

- Item Review/Discover explains the destination that matches the configured rules.

### If it does not work

- Unexpected routing often comes from overlapping rules, stale destination names or a missing default root.

### Safety / privacy

- Routing changes can affect future imports; preview with one known title before bulk work.

## Self-Healing

Self-Healing scans for bounded repair/upgrade opportunities and can trigger searches through the owning Arr application rather than directly replacing library databases.

### Before you start

- Relevant Arr connections and visible libraries.

### Setup

1. Configure Self-Healing settings and enable only the checks you want automated.

### How to use it

- Review detected candidates, trigger a search deliberately, and inspect the resulting Arr activity.

### What working looks like

- A candidate has an explainable reason and the owning Arr receives the requested search.

### If it does not work

- If nothing is found, confirm the library inventory and Arr missing/cutoff state are visible.

### Safety / privacy

- Start with conservative/manual search triggering before enabling broader automation.

## Quality Lab

Quality Lab parses release names and shows how ArrNexus policy scores quality, pack type, cache state and other release characteristics.

### Before you start

- A release name or acquisition context to evaluate.

### Setup

1. Adjust release policy in Settings if the default scoring does not match your priorities.

### How to use it

- Paste/inspect a release to understand the score instead of treating selection as a black box.

### What working looks like

- The page explains the parsed attributes and resulting score/recommendation.

### If it does not work

- Unusual release names may not expose every attribute; compare with the raw name and Prowlarr result.

### Safety / privacy

- Quality Lab is advisory; changing global policy affects future decisions.

# Operations

## Download Queue

Download Queue aggregates supported acquisition/download activity so you can see which backend owns current work and whether it is progressing.

### Before you start

- At least one supported connected backend with queue information.

### Setup

1. Verify the backend connection in Connections/its dedicated integration page.

### How to use it

- Use source filters to distinguish different acquisition systems.

### What working looks like

- Active items show source/status/progress rather than requiring separate tabs for every backend.

### If it does not work

- An empty queue can be correct; verify the source-specific backend before assuming ArrNexus is failing.

## Timeline & activity history

Timeline groups ArrNexus events around a title/source so decisions and actions can be read in order rather than as disconnected log lines.

### Before you start

- Recorded ArrNexus activity for the title/source.

### Setup

1. No extra setup.

### How to use it

- Open Timeline from an item/job/log context when reconstructing what happened.

### What working looks like

- Events show chronological request/import/routing state.

### If it does not work

- If an external service action is missing, also inspect Unified Logs; ArrNexus only records events it can observe.

# Connections & integrations

## Connections: Radarr, Sonarr, Lidarr, Prowlarr, Seerr

Connections stores the service URL and protected API key/token ArrNexus needs to talk to your specialist applications.

### Before you start

- The target service is running and reachable from the ArrNexus container.

### Setup

1. Open Connections.
2. Enter the service URL as seen from the ArrNexus container, not necessarily the URL used in your desktop browser.
3. Copy the target application's API key/token from its own settings and save/test the connection.

### How to use it

- Use service-specific pages only after the connection verifies.
- Multiple Arr instances discovered by the environment remain separate destinations.

### What working looks like

- ArrNexus receives the expected authenticated API response and shows version/name where supported.

### If it does not work

- Do not use localhost for another container.
- If the browser can reach a service but ArrNexus cannot, check Docker network/DNS/routing from the container.
- A reachable HTML login page is not considered a healthy API connection.

### Safety / privacy

- API keys are stored as secrets and should not appear in screenshots or diagnostics.

### Related guides

`media-servers`, `dumb-namespace`

## Jellyfin, Plex, Emby & custom media servers

Jellyfin remains the deepest library integration. Plex and Emby have first-class authenticated connection/health probes, and a custom HTTP connector supports other media servers without executing third-party code.

### Before you start

- A reachable media server and the token/API key required by that server.

### Setup

1. Jellyfin: save the server URL and API key in Connections.
2. Plex: save the Plex URL and X-Plex-Token.
3. Emby: save the Emby URL and API key.
4. Custom server: provide name, base URL, health path and optional bearer/custom-header/query authentication.

### How to use it

- Use Connections/Readiness to verify health. Jellyfin-specific library features only appear where the Jellyfin API is actually implemented.

### What working looks like

- Probe returns a recognized server response/name/version where supported.

### If it does not work

- A reverse-proxy landing page is not the same as a protected API response.
- For Plex/Emby, confirm the token is valid and the configured base URL reaches the actual server API.

### Safety / privacy

- Custom connectors perform bounded HTTP probes only; do not put arbitrary scripts/plugins into ArrNexus.

## Provider Registry

Provider Registry makes ArrNexus provider-neutral: Real-Debrid is one option among Debrid, Usenet and streaming services that can coexist.

### Before you start

- A provider account only for providers you choose to enable.

### Setup

1. Open Providers as an administrator.
2. Enable the provider, fill only its required credential fields and save.
3. Leave optional providers disabled instead of entering placeholder values.

### How to use it

- Provider capabilities feed acquisition/AIOStreams planning. Existing AIOStreams credentials are preserved where the bridge is designed to avoid overwrites.

### What working looks like

- Enabled providers show configured state without rendering raw credentials.

### If it does not work

- If a provider uses username/password, client ID or encoded token rather than an API key, use the fields shown for that provider instead of forcing one credential shape.

### Safety / privacy

- Secrets are masked. Do not paste provider credentials into logs, screenshots or GitHub issues.

### Related guides

`aiostreams`, `acquisition`

## InfiniDysk / NzbDAV telemetry

InfiniDysk gives ArrNexus native Usenet/download telemetry, queue/history and bounded operational actions where supported by the upstream service.

### Before you start

- An InfiniDysk/NzbDAV deployment and the URL/credential expected by its API.

### Setup

1. Configure the InfiniDysk connector in the relevant connection/ecosystem settings.
2. Verify authenticated API behaviour rather than a frontend page.

### How to use it

- Select telemetry window and media filter; health, queue, history and overview data are fetched concurrently and cached briefly.

### What working looks like

- Overview/queue/history render without leaking credentials and the page reports snapshot age.

### If it does not work

- If /api/infinidysk/live is slow, check upstream latency and performance logs.
- If only a web UI loads, confirm the API URL/credential.

### Safety / privacy

- Operational actions are explicit; inspect the target before triggering an upstream action.

## Decypharr

Decypharr is the Debrid/torrent-side integration in the reference DUMB architecture. ArrNexus verifies the protected API and exposes relevant torrents/repairs/Arr context.

### Before you start

- A Decypharr instance reachable from ArrNexus and its token/credential.

### Setup

1. Save the Decypharr URL and protected credential in the supported connector settings.

### How to use it

- Use the dedicated Decypharr page for visibility and bounded repair context.

### What working looks like

- Authenticated API state/version is visible; a login/frontend page alone is not accepted as healthy.

### If it does not work

- 401/403 normally means the Bearer/API token is wrong or missing.
- Network failures require checking container-to-service routing.

### Safety / privacy

- Do not expose Decypharr tokens in diagnostics or screenshots.

## Prowlarr Indexers

Indexer management exposes Prowlarr-backed state and supported controls such as enabled state, priority, RSS, automatic search and interactive search.

### Before you start

- A verified Prowlarr connection.

### Setup

1. Add Prowlarr URL/API key under Connections.

### How to use it

- Review/edit an indexer conservatively. ArrNexus sends the full object when required instead of constructing a destructive partial replacement.
- For Sonarr TV releases ArrNexus uses seriesId + seasonNumber and merges/deduplicates results.

### What working looks like

- Saved changes survive a Prowlarr re-read and interactive results correspond to the intended movie/season.

### If it does not work

- DUMB or another manager may restore routing-sensitive indexer values after you edit them; check external ownership before assuming ArrNexus ignored the change.

### Safety / privacy

- Changing Prowlarr affects multiple Arr applications; record existing values before broad edits.

# Music

## Music Hub

Music Hub combines clearly-labelled discovery sources with Lidarr hand-off and optional per-user Spotify personal data.

### Before you start

- Lidarr for managed music acquisition; optional provider application credentials for richer catalogue sources.

### Setup

1. Configure Music API Settings for Spotify/SoundCloud/Jamendo/Last.fm as needed.
2. Verify Lidarr connection/root for add/search actions.

### How to use it

- Choose source and search type, inspect featured/trend data, open artist detail and hand a selected artist/album to Lidarr.

### What working looks like

- Provider ownership is labelled honestly; optional provider failure does not take down the whole Music page.

### If it does not work

- If a catalogue URL points at example.com/example.invalid, ArrNexus rejects it; configure a real provider instead.
- If artist pages are slow, check performance logs for /music/artist and upstream MusicBrainz/Lidarr latency.

### Safety / privacy

- External music catalogues are discovery sources; acquisition ownership stays with Lidarr/Prowlarr.

### Related guides

`music-api-settings`, `spotify`

## Music API Settings

Administrator page for application-level Spotify, SoundCloud, Jamendo and Last.fm credentials. These are separate from an individual user's Spotify OAuth link.

### Before you start

- Administrator access and developer credentials from the provider you choose to enable.

### Setup

1. Open Music API Settings from the sidebar.
2. Enter only the provider credentials you actually use.
3. For Spotify, register the exact callback shown by ArrNexus in the Spotify Developer Dashboard, then save Client ID/Secret and callback URI.

### How to use it

- After Spotify app credentials are saved, each ArrNexus user links their own account from Music Hub → Spotify → Connect Spotify.

### What working looks like

- The page reports configured state without displaying saved secrets and Music Hub can use the provider.

### If it does not work

- Spotify INVALID_REDIRECT_URI means the URI registered with Spotify does not exactly match the URI ArrNexus sends.
- SoundCloud/Jamendo/Last.fm failures should be checked against the provider's current developer credentials and Unified Logs.

### Safety / privacy

- Leave secret fields blank to keep an existing saved secret; never paste them into support screenshots.

### Related guides

`spotify`

## Spotify app setup & per-user OAuth

Spotify has two layers in ArrNexus: application credentials enable catalogue/API access; per-user OAuth grants personal saved music, playlists, top items and recent listening.

### Before you start

- A Spotify developer application, Client ID and Client Secret.
- For remote ArrNexus, a public HTTPS callback address.
- Spotify Development Mode currently requires the app owner to have Premium and each authenticated user to be on the app allowlist; check Spotify's current official quota-mode documentation because these rules can change.

### Setup

1. Open Music API Settings and copy the callback URI ArrNexus shows. For this application it ends with /music/spotify/callback.
2. In Spotify Developer Dashboard → your app → Settings, add that exact Redirect URI. Do not add/remove a trailing slash unless ArrNexus shows it.
3. Save the Spotify Client ID and Client Secret in ArrNexus.
4. In Spotify Development Mode, add the intended Spotify account under Users Management/allowlist.
5. Open Music Hub → Spotify and click Connect Spotify, approve the scopes, and let Spotify redirect back to ArrNexus.

### How to use it

- Each ArrNexus profile links Spotify independently. Disconnecting one profile does not remove the shared application credentials.

### What working looks like

- Music Hub changes from 'app ready — account not linked' to a linked profile and loads personal shelves such as saved tracks/albums/playlists/top/recent data.

### If it does not work

- INVALID_REDIRECT_URI: compare the two callback strings character-for-character and require HTTPS except explicit 127.0.0.1/[::1] loopback development.
- 403 after successful login in Development Mode: confirm that Spotify user is allowlisted and the app owner still meets Spotify's current Premium requirement.
- OAuth state validation error: start Connect Spotify again; do not reuse an old callback URL.
- 429: Spotify rate/quota limits are upstream; retry later and inspect the returned reason.

### Safety / privacy

- The Client Secret and refresh tokens are sensitive. ArrNexus stores them as secret settings and must not print them in UI/logs.

### Related guides

`music-api-settings`, `music-hub`

# Connections & integrations

## Ecosystem & Community Connector SDK

Ecosystem manages data-defined connectors around the DUMB stack and can install supported connector definitions without executing arbitrary third-party Python code.

### Before you start

- A connector definition only if you are extending beyond built-in integrations.

### Setup

1. Review built-in connector state first.
2. For a community connector/provider, install only the supported JSON/data definition format.

### How to use it

- Probe enabled connectors and inspect their status from one place.

### What working looks like

- The expected authenticated/status response is parsed and displayed.

### If it does not work

- If a connector returns HTML where JSON/API data is expected, correct the API endpoint rather than weakening health verification.

### Safety / privacy

- Treat community definitions as untrusted data and review URLs/credential fields before enabling them.

## AIOStreams Bridge

The AIOStreams Bridge manages one AIOStreams user configuration safely: verify, fetch full config, generate a masked auto-wire preview, reject stale previews, back up before PUT, verify after write and support rollback.

### Before you start

- A running AIOStreams instance and an AIOStreams user UUID/alias plus password or encryptedPassword.
- Optional Prowlarr/providers in ArrNexus if you want Auto-Wire to reuse them.

### Setup

1. Create/save an AIOStreams user configuration first.
2. In ArrNexus → AIOStreams enter Base URL, UUID/alias and credential.
3. Verify reachability and authenticated User API access before generating a preview.

### How to use it

- Generate Auto-Wire Preview and inspect every change before Apply.
- Apply only a fresh preview. ArrNexus rechecks the digest, creates a private pre-write backup, sends the full config and verifies the result.
- Use Search diagnostics with a movie/series ID; sensitive playback URLs/headers are redacted.
- Rollback creates another safety backup before restoring the selected configuration.

### What working looks like

- Reachable, authenticated and configured checks are green; Apply preserves unrelated AIOStreams settings and backups are available.

### If it does not work

- Stale preview refusal is a safety PASS: regenerate preview after any remote AIOStreams change.
- Reachable but not authenticated usually means UUID/alias/credential is wrong.
- If Prowlarr wiring is absent, verify ArrNexus has both Prowlarr URL and API key.

### Safety / privacy

- AIOStreams PUT is a full replacement operation, so never bypass preview/digest/backup protections.
- Search diagnostics must not expose Authorization, cookies, proxy headers or playback URLs.

### Related guides

`providers`

# Operations

## Stack Readiness

Stack Readiness deliberately performs bounded live verification of configured services, environment, provider and mount assumptions; it is deeper than the cached Dashboard snapshot.

### Before you start

- Administrator access.

### Setup

1. Configure the services/providers/mounts you actually use before expecting a complete score.

### How to use it

- Open Readiness when commissioning, after an upgrade or when a workflow fails.
- Refresh for a live check instead of using it as the page you repeatedly click during normal navigation.

### What working looks like

- Required pieces for your chosen stack are green and warnings explain optional/missing capabilities.

### If it does not work

- A slow Readiness page normally means one or more external APIs are timing out; inspect Server-Timing/performance logs and test those connections individually.

### Safety / privacy

- Readiness reports state; it does not grant permission to rewrite third-party configurations.

## Problem Centre

Problem Centre turns detected broken links, missing media and other diagnosable conditions into a focused work queue.

### Before you start

- Visible managed libraries and whichever Arr services are needed to classify the problem.

### Setup

1. No separate setup beyond correct libraries/connections.

### How to use it

- Review the problem reason and owning service before choosing repair/search actions.

### What working looks like

- Each issue has enough context to decide whether ArrNexus, the Arr application, mount layer or provider should own the fix.

### If it does not work

- If it takes several seconds, inspect slow_request logs; Problem Centre shares a cached broken-link scan with Maintenance.

### Safety / privacy

- Prefer explicit bounded repairs. Do not delete source media merely because a managed-library link is broken.

## Maintenance, repair & housekeeping

Maintenance combines backups/repair context with filesystem/source/link/import inventory work and v10.1 Library Consolidation for duplicate movie/episode symlinks.

### Before you start

- Correct source/library visibility.

### Setup

1. No separate setup; configure backups under Settings.

### How to use it

- Inspect broken links/source mappings first, then run a specific repair action.
- Open Library Consolidation to scan every managed movie/TV symlink and preview duplicate groups before applying cleanup.
- Use Diagnostics when collecting support information.

### What working looks like

- Repair changes only the intended managed link/state and the action is logged.
- Consolidation keeps one highest-ranked valid link per movie part/episode, asks the owning Radarr/Sonarr item to rescan, and can optionally remove only provider sources made unreferenced by that exact operation.

### If it does not work

- If Maintenance is slow, search Unified Logs for slow_request GET /maintenance and verify filesystem/network mounts are responsive.
- If a consolidation preview becomes stale, run a fresh preview; ArrNexus refuses to apply a changed library plan.

### Safety / privacy

- Take a database backup before broad maintenance or upgrades.
- Provider deletion in Library Consolidation is off by default and requires an exact Real-Debrid torrent match.

### Related guides

`backups`, `logs`

## Unified Logs & performance diagnosis

Unified Logs combines ArrNexus events with known-error explanations and records slow route timings introduced to make performance problems measurable.

### Before you start

- Sign in.

### Setup

1. No setup. External log sources may require their own connector configuration.

### How to use it

- Filter by source/event/level. Search source=performance or event=slow_request to find routes exceeding the threshold.
- Use request_failed to locate exceptions that caused HTTP failures.

### What working looks like

- A problem can be tied to a route/service/action instead of guessed from UI latency.

### If it does not work

- If a page feels slow but has no slow_request entry, inspect browser/network latency and the X-ArrNexus-Elapsed-Ms response header.

### Safety / privacy

- Sanitize raw logs before sharing publicly; use the diagnostics bundle when possible.

# Administration

## General Settings

Settings manages application branding/public URL, SMTP, users, paths, music application credentials, policy, acquisition, notifications, backups, diagnostics and update source.

### Before you start

- Administrator access.

### Setup

1. Configure only the sections your deployment uses. Most normal state is stored under /data rather than a large .env file.

### How to use it

- Save one section at a time and verify the related feature before changing another major subsystem.

### What working looks like

- Saved values persist across container rebuilds because ./data is preserved.

### If it does not work

- If a secret field shows blank after reload, that can be intentional masking; the page should indicate configured state.
- If settings vanish after an upgrade, confirm the old data directory was copied/mounted into the new release.

### Safety / privacy

- Back up /data before major version upgrades.

### Related guides

`notifications`, `backups`, `updates`, `language-guard`, `music-api-settings`

## Language Guard

Language Guard uses ffprobe against the actual media stream metadata before a DMM source is linked into the managed library.

### Before you start

- The container image includes ffmpeg/ffprobe and the media source is visible in the active namespace.

### Setup

1. Configure required audio/subtitle languages and unknown-metadata behaviour under Settings.
2. Choose whether rejected Real-Debrid sources should be removed after an exact provider match; v10.1 enables this by default.

### How to use it

- Run the check from Item Review or allow the import workflow to consult the cached result.
- A rejection is recorded separately from a true import failure and the DMM Inbox cache is invalidated immediately.

### What working looks like

- Audio/subtitle streams are reported explicitly and policy passes/fails with a reason.
- Probe failures display as Language check failed; policy failures display as Language rejected; successful provider cleanup removes the rejected item from Waiting.

### If it does not work

- Filename language tags are not enough; if ffprobe cannot access the actual path, fix namespace/mount visibility.
- Unknown metadata may fail closed by policy.
- If rejected-source cleanup is skipped, inspect the job result: ArrNexus will state whether RD was disconnected, no exact torrent matched, or the match was ambiguous.

### Safety / privacy

- Rejected-source cleanup never uses fuzzy title matching. It requires one exact Real-Debrid torrent identity; ambiguous matches remain untouched.

## Notifications: ntfy, Gotify, Discord & email

Notifications can report ArrNexus events through configured channels and include a test action so credentials/endpoints can be verified before relying on alerts.

### Before you start

- A channel endpoint/account. Email notifications also require SMTP settings.

### Setup

1. Enable notifications in Settings.
2. Configure one or more channels: ntfy server/topic/token, Gotify URL/token, Discord webhook, or email recipient.
3. Use Send test notification.

### How to use it

- Choose failures-only if you want to suppress routine success messages.

### What working looks like

- The test reaches the intended destination and ArrNexus logs the result.

### If it does not work

- Email failures: verify SMTP host/port/STARTTLS/user/password/from address.
- Webhook/token failures: verify the secret and that the ArrNexus container can reach the endpoint.

### Safety / privacy

- Webhook URLs and app tokens are credentials; never expose them in screenshots/logs.

# Accounts & security

## Password recovery & SMTP

Password recovery creates a single-use, time-limited token and emails a reset link through the administrator's SMTP configuration.

### Before you start

- The user profile has an email address and SMTP is configured under Settings.

### Setup

1. Configure SMTP host, port, username/password, From address and STARTTLS as required by your provider.
2. Set the public ArrNexus URL when using a reverse proxy so reset links use the public HTTPS address.
3. Use Forgot password on the login page and enter the saved profile email.

### How to use it

- Open the emailed link within 30 minutes and choose a new password.

### What working looks like

- The reset page accepts the one-time token and the new password works at login.

### If it does not work

- No email: inspect Unified Logs and SMTP settings; ArrNexus intentionally does not reveal whether an address exists.
- Wrong internal URL in email: set/verify the public URL/reverse-proxy forwarded host/proto.

### Safety / privacy

- Reset tokens are single-use and expire. Connected service credentials are not changed by an ArrNexus password reset.

# Administration

## Backups, sanitized config & diagnostics

ArrNexus can create rolling SQLite backups, export/import non-secret configuration and generate a sanitized diagnostics bundle.

### Before you start

- Administrator access and writable /data.

### Setup

1. Set automatic backup enabled/retention under Settings.
2. Create a manual backup before major upgrades.

### How to use it

- Download a database backup when you need a full ArrNexus-state restore.
- Use sanitized config export/import for portable non-secret settings.
- Use Diagnostics for support because it masks known secrets.

### What working looks like

- Backup files appear with size/time and restore/export operations complete without exposing credentials.

### If it does not work

- If backup creation fails, check /data permissions and free space.

### Safety / privacy

- Database backups are private even if UI secrets are masked; they may contain encrypted/protected application state. Do not publish them.

## Native updates, release ZIPs & rollback

ArrNexus v10 can discover newer GitHub Releases, verify the release checksum, back up the live SQLite database, run the complete regression validator, stage the new runtime and restart itself without Portainer or Docker-socket access.

### Before you start

- The first move to v10 is a normal Docker rebuild because v10 introduces the self-update bootstrap.
- Future in-app updates require outbound HTTPS access to GitHub Releases and a release containing both an ArrNexus ZIP and matching .sha256 asset.

### Setup

1. Keep Fudmonk95/ArrNexus as the update repository unless you deliberately maintain your own fork.
2. Select Stable/Beta/Development under Settings → Diagnostics & updates.
3. When an update notification appears, review the target version and press Install update.

### How to use it

- ArrNexus downloads the release, refuses non-HTTPS assets, verifies SHA-256, blocks unsafe ZIP traversal paths, creates a SQLite backup and validates the staged source before switching runtime.
- The browser watches update progress and reloads automatically once the new /api/health reports the target version.
- The v10 bootstrap keeps previous runtimes under /data/runtime/releases so a newly activated runtime that cannot become healthy can be rolled back automatically.

### What working looks like

- Update status progresses through download, verify, backup, dependencies, validation, staging and restart.
- The page reloads with the new ArrNexus version while users/connections/providers remain present because /data is persistent.

### If it does not work

- If Install is disabled, confirm the GitHub Release includes both the ArrNexus ZIP and .sha256 asset.
- If a future release needs Docker/OS/bootstrap changes, perform a normal container rebuild for that release instead of bypassing the safety boundary.
- If the new runtime cannot become healthy, inspect update status/logs; the bootstrap should return to the previous staged runtime.

### Safety / privacy

- ArrNexus never needs the Docker socket for ordinary v10+ application updates.
- The live database is not replaced by release files; a transaction-safe SQLite backup is created first.
- Do not disable checksum or validator failures to force an update through.

## Community music/provider JSON

The community catalogue/provider SDK uses JSON/data definitions rather than arbitrary Python execution.

### Before you start

- A provider JSON definition you trust and have reviewed.

### Setup

1. Install the JSON from the supported Settings upload form.
2. Confirm external search URLs are real HTTPS destinations and not documentation placeholders.

### How to use it

- The provider appears as a labelled catalogue/discovery source where supported.

### What working looks like

- Provider searches build only the expected bounded URL/data request.

### If it does not work

- example.com/example.invalid URLs are intentionally rejected.
- Malformed JSON should be fixed rather than bypassing validation.

### Safety / privacy

- Review third-party URLs and never embed private credentials directly in a community JSON file intended for sharing.

# Accounts & security

## Profile

Profile stores per-user display name, email and password. Product-wide visual styling is fixed; the old theme gallery is intentionally removed.

### Before you start

- Signed-in user.

### Setup

1. Set an email if you want password recovery to work.

### How to use it

- Update display name/email or enter a new password; leave password blank to keep the current one.

### What working looks like

- Changes are reflected in the sidebar/account and survive restart.

### If it does not work

- If password recovery fails, verify both profile email and administrator SMTP settings.

### Safety / privacy

- Do not share accounts; use separate users and permissions.

## Security & privacy model

ArrNexus is designed for a private self-hosted control plane: secret settings are masked, diagnostics are sanitized, source media is preserved by non-destructive workflows and community extensions are data-only.

### Before you start

- Deploy behind appropriate network/reverse-proxy access controls for your environment.

### Setup

1. Use HTTPS when exposing ArrNexus remotely.
2. Create separate user accounts and keep admin access limited.
3. Back up /data securely.

### How to use it

- Treat any raw API key/token/webhook/backup as sensitive even if ArrNexus normally masks it.

### What working looks like

- Public landing/help/release export contain no private library/IP/path/credential state; private operational pages require authentication.

### If it does not work

- If a credential appears in UI/logs/diagnostics, treat it as a security bug and rotate the exposed secret.

### Safety / privacy

- Do not expose ArrNexus directly to the Internet without understanding your reverse proxy and authentication boundary.

# Operations

## Performance troubleshooting

ArrNexus measures route time with Server-Timing and X-ArrNexus-Elapsed-Ms and logs requests above the slow threshold so optimisation can target the actual expensive route.

### Before you start

- A page that feels slow enough to investigate.

### Setup

1. No setup required.

### How to use it

- Use the application normally, then open Unified Logs and filter source=performance/event=slow_request.
- Compare repeated timings after caches warm; one first-load result is not enough to diagnose a route.

### What working looks like

- You can identify whether latency is in /maintenance, /music/artist, /inbox, /readiness, InfiniDysk or another route and then test its upstream dependency.

### If it does not work

- If server time is low but browser navigation is slow, inspect browser/network/reverse-proxy latency.
- If server time is high, test the integrations used by that route and look for upstream timeout patterns.

### Safety / privacy

- Avoid solving slowness by disabling authentication/validation or reducing safety timeouts globally.

### Related guides

`logs`

# Start here

## Public download & deployment methods

The public landing page can export a clean source-only ZIP from the running build and documents ZIP, Git clone, Portainer Git stack and future official-container deployment paths.

### Before you start

- For the built-in exporter, a running ArrNexus instance.

### Setup

1. Use /download/latest for the source ZIP and /download/latest.sha256 for its digest.
2. For Git deployment, point Portainer at the public repository and docker-compose.yml when the project is published.
3. GHCR examples remain templates until an official ArrNexus image is actually published.

### How to use it

- Validate a downloaded source release before Docker build; preserve data separately during upgrades.

### What working looks like

- The exported ZIP contains source/docs only and excludes runtime/private state.

### If it does not work

- If an unofficial image appears before an official release, do not assume it is trusted because it uses the ArrNexus name.

### Safety / privacy

- Public exports must never contain /data, databases, .env, backups, virtualenvs, bytecode or secrets.
