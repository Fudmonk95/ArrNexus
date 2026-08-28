from __future__ import annotations
from pathlib import Path
import json
import os
from .scanner import video_files, episode_season, EPISODE_RE
from .namespace import view_path, logical_from_view
from .config import settings
from .paths import movie_roots, tv_roots, lidarr_root, source_root


class ImportErrorSafe(RuntimeError):
    pass


def make_symlink(src_logical: Path, dst_logical: Path):
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

    # Store the DUMB-visible /mnt/debrid target, never /proc/<pid>/root/...
    os.symlink(str(src_logical), str(dst_actual))
    return "created"


def ensure_logical_dir(path: Path):
    view_path(path).mkdir(parents=True, exist_ok=True)


def _safe_media_name(value: str) -> str:
    # Keep normal punctuation but avoid path separators/control characters.
    return "".join(ch for ch in value if ch not in "\\/:*?\"<>|" and ord(ch) >= 32).strip().rstrip(".")


def import_movie_source(source: str, destination_dir: str, canonical_title: str = "", year: int | None = None) -> list[str]:
    src = Path(source)
    dest = Path(destination_dir)
    files = video_files(src)
    if not files:
        raise ImportErrorSafe("No video files found")
    ensure_logical_dir(dest)
    created = []
    base = _safe_media_name(canonical_title or dest.name)
    if year and f"({year})" not in base:
        base = f"{base} ({year})"
    for idx, f in enumerate(files, start=1):
        suffix = f.suffix.lower() or f.suffix
        clean = f"{base}{suffix}" if len(files) == 1 else f"{base} - Part {idx:02d}{suffix}"
        out = dest / clean
        result = make_symlink(f, out)
        if result in {"created", "exists"}:
            created.append(str(out))
    return created


def import_tv_source(source: str, series_path: str, canonical_title: str = "") -> list[str]:
    src = Path(source)
    series = Path(series_path)
    files = video_files(src)
    if not files:
        raise ImportErrorSafe("No video files found")
    created = []
    show = _safe_media_name(canonical_title or series.name)
    used = set()
    for idx, f in enumerate(files, start=1):
        season = episode_season(f.name)
        if season is None:
            raise ImportErrorSafe(f"Cannot determine season from: {f.name}")
        m = EPISODE_RE.search(f.name)
        token = m.group(0).upper().replace("X", "x") if m else f"S{season:02d}E{idx:02d}"
        clean = f"{show} - {token}{f.suffix.lower()}"
        if clean.lower() in used:
            clean = f"{show} - {token} - {idx:02d}{f.suffix.lower()}"
        used.add(clean.lower())
        dest = series / f"Season {season:02d}" / clean
        result = make_symlink(f, dest)
        if result in {"created", "exists"}:
            created.append(str(dest))
    return created


def unlink_created(paths: list[str]) -> tuple[int, list[str]]:
    removed = 0
    errors = []
    for logical in paths:
        try:
            actual = view_path(logical)
            if actual.is_symlink():
                actual.unlink()
                removed += 1
                # remove empty season/movie dirs without touching non-empty data
                parent = actual.parent
                for _ in range(2):
                    try:
                        if parent != view_path(settings.dumb_root) and parent.is_dir() and not any(parent.iterdir()):
                            parent.rmdir()
                            parent = parent.parent
                        else:
                            break
                    except OSError:
                        break
        except Exception as exc:
            errors.append(f"{logical}: {exc}")
    return removed, errors


def all_library_roots() -> dict[str, str]:
    roots = {}
    roots.update({f"radarr:{k}": v for k, v in movie_roots().items()})
    roots.update({f"sonarr:{k}": v for k, v in tv_roots().items()})
    roots["lidarr:default"] = lidarr_root()
    return roots


def scan_broken_symlinks(limit: int = 1000) -> list[dict]:
    broken = []
    for root_key, logical_root in all_library_roots().items():
        try:
            actual_root = view_path(logical_root)
            if not actual_root.exists():
                continue
            for p in actual_root.rglob("*"):
                if len(broken) >= limit:
                    return broken
                if not p.is_symlink():
                    continue
                target = os.readlink(p)
                # target is normally a logical /mnt/debrid path
                exists = False
                try:
                    exists = view_path(target).exists() if target.startswith(settings.dumb_root) else Path(target).exists()
                except Exception:
                    exists = False
                if not exists:
                    broken.append({
                        "root": root_key,
                        "path": str(logical_from_view(p)),
                        "target": target,
                        "filename": p.name,
                    })
        except (OSError, PermissionError):
            continue
    return broken


def repair_broken_symlink(logical_path: str) -> tuple[bool, str]:
    actual = view_path(logical_path)
    if not actual.is_symlink():
        return False, "Path is not a symlink"
    filename = actual.name
    source_root = view_path(source_root())
    matches = []
    try:
        for p in source_root.rglob(filename):
            if p.is_file():
                matches.append(p)
                if len(matches) > 1:
                    break
    except OSError as exc:
        return False, str(exc)
    if len(matches) != 1:
        return False, f"Found {len(matches)} possible source files; repair requires exactly one"
    target_logical = logical_from_view(matches[0])
    actual.unlink()
    os.symlink(str(target_logical), str(actual))
    return True, str(target_logical)
