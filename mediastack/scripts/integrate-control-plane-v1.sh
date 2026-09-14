#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/arrnexus-mediastack-src}"
BRANCH="feature/mediastack-v1-control-plane"

for cmd in git python3; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Required command is missing: $cmd" >&2; exit 1; }
done

if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "ArrNexus checkout not found at $REPO_DIR" >&2
  exit 2
fi

cd "$REPO_DIR"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

python3 - <<'PY'
from pathlib import Path

path = Path("app/main.py")
text = path.read_text(encoding="utf-8")

# The control-plane proxy preserves useful HTTP errors from the Stack Agent.
if "\nimport httpx\n" not in text:
    needle = "import secrets\n"
    if needle not in text:
        raise SystemExit("Could not find import anchor in app/main.py")
    text = text.replace(needle, needle + "import httpx\n", 1)

# Import setup/control-plane state.
if "from . import stack_setup\n" not in text:
    needle = "from . import mediastack\n"
    if needle not in text:
        raise SystemExit("Could not find MediaStack import anchor in app/main.py")
    text = text.replace(needle, needle + "from . import stack_setup\n", 1)

# Start the opt-in maintenance-window scheduler with the normal production
# lifespan. The isolated UI-test still runs with --lifespan off, so it never
# performs Docker actions.
if 'name="mediastack-update-scheduler"' not in text:
    needle = '        asyncio.create_task(mediastack.metadata_loop(), name="mediastack-metadata"),\n'
    if needle not in text:
        raise SystemExit("Could not find MediaStack lifespan anchor")
    text = text.replace(
        needle,
        needle + '        asyncio.create_task(mediastack.update_scheduler_loop(), name="mediastack-update-scheduler"),\n',
        1,
    )

# Fresh MediaStack deployments can opt into first-login onboarding. Existing
# installations are unaffected unless MEDIASTACK_GUIDED_SETUP=true is supplied.
setup_return = '        log_event("info", "auth", "setup", "Initial administrator created")\n        return _go("/")'
if setup_return in text:
    text = text.replace(
        setup_return,
        '        log_event("info", "auth", "setup", "Initial administrator created")\n'
        '        if os.getenv("MEDIASTACK_GUIDED_SETUP", "false").lower() in {"1", "true", "yes"}:\n'
        '            return _go("/stack-setup")\n'
        '        return _go("/")',
        1,
    )

# When guided setup is enabled, the normal dashboard becomes the continuation
# point until onboarding has been saved once.
dashboard_anchor = '''async def dashboard(request: Request):\n    # Dashboard rendering is intentionally cache-only.'''
if dashboard_anchor in text and "MEDIASTACK_GUIDED_SETUP" not in text[text.find(dashboard_anchor):text.find(dashboard_anchor)+500]:
    text = text.replace(
        dashboard_anchor,
        '''async def dashboard(request: Request):\n    if (\n        os.getenv("MEDIASTACK_GUIDED_SETUP", "false").lower() in {"1", "true", "yes"}\n        and not stack_setup.state().get("completed")\n    ):\n        return _go("/stack-setup")\n    # Dashboard rendering is intentionally cache-only.''',
        1,
    )

start = text.find('@app.get("/mediastack", response_class=HTMLResponse)')
end = text.find('@app.get("/logs", response_class=HTMLResponse)', start)
if start < 0 or end < 0:
    raise SystemExit("Could not find existing MediaStack route block")

