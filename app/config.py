from dataclasses import dataclass
import os


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    username: str = env("APP_USERNAME", "admin")
    password: str = env("APP_PASSWORD", "change-me")
    session_secret: str = env("SESSION_SECRET", "change-this-session-secret")
    dumb_root: str = env("DUMB_ROOT", "/mnt/debrid")
    source_root: str = env("SOURCE_ROOT", "/mnt/debrid/decypharr/__all__")
    radarr_process_match: str = env("RADARR_PROCESS_MATCH", "/opt/radarr/Radarr/Radarr")
    radarr_data_match: str = env("RADARR_DATA_MATCH", "--data=/radarr/nzbdav")
    db_path: str = env("DB_PATH", "/data/router.db")

    radarr_url: str = env("RADARR_URL", "http://192.168.137.10:7878")
    radarr_api_key: str = env("RADARR_API_KEY")
    radarr_quality_profile_name: str = env("RADARR_QUALITY_PROFILE_NAME", "Any HD")

    sonarr_url: str = env("SONARR_URL", "http://192.168.137.10:8989")
    sonarr_api_key: str = env("SONARR_API_KEY")
    sonarr_quality_profile_name: str = env("SONARR_QUALITY_PROFILE_NAME", "Any")

    lidarr_url: str = env("LIDARR_URL", "http://192.168.137.10:8686")
    lidarr_api_key: str = env("LIDARR_API_KEY")
    lidarr_quality_profile_name: str = env("LIDARR_QUALITY_PROFILE_NAME", "Any")
    lidarr_metadata_profile_name: str = env("LIDARR_METADATA_PROFILE_NAME", "Standard")

    prowlarr_url: str = env("PROWLARR_URL", "http://192.168.137.10:9696")
    prowlarr_api_key: str = env("PROWLARR_API_KEY")

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


settings = Settings()
