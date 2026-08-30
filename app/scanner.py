from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib
import os
import re
import threading
import time
from .config import settings
from .paths import source_root
from .namespace import view_path, logical_from_view

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".m2ts", ".webm"}
AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".alac"}
EPISODE_RE = re.compile(r"(?i)(?:\bS(?P<s>\d{1,2})E\d{1,3}\b|\b(?P<s2>\d{1,2})x\d{1,3}\b)")
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
RELEASE_NOISE = re.compile(
    r"(?i)\b(4320p|2160p|1080p|720p|576p|480p|bluray|blu[- ]?ray|web[- .]?dl|webrip|hdtv|hdr10\+?|hdr|dv|dolby[ .]?vision|x264|x265|h264|h265|hevc|av1|remux|aac|dts|truehd|atmos|ddp?5?\.?1|proper|repack)\b.*$"
)

QUALITY_PATTERNS = [
    (4320, re.compile(r"(?i)\b(4320p|8k)\b")),
    (2160, re.compile(r"(?i)\b(2160p|4k|uhd)\b")),
    (1080, re.compile(r"(?i)\b1080[pi]?\b")),
    (720, re.compile(r"(?i)\b720[pi]?\b")),
    (576, re.compile(r"(?i)\b576[pi]?\b")),
    (480, re.compile(r"(?i)\b480[pi]?\b")),
]


@dataclass
class ScanItem:
    name: str
    path: str
    media_type: str
    title_guess: str
    year_guess: int | None
    video_count: int
    season_numbers: list[int]
    size_bytes: int
    quality: int
    fingerprint: str

    def dict(self):
        return asdict(self)


def media_files(path: Path | str, exts: set[str]) -> list[Path]:
    logical = Path(path)
    actual = view_path(logical)
    actual_files: list[Path] = []
    if actual.is_file():
        actual_files = [actual] if actual.suffix.lower() in exts else []
    else:
        try:
            for p in actual.rglob("*"):
                if p.is_file() and p.suffix.lower() in exts:
                    actual_files.append(p)
        except (OSError, PermissionError):
            pass
    return [logical_from_view(p) for p in actual_files]


def video_files(path: Path | str) -> list[Path]:
    return media_files(path, VIDEO_EXTS)


def audio_files(path: Path | str) -> list[Path]:
    return media_files(path, AUDIO_EXTS)


def _strip_release_prefix(value: str) -> str:
    text = value.strip()
    # Common DMM/RD torrent names include tracker/site prefixes such as
    # "www.UIndex.org - Movie" or "www 1TamilMV ing - Movie". Those make
    # metadata matching collapse to "www" and produce WW poster fallbacks.
    low = text.lower()
    if low.startswith("www"):
        for marker in (" - ", " – ", " — ", " : "):
            if marker in text:
                prefix, rest = text.split(marker, 1)
                if len(prefix) <= 48 and rest.strip():
                    text = rest.strip()
                    break
    text = re.sub(r"^\s*\[[^\]]{1,40}\]\s*[-_:]*\s*", "", text)
    text = re.sub(r"(?i)^\s*(?:torrentgalaxy|tgx|yts(?:\.mx)?|rarbg|eztv)\s*[-_:]+\s*", "", text)
    return text


def parse_title_year(name: str) -> tuple[str, int | None]:
    raw = _strip_release_prefix(name)
    cleaned = raw.replace("_", " ").replace(".", " ")
    m = YEAR_RE.search(cleaned)
    year = int(m.group(1)) if m else None
    if m:
        title = cleaned[: m.start()]
    else:
        title = RELEASE_NOISE.sub("", cleaned)
    title = re.sub(r"[\[\](){}]+", " ", title)
    # Strip residual release metadata after a clear separator.
    title = re.split(r"\s[-–—]\s(?=(?:1080|720|2160|bluray|web|hdtv|x26|h26))", title, maxsplit=1, flags=re.I)[0]
    title = re.sub(r"\s+", " ", title).strip(" -._,")
    return title or raw or name, year


