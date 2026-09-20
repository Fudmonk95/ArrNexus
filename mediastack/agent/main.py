from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request

DOCKER_SOCKET = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
CONFIG_ROOT = Path(os.getenv("CONFIG_ROOT", "/stack-config"))
STATE_ROOT = Path(os.getenv("STATE_ROOT", "/state"))
STACK_LABEL = os.getenv("STACK_LABEL", "arrnexus.mediastack=true")
WATCH_CONTAINERS = {
    item.strip()
    for item in os.getenv("WATCH_CONTAINERS", "").split(",")
    if item.strip()
}
UPDATE_TIMEOUT = float(os.getenv("UPDATE_TIMEOUT", "12"))
WRITE_REQUESTED = os.getenv("WRITE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
AGENT_TOKEN = os.getenv("AGENT_TOKEN", "").strip()
WRITE_ENABLED = WRITE_REQUESTED and bool(AGENT_TOKEN)
WRITE_ALLOWLIST = {
    item.strip()
    for item in os.getenv("WRITE_ALLOWLIST", "").split(",")
    if item.strip()
}
GHCR_USERNAME = os.getenv("GHCR_USERNAME", "").strip()
GHCR_TOKEN = os.getenv("GHCR_TOKEN", "").strip()
STABILIZATION_SECONDS = max(3, int(os.getenv("STABILIZATION_SECONDS", "10")))
HEALTH_TIMEOUT_SECONDS = max(20, int(os.getenv("HEALTH_TIMEOUT_SECONDS", "120")))

app = FastAPI(title="ArrNexus MediaStack Agent", version="1.0.0")

_JOBS: dict[str, dict[str, Any]] = {}
_JOB_LOCK = asyncio.Lock()
_SERVICE_LOCKS: dict[str, asyncio.Lock] = {}

_CONFIG_EXCLUDES = (
    "archive-layouts/",
    "archive_layouts/",
    "cache/",
    "logs/",
    "log/",
    "metadata/",
    "tmp/",
    "temp/",
)


_RESOURCE_RECOMMENDATIONS: dict[str, tuple[float, int]] = {
    "zurg": (4.0, 12288),
    "jellyfin": (4.0, 4096),
    "arrnexus": (2.0, 2048),
    "sonarr": (2.0, 2048),
    "radarr": (2.0, 2048),
    "lidarr": (1.5, 1536),
    "prowlarr": (1.5, 1024),
    "bazarr": (1.5, 1024),
    "whisparr": (1.5, 1536),
    "seerrng": (1.0, 1024),
    "lidarr-postgres": (1.0, 2048),
    "homarr": (0.5, 512),
    "maintainerr": (0.5, 512),
    "neutarr": (0.5, 512),
    "profilarr": (0.5, 512),
    "profilarr-parser": (0.5, 512),
}


def _ensure_state_root() -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        STATE_ROOT.chmod(0o700)
    except OSError:
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def docker_client(timeout: float = 15.0) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCKET)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://docker",
        timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
    )


