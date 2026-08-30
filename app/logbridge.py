from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx

from .ecosystem import connector_config
from .infinidysk import InfiniDyskClient

_LEVEL_RE = re.compile(r"\b(TRACE|DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL)\b", re.I)
_TS_RE = re.compile(r"^(?P<ts>\d{4}[-/]\d{2}[-/]\d{2}[ T][^ ]+|\d{1,2}:\d{2}:\d{2}(?:\.\d+)?)")


def _level(text: str) -> str:
    m = _LEVEL_RE.search(text or "")
    if not m:
        return "info"
    v = m.group(1).lower()
    if v.startswith("warn"):
        return "warning"
    if v in {"fatal","critical"}:
        return "critical"
    return v


def _row(source: str, message: str, created_at: str = "", event: str = "external") -> dict[str, Any]:
    msg = (message or "").rstrip()
    m = _TS_RE.search(msg)
    if not created_at and m:
        created_at = m.group("ts")
    return {
        "id": None,
        "level": _level(msg),
        "source": source,
        "event": event,
        "message": msg,
        "context": "{}",
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "external": True,
    }


def parse_text_log(text: str, source: str, limit: int = 1000) -> list[dict[str, Any]]:
    rows = [_row(source, line) for line in (text or "").splitlines() if line.strip()]
    return rows[-max(1, int(limit)):]


async def dumb_logs(process_name: str = "DUMB", limit: int = 1000) -> tuple[list[dict], str]:
    cfg = connector_config("dumb")
    if not cfg.get("enabled") or not cfg.get("url"):
        return [], "DUMB connector is not configured"
    headers = {"User-Agent": "ArrNexus/9.0"}
    if cfg.get("api_key"):
        headers["X-Api-Key"] = str(cfg.get("api_key"))
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=headers) as client:
            r = await client.get(str(cfg["url"]).rstrip("/") + "/logs", params={"process_name": process_name, "tail_bytes": 8388608})
        if r.status_code in {401,403}:
            return [], f"DUMB log API authentication failed (HTTP {r.status_code})"
        if r.status_code >= 400:
            return [], f"DUMB log API returned HTTP {r.status_code}"
        data = r.json()
        text = str(data.get("chunk") or data.get("log") or "") if isinstance(data, dict) else ""
        return parse_text_log(text, f"dumb:{process_name}", limit), ""
    except Exception as exc:
        return [], str(exc)


async def infinidysk_warning_logs(limit: int = 500) -> tuple[list[dict], str]:
    """Read InfiniDysk's supported SAB warnings feed.

    InfiniDysk documents `mode=warnings` as recent Warning-and-above log entries.
    This intentionally does not pretend to be its entire local log file.
    """
    try:
        data = await InfiniDyskClient().sab("warnings")
    except Exception as exc:
        return [], str(exc)
    candidates: list[Any] = []
    if isinstance(data, dict):
        for key in ("warnings", "warning", "items", "results"):
            value = data.get(key)
            if isinstance(value, list):
                candidates = value
                break
        if not candidates and isinstance(data.get("result"), list):
            candidates = data["result"]
    rows: list[dict] = []
    for item in candidates[-max(1,int(limit)):]:
        if isinstance(item, str):
            rows.append(_row("infinidysk", item, event="warning"))
        elif isinstance(item, dict):
            msg = str(item.get("message") or item.get("msg") or item.get("text") or item)
            ts = str(item.get("timestamp") or item.get("time") or item.get("created_at") or "")
            row = _row("infinidysk", msg, ts, event=str(item.get("event") or "warning"))
            if item.get("level"):
                row["level"] = _level(str(item.get("level")))
            rows.append(row)
    return rows, ""


async def external_log_rows(origin: str, process_name: str = "DUMB", limit: int = 1000) -> tuple[list[dict], str]:
    origin = (origin or "arrnexus").lower()
    if origin == "dumb":
        return await dumb_logs(process_name, limit)
    if origin == "infinidysk":
        return await infinidysk_warning_logs(limit)
    return [], ""
