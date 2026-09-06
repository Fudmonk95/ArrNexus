# ArrNexus v11.0.0-beta

ArrNexus v11 is a deliberately smaller, Zurg-first control plane for my own media server.

## Why v11 exists

Earlier ArrNexus releases accumulated a large amount of low-level media handling. That made ArrNexus responsible for work that no longer belongs here.

The current Zurg nightly builds now provide the source-library layer I need, including the provider-facing and low-level media presentation work. Because that layer is working reliably on my server, ArrNexus no longer tries to duplicate it.

**Zurg is now the source library. ArrNexus is the management and orchestration layer above it.**

For Zurg itself, start with the official project and its release-cycle documentation:

- https://github.com/debridmediamanager/zurg-public
- https://github.com/debridmediamanager/zurg-public/wiki
- https://github.com/debridmediamanager/zurg-public/wiki/Release-cycle

The public Zurg project explains the stable/nightly release model and how to obtain the build appropriate to your setup.

## Personal-build notice

From v11 onward, ArrNexus releases are built for **my own server and my own workflow**. I am no longer trying to make every future release fit other people's stacks.

The source remains available for people who want to inspect it, fork it and change it under the repository's existing licence. If this direction is not right for your setup, you are welcome to continue your own build and modify it however you want. Future ArrNexus releases from me should be treated as personal-server releases rather than general support releases.

## The v11 architecture

```text
                 Seerr
                   |
                   v
          Sonarr / Radarr
             |       |
             +---+---+
                 |
              ArrNexus
         health / visibility
       lists / orchestration
                 |
                 v
        /zurg_mnt/zurg
                 |
                Zurg
                 |
                 v
              Jellyfin

Music Hub: Spotify + Beatport + Lidarr
Media Automation: Jellyfin collections + Kometa YAML
```

ArrNexus treats `/zurg_mnt/zurg` as read-only. It observes Zurg; it does not rename, move, split, extract or stage source media. DMM talks directly to Zurg on this server, so ArrNexus does not maintain a second acquisition inbox between them.

## What remains

- Zurg filesystem and endpoint health
- Zurg library activity and unplayable visibility
- Sonarr connection and native list import
- Radarr connection and native list import
- Lidarr connection and Music Hub acquisition
- Prowlarr health visibility
- Seerr request tracking
- Jellyfin library and collection automation
- Spotify Music Hub
- Beatport search hand-off
- Trakt, TMDb, IMDb, RSS, JSON and Simkl list sources
- Kometa YAML import/export for collection definitions
- local authentication, logs, diagnostics and database backups

## Live request tracking

The **Live Requests** page polls Seerr, Sonarr/Radarr and Zurg every two seconds and builds a single request timeline:

```text
Requested
  -> Approved
  -> Searching
  -> Grabbed
  -> Zurg working
  -> Mounted in Zurg
  -> Imported
  -> Finished
```

The exact stages are inferred from the systems that currently own each step. ArrNexus stores stage changes so the current state and recent transitions remain visible.

## Music Hub

Music Hub has been reduced to the sources I actually use:

- **Spotify** for account/library discovery and catalogue search
- **Beatport** for current web search hand-off
- **Lidarr** for adding and searching artists

Beatport no longer depends on a private API integration. ArrNexus creates a current Beatport search URL and hands the query to Beatport directly.

## Media Automation / Kometa

Media Automation is now Jellyfin-first. ArrNexus can:

- resolve collection definitions from supported list sources;
- preview matches against the Jellyfin library;
- create/update Jellyfin collections;
- run collection definitions on a schedule;
- import supported Kometa YAML collection IDs; and
- export collection definitions as Kometa YAML.

ArrNexus does not pretend to own the external Kometa runtime. It manages the collection definitions and the Jellyfin result on this server.

## Installation

### Requirements

- Docker Engine with Compose support
- a working Zurg mount on the host at `/zurg_mnt/zurg`
- the `/zurg_mnt` mount capable of propagating into the ArrNexus container
- whichever Arr services you want ArrNexus to use

### Deploy

```bash
cp .env.example .env
mkdir -p data
docker compose up -d --build
```

Open:

```text
http://SERVER-IP:8484
```

On first launch, create the local administrator account and then configure service URLs/API keys under **Arr Services**.

### Zurg mount

The Compose file bind-mounts the host's `/zurg_mnt` read-only using `rslave` propagation. The expected source root inside ArrNexus is:

```text
/zurg_mnt/zurg
```

Do not install another mount process inside ArrNexus. Zurg owns the mount; ArrNexus only consumes it.

## Updating from v10

v11 is an architectural reset, not a cosmetic update. Back up your existing ArrNexus data first.

The v11 database initializer can reuse the core local user/list/automation tables from an existing database, but the application no longer loads the removed low-level subsystems. For the cleanest personal-server migration, keep a copy of the old data directory, deploy v11 separately, then configure only the services you still use.

## Release status

**v11.0.0-beta** is the first stripped Zurg-first release. It intentionally has fewer features than v10 because the removed features no longer belong in ArrNexus on this server.
