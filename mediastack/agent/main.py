from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query

DOCKER_SOCKET = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
CONFIG_ROOT = Path(os.getenv("CONFIG_ROOT", "/stack-config"))
STACK_LABEL = os.getenv("STACK_LABEL", "arrnexus.mediastack=true")
WATCH_CONTAINERS = {
    item.strip()
    for item in os.getenv("WATCH_CONTAINERS", "").split(",")
    if item.strip()
}
UPDATE_TIMEOUT = float(os.getenv("UPDATE_TIMEOUT", "8"))

app = FastAPI(title="ArrNexus MediaStack Agent", version="0.2.0")


def docker_client() -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCKET)
    return httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=10.0)


def _config_roots() -> dict[str, Path]:
    """Return named read-only config roots.

    CONFIG_ROOTS format:
      zurg=/config-roots/zurg;sonarr=/config-roots/sonarr

    The normal consolidated MediaStack uses CONFIG_ROOT=/stack-config instead.
    """
    raw = os.getenv("CONFIG_ROOTS", "").strip()
    roots: dict[str, Path] = {}
    if raw:
        for item in raw.split(";"):
            if "=" not in item:
                continue
            name, value = item.split("=", 1)
            name = name.strip()
            value = value.strip()
            if name and value:
                roots[name] = Path(value)
    if not roots:
        roots["mediastack"] = CONFIG_ROOT
    return roots


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


def _container_name(container: dict[str, Any]) -> str:
    return (container.get("Names") or [container.get("Id", "")[:12]])[0].lstrip("/")


def _is_managed(container: dict[str, Any]) -> bool:
    name = _container_name(container)
    labels = container.get("Labels") or {}
    label_key, _, label_value = STACK_LABEL.partition("=")
    labelled = label_key in labels and (not label_value or str(labels.get(label_key)) == label_value)
    return labelled or name in WATCH_CONTAINERS


async def _container_row(client: httpx.AsyncClient, container: dict[str, Any]) -> dict[str, Any]:
    cid = container["Id"]
    name = _container_name(container)
    inspect_resp = await client.get(f"/containers/{cid}/json")
    inspect_resp.raise_for_status()
    detail = inspect_resp.json()
    state = detail.get("State") or {}

    stats: dict[str, Any] = {}
    if state.get("Running"):
        stats_resp = await client.get(f"/containers/{cid}/stats", params={"stream": "false"})
        if stats_resp.status_code == 200:
            stats = stats_resp.json()

    used, limit, mem_pct = _memory(stats)
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

    ports: list[dict[str, Any]] = []
    for internal, bindings in ((detail.get("NetworkSettings") or {}).get("Ports") or {}).items():
        for binding in bindings or []:
            ports.append(
                {
                    "container": internal,
                    "host_ip": binding.get("HostIp") or "",
                    "host_port": binding.get("HostPort") or "",
                }
            )

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
        "ports": ports,
        "labels": (detail.get("Config") or {}).get("Labels") or {},
    }


async def _raw_containers(client: httpx.AsyncClient, include_all: bool = True) -> list[dict[str, Any]]:
    response = await client.get("/containers/json", params={"all": "true" if include_all else "false"})
    response.raise_for_status()
    return response.json()


