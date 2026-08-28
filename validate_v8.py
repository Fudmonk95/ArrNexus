#!/usr/bin/env python3
"""ArrNexus v8.0.0-beta release validator.

The validator intentionally runs the complete v7 regression suite first, then
exercises the AIOStreams v8 bridge against a deterministic local HTTP server.
No live Arr, Spotify, Real-Debrid, NzbDAV or AIOStreams credentials are used.
"""
from __future__ import annotations

import base64
import compileall
import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def require(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def run_v7(root: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(root / "validate_v7.py")],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=150,
    )
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)
    require(proc.returncode == 0, f"Preserved v7 validator failed with exit code {proc.returncode}")


class AIOState:
    def __init__(self):
        self.user = "validation-user"
        self.password = "validation-password"
        self.encrypted = "validation-encrypted-password"
        self.puts = 0
        self.config = {
            "services": [
                {
                    "id": "nzbdav",
                    "enabled": False,
                    "credentials": {
                        "apiKey": "existing-nzbdav-secret",
                        "username": "existing-user",
                    },
                },
                {"id": "realdebrid", "enabled": False, "credentials": {}},
            ],
            "presets": [],
            "unrelated": {"keep": True, "nested": {"operatorOwned": "preserve-me"}},
        }


class AIOHandler(BaseHTTPRequestHandler):
    server_version = "AIOStreamsValidation/1"

    def log_message(self, *_args):
        pass

    @property
    def state(self) -> AIOState:
        return self.server.aio_state  # type: ignore[attr-defined]

    def _json(self, code: int, payload: dict):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _auth_ok(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
            user, password = decoded.split(":", 1)
        except Exception:
            return False
        return user == self.state.user and password in {self.state.password, self.state.encrypted}

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/v1/status":
            return self._json(200, {"success": True, "detail": "Validation AIOStreams online", "error": None, "data": {"version": "2.validation"}})
        if parsed.path == "/api/v1/user":
            if not self._auth_ok():
                return self._json(401, {"success": False, "error": {"code": "UNAUTHORIZED", "message": "Invalid credentials"}, "data": None})
            return self._json(200, {
                "success": True,
                "detail": "User details retrieved successfully",
                "error": None,
                "data": {
                    "userData": copy.deepcopy(self.state.config),
                    "encryptedPassword": self.state.encrypted,
                },
            })
        if parsed.path == "/api/v1/search":
            if not self._auth_ok():
                return self._json(401, {"success": False, "error": {"message": "Invalid credentials"}, "data": None})
            q = parse_qs(parsed.query)
            return self._json(200, {
                "success": True,
                "detail": None,
                "error": None,
                "data": {
                    "filtered": 0,
                    "results": [{
                        "name": "Validation Release",
                        "url": "https://playback.example.invalid/private/stream?token=super-secret-query",
                        "infoHash": "0123456789abcdef",
                        "requestHeaders": {"Authorization": "Bearer secret", "Cookie": "sid=secret"},
                        "behaviorHints": {
                            "proxyHeaders": {
                                "request": {"Authorization": "Bearer another-secret"},
                                "response": {"Set-Cookie": "secret-cookie"},
                            },
                            "videoHash": "not-a-secret",
                        },
                        "description": f"type={q.get('type', [''])[0]} id={q.get('id', [''])[0]}",
                    }],
                    "statistics": [],
                    "errors": [],
                },
            })
        return self._json(404, {"success": False, "error": {"message": "not found"}, "data": None})

    def do_PUT(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/v1/user":
            return self._json(404, {"success": False, "error": {"message": "not found"}, "data": None})
        if not self._auth_ok():
            return self._json(401, {"success": False, "error": {"message": "Invalid credentials"}, "data": None})
        length = int(self.headers.get("Content-Length", "0") or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json(400, {"success": False, "error": {"message": "bad json"}, "data": None})
        config = payload.get("config")
        if not isinstance(config, dict):
            return self._json(400, {"success": False, "error": {"message": "full config required"}, "data": None})
        self.state.puts += 1
        self.state.config = copy.deepcopy(config)
        return self._json(200, {
            "success": True,
            "detail": "User updated successfully",
            "error": None,
            "data": {"uuid": self.state.user, "userData": copy.deepcopy(self.state.config)},
        })


def main() -> int:
    root = Path(__file__).resolve().parent
    require((root / "validate_v7.py").is_file(), "Preserved validate_v7.py is missing")
    require(compileall.compile_dir(root / "app", quiet=1), "Python compilation failed")

    # Full prior-release regression suite first.
    run_v7(root)

    with tempfile.TemporaryDirectory(prefix="arrnexus-v8-validate-") as tmp:
        os.environ["DB_PATH"] = str(Path(tmp) / "router.db")
        os.environ["DB_DIR"] = tmp
        os.environ["SESSION_SECRET"] = "validation-only-v8-session-secret"

        from app.db import init_db, setting_get, setting_set
        from app.connections import save_connection as save_arr_connection
        init_db()

        import app.aiostreams as aio

        # Pure merge tests: no mutation, unrelated data preserved, conservative
        # credential reuse, Prowlarr URL+key reuse and automatic source/service semantics.
        original = {
            "services": [
                {"id": "nzbdav", "enabled": False, "credentials": {"apiKey": "existing-nzbdav-secret", "username": "existing-user"}},
                {"id": "realdebrid", "enabled": False, "credentials": {}},
            ],
            "presets": [],
            "unrelated": {"keep": True},
        }
        integrations = {
            "prowlarr": {"url": "http://prowlarr.invalid:9696", "has_api_key": True, "api_key": "validation-prowlarr-secret"},
            "realdebrid": {"available": True, "api_key": "validation-rd-secret"},
            "nzbdav": {"available": True, "credentials": {"url": "http://nzbdav.invalid:3000", "apiKey": "replacement-should-not-win", "password": "new-password-only-if-missing"}},
        }
        before = copy.deepcopy(original)
        plan = aio.merge_autowire(original, integrations, wire_prowlarr=True, wire_realdebrid=True, wire_nzbdav=True)
        merged = plan["config"]
        require(original == before, "Auto-Wire mutated the source config")
        require(merged.get("unrelated", {}).get("keep") is True, "Unrelated AIOStreams settings were lost")
        services = {x.get("id"): x for x in merged.get("services", []) if isinstance(x, dict)}
        require(services["nzbdav"]["credentials"].get("apiKey") == "existing-nzbdav-secret", "Existing NzbDAV API key was overwritten")
        require(services["nzbdav"]["credentials"].get("username") == "existing-user", "Existing NzbDAV username was overwritten")
        require(services["nzbdav"]["credentials"].get("url") == "http://nzbdav.invalid:3000", "Missing NzbDAV URL was not safely reused")
        require(services["nzbdav"]["credentials"].get("password") == "new-password-only-if-missing", "Missing NzbDAV password was not safely reused")
        require(services["realdebrid"]["credentials"].get("apiKey") == "validation-rd-secret", "Real-Debrid key was not merged")
        preset = next((x for x in merged.get("presets", []) if x.get("type") == "prowlarr"), None)
        require(preset is not None and preset.get("enabled") is True, "Prowlarr preset was not created")
        opts = preset.get("options") or {}
        require(opts.get("prowlarrUrl") == "http://prowlarr.invalid:9696", "Prowlarr URL merge failed")
        require(opts.get("prowlarrApiKey") == "validation-prowlarr-secret", "Prowlarr API-key merge failed")
        require(opts.get("sources") == [], "New Prowlarr preset must allow torrent + usenet")
        require("services" not in opts, "New Prowlarr preset should preserve automatic service selection")

        explicit = copy.deepcopy(original)
        explicit["services"].append({"id": "aiostreams", "enabled": True, "credentials": {}})
        explicit["presets"] = [{"type": "prowlarr", "enabled": True, "options": {"name": "Existing", "services": ["aiostreams"], "sources": ["torrent"]}}]
        plan2 = aio.merge_autowire(explicit, integrations, wire_prowlarr=True, wire_realdebrid=True, wire_nzbdav=True)
        opts2 = plan2["config"]["presets"][0]["options"]
        require("aiostreams" in opts2.get("services", []), "Existing explicit Prowlarr service allow-list was not preserved")
        require("realdebrid" in opts2.get("services", []) and "nzbdav" in opts2.get("services", []), "Explicit Prowlarr service allow-list was not conservatively extended")
        require(opts2.get("sources") == ["torrent"], "Existing explicit Prowlarr source selection was overwritten")

        masked = json.dumps(aio.sanitize_for_display(merged))
        for secret in ("validation-rd-secret", "existing-nzbdav-secret", "validation-prowlarr-secret", "new-password-only-if-missing"):
            require(secret not in masked, f"Secret masking regression: {secret}")
        search_safe = json.dumps(aio.safe_search_payload({"results": [{"url": "https://stream.invalid/secret", "requestHeaders": {"Authorization": "Bearer secret"}, "behaviorHints": {"proxyHeaders": {"Cookie": "bad"}}}]}))
        require("stream.invalid" not in search_safe and "Bearer secret" not in search_safe and "Cookie" not in search_safe, "AIOStreams search diagnostics leaked playback URL/header content")
        require("playback-url-redacted" in search_safe, "Search redaction marker missing")

        # Configure deterministic local AIOStreams plus ArrNexus integration values.
        state = AIOState()
        server = ThreadingHTTPServer(("127.0.0.1", 0), AIOHandler)
        server.aio_state = state  # type: ignore[attr-defined]
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            aio.save_connection(f"http://127.0.0.1:{port}", state.user, state.password)
            save_arr_connection("prowlarr", "http://prowlarr.invalid:9696", "validation-prowlarr-secret")
            setting_set("realdebrid.api_key", "validation-rd-secret", True)
            setting_set("connector.nzbdav.url", "http://nzbdav.invalid:3000", False)
            setting_set("connector.nzbdav.api_key", "validation-nzbdav-discovered", True)

            import asyncio
            verified = asyncio.run(aio.verify())
            require(verified.get("ok") and verified.get("encrypted_password_saved"), "Live-mock AIOStreams User API verification failed")
            require(setting_get(aio.ENCRYPTED_PASSWORD_KEY) == state.encrypted, "encryptedPassword was not persisted privately")

            # Stale-preview protection: remote config changes after preview -> zero PUTs and zero backups.
            current = asyncio.run(aio.get_user(raw=True))["userData"]
            stale_digest = aio.config_digest(current)
            state.config["remoteChangedAfterPreview"] = True
            before_puts = state.puts
            before_backups = len(aio.list_backups())
            try:
                asyncio.run(aio.apply_autowire(stale_digest, wire_prowlarr=True, wire_realdebrid=True, wire_nzbdav=True))
                raise AssertionError("Stale preview unexpectedly applied")
            except aio.AIOStreamsError as exc:
                require("changed after the preview" in str(exc), "Stale-preview error message regression")
            require(state.puts == before_puts, "Stale preview caused an AIOStreams PUT")
            require(len(aio.list_backups()) == before_backups, "Stale preview created a misleading pre-write backup")

            # Successful write: full merge, unrelated config retained, private backup first.
            fresh = asyncio.run(aio.get_user(raw=True))["userData"]
            fresh_digest = aio.config_digest(fresh)
            result = asyncio.run(aio.apply_autowire(fresh_digest, wire_prowlarr=True, wire_realdebrid=True, wire_nzbdav=True))
            require(not result.get("no_change"), "Expected Auto-Wire changes were not applied")
            require(state.puts == before_puts + 1, "Successful Auto-Wire did not make exactly one PUT")
            backup = result.get("backup") or {}
            require(backup.get("name"), "Pre-write AIOStreams backup was not created")
            backup_path = Path(tmp) / "aiostreams-backups" / backup["name"]
            require(backup_path.is_file(), "Pre-write backup file missing")
            require((backup_path.stat().st_mode & 0o777) == 0o600, "AIOStreams backup file permissions are not 0600")
            require(state.config.get("unrelated", {}).get("nested", {}).get("operatorOwned") == "preserve-me", "Successful PUT lost unrelated configuration")
            svc = {x.get("id"): x for x in state.config.get("services", []) if isinstance(x, dict)}
            require(svc["nzbdav"]["credentials"].get("apiKey") == "existing-nzbdav-secret", "Successful PUT overwrote existing NzbDAV credentials")
            require(svc["nzbdav"]["credentials"].get("url") == "http://nzbdav.invalid:3000", "Successful PUT did not fill confirmed NzbDAV URL")
            require(svc["realdebrid"]["credentials"].get("apiKey") == "validation-rd-secret", "Successful PUT did not wire Real-Debrid")

            # Search API redaction through the actual AIOStreams HTTP client.
            result_search = asyncio.run(aio.search("movie", "tt1234567", True))
            safe = json.dumps(aio.safe_search_payload(result_search))
            require("playback.example.invalid" not in safe and "super-secret-query" not in safe, "Search result leaked playback URL")
            require("Bearer secret" not in safe and "secret-cookie" not in safe, "Search result leaked headers")

            # Rollback: create safety backup of current state before restoring selected backup.
            count_before_rollback = len(aio.list_backups())
            rollback_result = asyncio.run(aio.rollback(backup["name"]))
            require(rollback_result.get("restored") == backup["name"], "Rollback did not report selected backup")
            require(len(aio.list_backups()) == count_before_rollback + 1, "Rollback did not create a pre-rollback safety backup")
            require(state.config.get("remoteChangedAfterPreview") is True, "Rollback did not restore selected pre-write configuration")

            # Import the real app after DB/environment setup and smoke the new admin routes.
            from fastapi.testclient import TestClient
            import app.main as main_app
            for template in sorted((root / "app" / "templates").glob("*.html")):
                main_app.templates.env.get_template(template.name)

            with TestClient(main_app.app) as client:
                setup = client.post("/setup", data={
                    "username": "v8validator",
                    "email": "v8@example.invalid",
                    "display_name": "V8 Validator",
                    "password": "validation-password-123",
                    "confirm": "validation-password-123",
                }, follow_redirects=False)
                require(setup.status_code == 303, "v8 fresh admin setup")
                page = client.get("/aiostreams", follow_redirects=False)
                require(page.status_code == 200 and "Auto-Wire preview" in page.text and "Backups & rollback" in page.text, "AIOStreams admin page")
                status_api = client.get("/api/aiostreams/status")
                require(status_api.status_code == 200 and "authenticated" in status_api.json(), "AIOStreams bridge status API")
                search_api = client.get("/api/aiostreams/search?type=movie&id=tt1234567")
                require(search_api.status_code == 200, "AIOStreams search API route")
                search_text = search_api.text
                require("playback.example.invalid" not in search_text and "Bearer secret" not in search_text, "AIOStreams route leaked playback URL/header")

                # Non-admin must not be able to use the bridge even if they know the URL.
                from app.db import create_user
                create_user("normaluser", "normal@example.invalid", "Normal", "validation-password-456", "user")
                client.get("/logout")
                login = client.post("/login", data={"username": "normaluser", "password": "validation-password-456"}, follow_redirects=False)
                require(login.status_code == 303, "normal-user login")
                denied = client.get("/aiostreams", follow_redirects=False)
                require(denied.status_code == 403, "AIOStreams page is not administrator-only")
        finally:
            server.shutdown()
            server.server_close()

    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    base = (root / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    template = (root / "app" / "templates" / "aiostreams.html").read_text(encoding="utf-8")
    module_source = (root / "app" / "aiostreams.py").read_text(encoding="utf-8")
    require("ARRNEXUS V8 AIOSTREAMS ROUTES" in main_source, "v8 route block missing")
    for route in ("/aiostreams", "/aiostreams/preview", "/aiostreams/apply", "/aiostreams/rollback", "/api/aiostreams/status", "/api/aiostreams/search"):
        require(route in main_source, f"Missing v8 route: {route}")
    require("/aiostreams" in base, "AIOStreams sidebar navigation missing")
    require("Auto-Wire preview" in template and "Backups & rollback" in template, "AIOStreams UI sections missing")
    require("/api/v1/user" in module_source and '"PUT"' in module_source, "AIOStreams full User API update path missing")
    require('create_backup(existing, "before-autowire")' in module_source, "AIOStreams pre-write backup missing")
    require('create_backup(current["userData"], "before-rollback")' in module_source, "AIOStreams pre-rollback backup missing")
    require('APP_VERSION = \"9.' in main_source or 'APP_VERSION = \"8.0.0-beta\"' in main_source, "v8/v9 beta version string missing")

    print("PASS: ArrNexus v8 regression suite retained: v7 regressions + AIOStreams full-config preview/apply/backup/rollback/search integration")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
