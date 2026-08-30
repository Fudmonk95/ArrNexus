from __future__ import annotations

import re
from typing import Any

import httpx

from .ecosystem import connector_config


class InfiniDyskError(RuntimeError):
    pass


class InfiniDyskClient:
    def __init__(self):
        cfg = connector_config("infinidysk")
        self.url = str(cfg.get("url") or "").rstrip("/")
        self.api_key = str(cfg.get("api_key") or "")
        self.enabled = bool(cfg.get("enabled"))

    def _require(self):
        if not self.enabled:
            raise InfiniDyskError("InfiniDysk connector is disabled")
        if not self.url:
            raise InfiniDyskError("InfiniDysk URL is not configured")

    async def health(self) -> dict[str, Any]:
        self._require()
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent":"ArrNexus/9.0"}) as client:
            r = await client.get(self.url + "/healthz")
        if r.status_code >= 400:
            raise InfiniDyskError(f"Health check failed: HTTP {r.status_code}")
        try:
            data = r.json()
            return data if isinstance(data, dict) else {"status": data}
        except Exception:
            return {"status": r.text.strip() or "healthy"}

    async def sab(self, mode: str, **params) -> dict[str, Any]:
        self._require()
        if not self.api_key and mode not in {"version", "auth"}:
            raise InfiniDyskError("InfiniDysk SAB/API key is not configured")
        query = {"mode": mode, "output": "json", **params}
        if self.api_key:
            query["apikey"] = self.api_key
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers={"User-Agent":"ArrNexus/9.0"}) as client:
            r = await client.get(self.url + "/api", params=query)
        if r.status_code >= 400:
            raise InfiniDyskError(f"SAB API failed: HTTP {r.status_code} {r.text[:300]}")
        try:
            data = r.json()
        except Exception as exc:
            raise InfiniDyskError("InfiniDysk returned a non-JSON SAB response") from exc
        if isinstance(data, dict) and data.get("error"):
            raise InfiniDyskError(str(data.get("error")))
        return data if isinstance(data, dict) else {"result": data}

    async def queue(self) -> dict[str, Any]:
        return await self.sab("queue", start=0, limit=100)

    async def history(self) -> dict[str, Any]:
        return await self.sab("history", start=0, limit=50)

    async def pause(self) -> dict[str, Any]:
        return await self.sab("pause")

    async def resume(self) -> dict[str, Any]:
        return await self.sab("resume")

    async def overview(self, window: str = "24h", sections: str = "all") -> dict[str, Any]:
        """Native InfiniDysk dashboard data. This is preferred over scraping
        Prometheus because it is the same structured API used by InfiniDysk's own
        Overview screen."""
        self._require()
        if not self.api_key:
            raise InfiniDyskError("InfiniDysk API key is not configured")
        if window not in {"1h", "24h", "7d", "30d", "all"}:
            window = "24h"
        headers = {"User-Agent": "ArrNexus/9.0", "X-Api-Key": self.api_key}
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, headers=headers) as client:
            r = await client.get(self.url + "/api/get-overview-stats", params={"window": window, "sections": sections})
        if r.status_code in {401, 403}:
            raise InfiniDyskError(f"InfiniDysk overview authentication failed (HTTP {r.status_code})")
        if r.status_code >= 400:
            raise InfiniDyskError(f"InfiniDysk overview failed: HTTP {r.status_code} {r.text[:300]}")
        try:
            data = r.json()
        except Exception as exc:
            raise InfiniDyskError("InfiniDysk returned a non-JSON Overview response") from exc
        return data if isinstance(data, dict) else {}

    async def metrics(self) -> list[dict[str, Any]]:
        self._require()
        headers = {"User-Agent":"ArrNexus/9.0"}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=headers) as client:
            r = await client.get(self.url + "/metrics")
        if r.status_code >= 400:
            return []
        return parse_prometheus_metrics(r.text)


def parse_prometheus_metrics(text: str) -> list[dict[str, Any]]:
    """Extract a small, readable subset of operational Prometheus metrics."""
    rows: list[dict[str, Any]] = []
    interesting = re.compile(r"(nntp|throughput|bytes|latency|seek|error|fail|active|stream|provider|queue|download)", re.I)
    line_re = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+(-?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?)$")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = line_re.match(line)
        if not m:
            continue
        name, labels, value = m.groups()
        if not interesting.search(name):
            continue
        try:
            numeric = float(value)
        except Exception:
            continue
        rows.append({"name": name, "labels": labels or "", "value": numeric})
    # Avoid flooding the UI. Prefer non-zero values and stable alphabetical order.
    rows.sort(key=lambda x: (0 if x["value"] else 1, x["name"], x["labels"]))
    return rows[:40]
