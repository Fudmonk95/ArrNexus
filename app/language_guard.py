from __future__ import annotations

"""Media language verification for ArrNexus.

Language Guard probes media already present in the DUMB/Decypharr namespace
with ffprobe, records the result in ArrNexus' metadata cache and lets the import
pipeline reject a source before creating library symlinks when the configured
language policy is not met.

From v10.1, an administrator can automatically remove an exactly identified
Real-Debrid source after rejection.  Cleanup is deliberately fail-safe: fuzzy or
ambiguous provider matches are never deleted.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Callable
from typing import Any

from .db import cache_get, cache_set, setting_get, setting_set
from .namespace import view_path
from .scanner import video_files
from .process_control import run_cancellable


ENGLISH_CODES = {
    "en", "eng", "english", "en-us", "en-gb", "en-ca", "en-au", "en-nz",
}
SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".sub"}
ENGLISH_NAME_RE = re.compile(r"(?i)(?:^|[._\-\s])(eng|english|en(?:[-_.](?:us|gb|uk|ca|au))?)(?:$|[._\-\s])")
UNKNOWN_LANGUAGE_CODES = {"", "und", "unk", "unknown", "zxx", "mul", "mis"}


@dataclass(frozen=True)
class LanguagePolicy:
    enabled: bool = True
    require_english_audio: bool = True
    require_english_subtitles: bool = False
    require_default_english_audio: bool = False
    unknown_is_failure: bool = True
    auto_upgrade_search: bool = True
    remove_rejected_debrid: bool = False
    max_files: int = 300
    probe_timeout_seconds: int = 20


def _bool_setting(key: str, default: bool) -> bool:
    raw = setting_get(key, "true" if default else "false")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def language_checks_enabled() -> bool:
    return _bool_setting("language.enabled", True)


def set_language_checks_enabled(enabled: bool) -> None:
    # This is the v10.5 user-facing master control. All other Language Guard
    # policy settings and cached results are deliberately retained untouched.
    setting_set("language.enabled", "true" if enabled else "false")


def load_language_policy() -> LanguagePolicy:
    try:
        max_files = max(1, min(1000, int(setting_get("language.max_files", "300") or 300)))
    except Exception:
        max_files = 300
    try:
        timeout = max(5, min(90, int(setting_get("language.probe_timeout", "20") or 20)))
    except Exception:
        timeout = 20
    return LanguagePolicy(
        enabled=language_checks_enabled(),
        require_english_audio=_bool_setting("language.require_english_audio", True),
        require_english_subtitles=_bool_setting("language.require_english_subtitles", False),
        require_default_english_audio=_bool_setting("language.require_default_english_audio", False),
        unknown_is_failure=_bool_setting("language.unknown_is_failure", True),
        auto_upgrade_search=_bool_setting("language.auto_upgrade_search", True),
        remove_rejected_debrid=_bool_setting("language.remove_rejected_debrid", False),
        max_files=max_files,
        probe_timeout_seconds=timeout,
    )


def save_language_policy(
    *,
    enabled: bool,
    require_english_audio: bool,
    require_english_subtitles: bool,
    require_default_english_audio: bool,
    unknown_is_failure: bool,
    auto_upgrade_search: bool,
    remove_rejected_debrid: bool = False,
    max_files: int = 300,
    probe_timeout_seconds: int = 20,
) -> None:
    values = {
        "language.enabled": enabled,
        "language.require_english_audio": require_english_audio,
        "language.require_english_subtitles": require_english_subtitles,
        "language.require_default_english_audio": require_default_english_audio,
        "language.unknown_is_failure": unknown_is_failure,
        "language.auto_upgrade_search": auto_upgrade_search,
        "language.remove_rejected_debrid": remove_rejected_debrid,
    }
    for key, value in values.items():
        setting_set(key, "true" if value else "false")
    setting_set("language.max_files", str(max(1, min(1000, int(max_files)))))
    setting_set("language.probe_timeout", str(max(5, min(90, int(probe_timeout_seconds)))))


def _normalise_language(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "en-us": "en-us", "en-gb": "en-gb", "en-uk": "en-gb",
        "en-au": "en-au", "en-ca": "en-ca", "english": "eng",
    }
    return aliases.get(raw, raw)


def is_english(value: Any) -> bool:
    lang = _normalise_language(value)
    return lang in ENGLISH_CODES or lang.startswith("en-")


def _stream_language(stream: dict) -> str:
    tags = stream.get("tags") or {}
    raw = _normalise_language(tags.get("language") or tags.get("LANGUAGE") or "")
    title = str(tags.get("title") or tags.get("TITLE") or "").strip()
    # Old scene/archive media frequently uses `und` or leaves the language tag
    # blank even when the track title says English. Treat a clear English track
    # title as positive evidence, otherwise preserve unknown metadata as unknown.
    if raw in UNKNOWN_LANGUAGE_CODES and ENGLISH_NAME_RE.search(title):
        return "eng"
    return raw


def _stream_language_unknown(stream: dict) -> bool:
    lang = _stream_language(stream)
    return lang in UNKNOWN_LANGUAGE_CODES


def _stream_default(stream: dict) -> bool:
    disp = stream.get("disposition") or {}
    try:
        return bool(int(disp.get("default") or 0))
    except Exception:
        return bool(disp.get("default"))


def evaluate_probe_payload(payload: dict, policy: LanguagePolicy | None = None, *, external_english_subtitles: bool = False) -> dict:
    """Evaluate one ffprobe JSON payload without treating unknown tags as non-English."""
    policy = policy or load_language_policy()
    streams = payload.get("streams") or []
    audio = [s for s in streams if str(s.get("codec_type") or "").lower() == "audio"]
    subs = [s for s in streams if str(s.get("codec_type") or "").lower() == "subtitle"]

    audio_languages = sorted({x for x in (_stream_language(s) for s in audio) if x and x not in UNKNOWN_LANGUAGE_CODES})
    subtitle_languages = sorted({x for x in (_stream_language(s) for s in subs) if x and x not in UNKNOWN_LANGUAGE_CODES})
    audio_unknown = any(_stream_language_unknown(s) for s in audio)
    subtitle_unknown = any(_stream_language_unknown(s) for s in subs)

    english_audio = any(is_english(x) for x in audio_languages)
    english_subtitles = external_english_subtitles or any(is_english(x) for x in subtitle_languages)
    default_audio = [s for s in audio if _stream_default(s)]
    default_english_audio = any(is_english(_stream_language(s)) for s in default_audio)
    default_audio_unknown = bool(default_audio) and any(_stream_language_unknown(s) for s in default_audio)

    missing: list[str] = []
    metadata_unknown = False

    if policy.require_english_audio and not english_audio:
        missing.append("English audio")
        # No audio at all is a confirmed absence. An unlabelled/undefined audio
        # stream is not evidence of a foreign-language source.
        if audio and (audio_unknown or not audio_languages):
            metadata_unknown = True
    if policy.require_default_english_audio and not default_english_audio:
        missing.append("default English audio")
        if default_audio_unknown or (default_audio and not any(_stream_language(s) not in UNKNOWN_LANGUAGE_CODES for s in default_audio)):
            metadata_unknown = True
    if policy.require_english_subtitles and not english_subtitles:
        missing.append("English subtitles")
        if not subs or subtitle_unknown or not subtitle_languages:
            metadata_unknown = True

    if metadata_unknown:
        status = "unknown"
        missing.append("language metadata is unknown")
    elif missing:
        status = "fail"
    else:
        status = "pass"

    # Destructive-safe means the required stream metadata was explicit enough
    # to prove a policy failure. Any unknown/mixed/unlabelled stream blocks it.
    confirmed_policy_failure = status == "fail"
    compliant = status == "pass" or (status == "unknown" and not policy.unknown_is_failure)
    return {
        "status": status,
        "compliant": compliant,
        "destructive_safe": bool(confirmed_policy_failure and not metadata_unknown),
        "english_audio": english_audio,
        "english_subtitles": english_subtitles,
        "default_english_audio": default_english_audio,
        "audio_languages": audio_languages,
        "subtitle_languages": subtitle_languages,
        "audio_unknown": bool(audio_unknown),
        "subtitle_unknown": bool(subtitle_unknown),
        "audio_streams": len(audio),
        "subtitle_streams": len(subs),
        "external_english_subtitles": bool(external_english_subtitles),
        "missing": sorted(set(missing)),
    }


def _matching_external_english_subtitle(video_logical: Path) -> bool:
    """Look for a sidecar subtitle that clearly belongs to this video."""
    try:
        parent = view_path(video_logical).parent
        stem = video_logical.stem.lower()
        for p in parent.iterdir():
            if not p.is_file() or p.suffix.lower() not in SUBTITLE_EXTS:
                continue
            name = p.name.lower()
            # Match either the complete video stem, or an obvious English sidecar
            # in a single-video movie folder.
            if name.startswith(stem + ".") and ENGLISH_NAME_RE.search(name):
                return True
    except Exception:
        pass
    return False


def _ffprobe(logical_file: Path, timeout_seconds: int, cancel_check: Callable[[], bool] | None = None) -> dict:
    actual = view_path(logical_file)
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=index,codec_type:stream_tags=language,title:stream_disposition=default,forced",
        "-of", "json", str(actual),
    ]
    try:
        proc = run_cancellable(cmd, capture_output=True, text=True, timeout=timeout_seconds, check=False, cancel_check=cancel_check)
    except FileNotFoundError as exc:
        raise RuntimeError("ffprobe is not installed in the ArrNexus container") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffprobe timed out after {timeout_seconds}s") from exc
    if proc.returncode != 0:
        detail = " ".join((proc.stderr or proc.stdout or "ffprobe failed").split())[:400]
        raise RuntimeError(detail)
    try:
        payload = json.loads(proc.stdout or "{}")
    except Exception as exc:
        raise RuntimeError("ffprobe returned invalid JSON") from exc
    return payload if isinstance(payload, dict) else {}


def _policy_fingerprint(policy: LanguagePolicy) -> str:
    raw = json.dumps(policy.__dict__, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cache_key(source_path: str, fingerprint: str, policy: LanguagePolicy, selection_key: str = "") -> str:
    ident = hashlib.sha256(source_path.encode("utf-8", errors="replace")).hexdigest()[:24]
    fp = (fingerprint or "nofingerprint")[:32]
    selected = f":sel:{selection_key[:20]}" if selection_key else ""
    return f"language:v105:{ident}:{fp}:{_policy_fingerprint(policy)}{selected}"


def _override_key(source_path: str, fingerprint: str) -> str:
    ident = hashlib.sha256(source_path.encode("utf-8", errors="replace")).hexdigest()[:24]
    return f"language_override:v104:{ident}:{(fingerprint or 'nofingerprint')[:32]}"


def language_override(source_path: str, fingerprint: str) -> dict | None:
    row = cache_get(_override_key(source_path, fingerprint))
    if isinstance(row, dict) and row.get("english") is True:
        return row
    return None


def set_language_override(source_path: str, fingerprint: str, *, english: bool, actor: str = "administrator") -> None:
    cache_set(_override_key(source_path, fingerprint), {
        "english": bool(english),
        "actor": actor,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })

def cached_language_result(source_path: str, fingerprint: str = "") -> dict | None:
    policy = load_language_policy()
    if not policy.enabled:
        return {
            "status": "disabled", "compliant": True, "enabled": False,
            "summary": "Language Checks OFF — imports will bypass Language Guard", "files": [], "missing": [],
        }
    row = cache_get(_cache_key(source_path, fingerprint, policy))
    return row if isinstance(row, dict) else None


def inspect_source_languages(
    source_path: str, fingerprint: str = "", force: bool = False,
    *, selected_files: list[str] | None = None, cancel_check: Callable[[], bool] | None = None,
) -> dict:
    """Probe every video in a DMM source up to the configured safety ceiling.

    If a source exceeds max_files it is deliberately *not* considered compliant;
    with the default strict policy the uninspected remainder makes the result
    fail rather than silently allowing unverified episodes into the library.
    """
    policy = load_language_policy()
    if not policy.enabled:
        return {
            "status": "disabled", "compliant": True, "enabled": False,
            "summary": "Language Checks OFF — imports will bypass Language Guard", "files": [], "missing": [],
        }
    override = language_override(source_path, fingerprint)
    if override:
        return {
            "status": "pass", "compliant": True, "enabled": True,
            "destructive_safe": False, "manual_override": True,
            "summary": "English audio confirmed by administrator for this exact source fingerprint",
            "files": [], "missing": [], "file_count": len(video_files(Path(source_path))),
            "checked_count": 0, "truncated": False, "errors": [],
            "checked_at": override.get("updated_at"),
        }

    requested = [str(x) for x in (selected_files or []) if str(x)]
    selection_key = hashlib.sha256("\n".join(sorted(requested)).encode("utf-8", errors="replace")).hexdigest() if requested else ""
    key = _cache_key(source_path, fingerprint, policy, selection_key)
    if not force:
        cached = cache_get(key)
        if isinstance(cached, dict):
            return cached

    all_files = video_files(Path(source_path))
    if requested:
        requested_set = {str(Path(x)) for x in requested}
        files = [x for x in all_files if str(x) in requested_set]
    else:
        files = all_files
    if not files:
        result = {
            "status": "unknown", "compliant": False, "enabled": True,
            "destructive_safe": False,
            "summary": "Manual review required: no video files were available for language verification", "files": [],
            "missing": ["video media"], "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        cache_set(key, result)
        return result

    selected = files[: policy.max_files]
    rows: list[dict] = []
    errors: list[str] = []
    for logical in selected:
        try:
            payload = _ffprobe(logical, policy.probe_timeout_seconds, cancel_check=cancel_check)
            evaluated = evaluate_probe_payload(
                payload, policy, external_english_subtitles=_matching_external_english_subtitle(logical)
            )
            rows.append({"path": str(logical), "name": logical.name, **evaluated})
        except Exception as exc:
            rows.append({
                "path": str(logical), "name": logical.name, "status": "unknown",
                "compliant": False, "destructive_safe": False,
                "english_audio": False, "english_subtitles": False,
                "audio_languages": [], "subtitle_languages": [], "missing": ["language probe failed"],
                "error": str(exc)[:500],
            })
            errors.append(f"{logical.name}: {str(exc)[:160]}")

    truncated = len(files) > len(selected)
    failed = [r for r in rows if r.get("status") == "fail"]
    unknown = [r for r in rows if r.get("status") == "unknown"]
    missing = sorted({m for r in rows for m in (r.get("missing") or [])})
    if truncated:
        missing.append(f"{len(files)-len(selected)} unverified file(s) above scan limit")

    # Any uncertainty anywhere in a multi-file source takes precedence over a
    # nominal explicit failure elsewhere.  This prevents a season pack with a
    # mixture of undefined/unlabelled tracks and suspicious language tags from
    # being promoted to a destructive-safe rejection.  Only a fully inspected
    # source with no unknown/probe-failed members can become ``fail``.
    if unknown or truncated or errors:
        status = "unknown"
    elif failed:
        status = "fail"
    else:
        status = "pass"
    compliant = status == "pass" or (status == "unknown" and not policy.unknown_is_failure)
    destructive_safe = bool(status == "fail" and not errors and not truncated and failed and all(bool(r.get("destructive_safe")) for r in failed))
    if status == "pass":
        verified = "English audio" + (" + subtitles" if policy.require_english_subtitles else "")
        summary = f"{verified} verified on {len(rows)} file(s)"
    elif status == "unknown":
        action = "import allowed by current policy" if not policy.unknown_is_failure else "import blocked until reviewed"
        summary = f"Manual review required: language verification incomplete on {len(rows)} of {len(files)} file(s); {action}"
    else:
        bits = ", ".join(missing) if missing else "language policy not met"
        summary = f"Language Guard blocked source: {bits}"

    result = {
        "status": status,
        "compliant": compliant,
        "destructive_safe": destructive_safe,
        "enabled": True,
        "summary": summary,
        "missing": missing,
        "files": rows,
        "file_count": len(files),
        "checked_count": len(rows),
        "truncated": truncated,
        "errors": errors,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "policy": {
            "require_english_audio": policy.require_english_audio,
            "require_english_subtitles": policy.require_english_subtitles,
            "require_default_english_audio": policy.require_default_english_audio,
            "unknown_is_failure": policy.unknown_is_failure,
            "auto_upgrade_search": policy.auto_upgrade_search,
            "remove_rejected_debrid": policy.remove_rejected_debrid,
        },
    }
    cache_set(key, result)
    return result


def result_badge(result: dict | None) -> tuple[str, str]:
    if not result:
        return "unchecked", "Language unchecked"
    status = str(result.get("status") or "unchecked")
    if status == "pass":
        if result.get("manual_override"):
            return "pass", "English ✓ · admin confirmed"
        return "pass", "English ✓" + (" · subs ✓" if result.get("english_subtitles") else "")
    if status == "fail":
        missing = [str(x).lower() for x in (result.get("missing") or [])]
        if result.get("errors") or any("probe failed" in x for x in missing):
            return "probe_failed", "Language check failed"
        return "fail", "Language rejected"
    if status == "disabled":
        return "disabled", "Language Guard off"
    return "unknown", "Manual review"
