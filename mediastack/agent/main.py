from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query

DOCKER_SOCKET = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
CONFIG_ROOT = Path(os.getenv("CONFIG_ROOT", "/stack-config"))
STACK_LABEL = os.getenv("STACK_LABEL", "arrnexus.mediastack=true")
UPDATE_TIMEOUT = float(os.getenv("UPDATE_TIMEOUT", "8"))

app = FastAPI(title="ArrNexus MediaStack Agent", version="0.1.0")


def docker_client() -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCKET)
    return httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=10.0)


def _cpu_percent(stats: dict[str, Any]) -> float:
    cpu = stats.get("cpu_stats") or {}
    pre = stats.get("precpu_stats") or {}
    cpu_delta = (cpu.get("cpu_usage") or {}).get("total_usage", 0) - (pre.get("cpu_usage") or {}).get("total_usage", 0)
    sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    online = cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
    if cpu_delta > 0 and sys_delta > 0:
        return round((cpu_delta / sys_delta) * online * 100.0, 2)
    return 0.0


def _memory(stats: dict[str, Any]) -> tuple[int, int, float]:
    mem = stats.get("memory_stats") or {}
    usage = int(mem.get("usage") or 0)
    cache = int((mem.get("stats") or {}).get("cache") or 0)
    used = max(0, usage - cache)
    limit = int(mem.get("limit") or 0)
    pct = round((used / limit) * 100.0, 2) if limit else 0.0
    return used, limit, pct


def _parse_image(image: str) -> tuple[str, str, str]:
    image = image.split("@", 1)[0]
    last = image.rsplit("/", 1)[-1]
    tag = "latest"
    if ":" in last:
        image, tag = image.rsplit(":", 1)

    first = image.split("/", 1)[0]
    if "." in first or ":" in first or first == "localhost":
        registry, repo = image.split("/", 1)
    else:
        registry = "registry-1.docker.io"
        repo = image if "/" in image else f"library/{image}"

    return registry, repo, tag


async def _registry_digest(image: str) -> str | None:
    registry, repo, tag = _parse_image(image)
    url = f"https://{registry}/v2/{repo}/manifests/{tag}"
    headers = {
        "Accept": ", ".join(
            [
                "application/vnd.oci.image.index.v1+json",
                "application/vnd.oci.image.manifest.v1+json",
                "application/vnd.docker.distribution.manifest.list.v2+json",
                "application/vnd.docker.distribution.manifest.v2+json",
            ]
        )
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=UPDATE_TIMEOUT) as client:
        response = await client.head(url, headers=headers)
        if response.status_code == 401:
            challenge = response.headers.get("www-authenticate", "")
            if not challenge.lower().startswith("bearer "):
                return None
            params = dict(re.findall(r'(\w+)="([^"]+)"', challenge))
            realm = params.get("realm")
            if not realm:
                return None
            token_resp = await client.get(
                realm,
                params={k: v for k, v in {"service": params.get("service"), "scope": params.get("scope")}.items() if v},
            )
            token_resp.raise_for_status()
            payload = token_resp.json()
            token = payload.get("token") or payload.get("access_token")
            if not token:
                return None
            headers["Authorization"] = f"Bearer {token}"
            response = await client.head(url, headers=headers)
        response.raise_for_status()
        return response.headers.get("docker-content-digest")