routes = r'''@app.get("/mediastack", response_class=HTMLResponse)
async def mediastack_page(request: Request):
    try:
        await mediastack.refresh_status()
        await mediastack.refresh_jobs()
    except Exception:
        pass
    return _render(request, "mediastack.html", stack=mediastack.cached_snapshot())


@app.get("/api/mediastack")
async def mediastack_api(request: Request):
    _require_user(request)
    return mediastack.cached_snapshot()


@app.post("/api/mediastack/refresh")
async def mediastack_refresh_api(request: Request):
    _require_user(request)
    await mediastack.refresh_status()
    await mediastack.refresh_jobs()
    return mediastack.cached_snapshot()


@app.post("/api/mediastack/check-updates")
async def mediastack_update_check_api(request: Request):
    _require_user(request)
    await mediastack.refresh_updates()
    return mediastack.cached_snapshot()


@app.post("/api/mediastack/refresh-configs")
async def mediastack_config_refresh_api(request: Request):
    _require_user(request)
    await mediastack.refresh_configs()
    return mediastack.cached_snapshot().get("configs") or {}


@app.get("/api/mediastack/logs/{name}")
async def mediastack_logs_api(request: Request, name: str, tail: int = 250):
    _require_user(request)
    try:
        return await mediastack.logs(name, tail)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.get("/api/mediastack/config")
async def mediastack_config_api(request: Request, root: str, path: str):
    _require_user(request)
    try:
        return await mediastack.config_file(root, path)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.get("/api/mediastack/discovery")
async def mediastack_discovery_api(request: Request):
    _require_user(request)
    try:
        return await mediastack.discovery()
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.get("/api/mediastack/update-plan/{name}")
async def mediastack_update_plan_api(request: Request, name: str):
    _require_user(request)
    try:
        return await mediastack.update_plan(name)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.post("/api/mediastack/actions/{name}/{action}")
async def mediastack_action_api(request: Request, name: str, action: str):
    _require_user(request)
    if action not in {"start", "stop", "restart"}:
        raise HTTPException(400, "Unsupported MediaStack action")
    try:
        return await mediastack.lifecycle(name, action)
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:1000] if exc.response is not None else str(exc)
        raise HTTPException(exc.response.status_code if exc.response is not None else 502, detail)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.post("/api/mediastack/actions/{name}/update")
async def mediastack_start_update_api(request: Request, name: str):
    _require_user(request)
    try:
        return await mediastack.start_update(name)
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:1000] if exc.response is not None else str(exc)
        raise HTTPException(exc.response.status_code if exc.response is not None else 502, detail)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.get("/api/mediastack/jobs/{job_id}")
async def mediastack_job_api(request: Request, job_id: str):
    _require_user(request)
    try:
        return await mediastack.job(job_id)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.get("/stack-setup", response_class=HTMLResponse)
async def stack_setup_page(request: Request):
    try:
        await mediastack.refresh_status()
    except Exception:
        pass
    stack = mediastack.cached_snapshot()
    return _render(request, "stack_setup.html", setup=stack_setup.page_context(stack), stack=stack)


@app.post("/api/stack-setup/preview")
async def stack_setup_preview_api(request: Request):
    _require_user(request)
    payload = await request.json()
    current = stack_setup.normalise(payload if isinstance(payload, dict) else {})
    return {
        "ok": True,
        "state": current,
        "plan": stack_setup.adoption_plan(mediastack.cached_snapshot(), current),
    }


@app.get("/api/stack-setup/recovery-compose")
async def stack_setup_recovery_compose_api(request: Request):
    _require_user(request)
    text = stack_setup.recovery_compose(mediastack.cached_snapshot(), stack_setup.state())
    return PlainTextResponse(
        text,
        media_type="text/yaml",
        headers={"Content-Disposition": 'attachment; filename="arrnexus-mediastack-recovery.yml"'},
    )


@app.post("/stack-setup")
async def stack_setup_save(request: Request):
    _require_user(request)
    form = await request.form()
    selected = [str(value) for value in form.getlist("services")]
    payload = {
        "mode": str(form.get("mode") or "adopt"),
        "selected_services": selected,
        "stack_root": str(form.get("stack_root") or "/opt/arrnexus-mediastack"),
        "zurg_mount_root": str(form.get("zurg_mount_root") or "/zurg_mnt"),
        "config_strategy": str(form.get("config_strategy") or "keep-existing"),
        "tz": str(form.get("tz") or "Europe/London"),
        "puid": str(form.get("puid") or "1000"),
        "pgid": str(form.get("pgid") or "1000"),
        "jellyfin_gpu": str(form.get("jellyfin_gpu") or "auto"),
        "update_mode": str(form.get("update_mode") or "review"),
        "update_time": str(form.get("update_time") or "04:00"),
        "rollback": bool(form.get("rollback")),
        "stabilization_seconds": str(form.get("stabilization_seconds") or "30"),
    }
    action = str(form.get("action") or "save")
    complete = action == "complete"
    saved = stack_setup.save(payload, complete=complete)
    policies = {
        key: str(form.get(f"policy_{key}") or "")
        for key in saved.get("selected_services") or []
    }
    stack_setup.save_service_policies(policies)

    stack = mediastack.cached_snapshot()
    plan = stack_setup.adoption_plan(stack, saved)
    if complete and saved.get("mode") == "adopt":
        names = [str(row.get("container") or "") for row in plan.get("services") or [] if row.get("present") and row.get("container")]
        if stack.get("write_enabled") and names:
            try:
                await mediastack.adopt(names)
                _flash(request, f"MediaStack setup saved. Adopted {len(names)} existing container(s).", "success")
            except Exception as exc:
                _flash(request, f"Setup saved, but adoption needs attention: {exc}", "error")
        else:
            _flash(request, "MediaStack setup saved. The Docker control channel is still locked, so no containers were changed.", "success")
    elif complete and saved.get("mode") == "fresh":
        _flash(request, "Fresh-install desired state saved. No production containers were replaced; the install executor can apply this plan when enabled.", "success")
    else:
        _flash(request, "MediaStack setup plan saved.", "success")
    return _go("/stack-setup")


'''
text = text[:start] + routes + text[end:]
path.write_text(text, encoding="utf-8")
PY

python3 -m py_compile app/main.py app/mediastack.py app/mediastack_catalog.py app/stack_setup.py mediastack/agent/main.py

git diff --check

echo "Changes being committed:"
git diff --stat

git add app/main.py
git commit -m "Wire MediaStack v1 control plane into ArrNexus"
git push origin "$BRANCH"

echo
echo "MediaStack v1 routes and guided setup integration pushed to $BRANCH."
echo "No running containers were changed."
