#!/usr/bin/env python3
"""ArrNexus v9.2 regression validator carried into v9.3.

Runs the carried v9.1 -> v9 -> v8 -> v7 regression chain first, then tests the
v9.2 production-data Dashboard fix, degraded-dashboard fallback, README-driven
public documentation, safer navigation prefetch and request timing diagnostics.
"""
from __future__ import annotations
import compileall
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def require(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def run_v91(root: Path) -> None:
    if os.getenv("ARRNEXUS_VALIDATE_LAYER_ONLY") == "1":
        return
    proc = subprocess.run([sys.executable, str(root / "validate_v91.py")], cwd=root, text=True, capture_output=True, timeout=300)
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)
    require(proc.returncode == 0, f"Retained v9.1 regression validator failed with exit code {proc.returncode}")


def main() -> int:
    root = Path(__file__).resolve().parent
    run_v91(root)
    require(compileall.compile_dir(root / "app", quiet=1), "Python compilation failed")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="arrnexus-v92-validate-") as tmp:
        os.environ["DB_PATH"] = str(Path(tmp) / "router.db")
        os.environ["DB_DIR"] = tmp
        os.environ["SESSION_SECRET"] = "validation-only-v92-session-secret"
        for key in ("RADARR_API_KEY","SONARR_API_KEY","LIDARR_API_KEY","PROWLARR_API_KEY","JELLYFIN_API_KEY","SEERR_API_KEY"):
            os.environ[key] = ""

        from fastapi.testclient import TestClient
        import app.main as main_app
        from app.db import db, add_activity, create_job

        for template in sorted((root / "app" / "templates").glob("*.html")):
            main_app.templates.env.get_template(template.name)

        with TestClient(main_app.app) as client:
            landing = client.get("/", follow_redirects=False)
            require(landing.status_code == 200, "public landing page")
            for text in (
                "Reference architecture", "WHAT IS REQUIRED?", "INSTALLATION", "DOCKER NETWORKING",
                "python3 -m venv .venv", "DMM / Debrid Inbox", "ACQUISITION STRATEGIES",
                "TROUBLESHOOTING", "Download ZIP",
            ):
                require(text in landing.text, f"README-driven landing content missing: {text}")
            require(landing.headers.get("x-arrnexus-elapsed-ms"), "request timing response header missing")
            require("Server-Timing" in landing.headers, "Server-Timing response header missing")

            setup = client.post("/setup", data={
                "username":"v92validator", "email":"v92@example.invalid", "display_name":"V9.2 Validator",
                "password":"validation-password-123", "confirm":"validation-password-123",
            }, follow_redirects=False)
            require(setup.status_code == 303, "administrator setup")

            # Reproduce the real v9.1 crash condition: non-empty SQLite Row
            # objects in all three cached dashboard history lists.
            with db() as conn:
                conn.execute("""INSERT INTO imports(source_path,source_name,media_type,destination_key,destination_path,status,undone)
                              VALUES(?,?,?,?,?,?,?)""", ("/mnt/debrid/example","Example Movie","movie","default","/library/example","complete",0))
            add_activity("test", "Example Movie", "validator history row", "/mnt/debrid/example")
            create_job("validator", [{"source_path":"/mnt/debrid/example","display_name":"Example Movie","destination_key":"default"}])
            main_app._DASHBOARD_CACHE = None
            main_app._DASHBOARD_CACHE_AT = 0.0
            dash = client.get("/dashboard", follow_redirects=False)
            require(dash.status_code == 200, f"dashboard with non-empty migrated history returned {dash.status_code}")
            require("ARRNEXUS CONTROL CENTRE" in dash.text, "dashboard did not render")

            # The route itself must fail safe if a future dependency throws.
            original_snapshot = main_app.dashboard_snapshot
            async def broken_snapshot(force: bool = False):
                raise RuntimeError("validator forced snapshot failure")
            main_app.dashboard_snapshot = broken_snapshot
            try:
                degraded = client.get("/dashboard", follow_redirects=False)
            finally:
                main_app.dashboard_snapshot = original_snapshot
            require(degraded.status_code == 200, "dashboard did not degrade gracefully")
            require("Dashboard is running in degraded mode" in degraded.text, "degraded dashboard warning missing")

    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    landing_source = (root / "app" / "templates" / "landing.html").read_text(encoding="utf-8")
    js = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")
    css = (root / "app" / "static" / "app.css").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")

    require(('APP_VERSION = "9.' in main_source or 'APP_VERSION = "10.' in main_source), "compatible v9.2+ version string missing")
    require("[dict(x) for x in recent_imports(8)]" in main_source, "dashboard DB rows are not normalised")
    require("request_timing_middleware" in main_source and "slow_request" in main_source, "route performance telemetry missing")
    require("idlePrefetch" not in js, "aggressive v9.1 idle prefetch still present")
    require("220" in js and "stale-while-revalidate" in js, "intent-prefetch / SWR navigation layer missing")
    require("ArrNexus v9.2" in css and "v92-dashboard-warning" in css, "v9.2 visual/reliability CSS missing")
    require("python3 -m venv .venv" in landing_source and "python validate.py" in landing_source, "public validator install guide incomplete")
    require("python3 -m venv .venv" in readme and "sqlite3.Row" in readme, "README retained v9.2 reliability/host validation detail incomplete")

    print("PASS: ArrNexus v9.2 regression layer retained: production-data Dashboard caching, public documentation and measurable performance diagnostics")
    return 0

if __name__ == "__main__":
    # v9.2's FastAPI TestClient can leave a background worker alive after all
    # assertions have completed. Flush the final PASS/failure output and exit
    # the validator process explicitly so newer retained release gates do not
    # hang during interpreter shutdown. This does not change any assertions.
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(code or 0))


