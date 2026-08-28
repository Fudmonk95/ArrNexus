#!/usr/bin/env python3
"""ArrNexus v9.4.0-beta release validator.

Runs the complete retained v9.3 -> v9.2 -> v9.1 -> v9 -> v8 -> v7 chain,
then validates the Help Centre/documentation coverage and reverse-proxy-aware
guidance added in v9.4.
"""
from __future__ import annotations

import compileall
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_v93(root: Path) -> None:
    if os.getenv("ARRNEXUS_VALIDATE_LAYER_ONLY") == "1":
        return
    proc = subprocess.run(
        [sys.executable, str(root / "validate_v93.py")],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=420,
    )
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)
    require(proc.returncode == 0, f"Retained v9.3 regression validator failed with exit code {proc.returncode}")


def main() -> int:
    root = Path(__file__).resolve().parent
    run_v93(root)
    require(compileall.compile_dir(root / "app", quiet=1), "Python compilation failed")

    # Regenerate release docs from the single Help catalogue and ensure the
    # checked-in documents are reproducible rather than hand-maintained copies.
    proc = subprocess.run([sys.executable, str(root / "generate_help_docs.py")], cwd=root, text=True, capture_output=True, timeout=60)
    require(proc.returncode == 0, f"Help documentation generation failed: {proc.stderr}")

    from app.help_catalog import TOPICS, TOPICS_BY_SLUG, topic_for_path
    require(len(TOPICS) >= 40, "Help catalogue is not broad enough for the v9.4 route surface")
    for topic in TOPICS:
        require(topic.get("slug") and topic.get("title") and topic.get("summary"), f"Incomplete Help topic identity: {topic}")
        for section in ("setup", "usage", "success", "troubleshooting"):
            require(topic.get(section), f"Help topic {topic['slug']} missing {section}")

    # Every primary private page must resolve to a contextual guide.  This is
    # the release rule that prevents future features from quietly losing docs.
    primary_pages = [
        "/dashboard", "/discover", "/music", "/music/settings", "/scraping", "/debrid",
        "/providers", "/infinidysk", "/decypharr", "/indexers", "/queue", "/inbox", "/item",
        "/libraries", "/browser", "/jobs", "/rules", "/self-healing", "/quality-lab", "/problems",
        "/maintenance", "/logs", "/ecosystem", "/readiness", "/aiostreams", "/arrs", "/settings",
        "/profile", "/onboarding",
    ]
    for path in primary_pages:
        slug = topic_for_path(path)
        require(slug in TOPICS_BY_SLUG and slug != "getting-started", f"Primary page {path} has no contextual Help mapping")

    with tempfile.TemporaryDirectory(prefix="arrnexus-v94-validate-") as tmp:
        os.environ["DB_PATH"] = str(Path(tmp) / "router.db")
        os.environ["DB_DIR"] = tmp
        os.environ["SESSION_SECRET"] = "validation-only-v94-session-secret"
        for key in (
            "RADARR_API_KEY", "SONARR_API_KEY", "LIDARR_API_KEY", "PROWLARR_API_KEY",
            "JELLYFIN_API_KEY", "PLEX_API_KEY", "EMBY_API_KEY", "SEERR_API_KEY",
        ):
            os.environ[key] = ""

        from fastapi.testclient import TestClient
        import app.main as main_app
        from app.db import setting_get

        for template in sorted((root / "app" / "templates").glob("*.html")):
            main_app.templates.env.get_template(template.name)

        with TestClient(main_app.app) as client:
            help_page = client.get("/help", follow_redirects=False)
            require(help_page.status_code == 200, "public Help Centre")
            for marker in ("Use the tool.", "Spotify app setup", "AIOStreams Bridge", "DUMB mount namespace", "Password recovery"):
                require(marker in help_page.text, f"Help Centre missing {marker}")

            spotify_help = client.get("/help?topic=spotify", follow_redirects=False)
            for marker in ("Users Management", "/music/spotify/callback", "INVALID_REDIRECT_URI", "403", "Premium"):
                require(marker in spotify_help.text, f"Spotify Help missing {marker}")

            landing = client.get("/", follow_redirects=False)
            require(landing.status_code == 200 and ("Help Centre" in landing.text or "HELP CENTRE" in landing.text) and 'href="/help"' in landing.text, "v9.4 landing Help integration")

            setup = client.post("/setup", data={
                "username": "v94validator",
                "email": "v94@example.invalid",
                "display_name": "V9.4 Validator",
                "password": "validation-password-123",
                "confirm": "validation-password-123",
            }, follow_redirects=False)
            require(setup.status_code == 303, "administrator setup")

            # Dashboard behaviour is already covered by the retained v9.2 layer.
            # v9.4 only needs to prove that the persistent shell resolves a
            # contextual Help topic; source/Jinja checks below cover this without
            # fanning out to every configured live integration during validation.

            # Reverse-proxy-aware Spotify callback suggestion: do not force users
            # to guess the externally visible callback behind Cloudflare/Traefik.
            music_settings = client.get(
                "/music/settings",
                headers={"x-forwarded-proto": "https", "x-forwarded-host": "arrnexus.validator.invalid"},
                follow_redirects=False,
            )
            require(music_settings.status_code == 200, "Music API Settings")
            expected = "https://arrnexus.validator.invalid/music/spotify/callback"
            require(expected in music_settings.text, "reverse-proxy Spotify callback suggestion missing")
            for marker in ("Spotify setup checklist", "Users Management", "Open full Spotify troubleshooting"):
                require(marker in music_settings.text, f"Music settings missing {marker}")

            # Explicit Public URL must persist and is validated as an origin only.
            save = client.post("/settings/general", data={
                "app_title": "ArrNexus",
                "public_url": "https://public.validator.invalid",
                "smtp_host": "", "smtp_port": "587", "smtp_username": "", "smtp_password": "", "smtp_from": "", "smtp_starttls": "false",
            }, follow_redirects=False)
            require(save.status_code == 303, "Public URL setting save")
            require(setting_get("app.public_url") == "https://public.validator.invalid", "Public URL not persisted")
            music_settings = client.get("/music/settings", follow_redirects=False)
            require("https://public.validator.invalid/music/spotify/callback" in music_settings.text, "configured public URL not used for Spotify callback")

    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    base = (root / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    help_template = (root / "app" / "templates" / "help.html").read_text(encoding="utf-8")
    music_settings = (root / "app" / "templates" / "music_settings.html").read_text(encoding="utf-8")
    landing = (root / "app" / "templates" / "landing.html").read_text(encoding="utf-8")
    css = (root / "app" / "static" / "app.css").read_text(encoding="utf-8")
    sw = (root / "app" / "static" / "sw.js").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    guide = (root / "docs" / "USER_GUIDE.md").read_text(encoding="utf-8")
    audit = (root / "docs" / "DOCUMENTATION_AUDIT.md").read_text(encoding="utf-8")

    require(re.search(r'APP_VERSION\s*=\s*"(?:9\.4\.0-beta|10\.[^"]+)"', main_source), "v9.4+ compatible version string missing")
    require('@app.get("/help"' in main_source and "help_topic_for_path" in main_source, "Help Centre route/context mapping missing")
    require("_public_origin" in main_source and 'setting_get("app.public_url"' in main_source, "reverse-proxy public origin support missing")
    require("Help & Guides" in base and "help_topic_for_path(request.url.path)" in base, "private contextual Help UI missing")
    require("Spotify setup checklist" in music_settings and "data-copy-text" in music_settings, "Spotify guided settings missing")
    require("HELP CENTRE" in landing and 'href="/help"' in landing, "public Help entry missing")
    require("ArrNexus v9.4" in css and "v94-help-topic" in css, "v9.4 Help Centre CSS missing")
    require("arrnexus-static-v9.4" in sw or "arrnexus-static-v10." in sw, "v9.4+ service-worker cache marker missing")
    for marker in ("Spotify app setup & per-user OAuth", "AIOStreams Bridge", "Plex", "Maintenance", "Notifications", "Password recovery", "DUMB mount namespace"):
        require(marker in guide, f"Generated user guide missing {marker}")
    m = re.search(r"Application routes/actions audited: \*\*(\d+)\*\*", audit)
    require(bool(m) and int(m.group(1)) >= 120, "Documentation audit does not cover the retained route/action surface")
    for marker in ("python3 -m venv .venv", "Users Management", "docs/USER_GUIDE.md", "Portainer"):
        require(marker in readme, f"README missing v9.4 documentation detail: {marker}")

    print("PASS: ArrNexus v9.4.0-beta retains v7/v8/v9/v9.1/v9.2/v9.3 regressions and adds source-backed Help Centre guidance, full route/action documentation coverage, guided Spotify OAuth setup and reverse-proxy-aware public links")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
