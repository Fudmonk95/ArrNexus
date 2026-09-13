#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/arrnexus-mediastack-src}"
BRANCH="feature/mediastack-v1-control-plane"

[[ -d "$REPO_DIR/.git" ]] || { echo "ArrNexus checkout not found at $REPO_DIR" >&2; exit 2; }
cd "$REPO_DIR"

git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

# First wire the routes/lifespan/onboarding into the recovered v13.4 main.py.
bash mediastack/scripts/integrate-control-plane-v1.sh

cd "$REPO_DIR"
git pull --ff-only origin "$BRANCH"

python3 - <<'PY'
from pathlib import Path

# ---------------------------------------------------------------------------
# Main route: the dynamic lifecycle route appears before the explicit update
# route in FastAPI, so make it intentionally dispatch "update" as well.
# ---------------------------------------------------------------------------
main_path = Path("app/main.py")
main = main_path.read_text(encoding="utf-8")
old = '''    if action not in {"start", "stop", "restart"}:
        raise HTTPException(400, "Unsupported MediaStack action")
    try:
        return await mediastack.lifecycle(name, action)
'''
new = '''    if action == "update":
        try:
            return await mediastack.start_update(name)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:1000] if exc.response is not None else str(exc)
            raise HTTPException(exc.response.status_code if exc.response is not None else 502, detail)
        except Exception as exc:
            raise HTTPException(502, str(exc))
    if action not in {"start", "stop", "restart"}:
        raise HTTPException(400, "Unsupported MediaStack action")
    try:
        return await mediastack.lifecycle(name, action)
'''
if old in main:
    main = main.replace(old, new, 1)
main_path.write_text(main, encoding="utf-8")

# ---------------------------------------------------------------------------
# Agent hardening before the write channel is ever enabled.
# ---------------------------------------------------------------------------
agent_path = Path("mediastack/agent/main.py")
agent = agent_path.read_text(encoding="utf-8")

if "WRITE_ALLOWLIST =" not in agent:
    anchor = 'WRITE_ENABLED = WRITE_REQUESTED and bool(AGENT_TOKEN)\n'
    addition = '''WRITE_ALLOWLIST = {
    item.strip()
    for item in os.getenv("WRITE_ALLOWLIST", "").split(",")
    if item.strip()
}
'''
    if anchor not in agent:
        raise SystemExit("Could not find Stack Agent write-mode anchor")
    agent = agent.replace(anchor, anchor + addition, 1)

# Replace state-directory creation calls before adding the helper itself.
if "def _ensure_state_root" not in agent:
    agent = agent.replace("STATE_ROOT.mkdir(parents=True, exist_ok=True)", "_ensure_state_root()")
    anchor = '''def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
'''
    helper = '''def _ensure_state_root() -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        STATE_ROOT.chmod(0o700)
    except OSError:
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
'''
    if anchor not in agent:
        raise SystemExit("Could not find Stack Agent state helper anchor")
    agent = agent.replace(anchor, helper, 1)

if "def _require_service_write" not in agent:
    anchor = '''def _require_write(request: Request) -> None:
    if not WRITE_ENABLED:
        reason = "write actions are disabled"
        if WRITE_REQUESTED and not AGENT_TOKEN:
            reason = "write actions require AGENT_TOKEN"
        raise HTTPException(403, reason)
    supplied = request.headers.get("authorization", "")
    expected = f"Bearer {AGENT_TOKEN}"
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "invalid MediaStack agent token")
'''
    replacement = anchor + '''\n\ndef _require_service_write(request: Request, name: str) -> None:
    _require_write(request)
    if WRITE_ALLOWLIST and name not in WRITE_ALLOWLIST:
        raise HTTPException(403, f"{name} is not in the MediaStack write allowlist")
'''
    if anchor not in agent:
        raise SystemExit("Could not find Stack Agent auth helper")
    agent = agent.replace(anchor, replacement, 1)

# Service actions must pass both token auth and the optional first-write
# allowlist. Adoption only writes local state, so it remains token-gated.
agent = agent.replace(
    '''async def lifecycle_action(name: str, action: str, request: Request) -> dict[str, Any]:
    _require_write(request)
''',
    '''async def lifecycle_action(name: str, action: str, request: Request) -> dict[str, Any]:
    _require_service_write(request, name)
''',
    1,
)
agent = agent.replace(
    '''async def start_update(name: str, request: Request) -> dict[str, Any]:
    _require_write(request)
''',
    '''async def start_update(name: str, request: Request) -> dict[str, Any]:
    _require_service_write(request, name)
''',
    1,
)

