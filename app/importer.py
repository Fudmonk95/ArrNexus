from __future__ import annotations
from pathlib import Path
import os
from .scanner import video_files, episode_season
from .namespace import view_path


class ImportErrorSafe(RuntimeError):
    pass


def make_symlink(src_logical: Path, dst_logical: Path):
    """Create a symlink inside DUMB's namespace view while storing a DUMB-visible target."""
    src_actual = view_path(src_logical)
    dst_actual = view_path(dst_logical)

    if not src_actual.exists():
        raise ImportErrorSafe(f"Source is unavailable: {src_logical}")

    dst_actual.parent.mkdir(parents=True, exist_ok=True)
    if dst_actual.exists() or dst_actual.is_symlink():
        if dst_actual.is_symlink():
            current = os.readlink(dst_actual)
            if current == str(src_logical):
                return "exists"
        raise ImportErrorSafe(f"Destination already exists: {dst_logical}")

    # IMPORTANT: write /mnt/debrid/... into the symlink, not /proc/<pid>/root/...
    os.symlink(str(src_logical), str(dst_actual))
    return "created"


def ensure_logical_dir(path: Path):
    view_path(path).mkdir(parents=True, exist_ok=True)


def import_movie_source(source: str, destination_dir: str) -> list[str]:
    src = Path(source)
    dest = Path(destination_dir)
    files = video_files(src)
    if not files:
        raise ImportErrorSafe("No video files found")
    ensure_logical_dir(dest)
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
