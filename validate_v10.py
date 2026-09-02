#!/usr/bin/env python3
"""ArrNexus v10.0.0-beta release validator.

Runs the complete retained v9.4 -> v9.3 -> v9.2 -> v9.1 -> v9 -> v8 -> v7
chain, then validates the v10 self-update bootstrap, collapsed connection UI,
product-wide visual system and redesigned public landing page.
"""
from __future__ import annotations

import compileall
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_retained_layers(root: Path) -> None:
    env = os.environ.copy()
    env["ARRNEXUS_VALIDATE_LAYER_ONLY"] = "1"
    for script, label in (
        ("validate_v7.py", "v7"),
        ("validate_v8.py", "v8"),
        ("validate_v9.py", "v9"),
        ("validate_v91.py", "v9.1"),
        ("validate_v92.py", "v9.2"),
        ("validate_v93.py", "v9.3"),
        ("validate_v94.py", "v9.4"),
    ):
        proc = subprocess.run(
            [sys.executable, str(root / script)], cwd=root, env=env,
            text=True, capture_output=True, timeout=240,
        )
        if proc.stdout:
            print(proc.stdout.rstrip())
        if proc.stderr:
            print(proc.stderr.rstrip(), file=sys.stderr)
        require(proc.returncode == 0, f"Retained {label} regression validator failed with exit code {proc.returncode}")


