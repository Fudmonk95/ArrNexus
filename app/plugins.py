from __future__ import annotations

import json
from pathlib import Path
from .config import settings
from urllib.parse import quote_plus, urlparse

PLUGIN_DIR = Path(settings.db_path).resolve().parent / "providers"


def safe_plugin_search_template(template: str) -> bool:
    """Reject documentation/placeholder catalogue templates before UI exposure."""
    value = str(template or "").strip()
    if "{query}" not in value or not value.startswith(("https://", "http://")):
        return False
    try:
        host = (urlparse(value.replace("{query}", "validator")).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    if not host:
        return False
    if host in {"example.com", "example.org", "example.net", "example.invalid"} or host.endswith((".example.com", ".example.org", ".example.net", ".example.invalid")):
        return False
    return True


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
            if not key or not name or not safe_plugin_search_template(template):
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
