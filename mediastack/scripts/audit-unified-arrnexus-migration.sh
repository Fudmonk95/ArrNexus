#!/usr/bin/env bash
set -euo pipefail

OUT="${1:-/home/renegademonk/arrnexus-unified-migration-audit.txt}"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

for cmd in docker python3; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Required command is missing: $cmd" >&2; exit 1; }
done

python3 - "$OUT" <<'PY'
import json, subprocess, sys, datetime
from pathlib import Path

out = Path(sys.argv[1])

def sh(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()

def docker_json(*args):
    raw = subprocess.check_output(["docker", *args], text=True)
    return json.loads(raw)

def inspect(name):
    return docker_json("inspect", name)[0]

def health(obj):
    state = obj.get("State") or {}
    h = (state.get("Health") or {}).get("Status")
    return h or state.get("Status") or "unknown"

def env_names(obj):
    rows = []
    for item in ((obj.get("Config") or {}).get("Env") or []):
        rows.append(item.split("=",1)[0])
    return sorted(set(rows))

def container_ports(obj):
    bindings = ((obj.get("HostConfig") or {}).get("PortBindings") or {})
    rows=[]
    for target, hosts in sorted(bindings.items()):
        hosts = hosts or []
        if not hosts:
            rows.append(f"{target}=<none>")
            continue
        for h in hosts:
            rows.append(f"{h.get('HostIp') or '*'}:{h.get('HostPort') or '?'}->{target}")
    return rows

def networks(obj):
    return sorted((((obj.get("NetworkSettings") or {}).get("Networks") or {}).keys()))

def mounts(obj):
    rows=[]
    for m in obj.get("Mounts") or []:
        rows.append({
            "type": m.get("Type"),
            "source": m.get("Source"),
            "destination": m.get("Destination"),
            "rw": m.get("RW"),
            "propagation": m.get("Propagation"),
            "name": m.get("Name"),
        })
    return rows

ids = sh("docker","ps","-aq").splitlines()
containers=[]
for cid in ids:
    if not cid: continue
    obj=inspect(cid)
    labels=(obj.get("Config") or {}).get("Labels") or {}
    name=(obj.get("Name") or "").lstrip("/")
    containers.append({
        "name": name,
        "id": (obj.get("Id") or "")[:12],
        "image": (obj.get("Config") or {}).get("Image"),
        "image_id": obj.get("Image"),
        "status": (obj.get("State") or {}).get("Status"),
        "health": health(obj),
        "restart_policy": ((obj.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name"),
        "user": (obj.get("Config") or {}).get("User") or "",
        "compose_project": labels.get("com.docker.compose.project") or "",
        "compose_service": labels.get("com.docker.compose.service") or "",
        "compose_config_files": labels.get("com.docker.compose.project.config_files") or "",
        "arrnexus_managed": labels.get("arrnexus.managed") or "",
        "arrnexus_mediastack": labels.get("arrnexus.mediastack") or "",
        "ports": container_ports(obj),
        "networks": networks(obj),
        "mounts": mounts(obj),
        "devices": (obj.get("HostConfig") or {}).get("Devices") or [],
        "group_add": (obj.get("HostConfig") or {}).get("GroupAdd") or [],
        "cap_add": (obj.get("HostConfig") or {}).get("CapAdd") or [],
        "security_opt": (obj.get("HostConfig") or {}).get("SecurityOpt") or [],
        "runtime": (obj.get("HostConfig") or {}).get("Runtime") or "",
        "env_names": env_names(obj),
    })

projects={}
for c in containers:
    p=c["compose_project"] or "<standalone>"
    projects.setdefault(p,[]).append(c["name"])

candidate_names = {
    "arrnexus","arrnexus-mediastack-ui-test","arrnexus-stack-agent","zurg","sonarr","radarr","lidarr","lidarr-postgres",
    "prowlarr","seerrng","jellyfin","whisparr","bazarr","neutarr","maintainerr","profilarr","profilarr-parser","homarr",
    "ersatztv","glances"
}

lines=[]
lines += [
    "ARRNEXUS UNIFIED MIGRATION AUDIT",
    "================================",
    f"Generated: {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
    "",
    "Goal: one Portainer stack named arrnexus, with ArrNexus owning/managing separate service containers.",
    "This audit is read-only. Environment VALUES are intentionally omitted; only variable names are listed.",
    "",
    "COMPOSE / STACK OWNERSHIP",
    "-------------------------",
]
for project,names in sorted(projects.items()):
    lines.append(f"{project}: {', '.join(sorted(names))}")

lines += ["", "CONTAINER SUMMARY", "-----------------"]
for c in sorted(containers,key=lambda x:x["name"]):
    marker=" *candidate*" if c["name"] in candidate_names else ""
    lines.append(f"{c['name']}{marker}")
    lines.append(f"  image: {c['image']}")
    lines.append(f"  status/health: {c['status']} / {c['health']}")
    lines.append(f"  project/service: {c['compose_project'] or '<standalone>'} / {c['compose_service'] or '-'}")
    if c["compose_config_files"]: lines.append(f"  compose file: {c['compose_config_files']}")
    lines.append(f"  arrnexus labels: managed={c['arrnexus_managed'] or '-'} mediastack={c['arrnexus_mediastack'] or '-'}")
    lines.append(f"  ports: {', '.join(c['ports']) if c['ports'] else '-'}")
    lines.append(f"  networks: {', '.join(c['networks']) if c['networks'] else '-'}")

lines += ["", "DETAILED CANDIDATE RUNTIME", "--------------------------"]
for c in sorted(containers,key=lambda x:x["name"]):
    if c["name"] not in candidate_names: continue
    lines.append("")
    lines.append(f"[{c['name']}]")
    lines.append(f"image={c['image']}")
    lines.append(f"image_id={c['image_id']}")
    lines.append(f"status={c['status']} health={c['health']}")
    lines.append(f"restart_policy={c['restart_policy']} user={c['user'] or '<default>'} runtime={c['runtime'] or '<default>'}")
    lines.append(f"project={c['compose_project'] or '<standalone>'} service={c['compose_service'] or '-'}")
    lines.append(f"ports={json.dumps(c['ports'])}")
    lines.append(f"networks={json.dumps(c['networks'])}")
    lines.append(f"devices={json.dumps(c['devices'], sort_keys=True)}")
    lines.append(f"group_add={json.dumps(c['group_add'])}")
    lines.append(f"cap_add={json.dumps(c['cap_add'])}")
    lines.append(f"security_opt={json.dumps(c['security_opt'])}")
    lines.append(f"env_names={json.dumps(c['env_names'])}")
    lines.append("mounts=")
    for m in c["mounts"]:
        lines.append("  " + json.dumps(m, sort_keys=True))

lines += [
    "",
    "MIGRATION NOTES",
    "---------------",
    "- Do NOT delete Portainer stacks yet.",
    "- Do NOT move or rename config directories during ownership migration.",
    "- The first unified ArrNexus stack should preserve every current bind mount, port, network, device, UID/GID, capability and security option.",
    "- After each service is proven under ArrNexus ownership, its old Portainer stack definition can be retired.",
    "- arrnexus-mediastack-ui-test is temporary and should disappear after the new ArrNexus becomes production.",
    "- arrnexus-stack-agent remains a separate helper container even though it will belong to the ArrNexus stack.",
    "- A literal one-container design would require running all services as processes inside ArrNexus and is intentionally NOT the target of this audit.",
]

out.write_text("\n".join(lines)+"\n", encoding="utf-8")
print(out)
PY

chmod 600 "$OUT"
echo "Unified migration audit written to: $OUT"
echo "No containers, stacks, networks, volumes or files were changed."
