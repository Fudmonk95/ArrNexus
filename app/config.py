from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import secrets


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def persistent_secret() -> str:
    supplied = env("ARRNEXUS_SESSION_SECRET") or env("SESSION_SECRET")
    if supplied:
        return supplied
    path = Path(env("DB_DIR", "/data")) / "session_secret"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            current = path.read_text(encoding="utf-8").strip()
            if current:
                return current
        current = secrets.token_hex(48)
        path.write_text(current + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return current
    except OSError:
        return secrets.token_hex(48)


@dataclass(frozen=True)
class Settings:
    app_name: str = env("APP_NAME", "ArrNexus")
    db_path: str = env("DB_PATH", "/data/arrnexus.db")
    session_secret: str = persistent_secret()
    public_url: str = env("ARRNEXUS_PUBLIC_URL", "")

    zurg_root: str = env("ZURG_ROOT", "/zurg_mnt/zurg")
    zurg_cache_path: str = env("ZURG_CACHE_PATH", "/host/zurg-rclone-cache")
    magic_root: str = env("MAGIC_ROOT", "/zurg_magic")
    magic_arr_prefix: str = env("MAGIC_ARR_PREFIX", "/zurg_mnt/zurg/__magic__")
    zurg_url: str = env("ZURG_URL", "http://192.168.137.10:9999")

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

    jellyfin_url: str = env("JELLYFIN_URL", "http://192.168.137.10:8096")
    jellyfin_api_key: str = env("JELLYFIN_API_KEY")

    seerr_url: str = env("SEERR_URL", "http://192.168.137.10:5055")
    seerr_api_key: str = env("SEERR_API_KEY")


settings = Settings()