# Do not carry old Portainer/Compose ownership labels into a container that has
# been successfully recreated by ArrNexus. This is the progressive adoption
# boundary: persistent config/mounts stay the same, ownership moves to ArrNexus.
old_labels = '''    labels = dict(config.get("Labels") or {})
    labels["arrnexus.mediastack"] = "true"
    labels["arrnexus.managed"] = "true"
'''
new_labels = '''    labels = dict(config.get("Labels") or {})
    for label in list(labels):
        if label.startswith("com.docker.compose.") or label.startswith("io.portainer."):
            labels.pop(label, None)
    labels["arrnexus.mediastack"] = "true"
    labels["arrnexus.managed"] = "true"
'''
if old_labels in agent:
    agent = agent.replace(old_labels, new_labels, 1)

# Inspect snapshots contain environment variables and therefore may contain
# credentials. Keep the state directory and inspect snapshots owner-only.
old_snapshot = '                snapshot.write_text(json.dumps(old_detail, indent=2) + "\\n", encoding="utf-8")\n'
if old_snapshot in agent and "snapshot.chmod(0o600)" not in agent:
    agent = agent.replace(old_snapshot, old_snapshot + '                snapshot.chmod(0o600)\n', 1)

old_adopted = '    tmp.write_text(json.dumps(sorted(names), indent=2) + "\\n", encoding="utf-8")\n    tmp.replace(path)\n'
if old_adopted in agent and "path.chmod(0o600)" not in agent[agent.find("def _save_adopted"):agent.find("def _require_write")]:
    agent = agent.replace(old_adopted, old_adopted + '    path.chmod(0o600)\n', 1)

# Advertise the allowlist so the UI can make it obvious when the control plane
# is in low-risk first-write mode.
health_anchor = '            "adopted_containers": sorted(_adopted_names()),\n'
if health_anchor in agent and '"write_allowlist"' not in agent[agent.find("async def health"):agent.find("@app.get(\"/api/capabilities\")")]:
    agent = agent.replace(health_anchor, health_anchor + '            "write_allowlist": sorted(WRITE_ALLOWLIST),\n', 1)
cap_anchor = '        "write_enabled": WRITE_ENABLED,\n        "actions": {\n'
if cap_anchor in agent and '"write_allowlist"' not in agent[agent.find("async def capabilities"):agent.find("@app.get(\"/api/system\")")]:
    agent = agent.replace(cap_anchor, '        "write_enabled": WRITE_ENABLED,\n        "write_allowlist": sorted(WRITE_ALLOWLIST),\n        "actions": {\n', 1)

agent_path.write_text(agent, encoding="utf-8")

# Full-stack Compose also forwards the optional write allowlist.
compose_path = Path("mediastack/docker-compose.yml")
compose = compose_path.read_text(encoding="utf-8")
if "WRITE_ALLOWLIST:" not in compose:
    compose = compose.replace(
        "      AGENT_TOKEN: ${MEDIASTACK_AGENT_TOKEN:-}\n",
        "      AGENT_TOKEN: ${MEDIASTACK_AGENT_TOKEN:-}\n      WRITE_ALLOWLIST: ${MEDIASTACK_WRITE_ALLOWLIST:-}\n",
        1,
    )
compose_path.write_text(compose, encoding="utf-8")
PY

python3 -m py_compile app/main.py app/mediastack.py app/mediastack_catalog.py app/stack_setup.py mediastack/agent/main.py
bash -n mediastack/scripts/start-ui-test.sh
bash -n mediastack/scripts/start-monitor-agent.sh
bash -n mediastack/scripts/integrate-control-plane-v1.sh
git diff --check

if ! git diff --quiet; then
  echo "Final control-plane hardening changes:"
  git diff --stat
  git add app/main.py mediastack/agent/main.py mediastack/docker-compose.yml
  git commit -m "Harden MediaStack write channel and progressive adoption"
  git push origin "$BRANCH"
fi

echo
echo "MediaStack v1 source integration is complete on $BRANCH."
echo "No running Docker media container was changed by this script."
