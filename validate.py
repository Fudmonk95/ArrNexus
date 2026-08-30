#!/usr/bin/env python3
"""Offline ArrNexus package validator.

Run inside the project folder after dependencies are installed:
    python validate.py
It does not call Radarr/Sonarr/Prowlarr/Real-Debrid or other external services.
"""
from __future__ import annotations
import compileall
import os
import tempfile
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    ok = compileall.compile_dir(root / "app", quiet=1)
    if not ok:
        print("FAIL: Python compilation")
        return 1

    with tempfile.TemporaryDirectory(prefix="arrnexus-validate-") as tmp:
        os.environ["DB_PATH"] = str(Path(tmp) / "router.db")
        os.environ["DB_DIR"] = tmp
        # Import only after DB variables are set.
        from fastapi.testclient import TestClient
        import app.main as main_app

        for template in sorted((root / "app" / "templates").glob("*.html")):
            main_app.templates.env.get_template(template.name)

        with TestClient(main_app.app) as client:
            assert client.get("/api/health").status_code == 200
            assert client.get("/setup").status_code == 200
            response = client.post(
                "/setup",
                data={
                    "username": "validator",
                    "email": "validator@example.invalid",
                    "display_name": "Validator",
                    "password": "validation-password-123",
                    "confirm": "validation-password-123",
                },
                follow_redirects=False,
            )
            assert response.status_code == 303
            for url in (
                "/settings", "/profile", "/logs", "/jobs", "/rules", "/libraries",
                "/arrs", "/queue", "/scraping", "/maintenance", "/timeline?title=Validation",
                "/discover", "/music", "/debrid", "/static/manifest.webmanifest", "/static/sw.js",
            ):
                r = client.get(url, follow_redirects=False)
                assert r.status_code < 500, f"{url}: {r.status_code}"

        from app.policy import score_release
        good = score_release({"title":"Example.2026.1080p.BluRay.x265","protocol":"torrent","size":8*1024**3,"seeders":25})
        bad = score_release({"title":"Example.2026.CAM.x264","protocol":"torrent","size":2*1024**3,"seeders":1})
        assert good["score"] > bad["score"] and bad["decision"] == "rejected"

    print("PASS: Python, templates, startup, authenticated pages, profile, PWA and release policy smoke tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