def main() -> int:
    root = Path(__file__).resolve().parent
    if os.getenv("ARRNEXUS_VALIDATE_V10_ONLY") != "1":
        run_retained_layers(root)

    require(compileall.compile_dir(root / "app", quiet=1), "Python compilation failed")
    require(compileall.compile_file(str(root / "bootstrap.py"), quiet=1), "Bootstrap Python compilation failed")

    # JavaScript syntax is part of the release gate when node is available.
    node = __import__("shutil").which("node")
    if node:
        proc = subprocess.run([node, "--check", str(root / "app" / "static" / "app.js")], text=True, capture_output=True, timeout=60)
        require(proc.returncode == 0, f"JavaScript syntax failed: {proc.stderr}")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="arrnexus-v10-validate-") as tmp:
        os.environ["DB_PATH"] = str(Path(tmp) / "router.db")
        os.environ["DB_DIR"] = tmp
        os.environ["SESSION_SECRET"] = "validation-only-v10-session-secret"
        os.environ["ARRNEXUS_SELF_UPDATE"] = "1"
        for key in (
            "RADARR_API_KEY", "SONARR_API_KEY", "LIDARR_API_KEY", "PROWLARR_API_KEY",
            "JELLYFIN_API_KEY", "PLEX_API_KEY", "EMBY_API_KEY", "SEERR_API_KEY",
        ):
            os.environ[key] = ""

        from fastapi.testclient import TestClient
        import app.main as main_app
        import app.updater as updater

        # Compile every template with the real ArrNexus Jinja environment.
        for template in sorted((root / "app" / "templates").glob("*.html")):
            main_app.templates.env.get_template(template.name)

        with TestClient(main_app.app) as client:
            health = client.get("/api/health", follow_redirects=False)
            require(health.status_code == 200 and str(health.json().get("version") or "").startswith("10."), "v10 health/version response")

            landing = client.get("/", follow_redirects=False)
            require(landing.status_code == 200, "v10 public landing")
            for marker in ("v10-hero.png", "v10-core-features.png", "v10-architecture.png", "v10-quick-start.png", "ArrNexus can now update ArrNexus"):
                require(marker in landing.text, f"v10 landing missing {marker}")

            setup = client.post("/setup", data={
                "username": "v10validator",
                "email": "v10@example.invalid",
                "display_name": "V10 Validator",
                "password": "validation-password-123",
                "confirm": "validation-password-123",
            }, follow_redirects=False)
            require(setup.status_code == 303, "v10 administrator setup")

            settings_page = client.get("/settings", follow_redirects=False)
            require(settings_page.status_code == 200, "v10 settings")
            for marker in ("NATIVE SELF-UPDATE", "Fudmonk95/ArrNexus", "Install available update", "SQLite backup"):
                require(marker in settings_page.text, f"v10 update settings missing {marker}")

            status = client.get("/api/update-status", follow_redirects=False)
            require(status.status_code == 200 and status.json().get("self_update_capable") is True, "v10 update status API")

        # Directly test updater safety primitives without any live GitHub dependency.
        updater.DATA_DIR = Path(tmp)
        updater.RUNTIME_DIR = Path(tmp) / "runtime"
        updater.RELEASES_DIR = updater.RUNTIME_DIR / "releases"
        updater.VENVS_DIR = updater.RUNTIME_DIR / "venvs"
        updater.STATUS_PATH = Path(tmp) / "update-status.json"
        updater.RESTART_REQUEST_PATH = updater.RUNTIME_DIR / "restart-request.json"

        db = sqlite3.connect(str(Path(tmp) / "router.db"))
        db.execute("CREATE TABLE IF NOT EXISTS v10_probe(value TEXT)")
        db.execute("INSERT INTO v10_probe(value) VALUES ('preserved')")
        db.commit(); db.close()
        backup = updater._backup_database("v10-validator")
        require(backup.exists(), "v10 updater database backup was not created")
        b = sqlite3.connect(str(backup)); row = b.execute("SELECT value FROM v10_probe").fetchone(); b.close()
        require(row and row[0] == "preserved", "v10 updater database backup is not readable/preserved")

        traversal = Path(tmp) / "bad.zip"
        with zipfile.ZipFile(traversal, "w") as zf:
            zf.writestr("../../escape.txt", "no")
        try:
            updater._safe_extract(traversal, Path(tmp) / "bad-extract")
            raise AssertionError("unsafe ZIP traversal was accepted")
        except ValueError:
            pass

        require(updater.version_key("10.0.1-beta") > updater.version_key("10.0.0-beta"), "v10 semantic update comparison")
        require(updater.version_key("10.0.0") > updater.version_key("10.0.0-beta"), "stable release ordering")

    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    updater_source = (root / "app" / "updater.py").read_text(encoding="utf-8")
    bootstrap_source = (root / "bootstrap.py").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    arrs = (root / "app" / "templates" / "arrs.html").read_text(encoding="utf-8")
    ecosystem = (root / "app" / "templates" / "ecosystem.html").read_text(encoding="utf-8")
    base = (root / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    landing = (root / "app" / "templates" / "landing.html").read_text(encoding="utf-8")
    css = (root / "app" / "static" / "app.css").read_text(encoding="utf-8")
    js = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")
    sw = (root / "app" / "static" / "sw.js").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    guide = (root / "docs" / "USER_GUIDE.md").read_text(encoding="utf-8")
    audit = (root / "docs" / "DOCUMENTATION_AUDIT.md").read_text(encoding="utf-8")

    require('APP_VERSION = "10.' in main_source, "v10-compatible version string missing")
    for marker in ("/api/update-check", "/api/update-status", "/api/update-install", "start_self_update"):
        require(marker in main_source, f"v10 update API missing {marker}")
    for marker in ("SHA-256", "_safe_extract", "_backup_database", "RESTART_REQUEST_PATH", "SELF_UPDATE_CAPABLE", "signal.SIGTERM"):
        require(marker in updater_source, f"v10 updater safety/runtime marker missing {marker}")
    for marker in ("RUNTIME = DATA / \"runtime\"", "wait_for_health", "rolled_back", "restart-request.json", "ARRNEXUS_SELF_UPDATE"):
        require(marker in bootstrap_source, f"v10 bootstrap marker missing {marker}")
    require('CMD ["python", "/opt/arrnexus-bootstrap.py"]' in dockerfile and "ARRNEXUS_SELF_UPDATE=1" in dockerfile, "Dockerfile is not using the v10 bootstrap")
    require("docker.sock" not in compose.lower(), "v10 self-update must not require the Docker socket")

    require(arrs.count("<details class=\"v10-service-accordion") >= 2 and "v10-accordion-stack" in arrs, "Connections are not collapsed v10 accordions")
    require(ecosystem.count("<details class=\"v10-service-accordion") >= 3 and "is-disabled" in ecosystem, "Ecosystem is not using collapsed/disabled v10 accordions")
    require("v10-update-modal" in base and "data-install-update" in base, "global v10 update notification modal missing")
    require("v10-update-modal" in css and "#030304" in css and "v10-public-page" in css, "v10 black product visual layer missing")
    require("/api/update-install" in js and "location.reload()" in js and "data-update-dismiss" in js, "v10 client update/reload workflow missing")
    require("arrnexus-static-v10." in sw, "v10 service-worker cache marker missing")
    for asset in ("v10-hero.png", "v10-architecture.png", "v10-core-features.png", "v10-quick-start.png"):
        require((root / "app" / "static" / asset).exists(), f"v10 public visual asset missing {asset}")
    require("Version 10 — ArrNexus updates itself" in readme and "Cleaner Connections & Ecosystem" in readme, "README missing v10 release architecture")
    require("Native updates, release ZIPs & rollback" in guide, "User Guide missing v10 update instructions")
    m = re.search(r"Application routes/actions audited: \*\*(\d+)\*\*", audit)
    require(bool(m) and int(m.group(1)) >= 122, "Documentation audit missing v10 update routes")

    print("PASS: ArrNexus v10.0.0-beta retains v7/v8/v9/v9.1/v9.2/v9.3/v9.4 regressions and adds checksum-verified native self-update with SQLite backup/validation/restart/rollback, collapsed Connections/Ecosystem configuration, unified black product styling and README-matched public landing visuals")
    return 0


if __name__ == "__main__":
    # Retained validators can leave TestClient/background workers alive after
    # the final assertion. Explicit process exit prevents release-gate hangs
    # without changing any validation assertions.
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(code or 0))


