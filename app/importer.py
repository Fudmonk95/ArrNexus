from __future__ import annotations
from pathlib import Path
import os
import shutil
from .scanner import video_files, episode_season


class ImportErrorSafe(RuntimeError):
    pass


def ensure_within(path: Path, parent: Path):
    try:
        path.resolve().relative_to(parent.resolve())
    except Exception as exc:
        raise ImportErrorSafe(f"Refusing path outside allowed root: {path}") from exc


def make_symlink(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        if dst.is_symlink() and os.path.realpath(dst) == str(src.resolve()):
            return "exists"
        raise ImportErrorSafe(f"Destination already exists: {dst}")
    os.symlink(str(src.resolve()), str(dst))
    return "created"


def import_movie_source(source: str, destination_dir: str) -> list[str]:
    src = Path(source)
    dest = Path(destination_dir)
    files = video_files(src)
    if not files:
        raise ImportErrorSafe("No video files found")
    dest.mkdir(parents=True, exist_ok=True)
    created = []
    for f in files:
        out = dest / f.name
        make_symlink(f, out)
        created.append(str(out))
    return created


def import_tv_source(source: str, series_path: str) -> list[str]:
    src = Path(source)
    series = Path(series_path)
    files = video_files(src)
    if not files:
        raise ImportErrorSafe("No video files found")
    created = []
    for f in files:
        season = episode_season(f.name)
        if season is None:
            raise ImportErrorSafe(f"Cannot determine season from: {f.name}")
        dest = series / f"Season {season:02d}" / f.name
        make_symlink(f, dest)
        created.append(str(dest))
    return created
