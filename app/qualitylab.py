from __future__ import annotations

import re
from typing import Any

from .policy import load_policy, score_release
from .tvpacks import classify_release


def parse_release_name(title: str) -> dict[str, Any]:
    t = title or ""
    lower = t.lower()
    resolution = 2160 if any(x in lower for x in ("2160p", "4k", "uhd")) else 1080 if "1080p" in lower else 720 if "720p" in lower else 576 if "576p" in lower else 480 if "480p" in lower else 0
    codec = "HEVC / x265" if any(x in lower for x in ("x265", "h265", "hevc")) else "H.264 / x264" if any(x in lower for x in ("x264", "h264", "avc")) else "AV1" if "av1" in lower else "Unknown"
    source = "Remux" if "remux" in lower else "BluRay" if any(x in lower for x in ("bluray", "blu-ray", "bdrip", "brrip")) else "WEB-DL" if any(x in lower for x in ("web-dl", "webdl", "web dl")) else "WEBRip" if "webrip" in lower else "HDTV" if "hdtv" in lower else "Unknown"
    hdr: list[str] = []
    if any(x in lower for x in ("dolby vision", "dovi", " dv ", ".dv.", "dv hdr")): hdr.append("Dolby Vision")
    if "hdr10+" in lower or "hdr10plus" in lower: hdr.append("HDR10+")
    elif "hdr10" in lower or re.search(r"(^|[ ._-])hdr([ ._-]|$)", lower): hdr.append("HDR10")
    audio = "TrueHD Atmos" if "truehd" in lower and "atmos" in lower else "Atmos" if "atmos" in lower else "DTS-HD MA" if any(x in lower for x in ("dts-hd", "dtshd")) else "EAC3" if any(x in lower for x in ("eac3", "ddp", "dd+")) else "AC3" if "ac3" in lower else "AAC" if "aac" in lower else "Unknown"
    edition = []
    for needle, label in (("extended", "Extended"), ("director", "Director's Cut"), ("proper", "PROPER"), ("repack", "REPACK")):
        if needle in lower: edition.append(label)
    group = ""
    m = re.search(r"-([A-Za-z0-9][A-Za-z0-9._-]{1,30})$", t.strip())
    if m: group = m.group(1)
    pack = classify_release(t).as_dict()
    return {
        "resolution": resolution,
        "codec": codec,
        "source": source,
        "hdr": hdr,
        "audio": audio,
        "edition": edition,
        "release_group": group,
        "pack": pack,
    }


def evaluate_release(title: str, *, protocol: str = "torrent", size_gb: float = 0, seeders: int = 0, cached: bool = False, media_type: str = "movie", pack_type: str = "") -> dict[str, Any]:
    parsed = parse_release_name(title)
    inferred_pack = pack_type or (parsed.get("pack") or {}).get("kind") or ""
    release = {
        "title": title,
        "protocol": protocol,
        "size": int(max(0.0, float(size_gb or 0)) * 1024**3),
        "seeders": max(0, int(seeders or 0)),
        "realDebridCached": bool(cached),
    }
    policy = score_release(release, load_policy(), media_type=media_type, pack_type=inferred_pack)
    return {"release": release, "parsed": parsed, "policy": policy, "pack_type": inferred_pack}
