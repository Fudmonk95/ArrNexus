from dataclasses import dataclass
import os
import secrets
from pathlib import Path


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "on"}


def persistent_secret() -> str:
    """Return a stable session secret without requiring an env file.

    ARRNEXUS_SESSION_SECRET/SESSION_SECRET can still override this for advanced
    deployments, but a normal Portainer deployment only needs /data persisted.
    """
    supplied = env("ARRNEXUS_SESSION_SECRET") or env("SESSION_SECRET")
    if supplied:
        return supplied
    path = Path(env("DB_DIR", "/data")) / "session_secret"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = secrets.token_hex(48)
        path.write_text(value + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return value
    except OSError:
        # Last-resort ephemeral secret. This keeps the app bootable on a
        # read-only filesystem, although sessions will reset after restart.
        return secrets.token_hex(48)


@dataclass(frozen=True)
class Settings:
    db_path: str = env("DB_PATH", "/data/router.db")
    session_secret: str = persistent_secret()
    app_name: str = env("APP_NAME", "ArrNexus")

    # Optional legacy bootstrap. Fresh installs do not need these variables;
    # they are sent to /setup and create the first administrator in the UI.
    bootstrap_username: str = env("APP_USERNAME")
    bootstrap_password: str = env("APP_PASSWORD")

    dumb_root: str = env("DUMB_ROOT", "/mnt/debrid")
    source_root: str = env("SOURCE_ROOT", "/mnt/debrid/decypharr/__all__")
    radarr_process_match: str = env("RADARR_PROCESS_MATCH", "/opt/radarr/Radarr/Radarr")
    radarr_data_match: str = env("RADARR_DATA_MATCH", "--data=/radarr/nzbdav")

    # All connection values are only fallbacks. UI-saved values in SQLite win.
    radarr_url: str = env("RADARR_URL", "http://host.docker.internal:7878")
    radarr_api_key: str = env("RADARR_API_KEY")
    radarr_quality_profile_name: str = env("RADARR_QUALITY_PROFILE_NAME", "Any HD")

    sonarr_url: str = env("SONARR_URL", "http://host.docker.internal:8989")
    sonarr_api_key: str = env("SONARR_API_KEY")
    sonarr_quality_profile_name: str = env("SONARR_QUALITY_PROFILE_NAME", "Any")

    lidarr_url: str = env("LIDARR_URL", "http://host.docker.internal:8686")
    lidarr_api_key: str = env("LIDARR_API_KEY")
    lidarr_quality_profile_name: str = env("LIDARR_QUALITY_PROFILE_NAME", "Any")
    lidarr_metadata_profile_name: str = env("LIDARR_METADATA_PROFILE_NAME", "Standard")

    prowlarr_url: str = env("PROWLARR_URL", "http://host.docker.internal:9696")
    prowlarr_api_key: str = env("PROWLARR_API_KEY")

    jellyfin_url: str = env("JELLYFIN_URL", "http://host.docker.internal:8096")
    jellyfin_api_key: str = env("JELLYFIN_API_KEY")

    seerr_url: str = env("SEERR_URL", "http://host.docker.internal:5055")
    seerr_api_key: str = env("SEERR_API_KEY")

    # Public/free music discovery. No personal listener account is needed.
    musicbrainz_base: str = env("MUSICBRAINZ_BASE", "https://musicbrainz.org/ws/2")
    listenbrainz_base: str = env("LISTENBRAINZ_BASE", "https://api.listenbrainz.org")
    music_user_agent: str = env("MUSIC_USER_AGENT", "ArrNexus/5.0 (self-hosted media manager)")
    public_music_country: str = env("PUBLIC_MUSIC_COUNTRY", "GB")
    enable_itunes_search: bool = env_bool("ENABLE_ITUNES_SEARCH", True)

    radarr_default_root: str = env("RADARR_DEFAULT_ROOT", "/mnt/debrid/nzbdav-symlinks/radarr-nzbdav")
    radarr_kids_root: str = env("RADARR_KIDS_ROOT", "/mnt/debrid/nzbdav-symlinks/radarr-kids-nzbdav")
    radarr_christmas_root: str = env("RADARR_CHRISTMAS_ROOT", "/mnt/debrid/nzbdav-symlinks/radarr-christmas-nzbdav")
    radarr_halloween_root: str = env("RADARR_HALLOWEEN_ROOT", "/mnt/debrid/nzbdav-symlinks/radarr-halloween-nzbdav")
    radarr_easter_root: str = env("RADARR_EASTER_ROOT", "/mnt/debrid/nzbdav-symlinks/radarr-easter-nzbdav")

    sonarr_default_root: str = env("SONARR_DEFAULT_ROOT", "/mnt/debrid/nzbdav-symlinks/sonarr-nzbdav")
    sonarr_kids_root: str = env("SONARR_KIDS_ROOT", "/mnt/debrid/nzbdav-symlinks/sonarr-kids-nzbdav")
    sonarr_netflix_root: str = env("SONARR_NETFLIX_ROOT", "/mnt/debrid/nzbdav-symlinks/sonarr-netflix-nzbdav")
    sonarr_disney_root: str = env("SONARR_DISNEY_ROOT", "/mnt/debrid/nzbdav-symlinks/sonarr-disneyplus-nzbdav")
    sonarr_amazon_root: str = env("SONARR_AMAZON_ROOT", "/mnt/debrid/nzbdav-symlinks/sonarr-amazon-nzbdav")
    sonarr_apple_root: str = env("SONARR_APPLE_ROOT", "/mnt/debrid/nzbdav-symlinks/sonarr-appletv-nzbdav")
    sonarr_bbc_root: str = env("SONARR_BBC_ROOT", "/mnt/debrid/nzbdav-symlinks/sonarr-bbc-nzbdav")

    lidarr_root: str = env("LIDARR_ROOT", "/mnt/debrid/nzbdav-symlinks/lidarr-nzbdav")

    @property
    def movie_roots(self):
        return {
            "default": self.radarr_default_root,
            "kids": self.radarr_kids_root,
            "christmas": self.radarr_christmas_root,
            "halloween": self.radarr_halloween_root,
            "easter": self.radarr_easter_root,
        }

    @property
    def tv_roots(self):
        return {
            "default": self.sonarr_default_root,
            "kids": self.sonarr_kids_root,
            "netflix": self.sonarr_netflix_root,
            "disney": self.sonarr_disney_root,
            "amazon": self.sonarr_amazon_root,
            "apple": self.sonarr_apple_root,
            "bbc": self.sonarr_bbc_root,
        }

    @property
    def arr_host(self) -> str:
        from urllib.parse import urlsplit
        return urlsplit(self.radarr_url).hostname or "127.0.0.1"


settings = Settings()
