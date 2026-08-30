from __future__ import annotations

import json
from pathlib import Path
from .config import settings
from urllib.parse import quote_plus

PLUGIN_DIR = Path(settings.db_path).resolve().parent / "providers"


def load_catalog_plugins() -> list[dict]:
    """Load safe, data-only provider plugins from /data/providers/*.json.

    Plugins cannot execute Python. They only describe an external catalog search
    URL, which makes this suitable for community-contributed provider packs.
    """
    out: list[dict] = []
    try:
        PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return out
    for path in sorted(PLUGIN_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            key = str(raw.get("key") or path.stem).strip().lower().replace(" ", "-")
            name = str(raw.get("name") or key).strip()
            template = str(raw.get("search_url") or "").strip()
            if not key or not name or "{query}" not in template or not template.startswith(("https://", "http://")):
                continue
            out.append({
                "key": f"plugin-{key}",
                "name": name,
                "mode": "plugin",
                "description": str(raw.get("description") or "Community catalog provider"),
                "search_url": template,
                "source_file": path.name,
            })
        except Exception:
            continue
    return out


def plugin_search_url(plugin: dict, query: str) -> str:
    return str(plugin.get("search_url") or "").replace("{query}", quote_plus(query or ""))
