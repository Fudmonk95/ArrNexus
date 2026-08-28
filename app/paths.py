from __future__ import annotations
from .config import settings
from .db import list_mounts, setting_get


def _mount_map(service: str, kind: str) -> dict[str, str]:
    try:
        rows = [x for x in list_mounts(True) if (x.get('service') or '') == service and (x.get('kind') or '') == kind]
    except Exception:
        rows = []
    out = {}
    for row in rows:
        key = (row.get('destination_key') or 'default').strip() or 'default'
        out[key] = row.get('logical_path') or ''
    return {k:v for k,v in out.items() if v}


def movie_roots() -> dict[str, str]:
    out = dict(settings.movie_roots)
    out.update(_mount_map('radarr','movie'))
    return out


def tv_roots() -> dict[str, str]:
    out = dict(settings.tv_roots)
    out.update(_mount_map('sonarr','tv'))
    return out


def lidarr_root() -> str:
    rows = _mount_map('lidarr','music')
    return rows.get('default') or settings.lidarr_root


def dumb_root() -> str:
    return setting_get("paths.dumb_root", "") or settings.dumb_root


def source_root() -> str:
    try:
        rows=[x for x in list_mounts(True) if (x.get('kind') or '') == 'source']
        if rows and rows[0].get('logical_path'):
            return rows[0]['logical_path']
    except Exception:
        pass
    return settings.source_root


def all_library_roots() -> dict[str, str]:
    roots={f'radarr:{k}':v for k,v in movie_roots().items()}
    roots.update({f'sonarr:{k}':v for k,v in tv_roots().items()})
    roots['lidarr:default']=lidarr_root()
    return roots