async def _container_row(client: httpx.AsyncClient, container: dict[str, Any]) -> dict[str, Any]:
    cid = container["Id"]
    name = (container.get("Names") or [cid[:12]])[0].lstrip("/")
    inspect_resp, stats_resp = await asyncio.gather(
        client.get(f"/containers/{cid}/json"),
        client.get(f"/containers/{cid}/stats", params={"stream": "false"}),
    )
    inspect_resp.raise_for_status()
    stats_resp.raise_for_status()
    detail = inspect_resp.json()
    stats = stats_resp.json()
    used, limit, mem_pct = _memory(stats)
    state = detail.get("State") or {}
    image_name = (detail.get("Config") or {}).get("Image") or container.get("Image") or ""
    image_id = detail.get("Image") or ""
    local_digests: list[str] = []
    if image_id:
        image_resp = await client.get(f"/images/{image_id}/json")
        if image_resp.status_code == 200:
            local_digests = image_resp.json().get("RepoDigests") or []

    mounts = [
        {
            "source": m.get("Source"),
            "destination": m.get("Destination"),
            "mode": m.get("Mode"),
            "rw": m.get("RW"),
            "propagation": m.get("Propagation"),
        }
        for m in detail.get("Mounts") or []
    ]

    return {
        "id": cid[:12],
        "name": name,
        "image": image_name,
        "image_id": image_id.replace("sha256:", "")[:12],
        "local_digests": local_digests,
        "state": state.get("Status") or container.get("State"),
        "health": (state.get("Health") or {}).get("Status"),
        "started_at": state.get("StartedAt"),
        "restart_count": detail.get("RestartCount", 0),
        "cpu_percent": _cpu_percent(stats),
        "memory_used": used,
        "memory_limit": limit,
        "memory_percent": mem_pct,
        "network_rx": sum(int(v.get("rx_bytes") or 0) for v in (stats.get("networks") or {}).values()),
        "network_tx": sum(int(v.get("tx_bytes") or 0) for v in (stats.get("networks") or {}).values()),
        "mounts": mounts,
        "labels": (detail.get("Config") or {}).get("Labels") or {},
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    try:
        async with docker_client() as client:
            response = await client.get("/_ping")
            response.raise_for_status()
        return {"ok": True, "docker": True, "config_root": str(CONFIG_ROOT)}
    except Exception as exc:
        return {"ok": False, "docker": False, "error": str(exc), "config_root": str(CONFIG_ROOT)}


@app.get("/api/containers")
async def containers(all_containers: bool = Query(False, alias="all")) -> dict[str, Any]:
    filters = None if all_containers else {"label": [STACK_LABEL]}
    params = {"all": "true"}
    if filters:
        import json
        params["filters"] = json.dumps(filters)
    async with docker_client() as client:
        response = await client.get("/containers/json", params=params)
        response.raise_for_status()
        raw = response.json()
        rows = await asyncio.gather(*[_container_row(client, c) for c in raw])
    return {"ok": True, "services": sorted(rows, key=lambda row: row["name"])}


@app.get("/api/updates")
async def updates() -> dict[str, Any]:
    data = await containers(False)
    rows: list[dict[str, Any]] = []
    for service in data["services"]:
        image = service["image"]
        try:
            remote = await _registry_digest(image)
            local_matches = [d for d in service.get("local_digests") or [] if "@sha256:" in d]
            local_hashes = {d.rsplit("@", 1)[-1] for d in local_matches}
            rows.append(
                {
                    "name": service["name"],
                    "image": image,
                    "remote_digest": remote,
                    "local_digests": local_matches,
                    "update_available": bool(remote and local_hashes and remote not in local_hashes),
                    "checkable": remote is not None,
                }
            )
        except Exception as exc:
            rows.append({"name": service["name"], "image": image, "checkable": False, "error": str(exc), "update_available": False})
    return {"ok": True, "services": rows}


@app.get("/api/configs")
async def configs() -> dict[str, Any]:
    if not CONFIG_ROOT.exists():
        return {"ok": True, "root": str(CONFIG_ROOT), "files": []}
    allowed = {".yml", ".yaml", ".json", ".xml", ".conf", ".ini", ".toml", ".properties"}
    files: list[dict[str, Any]] = []
    for path in CONFIG_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in allowed:
            continue
        rel = path.relative_to(CONFIG_ROOT)
        try:
            size = path.stat().st_size
        except OSError:
            continue
        files.append({"path": str(rel), "size": size})
        if len(files) >= 1000:
            break
    return {"ok": True, "root": str(CONFIG_ROOT), "files": sorted(files, key=lambda x: x["path"])}


@app.get("/api/config")
async def config_file(path: str) -> dict[str, Any]:
    requested = (CONFIG_ROOT / path).resolve()
    root = CONFIG_ROOT.resolve()
    try:
        requested.relative_to(root)
    except ValueError as exc:
        raise HTTPException(400, "Path escapes config root") from exc
    if not requested.is_file():
        raise HTTPException(404, "Config file not found")
    if requested.stat().st_size > 512 * 1024:
        raise HTTPException(413, "Config file is too large to display")
    text = requested.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r'(?im)^([^#\n]*(?:api[_-]?key|token|password|secret)[^:=\n]*[:=]\s*)(.+)$', r'\1********', text)
    return {"ok": True, "path": path, "content": text}