def quality_from_name(value: str) -> int:
    for resolution, regex in QUALITY_PATTERNS:
        if regex.search(value):
            return resolution
    return 0


def _safe_stat_size(logical_file: Path) -> int:
    try:
        return view_path(logical_file).stat().st_size
    except OSError:
        return 0


def fingerprint_files(files: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(files, key=lambda x: str(x).lower()):
        h.update(str(p).encode("utf-8", errors="replace"))
        h.update(b"\0")
        try:
            st = view_path(p).stat()
            h.update(str(st.st_size).encode())
            h.update(str(int(st.st_mtime)).encode())
        except OSError:
            pass
        h.update(b"\n")
    return h.hexdigest()


def inspect_item(path: Path | str) -> ScanItem:
    logical = Path(path)
    files = video_files(logical)
    seasons = set()
    has_episode = False
    max_quality = quality_from_name(logical.name)
    size = 0
    for f in files:
        m = EPISODE_RE.search(f.name)
        if m:
            has_episode = True
            s = m.group("s") or m.group("s2")
            if s is not None:
                seasons.add(int(s))
        max_quality = max(max_quality, quality_from_name(f.name))
        size += _safe_stat_size(f)
    title, year = parse_title_year(logical.name)
    return ScanItem(
        name=logical.name,
        path=str(logical),
        media_type="tv" if has_episode else "movie",
        title_guess=title,
        year_guess=year,
        video_count=len(files),
        season_numbers=sorted(seasons),
        size_bytes=size,
        quality=max_quality,
        fingerprint=fingerprint_files(files),
    )


_SCAN_CACHE_LOCK = threading.RLock()
_SCAN_CACHE_AT = 0.0
_SCAN_CACHE_ITEMS: list[ScanItem] = []
_SCAN_CACHE_ROOT = ""
_SCAN_CACHE_TTL = 30.0


def invalidate_scan_cache() -> None:
    global _SCAN_CACHE_AT, _SCAN_CACHE_ITEMS, _SCAN_CACHE_ROOT
    with _SCAN_CACHE_LOCK:
        _SCAN_CACHE_AT = 0.0
        _SCAN_CACHE_ITEMS = []
        _SCAN_CACHE_ROOT = ""


def _scan_source_uncached() -> list[ScanItem]:
    logical_root = Path(source_root())
    actual_root = view_path(logical_root)
    if not actual_root.exists():
        return []
    items = []
    try:
        entries = sorted(actual_root.iterdir(), key=lambda x: x.name.lower())
    except OSError:
        return []
    for p in entries:
        logical = logical_root / p.name
        item = inspect_item(logical)
        if item.video_count:
            items.append(item)
    return items


def scan_source(force: bool = False) -> list[ScanItem]:
    """Return the DMM source inventory with a short stale-safe cache.

    DMM folders can contain hundreds of virtual files and calculating every
    fingerprint on every menu click was one of the largest v6 latency sources.
    The source can be changed externally, so the cache is intentionally short.
    """
    global _SCAN_CACHE_AT, _SCAN_CACHE_ITEMS, _SCAN_CACHE_ROOT
    now = time.monotonic()
    root = source_root()
    with _SCAN_CACHE_LOCK:
        if (not force and _SCAN_CACHE_ROOT == root and _SCAN_CACHE_ITEMS and now - _SCAN_CACHE_AT < _SCAN_CACHE_TTL):
            return list(_SCAN_CACHE_ITEMS)
    items = _scan_source_uncached()
    with _SCAN_CACHE_LOCK:
        _SCAN_CACHE_AT = time.monotonic()
        _SCAN_CACHE_ITEMS = list(items)
        _SCAN_CACHE_ROOT = root
    return items


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def episode_season(filename: str) -> int | None:
    m = EPISODE_RE.search(filename)
    if not m:
        return None
    return int(m.group("s") or m.group("s2"))


def human_size(value: int) -> str:
    size = float(value or 0)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"