async def _managed_containers(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    return [row for row in await _raw_containers(client, True) if _is_managed(row)]


async def _find_managed(client: httpx.AsyncClient, name: str) -> dict[str, Any]:
    for row in await _managed_containers(client):
        if _container_name(row) == name or row.get("Id", "").startswith(name):
            return row
    raise HTTPException(404, "Managed container not found")


@app.get("/health")
async def health() -> dict[str, Any]:
    try:
        async with docker_client() as client:
            response = await client.get("/_ping")
            response.raise_for_status()
        return {
            "ok": True,
            "docker": True,
            "config_roots": {name: str(path) for name, path in _config_roots().items()},
            "watch_containers": sorted(WATCH_CONTAINERS),
        }
    except Exception as exc:
        return {"ok": False, "docker": False, "error": str(exc)}


@app.get("/api/system")
async def system_info() -> dict[str, Any]:
    async with docker_client() as client:
        response = await client.get("/info")
        response.raise_for_status()
        info = response.json()
    return {
        "ok": True,
        "docker_version": info.get("ServerVersion"),
        "driver": info.get("Driver"),
        "cpus": info.get("NCPU"),
        "memory_total": info.get("MemTotal"),
        "containers": info.get("Containers"),
        "containers_running": info.get("ContainersRunning"),
        "containers_stopped": info.get("ContainersStopped"),
        "operating_system": info.get("OperatingSystem"),
        "architecture": info.get("Architecture"),
    }


@app.get("/api/containers")
async def containers(all_containers: bool = Query(False, alias="all")) -> dict[str, Any]:
    async with docker_client() as client:
        raw = await _raw_containers(client, True)
        if not all_containers:
            raw = [row for row in raw if _is_managed(row)]
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
            rows.append(
                {
                    "name": service["name"],
                    "image": image,
                    "checkable": False,
                    "error": str(exc),
                    "update_available": False,
                }
            )
    return {"ok": True, "services": rows}


def _redact(text: str) -> str:
    keys = r"api[_-]?key|token|password|passwd|secret|username|user_name"
    text = re.sub(
        rf'(?im)^([^#\n]*(?:{keys})[^:=\n]*[:=]\s*)(.+)$',
        r'\1********',
        text,
    )
    text = re.sub(
        rf'(?is)(<\s*(?:{keys})\s*>)(.*?)(<\s*/\s*(?:{keys})\s*>)',
        r'\1********\3',
        text,
    )
    return text


@app.get("/api/configs")
async def configs() -> dict[str, Any]:
    allowed = {".yml", ".yaml", ".json", ".xml", ".conf", ".ini", ".toml", ".properties"}
    files: list[dict[str, Any]] = []
    roots = _config_roots()
    for root_name, root in roots.items():
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in allowed:
                continue
            try:
                size = path.stat().st_size
                rel = path.relative_to(root)
            except (OSError, ValueError):
                continue
            files.append({"root": root_name, "path": str(rel), "size": size})
            if len(files) >= 2000:
                break
        if len(files) >= 2000:
            break
    return {
        "ok": True,
        "roots": {name: str(path) for name, path in roots.items()},
        "files": sorted(files, key=lambda x: (x["root"], x["path"])),
    }


@app.get("/api/config")
async def config_file(root: str, path: str) -> dict[str, Any]:
    roots = _config_roots()
    base = roots.get(root)
    if base is None:
        raise HTTPException(404, "Config root not found")
    base = base.resolve()
    requested = (base / path).resolve()
    try:
        requested.relative_to(base)
    except ValueError as exc:
        raise HTTPException(400, "Path escapes config root") from exc
    if not requested.is_file():
        raise HTTPException(404, "Config file not found")
    if requested.stat().st_size > 512 * 1024:
        raise HTTPException(413, "Config file is too large to display")
    text = requested.read_text(encoding="utf-8", errors="replace")
    return {"ok": True, "root": root, "path": path, "content": _redact(text)}


def _demux_logs(data: bytes) -> str:
    if not data:
        return ""
    out: list[bytes] = []
    index = 0
    valid = True
    while index + 8 <= len(data):
        size = int.from_bytes(data[index + 4:index + 8], "big")
        if size < 0 or index + 8 + size > len(data):
            valid = False
            break
        out.append(data[index + 8:index + 8 + size])
        index += 8 + size
    if valid and index == len(data) and out:
        return b"".join(out).decode("utf-8", errors="replace")
    return data.decode("utf-8", errors="replace")


@app.get("/api/logs/{name}")
async def container_logs(name: str, tail: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    async with docker_client() as client:
        row = await _find_managed(client, name)
        response = await client.get(
            f"/containers/{row['Id']}/logs",
            params={"stdout": "1", "stderr": "1", "timestamps": "1", "tail": str(tail)},
        )
        response.raise_for_status()
    return {"ok": True, "name": _container_name(row), "tail": tail, "content": _demux_logs(response.content)}
