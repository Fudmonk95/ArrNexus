# DMM Arr Router v0.1

A small local web UI that bridges media already visible in Decypharr's `__all__` directory into Radarr/Sonarr-managed symlink libraries, while keeping the original Decypharr content untouched.

It also includes an Arr status page, library directory view, and a Lidarr/Usenet music search page.

## Safety model

- The app **never moves or deletes** anything from Decypharr `__all__`.
- Imports create symlinks into the selected Arr library.
- Existing Radarr/Sonarr matches are preferred.
- New movies/series can be added to Radarr/Sonarr from an Arr lookup result before linking.
- TV files must contain a parseable `SxxEyy` or `1x01` style episode number; otherwise the app refuses the import rather than guessing.

## Default routing

### Movies

- default → `/mnt/debrid/nzbdav-symlinks/radarr-nzbdav`
- kids → `/mnt/debrid/nzbdav-symlinks/radarr-kids-nzbdav`
- christmas → `/mnt/debrid/nzbdav-symlinks/radarr-christmas-nzbdav`
- halloween → `/mnt/debrid/nzbdav-symlinks/radarr-halloween-nzbdav`
- easter → `/mnt/debrid/nzbdav-symlinks/radarr-easter-nzbdav`

### TV

- default → `/mnt/debrid/nzbdav-symlinks/sonarr-nzbdav`
- kids → `/mnt/debrid/nzbdav-symlinks/sonarr-kids-nzbdav`
- netflix → `/mnt/debrid/nzbdav-symlinks/sonarr-netflix-nzbdav`
- disney → `/mnt/debrid/nzbdav-symlinks/sonarr-disneyplus-nzbdav`
- amazon → `/mnt/debrid/nzbdav-symlinks/sonarr-amazon-nzbdav`
- apple → `/mnt/debrid/nzbdav-symlinks/sonarr-appletv-nzbdav`
- bbc → `/mnt/debrid/nzbdav-symlinks/sonarr-bbc-nzbdav`

All of these are configurable in `.env`.

## Portainer / Docker setup

1. Copy `.env.example` to `.env`.
2. Fill in the Radarr, Sonarr and Lidarr API keys.
3. Change the UI password and session secret.
4. Check the Arr URLs. Defaults assume the Arrs are reachable on `192.168.137.10`.
5. Deploy the stack with Docker Compose / Portainer.
6. Browse to `http://192.168.137.10:8484` by default.

### Generate a session secret

```bash
openssl rand -hex 32
```

## First test

Use one movie that is already in Decypharr `__all__`.

1. Open **DMM Inbox**.
2. Select the movie.
3. Confirm the Radarr match and suggested destination.
4. Click **Create symlinks & rescan**.
5. Check Radarr and Jellyfin.

Do not bulk-import the library until several individual tests have worked.

## Music page

The music page talks to Lidarr, not Decypharr. It can:

- search Lidarr metadata for artists;
- list artists already in Lidarr;
- browse their albums;
- run Lidarr's release search for an album;
- grab a release through Lidarr's configured indexers/download client.

For the first version, add new artists to Lidarr itself, then use this UI for browsing/searching/grabbing releases. Automatic artist creation can be added next.

## Notes

This is an initial build. Before using it against a large library, test the path mappings carefully with a few items. The app intentionally refuses ambiguous TV filenames instead of creating bad season folders.
