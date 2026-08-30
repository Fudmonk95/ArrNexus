#!/usr/bin/env python3
"""ArrNexus v9.0.0-beta release validator.

Runs the retained v8 regression suite first, then validates the v9 product
front door, onboarding, provider registry, provider-neutral AIOStreams merge,
branding assets and authorization boundaries against a fresh local database.
No live user credentials are used.
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


def run_v8(root: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(root / "validate_v8.py")],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=240,
    )
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)
    require(proc.returncode == 0, f"Retained v8 regression validator failed with exit code {proc.returncode}")


def main() -> int:
    root = Path(__file__).resolve().parent
    run_v8(root)
    require(compileall.compile_dir(root / "app", quiet=1), "Python compilation failed")

    logo = root / "app" / "static" / "arrnexus-logo-v9.png"
    icon = root / "app" / "static" / "arrnexus-icon-v9.png"
    require(logo.is_file() and logo.stat().st_size > 10_000, "v9 ArrNexus wordmark asset missing")
    require(icon.is_file() and icon.stat().st_size > 10_000, "v9 ArrNexus icon asset missing")

    with tempfile.TemporaryDirectory(prefix="arrnexus-v9-validate-") as tmp:
        os.environ["DB_PATH"] = str(Path(tmp) / "router.db")
        os.environ["DB_DIR"] = tmp
        os.environ["SESSION_SECRET"] = "validation-only-v9-session-secret"
        os.environ["RADARR_API_KEY"] = ""
        os.environ["SONARR_API_KEY"] = ""
        os.environ["LIDARR_API_KEY"] = ""
        os.environ["PROWLARR_API_KEY"] = ""
        os.environ["JELLYFIN_API_KEY"] = ""
        os.environ["SEERR_API_KEY"] = ""

        from fastapi.testclient import TestClient
        import app.main as main_app
        from app import aiostreams as aio
        from app.db import create_user, setting_get
        from app.providers import provider_credentials_for_aiostreams, provider_state

        # All templates compile through the real app environment/custom filters.
        for template in sorted((root / "app" / "templates").glob("*.html")):
            main_app.templates.env.get_template(template.name)

        with TestClient(main_app.app) as client:
            landing = client.get("/", follow_redirects=False)
            require(landing.status_code == 200, "public landing page")
            require("Your Media Stack" in landing.text and "One Control Plane" in landing.text, "v9 public product copy missing")
            require("arrnexus-logo-v9" not in landing.text or "arrnexus-icon-v9" in landing.text, "v9 brand assets not referenced")
            require("1280 movies" not in landing.text, "public landing page leaked private dashboard data")

            private = client.get("/dashboard", follow_redirects=False)
            require(private.status_code == 303 and private.headers.get("location") == "/setup", "unconfigured dashboard should route to setup")

            setup = client.post(
                "/setup",
                data={
                    "username": "v9validator",
                    "email": "v9@example.invalid",
                    "display_name": "V9 Validator",
                    "password": "validation-password-123",
                    "confirm": "validation-password-123",
                },
                follow_redirects=False,
            )
            require(setup.status_code == 303 and setup.headers.get("location") == "/onboarding", "first admin should enter guided onboarding")
            require(setting_get("setup.complete", "") == "false", "setup should remain incomplete until onboarding finish")

            onboarding = client.get("/onboarding", follow_redirects=False)
            require(onboarding.status_code == 200, "onboarding page")
            require("Environment detection" in onboarding.text and "Choose providers" in onboarding.text and "System readiness" in onboarding.text, "guided onboarding sections missing")

            providers_page = client.get("/providers", follow_redirects=False)
            require(providers_page.status_code == 200, "provider registry page")
            for label in ("Real-Debrid", "TorBox", "Premiumize", "AllDebrid", "Easynews", "InfiniDysk / NzbDAV"):
                require(label in providers_page.text, f"provider missing from registry UI: {label}")

            save = client.post(
                "/providers/torbox",
                data={"enabled": "1", "apiKey": "validation-torbox-secret"},
                follow_redirects=False,
            )
            require(save.status_code == 303, "TorBox provider save")
            torbox = provider_state("torbox", mask=True)
            require(torbox["enabled"] and torbox["configured"], "TorBox provider was not enabled/configured")
            require(torbox["credentials"].get("apiKey") == "********", "provider secret was not masked")
            require(provider_credentials_for_aiostreams().get("torbox", {}).get("apiKey") == "validation-torbox-secret", "provider credential not available to internal AIOStreams bridge")
            masked_page = client.get("/providers")
            require("validation-torbox-secret" not in masked_page.text, "provider page leaked secret")

            # Provider-neutral merge: add TorBox, preserve unrelated config and
            # preserve an already-populated remote Premiumize credential.
            original = {
                "services": [
                    {"id": "premiumize", "enabled": True, "credentials": {"apiKey": "remote-premiumize-secret"}},
                    {"id": "realdebrid", "enabled": False, "credentials": {}},
                ],
                "presets": [],
                "unrelated": {"keep": True},
            }
            integrations = {
                "prowlarr": {"url": "", "api_key": ""},
                "realdebrid": {"available": False, "api_key": ""},
                "nzbdav": {"available": False, "credentials": {}, "fields": []},
                "providers": {
                    "torbox": {"apiKey": "validation-torbox-secret"},
                    "premiumize": {"apiKey": "arrnexus-should-not-overwrite"},
                },
            }
            plan = aio.merge_autowire(original, integrations, wire_prowlarr=False, wire_realdebrid=False, wire_nzbdav=False)
            require(plan["config"]["unrelated"] == {"keep": True}, "provider merge changed unrelated AIOStreams config")
            services = {x.get("id"): x for x in plan["config"].get("services", []) if isinstance(x, dict)}
            require(services["torbox"]["enabled"] is True, "TorBox was not enabled by provider-neutral merge")
            require(services["torbox"]["credentials"].get("apiKey") == "validation-torbox-secret", "TorBox key not wired")
            require(services["premiumize"]["credentials"].get("apiKey") == "remote-premiumize-secret", "existing AIOStreams provider credential was overwritten")
            safe = aio.safe_json(plan["config"])
            require("validation-torbox-secret" not in safe and "remote-premiumize-secret" not in safe, "provider merge preview leaked a secret")

            readiness = client.get("/readiness", follow_redirects=False)
            require(readiness.status_code == 200 and "Stack Readiness" in readiness.text, "stack readiness page")

            landing_after = client.get("/", follow_redirects=False)
            require(landing_after.status_code == 200 and "Sign in" not in landing_after.text and "Dashboard" in landing_after.text, "logged-in public landing CTA should expose dashboard, not private data")

            finish = client.post("/onboarding/finish", follow_redirects=False)
            require(finish.status_code == 303 and finish.headers.get("location", "").startswith("/dashboard"), "onboarding finish")
            require(setting_get("setup.complete", "") == "true", "onboarding did not mark setup complete")

            create_user("normalv9", "normalv9@example.invalid", "Normal", "validation-password-456", "user")
            client.get("/logout")
            login = client.post("/login", data={"username": "normalv9", "password": "validation-password-456"}, follow_redirects=False)
            require(login.status_code == 303 and login.headers.get("location") == "/dashboard", "login should enter private dashboard")
            for admin_url in ("/providers", "/readiness", "/onboarding"):
                denied = client.get(admin_url, follow_redirects=False)
                require(denied.status_code == 403, f"non-admin gained access to {admin_url}")

    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    base = (root / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    landing = (root / "app" / "templates" / "landing.html").read_text(encoding="utf-8")
    providers = (root / "app" / "providers.py").read_text(encoding="utf-8")
    aio_source = (root / "app" / "aiostreams.py").read_text(encoding="utf-8")
    require(any(v in main_source for v in ('APP_VERSION = "9.3.0-beta"', 'APP_VERSION = "9.2.0-beta"', 'APP_VERSION = "9.1.0-beta"', 'APP_VERSION = "9.0.0-beta"')), "v9+ beta version string missing")
    for route in ("/dashboard", "/onboarding", "/providers", "/readiness"):
        require(route in main_source, f"missing v9 route {route}")
    require("arrnexus-icon-v9.png" in base and "arrnexus-icon-v9.png" in landing, "v9 brand icon not integrated")
    require("Provider Registry" in landing or "provider" in landing.lower(), "provider-neutral public copy missing")
    require("provider_credentials_for_aiostreams" in providers and '"torbox"' in providers, "provider registry integration missing")
    require("provider_payload" in aio_source and "only fill missing remote" in aio_source, "provider-neutral AIOStreams safety merge missing")

    print("PASS: ArrNexus v9.0.0-beta retains v7/v8 regressions and adds branded public onboarding, provider-neutral acquisition, readiness scoring and safe multi-provider AIOStreams wiring")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
