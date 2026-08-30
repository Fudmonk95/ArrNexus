from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import re
from typing import Iterable
from .config import settings

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".m2ts", ".webm"}
EPISODE_RE = re.compile(r"(?i)(?:\bS(?P<s>\d{1,2})E\d{1,3}\b|\b(?P<s2>\d{1,2})x\d{1,3}\b)")
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
RELEASE_NOISE = re.compile(
    r"(?i)\b(2160p|1080p|720p|480p|bluray|blu[- ]?ray|web[- .]?dl|webrip|hdr|dv|x264|x265|h264|h265|hevc|av1|remux|aac|dts|ddp?5?\.?1|proper|repack)\b.*$"
)


@dataclass
class ScanItem:
    name: str
    path: str
    media_type: str
    title_guess: str
    year_guess: int | None
    video_count: int
    season_numbers: list[int]

    def dict(self):
        return asdict(self)


def video_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in VIDEO_EXTS else []
    out = []
    try:
        for p in path.rglob("*"):
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
                out.append(p)
    except (OSError, PermissionError):
        pass
    return out


def parse_title_year(name: str) -> tuple[str, int | None]:
    cleaned = name.replace("_", " ").replace(".", " ")
    m = YEAR_RE.search(cleaned)
    year = int(m.group(1)) if m else None
    if m:
        title = cleaned[: m.start()]
    else:
        title = RELEASE_NOISE.sub("", cleaned)
    title = re.sub(r"[\[\](){}]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" -._")
    return title or name, year


def inspect_item(path: Path) -> ScanItem:
    files = video_files(path)
    seasons = set()
    has_episode = False
    for f in files:
        m = EPISODE_RE.search(f.name)
        if m:
            has_episode = True
            s = m.group("s") or m.group("s2")
            if s is not None:
                seasons.add(int(s))
    title, year = parse_title_year(path.name)
    return ScanItem(
        name=path.name,
        path=str(path),
        media_type="tv" if has_episode else "movie",
        title_guess=title,
        year_guess=year,
        video_count=len(files),
        season_numbers=sorted(seasons),
    )


def scan_source() -> list[ScanItem]:
    root = Path(settings.source_root)
    if not root.exists():
        return []
    items = []
    for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
        item = inspect_item(p)
        if item.video_count:
            items.append(item)
    return items


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def episode_season(filename: str) -> int | None:
    m = EPISODE_RE.search(filename)
    if not m:
        return None
    return int(m.group("s") or m.group("s2"))
