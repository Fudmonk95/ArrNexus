from __future__ import annotations

import smtplib
from email.message import EmailMessage
import httpx

from .db import setting_get, log_event


def _enabled(key: str, default: bool = False) -> bool:
    raw = setting_get(key, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


async def send_notification(title: str, message: str, level: str = "info", event: str = "general") -> list[dict]:
    """Send configured notifications. Failures are isolated and logged."""
    results: list[dict] = []
    if not _enabled("notify.enabled", False):
        return results
    only_failures = _enabled("notify.failures_only", False)
    if only_failures and level not in {"error", "critical", "warning"}:
        return results

    ntfy_server = setting_get("notify.ntfy.server", "").rstrip("/")
    ntfy_topic = setting_get("notify.ntfy.topic", "")
    ntfy_token = setting_get("notify.ntfy.token", "")
    if ntfy_server and ntfy_topic:
        try:
            headers = {"Title": title, "Tags": "warning" if level in {"warning", "error", "critical"} else "white_check_mark"}
            if ntfy_token:
                headers["Authorization"] = f"Bearer {ntfy_token}"
            async with httpx.AsyncClient(timeout=12.0) as client:
                r = await client.post(f"{ntfy_server}/{ntfy_topic}", content=message.encode(), headers=headers)
            r.raise_for_status()
            results.append({"provider": "ntfy", "ok": True})
        except Exception as exc:
            results.append({"provider": "ntfy", "ok": False, "error": str(exc)})

    gotify_url = setting_get("notify.gotify.url", "").rstrip("/")
    gotify_token = setting_get("notify.gotify.token", "")
    if gotify_url and gotify_token:
        try:
            priority = 8 if level in {"error", "critical"} else 5 if level == "warning" else 2
            async with httpx.AsyncClient(timeout=12.0) as client:
                r = await client.post(f"{gotify_url}/message", params={"token": gotify_token}, json={"title": title, "message": message, "priority": priority})
            r.raise_for_status()
            results.append({"provider": "gotify", "ok": True})
        except Exception as exc:
            results.append({"provider": "gotify", "ok": False, "error": str(exc)})

    discord = setting_get("notify.discord.webhook", "")
    if discord:
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                r = await client.post(discord, json={"content": f"**{title}**\n{message}"})
            r.raise_for_status()
            results.append({"provider": "discord", "ok": True})
        except Exception as exc:
            results.append({"provider": "discord", "ok": False, "error": str(exc)})

    email_to = setting_get("notify.email.to", "")
    smtp_host = setting_get("smtp.host", "")
    if email_to and smtp_host:
        try:
            port = int(setting_get("smtp.port", "587") or 587)
            username = setting_get("smtp.username", "")
            password = setting_get("smtp.password", "")
            from_address = setting_get("smtp.from_address", "") or username
            msg = EmailMessage()
            msg["Subject"] = title
            msg["From"] = from_address
            msg["To"] = email_to
            msg.set_content(message)
            with smtplib.SMTP(smtp_host, port, timeout=12) as server:
                if _enabled("smtp.starttls", True):
                    server.starttls()
                if username:
                    server.login(username, password)
                server.send_message(msg)
            results.append({"provider": "email", "ok": True})
        except Exception as exc:
            results.append({"provider": "email", "ok": False, "error": str(exc)})

    for result in results:
        if not result.get("ok"):
            try:
                log_event("warning", "notifications", "delivery_failed", f"{result['provider']}: {result.get('error','unknown error')}", {"event": event})
            except Exception:
                pass
    return results
