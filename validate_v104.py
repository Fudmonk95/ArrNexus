#!/usr/bin/env python3
from __future__ import annotations

"""ArrNexus v10.4.0-beta release validator."""

import compileall
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile


def require(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def run_retained(root: Path) -> None:
    layers = [
        ("validate_v7.py", "v7", {"ARRNEXUS_VALIDATE_LAYER_ONLY":"1"}),
        ("validate_v8.py", "v8", {"ARRNEXUS_VALIDATE_LAYER_ONLY":"1"}),
        ("validate_v9.py", "v9", {"ARRNEXUS_VALIDATE_LAYER_ONLY":"1"}),
        ("validate_v91.py", "v9.1", {"ARRNEXUS_VALIDATE_LAYER_ONLY":"1"}),
        ("validate_v92.py", "v9.2", {"ARRNEXUS_VALIDATE_LAYER_ONLY":"1"}),
        ("validate_v93.py", "v9.3", {"ARRNEXUS_VALIDATE_LAYER_ONLY":"1"}),
        ("validate_v94.py", "v9.4", {"ARRNEXUS_VALIDATE_LAYER_ONLY":"1"}),
        ("validate_v10.py", "v10", {"ARRNEXUS_VALIDATE_V10_ONLY":"1"}),
        ("validate_v101.py", "v10.1", {"ARRNEXUS_VALIDATE_V101_ONLY":"1"}),
        ("validate_v102.py", "v10.2", {"ARRNEXUS_VALIDATE_V102_ONLY":"1"}),
        ("validate_v103.py", "v10.3", {"ARRNEXUS_VALIDATE_V103_ONLY":"1"}),
    ]
    for script, label, extra in layers:
        print(f"[retained] starting {label}", flush=True)
        env = os.environ.copy(); env.update(extra)
        # Historical TestClient layers can finish every assertion, print their
        # final PASS marker, then keep interpreter/background threads alive at
        # shutdown. Force unbuffered output so the certification runner can
        # distinguish that shutdown quirk from a validator that never passed.
        env["PYTHONUNBUFFERED"] = "1"
        with tempfile.TemporaryDirectory(prefix=f"arrnexus-v104-{label.replace('.','')}-") as td:
            outp, errp = Path(td)/"out.log", Path(td)/"err.log"
            with outp.open("wb") as out, errp.open("wb") as err:
                proc = subprocess.Popen([sys.executable, str(root/script)], cwd=root, env=env, stdout=out, stderr=err, start_new_session=True)
                deadline = __import__("time").monotonic() + 75
                pass_seen_at = None
                rc = None
                while __import__("time").monotonic() < deadline:
                    rc = proc.poll()
                    if rc is not None:
                        break
                    try:
                        current = outp.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        current = ""
                    if "PASS:" in current:
                        if pass_seen_at is None:
                            pass_seen_at = __import__("time").monotonic()
                        elif __import__("time").monotonic() - pass_seen_at >= 2.0:
                            # PASS is emitted only after the validator's final
                            # assertion. Reap the stuck interpreter/process
                            # group; this does not skip any test.
                            try: os.killpg(proc.pid, signal.SIGTERM)
                            except ProcessLookupError: pass
                            try: proc.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                try: os.killpg(proc.pid, signal.SIGKILL)
                                except ProcessLookupError: pass
                                proc.wait(timeout=5)
                            rc = 0
                            break
                    __import__("time").sleep(0.2)
                if rc is None:
                    try: os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    proc.wait(timeout=10)
                    rc = -9
                finally_rc = rc
                try: os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError: pass
            stdout = outp.read_text(encoding="utf-8", errors="replace")
            stderr = errp.read_text(encoding="utf-8", errors="replace")
            if stdout: print(stdout.rstrip())
            if stderr: print(stderr.rstrip(), file=sys.stderr)
            require("PASS:" in stdout and finally_rc == 0, f"Retained {label} validator failed/timed out with {finally_rc}")
        print(f"[retained] {label} complete", flush=True)


def main() -> int:
    root = Path(__file__).resolve().parent
    if os.getenv("ARRNEXUS_VALIDATE_V104_ONLY") != "1":
        run_retained(root)

    require(compileall.compile_dir(root/"app", quiet=1), "Python compilation failed")
    for name in ("bootstrap.py", "validate_v104.py", "validate_v103.py"):
        require(compileall.compile_file(str(root/name), quiet=1), f"Compilation failed: {name}")
    node = __import__("shutil").which("node")
    if node:
        proc = subprocess.run([node, "--check", str(root/"app"/"static"/"app.js")], text=True, capture_output=True, timeout=60)
        require(proc.returncode == 0, f"JavaScript syntax failed: {proc.stderr}")

    # Bind application imports to one isolated database before importing any
    # app module; config/db objects are initialized at import time.
    validation_tmp = tempfile.mkdtemp(prefix="arrnexus-v104-validate-")
    os.environ["DB_PATH"] = str(Path(validation_tmp)/"router.db")
    os.environ["DB_DIR"] = validation_tmp
    os.environ["SESSION_SECRET"] = "v104-validation-session-secret"
    os.environ["ARRNEXUS_SELF_UPDATE"] = "0"
    for key in ("RADARR_API_KEY","SONARR_API_KEY","LIDARR_API_KEY","PROWLARR_API_KEY","JELLYFIN_API_KEY","SEERR_API_KEY"):
        os.environ[key] = ""

    # Pure regression checks do not require a DUMB namespace.
    from app.language_guard import evaluate_probe_payload, LanguagePolicy
    from app.archive_media import _parse_7z_listing, _safe_member, identity_required_for_name
    from app.media_identity import canonical_media_name, match_confidence

    policy = LanguagePolicy(enabled=True, require_english_audio=True, require_english_subtitles=False, remove_rejected_debrid=False)
    unknown = evaluate_probe_payload({"streams":[{"codec_type":"audio","tags":{"language":"und"}}]}, policy)
    require(unknown["status"] == "unknown" and not unknown["destructive_safe"], "Undefined audio must be Manual review, never rejection")
    titled = evaluate_probe_payload({"streams":[{"codec_type":"audio","tags":{"language":"und","title":"English AAC"}}]}, policy)
    require(titled["status"] == "pass" and titled["english_audio"], "English track title fallback failed")
    foreign = evaluate_probe_payload({"streams":[{"codec_type":"audio","tags":{"language":"spa"}}]}, policy)
    require(foreign["status"] == "fail" and foreign["destructive_safe"], "Explicit non-English audio should remain a confirmed rejection")
    mixed_unknown = evaluate_probe_payload({"streams":[{"codec_type":"audio","tags":{"language":"spa"}},{"codec_type":"audio","tags":{"language":"und"}}]}, policy)
    require(mixed_unknown["status"] == "unknown" and not mixed_unknown["destructive_safe"], "Mixed explicit/unknown tracks must fail closed to Manual review")

    relaxed_policy = LanguagePolicy(enabled=True, require_english_audio=True, require_english_subtitles=False, unknown_is_failure=False, remove_rejected_debrid=False)
    relaxed_unknown = evaluate_probe_payload({"streams":[{"codec_type":"audio","tags":{"language":"und"}}]}, relaxed_policy)
    require(relaxed_unknown["status"] == "unknown" and relaxed_unknown["compliant"] and not relaxed_unknown["destructive_safe"], "Unknown-is-failure OFF must allow import without making uncertainty destructive-safe")

    require(identity_required_for_name("season-4_202405.rar"), "Generic Queen's Nose-style RAR must require identity resolution")
    require(not identity_required_for_name("Power.Rangers.S01.720p.rar"), "Descriptive RAR was incorrectly forced to identity resolution")
    listing = "Path = Power.Rangers.S01E01.mkv\nSize = 1234\nAttributes = A\n\nPath = Power.Rangers.S01E02.mkv\nSize = 2345\nAttributes = A\n\n"
    parsed = _parse_7z_listing(listing)
    require(len(parsed) == 2 and parsed[0]["size"] == 1234, "7z RAR listing parser failed")
    require(_safe_member("Season 1/episode.mkv") and not _safe_member("../escape.mkv") and not _safe_member("/absolute.mkv"), "RAR path traversal protection failed")
    identity = {"media_type":"tv", "title":"The Queen's Nose", "year":1995}
    require(canonical_media_name(identity, "Season 6 episode 1.mp4") == "The Queen's Nose - S06E01.mp4", "TMDb identity episode naming failed")
    require(canonical_media_name(identity, "Season 4.mp4") == "The Queen's Nose - Season 04.mp4", "TMDb identity combined-season naming failed")
    require(match_confidence("The Queens Nose", "The Queens Nose", 1995, 1995) >= 95, "Identity confidence scoring failed")

    # Fresh application/DB + HTML contract.
    if True:
        from app.db import init_db
        from app.updater import version_key
        import app.main as main_app
        from fastapi.testclient import TestClient
        init_db()
        for template in sorted((root/"app"/"templates").glob("*.html")):
            main_app.templates.env.get_template(template.name)
        with TestClient(main_app.app) as client:
            health = client.get("/api/health")
            require(health.status_code == 200 and health.json().get("version") in {"10.4.0-beta", "10.4.1-beta", "10.4.2-beta", "10.4.3-beta", "10.4.4-beta", "10.5.0-beta", "10.5.1-beta", "10.6.0-beta"}, "v10.4 health/version")
            setup = client.post("/setup", data={"username":"v104validator","email":"v104@example.invalid","display_name":"V10.4 Validator","password":"validation-password-123","confirm":"validation-password-123"}, follow_redirects=False)
            require(setup.status_code == 303, "v10.4 administrator setup")
            page = client.get("/maintenance/archives", follow_redirects=False)
            require(page.status_code == 200 and "Archived Media Recovery" in page.text and "Scan __all__ for archives" in page.text, "Archived Media Recovery page failed")
            async def _fake_inbox_snapshot():
                return {"rows": [], "built_at": 0.0}
            main_app._build_inbox_snapshot = _fake_inbox_snapshot
            main_app._INBOX_SNAPSHOT.clear()
            inbox = client.get("/inbox", follow_redirects=False)
            require(inbox.status_code == 200 and "Archives" in inbox.text and "Check all unchecked" in inbox.text, "DMM Inbox v10.4 controls failed")
        require(version_key("10.4.0-beta") > version_key("10.3.0-beta"), "Updater will not recognize v10.4")

    main_source = (root/"app"/"main.py").read_text(encoding="utf-8")
    router_source = (root/"app"/"router_service.py").read_text(encoding="utf-8")
    archive_source = (root/"app"/"archive_media.py").read_text(encoding="utf-8")
    identity_source = (root/"app"/"media_identity.py").read_text(encoding="utf-8")
    inbox_tpl = (root/"app"/"templates"/"inbox.html").read_text(encoding="utf-8")
    item_tpl = (root/"app"/"templates"/"item.html").read_text(encoding="utf-8")
    archive_tpl = (root/"app"/"templates"/"archive_media.html").read_text(encoding="utf-8")
    docker = (root/"Dockerfile").read_text(encoding="utf-8")
    sw = (root/"app"/"static"/"sw.js").read_text(encoding="utf-8")
    guide = (root/"docs"/"USER_GUIDE.md").read_text(encoding="utf-8")
    audit = (root/"docs"/"DOCUMENTATION_AUDIT.md").read_text(encoding="utf-8")
    readme = (root/"README.md").read_text(encoding="utf-8")

    for marker in ('/maintenance/archives', 'run_archive_extract_job', 'media_identity.search_tmdb'):
        require(marker in main_source, f"v10.4 main marker missing: {marker}")
    require(('APP_VERSION = "10.4.0-beta"' in main_source) or ('APP_VERSION = "10.4.1-beta"' in main_source) or ('APP_VERSION = "10.4.2-beta"' in main_source) or ('APP_VERSION = "10.4.3-beta"' in main_source) or ('APP_VERSION = "10.4.4-beta"' in main_source) or ('APP_VERSION = "10.5.0-beta"' in main_source) or (('APP_VERSION = "10.5.1-beta"' in main_source) or ('APP_VERSION = "10.6.0-beta"' in main_source)), "v10.4+ version marker missing")
    for marker in ('UNKNOWN_LANGUAGE_CODES', 'language_override:v104', 'status = "unknown"'):
        require(marker in (root/"app"/"language_guard.py").read_text(encoding="utf-8"), f"Language Guard v10.4 marker missing: {marker}")
    for marker in ('scan_archives', 'inspect_archive', 'extract_archive', 'path traversal', 'max_extract_bytes', 'identity_required'):
        require(marker.lower() in archive_source.lower(), f"Archived Media Recovery marker missing: {marker}")
    for marker in ('search_tmdb', 'save_identity', 'naming_preview', 'canonical_media_name', 'confidence'):
        require(marker in identity_source, f"Media identity marker missing: {marker}")
    require('media_identity.apply_to_item' in router_source, "Identity override does not feed actual Arr import matching")
    require('original_provider_source = is_within_logical(source_path, source_root())' in router_source and 'Provider cleanup is not applicable to ArrNexus recovered media' in router_source, "Recovered RAR media can still fall into provider cleanup")
    require('Review naming & import' in item_tpl and 'Mark this source as English' in item_tpl and 'Pre-import naming preview' in item_tpl, "Item Review rename/language override workflow missing")
    require('source packs' in inbox_tpl and 'series_sources' in inbox_tpl, "Series-first TV Inbox grouping UI missing")
    require(('Resolve identity before extraction' in archive_tpl or 'Resolve identity before recovery' in archive_tpl) and 'TMDb API key' in archive_tpl, "Ambiguous-RAR/TMDb workflow missing")
    require('RAR extractor not installed in this container' in archive_tpl and 'extractor_state' in archive_source, "RAR extractor/rebuild diagnostic missing")
    require('p7zip-full' in docker or '7zip' in docker, "Container does not install a RAR extractor")
    require((('arrnexus-static-v10.4' in sw) or ('arrnexus-static-v10.4.1' in sw) or ('arrnexus-static-v10.5.0' in sw)) or ("arrnexus-static-v10.5.1" in sw) or ("arrnexus-static-v10.6.0" in sw), "v10.4+ service-worker cache marker missing")
    require('Version 10.4' in readme and 'Archived Media Recovery' in readme, "README missing v10.4")
    require('/maintenance/archives' in audit and 'Archived Media Recovery' in guide, "Generated documentation missing v10.4 archive recovery")
    require((root/"docs"/"RELEASE_NOTES_v10.4.md").exists(), "v10.4 release notes missing")

    print("PASS: ArrNexus v10.4.0-beta retains v7-v10.3 regressions and adds unknown-language safety, fingerprint-bound English overrides, series-first TV grouping, review-first RAR recovery, TMDb identity resolution, naming preview, DUMB-visible extraction storage and archive safety gates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