def _config_roots() -> dict[str, Path]:
    """Return named read-only config roots.

    CONFIG_ROOTS format:
      zurg=/config-roots/zurg;sonarr=/config-roots/sonarr
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


def _adopted_names() -> set[str]:
    path = STATE_ROOT / "adopted.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(x) for x in data if str(x).strip()} if isinstance(data, list) else set()
    except Exception:
        return set()


def _save_adopted(names: set[str]) -> None:
    _ensure_state_root()
    path = STATE_ROOT / "adopted.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(names), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    path.chmod(0o600)


def _resource_policy_path() -> Path:
    return STATE_ROOT / "resource-policies.json"


def _resource_policies() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(_resource_policy_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for name, value in data.items():
            if not isinstance(value, dict):
                continue
            out[str(name)] = {
                "cpu_cores": float(value.get("cpu_cores") or 0),
                "memory_bytes": int(value.get("memory_bytes") or 0),
                "updated_at": str(value.get("updated_at") or ""),
            }
        return out
    except Exception:
        return {}


def _save_resource_policies(policies: dict[str, dict[str, Any]]) -> None:
    _ensure_state_root()
    path = _resource_policy_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(policies, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    path.chmod(0o600)


def _resource_recommendation(name: str) -> dict[str, Any]:
    cpu, memory_mib = _RESOURCE_RECOMMENDATIONS.get(name, (1.0, 1024))
    return {
        "cpu_cores": cpu,
        "memory_mib": memory_mib,
        "memory_bytes": memory_mib * 1024 * 1024,
    }


def _require_write(request: Request) -> None:
    if not WRITE_ENABLED:
        reason = "write actions are disabled"
        if WRITE_REQUESTED and not AGENT_TOKEN:
            reason = "write actions require AGENT_TOKEN"
        raise HTTPException(403, reason)
    supplied = request.headers.get("authorization", "")
    expected = f"Bearer {AGENT_TOKEN}"
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "invalid MediaStack agent token")


def _require_service_write(request: Request, name: str) -> None:
    _require_write(request)
    if WRITE_ALLOWLIST and name not in WRITE_ALLOWLIST:
        raise HTTPException(403, f"{name} is not in the MediaStack write allowlist")


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
    stat = mem.get("stats") or {}
    cache = int(stat.get("cache") or stat.get("inactive_file") or 0)
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
    auth: tuple[str, str] | None = None
    if registry == "ghcr.io" and GHCR_USERNAME and GHCR_TOKEN:
        auth = (GHCR_USERNAME, GHCR_TOKEN)
    async with httpx.AsyncClient(follow_redirects=True, timeout=UPDATE_TIMEOUT, auth=auth) as client:
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
                auth=auth,
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
    return labelled or name in WATCH_CONTAINERS or name in _adopted_names()


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
            "type": m.get("Type"),
            "name": m.get("Name"),
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

    networks = sorted(((detail.get("NetworkSettings") or {}).get("Networks") or {}).keys())
    labels = (detail.get("Config") or {}).get("Labels") or {}
    host_config = detail.get("HostConfig") or {}
    nano_cpus = int(host_config.get("NanoCpus") or 0)
    configured_memory = int(host_config.get("Memory") or 0)
    cpu_limit_cores = round(nano_cpus / 1_000_000_000, 3) if nano_cpus else 0.0
    policy = _resource_policies().get(name) or {}
    policy_cpu = float(policy.get("cpu_cores") or 0)
    policy_memory = int(policy.get("memory_bytes") or 0)
    policy_drift = bool(policy) and (
        abs(policy_cpu - cpu_limit_cores) > 0.001 or policy_memory != configured_memory
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
        "cpu_limit_cores": cpu_limit_cores,
        "memory_limit_configured": configured_memory,
        "resource_policy": policy,
        "resource_policy_drift": policy_drift,
        "resource_recommendation": _resource_recommendation(name),
        "network_rx": sum(int(v.get("rx_bytes") or 0) for v in (stats.get("networks") or {}).values()),
        "network_tx": sum(int(v.get("tx_bytes") or 0) for v in (stats.get("networks") or {}).values()),
        "mounts": mounts,
        "ports": ports,
        "networks": networks,
        "labels": labels,
        "compose_project": labels.get("com.docker.compose.project") or "",
        "compose_service": labels.get("com.docker.compose.service") or "",
        "managed": _is_managed(container),
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


async def _inspect(client: httpx.AsyncClient, cid: str) -> dict[str, Any]:
    response = await client.get(f"/containers/{cid}/json")
    response.raise_for_status()
    return response.json()


def _registry_auth_header(image: str) -> str | None:
    registry, _, _ = _parse_image(image)
    payload: dict[str, str] | None = None
    if registry == "ghcr.io" and GHCR_USERNAME and GHCR_TOKEN:
        payload = {
            "username": GHCR_USERNAME,
            "password": GHCR_TOKEN,
            "serveraddress": "ghcr.io",
        }
    if payload is None:
        return None
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    return encoded.rstrip("=")


async def _pull_image(client: httpx.AsyncClient, image: str) -> dict[str, Any]:
    registry, repo, tag = _parse_image(image)
    from_image = repo if registry == "registry-1.docker.io" else f"{registry}/{repo}"
    headers: dict[str, str] = {}
    auth_header = _registry_auth_header(image)
    if auth_header:
        headers["X-Registry-Auth"] = auth_header
    async with docker_client(timeout=600.0) as pull_client:
        response = await pull_client.post(
            "/images/create",
            params={"fromImage": from_image, "tag": tag},
            headers=headers,
        )
        response.raise_for_status()
        text = response.text
    last: dict[str, Any] = {}
    for line in text.splitlines():
        try:
            payload = json.loads(line)
        except Exception:
            continue
        last = payload
        if payload.get("error"):
            raise RuntimeError(str(payload.get("error")))
    image_resp = await client.get(f"/images/{image}/json")
    image_resp.raise_for_status()
    image_detail = image_resp.json()
    return {
        "image": image,
        "image_id": image_detail.get("Id"),
        "repo_digests": image_detail.get("RepoDigests") or [],
        "last_status": last.get("status") if isinstance(last, dict) else "",
    }


_CONFIG_KEYS = {
    "Hostname", "Domainname", "User", "AttachStdin", "AttachStdout", "AttachStderr",
    "ExposedPorts", "Tty", "OpenStdin", "StdinOnce", "Env", "Cmd", "Healthcheck",
    "ArgsEscaped", "Image", "Volumes", "WorkingDir", "Entrypoint", "NetworkDisabled",
    "MacAddress", "OnBuild", "Labels", "StopSignal", "StopTimeout", "Shell",
}

_HOST_KEYS = {
    "Binds", "ContainerIDFile", "LogConfig", "NetworkMode", "PortBindings",
    "RestartPolicy", "AutoRemove", "VolumeDriver", "VolumesFrom", "ConsoleSize",
    "CapAdd", "CapDrop", "CgroupnsMode", "Dns", "DnsOptions", "DnsSearch",
    "ExtraHosts", "GroupAdd", "IpcMode", "Cgroup", "Links", "OomScoreAdj",
    "PidMode", "Privileged", "PublishAllPorts", "ReadonlyRootfs", "SecurityOpt",
    "StorageOpt", "Tmpfs", "UTSMode", "UsernsMode", "ShmSize", "Sysctls",
    "Runtime", "Isolation", "CpuShares", "Memory", "NanoCpus", "CgroupParent",
    "BlkioWeight", "BlkioWeightDevice", "BlkioDeviceReadBps", "BlkioDeviceWriteBps",
    "BlkioDeviceReadIOps", "BlkioDeviceWriteIOps", "CpuPeriod", "CpuQuota",
    "CpuRealtimePeriod", "CpuRealtimeRuntime", "CpusetCpus", "CpusetMems",
    "Devices", "DeviceCgroupRules", "DeviceRequests", "MemoryReservation",
    "MemorySwap", "MemorySwappiness", "OomKillDisable", "PidsLimit", "Ulimits",
    "CpuCount", "CpuPercent", "IOMaximumIOps", "IOMaximumBandwidth", "MaskedPaths",
    "ReadonlyPaths", "Init", "Mounts",
}


def _clone_body(detail: dict[str, Any], image: str) -> tuple[dict[str, Any], dict[str, Any]]:
    source_config = detail.get("Config") or {}
    source_host = detail.get("HostConfig") or {}
    config = {key: source_config.get(key) for key in _CONFIG_KEYS if key in source_config}
    host = {key: source_host.get(key) for key in _HOST_KEYS if key in source_host}
    config["Image"] = image

    binds = list(host.get("Binds") or [])
    bound_targets = {
        str(entry).split(":", 2)[1]
        for entry in binds
        if isinstance(entry, str) and ":" in entry
    }
    for mount in detail.get("Mounts") or []:
        if mount.get("Type") != "volume" or not mount.get("Name") or not mount.get("Destination"):
            continue
        target = str(mount.get("Destination"))
        if target in bound_targets:
            continue
        mode = "rw" if bool(mount.get("RW", True)) else "ro"
        binds.append(f"{mount.get('Name')}:{target}:{mode}")
        bound_targets.add(target)
    if binds:
        host["Binds"] = binds

    labels = dict(config.get("Labels") or {})
    for label in list(labels):
        if label.startswith("com.docker.compose.") or label.startswith("io.portainer."):
            labels.pop(label, None)
    labels["arrnexus.mediastack"] = "true"
    labels["arrnexus.managed"] = "true"
    config["Labels"] = labels

    endpoints: dict[str, Any] = {}
    original_networks = ((detail.get("NetworkSettings") or {}).get("Networks") or {})
    for network_name, settings in original_networks.items():
        endpoint: dict[str, Any] = {}
        aliases = [
            alias
            for alias in (settings.get("Aliases") or [])
            if alias and alias not in {detail.get("Id"), detail.get("Name", "").lstrip("/")}
            and not str(alias).startswith(str(detail.get("Id") or "")[:12])
        ]
        if aliases:
            endpoint["Aliases"] = aliases
        if settings.get("IPAMConfig"):
            endpoint["IPAMConfig"] = settings.get("IPAMConfig")
        if settings.get("Links"):
            endpoint["Links"] = settings.get("Links")
        if settings.get("DriverOpts"):
            endpoint["DriverOpts"] = settings.get("DriverOpts")
        if settings.get("MacAddress"):
            endpoint["MacAddress"] = settings.get("MacAddress")
        if settings.get("GwPriority") is not None:
            endpoint["GwPriority"] = settings.get("GwPriority")
        endpoints[network_name] = endpoint

    body = dict(config)
    body["HostConfig"] = host
    if endpoints:
        body["NetworkingConfig"] = {"EndpointsConfig": endpoints}
    return body, original_networks


async def _disconnect_networks(client: httpx.AsyncClient, cid: str, networks: dict[str, Any]) -> None:
    for network_name in networks:
        response = await client.post(
            f"/networks/{network_name}/disconnect",
            json={"Container": cid, "Force": True},
        )
        if response.status_code not in {200, 204, 404}:
            response.raise_for_status()


async def _reconnect_networks(client: httpx.AsyncClient, cid: str, networks: dict[str, Any]) -> None:
    for network_name, settings in networks.items():
        endpoint: dict[str, Any] = {}
        aliases = [a for a in (settings.get("Aliases") or []) if a and not str(a).startswith(cid[:12])]
        if aliases:
            endpoint["Aliases"] = aliases
        if settings.get("IPAMConfig"):
            endpoint["IPAMConfig"] = settings.get("IPAMConfig")
        payload = {"Container": cid}
        if endpoint:
            payload["EndpointConfig"] = endpoint
        response = await client.post(f"/networks/{network_name}/connect", json=payload)
        if response.status_code not in {200, 201, 204, 403, 409}:
            response.raise_for_status()


async def _wait_ready(client: httpx.AsyncClient, cid: str, timeout: int = HEALTH_TIMEOUT_SECONDS) -> dict[str, Any]:
    started = time.monotonic()
    running_since: float | None = None
    last_state: dict[str, Any] = {}
    while time.monotonic() - started < timeout:
        detail = await _inspect(client, cid)
        state = detail.get("State") or {}
        last_state = state
        if not state.get("Running"):
            if state.get("Status") in {"exited", "dead"}:
                raise RuntimeError(f"container exited with code {state.get('ExitCode')}")
            await asyncio.sleep(2)
            continue

        if running_since is None:
            running_since = time.monotonic()

        health = (state.get("Health") or {}).get("Status")
        if health == "healthy":
            if time.monotonic() - running_since >= min(STABILIZATION_SECONDS, 5):
                return state
        elif health == "unhealthy":
            if time.monotonic() - running_since > max(15, STABILIZATION_SECONDS):
                raise RuntimeError("container healthcheck is unhealthy")
        elif not health and time.monotonic() - running_since >= STABILIZATION_SECONDS:
            return state
        await asyncio.sleep(2)
    raise RuntimeError(f"container did not stabilize within {timeout}s (last state={last_state.get('Status')})")


def _snapshot_path(job_id: str, name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", name)
    folder = STATE_ROOT / "snapshots" / job_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{safe}.inspect.json"


async def _job_update(job_id: str, **updates: Any) -> None:
    async with _JOB_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        job.update(updates)
        job["updated_at"] = _now()
        try:
            _ensure_state_root()
            jobs_dir = STATE_ROOT / "jobs"
            jobs_dir.mkdir(parents=True, exist_ok=True)
            (jobs_dir / f"{job_id}.json").write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass


async def _run_update(job_id: str, name: str) -> None:
    lock = _SERVICE_LOCKS.setdefault(name, asyncio.Lock())
    async with lock:
        old_id = ""
        backup_name = ""
        new_id = ""
        old_networks: dict[str, Any] = {}
        async with docker_client(timeout=60.0) as client:
            try:
                await _job_update(job_id, status="running", stage="inspect", message="Inspecting current container")
                row = await _find_managed(client, name)
                old_id = row["Id"]
                old_detail = await _inspect(client, old_id)
                image = str((old_detail.get("Config") or {}).get("Image") or "")
                if not image:
                    raise RuntimeError("container has no image reference")

                snapshot = _snapshot_path(job_id, name)
                snapshot.write_text(json.dumps(old_detail, indent=2) + "\n", encoding="utf-8")
                snapshot.chmod(0o600)
                old_image_id = str(old_detail.get("Image") or "")

                await _job_update(job_id, stage="pull", image=image, old_image_id=old_image_id, message=f"Pulling {image}")
                pulled = await _pull_image(client, image)
                new_image_id = str(pulled.get("image_id") or "")
                await _job_update(job_id, new_image_id=new_image_id, pulled=pulled)

                if new_image_id and old_image_id and new_image_id == old_image_id:
                    await _job_update(job_id, status="complete", stage="complete", changed=False, message="Already current")
                    return

                body, old_networks = _clone_body(old_detail, image)
                await _job_update(job_id, stage="stop", message=f"Stopping {name}")
                stop = await client.post(f"/containers/{old_id}/stop", params={"t": "30"})
                if stop.status_code not in {204, 304}:
                    stop.raise_for_status()

                backup_name = f"{name}-arrnexus-rollback-{int(time.time())}"
                rename = await client.post(f"/containers/{old_id}/rename", params={"name": backup_name})
                rename.raise_for_status()
                await _disconnect_networks(client, old_id, old_networks)

                await _job_update(job_id, stage="create", backup_container=backup_name, message="Creating replacement container")
                create = await client.post("/containers/create", params={"name": name}, json=body)
                if create.status_code >= 400:
                    raise RuntimeError(f"Docker create failed: {create.text[:800]}")
                new_id = str(create.json().get("Id") or "")
                if not new_id:
                    raise RuntimeError("Docker did not return a replacement container id")

                start = await client.post(f"/containers/{new_id}/start")
                if start.status_code not in {204, 304}:
                    start.raise_for_status()

                await _job_update(job_id, stage="health", new_container_id=new_id[:12], message="Waiting for replacement to stabilize")
                ready = await _wait_ready(client, new_id)

                remove_old = await client.delete(f"/containers/{old_id}", params={"v": "false", "force": "true"})
                if remove_old.status_code not in {204, 404}:
                    remove_old.raise_for_status()

                await _job_update(
                    job_id,
                    status="complete",
                    stage="complete",
                    changed=True,
                    health=(ready.get("Health") or {}).get("Status"),
                    message=f"{name} updated successfully",
                    finished_at=_now(),
                )
            except Exception as exc:
                await _job_update(job_id, status="rollback", stage="rollback", error=str(exc), message="Update failed; attempting rollback")
                rollback_error = ""
                try:
                    if new_id:
                        await client.delete(f"/containers/{new_id}", params={"v": "false", "force": "true"})
                    if old_id and backup_name:
                        rename = await client.post(f"/containers/{old_id}/rename", params={"name": name})
                        if rename.status_code >= 400 and rename.status_code != 409:
                            rename.raise_for_status()
                        await _reconnect_networks(client, old_id, old_networks)
                        start = await client.post(f"/containers/{old_id}/start")
                        if start.status_code not in {204, 304}:
                            start.raise_for_status()
                        await _wait_ready(client, old_id, timeout=min(HEALTH_TIMEOUT_SECONDS, 90))
                except Exception as rollback_exc:
                    rollback_error = str(rollback_exc)
                await _job_update(
                    job_id,
                    status="failed",
                    stage="failed",
                    rollback_ok=not bool(rollback_error),
                    rollback_error=rollback_error,
                    message="Update failed; rollback completed" if not rollback_error else "Update and rollback both need attention",
                    finished_at=_now(),
                )


async def _run_resource_update(job_id: str, name: str, cpu_cores: float, memory_bytes: int) -> None:
    lock = _SERVICE_LOCKS.setdefault(name, asyncio.Lock())
    async with lock:
        async with docker_client(timeout=60.0) as client:
            try:
                await _job_update(
                    job_id,
                    status="running",
                    stage="validate",
                    message=f"Validating resource limits for {name}",
                )
                row = await _find_managed(client, name)
                cid = row["Id"]
                detail = await _inspect(client, cid)
                host_config = detail.get("HostConfig") or {}

                info_resp = await client.get("/info")
                info_resp.raise_for_status()
                info = info_resp.json()
                host_cpus = int(info.get("NCPU") or 1)
                host_memory = int(info.get("MemTotal") or 0)

                if cpu_cores < 0:
                    raise RuntimeError("CPU limit cannot be negative")
                if cpu_cores and cpu_cores < 0.1:
                    raise RuntimeError("CPU limit must be at least 0.1 cores, or 0 for unlimited")
                if cpu_cores > host_cpus:
                    raise RuntimeError(f"CPU limit cannot exceed Docker host capacity ({host_cpus} cores)")
                if memory_bytes < 0:
                    raise RuntimeError("RAM limit cannot be negative")
                if memory_bytes and memory_bytes < 128 * 1024 * 1024:
                    raise RuntimeError("RAM limit must be at least 128 MiB, or 0 for unlimited")
                if host_memory and memory_bytes > host_memory:
                    raise RuntimeError("RAM limit cannot exceed Docker host memory")

                state = detail.get("State") or {}
                if memory_bytes and state.get("Running"):
                    stats_resp = await client.get(f"/containers/{cid}/stats", params={"stream": "false"})
                    if stats_resp.status_code == 200:
                        used, _, _ = _memory(stats_resp.json())
                        if used >= memory_bytes:
                            raise RuntimeError(
                                f"Requested RAM limit is below current usage ({round(used / 1024 / 1024)} MiB)"
                            )

                # Docker validates Memory and MemorySwap together during a live
                # container update. Always submit both values so moving from an
                # unlimited/default container to a finite RAM cap cannot be
                # rejected because of the container's previous swap setting.
                #
                # MemorySwap == Memory means no additional swap above the RAM
                # limit. When the user selects Unlimited, reset both to 0.
                update_body: dict[str, Any] = {
                    "NanoCpus": int(round(cpu_cores * 1_000_000_000)) if cpu_cores else 0,
                    "Memory": int(memory_bytes),
                    "MemorySwap": int(memory_bytes) if memory_bytes else 0,
                }

                await _job_update(
                    job_id,
                    stage="apply",
                    message=f"Applying CPU/RAM limits to {name}",
                    requested={"cpu_cores": cpu_cores, "memory_bytes": memory_bytes},
                )
                response = await client.post(f"/containers/{cid}/update", json=update_body)
                if response.status_code != 200:
                    raise RuntimeError(f"Docker resource update failed: {response.text[:800]}")

                policies = _resource_policies()
                policies[name] = {
                    "cpu_cores": cpu_cores,
                    "memory_bytes": memory_bytes,
                    "updated_at": _now(),
                }
                _save_resource_policies(policies)

                await _job_update(
                    job_id,
                    status="complete",
                    stage="complete",
                    message=f"{name} resource limits applied",
                    cpu_cores=cpu_cores,
                    memory_bytes=memory_bytes,
                    finished_at=_now(),
                )
            except Exception as exc:
                await _job_update(
                    job_id,
                    status="failed",
                    stage="failed",
                    error=str(exc),
                    message=f"{name} resource limit change failed",
                    finished_at=_now(),
                )


async def _run_lifecycle(job_id: str, name: str, action: str) -> None:
    lock = _SERVICE_LOCKS.setdefault(name, asyncio.Lock())
    async with lock:
        async with docker_client(timeout=120.0) as client:
            try:
                await _job_update(
                    job_id,
                    status="running",
                    stage=action,
                    message=f"{action.title()}ing {name}",
                )
                row = await _find_managed(client, name)
                cid = row["Id"]

                if action == "start":
                    response = await client.post(f"/containers/{cid}/start")
                    allowed = {204, 304}
                elif action == "stop":
                    response = await client.post(f"/containers/{cid}/stop", params={"t": "30"})
                    allowed = {204, 304}
                elif action == "restart":
                    response = await client.post(f"/containers/{cid}/restart", params={"t": "30"})
                    allowed = {204}
                else:
                    raise RuntimeError(f"unsupported lifecycle action: {action}")

                if response.status_code not in allowed:
                    response.raise_for_status()

                if action in {"start", "restart"}:
                    await _job_update(
                        job_id,
                        stage="stabilizing",
                        message=f"Waiting for {name} to stabilize",
                    )
                    ready = await _wait_ready(client, cid)
                    health = (ready.get("Health") or {}).get("Status")
                else:
                    health = None

                await _job_update(
                    job_id,
                    status="complete",
                    stage="complete",
                    health=health,
                    message=f"{name} {action} completed",
                    finished_at=_now(),
                )
            except Exception as exc:
                await _job_update(
                    job_id,
                    status="failed",
                    stage="failed",
                    error=str(exc),
                    message=f"{name} {action} failed",
                    finished_at=_now(),
                )


@app.get("/health")
async def health() -> dict[str, Any]:
    try:
        async with docker_client() as client:
            response = await client.get("/_ping")
            response.raise_for_status()
        return {
            "ok": True,
            "docker": True,
            "version": app.version,
            "write_requested": WRITE_REQUESTED,
            "write_enabled": WRITE_ENABLED,
            "write_reason": "enabled" if WRITE_ENABLED else ("missing AGENT_TOKEN" if WRITE_REQUESTED and not AGENT_TOKEN else "disabled"),
            "config_roots": {name: str(path) for name, path in _config_roots().items()},
            "watch_containers": sorted(WATCH_CONTAINERS),
            "adopted_containers": sorted(_adopted_names()),
            "write_allowlist": sorted(WRITE_ALLOWLIST),
        }
    except Exception as exc:
        return {"ok": False, "docker": False, "error": str(exc)}


@app.get("/api/capabilities")
async def capabilities() -> dict[str, Any]:
    return {
        "ok": True,
        "read": True,
        "write_requested": WRITE_REQUESTED,
        "write_enabled": WRITE_ENABLED,
        "write_allowlist": sorted(WRITE_ALLOWLIST),
        "actions": {
            "start": WRITE_ENABLED,
            "stop": WRITE_ENABLED,
            "restart": WRITE_ENABLED,
            "update": WRITE_ENABLED,
            "resources": WRITE_ENABLED,
            "adopt": WRITE_ENABLED,
        },
        "safe_update": {
            "pull_before_stop": True,
            "inspect_snapshot": True,
            "replacement_health_check": True,
            "automatic_container_rollback": True,
            "volumes_removed": False,
        },
    }


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
    async with docker_client(timeout=20.0) as client:
        raw = await _raw_containers(client, True)
        if not all_containers:
            raw = [row for row in raw if _is_managed(row)]
        rows = await asyncio.gather(*[_container_row(client, c) for c in raw])
    return {"ok": True, "services": sorted(rows, key=lambda row: row["name"])}


@app.get("/api/discovery")
async def discovery() -> dict[str, Any]:
    data = await containers(True)
    managed = [row for row in data["services"] if row.get("managed")]
    unmanaged = [row for row in data["services"] if not row.get("managed")]
    projects: dict[str, list[str]] = {}
    for row in data["services"]:
        project = str(row.get("compose_project") or "standalone")
        projects.setdefault(project, []).append(str(row.get("name") or ""))
    return {
        "ok": True,
        "managed": managed,
        "unmanaged": unmanaged,
        "projects": {key: sorted(value) for key, value in sorted(projects.items())},
    }


@app.get("/api/updates")
async def updates() -> dict[str, Any]:
    data = await containers(False)
    semaphore = asyncio.Semaphore(4)

    async def check_service(service: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            image = service["image"]
            try:
                remote = await _registry_digest(image)
                local_matches = [d for d in service.get("local_digests") or [] if "@sha256:" in d]
                local_hashes = {d.rsplit("@", 1)[-1] for d in local_matches}
                return {
                    "name": service["name"],
                    "image": image,
                    "remote_digest": remote,
                    "local_digests": local_matches,
                    "update_available": bool(remote and local_hashes and remote not in local_hashes),
                    "checkable": remote is not None,
                }
            except Exception as exc:
                return {
                    "name": service["name"],
                    "image": image,
                    "checkable": False,
                    "error": str(exc),
                    "update_available": False,
                }

    rows = await asyncio.gather(*(check_service(service) for service in data["services"]))
    return {"ok": True, "services": rows}


@app.get("/api/update-plan/{name}")
async def update_plan(name: str) -> dict[str, Any]:
    async with docker_client() as client:
        row = await _find_managed(client, name)
        detail = await _inspect(client, row["Id"])
        image = str((detail.get("Config") or {}).get("Image") or "")
        current_image_id = str(detail.get("Image") or "")
        remote_digest = None
        registry_error = ""
        try:
            remote_digest = await _registry_digest(image)
        except Exception as exc:
            registry_error = str(exc)
        return {
            "ok": True,
            "name": _container_name(row),
            "image": image,
            "current_image_id": current_image_id,
            "remote_digest": remote_digest,
            "registry_error": registry_error,
            "running": bool((detail.get("State") or {}).get("Running")),
            "health": ((detail.get("State") or {}).get("Health") or {}).get("Status"),
            "restart_count": int(detail.get("RestartCount") or 0),
            "mounts": detail.get("Mounts") or [],
            "networks": sorted(((detail.get("NetworkSettings") or {}).get("Networks") or {}).keys()),
            "write_enabled": WRITE_ENABLED,
            "steps": [
                "snapshot Docker inspect data",
                "pull replacement image before stopping the service",
                "stop and retain old container as rollback candidate",
                "create replacement with the same ports, mounts, limits, devices and networks",
                "require replacement to stabilize",
                "remove rollback container only after success",
            ],
        }


@app.post("/api/adopt")
async def adopt(request: Request) -> dict[str, Any]:
    _require_write(request)
    payload = await request.json()
    names = payload.get("names") or []
    if not isinstance(names, list):
        raise HTTPException(400, "names must be a list")
    async with docker_client() as client:
        all_rows = await _raw_containers(client, True)
        existing = {_container_name(row) for row in all_rows}
    selected = {str(name).strip() for name in names if str(name).strip() in existing}
    if not selected:
        raise HTTPException(400, "no matching containers were supplied")
    adopted = _adopted_names() | selected
    _save_adopted(adopted)
    return {"ok": True, "adopted": sorted(adopted)}


@app.get("/api/resources/{name}")
async def resource_state(name: str) -> dict[str, Any]:
    async with docker_client(timeout=20.0) as client:
        row = await _find_managed(client, name)
        service = await _container_row(client, row)
        info_resp = await client.get("/info")
        info_resp.raise_for_status()
        info = info_resp.json()
    return {
        "ok": True,
        "name": service["name"],
        "cpu_percent": service.get("cpu_percent") or 0,
        "memory_used": service.get("memory_used") or 0,
        "cpu_limit_cores": service.get("cpu_limit_cores") or 0,
        "memory_limit_bytes": service.get("memory_limit_configured") or 0,
        "policy": service.get("resource_policy") or {},
        "policy_drift": bool(service.get("resource_policy_drift")),
        "recommendation": service.get("resource_recommendation") or {},
        "host_cpus": int(info.get("NCPU") or 0),
        "host_memory": int(info.get("MemTotal") or 0),
        "write_enabled": WRITE_ENABLED,
    }


@app.post("/api/resources/{name}")
async def resource_update(name: str, request: Request) -> dict[str, Any]:
    _require_write(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(400, "JSON object required")

    try:
        cpu_cores = float(payload.get("cpu_cores") or 0)
        memory_mib = float(payload.get("memory_mib") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "cpu_cores and memory_mib must be numeric") from exc

    if memory_mib < 0:
        raise HTTPException(400, "memory_mib cannot be negative")
    memory_bytes = int(round(memory_mib * 1024 * 1024))

    async with docker_client() as client:
        row = await _find_managed(client, name)
        canonical = _container_name(row)

    lock = _SERVICE_LOCKS.setdefault(canonical, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, f"{canonical} already has an active management action")

    job_id = secrets.token_hex(10)
    async with _JOB_LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "type": "resources",
            "name": canonical,
            "status": "queued",
            "stage": "queued",
            "message": "Resource change queued",
            "requested": {"cpu_cores": cpu_cores, "memory_bytes": memory_bytes},
            "created_at": _now(),
            "updated_at": _now(),
        }

    asyncio.create_task(
        _run_resource_update(job_id, canonical, cpu_cores, memory_bytes),
        name=f"mediastack-resources-{canonical}-{job_id[:6]}",
    )
    return {
        "ok": True,
        "job_id": job_id,
        "status": "queued",
        "name": canonical,
        "cpu_cores": cpu_cores,
        "memory_bytes": memory_bytes,
    }


@app.post("/api/lifecycle/{name}/{action}")
async def lifecycle_action(name: str, action: str, request: Request) -> dict[str, Any]:
    _require_service_write(request, name)
    action = action.lower()
    if action not in {"start", "stop", "restart"}:
        raise HTTPException(400, "unsupported lifecycle action")

    async with docker_client() as client:
        row = await _find_managed(client, name)
        canonical = _container_name(row)

    lock = _SERVICE_LOCKS.setdefault(canonical, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, f"{canonical} already has an active management action")

    job_id = secrets.token_hex(10)
    async with _JOB_LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "type": action,
            "name": canonical,
            "status": "queued",
            "stage": "queued",
            "message": f"{action.title()} queued",
            "created_at": _now(),
            "updated_at": _now(),
        }

    asyncio.create_task(
        _run_lifecycle(job_id, canonical, action),
        name=f"mediastack-{action}-{canonical}-{job_id[:6]}",
    )
    return {"ok": True, "job_id": job_id, "status": "queued", "name": canonical, "action": action}


@app.post("/api/actions/{name}/update")
async def start_update(name: str, request: Request) -> dict[str, Any]:
    _require_service_write(request, name)
    async with docker_client() as client:
        row = await _find_managed(client, name)
        canonical = _container_name(row)

    lock = _SERVICE_LOCKS.setdefault(canonical, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, f"{canonical} already has an active management action")

    job_id = secrets.token_hex(10)
    async with _JOB_LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "type": "update",
            "name": canonical,
            "status": "queued",
            "stage": "queued",
            "message": "Queued",
            "created_at": _now(),
            "updated_at": _now(),
        }
    asyncio.create_task(_run_update(job_id, canonical), name=f"mediastack-update-{canonical}-{job_id[:6]}")
    return {"ok": True, "job_id": job_id, "status": "queued", "name": canonical}


@app.get("/api/jobs")
async def jobs() -> dict[str, Any]:
    async with _JOB_LOCK:
        rows = sorted(_JOBS.values(), key=lambda x: x.get("created_at") or "", reverse=True)
    return {"ok": True, "jobs": rows[:100]}


@app.get("/api/jobs/{job_id}")
async def job(job_id: str) -> dict[str, Any]:
    async with _JOB_LOCK:
        current = _JOBS.get(job_id)
    if current:
        return {"ok": True, "job": current}
    path = STATE_ROOT / "jobs" / f"{job_id}.json"
    if path.is_file():
        try:
            return {"ok": True, "job": json.loads(path.read_text(encoding="utf-8"))}
        except Exception:
            pass
    raise HTTPException(404, "job not found")


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


def _config_ignored(root_name: str, rel: Path) -> bool:
    normalized = str(rel).replace("\\", "/").lower().lstrip("/")
    if any(normalized.startswith(prefix) for prefix in _CONFIG_EXCLUDES):
        return True
    if root_name == "zurg" and "archive-layouts/" in normalized:
        return True
    return False


def _config_candidate_score(root_name: str, rel: Path) -> tuple[int, int, str]:
    """Rank a service's most useful human-editable configuration file.

    MediaStack should present one service card, not hundreds of generated
    XML/JSON/YAML files.  Lower scores win; path depth is used as the second
    tiebreaker so top-level configs are preferred.
    """
    normalized = str(rel).replace("\\", "/").lower()
    base = rel.name.lower()
    preferred: dict[str, tuple[str, ...]] = {
        "zurg": ("config.yml", "config.yaml"),
        "sonarr": ("config.xml",),
        "radarr": ("config.xml",),
        "lidarr": ("config.xml",),
        "prowlarr": ("config.xml",),
        "whisparr": ("config.xml",),
        "jellyfin": ("system.xml", "network.xml", "encoding.xml"),
        "seerrng": ("settings.json", "config.json"),
        "bazarr": ("config.yaml", "config.yml", "config.ini"),
        "neutarr": ("config.json", "settings.json", "config.yml", "config.yaml"),
        "maintainerr": ("settings.json", "config.json"),
        "profilarr": ("config.yml", "config.yaml", "settings.json"),
        "homarr": ("settings.json", "config.json"),
        "arrnexus": ("settings.json", "config.json"),
    }
    wanted = preferred.get(root_name, ())
    if base in wanted:
        return (wanted.index(base), len(rel.parts), normalized)
    generic = (
        "config.yml",
        "config.yaml",
        "config.json",
        "config.xml",
        "settings.json",
        "settings.yml",
        "settings.yaml",
        "settings.xml",
        "application.yml",
        "application.yaml",
        "application.json",
    )
    if base in generic:
        return (20 + generic.index(base), len(rel.parts), normalized)
    return (100, len(rel.parts), normalized)


def _primary_config(root_name: str, root: Path) -> dict[str, Any]:
    allowed = {".yml", ".yaml", ".json", ".xml", ".conf", ".ini", ".toml", ".properties"}
    candidates: list[tuple[tuple[int, int, str], Path, int]] = []
    file_count = 0
    if root.exists():
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in allowed:
                continue
            try:
                size = path.stat().st_size
                rel = path.relative_to(root)
            except (OSError, ValueError):
                continue
            if _config_ignored(root_name, rel):
                continue
            file_count += 1
            # Avoid choosing giant generated files as the primary config.
            if size <= 512 * 1024:
                candidates.append((_config_candidate_score(root_name, rel), rel, size))
            if file_count >= 500:
                break

    if not candidates:
        return {
            "root": root_name,
            "path": "",
            "size": 0,
            "available": False,
            "file_count": file_count,
        }

    _, rel, size = min(candidates, key=lambda item: item[0])
    return {
        "root": root_name,
        "path": str(rel),
        "size": size,
        "available": True,
        "file_count": file_count,
    }


@app.get("/api/configs")
async def configs() -> dict[str, Any]:
    roots = _config_roots()

    # One row per managed service.  Services without a readable plaintext
    # config still get a card so the Configuration section matches the service
    # inventory rather than the number of files found on disk.
    service_names = sorted(WATCH_CONTAINERS | set(roots.keys()) | _adopted_names())
    service_names = [name for name in service_names if name != "arrnexus-stack-agent"]

    files: list[dict[str, Any]] = []
    for name in service_names:
        root = roots.get(name)
        if root is None:
            files.append({
                "root": name,
                "path": "",
                "size": 0,
                "available": False,
                "file_count": 0,
            })
            continue
        files.append(_primary_config(name, root))

    return {
        "ok": True,
        "roots": {name: str(path) for name, path in roots.items()},
        "files": files,
        "service_count": len(files),
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
